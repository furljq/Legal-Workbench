"""Runtime configuration for Legal Workbench."""

from __future__ import annotations

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
STATIC_DIR = APP_ROOT / "static"
CAPABILITIES_DIR = APP_ROOT / "capabilities"
DEBUG_DIR = PROJECT_ROOT / "debug"

APP_NAME = "Legal Workbench"
APP_VERSION = "0.5.0-dev"

DEFAULT_HOST = os.environ.get("LEGAL_WORKBENCH_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("LEGAL_WORKBENCH_PORT", "8787"))


def ensure_runtime_dirs() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
