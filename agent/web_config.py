from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


AGENT_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", AGENT_DIR.parent)).resolve()

if FROZEN:
    executable_dir = Path(sys.executable).resolve().parent
    local_app_data = os.getenv("LOCALAPPDATA")
    PROJECT_ROOT = (
        Path(local_app_data) / "DrugReferAgent"
        if local_app_data
        else executable_dir / "DrugReferAgentData"
    )
    DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
    DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
    DEFAULT_SQL_FILE = (
        executable_dir / "sql.txt"
        if (executable_dir / "sql.txt").exists()
        else BUNDLE_ROOT / "sql.txt"
    )
    ENV_FILES = (executable_dir / ".env", PROJECT_ROOT / ".env")
else:
    PROJECT_ROOT = AGENT_DIR.parent
    DEFAULT_DATA_DIR = AGENT_DIR / "data"
    DEFAULT_LOG_DIR = AGENT_DIR / "logs"
    DEFAULT_SQL_FILE = PROJECT_ROOT / "sql.txt"
    ENV_FILES = (PROJECT_ROOT / ".env",)


def _path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default)))
    return value if value.is_absolute() else PROJECT_ROOT / value


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int
    auto_open_browser: bool
    start_with_windows: bool
    secure_cookie: bool
    web_state_file: Path
    master_key_file: Path
    polling_state_file: Path
    remote_query_cache: Path
    output_jsonl: Path
    sql_file: Path
    query_source: str
    api_base_url: str
    api_signin_url: str
    api_query_url: str
    post_url: str
    api_token_header: str
    api_token_scheme: str
    api_timeout_seconds: float
    poll_seconds: float
    vn_debounce_seconds: float
    max_vns_per_cycle: int
    db_query_vn_batch_size: int
    db_query_retries: int
    db_read_timeout_seconds: int
    api_post_vn_batch_size: int
    lookback_days: int
    delete_confirm_rounds: int
    max_missing_absolute: int
    max_missing_percent: float
    max_replication_lag_seconds: int
    require_slave_health: bool
    dry_run: bool
    query_refresh_seconds: int
    master_sync_seconds: int = 3600
    master_post_batch_size: int = 50

    @classmethod
    def from_env(cls) -> "WebConfig":
        if load_dotenv is not None:
            for env_file in ENV_FILES:
                if env_file.exists():
                    load_dotenv(env_file)
                    break
        api_base_url = os.getenv(
            "API_BASE_URL", "https://cpho.dentdata.net"
        ).rstrip("/")
        api_root = f"{api_base_url}/drugrefer/api"
        return cls(
            host=os.getenv("WEB_HOST", "127.0.0.1"),
            port=int(os.getenv("WEB_PORT", "8765")),
            auto_open_browser=_bool("AUTO_OPEN_BROWSER", True),
            start_with_windows=_bool("AUTO_START_WINDOWS", True),
            secure_cookie=_bool("SESSION_SECURE_COOKIE", False),
            web_state_file=_path(
                "WEB_STATE_FILE", DEFAULT_DATA_DIR / "web.db"
            ),
            master_key_file=_path(
                "AGENT_MASTER_KEY_FILE", DEFAULT_DATA_DIR / "master.key"
            ),
            polling_state_file=_path(
                "POLLING_STATE_FILE", DEFAULT_DATA_DIR / "polling.db"
            ),
            remote_query_cache=_path(
                "REMOTE_QUERY_CACHE", DEFAULT_DATA_DIR / "remote-query-1.json"
            ),
            output_jsonl=_path(
                "OUTPUT_JSONL", DEFAULT_LOG_DIR / "post-preview.jsonl"
            ),
            sql_file=_path("SQL_FILE", DEFAULT_SQL_FILE),
            query_source=os.getenv("QUERY_SOURCE", "remote").strip().lower(),
            api_base_url=api_base_url,
            api_signin_url=os.getenv(
                "API_SIGNIN_URL",
                f"{api_root}/signIn",
            ),
            api_query_url=os.getenv(
                "API_QUERY_URL",
                f"{api_root}/syncData/query/2",
            ),
            post_url=os.getenv(
                "POST_URL",
                f"{api_root}/syncData/query/2",
            ),
            api_token_header=os.getenv("API_TOKEN_HEADER", "Authorization"),
            api_token_scheme=os.getenv("API_TOKEN_SCHEME", "Bearer"),
            api_timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", "15")),
            poll_seconds=float(os.getenv("POLL_SECONDS", "5")),
            vn_debounce_seconds=float(os.getenv("VN_DEBOUNCE_SECONDS", "3")),
            max_vns_per_cycle=int(os.getenv("MAX_VNS_PER_CYCLE", "100")),
            db_query_vn_batch_size=int(
                os.getenv("DB_QUERY_VN_BATCH_SIZE", "25")
            ),
            db_query_retries=int(os.getenv("DB_QUERY_RETRIES", "2")),
            db_read_timeout_seconds=int(
                os.getenv("DB_READ_TIMEOUT_SECONDS", "120")
            ),
            api_post_vn_batch_size=int(
                os.getenv("API_POST_VN_BATCH_SIZE", "25")
            ),
            lookback_days=int(os.getenv("LOOKBACK_DAYS", "1")),
            delete_confirm_rounds=int(os.getenv("DELETE_CONFIRM_ROUNDS", "3")),
            max_missing_absolute=int(os.getenv("MAX_MISSING_ABSOLUTE", "50")),
            max_missing_percent=float(os.getenv("MAX_MISSING_PERCENT", "5")),
            max_replication_lag_seconds=int(
                os.getenv("MAX_REPLICATION_LAG_SECONDS", "300")
            ),
            require_slave_health=_bool("REQUIRE_SLAVE_HEALTH", False),
            dry_run=_bool("DRY_RUN", False),
            query_refresh_seconds=int(os.getenv("QUERY_REFRESH_SECONDS", "3600")),
            master_sync_seconds=int(os.getenv("MASTER_SYNC_SECONDS", "3600")),
            master_post_batch_size=int(
                os.getenv("MASTER_POST_BATCH_SIZE", "50")
            ),
        )

    def with_server(self, host: str, port: int, auto_open: bool) -> "WebConfig":
        return replace(
            self,
            host=host,
            port=port,
            auto_open_browser=auto_open,
        )
