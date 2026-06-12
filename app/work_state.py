"""Current workbench debug state."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from config import DEBUG_DIR


CURRENT_PARSE_PATH = DEBUG_DIR / "current_parse.json"


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_current_parse(payload: dict[str, Any]) -> dict[str, Any]:
    record = {
        "updated_at": timestamp(),
        **payload,
    }
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_PARSE_PATH.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def load_current_parse() -> dict[str, Any] | None:
    if not CURRENT_PARSE_PATH.exists():
        return None
    try:
        data = json.loads(CURRENT_PARSE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
