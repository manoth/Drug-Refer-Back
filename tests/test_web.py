from __future__ import annotations

import os
import re
import stat
import tempfile
import unittest
import shutil
import zipfile
import io
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from agent.web_config import WebConfig
from agent.assets import prepare_web_assets
from agent.disk_logs import read_recent_disk_logs
from agent.diagnostics import build_diagnostic_archive
from agent.remote_query import RemoteApiError
from agent.preview import read_preview_tail
from agent.webapp import (
    _api_setup_error,
    _normalize_api_base_url,
    create_app,
)


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("CSRF token not found")
    return match.group(1)


class WebWizardTests(unittest.TestCase):
    def test_diagnostic_archive_redacts_identifiers_and_excludes_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            (log_dir / "agent.log").write_text(
                'INFO event vn=69000001 hos_guid={secret-guid} "cid":"1234567890123"\n',
                encoding="utf-8",
            )
            (log_dir / "error.log").write_text(
                "ERROR request password=secret Bearer abc.def.ghi\n",
                encoding="utf-8",
            )
            (log_dir / "post-preview.jsonl").write_text(
                '{"cid":"1234567890123"}\n',
                encoding="utf-8",
            )

            _, content = build_diagnostic_archive(
                log_dir,
                version="test-version",
                worker_status={"running": True},
            )

            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                self.assertNotIn("post-preview.jsonl", archive.namelist())
                combined = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in archive.namelist()
                )
            self.assertNotIn("69000001", combined)
            self.assertNotIn("secret-guid", combined)
            self.assertNotIn("1234567890123", combined)
            self.assertNotIn("password=secret", combined)
            self.assertNotIn("abc.def.ghi", combined)
            self.assertIn("[REDACTED]", combined)

    def test_frozen_web_assets_survive_bundle_directory_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "_MEI12345" / "agent"
            persistent = root / "local-app-data" / "DrugReferAgent"
            (bundle / "templates").mkdir(parents=True)
            (bundle / "static").mkdir()
            for relative in (
                "templates/base.html",
                "templates/login.html",
                "templates/logs.html",
                "static/app.css",
                "static/app.js",
            ):
                path = bundle / relative
                path.write_text(f"asset:{relative}", encoding="utf-8")

            asset_root = prepare_web_assets(
                bundle,
                persistent,
                frozen=True,
                version="test-version",
            )
            shutil.rmtree(root / "_MEI12345")

            self.assertEqual(
                "asset:templates/login.html",
                (asset_root / "templates/login.html").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "asset:static/app.js",
                (asset_root / "static/app.js").read_text(encoding="utf-8"),
            )

    def test_disk_log_tail_combines_rotated_agent_and_supervisor_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            (log_dir / "agent.log.1").write_text(
                "2026-07-23 10:00:00,000 INFO old.logger old entry\n",
                encoding="utf-8",
            )
            (log_dir / "agent.log").write_text(
                "2026-07-23 10:01:00,000 ERROR new.logger cycle failed\n"
                "Traceback detail\n",
                encoding="utf-8",
            )
            (log_dir / "supervisor.log").write_text(
                "2026-07-23T10:02:00+07:00 "
                "Web-service child stopped return_code=70\n",
                encoding="utf-8",
            )

            items = read_recent_disk_logs(log_dir, 10)

            self.assertEqual(4, len(items))
            self.assertIn("old.logger — old entry", items[0]["message"])
            self.assertEqual("ERROR", items[1]["level"])
            self.assertIn("traceback — Traceback detail", items[2]["message"])
            self.assertEqual("WARNING", items[3]["level"])
            self.assertIn("supervisor", items[3]["message"])

    def test_preview_tail_reads_only_latest_twenty_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.jsonl"
            path.write_text(
                "".join(
                    f'{{"case":{index},"text":"ภาษาไทย"}}\n'
                    for index in range(1, 26)
                ),
                encoding="utf-8",
            )

            items = read_preview_tail(path, 20)

            self.assertEqual(20, len(items))
            self.assertEqual(6, items[0]["case"])
            self.assertEqual(25, items[-1]["case"])

    def test_api_domain_normalization_and_validation(self) -> None:
        self.assertEqual(
            "https://cpho.dentdata.net",
            _normalize_api_base_url("https://cpho.dentdata.net/"),
        )
        with self.assertRaisesRegex(ValueError, "เฉพาะ Domain"):
            _normalize_api_base_url("https://cpho.dentdata.net/drugrefer/api")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _normalize_api_base_url("http://cpho.dentdata.net")

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.config = WebConfig(
            host="127.0.0.1",
            port=8765,
            auto_open_browser=False,
            start_with_windows=False,
            secure_cookie=False,
            web_state_file=root / "web.db",
            master_key_file=root / "master.key",
            polling_state_file=root / "polling.db",
            remote_query_cache=root / "remote-query.json",
            output_jsonl=root / "preview.jsonl",
            sql_file=Path(__file__).resolve().parents[1] / "sql.txt",
            query_source="remote",
            api_base_url="https://example.test",
            api_signin_url="https://example.test/signIn",
            api_query_url="https://example.test/query/1",
            post_url="https://example.test/query/1",
            api_token_header="Authorization",
            api_token_scheme="Bearer",
            api_timeout_seconds=5,
            poll_seconds=10,
            vn_debounce_seconds=10,
            max_vns_per_cycle=100,
            db_query_vn_batch_size=25,
            db_query_retries=2,
            db_read_timeout_seconds=120,
            api_post_vn_batch_size=25,
            lookback_days=1,
            delete_confirm_rounds=3,
            max_missing_absolute=50,
            max_missing_percent=5,
            max_replication_lag_seconds=300,
            require_slave_health=True,
            dry_run=True,
            query_refresh_seconds=300,
        )
        self.app = create_app(self.config)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.tempdir.cleanup()

    def login_and_change_password(self) -> str:
        response = self.client.get("/login")
        self.assertEqual(200, response.status_code)
        token = csrf_from(response.text)
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "admin", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual("/change-password", response.headers["location"])

        response = self.client.get("/change-password")
        token = csrf_from(response.text)
        response = self.client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "Str0ng!Pass",
                "confirm_password": "Str0ng!Pass",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        self.assertEqual("/setup/database", response.headers["location"])
        return token

    def test_password_change_requires_current_password_every_time(self) -> None:
        response = self.client.get("/login")
        token = csrf_from(response.text)
        self.client.post(
            "/login",
            data={"username": "admin", "password": "admin", "csrf_token": token},
        )
        response = self.client.get("/change-password")
        token = csrf_from(response.text)
        rejected = self.client.post(
            "/change-password",
            data={
                "current_password": "wrong-password",
                "new_password": "Str0ng!Pass",
                "confirm_password": "Str0ng!Pass",
                "csrf_token": token,
            },
        )
        self.assertEqual(400, rejected.status_code)
        self.assertIn("รหัสผ่านปัจจุบันไม่ถูกต้อง", rejected.text)
        self.assertIsNotNone(self.app.state.store.authenticate("admin", "admin"))

        token = csrf_from(rejected.text)
        accepted = self.client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "Str0ng!Pass",
                "confirm_password": "Str0ng!Pass",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        self.assertEqual("/setup/database", accepted.headers["location"])
        self.assertIsNone(self.app.state.store.authenticate("admin", "admin"))
        self.assertIsNotNone(
            self.app.state.store.authenticate("admin", "Str0ng!Pass")
        )

    def test_initial_login_forces_password_change(self) -> None:
        response = self.client.get("/login")
        self.assertIn("v1.6.0", response.text)
        token = csrf_from(response.text)
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "admin", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(303, response.status_code)
        self.assertEqual("/change-password", response.headers["location"])
        if os.name != "nt":
            self.assertEqual(
                0o600,
                stat.S_IMODE(self.config.master_key_file.stat().st_mode),
            )

    def test_login_recovers_from_signed_but_unreadable_session_cookie(self) -> None:
        signer = TimestampSigner(self.app.state.store.cipher.signing_secret)

        for invalid_payload in (b"%%%", b"eHh4", b"/w=="):
            with self.subTest(payload=invalid_payload):
                signed = signer.sign(invalid_payload).decode("ascii")
                response = self.client.get(
                    "/login",
                    headers={"cookie": f"hosxp_agent_session={signed}"},
                )

                self.assertEqual(200, response.status_code)
                self.assertIn('name="csrf_token"', response.text)
                self.assertIn(
                    "hosxp_agent_session=",
                    response.headers.get("set-cookie", ""),
                )

    def test_healthz_reports_version_and_web_state(self) -> None:
        response = self.client.get("/healthz")

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["ok"])
        self.assertEqual("1.6.0", response.json()["version"])

    def test_authenticated_user_can_download_diagnostic_zip(self) -> None:
        self.login_and_change_password()
        logging.getLogger("diagnostic-test").error("test diagnostic failure")

        response = self.client.get("/api/logs/diagnostics")

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/zip", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn("diagnostics.json", archive.namelist())
            self.assertIn("error.log", archive.namelist())

    def test_healthz_fails_when_configured_worker_is_stopped(self) -> None:
        with patch.object(
            self.app.state.store,
            "setup_status",
            return_value={"database": True, "api": True},
        ):
            response = self.client.get("/healthz")

        self.assertEqual(503, response.status_code)
        self.assertFalse(response.json()["ok"])
        self.assertIn("worker", response.json()["error"])

    def test_recent_logs_endpoint_returns_persisted_tail_and_sequence(self) -> None:
        log_path = self.config.output_jsonl.parent / "agent.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-07-23 10:03:00,000 WARNING test.logger persisted issue\n"
            )
        response = self.client.get("/login")
        token = csrf_from(response.text)
        self.client.post(
            "/login",
            data={
                "username": "admin",
                "password": "admin",
                "csrf_token": token,
            },
        )

        response = self.client.get("/api/logs/recent")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIsInstance(payload["broker_sequence"], int)
        self.assertTrue(
            any(
                "persisted issue" in item["message"]
                for item in payload["items"]
            )
        )

    def test_api_403_message_does_not_report_missing_cache(self) -> None:
        message = _api_setup_error(
            RemoteApiError("API login failed: 403 Forbidden")
        )
        self.assertIn("403 Forbidden", message)
        self.assertIn("Cloudflare", message)
        self.assertNotIn("cache", message.casefold())

    def test_cloudflare_challenge_message_explains_server_side_fix(self) -> None:
        message = _api_setup_error(
            RemoteApiError(
                "API login blocked by Cloudflare Challenge (HTTP 403); "
                "the request did not reach the JSON API"
            )
        )
        self.assertIn("ยังไม่ส่งคำขอถึงระบบ Login API", message)
        self.assertIn("/drugrefer/api/", message)

    def test_database_cannot_save_without_successful_test(self) -> None:
        self.login_and_change_password()
        response = self.client.get("/setup/database")
        token = csrf_from(response.text)
        self.assertIn('href="/setup/database"', response.text)
        self.assertIn('aria-label="ขั้นตอน 3 ยังไม่เปิดใช้งาน"', response.text)
        self.assertNotIn('href="/setup/api"', response.text)
        values = {
            "db_host": "127.0.0.1",
            "db_port": "3306",
            "db_name": "hos",
            "db_user": "reader",
            "db_password": "db-secret",
            "csrf_token": token,
            "test_token": "invalid",
        }
        response = self.client.post("/setup/database/save", data=values)
        self.assertEqual(400, response.status_code)
        self.assertIn("ต้องทดสอบการเชื่อมต่ออีกครั้ง", response.text)

    def test_full_wizard_encrypts_credentials(self) -> None:
        self.login_and_change_password()
        response = self.client.get("/setup/database")
        token = csrf_from(response.text)
        values = {
            "db_host": "127.0.0.1",
            "db_port": "3306",
            "db_name": "hos",
            "db_user": "reader",
            "db_password": "db-secret",
            "csrf_token": token,
        }
        with patch(
            "agent.webapp._test_database",
            return_value={
                "version": "10.5.21-MariaDB",
                "database": "hos",
                "date": "2026-07-20",
                "replication_configured": True,
                "warning": None,
            },
        ):
            tested = self.client.post("/setup/database/test", data=values)
        self.assertEqual(200, tested.status_code)
        values["test_token"] = tested.json()["test_token"]
        saved = self.client.post(
            "/setup/database/save", data=values, follow_redirects=False
        )
        self.assertEqual("/setup/api", saved.headers["location"])

        response = self.client.get("/setup/api")
        token = csrf_from(response.text)
        provider = MagicMock()
        provider.get_query.return_value = "SELECT o.vn FROM opitemrece o WHERE o.vn = ?"
        with patch("agent.webapp.QueryProvider", return_value=provider), patch.object(
            self.app.state.polling, "restart"
        ), patch(
            "agent.webapp.configure_windows_startup", return_value=True
        ) as startup_config:
            saved = self.client.post(
                "/setup/api",
                data={
                    "api_base_url": "https://api.example.test",
                    "query_source": "remote",
                    "api_query_id": "2",
                    "api_username": "api-user",
                    "api_password": "api-secret",
                    "delivery_mode": "dry_run",
                    "start_with_windows": "yes",
                    "csrf_token": token,
                },
                follow_redirects=False,
            )
        self.assertEqual("/logs", saved.headers["location"])
        provider.get_query.assert_called_once_with(True, False)
        self.assertEqual(
            "https://api.example.test",
            self.app.state.store.api_settings()["api_base_url"],
        )
        self.assertEqual("2", self.app.state.store.api_settings()["api_query_id"])
        candidate_config = self.app.state.store.api_settings()
        self.assertEqual("remote", candidate_config["query_source"])
        self.assertEqual("dry_run", candidate_config["delivery_mode"])
        self.assertEqual("true", candidate_config["start_with_windows"])
        startup_config.assert_called_once_with(True)

        returned_database = self.client.get("/setup/database")
        self.assertIn(
            'aria-label="ขั้นตอน 2 ฐานข้อมูล เสร็จแล้ว"',
            returned_database.text,
        )
        self.assertIn('class="step-node done current"', returned_database.text)
        self.assertIn('href="/change-password"', returned_database.text)
        self.assertIn('href="/setup/api"', returned_database.text)
        self.assertIn('href="/logs"', returned_database.text)

        self.config.output_jsonl.write_text(
            '{"dry_run":true,"generated_at":"2026-07-20T10:00:00+00:00","trigger":{"vn":"test-vn"},"body":[{"vn":"test-vn","version":1}]}\n'
            '{"dry_run":true,"generated_at":"2026-07-20T10:01:00+00:00","trigger":{"vn":"test-vn"},"body":[{"vn":"test-vn","version":2}]}\n',
            encoding="utf-8",
        )
        previews = self.client.get("/api/previews")
        self.assertEqual(200, previews.status_code)
        self.assertTrue(previews.json()["dry_run"])
        self.assertFalse(previews.json()["post_sent"])
        self.assertEqual(2, previews.json()["history_count"])
        self.assertEqual(2, len(previews.json()["items"]))
        self.assertEqual("test-vn", previews.json()["items"][0]["body"][0]["vn"])
        self.assertEqual(2, previews.json()["items"][0]["body"][0]["version"])
        self.assertEqual(1, previews.json()["items"][1]["body"][0]["version"])

        cleared = self.client.post(
            "/api/previews/clear",
            data={"csrf_token": token},
        )
        self.assertEqual(200, cleared.status_code)
        self.assertFalse(cleared.json()["polling_state_changed"])
        self.assertEqual("", self.config.output_jsonl.read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertEqual(
                0o600,
                stat.S_IMODE(self.config.output_jsonl.stat().st_mode),
            )
        self.assertEqual([], self.client.get("/api/previews").json()["items"])

        database_bytes = self.config.web_state_file.read_bytes()
        self.assertNotIn(b"db-secret", database_bytes)
        self.assertNotIn(b"api-secret", database_bytes)
        self.assertEqual("db-secret", self.app.state.store.database_settings()["db_password"])
        self.assertEqual("api-secret", self.app.state.store.api_settings()["api_password"])


if __name__ == "__main__":
    unittest.main()
