from __future__ import annotations

import json
import logging
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from typing import Literal

from itsdangerous import BadSignature, TimestampSigner
from starlette.datastructures import MutableHeaders, Secret
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send


LOGGER = logging.getLogger("hosxp-polling-agent.web-session")


class ResilientSessionMiddleware:
    """Cookie sessions that recover from legacy or malformed signed payloads.

    Starlette's standard middleware only catches a bad signature. A cookie can
    still have a valid signature but contain an obsolete, truncated, or
    otherwise invalid base64/JSON payload. In that case the exception happens
    before FastAPI reaches the route and every page returns a bare HTTP 500.
    Treating that cookie as an expired session lets the login page replace it.
    """

    def __init__(
        self,
        app: ASGIApp,
        secret_key: str | Secret,
        session_cookie: str = "session",
        max_age: int | None = 14 * 24 * 60 * 60,
        path: str = "/",
        same_site: Literal["lax", "strict", "none"] = "lax",
        https_only: bool = False,
        domain: str | None = None,
    ) -> None:
        self.app = app
        self.signer = TimestampSigner(str(secret_key))
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.path = path
        self.security_flags = "httponly; samesite=" + same_site
        if https_only:
            self.security_flags += "; secure"
        if domain is not None:
            self.security_flags += f"; domain={domain}"

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        connection = HTTPConnection(scope)
        initial_session_was_empty = True
        cookie = connection.cookies.get(self.session_cookie)
        if cookie is not None:
            try:
                encoded = self.signer.unsign(
                    cookie.encode("utf-8"),
                    max_age=self.max_age,
                )
                session = json.loads(b64decode(encoded))
                if not isinstance(session, dict):
                    raise ValueError("session payload is not an object")
                scope["session"] = session
                initial_session_was_empty = False
            except (
                BadSignature,
                Base64Error,
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                scope["session"] = {}
                # Mark it non-empty so routes that do not create a new session
                # (for example /healthz) still instruct the browser to delete it.
                initial_session_was_empty = False
                LOGGER.warning("Discarded an unreadable web session cookie")
        else:
            scope["session"] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if scope["session"]:
                    encoded = b64encode(
                        json.dumps(scope["session"]).encode("utf-8")
                    )
                    signed = self.signer.sign(encoded).decode("utf-8")
                    max_age = f"Max-Age={self.max_age}; " if self.max_age else ""
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}={signed}; path={self.path}; "
                        f"{max_age}{self.security_flags}",
                    )
                elif not initial_session_was_empty:
                    headers.append(
                        "Set-Cookie",
                        f"{self.session_cookie}=null; path={self.path}; "
                        "expires=Thu, 01 Jan 1970 00:00:00 GMT; "
                        f"{self.security_flags}",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)
