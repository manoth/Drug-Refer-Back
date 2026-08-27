from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

from .config import Config
from .main import Agent
from .web_config import WebConfig
from .web_store import WebStore


LOGGER = logging.getLogger("hosxp-polling-agent.web-worker")


@dataclass(frozen=True)
class LogEntry:
    sequence: int
    timestamp: str
    level: str
    message: str


class LogBroker:
    def __init__(self, capacity: int = 500):
        self._entries: Deque[LogEntry] = deque(maxlen=capacity)
        self._sequence = 0
        self._condition = threading.Condition()

    def publish(self, level: str, message: str) -> None:
        with self._condition:
            self._sequence += 1
            self._entries.append(
                LogEntry(
                    sequence=self._sequence,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    level=level,
                    message=message,
                )
            )
            self._condition.notify_all()

    def since(self, sequence: int) -> List[LogEntry]:
        with self._condition:
            return [entry for entry in self._entries if entry.sequence > sequence]

    @property
    def latest_sequence(self) -> int:
        with self._condition:
            return self._sequence


class BrokerLogHandler(logging.Handler):
    def __init__(self, broker: LogBroker):
        super().__init__(logging.INFO)
        self.broker = broker

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.broker.publish(
                record.levelname,
                f"{record.name} — {record.getMessage()}",
            )
        except Exception:
            self.handleError(record)


def build_agent_config(
    web: WebConfig,
    store: WebStore,
    api_override: Optional[Dict[str, str]] = None,
) -> Config:
    database = store.database_settings()
    api = api_override or store.api_settings()
    missing = [
        name
        for name in ("db_host", "db_port", "db_name", "db_user", "db_password")
        if not database.get(name)
    ]
    missing.extend(
        name
        for name in ("api_username", "api_password")
        if not api.get(name)
    )
    if missing:
        raise RuntimeError("Setup is incomplete: " + ", ".join(missing))

    api_base_url = api.get("api_base_url", web.api_base_url).rstrip("/")
    api_root = f"{api_base_url}/drugrefer/api"
    api_query_id = api.get("api_query_id", "2")
    query_source = api.get("query_source", web.query_source)
    delivery_mode = api.get("delivery_mode", "")
    dry_run = (
        delivery_mode != "live"
        if delivery_mode in {"dry_run", "live"}
        else web.dry_run
    )

    return Config(
        db_host=database["db_host"],
        db_port=int(database["db_port"]),
        db_name=database["db_name"],
        db_user=database["db_user"],
        db_password=database["db_password"],
        lookback_days=web.lookback_days,
        poll_seconds=web.poll_seconds,
        vn_debounce_seconds=web.vn_debounce_seconds,
        max_vns_per_cycle=web.max_vns_per_cycle,
        db_query_vn_batch_size=web.db_query_vn_batch_size,
        db_query_retries=web.db_query_retries,
        db_read_timeout_seconds=web.db_read_timeout_seconds,
        api_post_vn_batch_size=web.api_post_vn_batch_size,
        delete_confirm_rounds=web.delete_confirm_rounds,
        max_missing_absolute=web.max_missing_absolute,
        max_missing_percent=web.max_missing_percent,
        max_replication_lag_seconds=web.max_replication_lag_seconds,
        require_slave_health=web.require_slave_health,
        dry_run=dry_run,
        sql_file=web.sql_file,
        state_file=web.polling_state_file,
        output_jsonl=web.output_jsonl,
        remote_query_cache=web.remote_query_cache,
        query_source=query_source,
        query_refresh_seconds=web.query_refresh_seconds,
        master_sync_seconds=web.master_sync_seconds,
        master_post_batch_size=web.master_post_batch_size,
        api_signin_url=f"{api_root}/signIn",
        api_query_url=f"{api_root}/syncData/query/{api_query_id}",
        api_username=api["api_username"],
        api_password=api["api_password"],
        api_token_header=web.api_token_header,
        api_token_scheme=web.api_token_scheme,
        api_timeout_seconds=web.api_timeout_seconds,
        # POST uses the same configured query endpoint after an acknowledged
        # login/GET SQL cycle. The local outbox is cleared only on ok=true.
        post_url=f"{api_root}/syncData/query/{api_query_id}",
        drugitems_post_url=f"{api_root}/syncData/query/3",
        s_drugitems_post_url=f"{api_root}/syncData/query/4",
    )


class PollingService:
    def __init__(self, web: WebConfig, store: WebStore):
        self.web = web
        self.store = store
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_error: Optional[str] = None
        self._last_poll_at: Optional[str] = None
        self._poll_count = 0

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            if not all(self.store.setup_status().values()):
                raise RuntimeError("Database/API setup is not complete")
            self._stop.clear()
            self._last_error = None
            self._thread = threading.Thread(
                target=self._run,
                name="hosxp-polling-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, timeout: float = 15.0) -> bool:
        with self._lock:
            thread = self._thread
            if not thread or not thread.is_alive():
                return False
            self._stop.set()
        thread.join(timeout=timeout)
        return True

    def restart(self) -> None:
        self.stop()
        self.start()

    def _run(self) -> None:
        LOGGER.info("Polling worker started")
        agent: Optional[Agent] = None
        try:
            config = build_agent_config(self.web, self.store)
            agent = Agent(config)
            while not self._stop.is_set():
                try:
                    agent.run_once()
                    agent.run_master_sync_if_due()
                    self._last_error = None
                except Exception as exc:
                    self._last_error = str(exc)
                    LOGGER.error("Polling cycle failed: %s", exc, exc_info=True)
                self._last_poll_at = datetime.now(timezone.utc).isoformat()
                self._poll_count += 1
                self._stop.wait(config.poll_seconds)
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.exception("Polling worker could not start")
        finally:
            if agent is not None:
                agent.close()
            LOGGER.info("Polling worker stopped")

    def status(self) -> Dict[str, object]:
        running = bool(self._thread and self._thread.is_alive())
        return {
            "running": running,
            "last_error": self._last_error,
            "last_poll_at": self._last_poll_at,
            "poll_count": self._poll_count,
        }
