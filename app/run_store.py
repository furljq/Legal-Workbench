"""Local run record storage."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from config import RUNS_DIR


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def save_run_record(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or new_run_id())
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "run_id": run_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    (run_dir / "run.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(RUNS_DIR.glob("*/run.json"), reverse=True):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
        if len(records) >= limit:
            break
    return records

