from __future__ import annotations

import io
import json
import platform
import re
import sys
import zipfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable


SENSITIVE_FIELD = re.compile(
    r"(?i)((?<!\w)[\"']?(?:vn|hn|cid|hos_guid|(?:db_|api_)?password)"
    r"[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|\{[^}\r\n]*\}|[^\s,;]+)"
)
BEARER_TOKEN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")


def redact_diagnostic_text(value: str) -> str:
    value = SENSITIVE_FIELD.sub(r"\1[REDACTED]", value)
    return BEARER_TOKEN.sub(r"\1[REDACTED]", value)


def _existing(paths: Iterable[Path]) -> Iterable[Path]:
    return (path for path in paths if path.is_file())


def _agent_context(log_dir: Path, line_limit: int = 1500) -> str:
    lines: deque[str] = deque(maxlen=line_limit)
    paths = [
        log_dir / "agent.log.3",
        log_dir / "agent.log.2",
        log_dir / "agent.log.1",
        log_dir / "agent.log",
    ]
    for path in _existing(paths):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines.extend(handle)
    return redact_diagnostic_text("".join(lines))


def build_diagnostic_archive(
    log_dir: Path,
    *,
    version: str,
    worker_status: Dict[str, object],
) -> tuple[str, bytes]:
    """Build a bounded, redacted diagnostic ZIP without payload history."""
    generated_at = datetime.now(timezone.utc)
    manifest = {
        "generated_at": generated_at.isoformat(),
        "agent_version": version,
        "platform": platform.platform(),
        "python": sys.version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "worker": worker_status,
        "privacy": (
            "VN, HN, CID, HOS GUID, password-like fields, and bearer tokens "
            "are redacted. post-preview.jsonl is never included."
        ),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "diagnostics.json",
            redact_diagnostic_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str)
            ),
        )
        context = _agent_context(log_dir)
        if context:
            archive.writestr("agent-context-redacted.log", context)

        candidates = [
            *(log_dir / f"error.log.{index}" for index in range(5, 0, -1)),
            log_dir / "error.log",
            *(log_dir / f"supervisor.log.{index}" for index in range(2, 0, -1)),
            log_dir / "supervisor.log",
            log_dir / "startup-error.log",
        ]
        for path in _existing(candidates):
            content = path.read_text(encoding="utf-8", errors="replace")
            archive.writestr(path.name, redact_diagnostic_text(content))

    filename = generated_at.strftime("DrugReferAgent-diagnostics-%Y%m%d-%H%M%SZ.zip")
    return filename, output.getvalue()
