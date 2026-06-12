"""AI service placeholder for v0.2.

Real model calls will be added with the SPA/SHA KTS generation workflow.
"""

from __future__ import annotations

import os


def public_config() -> dict[str, str | bool]:
    return {
        "configured": bool(os.environ.get("LEGAL_WORKBENCH_API_KEY")),
        "model": os.environ.get("LEGAL_WORKBENCH_MODEL", "built-in-placeholder"),
        "base_url": os.environ.get("LEGAL_WORKBENCH_BASE_URL", "built-in"),
    }


def test_connection() -> dict[str, str | bool]:
    return {
        "ok": True,
        "message": "v0.2 placeholder: AI generation is not enabled yet.",
        **public_config(),
    }

