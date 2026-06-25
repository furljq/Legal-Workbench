"""Build traceable source blocks and retrieval shards from structured documents."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


MAX_SHARD_CHARS = 800
MIN_SHARD_CHARS = 80
HARD_SPLIT_OVERLAP = 160

STRONG_BOUNDARY_RE = re.compile(r"(?<=[。；;！？!?])")


def normalize_for_match(value: str) -> str:
    """Canonicalize text for retrieval and quote matching without rewriting meaning."""
    text = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", text).strip()


def short_quote(value: str, limit: int = 160) -> str:
    text = normalize_for_match(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def compact_cells(cells: list[Any]) -> list[str]:
    compacted: list[str] = []
    for cell in cells:
        value = normalize_for_match(str(cell or ""))
        if not value:
            continue
        if compacted and compacted[-1] == value:
            continue
        compacted.append(value)
    return compacted


def hard_split(text: str, size: int = MAX_SHARD_CHARS, overlap: int = HARD_SPLIT_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(text):
        chunk = text[start : start + size].strip()
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
        start += step
    return chunks


def split_text_for_search(text: str) -> list[str]:
    normalized = normalize_for_match(text)
    if not normalized:
        return []
    if len(normalized) <= MAX_SHARD_CHARS:
        return [normalized]

    pieces = [part.strip() for part in STRONG_BOUNDARY_RE.split(normalized) if part.strip()]

    segments: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > MAX_SHARD_CHARS:
            if current:
                segments.append(current)
                current = ""
            segments.extend(hard_split(piece))
            continue
        if not current:
            current = piece
            continue
        if len(current) < MIN_SHARD_CHARS or len(current) + 1 + len(piece) <= MAX_SHARD_CHARS:
            current = f"{current} {piece}".strip()
        else:
            segments.append(current)
            current = piece

    if current:
        segments.append(current)

    final_segments: list[str] = []
    for segment in segments:
        if len(segment) <= MAX_SHARD_CHARS:
            final_segments.append(segment)
        else:
            final_segments.extend(hard_split(segment))
    return final_segments


def build_document_raw_blocks(structure_record: dict[str, Any], doc_index: int) -> tuple[list[dict[str, Any]], list[str]]:
    doc_id = f"D{doc_index:02d}"
    raw_blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    block_order = 1

    body_blocks = structure_record.get("body_blocks", [])
    block_mapping = {m["block_index"]: m for m in structure_record.get("block_mapping", []) if isinstance(m, dict)}
    nodes_by_id = {n["id"]: n for n in structure_record.get("nodes", []) if isinstance(n, dict)}

    file_name = structure_record.get("file_name", "")
    document_role = structure_record.get("document_role", {})
    document_type = structure_record.get("document_type", {})

    for block in body_blocks:
        if not isinstance(block, dict):
            continue
        bi = block.get("block_index")
        kind = block.get("kind", "")

        mapping_entry = block_mapping.get(bi, {})
        parent_node_id = mapping_entry.get("parent_node_id") or mapping_entry.get("node_id")
        parent_node = nodes_by_id.get(parent_node_id, {}) if parent_node_id else {}
        clause_context = parent_node.get("title", "") if parent_node else ""

        if kind == "paragraph":
            text = normalize_for_match(str(block.get("text") or ""))
            if not text:
                continue
            source = {"paragraph_index": block.get("paragraph_index", block.get("index"))}
            heading_level = mapping_entry.get("level")
            if heading_level:
                source["heading_level"] = heading_level
            raw_blocks.append({
                "block_id": f"{doc_id}-B{block_order:04d}",
                "doc_id": doc_id,
                "file_name": file_name,
                "document_role": document_role,
                "document_type": document_type,
                "kind": "paragraph",
                "order": block_order,
                "text": text,
                "normalized_text": text,
                "source": source,
                "source_locator": f"{clause_context} | {short_quote(text)}" if clause_context else f"{short_quote(text)}",
                "clause_context": clause_context,
                "node_id": parent_node_id,
            })
            block_order += 1

        elif kind == "table":
            table_index = int(block.get("table_index") or 0)
            for row in block.get("rows", []):
                if not isinstance(row, dict):
                    continue
                cells = compact_cells(list(row.get("cells", [])))
                if not cells:
                    continue
                text = " | ".join(cells)
                raw_blocks.append({
                    "block_id": f"{doc_id}-B{block_order:04d}",
                    "doc_id": doc_id,
                    "file_name": file_name,
                    "document_role": document_role,
                    "document_type": document_type,
                    "kind": "table_row",
                    "order": block_order,
                    "text": text,
                    "normalized_text": text,
                    "source": {
                        "table_index": table_index,
                        "row_index": row.get("row_index"),
                        "cells": cells,
                    },
                    "source_locator": f"{clause_context} | 表格第{table_index}张第{row.get('row_index', '')}行" if clause_context else f"表格第{table_index}张第{row.get('row_index', '')}行",
                    "clause_context": clause_context,
                    "node_id": parent_node_id,
                })
                block_order += 1

    long_block_count = sum(1 for block in raw_blocks if len(str(block.get("text") or "")) > MAX_SHARD_CHARS)
    if long_block_count:
        warnings.append(f"{long_block_count} 个原始文本块超过 {MAX_SHARD_CHARS} 字，已通过检索切片拆分。")
    return raw_blocks, warnings


def build_canonical_stream(raw_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    parts: list[str] = []
    ranges: list[dict[str, Any]] = []
    cursor = 0
    for block in raw_blocks:
        text = str(block.get("normalized_text") or "")
        if not text:
            continue
        if parts:
            parts.append("\n")
            cursor += 1
        start = cursor
        parts.append(text)
        cursor += len(text)
        ranges.append(
            {
                "start": start,
                "end": cursor,
                "block_id": block.get("block_id", ""),
                "kind": block.get("kind", ""),
                "source": block.get("source", {}),
            }
        )
    canonical_text = "".join(parts)
    return {
        "char_count": len(canonical_text),
        "text": canonical_text,
        "ranges": ranges,
    }


def build_search_shards(raw_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    for block in raw_blocks:
        block_text = str(block.get("normalized_text") or "")
        segments = split_text_for_search(block_text)
        for segment_index, segment in enumerate(segments, start=1):
            shard_id = f"{block.get('block_id', '')}-S{segment_index:02d}"
            origin = "block"
            if len(segments) > 1:
                origin = "split"
            if len(segment) >= MAX_SHARD_CHARS:
                origin = "window"
            shards.append(
                {
                    "shard_id": shard_id,
                    "doc_id": block.get("doc_id", ""),
                    "file_name": block.get("file_name", ""),
                    "document_role": block.get("document_role", {}),
                    "document_type": block.get("document_type", {}),
                    "kind": f"{block.get('kind', 'text')}_shard",
                    "origin": origin,
                    "source_block_ids": [block.get("block_id", "")],
                    "text": segment,
                    "normalized_text": normalize_for_match(segment),
                    "source_locator": block.get("source_locator", ""),
                    "character_count": len(segment),
                }
            )
    return shards


def build_source_index(structure_records: list[dict[str, Any]]) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    for doc_index, structure_record in enumerate(structure_records, start=1):
        doc_id = f"D{doc_index:02d}"
        raw_blocks, warnings = build_document_raw_blocks(structure_record, doc_index)
        search_shards = build_search_shards(raw_blocks)
        canonical_stream = build_canonical_stream(raw_blocks)
        documents.append(
            {
                "doc_id": doc_id,
                "file_name": structure_record.get("file_name", ""),
                "document_role": structure_record.get("document_role", {}),
                "document_type": structure_record.get("document_type", {}),
                "raw_block_count": len(raw_blocks),
                "search_shard_count": len(search_shards),
                "canonical_char_count": canonical_stream["char_count"],
                "warnings": warnings,
                "raw_blocks": raw_blocks,
                "search_shards": search_shards,
                "canonical_stream": canonical_stream,
            }
        )

    return {
        "phase": "v0.5-source-index",
        "summary": {
            "document_count": len(documents),
            "raw_block_count": sum(int(document["raw_block_count"]) for document in documents),
            "search_shard_count": sum(int(document["search_shard_count"]) for document in documents),
            "warning_count": sum(len(document.get("warnings", [])) for document in documents),
        },
        "documents": documents,
    }
