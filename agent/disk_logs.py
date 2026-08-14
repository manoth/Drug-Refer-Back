from __future__ import annotations

import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple


AGENT_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<logger>\S+) (?P<message>.*)$"
)
SUPERVISOR_LINE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T\S+) (?P<message>.*)$"
)


def _tail_lines(path: Path, limit: int) -> List[str]:
    if limit <= 0 or not path.exists():
        return []
    lines: Deque[str] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines.append(line.rstrip("\r\n"))
    return list(lines)


def _rotated_paths(path: Path, backup_count: int) -> Iterable[Path]:
    for index in range(backup_count, 0, -1):
        yield path.with_name(f"{path.name}.{index}")
    yield path


def _recent_lines(path: Path, limit: int, backup_count: int) -> List[str]:
    collected: List[str] = []
    # Read newest files first so old rotated files are touched only when the
    # current file does not contain enough physical lines.
    paths = list(_rotated_paths(path, backup_count))
    for candidate in reversed(paths):
        remaining = limit - len(collected)
        if remaining <= 0:
            break
        chunk = _tail_lines(candidate, remaining)
        collected[0:0] = chunk
    return collected[-limit:]


def _as_iso(value: str) -> Tuple[str, float]:
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value)
        else:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")
            parsed = parsed.astimezone()
        return parsed.isoformat(), parsed.timestamp()
    except ValueError:
        now = datetime.now().astimezone()
        return now.isoformat(), now.timestamp()


def _parse_agent_lines(lines: Iterable[str]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    last_time: Optional[str] = None
    last_sort = 0.0
    last_level = "INFO"
    for line in lines:
        match = AGENT_LINE.match(line)
        if match:
            timestamp, sort_value = _as_iso(match.group("time"))
            last_time = timestamp
            last_sort = sort_value
            last_level = match.group("level")
            entries.append(
                {
                    "timestamp": timestamp,
                    "level": last_level,
                    "message": (
                        f"{match.group('logger')} — {match.group('message')}"
                    ),
                    "_sort": sort_value,
                }
            )
        elif line:
            if last_time is None:
                last_time, last_sort = _as_iso("")
            entries.append(
                {
                    "timestamp": last_time,
                    "level": last_level,
                    "message": f"traceback — {line}",
                    "_sort": last_sort,
                }
            )
    return entries


def _parse_supervisor_lines(lines: Iterable[str]) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for line in lines:
        match = SUPERVISOR_LINE.match(line)
        if not match:
            continue
        timestamp, sort_value = _as_iso(match.group("time"))
        message = match.group("message")
        warning_words = ("failed", "stopped", "terminating", "return_code")
        level = (
            "WARNING"
            if any(word in message.casefold() for word in warning_words)
            else "INFO"
        )
        entries.append(
            {
                "timestamp": timestamp,
                "level": level,
                "message": f"supervisor — {message}",
                "_sort": sort_value,
            }
        )
    return entries


def read_recent_disk_logs(log_dir: Path, limit: int = 200) -> List[Dict[str, object]]:
    """Return a bounded chronological tail from agent and supervisor logs."""
    safe_limit = max(1, min(int(limit), 500))
    agent_entries = _parse_agent_lines(
        _recent_lines(log_dir / "agent.log", safe_limit, backup_count=3)
    )
    supervisor_entries = _parse_supervisor_lines(
        _recent_lines(log_dir / "supervisor.log", 50, backup_count=2)
    )
    combined = sorted(
        agent_entries + supervisor_entries,
        key=lambda entry: float(entry["_sort"]),
    )[-safe_limit:]
    for entry in combined:
        entry.pop("_sort", None)
    return combined
