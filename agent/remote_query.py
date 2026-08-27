from __future__ import annotations

import json
import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import Config
from .source import SourceError, load_vn_query, prepare_vn_query


LOGGER = logging.getLogger("hosxp-polling-agent.remote-api")


class RemoteApiError(RuntimeError):
    pass


def _is_cloudflare_challenge(response: httpx.Response) -> bool:
    server = response.headers.get("server", "").casefold()
    content_type = response.headers.get("content-type", "").casefold()
    return (
        response.status_code == 403
        and (server == "cloudflare" or "cf-ray" in response.headers)
        and "text/html" in content_type
    )


def _raise_api_http_error(action: str, exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    if _is_cloudflare_challenge(response):
        raise RemoteApiError(
            f"{action} blocked by Cloudflare Challenge (HTTP 403); "
            "the request did not reach the JSON API"
        ) from exc
    raise RemoteApiError(f"{action} failed: {exc}") from exc


def _find_token(payload: Any) -> Optional[Tuple[str, str]]:
    if isinstance(payload, dict):
        for key in ("jwt", "token", "accessToken", "access_token"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return key, value
        for value in payload.values():
            found = _find_token(value)
            if found:
                return found
    return None


class QueryProvider:
    def __init__(self, config: Config, client: Optional[httpx.Client] = None):
        self.config = config
        self.client = client or httpx.Client(
            timeout=config.api_timeout_seconds,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "HOSxP-DrugRefer-Agent/0.1",
            },
        )
        self._owns_client = client is None
        self._token: Optional[str] = None
        self._query: Optional[str] = None
        self._last_refresh = 0.0

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _login(self) -> None:
        LOGGER.info("API login started: %s", self.config.api_signin_url)
        try:
            response = self.client.post(
                self.config.api_signin_url,
                json={
                    "username": self.config.api_username,
                    "password": self.config.api_password,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            _raise_api_http_error("API login", exc)
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteApiError(f"API login failed: {exc}") from exc

        found = _find_token(payload)
        if not found:
            keys = sorted(payload.keys()) if isinstance(payload, dict) else []
            raise RemoteApiError(
                f"API login response has no JWT/token; response keys={keys}"
            )
        token_key, self._token = found
        LOGGER.info("API login succeeded: token field=%s (value hidden)", token_key)

    def _auth_headers(self) -> Dict[str, str]:
        if not self._token:
            self._login()
        scheme = self.config.api_token_scheme.strip()
        value = f"{scheme} {self._token}".strip() if scheme else str(self._token)
        return {self.config.api_token_header: value}

    def _fetch_remote(self) -> str:
        for attempt in range(2):
            try:
                response = self.client.get(
                    self.config.api_query_url,
                    headers=self._auth_headers(),
                )
                if response.status_code in {401, 403} and attempt == 0:
                    self._token = None
                    continue
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                _raise_api_http_error("GET remote SQL", exc)
            except (httpx.HTTPError, ValueError) as exc:
                raise RemoteApiError(f"GET remote SQL failed: {exc}") from exc

            query = payload.get("query") if isinstance(payload, dict) else None
            if not isinstance(query, str) or not query.strip():
                keys = sorted(payload.keys()) if isinstance(payload, dict) else []
                raise RemoteApiError(
                    f"Remote SQL response has no query string; response keys={keys}"
                )

            effective_query = prepare_vn_query(query)
            previous_digest = (
                hashlib.sha256(self._query.encode("utf-8")).hexdigest()
                if self._query is not None
                else None
            )
            current_digest = hashlib.sha256(
                effective_query.encode("utf-8")
            ).hexdigest()
            if previous_digest != current_digest or not self.config.remote_query_cache.exists():
                self._save_cache(payload, query, effective_query, current_digest)
                LOGGER.info(
                    "Remote SQL updated in RAM and disk cache: hash=%s path=%s",
                    current_digest[:12],
                    self.config.remote_query_cache,
                )
            else:
                LOGGER.info(
                    "Remote SQL hourly check completed: unchanged hash=%s",
                    current_digest[:12],
                )
            return effective_query
        raise RemoteApiError("GET remote SQL was not authorized after JWT refresh")

    def post_payload(
        self,
        payload: List[Dict[str, Any]],
        url: Optional[str] = None,
    ) -> None:
        """POST a raw JSON row array and require the API to acknowledge it."""
        if not isinstance(payload, list) or not payload:
            raise RemoteApiError("POST data requires a non-empty JSON array")
        if not all(isinstance(row, dict) for row in payload):
            raise RemoteApiError("POST data rows must be JSON objects")

        for attempt in range(2):
            try:
                response = self.client.post(
                    url or self.config.post_url,
                    headers=self._auth_headers(),
                    json=payload,
                )
                if response.status_code in {401, 403} and attempt == 0:
                    self._token = None
                    continue
                response.raise_for_status()
                response_payload = response.json()
                if not isinstance(response_payload, dict):
                    raise RemoteApiError(
                        "POST data failed: API response must be a JSON object"
                    )
                if response_payload.get("ok") is not True:
                    message = response_payload.get("message") or "API returned ok=false"
                    raise RemoteApiError(f"POST data was rejected by API: {message}")
                LOGGER.info(
                    "API data POST succeeded: status=%s rows=%d",
                    response.status_code,
                    len(payload),
                )
                return
            except httpx.HTTPStatusError as exc:
                _raise_api_http_error("POST data", exc)
            except ValueError as exc:
                raise RemoteApiError(
                    f"POST data failed: API response is not valid JSON: {exc}"
                ) from exc
            except httpx.HTTPError as exc:
                raise RemoteApiError(f"POST data failed: {exc}") from exc
        raise RemoteApiError("POST data was not authorized after JWT refresh")

    def begin_delivery_round(self) -> None:
        """Authenticate a fresh JWT only after query results are deliverable."""
        self._token = None
        self._login()

    def _save_cache(
        self,
        response_payload: Dict[str, Any],
        query: str,
        effective_query: str,
        query_sha256: str,
    ) -> None:
        cache = {
            "ok": bool(response_payload.get("ok", True)),
            "source_url": self.config.api_query_url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "effective_query": effective_query,
            "query_sha256": query_sha256,
        }
        path = self.config.remote_query_cache
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def _load_cache(self) -> str:
        path = self.config.remote_query_cache
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            query = payload.get("effective_query") or payload.get("query")
            if not isinstance(query, str):
                raise ValueError("cache has no query string")
            effective_query = prepare_vn_query(query)
        except (OSError, ValueError, json.JSONDecodeError, SourceError) as exc:
            raise RemoteApiError(f"Remote SQL cache is unavailable: {exc}") from exc
        LOGGER.warning("Using cached remote SQL: %s", path)
        return effective_query

    def get_query(
        self,
        force_refresh: bool = False,
        allow_cache_fallback: bool = True,
    ) -> str:
        if self.config.query_source == "file":
            return load_vn_query(self.config.sql_file)

        expired = monotonic() - self._last_refresh >= self.config.query_refresh_seconds
        if force_refresh or self._query is None or expired:
            # SQL refreshes are independent from delivery rounds. Authenticate
            # once for the hourly GET and never persist this token.
            self._token = None
            try:
                self._query = self._fetch_remote()
                self._last_refresh = monotonic()
            except RemoteApiError as exc:
                LOGGER.warning("Remote SQL refresh failed: %s", exc)
                if not allow_cache_fallback:
                    raise
                if self._query is None:
                    try:
                        self._query = self._load_cache()
                    except RemoteApiError as cache_exc:
                        raise RemoteApiError(
                            f"{exc}; cached fallback is also unavailable: {cache_exc}"
                        ) from exc
                else:
                    LOGGER.warning(
                        "Continuing with last-known-good SQL already held in RAM"
                    )
                # Do not retry a failed hourly refresh every polling cycle.
                self._last_refresh = monotonic()
        return self._query
