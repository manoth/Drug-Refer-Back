from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import pymysql
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .preview import clear_preview_history, compact_preview_history
from . import __version__
from .disk_logs import read_recent_disk_logs
from .remote_query import QueryProvider, RemoteApiError
from .security import (
    CredentialCipher,
    new_csrf_token,
    settings_fingerprint,
    validate_new_password,
    verify_csrf,
)
from .session import ResilientSessionMiddleware
from .web_config import AGENT_DIR, WebConfig
from .web_runtime import (
    BrokerLogHandler,
    LogBroker,
    PollingService,
    build_agent_config,
)
from .web_store import WebStore
from .windows_startup import configure_windows_startup


LOGGER = logging.getLogger("hosxp-polling-agent.web")
DATABASE_FIELDS = ("db_host", "db_port", "db_name", "db_user", "db_password")


@dataclass
class LoginAttempt:
    failures: int = 0
    locked_until: float = 0.0


class LoginLimiter:
    def __init__(self):
        self._attempts: Dict[str, LoginAttempt] = {}
        self._lock = threading.Lock()

    def is_locked(self, key: str) -> int:
        with self._lock:
            attempt = self._attempts.get(key)
            if not attempt or attempt.locked_until <= time.monotonic():
                return 0
            return max(1, int(attempt.locked_until - time.monotonic()))

    def failed(self, key: str) -> None:
        with self._lock:
            attempt = self._attempts.setdefault(key, LoginAttempt())
            attempt.failures += 1
            if attempt.failures >= 5:
                attempt.locked_until = time.monotonic() + min(
                    900, 30 * (2 ** (attempt.failures - 5))
                )

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def _configure_logging(broker: LogBroker, web: WebConfig) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if (
        sys.stderr is not None
        and not any(
            getattr(handler, "agent_terminal", False)
            for handler in root.handlers
        )
    ):
        terminal = logging.StreamHandler()
        terminal.agent_terminal = True  # type: ignore[attr-defined]
        terminal.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(terminal)
    for handler in list(root.handlers):
        if isinstance(handler, BrokerLogHandler) or getattr(
            handler, "agent_file", False
        ):
            root.removeHandler(handler)
            if getattr(handler, "agent_file", False):
                handler.close()

    log_path = web.output_jsonl.parent / "agent.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.agent_file = True  # type: ignore[attr-defined]
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(file_handler)
    web_handler = BrokerLogHandler(broker)
    web_handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
    root.addHandler(web_handler)


def _client_key(request: Request, username: str = "") -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{username.casefold()}"


def _api_setup_error(exc: Exception) -> str:
    message = str(exc)
    if "Cloudflare Challenge" in message:
        return (
            "Cloudflare แสดงหน้า Challenge และยังไม่ส่งคำขอถึงระบบ Login API "
            "ผู้ดูแลโดเมนต้องยกเว้น Challenge สำหรับ /drugrefer/api/ "
            "โดยจำกัดเฉพาะ IP ของเครื่อง Agent หรือใช้การยืนยันตัวตนแบบ service-to-service"
        )
    if "403 Forbidden" in message:
        return (
            "API ปฏิเสธการ Login (HTTP 403 Forbidden) กรุณาตรวจสอบ username/password "
            "และให้ผู้ดูแล API ตรวจสอบการอนุญาต IP หรือกฎ Cloudflare ของเครื่อง Agent"
        )
    if "401 Unauthorized" in message:
        return "API ไม่ยอมรับ username/password (HTTP 401 Unauthorized)"
    return message


def _normalize_api_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API Domain ต้องเป็น URL เช่น https://cpho.dentdata.net")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API Domain ต้องไม่มี username, query string หรือ fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("กรุณาระบุเฉพาะ Domain โดยไม่ใส่ path ต่อท้าย")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("API Domain ภายนอกต้องใช้ HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def _delivery_is_dry_run(web: WebConfig, store: WebStore) -> bool:
    mode = store.api_settings().get("delivery_mode", "")
    if mode in {"dry_run", "live"}:
        return mode != "live"
    return web.dry_run


def _flash(request: Request, kind: str, message: str) -> None:
    request.session["flash"] = {"kind": kind, "message": message}


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return str(token)


def _user(request: Request, store: WebStore) -> Optional[Dict[str, object]]:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return store.get_user(user_id)


def _safe_next_page(user: Dict[str, object], store: WebStore) -> str:
    if user.get("must_change_password"):
        return "/change-password"
    status = store.setup_status()
    if not status["database"]:
        return "/setup/database"
    if not status["api"]:
        return "/setup/api"
    return "/logs"


def _test_database(values: Dict[str, str]) -> Dict[str, Any]:
    connection = pymysql.connect(
        host=values["db_host"],
        port=int(values["db_port"]),
        user=values["db_user"],
        password=values["db_password"],
        database=values["db_name"],
        charset="tis620",
        use_unicode=True,
        connect_timeout=5,
        read_timeout=15,
        write_timeout=10,
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute("SELECT VERSION(), DATABASE(), CURDATE()")
            version, database, current_date = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = %s AND table_name = 'opitemrece'
                """,
                (values["db_name"],),
            )
            if int(cursor.fetchone()[0]) != 1:
                raise RuntimeError("ไม่พบตาราง opitemrece ในฐานข้อมูลที่ระบุ")

            replication_rows = []
            cursor.execute("SHOW SLAVE STATUS")
            replication_rows = cursor.fetchall()
            if not replication_rows:
                cursor.execute("SHOW ALL SLAVES STATUS")
                replication_rows = cursor.fetchall()
            connection.rollback()
        return {
            "version": str(version),
            "database": str(database),
            "date": str(current_date),
            "replication_configured": bool(replication_rows),
            "warning": (
                None
                if replication_rows
                else "เชื่อมต่อได้ แต่ไม่พบสถานะ replication บนเซิร์ฟเวอร์นี้"
            ),
        }
    finally:
        connection.close()


def create_app(config: Optional[WebConfig] = None) -> FastAPI:
    web = config or WebConfig.from_env()
    cipher = CredentialCipher(web.master_key_file)
    store = WebStore(web.web_state_file, cipher)
    broker = LogBroker()
    _configure_logging(broker, web)
    polling = PollingService(web, store)
    limiter = LoginLimiter()
    test_serializer = URLSafeTimedSerializer(
        cipher.signing_secret,
        salt="database-test-result",
    )
    templates = Jinja2Templates(directory=str(AGENT_DIR / "templates"))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        LOGGER.info("Web service started on http://%s:%s", web.host, web.port)
        if all(store.setup_status().values()):
            try:
                polling.start()
            except Exception:
                LOGGER.exception("Automatic polling start failed")
        yield
        polling.stop()
        LOGGER.info("Web service stopped")
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "agent_file", False):
                logging.getLogger().removeHandler(handler)
                handler.close()

    app = FastAPI(title="HOSxP Drug Refer Agent", lifespan=lifespan)
    app.add_middleware(
        ResilientSessionMiddleware,
        secret_key=cipher.signing_secret,
        session_cookie="hosxp_agent_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=web.secure_cookie,
    )
    app.mount("/static", StaticFiles(directory=str(AGENT_DIR / "static")), name="static")

    app.state.web_config = web
    app.state.store = store
    app.state.polling = polling
    app.state.broker = broker

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def render(
        request: Request,
        name: str,
        context: Optional[Dict[str, Any]] = None,
        status_code: int = 200,
    ) -> HTMLResponse:
        user = _user(request, store)
        payload: Dict[str, Any] = {
            "request": request,
            "user": user,
            "csrf_token": _csrf(request),
            "flash": request.session.pop("flash", None),
            "setup": store.setup_status() if user else {},
        }
        if context:
            payload.update(context)
        return templates.TemplateResponse(
            request=request,
            name=name,
            context=payload,
            status_code=status_code,
        )

    def require_login(request: Request) -> Optional[Dict[str, object]]:
        return _user(request, store)

    def csrf_valid(request: Request, supplied: str) -> bool:
        return verify_csrf(str(request.session.get("csrf", "")), supplied)

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        worker = polling.status()
        try:
            store.health_check()
            setup_complete = all(store.setup_status().values())
            healthy = not setup_complete or bool(worker["running"])
            error = None if healthy else "polling worker is not running"
        except Exception as exc:
            LOGGER.error("Web state health check failed: %s", exc, exc_info=True)
            healthy = False
            error = "web state is unavailable"
        return JSONResponse(
            {
                "ok": healthy,
                "version": __version__,
                "worker": worker,
                "error": error,
            },
            status_code=200 if healthy else 503,
        )

    @app.get("/")
    async def index(request: Request):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse(_safe_next_page(user, store), status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        user = require_login(request)
        if user:
            return RedirectResponse(_safe_next_page(user, store), status_code=303)
        return render(request, "login.html")

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
    ):
        if not csrf_valid(request, csrf_token):
            return render(
                request,
                "login.html",
                {"error": "Session หมดอายุ กรุณาลองใหม่"},
                403,
            )
        key = _client_key(request, username)
        wait = limiter.is_locked(key)
        if wait:
            return render(
                request,
                "login.html",
                {"error": f"เข้าสู่ระบบผิดหลายครั้ง กรุณารอ {wait} วินาที"},
                429,
            )
        user = store.authenticate(username.strip(), password)
        if not user:
            limiter.failed(key)
            LOGGER.warning("Web login failed for username=%s", username.strip())
            return render(
                request,
                "login.html",
                {"error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"},
                401,
            )
        limiter.succeeded(key)
        request.session.clear()
        request.session["user_id"] = user["id"]
        request.session["csrf"] = new_csrf_token()
        LOGGER.info("Web login succeeded for username=%s", user["username"])
        return RedirectResponse(_safe_next_page(user, store), status_code=303)

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form(...)):
        if not csrf_valid(request, csrf_token):
            return JSONResponse({"ok": False, "error": "Invalid CSRF"}, 403)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/change-password", response_class=HTMLResponse)
    async def change_password_page(request: Request):
        if not require_login(request):
            return RedirectResponse("/login", status_code=303)
        return render(request, "change_password.html")

    @app.post("/change-password")
    async def change_password(
        request: Request,
        current_password: str = Form(...),
        new_password: str = Form(...),
        confirm_password: str = Form(...),
        csrf_token: str = Form(...),
    ):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not csrf_valid(request, csrf_token):
            return render(
                request,
                "change_password.html",
                {"errors": ["Session หมดอายุ กรุณาลองใหม่"]},
                403,
            )
        key = _client_key(request, f"password-change:{user['username']}")
        wait = limiter.is_locked(key)
        if wait:
            return render(
                request,
                "change_password.html",
                {"errors": [f"ตรวจสอบรหัสผ่านผิดหลายครั้ง กรุณารอ {wait} วินาที"]},
                429,
            )
        verified = store.authenticate(str(user["username"]), current_password)
        if not verified:
            limiter.failed(key)
            LOGGER.warning(
                "Password change rejected: current password verification failed for username=%s",
                user["username"],
            )
            return render(
                request,
                "change_password.html",
                {"errors": ["รหัสผ่านปัจจุบันไม่ถูกต้อง"]},
                400,
            )
        limiter.succeeded(key)
        errors = validate_new_password(new_password, str(user["username"]))
        if new_password == current_password:
            errors.append("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านปัจจุบัน")
        if new_password != confirm_password:
            errors.append("รหัสผ่านยืนยันไม่ตรงกัน")
        if errors:
            return render(request, "change_password.html", {"errors": errors}, 400)
        store.change_password(int(user["id"]), new_password)
        request.session["csrf"] = new_csrf_token()
        _flash(request, "success", "เปลี่ยนรหัสผ่านเรียบร้อยแล้ว")
        LOGGER.info("Admin password changed for username=%s", user["username"])
        updated_user = store.get_user(int(user["id"])) or user
        return RedirectResponse(_safe_next_page(updated_user, store), status_code=303)

    @app.get("/setup/database", response_class=HTMLResponse)
    async def database_page(request: Request):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if user.get("must_change_password"):
            return RedirectResponse("/change-password", status_code=303)
        existing = store.database_settings()
        safe_existing = {key: value for key, value in existing.items() if key != "db_password"}
        return render(request, "database_setup.html", {"values": safe_existing})

    @app.post("/setup/database/test")
    async def database_test(
        request: Request,
        db_host: str = Form(...),
        db_port: str = Form(...),
        db_name: str = Form(...),
        db_user: str = Form(...),
        db_password: str = Form(...),
        csrf_token: str = Form(...),
    ):
        user = require_login(request)
        if not user or user.get("must_change_password"):
            return JSONResponse({"ok": False, "error": "Unauthorized"}, 401)
        if not csrf_valid(request, csrf_token):
            return JSONResponse({"ok": False, "error": "Session หมดอายุ"}, 403)
        values = {
            "db_host": db_host.strip(),
            "db_port": db_port.strip(),
            "db_name": db_name.strip(),
            "db_user": db_user.strip(),
            "db_password": db_password,
        }
        try:
            details = await asyncio.to_thread(_test_database, values)
        except Exception as exc:
            LOGGER.warning("Database connection test failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, 400)
        token = test_serializer.dumps(
            {"fingerprint": settings_fingerprint(values, DATABASE_FIELDS)}
        )
        LOGGER.info(
            "Database connection test succeeded: database=%s version=%s",
            details["database"],
            details["version"],
        )
        return {"ok": True, "test_token": token, "details": details}

    @app.post("/setup/database/save")
    async def database_save(
        request: Request,
        db_host: str = Form(...),
        db_port: str = Form(...),
        db_name: str = Form(...),
        db_user: str = Form(...),
        db_password: str = Form(...),
        test_token: str = Form(...),
        csrf_token: str = Form(...),
    ):
        user = require_login(request)
        if not user or user.get("must_change_password"):
            return RedirectResponse("/login", status_code=303)
        if not csrf_valid(request, csrf_token):
            return render(request, "database_setup.html", {"error": "Session หมดอายุ"}, 403)
        values = {
            "db_host": db_host.strip(),
            "db_port": db_port.strip(),
            "db_name": db_name.strip(),
            "db_user": db_user.strip(),
            "db_password": db_password,
        }
        try:
            signed = test_serializer.loads(test_token, max_age=600)
        except (BadSignature, SignatureExpired):
            signed = {}
        if signed.get("fingerprint") != settings_fingerprint(values, DATABASE_FIELDS):
            return render(
                request,
                "database_setup.html",
                {
                    "error": "ต้องทดสอบการเชื่อมต่ออีกครั้งก่อนบันทึก",
                    "values": {key: value for key, value in values.items() if key != "db_password"},
                },
                400,
            )
        store.save_settings(
            values,
            actor=str(user["username"]),
            action="database_settings_saved",
        )
        _flash(request, "success", "บันทึกการเชื่อมต่อฐานข้อมูลแล้ว")
        LOGGER.info("Encrypted database settings saved")
        return RedirectResponse("/setup/api", status_code=303)

    @app.get("/setup/api", response_class=HTMLResponse)
    async def api_page(request: Request):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if user.get("must_change_password"):
            return RedirectResponse("/change-password", status_code=303)
        if not store.setup_status()["database"]:
            return RedirectResponse("/setup/database", status_code=303)
        existing = store.api_settings()
        return render(
            request,
            "api_setup.html",
            {
                "api_base_url": existing.get("api_base_url", web.api_base_url),
                "api_username": existing.get("api_username", ""),
                "api_query_id": existing.get("api_query_id", "2"),
                "query_source": existing.get("query_source", web.query_source),
                "delivery_mode": existing.get(
                    "delivery_mode",
                    "dry_run" if web.dry_run else "live",
                ),
                "start_with_windows": existing.get(
                    "start_with_windows",
                    "true" if web.start_with_windows else "false",
                ) == "true",
            },
        )

    @app.post("/setup/api")
    async def api_save(
        request: Request,
        api_base_url: str = Form(...),
        query_source: str = Form(...),
        api_query_id: str = Form(...),
        api_username: str = Form(...),
        api_password: str = Form(...),
        csrf_token: str = Form(...),
        delivery_mode: str = Form("live"),
        confirm_live: Optional[str] = Form(None),
        start_with_windows: Optional[str] = Form(None),
    ):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not csrf_valid(request, csrf_token):
            return render(request, "api_setup.html", {"error": "Session หมดอายุ"}, 403)
        try:
            normalized_base_url = _normalize_api_base_url(api_base_url)
            normalized_query_source = query_source.strip().lower()
            if normalized_query_source not in {"remote", "file"}:
                raise ValueError("แหล่ง SQL ต้องเป็น API หรือไฟล์ภายในเครื่อง")
            normalized_query_id = str(int(api_query_id.strip()))
            if not 1 <= int(normalized_query_id) <= 999999:
                raise ValueError("SQL Query ID ต้องอยู่ระหว่าง 1 ถึง 999999")
            normalized_delivery_mode = delivery_mode.strip().lower()
            normalized_start_with_windows = start_with_windows == "yes"
            if normalized_delivery_mode not in {"dry_run", "live"}:
                raise ValueError("โหมดส่งข้อมูลไม่ถูกต้อง")
            if normalized_delivery_mode == "live" and confirm_live != "yes":
                raise ValueError(
                    "กรุณายืนยันการเปิด LIVE POST ก่อนบันทึก การเปิดโหมดนี้จะส่ง VN ที่ค้างในคิวด้วย"
                )
        except ValueError as exc:
            return render(
                request,
                "api_setup.html",
                {
                    "error": str(exc),
                    "api_base_url": api_base_url.strip(),
                    "api_username": api_username.strip(),
                    "api_query_id": api_query_id.strip(),
                    "query_source": query_source.strip().lower(),
                    "delivery_mode": delivery_mode.strip().lower(),
                    "start_with_windows": start_with_windows == "yes",
                },
                400,
            )
        credentials = {
            "api_base_url": normalized_base_url,
            "api_username": api_username.strip(),
            "api_password": api_password,
            "api_query_id": normalized_query_id,
            "query_source": normalized_query_source,
            "delivery_mode": normalized_delivery_mode,
            "start_with_windows": (
                "true" if normalized_start_with_windows else "false"
            ),
        }
        provider: Optional[QueryProvider] = None
        try:
            candidate = build_agent_config(web, store, api_override=credentials)
            candidate = replace(candidate, query_source="remote")
            provider = QueryProvider(candidate)
            await asyncio.to_thread(provider.get_query, True, False)
        except (RemoteApiError, RuntimeError, ValueError) as exc:
            LOGGER.warning("API credential test failed: %s", exc)
            return render(
                request,
                "api_setup.html",
                {
                    "error": _api_setup_error(exc),
                    "api_base_url": normalized_base_url,
                    "api_username": api_username.strip(),
                    "api_query_id": normalized_query_id,
                    "query_source": normalized_query_source,
                    "delivery_mode": normalized_delivery_mode,
                    "start_with_windows": normalized_start_with_windows,
                },
                400,
            )
        finally:
            if provider is not None:
                provider.close()
        store.save_settings(
            credentials,
            actor=str(user["username"]),
            action="api_credentials_saved",
        )
        LOGGER.info("Encrypted API credentials saved; JWT was not persisted")
        try:
            applied = await asyncio.to_thread(
                configure_windows_startup,
                normalized_start_with_windows,
            )
            LOGGER.info(
                "Windows Startup preference saved: enabled=%s registry_applied=%s",
                normalized_start_with_windows,
                applied,
            )
        except OSError as exc:
            LOGGER.warning("Windows Startup preference could not be applied: %s", exc)
            _flash(
                request,
                "error",
                "บันทึกค่าแล้ว แต่แก้ไข Windows Startup ไม่สำเร็จ: " + str(exc),
            )
        try:
            polling.restart()
        except Exception:
            LOGGER.exception("Polling restart after API setup failed")
        _flash(
            request,
            "success",
            ("เชื่อมต่อ API และเปิด LIVE POST แล้ว" if normalized_delivery_mode == "live"
             else "เชื่อมต่อ API และบันทึกโหมดทดสอบแล้ว")
            + (" • เปิด Windows Startup" if normalized_start_with_windows
               else " • ปิด Windows Startup")
        )
        return RedirectResponse("/logs", status_code=303)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        user = require_login(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        next_page = _safe_next_page(user, store)
        if next_page != "/logs":
            return RedirectResponse(next_page, status_code=303)
        dry_run = _delivery_is_dry_run(web, store)
        return render(
            request,
            "logs.html",
            {"worker": polling.status(), "dry_run": dry_run},
        )

    @app.get("/api/status")
    async def api_status(request: Request):
        if not require_login(request):
            return JSONResponse({"ok": False}, 401)
        dry_run = _delivery_is_dry_run(web, store)
        return {
            "ok": True,
            "worker": polling.status(),
            "setup": store.setup_status(),
            "dry_run": dry_run,
        }

    @app.get("/api/previews")
    async def api_previews(request: Request):
        if not require_login(request):
            return JSONResponse({"ok": False}, 401)
        try:
            history = await asyncio.to_thread(
                compact_preview_history,
                web.output_jsonl,
                20,
            )
            # Newest case first; the audit file itself remains untouched.
            items = list(reversed(history))
        except OSError as exc:
            LOGGER.warning("Could not read dry-run previews: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, 500)
        dry_run = _delivery_is_dry_run(web, store)
        return {
            "ok": True,
            "dry_run": dry_run,
            "post_sent": not dry_run,
            "output_file": str(web.output_jsonl),
            "history_count": len(items),
            "items": items,
        }

    @app.post("/api/previews/clear")
    async def clear_previews(
        request: Request,
        csrf_token: str = Form(...),
    ):
        user = require_login(request)
        if not user:
            return JSONResponse({"ok": False}, 401)
        if not csrf_valid(request, csrf_token):
            return JSONResponse({"ok": False, "error": "Invalid CSRF"}, 403)
        try:
            await asyncio.to_thread(clear_preview_history, web.output_jsonl)
        except OSError as exc:
            LOGGER.warning("Could not clear payload history: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, 500)
        LOGGER.info(
            "Payload history cleared by username=%s; polling state was unchanged",
            user["username"],
        )
        return {"ok": True, "items": [], "polling_state_changed": False}

    @app.post("/api/worker/{action}")
    async def worker_action(
        action: str,
        request: Request,
        csrf_token: str = Form(...),
    ):
        if not require_login(request):
            return JSONResponse({"ok": False}, 401)
        if not csrf_valid(request, csrf_token):
            return JSONResponse({"ok": False, "error": "Invalid CSRF"}, 403)
        try:
            if action == "start":
                changed = polling.start()
            elif action == "stop":
                changed = polling.stop()
            elif action == "restart":
                polling.restart()
                changed = True
            elif action == "shutdown":
                changed = polling.stop()
                shutdown_callback = getattr(
                    app.state, "shutdown_callback", None
                )
                if shutdown_callback is None:
                    return JSONResponse(
                        {"ok": False, "error": "Shutdown is unavailable"},
                        400,
                    )
                threading.Timer(0.5, shutdown_callback).start()
            else:
                return JSONResponse({"ok": False, "error": "Unknown action"}, 404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, 400)
        return {
            "ok": True,
            "changed": changed,
            "worker": polling.status(),
            "shutting_down": action == "shutdown",
        }

    @app.get("/api/logs/stream")
    async def logs_stream(request: Request):
        if not require_login(request):
            return JSONResponse({"ok": False}, 401)

        async def stream():
            # The history endpoint returns broker_sequence with its disk tail.
            # Starting from that sequence avoids a gap between persisted history
            # and the realtime stream.
            last_event_id = request.headers.get("last-event-id")
            try:
                sequence = (
                    int(last_event_id)
                    if last_event_id is not None
                    else int(
                        request.query_params.get(
                            "after",
                            str(broker.latest_sequence),
                        )
                    )
                )
            except ValueError:
                sequence = broker.latest_sequence
            while not await request.is_disconnected():
                entries = broker.since(sequence)
                for entry in entries:
                    sequence = entry.sequence
                    yield f"id: {entry.sequence}\ndata: {json.dumps(entry.__dict__, ensure_ascii=False)}\n\n"
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/logs/recent")
    async def recent_logs(request: Request):
        if not require_login(request):
            return JSONResponse({"ok": False}, 401)
        # Capture the broker sequence before reading disk. Any records published
        # afterward are replayed by /api/logs/stream?after=... .
        sequence = broker.latest_sequence
        try:
            items = await asyncio.to_thread(
                read_recent_disk_logs,
                web.output_jsonl.parent,
                200,
            )
        except OSError as exc:
            LOGGER.warning("Could not read persisted agent logs: %s", exc)
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(exc),
                    "broker_sequence": sequence,
                },
                500,
            )
        return {
            "ok": True,
            "items": items,
            "broker_sequence": sequence,
        }

    return app
