"""DOCX export for reviewed SPA/SHA KTS results."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENTATION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


EAST_ASIA_FONT = "宋体"
TITLE_FONT = "黑体"
GROUP_ORDER = {"SPA": 0, "SHA": 1}
PARENTHETICAL_MARKER_RE = re.compile(r"\s*([（(][一二三四五六七八九十\d]+[）)])")


class KtsDocxExportError(ValueError):
    """Raised when KTS results cannot be exported."""


def split_readable_lines(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    if "\n" in text:
        prepared = text
    else:
        prepared = re.sub(r"\s+", " ", text)
        prepared = re.sub(r"([。；;])\s*", r"\1\n", prepared)
        prepared = re.sub(
            r"([：:])\s*((?:（?[一二三四五六七八九十\d]+[）.)、]))",
            r"\1\n\2",
            prepared,
        )
        prepared = split_parenthetical_markers(prepared)
        prepared = re.sub(r"(其中[，,])\s*", r"\n\1", prepared)
    return [line.strip() for line in prepared.split("\n") if line.strip()]


def split_parenthetical_markers(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in PARENTHETICAL_MARKER_RE.finditer(text):
        before = text[: match.start()].rstrip()
        previous_char = before[-1:] if before else ""
        should_split = not before or previous_char in "：:；;。！？!?、\n"
        if not should_split:
            continue
        parts.append(text[cursor : match.start()].rstrip())
        parts.append("\n")
        parts.append(match.group(1))
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def number_readable_lines(lines: list[str]) -> list[str]:
    if len(lines) <= 1:
        return lines
    numbered: list[str] = []
    for index, line in enumerate(lines, start=1):
        if re.match(r"^(\d+[.、]\s*|（[一二三四五六七八九十\d]+）|\([0-9]+\))", line):
            numbered.append(line)
        else:
            numbered.append(f"{index}. {line}")
    return numbered


def format_kts_content(value: object) -> list[str]:
    return number_readable_lines(split_readable_lines(value))


def saved_human_review(item: dict[str, Any]) -> dict[str, Any] | None:
    review = item.get("human_review")
    if not isinstance(review, dict):
        return None
    if any(key in review for key in ("status", "content", "note", "updated_at")):
        return review
    return None


def export_content_lines(item: dict[str, Any]) -> list[str]:
    review = saved_human_review(item)
    if review is not None:
        content = str(review.get("content") or "").strip()
        return split_readable_lines(content)

    draft_content = str(item.get("draft_content") or "").strip()
    if draft_content:
        return format_kts_content(draft_content)
    if str(item.get("status") or "") == "unclear":
        return ["未见明确约定。"]
    return []


def export_items(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = record.get("items", [])
    if not isinstance(raw_items, list):
        raise KtsDocxExportError("KTS 结果格式不正确。")

    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        group = str(item.get("group") or "其他").strip() or "其他"
        label = str(item.get("label") or item.get("taxonomy_id") or "未命名事项").strip()
        items.append(
            {
                "index": index,
                "group": group,
                "label": label,
                "content_lines": export_content_lines(item),
            }
        )
    if not items:
        raise KtsDocxExportError("暂无可导出的 KTS 事项。")
    return sorted(items, key=lambda item: (GROUP_ORDER.get(str(item["group"]), 99), item["index"]))


def set_cell_text(cell, text: str, bold: bool = False, align=WD_ALIGN_PARAGRAPH.LEFT) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    run = paragraph.add_run(text)
    run.bold = bold
    run.font.name = EAST_ASIA_FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def append_cell_lines(cell, lines: list[str]) -> None:
    cell.text = ""
    if not lines:
        cell.paragraphs[0].text = ""
        return
    for index, line in enumerate(lines):
        paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.space_after = Pt(4)
        paragraph.paragraph_format.line_spacing = 1.15
        run = paragraph.add_run(line)
        run.font.name = EAST_ASIA_FONT
        run.font.size = Pt(10)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def set_cell_width(cell, width_cm: float) -> None:
    cell.width = Cm(width_cm)
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(int(width_cm * 567)))
    tc_w.set(qn("w:type"), "dxa")


def set_repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = f"w:{edge}"
        element = borders.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "B7B7B7")


def set_table_dimensions(table, widths: list[float]) -> None:
    total_width = sum(widths)
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(int(total_width * 567)))
    tbl_w.set(qn("w:type"), "dxa")

    tbl_layout = tbl_pr.find(qn("w:tblLayout"))
    if tbl_layout is None:
        tbl_layout = OxmlElement("w:tblLayout")
        tbl_pr.append(tbl_layout)
    tbl_layout.set(qn("w:type"), "fixed")

    tbl_grid = table._tbl.find(qn("w:tblGrid"))
    if tbl_grid is None:
        tbl_grid = OxmlElement("w:tblGrid")
        table._tbl.insert(1, tbl_grid)
    for child in list(tbl_grid):
        tbl_grid.remove(child)
    for width in widths:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(int(width * 567)))
        tbl_grid.append(grid_col)

    for column, width in zip(table.columns, widths):
        column.width = Cm(width)


def apply_cell_basics(cell, width_cm: float) -> None:
    set_cell_width(cell, width_cm)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragraph in cell.paragraphs:
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(4)


def set_document_defaults(document: Document) -> None:
    section = document.sections[0]
    section.orientation = WD_ORIENTATION.LANDSCAPE
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.54)
    section.right_margin = Cm(2.54)

    normal = document.styles["Normal"]
    normal.font.name = EAST_ASIA_FONT
    normal.font.size = Pt(10)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def add_title(document: Document, export_date: str = "") -> None:
    display_date = export_date.replace("-", ".")
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title.add_run("交易文件主要条款摘要")
    title_run.bold = True
    title_run.font.name = TITLE_FONT
    title_run.font.size = Pt(12)
    title_run._element.rPr.rFonts.set(qn("w:eastAsia"), TITLE_FONT)

    date = document.add_paragraph()
    date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date.add_run(display_date)
    date_run.bold = True
    date_run.font.name = EAST_ASIA_FONT
    date_run.font.size = Pt(12)
    date_run.font.color.rgb = RGBColor(0, 0, 0)
    date_run._element.rPr.rFonts.set(qn("w:eastAsia"), EAST_ASIA_FONT)


def add_table_shell(document: Document, widths: list[float]):
    table = document.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    table.autofit = False
    set_table_dimensions(table, widths)
    set_table_borders(table)

    header = table.rows[0]
    set_repeat_table_header(header)
    for cell, width, text in zip(header.cells, widths, ["#", "事项", "内容"]):
        apply_cell_basics(cell, width)
        set_cell_shading(cell, "A5C9EB")
        set_cell_text(cell, text, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    return table


def add_group_row(table, widths: list[float], group: str) -> None:
    group_row = table.add_row()
    merged = group_row.cells[0].merge(group_row.cells[-1])
    apply_cell_basics(merged, sum(widths))
    set_cell_shading(merged, "DAE9F7")
    set_cell_text(merged, group, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)


def add_item_rows(table, widths: list[float], label: str, content_lines: list[str]) -> None:
    lines = content_lines or [""]
    row = table.add_row()
    for cell, width in zip(row.cells, widths):
        apply_cell_basics(cell, width)
    set_cell_text(row.cells[0], "", align=WD_ALIGN_PARAGRAPH.CENTER)
    set_cell_text(row.cells[1], label, align=WD_ALIGN_PARAGRAPH.LEFT)
    append_cell_lines(row.cells[2], [str(line) for line in lines])


def add_kts_table(document: Document, items: list[dict[str, Any]]) -> None:
    widths = [0.74, 4.25, 19.61]
    for item_index, item in enumerate(items):
        table = add_table_shell(document, widths)
        add_group_row(table, widths, str(item["group"]))
        add_item_rows(table, widths, str(item["label"]), list(item["content_lines"]))
        if item_index < len(items) - 1:
            document.add_paragraph()


def build_kts_docx(record: dict[str, Any], export_date: str = "") -> bytes:
    items = export_items(record)
    document = Document()
    set_document_defaults(document)
    add_title(document, export_date)
    add_kts_table(document, items)

    output = BytesIO()
    document.save(output)
    return output.getvalue()
