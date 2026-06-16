"""Deterministic quote verification for KTS source evidence."""

from __future__ import annotations

from typing import Any

from source_index import normalize_for_match


def contains_ellipsis(value: str) -> bool:
    return "..." in value or "…" in value


def match_text(quote: str, source_text: str) -> tuple[bool, str]:
    if not quote or not source_text:
        return False, "empty"
    if contains_ellipsis(quote):
        return False, "ellipsis_not_allowed"
    if quote in source_text:
        return True, "exact"

    normalized_quote = normalize_for_match(quote)
    normalized_source = normalize_for_match(source_text)
    if normalized_quote and normalized_quote in normalized_source:
        return True, "normalized"
    return False, "not_found"


def document_by_id(source_index: dict[str, Any], doc_id: str) -> dict[str, Any] | None:
    for document in source_index.get("documents", []):
        if isinstance(document, dict) and document.get("doc_id") == doc_id:
            return document
    return None


def verify_quote(
    quote: str,
    candidate: dict[str, Any],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    """Verify that a quote can be found in the candidate or source document."""
    candidate_text = str(candidate.get("text") or "")
    matched, method = match_text(quote, candidate_text)
    if matched:
        return {
            "verified": True,
            "match_scope": "candidate",
            "match_method": method,
            "issue": "",
        }

    doc_id = str(candidate.get("doc_id") or "")
    document = document_by_id(source_index, doc_id)
    canonical_text = ""
    if document:
        canonical_stream = document.get("canonical_stream", {})
        if isinstance(canonical_stream, dict):
            canonical_text = str(canonical_stream.get("text") or "")

    matched, method = match_text(quote, canonical_text)
    if matched:
        return {
            "verified": True,
            "match_scope": "document",
            "match_method": method,
            "issue": "outside_candidate_match",
        }

    return {
        "verified": False,
        "match_scope": "none",
        "match_method": method,
        "issue": "quote_mismatch",
    }
