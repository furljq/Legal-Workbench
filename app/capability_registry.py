"""Capability registry.

A capability is the user-facing unit shown in the workbench. Each capability can
later orchestrate a chain of backend skills.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import CAPABILITIES_DIR


@dataclass(frozen=True)
class Capability:
    capability_id: str
    title: str
    description: str
    status: str
    version: str
    workflow: list[str]
    views: list[str]
    examples: list[str]
    raw: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "version": self.version,
            "workflow": self.workflow,
            "views": self.views,
            "examples": self.examples,
        }


def _load_capability_file(path: Path) -> Capability:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Capability(
        capability_id=data["capability_id"],
        title=data["title"],
        description=data.get("description", ""),
        status=data.get("status", "draft"),
        version=data.get("version", "0.0.0"),
        workflow=list(data.get("workflow", [])),
        views=list(data.get("views", [])),
        examples=list(data.get("examples", [])),
        raw=data,
    )


def load_capabilities() -> list[Capability]:
    capabilities: list[Capability] = []
    for path in sorted(CAPABILITIES_DIR.glob("*/capability.json")):
        capabilities.append(_load_capability_file(path))
    return capabilities


def get_capability(capability_id: str) -> Capability | None:
    for capability in load_capabilities():
        if capability.capability_id == capability_id:
            return capability
    return None

