"""Build traceable source blocks and retrieval shards from parsed documents."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


MAX_SHARD_CHARS = 800
MIN_SHARD_CHARS = 80
HARD_SPLIT_OVERLAP = 160

CLAUSE_MARKER_RE = re.compile(
    r"(?=(?:第[一二三四五六七八九十百千万零〇两\d]+条|"
    r"\d+(?:\.\d+){0,5}[.、．]\s*|"
    r"[（(][一二三四五六七八九十百千万零〇两\d]+[）)]))"
)
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

    marked = CLAUSE_MARKER_RE.sub("\n", normalized)
    pieces: list[str] = []
    for line in marked.splitlines():
        line = line.strip()
        if not line:
            continue
        pieces.extend(part.strip() for part in STRONG_BOUNDARY_RE.split(line) if part.strip())

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


def paragraph_block(
    document: dict[str, Any],
    doc_id: str,
    block_order: int,
    paragraph: dict[str, Any],
) -> dict[str, Any] | None:
    text = normalize_for_match(str(paragraph.get("text") or ""))
    if not text:
        return None
    source = {"paragraph_index": paragraph.get("paragraph_index", paragraph.get("index"))}
    if paragraph.get("heading_level") is not None:
        source["heading_level"] = paragraph.get("heading_level")
    return {
        "block_id": f"{doc_id}-B{block_order:04d}",
        "doc_id": doc_id,
        "file_name": document.get("file_name", ""),
        "document_role": document.get("document_role", {}),
        "document_type": document.get("document_type", {}),
        "kind": "paragraph",
        "order": block_order,
        "text": text,
        "normalized_text": text,
        "source": source,
        "source_locator": f"原文检索：{short_quote(text)}",
    }


def table_row_block(
    document: dict[str, Any],
    doc_id: str,
    block_order: int,
    table_index: int,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    cells = compact_cells(list(row.get("cells", [])))
    if not cells:
        return None
    text = " | ".join(cells)
    return {
        "block_id": f"{doc_id}-B{block_order:04d}",
        "doc_id": doc_id,
        "file_name": document.get("file_name", ""),
        "document_role": document.get("document_role", {}),
        "document_type": document.get("document_type", {}),
        "kind": "table_row",
        "order": block_order,
        "text": text,
        "normalized_text": text,
        "source": {
            "table_index": table_index,
            "row_index": row.get("row_index"),
            "cells": cells,
        },
        "source_locator": f"表格第{table_index}张第{row.get('row_index', '')}行：{short_quote(text)}",
    }


def build_document_raw_blocks(document: dict[str, Any], doc_index: int) -> tuple[list[dict[str, Any]], list[str]]:
    doc_id = f"D{doc_index:02d}"
    raw_blocks: list[dict[str, Any]] = []
    warnings: list[str] = []
    block_order = 1

    body_blocks = document.get("body_blocks")
    if isinstance(body_blocks, list) and body_blocks:
        for block in body_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("kind") == "paragraph":
                item = paragraph_block(document, doc_id, block_order, block)
                if item is not None:
                    raw_blocks.append(item)
                    block_order += 1
                continue
            if block.get("kind") == "table":
                table_index = int(block.get("table_index") or 0)
                for row in block.get("rows", []):
                    if not isinstance(row, dict):
                        continue
                    item = table_row_block(document, doc_id, block_order, table_index, row)
                    if item is not None:
                        raw_blocks.append(item)
                        block_order += 1
    else:
        warnings.append("当前解析结果未保留段落与表格的正文顺序，表格上下文只能近似处理。")
        for paragraph in document.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
            item = paragraph_block(document, doc_id, block_order, paragraph)
            if item is not None:
                raw_blocks.append(item)
                block_order += 1
        for table in document.get("tables", []):
            if not isinstance(table, dict):
                continue
            table_index = int(table.get("index") or 0)
            for row in table.get("rows", []):
                if not isinstance(row, dict):
                    continue
                item = table_row_block(document, doc_id, block_order, table_index, row)
                if item is not None:
                    raw_blocks.append(item)
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


def build_source_index(parse_record: dict[str, Any]) -> dict[str, Any]:
    parsed_documents = [
        document
        for document in parse_record.get("result", {}).get("documents", [])
        if isinstance(document, dict) and document.get("status") == "parsed"
    ]

    documents: list[dict[str, Any]] = []
    for doc_index, document in enumerate(parsed_documents, start=1):
        doc_id = f"D{doc_index:02d}"
        raw_blocks, warnings = build_document_raw_blocks(document, doc_index)
        search_shards = build_search_shards(raw_blocks)
        canonical_stream = build_canonical_stream(raw_blocks)
        documents.append(
            {
                "doc_id": doc_id,
                "file_name": document.get("file_name", ""),
                "document_role": document.get("document_role", {}),
                "document_type": document.get("document_type", {}),
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
        "phase": "v0.4-source-index",
        "source_parse_updated_at": parse_record.get("updated_at", ""),
        "summary": {
            "document_count": len(documents),
            "raw_block_count": sum(int(document["raw_block_count"]) for document in documents),
            "search_shard_count": sum(int(document["search_shard_count"]) for document in documents),
            "warning_count": sum(len(document.get("warnings", [])) for document in documents),
        },
        "documents": documents,
    }
