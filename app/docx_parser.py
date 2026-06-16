"""DOCX intake parser for transaction documents."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any


class DocxParseError(RuntimeError):
    """Raised when an uploaded file cannot be parsed as a DOCX document."""


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def classify_heading(text: str, style_name: str) -> int | None:
    normalized = normalize_space(text)
    style = (style_name or "").lower()
    if not normalized:
        return None

    style_match = re.search(r"heading\s*(\d+)|标题\s*(\d+)", style)
    if style_match:
        value = style_match.group(1) or style_match.group(2)
        return int(value) if value else 1

    patterns = [
        (1, r"^第[一二三四五六七八九十百零〇]+[章节篇部分]\b"),
        (1, r"^[一二三四五六七八九十]+、"),
        (2, r"^第[一二三四五六七八九十百零〇]+条\b"),
        (2, r"^\([一二三四五六七八九十]+\)"),
        (2, r"^\d+[.．、]\s*\S+"),
        (3, r"^\d+[.．]\d+"),
    ]
    for level, pattern in patterns:
        if re.search(pattern, normalized):
            return level
    return None


def guess_document_type(file_name: str, paragraphs: list[dict[str, Any]]) -> dict[str, str]:
    sample = "\n".join([file_name, *[item["text"] for item in paragraphs[:40]]])
    if re.search(r"增资协议|增资认购|投资协议|SPA", sample, re.IGNORECASE):
        return {"code": "capital_increase_agreement", "label": "增资协议"}
    if re.search(r"股东协议|股东间协议|SHA", sample, re.IGNORECASE):
        return {"code": "shareholders_agreement", "label": "股东协议"}
    if re.search(r"核心条款|主要条款|关键条款|KTS|条款摘要", sample, re.IGNORECASE):
        return {"code": "kts_summary_or_template", "label": "KTS 摘要或模板"}
    return {"code": "transaction_document", "label": "交易文件"}


def parse_docx_file(path: Path, original_name: str | None = None) -> dict[str, Any]:
    if not zipfile.is_zipfile(path):
        raise DocxParseError("文件不是有效的 DOCX/OOXML 文档。")

    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise DocxParseError("缺少 python-docx 依赖，请先安装 requirements.txt。") from exc

    try:
        document = Document(path)
    except Exception as exc:  # noqa: BLE001
        raise DocxParseError(f"DOCX 读取失败：{exc}") from exc

    file_name = original_name or path.name
    paragraphs: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    body_blocks: list[dict[str, Any]] = []
    paragraph_index = 0
    table_index = 0

    def parse_paragraph(paragraph, block_index: int) -> dict[str, Any] | None:
        nonlocal paragraph_index
        paragraph_index += 1
        text = normalize_space(paragraph.text)
        if not text:
            return None

        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        heading_level = classify_heading(text, style_name)
        item: dict[str, Any] = {
            "index": paragraph_index,
            "paragraph_index": paragraph_index,
            "text": text,
            "style": style_name,
        }
        block_item: dict[str, Any] = {
            "block_index": block_index,
            "kind": "paragraph",
            "paragraph_index": paragraph_index,
            "text": text,
            "style": style_name,
        }
        if heading_level is not None:
            item["heading_level"] = heading_level
            block_item["heading_level"] = heading_level
            headings.append(
                {
                    "index": paragraph_index,
                    "level": heading_level,
                    "text": text,
                }
            )
        paragraphs.append(item)
        return block_item

    def parse_table(table, block_index: int) -> dict[str, Any]:
        nonlocal table_index
        table_index += 1
        rows: list[dict[str, Any]] = []
        column_count = 0
        for row_index, row in enumerate(table.rows, start=1):
            cells = [normalize_space(cell.text) for cell in row.cells]
            column_count = max(column_count, len(cells))
            if not any(cells):
                continue
            row_item = {"row_index": row_index, "cells": cells}
            rows.append(row_item)

        table_item = {
            "index": table_index,
            "table_index": table_index,
            "row_count": len(table.rows),
            "column_count": column_count,
            "rows": rows,
        }
        tables.append(table_item)
        return {
            "block_index": block_index,
            "kind": "table",
            **table_item,
        }

    for block_index, child in enumerate(document.element.body.iterchildren(), start=1):
        if isinstance(child, CT_P):
            block = parse_paragraph(Paragraph(child, document), block_index)
            if block is not None:
                body_blocks.append(block)
            continue
        if isinstance(child, CT_Tbl):
            body_blocks.append(parse_table(Table(child, document), block_index))

    doc_type = guess_document_type(file_name, paragraphs)

    return {
        "file_name": file_name,
        "stored_name": path.name,
        "file_size": path.stat().st_size,
        "status": "parsed",
        "document_type": doc_type,
        "counts": {
            "paragraphs": len(paragraphs),
            "headings": len(headings),
            "tables": len(tables),
            "body_blocks": len(body_blocks),
        },
        "headings": headings,
        "body_blocks": body_blocks,
        "paragraphs": paragraphs,
        "tables": tables,
    }
