"""KTS candidate retrieval and evidence-first extraction draft."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from functools import lru_cache
import json
import re
import time
from pathlib import Path
from typing import Any

from ai_client import AIClientError, ai_configured, ai_max_workers, chat_json, is_transient_ai_error
from config import CAPABILITIES_DIR
from evidence_verifier import verify_quote
from source_refs import clean_clause_refs
from source_index import normalize_for_match


MAX_CANDIDATES_PER_ITEM = 8
MAX_ANCHOR_SHARDS_PER_ITEM = 18
MAX_CANDIDATE_CHARS = 2000
MAX_TABLE_CANDIDATE_CHARS = 5000
MAX_AI_REVIEW_CANDIDATES = 8
MAX_AI_SCAN_SHARDS = 120
MAX_EXTRACTION_EVIDENCE = MAX_CANDIDATES_PER_ITEM
SOURCE_BLOCK_OVERLAP_DUPLICATE_RATIO = 0.70
EVIDENCE_TEXT_SIMILARITY_DUPLICATE_RATIO = 0.82
EVIDENCE_QUOTE_SIMILARITY_DUPLICATE_RATIO = 0.86
MIN_SIMILARITY_TEXT_CHARS = 80
MAX_ADAPTIVE_MODEL_RETRIES = 2
ADAPTIVE_RETRY_SLEEP_SECONDS = 2
MAX_ABSENCE_SNIPPETS_PER_CHECK = 8
ABSENCE_SNIPPET_RADIUS = 180
MAX_STYLE_POLISH_CHARS_PER_ITEM = 1800
STYLE_POLISH_TIMEOUT_SECONDS = 240
TAXONOMY_PATH = CAPABILITIES_DIR / "spa_sha_kts" / "kts_taxonomy.json"
CONTENT_SCHEMA_PATH = CAPABILITIES_DIR / "spa_sha_kts" / "kts_content_schema.json"
ProgressCallback = Callable[[dict[str, Any]], None]

EXTRA_TERMS = {
    "spa.transaction_arrangement": ["交易安排", "融资额", "投资款", "投前估值", "投后估值", "股权结构", "签署方"],
    "spa.closing": ["交割日", "付款通知", "出资证明", "工商变更", "股东名册"],
    "spa.closing_conditions": ["交割先决条件", "先决条件", "重大不利", "投委会", "尽职调查", "法律意见书"],
    "spa.representations_warranties": ["陈述及保证", "陈述保证", "资料真实", "知识产权", "合法合规"],
    "spa.post_closing_covenants": ["交割后承诺", "资金用途", "整改", "重组", "实缴", "持续任职"],
    "spa.termination": ["解除", "终止", "最后期限", "long stop"],
    "spa.liability": ["违约责任", "赔偿", "补偿", "连带责任", "责任上限"],
    "spa.expenses": ["费用", "律师费", "交易费用"],
    "spa.compliance": ["反腐败", "反商业贿赂", "道德合规", "廉洁", "利益输送", "代持"],
    "spa.other": ["排他", "独家", "保密", "适用法律", "争议解决", "仲裁", "通知", "送达", "权利义务转让", "协议生效", "附件"],
    "sha.board_composition": ["董事会构成", "董事会由", "董事", "委派", "观察员", "董事长"],
    "sha.board_reserved_matters": [
        "董事会保护性事项",
        "董事会重大事项",
        "重大事项决策",
        "投资人董事同意",
        "投资方董事",
        "董事会批准",
        "保护性事项",
    ],
    "sha.shareholder_reserved_matters": [
        "股东会保护性事项",
        "股东会重大事项",
        "重大事项决策",
        "股东会",
        "股东同意",
        "多数投资人",
        "特别决议",
        "保护性事项",
    ],
    "sha.preemptive_right": ["优先认购权", "优先认购", "新增注册资本", "新增发行", "二次认购权"],
    "sha.transfer_restriction": ["股权转让限制", "转股限制", "锁定", "竞对", "QIPO"],
    "sha.rofr_tag": ["优先购买权", "共同出售权", "共售权", "随售权", "二次购买权", "ROFR"],
    "sha.anti_dilution": ["反稀释", "棘轮", "全棘轮", "加权平均", "广义加权平均", "价格调整"],
    "sha.esop": ["员工股权激励", "期权池", "员工持股", "ESOP", "后续融资"],
    "sha.information_audit": ["信息权", "检查权", "审计权", "独立审计权", "财务报表", "预算"],
    "sha.redemption": ["特殊回购权", "回购触发事项", "回购义务人", "回购价款", "利益输送"],
    "sha.dividend": ["分红权", "利润分配", "股利", "不得分红"],
    "sha.liquidation_preference": ["优先清算", "清算事件", "视同清算", "清算顺位", "优先清算额", "剩余财产"],
    "sha.other": ["常规回购权", "领售权", "最惠国待遇", "创始人全职", "全职付出", "不竞争义务", "竞业限制"],
}


def load_kts_taxonomy(path: Path = TAXONOMY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_kts_content_schema(path: Path = CONTENT_SCHEMA_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(flatten_strings(child))
        return items
    if isinstance(value, list):
        items = []
        for child in value:
            items.extend(flatten_strings(child))
        return items
    return []


def content_schema_for_item(
    item: dict[str, Any],
    content_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = content_schema or load_kts_content_schema()
    items = schema.get("items", {}) if isinstance(schema, dict) else {}
    if not isinstance(items, dict):
        return {}
    item_id = str(item.get("id") or item.get("taxonomy_id") or "")
    raw_item = items.get(item_id, {})
    if not isinstance(raw_item, dict):
        return {}
    merged = dict(raw_item)
    merged["schema_id"] = schema.get("schema_id", "")
    merged["schema_version"] = schema.get("version", "")
    if not merged.get("drafting_guidance"):
        merged["drafting_guidance"] = schema.get("default_drafting_guidance", "")
    return merged


def enrich_taxonomy_item(
    item: dict[str, Any],
    content_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enriched = dict(item)
    enriched["content_schema"] = content_schema_for_item(item, content_schema)
    return enriched


def content_schema_terms(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    terms: list[str] = []
    fields = schema.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            terms.append(str(field.get("label") or ""))
            aliases = field.get("aliases", [])
            if isinstance(aliases, list):
                terms.extend(str(alias) for alias in aliases)
    return terms


def split_label_terms(label: str) -> list[str]:
    values = [label]
    for piece in re.split(r"[/&、及和\s]+", label):
        piece = piece.strip()
        if piece:
            values.append(piece)
    return values


def collect_terms(item: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    terms.extend(split_label_terms(str(item.get("label") or "")))
    terms.extend(flatten_strings(item.get("template_labels", {})))
    terms.extend(str(keyword) for keyword in item.get("keywords", []))
    terms.extend(flatten_strings(item.get("absence_checks", [])))
    terms.extend(content_schema_terms(item.get("content_schema", {})))
    terms.extend(EXTRA_TERMS.get(str(item.get("id") or ""), []))

    seen: set[str] = set()
    normalized_terms: list[str] = []
    for term in terms:
        normalized = normalize_for_match(term)
        if not normalized:
            continue
        if len(normalized) < 2 and normalized.upper() not in {"AI"}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_terms.append(normalized)
    normalized_terms.sort(key=lambda value: (-len(value), value))
    return normalized_terms


def exact_quote(value: str, limit: int = 180) -> str:
    text = normalize_for_match(value)
    if len(text) <= limit:
        return text
    for delimiter in ["。", "；", ";", "！", "？"]:
        position = text.find(delimiter)
        if 0 < position + 1 <= limit:
            return text[: position + 1]
    return text[:limit].rstrip()


def document_type_code(value: Any) -> str:
    return str(value.get("code") or "") if isinstance(value, dict) else ""


def score_shard(item: dict[str, Any], shard: dict[str, Any], terms: list[str]) -> tuple[int, list[str]]:
    allowed_types = set(item.get("document_types") or [])
    shard_doc_type = document_type_code(shard.get("document_type", {}))
    if allowed_types and shard_doc_type not in allowed_types:
        return 0, []

    text = str(shard.get("normalized_text") or "")
    term_score_total = 0
    reasons: list[str] = []

    for term in terms:
        if term and term in text:
            term_score = max(2, min(8, len(term) // 2))
            term_score_total += term_score
            reasons.append(f"命中：{term}")

    if term_score_total <= 0:
        return 0, []

    score = term_score_total
    if allowed_types and shard_doc_type in allowed_types:
        score += 4
        reasons.insert(0, "文件类型匹配")

    if score > 0 and "table_row" in str(shard.get("kind") or ""):
        score += 2
        reasons.append("表格行候选")
    return score, reasons


def block_positions(document: dict[str, Any]) -> dict[str, int]:
    return {
        str(block.get("block_id")): index
        for index, block in enumerate(document.get("raw_blocks", []))
        if isinstance(block, dict)
    }


def build_candidate_text(
    document: dict[str, Any],
    shard: dict[str, Any],
    before: int = 2,
    after: int = 4,
) -> tuple[str, list[str]]:
    raw_blocks = [block for block in document.get("raw_blocks", []) if isinstance(block, dict)]
    positions = block_positions(document)
    source_block_ids = [str(value) for value in shard.get("source_block_ids", []) if value]
    anchor_block_id = source_block_ids[0] if source_block_ids else ""
    anchor_position = positions.get(anchor_block_id)
    if anchor_position is None:
        return str(shard.get("text") or ""), source_block_ids

    selected: list[dict[str, Any]] = []
    for index in range(max(0, anchor_position - before), min(len(raw_blocks), anchor_position + after + 1)):
        selected.append(raw_blocks[index])

    selected = expand_selected_tables(raw_blocks, selected)
    max_chars = MAX_TABLE_CANDIDATE_CHARS if any(block.get("kind") == "table_row" for block in selected) else MAX_CANDIDATE_CHARS
    parts: list[str] = []
    block_ids: list[str] = []
    for block in selected:
        text = str(block.get("normalized_text") or "")
        if not text:
            continue
        if len("\n".join(parts + [text])) > max_chars and parts:
            if block.get("block_id") != anchor_block_id:
                continue
        parts.append(text)
        block_ids.append(str(block.get("block_id") or ""))

    if not parts:
        return str(shard.get("text") or ""), source_block_ids
    return "\n".join(parts), block_ids


def table_key(block: dict[str, Any]) -> tuple[str, str] | None:
    if block.get("kind") != "table_row":
        return None
    source = block.get("source", {})
    if not isinstance(source, dict):
        return None
    table_index = source.get("table_index")
    if table_index is None:
        return None
    return (str(block.get("doc_id") or ""), str(table_index))


def expand_selected_tables(
    raw_blocks: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    table_keys = {key for block in selected if (key := table_key(block)) is not None}
    if not table_keys:
        return selected
    selected_ids = {str(block.get("block_id") or "") for block in selected}
    expanded = list(selected)
    for block in raw_blocks:
        block_id = str(block.get("block_id") or "")
        if block_id in selected_ids:
            continue
        if table_key(block) in table_keys:
            expanded.append(block)
            selected_ids.add(block_id)
    expanded.sort(key=lambda block: int(block.get("order") or 0))
    return expanded


def source_span_for_blocks(document: dict[str, Any], block_ids: list[str]) -> dict[str, Any]:
    positions = block_positions(document)
    ordered_blocks = [
        (positions[block_id], block_id)
        for block_id in block_ids
        if block_id in positions
    ]
    if not ordered_blocks:
        return {}
    ordered_blocks.sort(key=lambda item: item[0])
    return {
        "doc_id": document.get("doc_id", ""),
        "start_block_id": ordered_blocks[0][1],
        "end_block_id": ordered_blocks[-1][1],
        "start_block_index": ordered_blocks[0][0],
        "end_block_index": ordered_blocks[-1][0],
        "block_count": ordered_blocks[-1][0] - ordered_blocks[0][0] + 1,
    }


def build_item_candidates(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    terms = collect_terms(item)
    scored: list[tuple[int, list[str], dict[str, Any], dict[str, Any]]] = []

    for document in source_index.get("documents", []):
        if not isinstance(document, dict) or not allowed_document_for_item(item, document):
            continue
        for shard in document.get("search_shards", []):
            if not isinstance(shard, dict):
                continue
            score, reasons = score_shard(item, shard, terms)
            if score <= 0:
                continue
            scored.append((score, reasons, document, shard))

    scored.sort(key=lambda row: (-row[0], str(row[3].get("shard_id") or "")))
    candidates: list[dict[str, Any]] = []
    seen_windows: set[tuple[str, tuple[str, ...], str]] = set()
    item_id = str(item.get("id") or "item")

    for score, reasons, document, shard in scored[:MAX_ANCHOR_SHARDS_PER_ITEM]:
        text, block_ids = build_candidate_text(document, shard)
        quote = exact_quote(str(shard.get("text") or ""), 180)
        key = (str(document.get("doc_id") or ""), tuple(block_ids), quote)
        if key in seen_windows:
            continue
        seen_windows.add(key)
        candidates.append(
            {
                "candidate_id": f"{item_id}-C{len(candidates) + 1:02d}",
                "taxonomy_id": item_id,
                "score": score,
                "reasons": reasons[:8],
                "retrieval_channels": ["rule_keyword"],
                "doc_id": document.get("doc_id", ""),
                "file_name": document.get("file_name", ""),
                "document_role": document.get("document_role", {}),
                "document_type": document.get("document_type", {}),
                "shard_ids": [shard.get("shard_id", "")],
                "source_block_ids": block_ids,
                "source_span": source_span_for_blocks(document, block_ids),
                "source_locator": shard.get("source_locator", ""),
                "source_quote": quote,
                "text": text,
                "character_count": len(text),
            }
        )
        if len(candidates) >= MAX_CANDIDATES_PER_ITEM:
            break
    return candidates


def ai_item_brief(item: dict[str, Any]) -> str:
    terms = "、".join(collect_terms(item)[:12])
    return (
        f"KTS事项：{item.get('label', '')}\n"
        f"事项ID：{item.get('id', item.get('taxonomy_id', ''))}\n"
        f"检索词：{terms}"
    )


def candidate_for_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "file_name": candidate.get("file_name", ""),
        "score": candidate.get("score", 0),
        "source_quote": candidate.get("source_quote", ""),
        "text": str(candidate.get("text") or "")[:1200],
    }


def parse_ai_selections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def review_candidates_with_ai(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ai_configured():
        return candidates, {"status": "model_unavailable", "candidate_added_count": 0}
    if not candidates:
        return candidates, {"status": "skipped_no_rule_candidates", "candidate_added_count": 0}

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易文件KTS候选证据的语义复核器。"
                "你只判断候选原文是否与目标KTS事项相关。"
                "必须输出JSON，不得编造候选ID或原文quote。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "从候选中选出与KTS事项真正相关的证据，并为每项给出相关性。",
                    "rules": [
                        "只使用给定candidate_id。",
                        "quote必须从候选text中逐字复制连续片段。",
                        "如候选只是弱相关或误命中，relevance设为low或irrelevant。",
                        "输出JSON格式：{\"selected_candidates\":[{\"candidate_id\":\"...\",\"relevance\":\"high|medium|low|irrelevant\",\"quote\":\"...\",\"reason\":\"...\"}],\"notes\":\"...\"}",
                    ],
                    "kts_item": ai_item_brief(item),
                    "candidates": [candidate_for_ai(candidate) for candidate in candidates[:MAX_AI_REVIEW_CANDIDATES]],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages)
    except AIClientError as exc:
        return candidates, {"status": "model_error", "error": str(exc), "candidate_added_count": 0}

    candidate_by_id = {str(candidate.get("candidate_id")): candidate for candidate in candidates}
    for selection in parse_ai_selections(response.get("selected_candidates")):
        candidate_id = str(selection.get("candidate_id") or "")
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue
        relevance = str(selection.get("relevance") or "low")
        quote = normalize_for_match(str(selection.get("quote") or ""))
        candidate["ai_relevance"] = relevance
        candidate["ai_reason"] = str(selection.get("reason") or "")
        if quote:
            verification = verify_quote(quote, candidate, source_index)
            candidate["ai_quote"] = quote
            candidate["ai_quote_verification"] = verification
            candidate["ai_quote_verified"] = bool(verification.get("verified"))

    rank = {"high": 0, "medium": 1, "low": 2, "irrelevant": 3}
    candidates.sort(
        key=lambda candidate: (
            rank.get(str(candidate.get("ai_relevance") or "not_reviewed"), 4),
            -int(candidate.get("score") or 0),
            str(candidate.get("candidate_id") or ""),
        )
    )
    return candidates, {
        "status": "reviewed",
        "candidate_added_count": 0,
        "selected_count": sum(1 for candidate in candidates if candidate.get("ai_relevance")),
        "notes": str(response.get("notes") or ""),
    }


def shard_for_ai(shard: dict[str, Any]) -> dict[str, Any]:
    return {
        "shard_id": shard.get("shard_id", ""),
        "file_name": shard.get("file_name", ""),
        "document_role": shard.get("document_role", {}),
        "source_locator": shard.get("source_locator", ""),
        "text": str(shard.get("text") or "")[:320],
    }


def allowed_document_for_item(item: dict[str, Any], document: dict[str, Any]) -> bool:
    allowed_types = set(item.get("document_types") or [])
    if not allowed_types:
        return True
    return document_type_code(document.get("document_type", {})) in allowed_types


def source_shards_for_ai_scan(item: dict[str, Any], source_index: dict[str, Any]) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = []
    for document in source_index.get("documents", []):
        if not isinstance(document, dict) or not allowed_document_for_item(item, document):
            continue
        for shard in document.get("search_shards", []):
            if not isinstance(shard, dict):
                continue
            text = str(shard.get("text") or "")
            if len(text) < 12:
                continue
            shards.append(shard)
    return shards[:MAX_AI_SCAN_SHARDS]


def find_shard(source_index: dict[str, Any], shard_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for document in source_index.get("documents", []):
        if not isinstance(document, dict):
            continue
        for shard in document.get("search_shards", []):
            if isinstance(shard, dict) and shard.get("shard_id") == shard_id:
                return document, shard
    return None


def candidate_from_ai_selection(
    item: dict[str, Any],
    source_index: dict[str, Any],
    selection: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    shard_id = str(selection.get("shard_id") or "")
    located = find_shard(source_index, shard_id)
    if located is None:
        return None
    document, shard = located
    text, block_ids = build_candidate_text(document, shard)
    fallback_quote = exact_quote(str(shard.get("text") or ""), 180)
    ai_quote = normalize_for_match(str(selection.get("quote") or ""))
    source_quote = ai_quote or fallback_quote
    candidate = {
        "candidate_id": f"{item.get('id', item.get('taxonomy_id', 'item'))}-AI{index:02d}",
        "taxonomy_id": item.get("id", item.get("taxonomy_id", "")),
        "score": 1,
        "reasons": ["模型语义召回", str(selection.get("reason") or "")],
        "retrieval_channels": ["model_semantic_scan"],
        "doc_id": document.get("doc_id", ""),
        "file_name": document.get("file_name", ""),
        "document_role": document.get("document_role", {}),
        "document_type": document.get("document_type", {}),
        "shard_ids": [shard_id],
        "source_block_ids": block_ids,
        "source_span": source_span_for_blocks(document, block_ids),
        "source_locator": shard.get("source_locator", ""),
        "source_quote": source_quote,
        "text": text,
        "character_count": len(text),
        "ai_relevance": str(selection.get("relevance") or "medium"),
        "ai_reason": str(selection.get("reason") or ""),
    }
    if ai_quote:
        verification = verify_quote(ai_quote, candidate, source_index)
        candidate["ai_quote"] = ai_quote
        candidate["ai_quote_verification"] = verification
        candidate["ai_quote_verified"] = bool(verification.get("verified"))
        if not verification.get("verified"):
            candidate["source_quote"] = fallback_quote
    return candidate


def scan_no_candidate_item_with_ai(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not ai_configured():
        return [], {"status": "model_unavailable", "candidate_added_count": 0}

    shards = source_shards_for_ai_scan(item, source_index)
    if not shards:
        return [], {"status": "skipped_no_source_shards", "candidate_added_count": 0}

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易文件KTS候选证据的语义召回器。"
                "你只能从给定source shards中选择可能相关的原文片段。"
                "必须输出JSON，不得编造shard_id或quote。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "规则检索没有找到候选。请从source shards中判断是否存在与KTS事项相关的片段。",
                    "rules": [
                        "只返回确实可能相关的shard_id。",
                        "quote必须从对应text中逐字复制连续片段。",
                        "如果未发现相关内容，返回空数组。",
                        "输出JSON格式：{\"candidates\":[{\"shard_id\":\"...\",\"relevance\":\"high|medium|low\",\"quote\":\"...\",\"reason\":\"...\"}],\"notes\":\"...\"}",
                    ],
                    "kts_item": ai_item_brief(item),
                    "source_shards": [shard_for_ai(shard) for shard in shards],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages)
    except AIClientError as exc:
        return [], {"status": "model_error", "error": str(exc), "candidate_added_count": 0}

    candidates: list[dict[str, Any]] = []
    seen_shards: set[str] = set()
    for selection in parse_ai_selections(response.get("candidates")):
        shard_id = str(selection.get("shard_id") or "")
        if not shard_id or shard_id in seen_shards:
            continue
        candidate = candidate_from_ai_selection(item, source_index, selection, len(candidates) + 1)
        if candidate is None:
            continue
        seen_shards.add(shard_id)
        candidates.append(candidate)
        if len(candidates) >= MAX_CANDIDATES_PER_ITEM:
            break

    return candidates, {
        "status": "semantic_scan_found" if candidates else "semantic_scan_no_candidate",
        "candidate_added_count": len(candidates),
        "notes": str(response.get("notes") or ""),
        "scanned_shard_count": len(shards),
    }


def apply_ai_semantic_recall(
    item: dict[str, Any],
    rule_candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if rule_candidates:
        return review_candidates_with_ai(item, rule_candidates, source_index)
    return scan_no_candidate_item_with_ai(item, source_index)


def reduced_worker_count(worker_count: int) -> int:
    return max(1, worker_count // 2)


def candidate_item_transient_error(item: dict[str, Any]) -> bool:
    model_review = item.get("model_review", {})
    if not isinstance(model_review, dict):
        return False
    if model_review.get("status") != "model_error":
        return False
    return is_transient_ai_error(model_review.get("error", ""))


def extraction_item_transient_error(item: dict[str, Any]) -> bool:
    review_notes = item.get("review_notes", [])
    if not isinstance(review_notes, list):
        return False
    return any(is_transient_ai_error(note) for note in review_notes)


def adaptive_retry_sleep(attempt: int) -> None:
    time.sleep(ADAPTIVE_RETRY_SLEEP_SECONDS * max(1, attempt))


def build_candidate_items_adaptive(
    taxonomy_items: list[dict[str, Any]],
    source_index: dict[str, Any],
    initial_worker_count: int,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    ordered_items: list[dict[str, Any] | None] = [None] * len(taxonomy_items)
    attempts: list[int] = [0] * len(taxonomy_items)
    pending_indices = list(range(len(taxonomy_items)))
    completed_count = 0
    worker_count = max(1, initial_worker_count)

    while pending_indices:
        batch_indices = pending_indices[:worker_count]
        pending_indices = pending_indices[worker_count:]
        batch_results: dict[int, dict[str, Any]] = {}

        if worker_count > 1 and len(batch_indices) > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(build_kts_candidate_item, taxonomy_items[index], source_index): index
                    for index in batch_indices
                }
                for future in as_completed(futures):
                    batch_results[futures[future]] = future.result()
        else:
            for index in batch_indices:
                batch_results[index] = build_kts_candidate_item(taxonomy_items[index], source_index)

        retry_indices: list[int] = []
        for index in batch_indices:
            item = batch_results[index]
            if candidate_item_transient_error(item) and attempts[index] < MAX_ADAPTIVE_MODEL_RETRIES:
                attempts[index] += 1
                retry_indices.append(index)
                continue
            ordered_items[index] = item
            completed_count += 1
            if progress_callback:
                progress_callback(
                    {
                        "stage": "model_review",
                        "stage_label": "模型语义复核",
                        "completed_items": completed_count,
                        "total_items": len(taxonomy_items),
                        "worker_count": worker_count,
                    }
                )

        if retry_indices:
            worker_count = reduced_worker_count(worker_count)
            pending_indices = retry_indices + pending_indices
            adaptive_retry_sleep(max(attempts[index] for index in retry_indices))

    return [item for item in ordered_items if item is not None]


def build_kts_candidates(
    source_index: dict[str, Any],
    taxonomy: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    taxonomy = taxonomy or load_kts_taxonomy()
    content_schema = load_kts_content_schema()
    taxonomy_items = [
        enrich_taxonomy_item(item, content_schema)
        for item in taxonomy.get("items", [])
        if isinstance(item, dict)
    ]
    if progress_callback:
        progress_callback(
            {
                "stage": "rule_match",
                "stage_label": "规则检索候选",
                "completed_items": 0,
                "total_items": len(taxonomy_items),
                "worker_count": 1,
            }
        )

    items = []
    for item in taxonomy_items:
        items.append(build_kts_candidate_item(item, source_index))
        if progress_callback:
            progress_callback(
                {
                    "stage": "rule_match",
                    "stage_label": "规则检索候选",
                    "completed_items": len(items),
                    "total_items": len(taxonomy_items),
                    "worker_count": 1,
                }
            )

    return {
        "phase": "v0.8-kts-candidates",
        "taxonomy_id": taxonomy.get("taxonomy_id", ""),
        "taxonomy_version": taxonomy.get("version", ""),
        "source_index_updated_at": source_index.get("updated_at", ""),
        "summary": {
            "taxonomy_item_count": len(items),
            "candidate_item_count": sum(1 for item in items if item["candidate_count"]),
            "no_candidate_item_count": sum(1 for item in items if not item["candidate_count"]),
            "candidate_count": sum(int(item["candidate_count"]) for item in items),
            "rule_candidate_count": sum(int(item["rule_candidate_count"]) for item in items),
        },
        "items": items,
    }


def build_kts_candidate_item(item: dict[str, Any], source_index: dict[str, Any]) -> dict[str, Any]:
    rule_candidates = build_item_candidates(item, source_index)
    return {
        "taxonomy_id": item.get("id", ""),
        "group": item.get("group", ""),
        "label": item.get("label", ""),
        "template_labels": item.get("template_labels", {}),
        "content_schema": item.get("content_schema", {}),
        "document_types": item.get("document_types", []),
        "extraction_mode": item.get("extraction_mode", "evidence"),
        "absence_checks": item.get("absence_checks", []),
        "retrieval_status": "candidate_found" if rule_candidates else "no_candidate",
        "rule_candidate_count": len(rule_candidates),
        "candidate_count": len(rule_candidates),
        "query_terms": collect_terms(item),
        "model_review": {"status": "skipped_unified"},
        "candidates": rule_candidates,
    }


def candidate_quote_for_extraction(candidate: dict[str, Any]) -> str:
    if candidate.get("ai_quote_verified"):
        return str(candidate.get("ai_quote") or "")
    return str(candidate.get("source_quote") or "")


def candidate_for_extraction_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "file_name": candidate.get("file_name", ""),
        "document_role": candidate.get("document_role", {}),
        "source_locator": candidate.get("source_locator", ""),
        "quote": candidate_quote_for_extraction(candidate),
        "context": str(candidate.get("text") or "")[:1400],
        "relevance": candidate.get("ai_relevance", ""),
    }


def content_schema_for_ai(item: dict[str, Any]) -> dict[str, Any]:
    schema = item.get("content_schema", {})
    if not isinstance(schema, dict):
        return {}
    fields = []
    for field in schema.get("fields", []):
        if not isinstance(field, dict):
            continue
        fields.append(
            {
                "key": field.get("key", ""),
                "label": field.get("label", ""),
                "required": bool(field.get("required")),
                "aliases": field.get("aliases", []),
            }
        )
    return {
        "drafting_guidance": schema.get("drafting_guidance", ""),
        "fields": fields,
        "lawyer_note_prompts": schema.get("lawyer_note_prompts", []),
    }


IMPORTANT_FACT_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"第\d+(?:\.\d+)*条"
    r"|\d+(?:[.,，]\d+)+(?:%|％|万元|亿元|元|日|天|年|个月|个工作日|倍|席)?"
    r"|\d+(?:%|％|万元|亿元|元|日|天|年|个月|个工作日|倍|席)"
    r"|百分之[一二三四五六七八九十百千万零〇两\d]+"
    r"|[一二三四五六七八九十百千万零〇两\d]+(?:日|天|年|个月|个工作日)"
    r"|QIPO|IPO|ESOP|Long-Stop|long stop"
    r")"
)
BRACKETED_NOTE_TOKEN_RE = re.compile(r"【[^】]{1,1200}】")


def item_for_style_polish(item: dict[str, Any]) -> dict[str, Any]:
    content_schema = item.get("content_schema", {})
    drafting_guidance = ""
    if isinstance(content_schema, dict):
        drafting_guidance = str(content_schema.get("drafting_guidance") or "")
    return {
        "taxonomy_id": item.get("taxonomy_id", ""),
        "group": item.get("group", ""),
        "label": item.get("label", ""),
        "drafting_guidance": drafting_guidance,
        "draft_content": str(item.get("draft_content") or "")[:MAX_STYLE_POLISH_CHARS_PER_ITEM],
    }


def important_fact_tokens(text: str) -> set[str]:
    return {match.group(0) for match in IMPORTANT_FACT_TOKEN_RE.finditer(text or "") if match.group(0)}


def validate_polished_content(original: str, polished: str) -> tuple[bool, str]:
    original = str(original or "").strip()
    polished = str(polished or "").strip()
    if not original:
        return False, "empty_original"
    if not polished:
        return False, "empty_polished"
    if len(polished) > max(120, int(len(original) * 2.2)):
        return False, "too_long"
    if len(polished) < max(12, int(len(original) * 0.35)):
        return False, "too_short"
    missing_tokens = sorted(important_fact_tokens(original) - important_fact_tokens(polished))
    if missing_tokens:
        return False, "missing_fact_tokens:" + "、".join(missing_tokens[:8])
    missing_notes = [
        note
        for note in BRACKETED_NOTE_TOKEN_RE.findall(original)
        if note not in polished
    ]
    if missing_notes:
        return False, "missing_bracketed_notes"
    return True, ""


def normalize_style_polish_items(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        raw_items = value.get("items", [])
    else:
        raw_items = value
    if not isinstance(raw_items, list):
        return {}
    normalized: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        taxonomy_id = str(item.get("taxonomy_id") or item.get("id") or "").strip()
        polished = str(item.get("polished_content") or item.get("draft_content") or "").strip()
        if taxonomy_id and polished:
            normalized[taxonomy_id] = polished
    return normalized


def polish_kts_draft_contents(
    items: list[dict[str, Any]],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    draft_items = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("draft_content") or "").strip()
    ]
    if not draft_items:
        return {"status": "skipped_no_draft_content", "item_count": 0, "changed_count": 0}
    if not ai_configured():
        return {"status": "skipped_model_unavailable", "item_count": len(draft_items), "changed_count": 0}

    if progress_callback:
        progress_callback(
            {
                "stage": "style_polish",
                "stage_label": "润色摘要",
                "completed_items": 0,
                "total_items": len(draft_items),
            }
        )

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要的最后文风编辑。"
                "你的任务是提升中文法律摘要的可读性、通顺度和模板文风一致性。"
                "你只能重组、断句、分点和轻微改写已有内容，不得新增、删除或改变任何交易事实。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "对下列KTS事项的draft_content进行最后润色。",
                    "rules": [
                        "不得新增事实、主体、金额、比例、期限、条件、例外或风险判断。",
                        "不得删除原文中的金额、比例、期限、主体、否定结论和【注：...】内容。",
                        "可以把长句拆成更自然的短句，可以用1. 2. 3.组织并列信息。",
                        "保留律师KTS摘要文风：结论先行、简洁、准确、法言法语。",
                        "保留字段标签：内容的分行结构；不要把多个字段合并为一段，也不要拆散原有的主项-子项层级。",
                        "如原内容已经足够清楚，可原样返回。",
                        "每个输入事项都必须返回一条结果。",
                        "输出JSON格式：{\"items\":[{\"taxonomy_id\":\"...\",\"polished_content\":\"...\"}]}",
                    ],
                    "items": [item_for_style_polish(item) for item in draft_items],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages, temperature=0.1, timeout_seconds=STYLE_POLISH_TIMEOUT_SECONDS)
    except AIClientError as exc:
        for item in draft_items:
            item["style_polish"] = {"status": "model_error", "error": str(exc)}
        return {
            "status": "model_error",
            "item_count": len(draft_items),
            "changed_count": 0,
            "error": str(exc),
        }

    polished_by_id = normalize_style_polish_items(response)
    changed_count = 0
    accepted_count = 0
    rejected_count = 0
    for item in draft_items:
        taxonomy_id = str(item.get("taxonomy_id") or "")
        original = str(item.get("draft_content") or "").strip()
        polished = polished_by_id.get(taxonomy_id, "")
        accepted, reason = validate_polished_content(original, polished)
        if not accepted:
            rejected_count += 1
            item["style_polish"] = {
                "status": "rejected",
                "reason": reason,
                "original_length": len(original),
                "polished_length": len(polished),
            }
            continue
        accepted_count += 1
        if polished != original:
            item["draft_content"] = polished
            changed_count += 1
        item["style_polish"] = {
            "status": "changed" if polished != original else "unchanged",
            "original_length": len(original),
            "polished_length": len(polished),
        }

    if progress_callback:
        progress_callback(
            {
                "stage": "style_polish",
                "stage_label": "润色摘要",
                "completed_items": len(draft_items),
                "total_items": len(draft_items),
            }
        )

    return {
        "status": "polished",
        "item_count": len(draft_items),
        "accepted_count": accepted_count,
        "changed_count": changed_count,
        "rejected_count": rejected_count,
    }


def normalize_evidence_quote(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def similarity_ratio(left: str, right: str) -> float:
    if len(left) < MIN_SIMILARITY_TEXT_CHARS or len(right) < MIN_SIMILARITY_TEXT_CHARS:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def duplicate_evidence_quote(normalized_quote: str, seen_quotes: list[str]) -> bool:
    if not normalized_quote:
        return True
    for seen_quote in seen_quotes:
        if normalized_quote == seen_quote:
            return True
        if len(normalized_quote) >= 30 and len(seen_quote) >= 30:
            if normalized_quote in seen_quote or seen_quote in normalized_quote:
                return True
    return False


def source_block_id_list(candidate: dict[str, Any]) -> list[str]:
    values = candidate.get("source_block_ids", [])
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value)]


def source_block_ids(candidate: dict[str, Any]) -> set[str]:
    return set(source_block_id_list(candidate))


def block_sort_key(block_id: str) -> tuple[str, int, str]:
    match = re.match(r"^(?P<doc>D\d+)-B(?P<order>\d+)$", block_id)
    if not match:
        return ("", 0, block_id)
    return (match.group("doc"), int(match.group("order")), block_id)


def sorted_source_block_ids(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)}, key=block_sort_key)


def inferred_source_span(candidate: dict[str, Any]) -> dict[str, Any]:
    block_ids = sorted_source_block_ids(source_block_id_list(candidate))
    if not block_ids:
        return {}
    parsed = [block_sort_key(block_id) for block_id in block_ids]
    doc_id = str(candidate.get("doc_id") or parsed[0][0])
    orders = [item[1] for item in parsed if item[1] > 0]
    if not orders:
        return {}
    return {
        "doc_id": doc_id,
        "start_block_id": block_ids[0],
        "end_block_id": block_ids[-1],
        "start_block_index": min(orders),
        "end_block_index": max(orders),
        "block_count": max(orders) - min(orders) + 1,
    }


def candidate_source_span(candidate: dict[str, Any]) -> dict[str, Any]:
    span = candidate.get("source_span", {})
    if isinstance(span, dict) and span.get("doc_id"):
        return span
    return inferred_source_span(candidate)


def source_spans_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not left or not right:
        return False
    if str(left.get("doc_id") or "") != str(right.get("doc_id") or ""):
        return False
    left_start = int(left.get("start_block_index") or -1)
    left_end = int(left.get("end_block_index") or -1)
    right_start = int(right.get("start_block_index") or -1)
    right_end = int(right.get("end_block_index") or -1)
    if min(left_start, left_end, right_start, right_end) < 0:
        return False
    return max(left_start, right_start) <= min(left_end, right_end)


def merge_source_spans(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return right
    if not right:
        return left
    if str(left.get("doc_id") or "") != str(right.get("doc_id") or ""):
        return left
    start_left = int(left.get("start_block_index") or 0)
    start_right = int(right.get("start_block_index") or 0)
    end_left = int(left.get("end_block_index") or 0)
    end_right = int(right.get("end_block_index") or 0)
    start = min(start_left, start_right)
    end = max(end_left, end_right)
    return {
        "doc_id": left.get("doc_id", ""),
        "start_block_id": left.get("start_block_id") if start_left <= start_right else right.get("start_block_id"),
        "end_block_id": left.get("end_block_id") if end_left >= end_right else right.get("end_block_id"),
        "start_block_index": start,
        "end_block_index": end,
        "block_count": end - start + 1,
    }


def merge_unique_values(*groups: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        values = group if isinstance(group, list) else [group]
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def merge_candidate_text(left: Any, right: Any, limit: int = MAX_CANDIDATE_CHARS) -> str:
    lines: list[str] = []
    seen_lines: set[str] = set()
    for value in [left, right]:
        for line in str(value or "").splitlines():
            text = line.strip()
            normalized = normalize_evidence_quote(text)
            if not normalized or normalized in seen_lines:
                continue
            next_text = "\n".join([*lines, text])
            if len(next_text) > limit and lines:
                continue
            seen_lines.add(normalized)
            lines.append(text)
    return "\n".join(lines)


def source_block_overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_ids = source_block_ids(left)
    right_ids = source_block_ids(right)
    if not left_ids or not right_ids:
        return 0.0
    return len(left_ids & right_ids) / min(len(left_ids), len(right_ids))


def duplicate_evidence_candidate(
    candidate: dict[str, Any],
    normalized_quote: str,
    normalized_context: str,
    selected: list[tuple[dict[str, Any], str, str]],
) -> bool:
    for selected_candidate, selected_quote, selected_context in selected:
        if duplicate_evidence_quote(normalized_quote, [selected_quote]):
            return True
        if (
            similarity_ratio(normalized_quote, selected_quote)
            >= EVIDENCE_QUOTE_SIMILARITY_DUPLICATE_RATIO
        ):
            return True
        if (
            similarity_ratio(normalized_context, selected_context)
            >= EVIDENCE_TEXT_SIMILARITY_DUPLICATE_RATIO
        ):
            return True
        if (
            source_block_overlap_ratio(candidate, selected_candidate)
            >= SOURCE_BLOCK_OVERLAP_DUPLICATE_RATIO
        ):
            return True
    return False


def merge_extraction_candidate(
    primary: dict[str, Any],
    addition: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(primary)
    merged["merged_candidate_ids"] = merge_unique_values(
        primary.get("merged_candidate_ids", [primary.get("candidate_id", "")]),
        addition.get("merged_candidate_ids", [addition.get("candidate_id", "")]),
    )
    merged["source_block_ids"] = sorted_source_block_ids(
        merge_unique_values(primary.get("source_block_ids", []), addition.get("source_block_ids", []))
    )
    merged["source_span"] = merge_source_spans(
        candidate_source_span(primary),
        candidate_source_span(addition),
    )
    merged["shard_ids"] = merge_unique_values(primary.get("shard_ids", []), addition.get("shard_ids", []))
    merged["retrieval_channels"] = merge_unique_values(
        primary.get("retrieval_channels", []),
        addition.get("retrieval_channels", []),
    )
    merged["source_locators"] = merge_unique_values(
        primary.get("source_locators", [primary.get("source_locator", "")]),
        addition.get("source_locators", [addition.get("source_locator", "")]),
    )
    merged_text = merge_candidate_text(primary.get("text", ""), addition.get("text", ""))
    merged["text"] = merged_text
    merged["character_count"] = len(merged_text)
    return merged


def find_candidate_cluster(
    candidate: dict[str, Any],
    normalized_quote: str,
    normalized_context: str,
    clusters: list[tuple[dict[str, Any], str, str]],
) -> int | None:
    span = candidate_source_span(candidate)
    for index, (cluster_candidate, _cluster_quote, _cluster_context) in enumerate(clusters):
        if source_spans_overlap(span, candidate_source_span(cluster_candidate)):
            return index
    for index, (cluster_candidate, cluster_quote, cluster_context) in enumerate(clusters):
        if duplicate_evidence_candidate(
            candidate,
            normalized_quote,
            normalized_context,
            [(cluster_candidate, cluster_quote, cluster_context)],
        ):
            return index
    return None


def should_merge_candidate_clusters(
    left: tuple[dict[str, Any], str, str],
    right: tuple[dict[str, Any], str, str],
) -> bool:
    left_candidate, left_quote, left_context = left
    right_candidate, right_quote, right_context = right
    if source_spans_overlap(candidate_source_span(left_candidate), candidate_source_span(right_candidate)):
        return True
    return duplicate_evidence_candidate(
        right_candidate,
        right_quote,
        right_context,
        [(left_candidate, left_quote, left_context)],
    )


def merge_candidate_clusters(
    clusters: list[tuple[dict[str, Any], str, str]],
) -> list[tuple[dict[str, Any], str, str]]:
    merged_clusters = list(clusters)
    changed = True
    while changed:
        changed = False
        for left_index in range(len(merged_clusters)):
            if changed:
                break
            for right_index in range(left_index + 1, len(merged_clusters)):
                left = merged_clusters[left_index]
                right = merged_clusters[right_index]
                if not should_merge_candidate_clusters(left, right):
                    continue
                merged_candidate = merge_extraction_candidate(left[0], right[0])
                merged_clusters[left_index] = (
                    merged_candidate,
                    left[1],
                    normalize_evidence_quote(merged_candidate.get("text")),
                )
                del merged_clusters[right_index]
                changed = True
                break
    return merged_clusters


def select_extraction_candidates(
    candidates: list[dict[str, Any]],
    limit: int = MAX_EXTRACTION_EVIDENCE,
) -> list[dict[str, Any]]:
    clusters: list[tuple[dict[str, Any], str, str]] = []
    for candidate in candidates:
        quote = candidate_quote_for_extraction(candidate)
        normalized_quote = normalize_evidence_quote(quote)
        normalized_context = normalize_evidence_quote(candidate.get("text"))
        cluster_index = find_candidate_cluster(
            candidate,
            normalized_quote,
            normalized_context,
            clusters,
        )
        if cluster_index is not None:
            cluster_candidate, cluster_quote, cluster_context = clusters[cluster_index]
            merged_candidate = merge_extraction_candidate(cluster_candidate, candidate)
            clusters[cluster_index] = (
                merged_candidate,
                cluster_quote,
                normalize_evidence_quote(merged_candidate.get("text")),
            )
            clusters = merge_candidate_clusters(clusters)
            continue
        if len(clusters) >= limit:
            break
        clusters.append((dict(candidate), normalized_quote, normalized_context))
        clusters = merge_candidate_clusters(clusters)
    return [candidate for candidate, _quote, _context in clusters]


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_field_values(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    allowed_statuses = {"found", "not_found", "unclear", "not_applicable"}
    for raw_field in value:
        if not isinstance(raw_field, dict):
            continue
        key = str(raw_field.get("key") or "").strip()
        label = str(raw_field.get("label") or "").strip()
        if not key and not label:
            continue
        status = str(raw_field.get("status") or "unclear").strip()
        if status not in allowed_statuses:
            status = "unclear"
        source_candidate_ids = raw_field.get("source_candidate_ids", [])
        if not isinstance(source_candidate_ids, list):
            source_candidate_ids = []
        normalized.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "value": str(raw_field.get("value") or "").strip(),
                "note": str(raw_field.get("note") or "").strip(),
                "source_candidate_ids": [
                    str(candidate_id)
                    for candidate_id in source_candidate_ids
                    if str(candidate_id).strip()
                ],
            }
        )
    return normalized


def normalize_extracted_facts(value: Any) -> dict[str, Any]:
    facts = value if isinstance(value, dict) else {}
    return {
        "summary_points": normalize_string_list(facts.get("summary_points")),
        "key_terms": normalize_string_list(facts.get("key_terms")),
        "unclear_points": normalize_string_list(facts.get("unclear_points")),
        "field_values": normalize_field_values(facts.get("field_values")),
        "clause_refs": clean_clause_refs(normalize_string_list(facts.get("clause_refs"))),
        "lawyer_notes": normalize_string_list(facts.get("lawyer_notes")),
        "missing_or_unclear": normalize_string_list(facts.get("missing_or_unclear")),
    }


def schema_fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    content_schema = item.get("content_schema", {})
    if not isinstance(content_schema, dict):
        return []
    fields = content_schema.get("fields", [])
    return [field for field in fields if isinstance(field, dict)]


def field_value_map(extracted_facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = extracted_facts.get("field_values", [])
    if not isinstance(values, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        key = str(value.get("key") or "").strip()
        if key:
            mapped[key] = value
        label = str(value.get("label") or "").strip()
        if label:
            mapped[f"label:{label}"] = value
    return mapped


def build_schema_coverage(item: dict[str, Any], extracted_facts: dict[str, Any]) -> dict[str, Any]:
    fields = schema_fields(item)
    values_by_key = field_value_map(extracted_facts)
    coverage_fields: list[dict[str, Any]] = []
    required_total = 0
    required_found = 0
    required_unclear = 0
    required_missing = 0

    for field in fields:
        key = str(field.get("key") or "").strip()
        label = str(field.get("label") or key).strip()
        required = bool(field.get("required"))
        value = values_by_key.get(key) or values_by_key.get(f"label:{label}") or {}
        status = str(value.get("status") or "unclear").strip()
        if status not in {"found", "not_found", "unclear", "not_applicable"}:
            status = "unclear"
        if required:
            required_total += 1
            if status == "found":
                required_found += 1
            elif status == "not_found":
                required_missing += 1
            elif status != "not_applicable":
                required_unclear += 1
        coverage_fields.append(
            {
                "key": key,
                "label": label,
                "required": required,
                "status": status,
                "value": str(value.get("value") or "").strip(),
                "note": str(value.get("note") or "").strip(),
            }
        )

    if required_total == 0:
        status = "not_configured"
    elif required_found == required_total:
        status = "complete"
    elif required_found > 0:
        status = "partial"
    else:
        status = "weak"
    return {
        "status": status,
        "required_total": required_total,
        "required_found": required_found,
        "required_missing": required_missing,
        "required_unclear": required_unclear,
        "fields": coverage_fields,
    }


def schema_coverage_review_notes(coverage: dict[str, Any]) -> list[str]:
    fields = coverage.get("fields", [])
    if not isinstance(fields, list):
        return []
    missing_labels = [
        str(field.get("label") or "")
        for field in fields
        if isinstance(field, dict) and field.get("required") and field.get("status") == "not_found"
    ]
    unclear_labels = [
        str(field.get("label") or "")
        for field in fields
        if isinstance(field, dict) and field.get("required") and field.get("status") == "unclear"
    ]
    notes: list[str] = []
    if missing_labels:
        notes.append("以下关键字段未见明确约定或未被模型提取：" + "、".join(missing_labels) + "。")
    if unclear_labels:
        notes.append("以下关键字段需要律师确认：" + "、".join(unclear_labels) + "。")
    return notes


def normalize_absence_checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    checks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        checks.append(
            {
                "label": label,
                "expected_meaning": str(item.get("expected_meaning") or "").strip(),
                "keywords": normalize_string_list(item.get("keywords")),
                "required_cooccurrence": normalize_required_cooccurrence(
                    item.get("required_cooccurrence")
                ),
            }
        )
    return checks


def normalize_required_cooccurrence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    window_chars = value.get("window_chars", ABSENCE_SNIPPET_RADIUS * 2)
    try:
        normalized_window = max(40, min(1200, int(window_chars)))
    except (TypeError, ValueError):
        normalized_window = ABSENCE_SNIPPET_RADIUS * 2
    return {
        "right_terms": normalize_string_list(value.get("right_terms")),
        "trigger_terms": normalize_string_list(value.get("trigger_terms")),
        "window_chars": normalized_window,
    }


def allowed_documents_for_item(item: dict[str, Any], source_index: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        document
        for document in source_index.get("documents", [])
        if isinstance(document, dict) and allowed_document_for_item(item, document)
    ]


def snippet_around(text: str, position: int, radius: int = ABSENCE_SNIPPET_RADIUS) -> str:
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def count_required_cooccurrences(
    text: str,
    cooccurrence: dict[str, Any],
) -> int:
    right_terms = [normalize_for_match(term) for term in cooccurrence.get("right_terms", [])]
    trigger_terms = [normalize_for_match(term) for term in cooccurrence.get("trigger_terms", [])]
    right_terms = [term for term in right_terms if term]
    trigger_terms = [term for term in trigger_terms if term]
    if not right_terms or not trigger_terms:
        return 0
    window_chars = int(cooccurrence.get("window_chars") or ABSENCE_SNIPPET_RADIUS * 2)
    hit_count = 0
    for right_term in right_terms:
        start = 0
        while True:
            position = text.find(right_term, start)
            if position < 0:
                break
            window = text[max(0, position - window_chars) : position + len(right_term) + window_chars]
            if any(trigger in window for trigger in trigger_terms):
                hit_count += 1
            start = position + max(1, len(right_term))
    return hit_count


def build_absence_check_results(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    documents = allowed_documents_for_item(item, source_index)
    for check in normalize_absence_checks(item.get("absence_checks")):
        snippets: list[dict[str, str]] = []
        hit_count = 0
        cooccurrence_hit_count = 0
        cooccurrence = check.get("required_cooccurrence", {})
        for document in documents:
            canonical = document.get("canonical_stream", {})
            text = str(canonical.get("text") or "") if isinstance(canonical, dict) else ""
            search_text = normalize_for_match(text)
            if not search_text:
                continue
            if cooccurrence:
                cooccurrence_hit_count += count_required_cooccurrences(search_text, cooccurrence)
            for keyword in check["keywords"]:
                term = normalize_for_match(keyword)
                if not term:
                    continue
                start = 0
                while True:
                    position = search_text.find(term, start)
                    if position < 0:
                        break
                    hit_count += 1
                    if len(snippets) < MAX_ABSENCE_SNIPPETS_PER_CHECK:
                        snippets.append(
                            {
                                "file_name": str(document.get("file_name") or ""),
                                "keyword": keyword,
                                "snippet": snippet_around(search_text, position),
                            }
                        )
                    start = position + max(1, len(term))
        results.append(
            {
                "label": check["label"],
                "expected_meaning": check["expected_meaning"],
                "keywords": check["keywords"],
                "keyword_hit_count": hit_count,
                "required_cooccurrence": cooccurrence,
                "cooccurrence_hit_count": cooccurrence_hit_count,
                "snippets": snippets,
            }
        )
    return results


def rule_absence_labels(checks: list[dict[str, Any]], draft_content: str) -> list[str]:
    return [
        str(check.get("label") or "")
        for check in checks
        if (
            int(check.get("keyword_hit_count") or 0) == 0
            or (
                isinstance(check.get("required_cooccurrence"), dict)
                and bool(check.get("required_cooccurrence"))
                and int(check.get("cooccurrence_hit_count") or 0) == 0
            )
        )
        and str(check.get("label") or "")
        and str(check.get("label") or "") not in draft_content
    ]


def agreement_label_for_item(item: dict[str, Any]) -> str:
    group = str(item.get("group") or "").upper()
    if group == "SPA":
        return "增资协议"
    if group == "SHA":
        return "股东协议"
    return "交易文件"


def ensure_rule_absences(draft_content: str, missing_labels: list[str], agreement_label: str) -> str:
    if not missing_labels:
        return draft_content
    addition = "、".join(missing_labels)
    if not draft_content:
        return f"{agreement_label}无{addition}的明确约定。"

    text = draft_content.rstrip()
    ending = "。" if text.endswith("。") else ""
    base = text[:-1] if ending else text
    marker = "的明确约定"
    if marker in base:
        return base.replace(marker, f"、{addition}{marker}", 1) + "。"
    if base.startswith(f"{agreement_label}无"):
        return f"{base}、{addition}的明确约定。"
    return f"{draft_content} {agreement_label}未见{addition}的明确约定。"


def extract_absence_checks_with_ai(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    checks = build_absence_check_results(item, source_index)
    agreement_label = agreement_label_for_item(item)
    if not checks:
        return {
            "status": "unclear",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["当前事项未配置需要检查的常见缺失条款，需律师复核。"],
        }
    if not ai_configured():
        return {
            "status": "needs_review",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["已完成常见缺失条款的关键词命中检查，但模型抽取未完成，需律师复核。"],
        }

    extraction_candidates = select_extraction_candidates(candidates)
    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要起草助手。"
                f"你的任务是核对{agreement_label}中若干常见条款是否有明确约定。"
                "你必须区分普通权利或义务条款和仅作为其他特殊条款一部分出现的文字。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "为KTS事项“其他”判断若干常见条款是否未约定，并起草表格“内容”列。",
                    "rules": [
                        "只依据search_results和related_evidence，不得使用外部知识或猜测。",
                        "必须逐项判断search_results中的每一个label，并在draft_content中列明所有判断为未见明确约定的事项，不得遗漏。",
                        "keyword_hit_count为0时，可判断对应事项未见明显约定。",
                        "如命中片段只是定义、上下文引用或其他事项的一部分，而不是明确权利义务，也应判断为未见明确约定。",
                        "常规回购权必须区别于道德合规、利益输送、股权代持、资金往来等触发的特殊回购权；仅有特殊回购权时，常规回购权仍判断为未约定。",
                        "创始人全职付出与不竞争义务应分别判断；不得因不竞争义务存在就推定创始人全职付出已约定，反之亦然。",
                        f"draft_content应接近参考模板文风，优先写成“{agreement_label}无……”的简洁句式。",
                        "如不能确认某事项是否未约定，status设为needs_review，并在unclear_points和review_notes说明。",
                        "必须逐项返回content_schema.fields中的全部字段；field_values中的key必须使用给定字段key。",
                        "字段status只能为found、not_found、unclear、not_applicable。对于已判断未见明确约定的事项，status使用not_found，value简要写“未见明确约定”。",
                        "clause_refs只写条款编号或条款标题，不得包含文件名、candidate_id、source_locator或原文摘录。",
                        "输出JSON格式：{\"status\":\"drafted|needs_review|unclear\",\"extracted_facts\":{\"summary_points\":[\"...\"],\"key_terms\":[\"...\"],\"field_values\":[{\"key\":\"...\",\"label\":\"...\",\"status\":\"found|not_found|unclear|not_applicable\",\"value\":\"...\",\"source_candidate_ids\":[\"...\"],\"note\":\"...\"}],\"clause_refs\":[\"...\"],\"lawyer_notes\":[\"...\"],\"missing_or_unclear\":[\"...\"],\"unclear_points\":[\"...\"]},\"draft_content\":\"...\",\"review_notes\":[\"...\"]}",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "group": item.get("group", ""),
                    },
                    "content_schema": content_schema_for_ai(item),
                    "search_results": checks,
                    "related_evidence": [
                        candidate_for_extraction_ai(candidate)
                        for candidate in extraction_candidates
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages)
    except AIClientError as exc:
        return {
            "status": "needs_review",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": [f"模型抽取失败：{exc}"],
        }

    status = str(response.get("status") or "needs_review")
    if status not in {"drafted", "needs_review", "unclear"}:
        status = "needs_review"
    draft_content = str(response.get("draft_content") or "").strip()
    missing_rule_labels = rule_absence_labels(checks, draft_content)
    draft_content = ensure_rule_absences(draft_content, missing_rule_labels, agreement_label)
    if status == "drafted" and not draft_content:
        status = "needs_review"
    review_notes = normalize_string_list(response.get("review_notes"))
    if missing_rule_labels:
        review_notes.append(
            "系统根据全篇关键词或常规触发近邻规则补充未见明确约定事项："
            + "、".join(missing_rule_labels)
            + "。"
        )
    extracted_facts = normalize_extracted_facts(response.get("extracted_facts"))
    return {
        "status": status,
        "extracted_facts": extracted_facts,
        "draft_content": draft_content,
        "review_notes": review_notes,
    }


def source_blocks_by_id(source_index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    for document in source_index.get("documents", []):
        if not isinstance(document, dict):
            continue
        for block in document.get("raw_blocks", []):
            if not isinstance(block, dict):
                continue
            block_id = str(block.get("block_id") or "")
            if block_id:
                blocks[block_id] = block
    return blocks


def evidence_tables_for_candidate(
    candidate: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = source_blocks_by_id(source_index)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for block_id in source_block_id_list(candidate):
        block = blocks.get(block_id)
        if not block or block.get("kind") != "table_row":
            continue
        source = block.get("source", {})
        if not isinstance(source, dict):
            continue
        table_index = str(source.get("table_index") or "")
        key = (str(block.get("doc_id") or ""), table_index)
        table = grouped.setdefault(
            key,
            {
                "file_name": block.get("file_name", ""),
                "table_index": table_index,
                "rows": [],
            },
        )
        table["rows"].append(
            {
                "row_index": source.get("row_index"),
                "cells": [str(cell) for cell in source.get("cells", [])],
            }
        )

    tables: list[dict[str, Any]] = []
    for table in grouped.values():
        rows = table.get("rows", [])
        if isinstance(rows, list):
            rows.sort(key=lambda row: int(row.get("row_index") or 0) if isinstance(row, dict) else 0)
        if rows:
            tables.append(table)
    tables.sort(key=lambda table: int(table.get("table_index") or 0))
    return tables


MAX_SCAN_EXTRACT_SHARDS = 80


def scan_and_extract_with_ai(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    """For items with no rule candidates: send shards and ask AI to find + extract."""
    if not ai_configured():
        return {
            "status": "unclear",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["未找到候选原文且模型不可用，需律师复核。"],
        }

    shards = source_shards_for_ai_scan(item, source_index)[:MAX_SCAN_EXTRACT_SHARDS]
    if not shards:
        return {
            "status": "unclear",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["未找到可检索的文档分片。"],
        }

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要起草助手。"
                "关键词检索未命中该事项的候选证据，现在给你文档原文分片，请直接判断是否有相关内容并提取。"
                "如果文档中确实没有该事项的约定，status设为unclear并在review_notes说明。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "从文档分片中查找与目标KTS事项相关的内容，评估并抽取事实，起草摘要。",
                    "rules": [
                        "浏览所有source_shards，找出与目标KTS事项相关的片段。",
                        "如果找到相关内容，按content_schema字段抽取事实并起草draft_content。",
                        "如果确实未见相关约定，status设为unclear，draft_content留空，review_notes写明未见约定。",
                        "不得编造未在source_shards中出现的交易条件。",
                        "文风应接近律师交易文件主要条款摘要：简洁、准确、法言法语。",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "template_labels": item.get("template_labels", {}),
                        "group": item.get("group", ""),
                    },
                    "content_schema": content_schema_for_ai(item),
                    "source_shards": [shard_for_ai(shard) for shard in shards],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages)
    except AIClientError as exc:
        return {
            "status": "unclear",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": [f"模型扫描抽取失败：{exc}"],
        }

    status = str(response.get("status") or "unclear")
    if status not in {"drafted", "needs_review", "unclear"}:
        status = "unclear"
    draft_content = str(response.get("draft_content") or "").strip()
    extracted_facts = normalize_extracted_facts(response.get("extracted_facts"))
    evidence_assessment = response.get("evidence_assessment", [])
    if not isinstance(evidence_assessment, list):
        evidence_assessment = []
    return {
        "status": status,
        "extracted_facts": extracted_facts,
        "draft_content": draft_content,
        "evidence_assessment": evidence_assessment,
        "review_notes": normalize_string_list(response.get("review_notes")),
    }


def extract_facts_with_ai(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    if item.get("extraction_mode") == "absence_check":
        return extract_absence_checks_with_ai(item, candidates, source_index)
    if not candidates:
        return scan_and_extract_with_ai(item, source_index)
    if not ai_configured():
        return {
            "status": "needs_review",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["已找到候选原文，但模型抽取未完成，需律师复核。"],
        }

    extraction_candidates = select_extraction_candidates(candidates)
    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要起草助手。"
                "你的任务是：1) 评估候选证据与目标事项的相关性；2) 从相关证据中按字段抽取事实；3) 起草KTS摘要。"
                "你只能依据给定候选证据抽取事实和起草摘要，不得编造未给出的交易条件。"
                "文风应接近律师交易文件主要条款摘要：简洁、准确、法言法语。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "评估证据相关性，抽取事实，起草KTS事项摘要。",
                    "rules": [
                        "先对每条evidence评估relevance（high/medium/low/irrelevant），并为相关证据提取key_excerpt（50-150字的最短必要原文片段，保留完整句子），再基于high和medium证据抽取事实。",
                        "只依据给定evidence，不得使用外部知识或猜测。",
                        "必须逐项返回content_schema.fields中的全部字段；field_values中的key必须使用给定字段key。",
                        "字段status只能为found、not_found、unclear、not_applicable；证据明确时用found，证据没有体现时用not_found，证据不足或冲突时用unclear。",
                        "字段value应写该字段的提炼结果，不要粘贴整段原文；source_candidate_ids填写支持该字段的candidate_id。",
                        "必须遵守content_schema.drafting_guidance的事项边界；即使evidence窗口中出现其他KTS事项的事实，也不得写入当前事项的draft_content或字段value。",
                        "费用、税费、违约追责费用、违约赔偿、解除救济等内容，只有在当前KTS事项明确要求时才可纳入；否则应留给对应事项。",
                        "draft_content应按字段分层输出，格式为：每个主要字段占一行，用“字段标签：内容”格式（如“回购触发事项：xxx”）；如某字段下有多个子项，子项另起一行，不带标签前缀。这样导出时主项会自动编为1. 2. 3.，子项编为(1) (2) (3)。避免机械罗列通知程序和低价值原文。",
                        "如存在未约定、表述不清、偏离惯常或需客户确认之处，同时写入missing_or_unclear、lawyer_notes和review_notes；重要提示可在draft_content末尾用【注：...】呈现。",
                        "能从原文识别条款编号或标题时，写入clause_refs；不能识别则返回空数组，不得猜测条款号。",
                        "clause_refs只写条款编号或条款标题，不得包含文件名、candidate_id、source_locator或原文摘录。",
                        "如证据不足以形成摘要，status设为unclear，draft_content留空。",
                        "输出JSON格式：{\"status\":\"drafted|needs_review|unclear\",\"extracted_facts\":{\"summary_points\":[\"...\"],\"key_terms\":[\"...\"],\"field_values\":[{\"key\":\"...\",\"label\":\"...\",\"status\":\"found|not_found|unclear|not_applicable\",\"value\":\"...\",\"source_candidate_ids\":[\"...\"],\"note\":\"...\"}],\"clause_refs\":[\"...\"],\"lawyer_notes\":[\"...\"],\"missing_or_unclear\":[\"...\"],\"unclear_points\":[\"...\"]},\"draft_content\":\"...\",\"review_notes\":[\"...\"]}",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "template_labels": item.get("template_labels", {}),
                        "group": item.get("group", ""),
                    },
                    "content_schema": content_schema_for_ai(item),
                    "evidence": [
                        candidate_for_extraction_ai(candidate)
                        for candidate in extraction_candidates
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]

    try:
        response = chat_json(messages)
    except AIClientError as exc:
        return {
            "status": "needs_review",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": [f"模型抽取失败：{exc}"],
        }

    status = str(response.get("status") or "needs_review")
    if status not in {"drafted", "needs_review", "unclear"}:
        status = "needs_review"
    draft_content = str(response.get("draft_content") or "").strip()
    if status == "drafted" and not draft_content:
        status = "needs_review"
    extracted_facts = normalize_extracted_facts(response.get("extracted_facts"))
    return {
        "status": status,
        "extracted_facts": extracted_facts,
        "draft_content": draft_content,
        "review_notes": normalize_string_list(response.get("review_notes")),
    }


def build_kts_extraction_item(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    candidates = [candidate for candidate in item.get("candidates", []) if isinstance(candidate, dict)]
    extraction_candidates = select_extraction_candidates(candidates)
    evidence: list[dict[str, Any]] = []
    verified_count = 0
    for candidate in extraction_candidates:
        quote = candidate_quote_for_extraction(candidate)
        verification = verify_quote(quote, candidate, source_index)
        if verification.get("verified"):
            verified_count += 1
        evidence.append(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "file_name": candidate.get("file_name", ""),
                "document_role": candidate.get("document_role", {}),
                "source_locator": candidate.get("source_locator", ""),
                "quote": quote,
                "context": str(candidate.get("text") or "")[:1400],
                "source_block_ids": candidate.get("source_block_ids", []),
                "source_span": candidate.get("source_span", {}),
                "shard_ids": candidate.get("shard_ids", []),
                "merged_candidate_ids": candidate.get("merged_candidate_ids", []),
                "tables": evidence_tables_for_candidate(candidate, source_index),
                "score": candidate.get("score", 0),
                "retrieval_channels": candidate.get("retrieval_channels", []),
                "ai_relevance": candidate.get("ai_relevance", ""),
                **verification,
            }
        )

    extraction = extract_facts_with_ai(item, candidates, source_index)
    # Merge key_excerpt from evidence_assessment into evidence items
    assessment_by_id = {}
    for assess in extraction.get("evidence_assessment", []):
        if isinstance(assess, dict) and assess.get("candidate_id"):
            assessment_by_id[assess["candidate_id"]] = assess
    for ev in evidence:
        cid = ev.get("candidate_id", "")
        assess = assessment_by_id.get(cid, {})
        if assess.get("key_excerpt"):
            ev["key_excerpt"] = assess["key_excerpt"]
        if assess.get("relevance"):
            ev["ai_relevance"] = assess["relevance"]
    extracted_facts = extraction["extracted_facts"]
    schema_coverage = build_schema_coverage(item, extracted_facts)
    review_notes = [
        *extraction["review_notes"],
        *schema_coverage_review_notes(schema_coverage),
    ]
    return (
        {
            "taxonomy_id": item.get("taxonomy_id", ""),
            "group": item.get("group", ""),
            "label": item.get("label", ""),
            "template_labels": item.get("template_labels", {}),
            "content_schema": item.get("content_schema", {}),
            "status": extraction["status"],
            "candidate_count": len(candidates),
            "model_review": item.get("model_review", {}),
            "source_evidence": evidence,
            "extracted_facts": extracted_facts,
            "schema_coverage": schema_coverage,
            "clause_refs": extracted_facts.get("clause_refs", []),
            "lawyer_notes": extracted_facts.get("lawyer_notes", []),
            "missing_or_unclear": extracted_facts.get("missing_or_unclear", []),
            "draft_content": extraction["draft_content"],
            "review_notes": review_notes,
        },
        verified_count,
    )


def build_extraction_items_adaptive(
    source_items: list[dict[str, Any]],
    source_index: dict[str, Any],
    initial_worker_count: int,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    ordered_items: list[dict[str, Any] | None] = [None] * len(source_items)
    verified_counts: list[int] = [0] * len(source_items)
    attempts: list[int] = [0] * len(source_items)
    pending_indices = list(range(len(source_items)))
    completed_count = 0
    worker_count = max(1, initial_worker_count)

    while pending_indices:
        batch_indices = pending_indices[:worker_count]
        pending_indices = pending_indices[worker_count:]
        batch_results: dict[int, tuple[dict[str, Any], int]] = {}

        if worker_count > 1 and len(batch_indices) > 1:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(build_kts_extraction_item, source_items[index], source_index): index
                    for index in batch_indices
                }
                for future in as_completed(futures):
                    batch_results[futures[future]] = future.result()
        else:
            for index in batch_indices:
                batch_results[index] = build_kts_extraction_item(source_items[index], source_index)

        retry_indices: list[int] = []
        for index in batch_indices:
            item, verified_count = batch_results[index]
            if extraction_item_transient_error(item) and attempts[index] < MAX_ADAPTIVE_MODEL_RETRIES:
                attempts[index] += 1
                retry_indices.append(index)
                continue
            ordered_items[index] = item
            verified_counts[index] = verified_count
            completed_count += 1
            if progress_callback:
                progress_callback(
                    {
                        "stage": "extraction",
                        "stage_label": "生成摘要",
                        "completed_items": completed_count,
                        "total_items": len(source_items),
                        "worker_count": worker_count,
                    }
                )

        if retry_indices:
            worker_count = reduced_worker_count(worker_count)
            pending_indices = retry_indices + pending_indices
            adaptive_retry_sleep(max(attempts[index] for index in retry_indices))

    return [item for item in ordered_items if item is not None], verified_counts


def build_kts_extraction(
    candidates_record: dict[str, Any],
    source_index: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    source_items = [item for item in candidates_record.get("items", []) if isinstance(item, dict)]
    worker_count = min(ai_max_workers(), len(source_items)) if ai_configured() and source_items else 1
    if progress_callback:
        progress_callback(
            {
                "stage": "extraction",
                "stage_label": "生成摘要",
                "completed_items": 0,
                "total_items": len(source_items),
                "worker_count": worker_count,
            }
        )

    if ai_configured():
        items, verified_counts = build_extraction_items_adaptive(
            source_items,
            source_index,
            worker_count,
            progress_callback,
        )
    else:
        verified_counts = [0] * len(source_items)
        items = []
        for index, item in enumerate(source_items):
            extraction_item, verified_count = build_kts_extraction_item(item, source_index)
            items.append(extraction_item)
            verified_counts[index] = verified_count
            if progress_callback:
                progress_callback(
                    {
                        "stage": "extraction",
                        "stage_label": "生成摘要",
                        "completed_items": len(items),
                        "total_items": len(source_items),
                        "worker_count": worker_count,
                    }
                )

    style_polish_summary = polish_kts_draft_contents(items, progress_callback)

    return {
        "phase": "v0.7-kts-extraction",
        "taxonomy_id": candidates_record.get("taxonomy_id", ""),
        "taxonomy_version": candidates_record.get("taxonomy_version", ""),
        "source_candidates_updated_at": candidates_record.get("updated_at", ""),
        "summary": {
            "taxonomy_item_count": len(items),
            "drafted_count": sum(1 for item in items if item["status"] == "drafted"),
            "draft_content_count": sum(1 for item in items if item.get("draft_content")),
            "needs_review_count": sum(1 for item in items if item["status"] == "needs_review"),
            "unclear_count": sum(1 for item in items if item["status"] == "unclear"),
            "candidate_count": sum(int(item["candidate_count"]) for item in items),
            "verified_evidence_count": sum(verified_counts),
            "schema_coverage": {
                "complete_item_count": sum(
                    1 for item in items if item.get("schema_coverage", {}).get("status") == "complete"
                ),
                "partial_item_count": sum(
                    1 for item in items if item.get("schema_coverage", {}).get("status") == "partial"
                ),
                "weak_item_count": sum(
                    1 for item in items if item.get("schema_coverage", {}).get("status") == "weak"
                ),
                "required_field_count": sum(
                    int(item.get("schema_coverage", {}).get("required_total", 0))
                    for item in items
                ),
                "required_field_found_count": sum(
                    int(item.get("schema_coverage", {}).get("required_found", 0))
                    for item in items
                ),
            },
            "model_extraction": {
                "worker_count": worker_count,
            },
            "style_polish": style_polish_summary,
        },
        "items": items,
    }
