"""Current workbench debug state."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from config import DEBUG_DIR


CURRENT_PARSE_PATH = DEBUG_DIR / "current_parse.json"
CURRENT_STRUCTURE_ANALYSIS_PATH = DEBUG_DIR / "current_structure_analysis.json"
CURRENT_SOURCE_INDEX_PATH = DEBUG_DIR / "current_source_index.json"
CURRENT_KTS_CANDIDATES_PATH = DEBUG_DIR / "current_kts_candidates.json"
CURRENT_KTS_EXTRACTION_PATH = DEBUG_DIR / "current_kts_extraction.json"


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def save_current_parse(payload: dict[str, Any]) -> dict[str, Any]:
    return save_current_state(CURRENT_PARSE_PATH, payload)


def save_current_structure_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    return save_current_state(CURRENT_STRUCTURE_ANALYSIS_PATH, payload)


def save_current_source_index(payload: dict[str, Any]) -> dict[str, Any]:
    return save_current_state(CURRENT_SOURCE_INDEX_PATH, payload)


def save_current_kts_candidates(payload: dict[str, Any]) -> dict[str, Any]:
    return save_current_state(CURRENT_KTS_CANDIDATES_PATH, payload)


def save_current_kts_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    return save_current_state(CURRENT_KTS_EXTRACTION_PATH, payload)


def save_current_state(path, payload: dict[str, Any]) -> dict[str, Any]:
    record = {
        "updated_at": timestamp(),
        **payload,
    }
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def load_current_parse() -> dict[str, Any] | None:
    return load_current_state(CURRENT_PARSE_PATH)


def load_current_structure_analysis() -> dict[str, Any] | None:
    return load_current_state(CURRENT_STRUCTURE_ANALYSIS_PATH)


def load_current_source_index() -> dict[str, Any] | None:
    return load_current_state(CURRENT_SOURCE_INDEX_PATH)


def load_current_kts_candidates() -> dict[str, Any] | None:
    return load_current_state(CURRENT_KTS_CANDIDATES_PATH)


def load_current_kts_extraction() -> dict[str, Any] | None:
    return load_current_state(CURRENT_KTS_EXTRACTION_PATH)


def load_current_state(path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
