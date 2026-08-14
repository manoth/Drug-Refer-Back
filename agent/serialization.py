from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict


def normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    if isinstance(value, timedelta):
        # PyMySQL returns MariaDB/MySQL TIME columns as timedelta.  Keep a
        # deterministic, JSON-safe TIME representation (including negative
        # values and hours greater than 23, both supported by MySQL TIME).
        total_microseconds = (
            (value.days * 86_400 + value.seconds) * 1_000_000
            + value.microseconds
        )
        sign = "-" if total_microseconds < 0 else ""
        total_microseconds = abs(total_microseconds)
        total_seconds, microseconds = divmod(total_microseconds, 1_000_000)
        hours, remainder = divmod(total_seconds, 3_600)
        minutes, seconds = divmod(remainder, 60)
        result = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
        if microseconds:
            result += f".{microseconds:06d}".rstrip("0")
        return result
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        # HOSxP tables are commonly declared TIS-620 but production data may
        # contain Windows-874 extensions such as 0xA0 (non-breaking space).
        # CP874 is compatible with Thai TIS-620 characters and defines those
        # extra bytes. Replace only truly invalid bytes instead of failing the
        # whole event batch.
        for encoding in ("cp874", "utf-8"):
            try:
                return value.decode(encoding).replace("\u00a0", " ")
            except UnicodeDecodeError:
                pass
        return value.decode("cp874", errors="replace").replace("\u00a0", " ")
    return value


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key): normalize_value(value) for key, value in row.items()}


def canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(
        normalize_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def row_hash(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
