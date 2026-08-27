from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - allows stdlib-only unit tests
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    if not value.is_absolute():
        value = PROJECT_ROOT / value
    return value


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    lookback_days: int
    poll_seconds: float
    vn_debounce_seconds: float
    max_vns_per_cycle: int
    db_query_vn_batch_size: int
    db_query_retries: int
    db_read_timeout_seconds: int
    api_post_vn_batch_size: int
    delete_confirm_rounds: int
    max_missing_absolute: int
    max_missing_percent: float
    max_replication_lag_seconds: int
    require_slave_health: bool
    dry_run: bool
    sql_file: Path
    state_file: Path
    output_jsonl: Path
    remote_query_cache: Path
    query_source: str
    query_refresh_seconds: int
    master_sync_seconds: int
    master_post_batch_size: int
    api_signin_url: str
    api_query_url: str
    api_username: str
    api_password: str
    api_token_header: str
    api_token_scheme: str
    api_timeout_seconds: float
    post_url: str
    drugitems_post_url: str
    s_drugitems_post_url: str

    @classmethod
    def from_env(cls) -> "Config":
        if load_dotenv is not None:
            load_dotenv(PROJECT_ROOT / ".env")

        query_source = os.getenv("QUERY_SOURCE", "remote").strip().lower()
        required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
        if query_source == "remote":
            required.extend(["API_USERNAME", "API_PASSWORD"])
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        api_base_url = os.getenv(
            "API_BASE_URL", "https://cpho.dentdata.net"
        ).rstrip("/")
        api_root = f"{api_base_url}/drugrefer/api"
        api_query_id = os.getenv("API_QUERY_ID", "2").strip()
        api_query_url = os.getenv(
            "API_QUERY_URL",
            f"{api_root}/syncData/query/{api_query_id}",
        )

        config = cls(
            db_host=os.environ["DB_HOST"],
            db_port=int(os.getenv("DB_PORT", "3306")),
            db_name=os.environ["DB_NAME"],
            db_user=os.environ["DB_USER"],
            db_password=os.environ["DB_PASSWORD"],
            lookback_days=int(os.getenv("LOOKBACK_DAYS", "1")),
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
            delete_confirm_rounds=int(os.getenv("DELETE_CONFIRM_ROUNDS", "3")),
            max_missing_absolute=int(os.getenv("MAX_MISSING_ABSOLUTE", "50")),
            max_missing_percent=float(os.getenv("MAX_MISSING_PERCENT", "5")),
            max_replication_lag_seconds=int(
                os.getenv("MAX_REPLICATION_LAG_SECONDS", "300")
            ),
            require_slave_health=_bool_from_env("REQUIRE_SLAVE_HEALTH", False),
            dry_run=_bool_from_env("DRY_RUN", False),
            sql_file=_path_from_env("SQL_FILE", "sql.txt"),
            state_file=_path_from_env("STATE_FILE", "data/agent.db"),
            output_jsonl=_path_from_env("OUTPUT_JSONL", "logs/post-preview.jsonl"),
            remote_query_cache=_path_from_env(
                "REMOTE_QUERY_CACHE", "data/remote-query-1.json"
            ),
            query_source=query_source,
            query_refresh_seconds=int(os.getenv("QUERY_REFRESH_SECONDS", "3600")),
            master_sync_seconds=int(os.getenv("MASTER_SYNC_SECONDS", "3600")),
            master_post_batch_size=int(
                os.getenv("MASTER_POST_BATCH_SIZE", "50")
            ),
            api_signin_url=os.getenv(
                "API_SIGNIN_URL",
                f"{api_root}/signIn",
            ),
            api_query_url=os.getenv(
                "API_QUERY_URL",
                api_query_url,
            ),
            api_username=os.getenv("API_USERNAME", ""),
            api_password=os.getenv("API_PASSWORD", ""),
            api_token_header=os.getenv("API_TOKEN_HEADER", "Authorization"),
            api_token_scheme=os.getenv("API_TOKEN_SCHEME", "Bearer"),
            api_timeout_seconds=float(os.getenv("API_TIMEOUT_SECONDS", "15")),
            post_url=os.getenv(
                "POST_URL",
                api_query_url,
            ),
            drugitems_post_url=os.getenv(
                "DRUGITEMS_POST_URL",
                f"{api_root}/syncData/query/3",
            ),
            s_drugitems_post_url=os.getenv(
                "S_DRUGITEMS_POST_URL",
                f"{api_root}/syncData/query/4",
            ),
        )

        if config.lookback_days < 0:
            raise ValueError("LOOKBACK_DAYS must be >= 0")
        if config.poll_seconds <= 0:
            raise ValueError("POLL_SECONDS must be > 0")
        if config.vn_debounce_seconds < 0:
            raise ValueError("VN_DEBOUNCE_SECONDS must be >= 0")
        if config.max_vns_per_cycle < 1:
            raise ValueError("MAX_VNS_PER_CYCLE must be >= 1")
        if config.db_query_vn_batch_size < 1:
            raise ValueError("DB_QUERY_VN_BATCH_SIZE must be >= 1")
        if config.db_query_retries < 0:
            raise ValueError("DB_QUERY_RETRIES must be >= 0")
        if config.db_read_timeout_seconds < 1:
            raise ValueError("DB_READ_TIMEOUT_SECONDS must be >= 1")
        if config.api_post_vn_batch_size < 1:
            raise ValueError("API_POST_VN_BATCH_SIZE must be >= 1")
        if config.delete_confirm_rounds < 1:
            raise ValueError("DELETE_CONFIRM_ROUNDS must be >= 1")
        if not 0 <= config.max_missing_percent <= 100:
            raise ValueError("MAX_MISSING_PERCENT must be between 0 and 100")
        if config.query_source not in {"remote", "file"}:
            raise ValueError("QUERY_SOURCE must be remote or file")
        if config.query_refresh_seconds < 1:
            raise ValueError("QUERY_REFRESH_SECONDS must be >= 1")
        if config.master_sync_seconds < 1:
            raise ValueError("MASTER_SYNC_SECONDS must be >= 1")
        if config.master_post_batch_size < 1:
            raise ValueError("MASTER_POST_BATCH_SIZE must be >= 1")
        if config.api_timeout_seconds <= 0:
            raise ValueError("API_TIMEOUT_SECONDS must be > 0")
        return config
