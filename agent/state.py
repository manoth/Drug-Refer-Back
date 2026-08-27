from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .serialization import canonical_json, row_hash


@dataclass(frozen=True)
class ChangeEvent:
    event_id: str
    event_type: str
    hos_guid: str
    vn: Optional[str]
    before: Optional[Dict[str, Any]]
    after: Optional[Dict[str, Any]]
    detected_at: str

    def trigger_summary(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "hos_guid": self.hos_guid,
            "vn": self.vn,
            "detected_at": self.detected_at,
        }


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._create_schema()
        os.chmod(path, 0o600)

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS row_state (
                hos_guid TEXT PRIMARY KEY,
                vn TEXT,
                row_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                missing_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS event_outbox (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                hos_guid TEXT NOT NULL,
                vn TEXT,
                before_json TEXT,
                after_json TEXT,
                detected_at TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS ix_event_outbox_vn
                ON event_outbox(vn, detected_at);

            CREATE TABLE IF NOT EXISTS master_row_state (
                table_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (table_name, row_key)
            );
            """
        )
        self.connection.commit()

    def is_initialized(self) -> bool:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'initialized'"
        ).fetchone()
        return row is not None and row["value"] == "true"

    def bootstrap(self, snapshot: Dict[str, Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.execute("DELETE FROM row_state")
            self.connection.executemany(
                """
                INSERT INTO row_state(
                    hos_guid, vn, row_hash, payload_json,
                    first_seen_at, last_seen_at, missing_count
                ) VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                [
                    (
                        hos_guid,
                        payload.get("vn"),
                        row_hash(payload),
                        canonical_json(payload),
                        now,
                        now,
                    )
                    for hos_guid, payload in snapshot.items()
                ],
            )
            self.connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES ('initialized', 'true')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    def _load_states(self) -> Dict[str, sqlite3.Row]:
        rows = self.connection.execute("SELECT * FROM row_state").fetchall()
        return {row["hos_guid"]: row for row in rows}

    def pending_events(self, include_previewed: bool = False) -> List[ChangeEvent]:
        statuses = ("pending", "previewed") if include_previewed else ("pending",)
        markers = ", ".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"""
            SELECT * FROM event_outbox
            WHERE delivery_status IN ({markers})
            ORDER BY detected_at, event_id
            """,
            statuses,
        ).fetchall()
        return [
            ChangeEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                hos_guid=row["hos_guid"],
                vn=row["vn"],
                before=json.loads(row["before_json"]) if row["before_json"] else None,
                after=json.loads(row["after_json"]) if row["after_json"] else None,
                detected_at=row["detected_at"],
            )
            for row in rows
        ]

    def mark_events(self, event_ids: List[str], status: str) -> None:
        if not event_ids:
            return
        with self.connection:
            self.connection.executemany(
                """
                UPDATE event_outbox SET delivery_status = ?
                WHERE event_id = ?
                """,
                [(status, event_id) for event_id in event_ids],
            )

    def acknowledge_events(self, event_ids: List[str]) -> None:
        """Remove events from the retry queue only after a successful POST."""
        if not event_ids:
            return
        with self.connection:
            self.connection.executemany(
                "DELETE FROM event_outbox WHERE event_id = ?",
                [(event_id,) for event_id in event_ids],
            )

    def changed_master_rows(
        self,
        table: str,
        snapshot: Dict[str, Dict[str, Any]],
    ) -> List[Tuple[str, Dict[str, Any], str]]:
        """Return rows whose current hash has not yet been acknowledged."""
        rows = self.connection.execute(
            "SELECT row_key, row_hash FROM master_row_state WHERE table_name = ?",
            (table,),
        ).fetchall()
        synced = {row["row_key"]: row["row_hash"] for row in rows}
        changed: List[Tuple[str, Dict[str, Any], str]] = []
        for row_key, payload in snapshot.items():
            digest = row_hash(payload)
            if synced.get(row_key) != digest:
                changed.append((row_key, payload, digest))
        return changed

    def acknowledge_master_rows(
        self,
        table: str,
        rows: List[Tuple[str, Dict[str, Any], str]],
    ) -> None:
        """Persist hashes only after the API has acknowledged the batch."""
        if not rows:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self.connection:
            self.connection.executemany(
                """
                INSERT INTO master_row_state(
                    table_name, row_key, row_hash, payload_json, synced_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(table_name, row_key) DO UPDATE SET
                    row_hash = excluded.row_hash,
                    payload_json = excluded.payload_json,
                    synced_at = excluded.synced_at
                """,
                [
                    (table, row_key, digest, canonical_json(payload), now)
                    for row_key, payload, digest in rows
                ],
            )

    @staticmethod
    def _is_in_scope(payload: Dict[str, Any], server_date: date, lookback_days: int) -> bool:
        start = server_date - timedelta(days=lookback_days)
        end = server_date + timedelta(days=1)
        for field in ("vstdate", "rxdate"):
            value = payload.get(field)
            if not value:
                continue
            try:
                candidate = date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
            if start <= candidate < end:
                return True
        return False

    @staticmethod
    def _new_event(
        event_type: str,
        hos_guid: str,
        vn: Optional[str],
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
        detected_at: str,
    ) -> ChangeEvent:
        return ChangeEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            hos_guid=hos_guid,
            vn=vn,
            before=before,
            after=after,
            detected_at=detected_at,
        )

    def apply_snapshot(
        self,
        snapshot: Dict[str, Dict[str, Any]],
        server_date: date,
        lookback_days: int,
        delete_confirm_rounds: int,
        max_missing_absolute: int,
        max_missing_percent: float,
    ) -> Tuple[List[ChangeEvent], Optional[str]]:
        now = datetime.now(timezone.utc).isoformat()
        states = self._load_states()
        events: List[ChangeEvent] = []

        eligible_states: Dict[str, sqlite3.Row] = {}
        stale_guids: List[str] = []
        for hos_guid, state in states.items():
            payload = json.loads(state["payload_json"])
            if self._is_in_scope(payload, server_date, lookback_days):
                eligible_states[hos_guid] = state
            else:
                stale_guids.append(hos_guid)

        missing_guids = set(eligible_states) - set(snapshot)
        percentage_limit = math.ceil(
            len(eligible_states) * (max_missing_percent / 100.0)
        )
        missing_limit = max(max_missing_absolute, percentage_limit)
        missing_guard = len(missing_guids) > missing_limit
        warning = None
        if missing_guard:
            warning = (
                f"Missing guard activated: {len(missing_guids)} rows disappeared; "
                f"limit is {missing_limit}. DELETE inference skipped."
            )

        with self.connection:
            for hos_guid in stale_guids:
                self.connection.execute(
                    "DELETE FROM row_state WHERE hos_guid = ?", (hos_guid,)
                )

            for hos_guid, payload in snapshot.items():
                digest = row_hash(payload)
                current = states.get(hos_guid)
                vn = payload.get("vn")
                if current is None:
                    event = self._new_event(
                        "INSERT", hos_guid, vn, None, payload, now
                    )
                    events.append(event)
                    self.connection.execute(
                        """
                        INSERT INTO row_state(
                            hos_guid, vn, row_hash, payload_json,
                            first_seen_at, last_seen_at, missing_count
                        ) VALUES (?, ?, ?, ?, ?, ?, 0)
                        """,
                        (hos_guid, vn, digest, canonical_json(payload), now, now),
                    )
                elif current["row_hash"] != digest:
                    before = json.loads(current["payload_json"])
                    event = self._new_event(
                        "UPDATE", hos_guid, vn or current["vn"], before, payload, now
                    )
                    events.append(event)
                    self.connection.execute(
                        """
                        UPDATE row_state
                        SET vn = ?, row_hash = ?, payload_json = ?,
                            last_seen_at = ?, missing_count = 0
                        WHERE hos_guid = ?
                        """,
                        (vn, digest, canonical_json(payload), now, hos_guid),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE row_state
                        SET last_seen_at = ?, missing_count = 0
                        WHERE hos_guid = ?
                        """,
                        (now, hos_guid),
                    )

            if not missing_guard:
                for hos_guid in missing_guids:
                    current = eligible_states[hos_guid]
                    next_count = current["missing_count"] + 1
                    if next_count >= delete_confirm_rounds:
                        before = json.loads(current["payload_json"])
                        event = self._new_event(
                            "DELETE_INFERRED",
                            hos_guid,
                            current["vn"],
                            before,
                            None,
                            now,
                        )
                        events.append(event)
                        self.connection.execute(
                            "DELETE FROM row_state WHERE hos_guid = ?", (hos_guid,)
                        )
                    else:
                        self.connection.execute(
                            """
                            UPDATE row_state SET missing_count = ?
                            WHERE hos_guid = ?
                            """,
                            (next_count, hos_guid),
                        )

            self.connection.executemany(
                """
                INSERT INTO event_outbox(
                    event_id, event_type, hos_guid, vn,
                    before_json, after_json, detected_at, delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                [
                    (
                        event.event_id,
                        event.event_type,
                        event.hos_guid,
                        event.vn,
                        canonical_json(event.before) if event.before else None,
                        canonical_json(event.after) if event.after else None,
                        event.detected_at,
                    )
                    for event in events
                ],
            )

        return events, warning
