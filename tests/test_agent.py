from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import httpx

from agent.preview import build_previews, emit_previews, read_preview_tail
from agent.main import Agent
from agent.remote_query import QueryProvider, RemoteApiError
from agent.serialization import canonical_json, normalize_value
from agent.source import (
    MariaDBSource,
    SourceError,
    load_vn_query,
    prepare_vn_batch_query,
    prepare_vn_query,
)
from agent.state import ChangeEvent, StateStore


def row(hos_guid: str, vn: str, qty: int) -> dict:
    return {
        "hos_guid": hos_guid,
        "vn": vn,
        "qty": qty,
        "vstdate": "2026-07-20",
        "rxdate": None,
    }


class PreviewRetentionTests(unittest.TestCase):
    def test_emit_keeps_only_latest_twenty_payload_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.jsonl"
            emit_previews(
                ({"case": index} for index in range(1, 26)),
                path,
                echo=False,
            )

            items = read_preview_tail(path, 100)
            self.assertEqual(20, len(items))
            self.assertEqual(6, items[0]["case"])
            self.assertEqual(25, items[-1]["case"])


class SerializationTests(unittest.TestCase):
    def test_dirty_tis620_non_breaking_space_is_normalized(self) -> None:
        value = "ยา".encode("tis-620") + b"\xa0" + "เม็ด".encode("tis-620")

        self.assertEqual("ยา เม็ด", normalize_value(value))

    def test_invalid_legacy_byte_does_not_abort_json_serialization(self) -> None:
        normalized = normalize_value(b"name:\x81value")

        self.assertIsInstance(normalized, str)
        self.assertIn("name:", normalized)

    def test_mysql_time_timedelta_is_json_safe(self) -> None:
        value = timedelta(hours=13, minutes=7, seconds=5, microseconds=4500)
        self.assertEqual("13:07:05.0045", normalize_value(value))
        self.assertEqual(
            '{"recetime":"13:07:05.0045"}',
            canonical_json({"recetime": value}),
        )
        self.assertEqual(
            '{"data":[{"vsttime":"13:07:05.0045"}]}',
            canonical_json({"data": [{"vsttime": value}]}),
        )

    def test_mysql_time_supports_negative_and_over_24_hours(self) -> None:
        self.assertEqual("27:00:00", normalize_value(timedelta(hours=27)))
        self.assertEqual(
            "-01:02:03.0045",
            normalize_value(
                -timedelta(hours=1, minutes=2, seconds=3, microseconds=4500)
            ),
        )


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tempdir.name) / "agent.db")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def apply(self, snapshot: dict, rounds: int = 3):
        return self.store.apply_snapshot(
            snapshot=snapshot,
            server_date=date(2026, 7, 20),
            lookback_days=1,
            delete_confirm_rounds=rounds,
            max_missing_absolute=50,
            max_missing_percent=5,
        )

    def test_bootstrap_emits_no_events(self) -> None:
        self.store.bootstrap({"guid-1": row("guid-1", "vn-1", 1)})
        events, warning = self.apply({"guid-1": row("guid-1", "vn-1", 1)})
        self.assertEqual([], events)
        self.assertIsNone(warning)

    def test_insert_and_update(self) -> None:
        self.store.bootstrap({})
        events, _ = self.apply({"guid-1": row("guid-1", "vn-1", 1)})
        self.assertEqual(["INSERT"], [event.event_type for event in events])

        events, _ = self.apply({"guid-1": row("guid-1", "vn-1", 2)})
        self.assertEqual(["UPDATE"], [event.event_type for event in events])
        self.assertEqual(1, events[0].before["qty"])
        self.assertEqual(2, events[0].after["qty"])

        pending = self.store.pending_events()
        self.assertEqual(2, len(pending))
        event_ids = [event.event_id for event in pending]
        self.store.mark_events(event_ids, "previewed")
        self.assertEqual([], self.store.pending_events())
        self.assertEqual(2, len(self.store.pending_events(include_previewed=True)))
        self.store.acknowledge_events(event_ids)
        self.assertEqual([], self.store.pending_events(include_previewed=True))

    def test_master_rows_change_until_the_api_batch_is_acknowledged(self) -> None:
        first = {
            "1000001": {"icode": "1000001", "name": "Drug A"},
            "1000002": {"icode": "1000002", "name": "Drug B"},
        }
        changed = self.store.changed_master_rows("drugitems", first)
        self.assertEqual(["1000001", "1000002"], [item[0] for item in changed])

        self.store.acknowledge_master_rows("drugitems", changed[:1])
        remaining = self.store.changed_master_rows("drugitems", first)
        self.assertEqual(["1000002"], [item[0] for item in remaining])

        modified = dict(first)
        modified["1000001"] = {"icode": "1000001", "name": "Drug A edited"}
        changed_again = self.store.changed_master_rows("drugitems", modified)
        self.assertEqual(
            ["1000001", "1000002"],
            [item[0] for item in changed_again],
        )

    def test_delete_requires_confirmation(self) -> None:
        self.store.bootstrap({"guid-1": row("guid-1", "vn-1", 1)})
        for _ in range(2):
            events, _ = self.apply({})
            self.assertEqual([], events)
        events, _ = self.apply({})
        self.assertEqual(["DELETE_INFERRED"], [event.event_type for event in events])

    def test_preview_groups_events_by_vn(self) -> None:
        self.store.bootstrap({})
        events, _ = self.apply(
            {
                "guid-1": row("guid-1", "vn-1", 1),
                "guid-2": row("guid-2", "vn-1", 2),
            }
        )
        previews = build_previews(
            "https://example.invalid/api",
            events,
            {"vn-1": [{"vn": "vn-1", "drug": "A"}]},
            query_timing={"database_query_ms": 12.5},
        )
        self.assertEqual(1, len(previews))
        self.assertEqual(2, len(previews[0]["trigger"]["events"]))
        self.assertEqual("vn-1", previews[0]["body"][0]["vn"])
        self.assertEqual(12.5, previews[0]["query"]["database_query_ms"])
        self.assertEqual(1, previews[0]["query"]["rows_returned"])


class SqlValidationTests(unittest.TestCase):
    def test_requires_one_vn_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sql.txt"
            path.write_text("SELECT * FROM t WHERE vn = ?", encoding="utf-8")
            self.assertEqual("SELECT * FROM t WHERE vn = ?", load_vn_query(path))

            path.write_text("SELECT * FROM t", encoding="utf-8")
            with self.assertRaises(SourceError):
                load_vn_query(path)

    def test_rejects_mutating_sql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sql.txt"
            path.write_text("DELETE FROM t WHERE vn = ?", encoding="utf-8")
            with self.assertRaises(SourceError):
                load_vn_query(path)

    def test_allows_replace_scalar_function(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sql.txt"
            path.write_text(
                "SELECT REPLACE(name, ',', ' ') FROM t WHERE vn = ?",
                encoding="utf-8",
            )
            self.assertIn("REPLACE", load_vn_query(path))

    def test_rejects_select_into_outfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sql.txt"
            path.write_text(
                "SELECT * FROM t WHERE vn = ? INTO OUTFILE '/tmp/data'",
                encoding="utf-8",
            )
            with self.assertRaises(SourceError):
                load_vn_query(path)

    def test_injects_vn_filter_into_remote_query(self) -> None:
        query = prepare_vn_query(
            "SELECT o.vn FROM opitemrece o "
            "WHERE o.hn IS NOT NULL GROUP BY o.vn"
        )
        self.assertIn("AND o.vn = ?", query)
        self.assertLess(query.index("AND o.vn = ?"), query.index("GROUP BY"))

    def test_expands_multiple_vns_as_bound_in_parameters(self) -> None:
        query = prepare_vn_batch_query(
            "SELECT o.vn FROM opitemrece o WHERE o.vn = ?",
            3,
        )
        self.assertIn("o.vn IN (%s, %s, %s)", query)
        self.assertNotIn("vn-1", query)

    def test_expands_api_in_template_with_multiple_vns(self) -> None:
        query = prepare_vn_batch_query(
            "SELECT o.vn FROM opitemrece o WHERE o.vn IN ({{VN}})",
            2,
        )
        self.assertIn("o.vn IN (%s, %s)", query)

    def test_rejects_unsupported_batch_placeholder_position(self) -> None:
        with self.assertRaisesRegex(SourceError, "vn ="):
            prepare_vn_batch_query(
                "SELECT vn FROM visits WHERE COALESCE(?, vn) = vn",
                2,
            )

    def test_master_table_reader_rejects_unapproved_table_name(self) -> None:
        source = MariaDBSource(SimpleNamespace())
        with self.assertRaisesRegex(SourceError, "not allowed"):
            source.fetch_master_table("patient")


class MariaDBBatchTests(unittest.TestCase):
    def test_detail_query_splits_large_vn_list_into_small_batches(self) -> None:
        source = MariaDBSource(
            SimpleNamespace(
                db_query_vn_batch_size=2,
                db_query_retries=0,
                sql_file=Path("unused.sql"),
            )
        )
        source._query_vn_batch = MagicMock(
            side_effect=lambda batch, query: [{"vn": vn} for vn in batch]
        )

        results = source.run_query_for_vns(
            ["vn-5", "vn-1", "vn-4", "vn-2", "vn-3"],
            query="SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)",
        )

        batches = [call.args[0] for call in source._query_vn_batch.call_args_list]
        self.assertEqual(
            [["vn-1", "vn-2"], ["vn-3", "vn-4"], ["vn-5"]],
            batches,
        )
        self.assertEqual({"vn-1", "vn-2", "vn-3", "vn-4", "vn-5"}, set(results))

    def test_detail_query_retries_with_a_fresh_batch_attempt(self) -> None:
        source = MariaDBSource(
            SimpleNamespace(db_query_retries=2)
        )
        source._query_vn_batch = MagicMock(
            side_effect=[RuntimeError("packet sequence"), [{"vn": "vn-1"}]]
        )

        with patch("agent.source.time.sleep"):
            rows = source._query_vn_batch_with_retry(
                ["vn-1"],
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)",
            )

        self.assertEqual([{"vn": "vn-1"}], rows)
        self.assertEqual(2, source._query_vn_batch.call_count)


class AgentFlowTests(unittest.TestCase):
    def _agent(self, root: Path, dry_run: bool = True) -> Agent:
        agent = Agent.__new__(Agent)
        agent.config = SimpleNamespace(
            lookback_days=1,
            delete_confirm_rounds=3,
            max_missing_absolute=50,
            max_missing_percent=5,
            vn_debounce_seconds=10,
            max_vns_per_cycle=100,
            api_post_vn_batch_size=25,
            query_source="remote",
            api_query_url="https://example.test/query/2",
            sql_file=root / "sql.txt",
            dry_run=dry_run,
            output_jsonl=root / "preview.jsonl",
            post_url="https://example.test/query/2",
        )
        agent.source = MagicMock()
        agent.store = MagicMock()
        agent.query_provider = MagicMock()
        agent.source.fetch_snapshot.return_value = (
            date(2026, 7, 20),
            {"guid-1": row("guid-1", "vn-1", 1)},
            None,
        )
        agent.store.is_initialized.return_value = True
        return agent

    def test_large_backlog_limits_unique_vns_per_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            agent.config.max_vns_per_cycle = 2
            detected = "2026-07-20T10:00:00+00:00"
            events = [
                ChangeEvent(f"e{i}", "UPDATE", f"g{i}", f"vn-{i}", {}, {}, detected)
                for i in range(1, 4)
            ]
            agent.store.apply_snapshot.return_value = (events, None)
            agent.store.pending_events.return_value = events
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)"
            )
            agent.source.run_query_for_vns.return_value = {
                "vn-1": [{"vn": "vn-1"}],
                "vn-2": [{"vn": "vn-2"}],
            }

            with patch("agent.main.emit_previews"):
                agent.run_once()

            requested = list(agent.source.run_query_for_vns.call_args.args[0])
            self.assertEqual(["vn-1", "vn-2"], requested)
            marked_ids = agent.store.mark_events.call_args_list[-1].args[0]
            self.assertEqual(["e1", "e2"], marked_ids)

    def test_master_sync_posts_only_changed_rows_in_bounded_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory), dry_run=False)
            agent.config.master_sync_seconds = 3600
            agent.config.master_post_batch_size = 2
            agent.config.drugitems_post_url = "https://example.test/query/3"
            agent.config.s_drugitems_post_url = "https://example.test/query/4"
            agent._next_master_sync_at = 0.0
            drug_rows = {
                str(index): {"icode": str(index), "name": f"Drug {index}"}
                for index in range(3)
            }
            supply_rows = {"9": {"icode": "9", "name": "Supply"}}
            agent.source.fetch_master_table.side_effect = [
                drug_rows,
                supply_rows,
            ]
            agent.store.changed_master_rows.side_effect = [
                [
                    (key, payload, f"hash-{key}")
                    for key, payload in drug_rows.items()
                ],
                [("9", supply_rows["9"], "hash-9")],
            ]

            self.assertTrue(agent.run_master_sync_if_due())
            self.assertEqual(3, agent.query_provider.post_payload.call_count)
            self.assertEqual(
                call(
                    [drug_rows["0"], drug_rows["1"]],
                    url="https://example.test/query/3",
                ),
                agent.query_provider.post_payload.call_args_list[0],
            )
            self.assertEqual(
                call([supply_rows["9"]], url="https://example.test/query/4"),
                agent.query_provider.post_payload.call_args_list[2],
            )
            self.assertEqual(3, agent.store.acknowledge_master_rows.call_count)
            self.assertFalse(agent.run_master_sync_if_due())

    def test_master_sync_failure_is_retained_without_raising_into_event_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory), dry_run=False)
            agent.config.master_sync_seconds = 3600
            agent.config.master_post_batch_size = 50
            agent.config.drugitems_post_url = "https://example.test/query/3"
            agent.config.s_drugitems_post_url = "https://example.test/query/4"
            agent._next_master_sync_at = 0.0
            row_data = {"1": {"icode": "1"}}
            agent.source.fetch_master_table.side_effect = [row_data, {}]
            agent.store.changed_master_rows.side_effect = [
                [("1", row_data["1"], "hash-1")],
                [],
            ]
            agent.query_provider.post_payload.side_effect = RemoteApiError("down")

            with self.assertLogs("hosxp-polling-agent", level="ERROR"):
                self.assertTrue(agent.run_master_sync_if_due())

            agent.store.acknowledge_master_rows.assert_not_called()

    def test_no_event_only_checks_hourly_sql_cache_and_does_not_query_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            agent.store.apply_snapshot.return_value = ([], None)
            agent.store.pending_events.return_value = []

            agent.run_once()

            agent.query_provider.get_query.assert_called_once_with()
            agent.source.run_query_for_vns.assert_not_called()
            agent.query_provider.begin_delivery_round.assert_not_called()

    def test_events_without_vn_are_logged_as_five_examples_and_one_summary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            detected = "2026-07-20T10:00:00+00:00"
            events = [
                ChangeEvent(
                    f"e{i}",
                    "UPDATE",
                    f"g{i}",
                    None,
                    {},
                    {},
                    detected,
                )
                for i in range(1, 13)
            ]
            agent.store.apply_snapshot.return_value = (events, None)
            agent.store.pending_events.return_value = events

            with self.assertLogs("hosxp-polling-agent", level="WARNING") as captured:
                agent.run_once()

            individual = [
                line for line in captured.output if "has no vn" in line
            ]
            summaries = [
                line
                for line in captured.output
                if "Events without VN condensed" in line
            ]
            self.assertEqual(5, len(individual))
            self.assertEqual(1, len(summaries))
            self.assertIn("total=12", summaries[0])
            agent.store.mark_events.assert_any_call(
                [f"e{i}" for i in range(1, 13)],
                "skipped_no_vn",
            )

    def test_event_batch_fetches_sql_once_for_all_unique_vns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            detected = "2026-07-20T10:00:00+00:00"
            events = [
                ChangeEvent("e1", "INSERT", "g1", "vn-1", None, {}, detected),
                ChangeEvent("e2", "UPDATE", "g2", "vn-2", {}, {}, detected),
                ChangeEvent("e3", "UPDATE", "g3", "vn-1", {}, {}, detected),
            ]
            agent.store.apply_snapshot.return_value = (events, None)
            agent.store.pending_events.return_value = events
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn = ?"
            )
            agent.source.run_query_for_vns.return_value = {
                "vn-1": [{"vn": "vn-1"}],
                "vn-2": [{"vn": "vn-2"}],
            }

            with patch("agent.main.emit_previews") as emit:
                agent.run_once()

            agent.query_provider.get_query.assert_called_once_with()
            requested_vns = set(agent.source.run_query_for_vns.call_args.args[0])
            self.assertEqual({"vn-1", "vn-2"}, requested_vns)
            self.assertEqual(2, len(emit.call_args.args[0]))

    def test_recent_same_vn_events_wait_and_do_not_call_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            detected = datetime.now(timezone.utc).isoformat()
            events = [
                ChangeEvent("e1", "INSERT", "g1", "vn-1", None, {}, detected),
                ChangeEvent("e2", "UPDATE", "g2", "vn-1", {}, {}, detected),
                ChangeEvent("e3", "DELETE_INFERRED", "g3", "vn-1", {}, None, detected),
            ]
            agent.store.apply_snapshot.return_value = (events, None)
            agent.store.pending_events.return_value = events

            agent.run_once()

            agent.query_provider.get_query.assert_called_once_with()
            agent.source.run_query_for_vns.assert_not_called()
            agent.query_provider.begin_delivery_round.assert_not_called()

    def test_no_query_rows_closes_events_without_preview_or_post(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            event = ChangeEvent(
                "e1", "UPDATE", "g1", "vn-1", {}, {}, "2026-07-20T10:00:00+00:00"
            )
            agent.store.apply_snapshot.return_value = ([event], None)
            agent.store.pending_events.return_value = [event]
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)"
            )
            agent.source.run_query_for_vns.return_value = {"vn-1": []}

            with patch("agent.main.emit_previews") as emit:
                agent.run_once()

            emit.assert_not_called()
            agent.query_provider.post_payload.assert_not_called()
            agent.query_provider.begin_delivery_round.assert_not_called()
            agent.store.mark_events.assert_any_call(["e1"], "skipped_no_data")

    def test_live_post_clears_only_acknowledged_event_from_retry_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory), dry_run=False)
            event = ChangeEvent(
                "e1", "INSERT", "g1", "vn-1", None, {}, "2026-07-20T10:00:00+00:00"
            )
            agent.store.apply_snapshot.return_value = ([event], None)
            agent.store.pending_events.return_value = [event]
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)"
            )
            agent.source.run_query_for_vns.return_value = {
                "vn-1": [{"vn": "vn-1", "drug": "A"}]
            }

            agent.run_once()

            agent.store.pending_events.assert_called_once_with(include_previewed=True)
            agent.query_provider.post_payload.assert_called_once_with(
                [{"vn": "vn-1", "drug": "A"}]
            )
            agent.query_provider.begin_delivery_round.assert_called_once_with()
            agent.store.acknowledge_events.assert_called_once_with(["e1"])

    def test_failed_live_post_keeps_event_in_retry_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory), dry_run=False)
            event = ChangeEvent(
                "e1", "INSERT", "g1", "vn-1", None, {}, "2026-07-20T10:00:00+00:00"
            )
            agent.store.apply_snapshot.return_value = ([event], None)
            agent.store.pending_events.return_value = [event]
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)"
            )
            agent.source.run_query_for_vns.return_value = {
                "vn-1": [{"vn": "vn-1", "drug": "A"}]
            }
            agent.query_provider.post_payload.side_effect = RemoteApiError("temporary")

            with self.assertRaises(RemoteApiError):
                agent.run_once()

            agent.store.acknowledge_events.assert_not_called()

    def test_live_post_combines_multiple_vns_into_bounded_array_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory), dry_run=False)
            agent.config.api_post_vn_batch_size = 25
            detected = "2026-07-20T10:00:00+00:00"
            events = [
                ChangeEvent("e1", "INSERT", "g1", "vn-1", None, {}, detected),
                ChangeEvent("e2", "UPDATE", "g2", "vn-2", {}, {}, detected),
            ]
            agent.store.apply_snapshot.return_value = (events, None)
            agent.store.pending_events.return_value = events
            agent.query_provider.get_query.return_value = (
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?, ?)"
            )
            agent.source.run_query_for_vns.return_value = {
                "vn-1": [{"vn": "vn-1", "drug": "A"}],
                "vn-2": [{"vn": "vn-2", "drug": "B"}],
            }

            agent.run_once()

            agent.query_provider.post_payload.assert_called_once_with(
                [
                    {"vn": "vn-1", "drug": "A"},
                    {"vn": "vn-2", "drug": "B"},
                ]
            )
            agent.store.acknowledge_events.assert_called_once_with(["e1", "e2"])

    def test_cached_sql_failure_forces_one_refresh_and_retries_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self._agent(Path(directory))
            event = ChangeEvent(
                "e1", "UPDATE", "g1", "vn-1", {}, {},
                "2026-07-20T10:00:00+00:00",
            )
            agent.store.apply_snapshot.return_value = ([event], None)
            agent.store.pending_events.return_value = [event]
            agent.query_provider.get_query.side_effect = [
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)",
                "SELECT o.vn FROM opitemrece o WHERE o.vn IN (?)",
            ]
            agent.source.run_query_for_vns.side_effect = [
                SourceError("cached SQL column no longer exists"),
                {"vn-1": [{"vn": "vn-1"}]},
            ]

            with patch("agent.main.emit_previews"):
                agent.run_once()

            self.assertEqual(
                [
                    call(),
                    call(force_refresh=True, allow_cache_fallback=False),
                ],
                agent.query_provider.get_query.call_args_list,
            )
            self.assertEqual(2, agent.source.run_query_for_vns.call_count)


class RemoteQueryTests(unittest.TestCase):
    def test_query_is_served_from_ram_until_refresh_interval_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = {"login": 0, "get": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/signIn"):
                    calls["login"] += 1
                    return httpx.Response(200, json={"token": "sql-token"})
                calls["get"] += 1
                return httpx.Response(
                    200,
                    json={"ok": True, "query": "SELECT o.vn FROM opitemrece o"},
                )

            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/2",
                post_url="https://example.test/query/2",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=Path(directory) / "query.json",
                query_source="remote",
                query_refresh_seconds=3600,
                sql_file=Path(directory) / "sql.txt",
            )
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = QueryProvider(config, client=client)

            first = provider.get_query()
            second = provider.get_query()

            client.close()
            self.assertEqual(first, second)
            self.assertEqual({"login": 1, "get": 1}, calls)

    def test_delivery_round_logs_in_again_after_hourly_sql_get(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = {"login": 0, "get": 0, "post": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/signIn"):
                    calls["login"] += 1
                    return httpx.Response(
                        200,
                        json={"token": f"round-token-{calls['login']}"},
                    )
                if request.method == "GET":
                    calls["get"] += 1
                    self.assertEqual(
                        "Bearer round-token-1",
                        request.headers["Authorization"],
                    )
                    return httpx.Response(
                        200,
                        json={"ok": True, "query": "SELECT o.vn FROM opitemrece o"},
                    )
                calls["post"] += 1
                self.assertEqual(
                    "Bearer round-token-2",
                    request.headers["Authorization"],
                )
                self.assertEqual([{"vn": "v1"}], request.read() and __import__("json").loads(request.content))
                return httpx.Response(200, json={"ok": True})

            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/1",
                post_url="https://example.test/query/1",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=Path(directory) / "query.json",
                query_source="remote",
                query_refresh_seconds=300,
                sql_file=Path(directory) / "sql.txt",
            )
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = QueryProvider(config, client=client)
            provider.get_query(force_refresh=True, allow_cache_fallback=False)
            provider.begin_delivery_round()
            provider.post_payload([{"vn": "v1"}])
            client.close()
            self.assertEqual({"login": 2, "get": 1, "post": 1}, calls)

    def test_post_rejects_http_200_with_api_ok_false(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("/signIn"):
                    return httpx.Response(200, json={"token": "round-token"})
                return httpx.Response(
                    200,
                    json={"ok": False, "message": "database insert failed"},
                )

            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/2",
                post_url="https://example.test/query/2",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=Path(directory) / "query.json",
                query_source="remote",
                query_refresh_seconds=300,
                sql_file=Path(directory) / "sql.txt",
            )
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = QueryProvider(config, client=client)
            with self.assertRaisesRegex(
                RemoteApiError, "database insert failed"
            ):
                provider.post_payload([{"vn": "v1"}])
            client.close()

    def test_cloudflare_challenge_is_reported_as_gateway_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    403,
                    text="<html><title>Just a moment...</title></html>",
                    headers={
                        "server": "cloudflare",
                        "cf-ray": "test-ray",
                        "content-type": "text/html; charset=UTF-8",
                    },
                )

            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/1",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=Path(directory) / "query.json",
                query_source="remote",
                query_refresh_seconds=300,
                sql_file=Path(directory) / "sql.txt",
            )
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = QueryProvider(config, client=client)
            with self.assertRaisesRegex(
                RemoteApiError, "did not reach the JSON API"
            ):
                provider.get_query(True, False)
            client.close()

    def test_login_fetch_and_cache_without_storing_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "query.json"
            calls = {"login": 0, "query": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                if request.method == "POST":
                    calls["login"] += 1
                    return httpx.Response(200, json={"ok": True, "jwt": "secret-jwt"})
                calls["query"] += 1
                self.assertEqual("Bearer secret-jwt", request.headers["Authorization"])
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "query": (
                            "SELECT o.vn FROM opitemrece o "
                            "WHERE o.hn IS NOT NULL GROUP BY o.vn"
                        ),
                    },
                )

            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/1",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=cache,
                query_source="remote",
                query_refresh_seconds=300,
                sql_file=Path(directory) / "sql.txt",
            )
            client = httpx.Client(transport=httpx.MockTransport(handler))
            provider = QueryProvider(config, client=client)
            query = provider.get_query(force_refresh=True)
            provider.get_query(force_refresh=True)
            provider.close()
            client.close()

            self.assertIn("AND o.vn = ?", query)
            self.assertEqual({"login": 2, "query": 2}, calls)
            cache_text = cache.read_text(encoding="utf-8")
            self.assertNotIn("secret-jwt", cache_text)
            self.assertIn("effective_query", cache_text)

    def test_setup_validation_preserves_remote_error_when_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = SimpleNamespace(
                api_timeout_seconds=5,
                api_signin_url="https://example.test/signIn",
                api_query_url="https://example.test/query/1",
                api_username="user",
                api_password="password",
                api_token_header="Authorization",
                api_token_scheme="Bearer",
                remote_query_cache=Path(directory) / "missing-query.json",
                query_source="remote",
                query_refresh_seconds=300,
                sql_file=Path(directory) / "sql.txt",
            )
            provider = QueryProvider(config, client=httpx.Client())
            with patch.object(
                provider,
                "_fetch_remote",
                side_effect=RemoteApiError("API login failed: 403 Forbidden"),
            ):
                with self.assertRaisesRegex(RemoteApiError, "403 Forbidden") as caught:
                    provider.get_query(True, False)
            provider.close()
            self.assertNotIn("cache is unavailable", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
