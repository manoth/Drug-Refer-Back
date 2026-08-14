from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .state import ChangeEvent


PREVIEW_HISTORY_LIMIT = 20
PREVIEW_FILE_LOCK = threading.RLock()


def _read_tail_unlocked(
    path: Path,
    limit: int = PREVIEW_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    safe_limit = max(1, min(limit, 100))
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        position = source.tell()
        buffer = b""
        while position > 0 and buffer.count(b"\n") <= safe_limit:
            chunk_size = min(64 * 1024, position)
            position -= chunk_size
            source.seek(position)
            buffer = source.read(chunk_size) + buffer

    items: List[Dict[str, Any]] = []
    for raw_line in buffer.splitlines()[-safe_limit:]:
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def _replace_history_unlocked(
    path: Path,
    items: Iterable[Dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in items
        ),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def read_preview_tail(
    path: Path,
    limit: int = PREVIEW_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    with PREVIEW_FILE_LOCK:
        return _read_tail_unlocked(path, limit)


def compact_preview_history(
    path: Path,
    limit: int = PREVIEW_HISTORY_LIMIT,
) -> List[Dict[str, Any]]:
    """Keep only the newest payload cases and return them oldest-first."""
    with PREVIEW_FILE_LOCK:
        items = _read_tail_unlocked(path, limit)
        if path.exists():
            _replace_history_unlocked(path, items)
        return items


def clear_preview_history(path: Path) -> None:
    with PREVIEW_FILE_LOCK:
        _replace_history_unlocked(path, [])


def build_previews(
    endpoint: str,
    events: Iterable[ChangeEvent],
    query_results: Dict[str, List[Dict[str, Any]]],
    query_timing: Optional[Dict[str, Any]] = None,
    dry_run: bool = True,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[ChangeEvent]] = {}
    for event in events:
        if event.vn:
            grouped.setdefault(event.vn, []).append(event)

    generated_at = datetime.now(timezone.utc).isoformat()
    return [
        {
            "dry_run": dry_run,
            "method": "POST",
            "url": endpoint,
            "trigger": {
                "vn": vn,
                "events": [event.trigger_summary() for event in vn_events],
            },
            # The Drug Refer API reads req.body as an array and passes it
            # directly to Knex insert(...). Keep the preview identical to the
            # payload that will be sent when live delivery is enabled.
            "body": query_results.get(vn, []),
            "query": {
                **(query_timing or {}),
                "vn": vn,
                "rows_returned": len(query_results.get(vn, [])),
            },
            "generated_at": generated_at,
        }
        for vn, vn_events in sorted(grouped.items())
    ]


def emit_previews(
    previews: Iterable[Dict[str, Any]],
    output_path: Path,
    echo: bool = True,
) -> None:
    previews = list(previews)
    if not previews:
        return
    for preview in previews:
        if echo:
            print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
    with PREVIEW_FILE_LOCK:
        retained = _read_tail_unlocked(output_path, PREVIEW_HISTORY_LIMIT)
        retained.extend(previews)
        _replace_history_unlocked(
            output_path,
            retained[-PREVIEW_HISTORY_LIMIT:],
        )
