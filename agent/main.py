from __future__ import annotations

import argparse
import logging
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import DefaultDict, List

from .config import Config
from .preview import build_previews, emit_previews
from .remote_query import QueryProvider, RemoteApiError
from .source import MariaDBSource, SlaveUnhealthy, SourceError, load_vn_query
from .state import ChangeEvent, StateStore


LOGGER = logging.getLogger("hosxp-polling-agent")


class Agent:
    def __init__(self, config: Config):
        self.config = config
        self.source = MariaDBSource(config)
        self.store = StateStore(config.state_file)
        self.query_provider = QueryProvider(config)

    def close(self) -> None:
        self.query_provider.close()
        self.store.close()

    def run_once(self) -> None:
        cycle_started = time.perf_counter()
        sql_fetch_started = time.perf_counter()
        detail_query = None
        try:
            # Cache-first: this is a RAM lookup on normal cycles. It performs
            # Login + GET only at startup and after the refresh interval.
            detail_query = self.query_provider.get_query()
        except RemoteApiError as exc:
            LOGGER.warning(
                "SQL cache refresh is unavailable; polling will continue and "
                "pending VNs will be retained if no cached SQL exists: %s",
                exc,
            )
        sql_fetch_ms = round(
            (time.perf_counter() - sql_fetch_started) * 1000,
            2,
        )

        server_date, snapshot, health = self.source.fetch_snapshot()
        if not self.store.is_initialized():
            self.store.bootstrap(snapshot)
            LOGGER.info(
                "Bootstrap complete: %d rows stored; no events emitted", len(snapshot)
            )
            return

        events, warning = self.store.apply_snapshot(
            snapshot=snapshot,
            server_date=server_date,
            lookback_days=self.config.lookback_days,
            delete_confirm_rounds=self.config.delete_confirm_rounds,
            max_missing_absolute=self.config.max_missing_absolute,
            max_missing_percent=self.config.max_missing_percent,
        )
        if warning:
            LOGGER.warning(warning)

        # Previewed events remain a durable local backup. A future live run
        # includes them again and removes them only after POST succeeds.
        pending_events = self.store.pending_events(
            include_previewed=not self.config.dry_run
        )
        pending_by_vn: DefaultDict[str, List[ChangeEvent]] = defaultdict(list)
        skipped_event_ids: List[str] = []
        event_log_limit = 10
        no_vn_log_limit = 5
        no_vn_lines_shown = 0
        for index, event in enumerate(pending_events):
            if index < event_log_limit:
                LOGGER.info(
                    "EVENT %-15s vn=%s hos_guid=%s",
                    event.event_type,
                    event.vn,
                    event.hos_guid,
                )
            if event.vn:
                pending_by_vn[event.vn].append(event)
            else:
                skipped_event_ids.append(event.event_id)
                if no_vn_lines_shown < no_vn_log_limit:
                    LOGGER.warning(
                        "Event %s for hos_guid=%s has no vn; detail query skipped",
                        event.event_type,
                        event.hos_guid,
                    )
                    no_vn_lines_shown += 1

        if len(skipped_event_ids) > no_vn_log_limit:
            LOGGER.warning(
                "Events without VN condensed: total=%d examples_shown=%d; "
                "detail query skipped for these events",
                len(skipped_event_ids),
                no_vn_log_limit,
            )

        if len(pending_events) > event_log_limit:
            LOGGER.info(
                "Pending event log condensed: total_events=%d unique_vns=%d "
                "individual_lines_shown=%d",
                len(pending_events),
                len(pending_by_vn),
                event_log_limit,
            )

        self.store.mark_events(skipped_event_ids, "skipped_no_vn")

        now = datetime.now(timezone.utc)
        by_vn: DefaultDict[str, List[ChangeEvent]] = defaultdict(list)
        deferred_vns: List[str] = []
        for vn, vn_events in pending_by_vn.items():
            latest = max(datetime.fromisoformat(event.detected_at) for event in vn_events)
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            if (now - latest).total_seconds() >= self.config.vn_debounce_seconds:
                by_vn[vn].extend(vn_events)
            else:
                deferred_vns.append(vn)

        if deferred_vns:
            LOGGER.info(
                "VN coalescing: pending_events=%d deferred_vns=%d quiet_window=%.1fs; no API call for deferred VNs",
                sum(len(pending_by_vn[vn]) for vn in deferred_vns),
                len(deferred_vns),
                self.config.vn_debounce_seconds,
            )

        max_vns = max(1, int(getattr(self.config, "max_vns_per_cycle", 100)))
        if len(by_vn) > max_vns:
            ready_vns = list(by_vn)
            throttled_vns = ready_vns[max_vns:]
            by_vn = defaultdict(
                list,
                ((vn, by_vn[vn]) for vn in ready_vns[:max_vns]),
            )
            LOGGER.info(
                "Backlog throttled: processing_vns=%d retained_vns=%d "
                "max_vns_per_cycle=%d",
                len(by_vn),
                len(throttled_vns),
                max_vns,
            )

        processable_events = [
            event for vn_events in by_vn.values() for event in vn_events
        ]

        if by_vn:
            if detail_query is None:
                LOGGER.warning(
                    "%d pending VN(s) retained because remote SQL is unavailable",
                    len(by_vn),
                )
                return
            LOGGER.info(
                "Event batch ready: events=%d unique_vns=%d; cached SQL ready",
                len(processable_events),
                len(by_vn),
            )

        database_query_started = time.perf_counter()
        query_results = {}
        if by_vn:
            try:
                query_results = self.source.run_query_for_vns(
                    by_vn.keys(), query=detail_query
                )
            except SourceError as first_error:
                if self.config.query_source != "remote":
                    raise
                LOGGER.warning(
                    "Cached SQL query failed; forcing one immediate SQL refresh "
                    "before retry: %s",
                    first_error,
                )
                refreshed_query = self.query_provider.get_query(
                    force_refresh=True,
                    allow_cache_fallback=False,
                )
                query_results = self.source.run_query_for_vns(
                    by_vn.keys(), query=refreshed_query
                )
        database_query_ms = round(
            (time.perf_counter() - database_query_started) * 1000,
            2,
        )
        no_data_vns = {vn for vn in by_vn if not query_results.get(vn)}
        if no_data_vns:
            no_data_event_ids = [
                event.event_id
                for vn in no_data_vns
                for event in by_vn[vn]
            ]
            self.store.mark_events(no_data_event_ids, "skipped_no_data")
            LOGGER.info(
                "Detail query returned no data: vns=%d events=%d; POST skipped and queue entries closed",
                len(no_data_vns),
                len(no_data_event_ids),
            )

        deliverable_events = [
            event
            for vn, vn_events in by_vn.items()
            if vn not in no_data_vns
            for event in vn_events
        ]
        previews = build_previews(
            self.config.post_url,
            deliverable_events,
            query_results,
            query_timing={
                "source": self.config.query_source,
                "sql_endpoint": (
                    self.config.api_query_url
                    if self.config.query_source == "remote"
                    else str(self.config.sql_file)
                ),
                "sql_fetch_ms": sql_fetch_ms,
                "database_query_ms": database_query_ms,
                "batch_vns": sorted(by_vn),
                "batch_vn_count": len(by_vn),
                "cycle_elapsed_ms": round(
                    (time.perf_counter() - cycle_started) * 1000,
                    2,
                ),
            },
            dry_run=self.config.dry_run,
        )
        if self.config.dry_run and previews:
            emit_previews(previews, self.config.output_jsonl)
        elif not self.config.dry_run and previews:
            # Querying no rows never reaches here. Start a separate delivery
            # round and obtain a fresh JWT only when data is ready to POST.
            self.query_provider.begin_delivery_round()
            post_failures: List[str] = []
            post_batch_size = max(
                1,
                int(getattr(self.config, "api_post_vn_batch_size", 25)),
            )
            for offset in range(0, len(previews), post_batch_size):
                preview_batch = previews[offset : offset + post_batch_size]
                payload_rows = [
                    row
                    for preview in preview_batch
                    for row in preview["body"]
                ]
                batch_vns = [
                    str(preview["trigger"]["vn"])
                    for preview in preview_batch
                ]
                try:
                    self.query_provider.post_payload(payload_rows)
                except RemoteApiError as exc:
                    post_failures.extend(batch_vns)
                    LOGGER.error(
                        "API POST batch failed; %d VN(s) retained for retry: "
                        "first_vn=%s error=%s",
                        len(batch_vns),
                        batch_vns[0] if batch_vns else None,
                        exc,
                    )
                    continue
                acknowledged_at = datetime.now(timezone.utc).isoformat()
                posted_event_ids: List[str] = []
                for preview in preview_batch:
                    preview["delivery"] = {
                        "status": "sent",
                        "acknowledged_at": acknowledged_at,
                        "batch_vn_count": len(batch_vns),
                    }
                    posted_event_ids.extend(
                        item["event_id"]
                        for item in preview["trigger"]["events"]
                    )
                emit_previews(
                    preview_batch,
                    self.config.output_jsonl,
                    echo=False,
                )
                self.store.acknowledge_events(posted_event_ids)
                LOGGER.info(
                    "API POST batch acknowledged: vns=%d rows=%d events=%d; "
                    "retry queue cleared for this batch",
                    len(batch_vns),
                    len(payload_rows),
                    len(posted_event_ids),
                )
            if post_failures:
                raise RemoteApiError(
                    f"API POST failed for {len(post_failures)} VN(s); "
                    "successful VNs were acknowledged and failed VNs remain queued"
                )
        if previews and self.config.dry_run:
            LOGGER.info(
                "DRY RUN: %d POST preview(s) written to %s; no API POST was sent",
                len(previews),
                self.config.output_jsonl,
            )
        if self.config.dry_run:
            self.store.mark_events(
                [event.event_id for event in deliverable_events],
                "previewed",
            )

        lag = health.get("seconds_behind_master") if health else None
        LOGGER.info(
            "Poll complete: rows=%d events=%d vns=%d replication_lag=%s "
            "sql_fetch_ms=%.2f database_query_ms=%.2f cycle_ms=%.2f",
            len(snapshot),
            len(events),
            len(by_vn),
            lag,
            sql_fetch_ms,
            database_query_ms,
            (time.perf_counter() - cycle_started) * 1000,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only HOSxP opitemrece hybrid polling agent"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one polling cycle and exit",
    )
    parser.add_argument(
        "--validate-sql",
        action="store_true",
        help="Validate sql.txt and exit without connecting to MariaDB",
    )
    parser.add_argument(
        "--fetch-query",
        action="store_true",
        help="Login, fetch/cache remote SQL, and exit without polling MariaDB",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    try:
        config = Config.from_env()
        load_vn_query(config.sql_file)
    except (ValueError, OSError, SourceError) as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2

    if args.validate_sql:
        LOGGER.info("SQL is a valid read-only SELECT with one vn placeholder")
        return 0

    if args.fetch_query:
        provider = QueryProvider(config)
        try:
            provider.get_query(force_refresh=True)
            LOGGER.info("Remote SQL is ready (query text hidden from terminal log)")
            return 0
        except RemoteApiError as exc:
            LOGGER.error("Remote SQL fetch failed: %s", exc)
            return 3
        finally:
            provider.close()

    stopped = False

    def stop_handler(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True
        LOGGER.info("Stop requested by signal %s", signum)

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    agent = Agent(config)
    try:
        while not stopped:
            started = time.monotonic()
            try:
                agent.run_once()
            except SlaveUnhealthy as exc:
                LOGGER.warning("Polling skipped: %s", exc)
            except Exception:
                LOGGER.exception("Polling cycle failed")

            if args.once:
                break
            elapsed = time.monotonic() - started
            remaining = max(0.0, config.poll_seconds - elapsed)
            deadline = time.monotonic() + remaining
            while not stopped and time.monotonic() < deadline:
                time.sleep(min(0.5, deadline - time.monotonic()))
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
