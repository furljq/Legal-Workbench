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
    """Only detect headings from explicit Word Heading styles."""
    style = (style_name or "").lower()
    if not normalize_space(text):
        return None
    style_match = re.search(r"heading\s*(\d+)|标题\s*(\d+)", style)
    if style_match:
        value = style_match.group(1) or style_match.group(2)
        return int(value) if value else 1
    return None


CHINESE_COUNTING = "零一二三四五六七八九十"
CHINESE_COUNTING_THOUSAND_MAP = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
    11: "十一", 12: "十二", 13: "十三", 14: "十四", 15: "十五",
    16: "十六", 17: "十七", 18: "十八", 19: "十九", 20: "二十",
    21: "二十一", 22: "二十二", 23: "二十三", 24: "二十四", 25: "二十五",
}


def _format_number(value: int, fmt: str) -> str:
    """Format a number according to Word numFmt."""
    if fmt == "decimal":
        return str(value)
    if fmt == "lowerLetter":
        if 1 <= value <= 26:
            return chr(ord('a') + value - 1)
        return str(value)
    if fmt == "upperLetter":
        if 1 <= value <= 26:
            return chr(ord('A') + value - 1)
        return str(value)
    if fmt == "lowerRoman":
        romans = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
                  "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"]
        if 1 <= value <= len(romans):
            return romans[value - 1]
        return str(value)
    if fmt == "upperRoman":
        romans = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
        if 1 <= value <= len(romans):
            return romans[value - 1]
        return str(value)
    if fmt in ("chineseCounting", "chineseCountingThousand"):
        return CHINESE_COUNTING_THOUSAND_MAP.get(value, str(value))
    if fmt == "decimalEnclosedCircleChinese":
        circled = "①②③④⑤⑥⑦⑧⑨⑩"
        if 1 <= value <= len(circled):
            return circled[value - 1]
        return str(value)
    return str(value)


class NumberingResolver:
    """Resolves Word automatic numbering to text prefixes."""

    def __init__(self, document: Any) -> None:
        self._abstract_defs: dict[str, list[dict[str, str]]] = {}
        self._num_to_abstract: dict[str, str] = {}
        self._counters: dict[str, list[int]] = {}
        self._parse_numbering(document)

    def _parse_numbering(self, document: Any) -> None:
        try:
            from docx.oxml.ns import qn
        except ImportError:
            return

        numbering_part = document.part.numbering_part
        if numbering_part is None:
            return
        numbering_xml = numbering_part._element

        for abstract_num in numbering_xml.findall(qn('w:abstractNum')):
            abstract_id = abstract_num.get(qn('w:abstractNumId'), '')
            levels: list[dict[str, str]] = []
            for lvl in abstract_num.findall(qn('w:lvl')):
                ilvl = lvl.get(qn('w:ilvl'), '0')
                num_fmt_el = lvl.find(qn('w:numFmt'))
                lvl_text_el = lvl.find(qn('w:lvlText'))
                start_el = lvl.find(qn('w:start'))
                levels.append({
                    'ilvl': ilvl,
                    'numFmt': num_fmt_el.get(qn('w:val'), 'decimal') if num_fmt_el is not None else 'decimal',
                    'lvlText': lvl_text_el.get(qn('w:val'), '%1.') if lvl_text_el is not None else '%1.',
                    'start': start_el.get(qn('w:val'), '1') if start_el is not None else '1',
                })
            levels.sort(key=lambda x: int(x['ilvl']))
            self._abstract_defs[abstract_id] = levels

        for num in numbering_xml.findall(qn('w:num')):
            num_id = num.get(qn('w:numId'), '')
            abstract_ref = num.find(qn('w:abstractNumId'))
            if abstract_ref is not None:
                self._num_to_abstract[num_id] = abstract_ref.get(qn('w:val'), '')

    def resolve(self, paragraph: Any) -> tuple[str, int | None]:
        """Return (numbering_prefix, ilvl) for a paragraph, or ('', None) if none."""
        try:
            from docx.oxml.ns import qn
        except ImportError:
            return '', None

        p_pr = paragraph._element.find(qn('w:pPr'))
        if p_pr is None:
            return '', None
        num_pr = p_pr.find(qn('w:numPr'))
        if num_pr is None:
            return '', None

        num_id_el = num_pr.find(qn('w:numId'))
        ilvl_el = num_pr.find(qn('w:ilvl'))
        if num_id_el is None:
            return '', None

        num_id = num_id_el.get(qn('w:val'), '0')
        ilvl = int(ilvl_el.get(qn('w:val'), '0')) if ilvl_el is not None else 0

        if num_id == '0':
            return '', None

        abstract_id = self._num_to_abstract.get(num_id, '')
        levels = self._abstract_defs.get(abstract_id, [])
        if not levels or ilvl >= len(levels):
            return '', None

        counter_key = f"{num_id}"
        if counter_key not in self._counters:
            self._counters[counter_key] = [
                int(lvl.get('start', '1')) - 1 for lvl in levels
            ]

        counters = self._counters[counter_key]
        while len(counters) <= ilvl:
            counters.append(0)

        counters[ilvl] += 1
        for i in range(ilvl + 1, len(counters)):
            start_val = int(levels[i]['start']) if i < len(levels) else 1
            counters[i] = start_val - 1

        level_def = levels[ilvl]
        lvl_text = level_def['lvlText']
        num_fmt = level_def['numFmt']

        # For multi-level patterns like %1.%2, use decimal for sub-levels
        # since Chinese legal docs typically render these as "1.1" not "一.一"
        is_multilevel = lvl_text.count('%') > 1

        result = lvl_text
        for i in range(min(ilvl + 1, len(counters))):
            placeholder = f"%{i + 1}"
            if placeholder in result:
                fmt = levels[i]['numFmt'] if i < len(levels) else 'decimal'
                if is_multilevel and fmt in ('chineseCounting', 'chineseCountingThousand'):
                    fmt = 'decimal'
                result = result.replace(placeholder, _format_number(counters[i], fmt))

        if result and not result.endswith((' ', '\t')):
            result = result + ' '

        return result.strip(), ilvl


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
    numbering = NumberingResolver(document)

    def parse_paragraph(paragraph, block_index: int) -> dict[str, Any] | None:
        nonlocal paragraph_index
        paragraph_index += 1
        raw_text = normalize_space(paragraph.text)
        if not raw_text:
            return None

        num_prefix, ilvl = numbering.resolve(paragraph)
        text = f"{num_prefix} {raw_text}".strip() if num_prefix else raw_text

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
        if num_prefix:
            block_item["numbering_prefix"] = num_prefix
            block_item["numbering_ilvl"] = ilvl
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
