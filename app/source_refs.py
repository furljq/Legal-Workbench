"""Utilities for user-facing KTS source references."""

from __future__ import annotations

import re
from typing import Any


DOCX_FILENAME_PREFIX_RE = re.compile(
    r"^\s*[^：:\n\r]{1,180}\.(?:docx|docm|doc)\s*(?:[：:：\-–—]|\s+)+",
    re.IGNORECASE,
)
SOURCE_LOCATOR_PREFIX_RE = re.compile(r"^原文检索[：:]\s*")


def clean_clause_ref(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = SOURCE_LOCATOR_PREFIX_RE.sub("", text).strip()
    previous = ""
    while text and text != previous:
        previous = text
        text = DOCX_FILENAME_PREFIX_RE.sub("", text).strip()
    return text


def clean_clause_refs(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for value in values:
        ref = clean_clause_ref(value)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs
