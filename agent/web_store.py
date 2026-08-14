from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional

from .security import (
    CredentialCipher,
    hash_password,
    needs_rehash,
    verify_password,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebStore:
    def __init__(self, path: Path, cipher: CredentialCipher):
        self.path = path
        self.cipher = cipher
        path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()
        os.chmod(self.path, 0o600)
        self.ensure_initial_admin()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            # sqlite3.Connection.__exit__ commits/rolls back but does not close
            # the file handle. Windows therefore keeps temporary test DB files
            # locked unless we explicitly close every short-lived connection.
            connection.close()

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS secure_settings (
                    name TEXT PRIMARY KEY,
                    encrypted_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT
                );
                """
            )

    def ensure_initial_admin(self) -> None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM web_users WHERE username = 'admin'"
            ).fetchone()
            if exists:
                return
            now = _now()
            connection.execute(
                """
                INSERT INTO web_users(
                    username, password_hash, must_change_password,
                    created_at, updated_at
                ) VALUES ('admin', ?, 1, ?, ?)
                """,
                (hash_password("admin"), now, now),
            )
            connection.execute(
                """
                INSERT INTO audit_log(occurred_at, actor, action, detail)
                VALUES (?, 'system', 'initial_admin_created', NULL)
                """,
                (now,),
            )

    def health_check(self) -> None:
        """Raise when the persistent web state cannot be read."""
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_users WHERE username = ?", (username,)
            ).fetchone()
            if row is None or not verify_password(row["password_hash"], password):
                return None
            if needs_rehash(row["password_hash"]):
                connection.execute(
                    "UPDATE web_users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (hash_password(password), _now(), row["id"]),
                )
            return {
                "id": row["id"],
                "username": row["username"],
                "must_change_password": bool(row["must_change_password"]),
            }

    def get_user(self, user_id: int) -> Optional[Dict[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, must_change_password FROM web_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "id": row["id"],
                "username": row["username"],
                "must_change_password": bool(row["must_change_password"]),
            }

    def change_password(self, user_id: int, new_password: str) -> None:
        with self._connect() as connection:
            user = connection.execute(
                "SELECT username FROM web_users WHERE id = ?", (user_id,)
            ).fetchone()
            connection.execute(
                """
                UPDATE web_users
                SET password_hash = ?, must_change_password = 0, updated_at = ?
                WHERE id = ?
                """,
                (hash_password(new_password), _now(), user_id),
            )
            connection.execute(
                """
                INSERT INTO audit_log(occurred_at, actor, action, detail)
                VALUES (?, ?, 'password_changed', NULL)
                """,
                (_now(), user["username"] if user else "unknown"),
            )

    def save_settings(self, values: Dict[str, str], actor: str, action: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO secure_settings(name, encrypted_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    encrypted_value = excluded.encrypted_value,
                    updated_at = excluded.updated_at
                """,
                [
                    (name, self.cipher.encrypt(str(value)), now)
                    for name, value in values.items()
                ],
            )
            connection.execute(
                """
                INSERT INTO audit_log(occurred_at, actor, action, detail)
                VALUES (?, ?, ?, ?)
                """,
                (now, actor, action, ",".join(sorted(values))),
            )

    def get_settings(self, names: list[str]) -> Dict[str, str]:
        if not names:
            return {}
        placeholders = ",".join("?" for _ in names)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT name, encrypted_value FROM secure_settings "
                f"WHERE name IN ({placeholders})",
                names,
            ).fetchall()
        return {
            row["name"]: self.cipher.decrypt(row["encrypted_value"])
            for row in rows
        }

    def database_settings(self) -> Dict[str, str]:
        return self.get_settings(
            ["db_host", "db_port", "db_name", "db_user", "db_password"]
        )

    def api_settings(self) -> Dict[str, str]:
        return self.get_settings(
            [
                "api_base_url",
                "api_username",
                "api_password",
                "api_query_id",
                "query_source",
                "delivery_mode",
                "start_with_windows",
            ]
        )

    def setup_status(self) -> Dict[str, bool]:
        database = self.database_settings()
        api = self.api_settings()
        return {
            "database": all(
                database.get(name)
                for name in ("db_host", "db_port", "db_name", "db_user", "db_password")
            ),
            "api": all(api.get(name) for name in ("api_username", "api_password")),
        }
