from __future__ import annotations

import argparse
import ctypes
import csv
from datetime import datetime
import ipaddress
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Optional

import uvicorn

from . import __version__
from .web_config import WebConfig
from .webapp import create_app
from .windows_startup import configure_windows_startup


LOGGER = logging.getLogger("hosxp-polling-agent.start")
ERROR_ALREADY_EXISTS = 183
UNEXPECTED_SERVER_EXIT = 70
SUPERVISOR_HEALTH_INTERVAL_SECONDS = 5.0
SUPERVISOR_STARTUP_GRACE_SECONDS = 60.0
SUPERVISOR_MAX_HEALTH_FAILURES = 6
SUPERVISOR_LOG_MAX_BYTES = 512 * 1024
SUPERVISOR_LOG_BACKUPS = 2
TAKEOVER_TIMEOUT_SECONDS = 15.0


class WindowsSingleInstance:
    def __init__(self, port: int):
        self.port = port
        self.handle: Optional[int] = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(
            None,
            False,
            f"Local\\DrugReferAgentWebService-{self.port}",
        )
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        already_running = kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        self.handle = int(handle)
        return not already_running

    def close(self) -> None:
        if self.handle is not None and sys.platform == "win32":
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def _open_browser_when_ready(url: str, timeout: float = 15.0) -> None:
    health_url = url.rstrip("/") + "/healthz"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    # Still open the page so the browser can display a useful connection error.
    webbrowser.open(url)


def _health_is_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/healthz",
            timeout=timeout,
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _running_agent_version(url: str, timeout: float = 2.0) -> Optional[str]:
    """Return the running version, an empty legacy version, or None if offline."""
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/healthz",
            timeout=timeout,
        ) as response:
            if response.status != 200:
                return None
            payload = json.load(response)
            if not isinstance(payload, dict):
                return ""
            return str(payload.get("version") or "")
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _agent_process_ids(tasklist_csv: str, current_pid: int) -> list[int]:
    """Extract other DrugReferAgent process IDs from Windows tasklist CSV."""
    result: list[int] = []
    for row in csv.reader(tasklist_csv.splitlines()):
        if len(row) < 2:
            continue
        image_name = row[0].strip().casefold()
        if not image_name.startswith("drugreferagent") or not image_name.endswith(
            ".exe"
        ):
            continue
        try:
            pid = int(row[1].replace(",", ""))
        except ValueError:
            continue
        if pid != current_pid:
            result.append(pid)
    return result


def _terminate_other_agent_processes(timeout: float = TAKEOVER_TIMEOUT_SECONDS) -> bool:
    """Stop old supervisor/child processes so this executable can take over."""
    if sys.platform != "win32":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    deadline = time.monotonic() + timeout
    current_pid = os.getpid()
    while time.monotonic() < deadline:
        listing = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=flags,
        )
        pids = _agent_process_ids(listing.stdout, current_pid)
        if not pids:
            return True
        for pid in pids:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                check=False,
                creationflags=flags,
            )
        time.sleep(0.25)
    return False


def _acquire_after_takeover(port: int, timeout: float = TAKEOVER_TIMEOUT_SECONDS) -> Optional[WindowsSingleInstance]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidate = WindowsSingleInstance(port)
        if candidate.acquire():
            return candidate
        candidate.close()
        time.sleep(0.25)
    return None


def _write_supervisor_log(config: WebConfig, message: str) -> None:
    try:
        log_path = config.output_jsonl.parent / "supervisor.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            log_path.exists()
            and log_path.stat().st_size >= SUPERVISOR_LOG_MAX_BYTES
        ):
            for index in range(SUPERVISOR_LOG_BACKUPS, 0, -1):
                source = (
                    log_path
                    if index == 1
                    else log_path.with_name(f"{log_path.name}.{index - 1}")
                )
                target = log_path.with_name(f"{log_path.name}.{index}")
                if not source.exists():
                    continue
                if target.exists():
                    target.unlink()
                source.replace(target)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"{datetime.now().astimezone().isoformat()} {message}\n"
            )
    except OSError:
        # The supervisor must keep recovering the service even if diagnostics
        # cannot be written because the disk is temporarily unavailable.
        pass


def _server_child_command(config: WebConfig) -> list[str]:
    return [
        str(sys.executable),
        "--server-child",
        "--host",
        config.host,
        "--port",
        str(config.port),
        "--no-browser",
    ]


def parse_args() -> argparse.Namespace:
    defaults = WebConfig.from_env()
    parser = argparse.ArgumentParser(description="Start the HOSxP Agent web service")
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Started automatically after Windows sign-in",
    )
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def _run_web_service(config: WebConfig) -> int:
    """Run one web-service child.

    Exit code 0 is reserved for an intentional Exit Agent request. Any other
    return asks the frozen Windows supervisor to start a fresh child.
    """
    browser_host = "127.0.0.1" if config.host in {"0.0.0.0", "::"} else config.host
    app = create_app(config)
    saved_startup = app.state.store.api_settings().get("start_with_windows")
    start_with_windows = (
        saved_startup == "true"
        if saved_startup in {"true", "false"}
        else config.start_with_windows
    )
    try:
        configure_windows_startup(start_with_windows)
    except OSError as exc:
        LOGGER.warning("Windows startup registration failed: %s", exc)
    try:
        is_loopback = ipaddress.ip_address(config.host).is_loopback
    except ValueError:
        is_loopback = config.host in {"localhost"}
    initial_admin = app.state.store.get_user(1)
    if not is_loopback and initial_admin and initial_admin.get("must_change_password"):
        raise SystemExit(
            "Refusing to expose admin/admin on a non-loopback address. "
            "Complete first-time setup on 127.0.0.1 before changing WEB_HOST."
        )
    if config.auto_open_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(f"http://{browser_host}:{config.port}/",),
            name="agent-browser-opener",
            daemon=True,
        ).start()
    server_config = uvicorn.Config(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(server_config)
    shutdown_requested = threading.Event()

    def shutdown_callback() -> None:
        shutdown_requested.set()
        server.should_exit = True

    app.state.shutdown_callback = shutdown_callback
    try:
        server.run()
    except Exception:
        LOGGER.exception("Web service terminated with an unhandled exception")
        return UNEXPECTED_SERVER_EXIT
    if shutdown_requested.is_set():
        LOGGER.info("Web service exited by explicit Exit Agent request")
        return 0
    LOGGER.error("Web service stopped unexpectedly; supervisor restart requested")
    return UNEXPECTED_SERVER_EXIT


def _run_frozen_windows_supervisor(
    config: WebConfig,
    url: str,
    open_browser: bool,
    allow_takeover: bool = False,
) -> int:
    instance = WindowsSingleInstance(config.port)
    if not instance.acquire():
        instance.close()
        running_version = _running_agent_version(url)
        if not allow_takeover or running_version == __version__:
            if open_browser:
                _open_browser_when_ready(url)
            return 0
        _write_supervisor_log(
            config,
            "Update takeover requested: "
            f"running_version={running_version or 'legacy/unknown'} "
            f"new_version={__version__}",
        )
        if not _terminate_other_agent_processes():
            _write_supervisor_log(
                config,
                "Update takeover failed: old Agent processes did not stop",
            )
            return UNEXPECTED_SERVER_EXIT
        replacement = _acquire_after_takeover(config.port)
        if replacement is None:
            _write_supervisor_log(
                config,
                "Update takeover failed: single-instance lock was not released",
            )
            return UNEXPECTED_SERVER_EXIT
        instance = replacement
        _write_supervisor_log(
            config,
            f"Update takeover succeeded: starting version={__version__}",
        )

    if open_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(url,),
            name="agent-browser-opener",
            daemon=True,
        ).start()

    restart_count = 0
    try:
        while True:
            command = _server_child_command(config)
            started_at = time.monotonic()
            _write_supervisor_log(
                config,
                f"Starting web-service child restart_count={restart_count}",
            )
            try:
                child = subprocess.Popen(
                    command,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                restart_count += 1
                _write_supervisor_log(
                    config,
                    f"Child start failed error={exc!r}; retrying",
                )
                time.sleep(min(30.0, 2.0 * restart_count))
                continue

            health_failures = 0
            while child.poll() is None:
                time.sleep(SUPERVISOR_HEALTH_INTERVAL_SECONDS)
                if _health_is_ready(url):
                    health_failures = 0
                    continue
                if (
                    time.monotonic() - started_at
                    <= SUPERVISOR_STARTUP_GRACE_SECONDS
                ):
                    continue
                health_failures += 1
                if health_failures < SUPERVISOR_MAX_HEALTH_FAILURES:
                    continue
                _write_supervisor_log(
                    config,
                    "Health check failed repeatedly; terminating hung child",
                )
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
                break

            return_code = child.poll()
            if return_code == 0:
                _write_supervisor_log(
                    config,
                    "Web-service child exited intentionally; supervisor stopped",
                )
                return 0

            restart_count = (
                0 if time.monotonic() - started_at >= 300 else restart_count + 1
            )
            delay = min(30.0, max(2.0, 2.0 * restart_count))
            _write_supervisor_log(
                config,
                f"Web-service child stopped return_code={return_code}; "
                f"restarting_in={delay:.0f}s",
            )
            time.sleep(delay)
    finally:
        instance.close()


def main() -> int:
    args = parse_args()
    config = WebConfig.from_env().with_server(
        args.host,
        args.port,
        not args.no_browser and not args.startup and not args.server_child,
    )
    browser_host = "127.0.0.1" if config.host in {"0.0.0.0", "::"} else config.host
    url = f"http://{browser_host}:{config.port}/"

    if (
        sys.platform == "win32"
        and getattr(sys, "frozen", False)
        and not args.server_child
    ):
        return _run_frozen_windows_supervisor(
            config,
            url,
            open_browser=not args.no_browser and not args.startup,
            allow_takeover=not args.startup,
        )

    if args.server_child:
        return _run_web_service(config)

    instance = WindowsSingleInstance(config.port)
    if not instance.acquire():
        instance.close()
        if not args.no_browser and not args.startup:
            _open_browser_when_ready(url)
        return 0
    try:
        # Source/development runs remain a single process. The Windows EXE uses
        # the supervisor path above for automatic crash and hang recovery.
        _run_web_service(config)
        return 0
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
