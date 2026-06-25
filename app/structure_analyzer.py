# -*- coding: utf-8 -*-
"""AI-driven document structure analysis.

Converts raw DOCX body_blocks into a structured Markdown representation
with explicit heading hierarchy. Replaces heuristic-based heading detection
with full AI classification.
"""

from __future__ import annotations

import json
from typing import Any

from ai_client import AIClientError, chat_json
from config import DEBUG_DIR


MAX_BLOCK_PREVIEW_CHARS = 150
MAX_TABLE_PREVIEW_CHARS = 60
MAX_BLOCKS_PER_BATCH = 350
BATCH_OVERLAP = 20

SYSTEM_PROMPT = (
    '你是中文法律文件的结构分析器。\n'
    '你的任务是对法律文件的段落列表进行结构标注，判断每个段落是"标题"还是"正文"，并为标题分配层级。\n\n'
    '## 层级定义\n\n'
    '- level 1 (#)：文档标题、章、编、部分（如"增资协议"、"第一章 总则"、"鉴于"、"附件1：xxx"）\n'
    '- level 2 (##)：条（如"1. 本次增资"、"第一条 定义"、"一、定义"）\n'
    '- level 3 (###)：款（如"1.1 总体方案"、"1.2 增资价款的支付"、"（一）定义"）\n'
    '- level 4 (####)：项（如"1.1.1"、"1.2.3"、"4.13"、"6.1.5"、"(1)"、"a."开头的段落）\n\n'
    '## 判断规则\n\n'
    '1. 以法律条款编号开头的段落一律标为标题，不论段落长短。编号格式包括：\n'
    '   - "N."（如1. 2. 3.）→ level 2\n'
    '   - "N.N"（如1.1 1.2 4.5）→ level 3\n'
    '   - "N.N.N"（如1.1.1 6.1.2 4.13.1）→ level 4\n'
    '   - "第N条"→ level 2\n'
    '   - "（N）"或"(N)"→ level 4\n'
    '2. 同一编号序列内的段落必须保持一致性：如果1.2.1是标题，则1.2.2、1.2.3也必须是标题\n'
    '3. 不以编号开头的短段落，如果是对后续内容的概括或章节名（如"鉴于："、"违约和赔偿"），也标为标题\n'
    '4. "鉴于："、"附件N：xxx" 是 level 1 标题\n'
    '5. 表格行永远不是标题\n'
    '6. 不以编号开头且不是章节名的段落，标为正文\n\n'
    '## 输出格式\n\n'
    '输出严格JSON，格式为：\n'
    '{"items": [{"index": 段落序号, "role": "heading"|"body", "level": 1-4或null}]}\n\n'
    '- 每个输入段落都必须出现在输出中\n'
    '- role为"body"时level为null\n'
    '- role为"heading"时level必须是1-4的整数\n'
)


def _prepare_block_entries(body_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract block entries for AI input."""
    entries = []
    for block in body_blocks:
        if not isinstance(block, dict):
            continue
        bi = block.get("block_index")
        if not isinstance(bi, int):
            continue
        kind = block.get("kind", "")
        if kind == "paragraph":
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            entries.append({
                "block_index": bi,
                "kind": "paragraph",
                "preview": text[:MAX_BLOCK_PREVIEW_CHARS],
                "char_count": len(text),
            })
        elif kind == "table":
            rows = block.get("rows", [])
            first_row_text = ""
            if rows and isinstance(rows[0], dict):
                cells = rows[0].get("cells", [])
                first_row_text = " | ".join(str(c) for c in cells)[:MAX_TABLE_PREVIEW_CHARS]
            entries.append({
                "block_index": bi,
                "kind": "table",
                "preview": f"[表格{block.get('row_count', '?')}行: {first_row_text}]",
                "char_count": 0,
            })
    return entries


def _format_entries_for_prompt(entries: list[dict[str, Any]]) -> str:
    lines = []
    for e in entries:
        if e["kind"] == "table":
            lines.append(f'[{e["block_index"]}] {e["preview"]}')
        else:
            suffix = "..." if e["char_count"] > MAX_BLOCK_PREVIEW_CHARS else ""
            lines.append(f'[{e["block_index"]}] {e["preview"]}{suffix}')
    return "\n".join(lines)


def _call_ai_structure(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send entries to AI and get structural annotations."""
    prompt_text = _format_entries_for_prompt(entries)
    user_content = (
        '以下是一份法律文件的段落列表，每行格式为 [序号] 内容预览。\n'
        '请对每个段落判断其角色（标题/正文）和层级。\n\n'
        f'段落列表：\n{prompt_text}\n\n'
        '请输出JSON（只输出JSON，不要其他文字）。'
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    response = chat_json(messages, temperature=0.0)
    items = response.get("items", [])
    if not isinstance(items, list):
        raise AIClientError("AI structure response missing items array")
    return items


def _validate_ai_items(
    ai_items: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and normalize AI response items against original entries."""
    entry_indices = {e["block_index"] for e in entries}
    validated = []
    for item in ai_items:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index not in entry_indices:
            continue
        role = str(item.get("role") or "body")
        if role not in ("heading", "body"):
            role = "body"
        level = item.get("level")
        if role == "heading":
            if not isinstance(level, int) or level not in (1, 2, 3, 4):
                role = "body"
                level = None
        else:
            level = None
        validated.append({"index": index, "role": role, "level": level})
    return validated


def _build_markdown(
    body_blocks: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]],
) -> str:
    """Build Markdown text from body_blocks + AI annotations."""
    lines = []
    for block in body_blocks:
        if not isinstance(block, dict):
            continue
        bi = block.get("block_index")
        kind = block.get("kind", "")

        if kind == "table":
            rows = block.get("rows", [])
            if rows:
                lines.append("")
                first_row = True
                for row in rows:
                    if isinstance(row, dict):
                        cells = row.get("cells", [])
                        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
                        if first_row:
                            lines.append("| " + " | ".join("---" for _ in cells) + " |")
                            first_row = False
                lines.append("")
            continue

        if kind != "paragraph":
            continue

        text = str(block.get("text") or "").strip()
        if not text:
            continue

        ann = annotations.get(bi)
        if ann and ann["role"] == "heading" and ann["level"]:
            prefix = "#" * ann["level"]
            lines.append("")
            lines.append(f"{prefix} {text}")
            lines.append("")
        else:
            lines.append(text)

    return "\n".join(lines)


def _build_nodes(
    body_blocks: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build structural node list from annotations."""
    nodes = []
    node_counter = 0
    heading_stack: list[dict[str, Any]] = []

    block_indices = [
        b.get("block_index") for b in body_blocks
        if isinstance(b, dict) and isinstance(b.get("block_index"), int)
    ]

    for i, bi in enumerate(block_indices):
        ann = annotations.get(bi)
        if not ann or ann["role"] != "heading":
            continue

        node_counter += 1
        level = ann["level"]

        # Find end block_index (next heading or end of doc)
        end_bi = block_indices[-1]
        for j in range(i + 1, len(block_indices)):
            next_bi = block_indices[j]
            next_ann = annotations.get(next_bi)
            if next_ann and next_ann["role"] == "heading" and next_ann["level"] <= level:
                end_bi = block_indices[j - 1] if j > 0 else bi
                break

        # Find parent
        while heading_stack and heading_stack[-1]["level"] >= level:
            heading_stack.pop()
        parent_id = heading_stack[-1]["id"] if heading_stack else None

        block = next(
            (b for b in body_blocks if isinstance(b, dict) and b.get("block_index") == bi),
            None,
        )
        title = str(block.get("text") or "")[:80] if block else ""

        node = {
            "id": f"n{node_counter}",
            "title": title,
            "level": level,
            "block_index": bi,
            "end_block_index": end_bi,
            "parent_id": parent_id,
        }
        nodes.append(node)
        heading_stack.append(node)

    return nodes


def _build_block_mapping(
    body_blocks: list[dict[str, Any]],
    annotations: dict[int, dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build block-level mapping showing each block's role and parent node."""
    node_by_bi = {n["block_index"]: n for n in nodes}
    mapping = []

    for block in body_blocks:
        if not isinstance(block, dict):
            continue
        bi = block.get("block_index")
        if not isinstance(bi, int):
            continue

        ann = annotations.get(bi)
        if ann and ann["role"] == "heading":
            node = node_by_bi.get(bi)
            mapping.append({
                "block_index": bi,
                "role": "heading",
                "level": ann["level"],
                "node_id": node["id"] if node else None,
            })
        else:
            parent_node = None
            for n in reversed(nodes):
                if n["block_index"] <= bi <= n["end_block_index"]:
                    parent_node = n
                    break
            mapping.append({
                "block_index": bi,
                "role": "body",
                "level": None,
                "parent_node_id": parent_node["id"] if parent_node else None,
            })

    return mapping


def structurize_document(document: dict[str, Any]) -> dict[str, Any]:
    """Full AI structure analysis: body_blocks → Markdown + nodes + block_mapping.

    Raises AIClientError if the model is unavailable or returns invalid data.
    """
    body_blocks = document.get("body_blocks")
    if not isinstance(body_blocks, list) or not body_blocks:
        raise AIClientError("Document has no body_blocks to analyze.")

    entries = _prepare_block_entries(body_blocks)
    if not entries:
        raise AIClientError("No analyzable paragraphs in document.")

    # Batch if needed
    all_ai_items: list[dict[str, Any]] = []
    for i in range(0, len(entries), MAX_BLOCKS_PER_BATCH - BATCH_OVERLAP):
        batch = entries[i:i + MAX_BLOCKS_PER_BATCH]
        ai_items = _call_ai_structure(batch)
        validated = _validate_ai_items(ai_items, batch)
        all_ai_items.extend(validated)

    # Deduplicate (overlap batches may produce duplicates)
    seen_indices: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for item in all_ai_items:
        if item["index"] not in seen_indices:
            seen_indices.add(item["index"])
            deduped.append(item)

    # Fill missing entries as body
    annotated_indices = {item["index"] for item in deduped}
    for entry in entries:
        if entry["block_index"] not in annotated_indices:
            deduped.append({"index": entry["block_index"], "role": "body", "level": None})

    annotations = {item["index"]: item for item in deduped}
    markdown = _build_markdown(body_blocks, annotations)
    nodes = _build_nodes(body_blocks, annotations)
    block_mapping = _build_block_mapping(body_blocks, annotations, nodes)

    return {
        "file_name": document.get("file_name", ""),
        "document_role": document.get("document_role", {}),
        "document_type": document.get("document_type", {}),
        "markdown": markdown,
        "nodes": nodes,
        "block_mapping": block_mapping,
        "body_blocks": body_blocks,
        "stats": {
            "total_blocks": len(entries),
            "heading_count": sum(1 for a in annotations.values() if a["role"] == "heading"),
            "body_count": sum(1 for a in annotations.values() if a["role"] == "body"),
        },
    }


def save_debug_markdown(structure_record: dict[str, Any], doc_index: int) -> None:
    """Save Markdown to debug directory for human inspection."""
    md_path = DEBUG_DIR / f"current_structure_D{doc_index:02d}.md"
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    md_path.write_text(structure_record.get("markdown", ""), encoding="utf-8")
