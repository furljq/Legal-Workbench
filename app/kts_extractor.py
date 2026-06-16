"""KTS candidate retrieval and evidence-first extraction draft."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from pathlib import Path
from typing import Any

from ai_client import AIClientError, ai_configured, ai_max_workers, chat_json
from config import CAPABILITIES_DIR
from evidence_verifier import verify_quote
from source_index import normalize_for_match


MAX_CANDIDATES_PER_ITEM = 8
MAX_ANCHOR_SHARDS_PER_ITEM = 18
MAX_CANDIDATE_CHARS = 2000
MAX_AI_REVIEW_CANDIDATES = 8
MAX_AI_SCAN_SHARDS = 120
MAX_EXTRACTION_EVIDENCE = 3
MAX_ABSENCE_SNIPPETS_PER_CHECK = 8
ABSENCE_SNIPPET_RADIUS = 180
TAXONOMY_PATH = CAPABILITIES_DIR / "spa_sha_kts" / "kts_taxonomy.json"
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
    if allowed_types and shard_doc_type and shard_doc_type not in allowed_types:
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

    parts: list[str] = []
    block_ids: list[str] = []
    for block in selected:
        text = str(block.get("normalized_text") or "")
        if not text:
            continue
        if len("\n".join(parts + [text])) > MAX_CANDIDATE_CHARS and parts:
            if block.get("block_id") != anchor_block_id:
                continue
        parts.append(text)
        block_ids.append(str(block.get("block_id") or ""))

    if not parts:
        return str(shard.get("text") or ""), source_block_ids
    return "\n".join(parts), block_ids


def build_item_candidates(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    terms = collect_terms(item)
    scored: list[tuple[int, list[str], dict[str, Any], dict[str, Any]]] = []

    for document in source_index.get("documents", []):
        if not isinstance(document, dict):
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
                "document_type": document.get("document_type", {}),
                "shard_ids": [shard.get("shard_id", "")],
                "source_block_ids": block_ids,
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
        "document_type": document.get("document_type", {}),
        "shard_ids": [shard_id],
        "source_block_ids": block_ids,
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


def build_kts_candidates(
    source_index: dict[str, Any],
    taxonomy: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    taxonomy = taxonomy or load_kts_taxonomy()
    taxonomy_items = [item for item in taxonomy.get("items", []) if isinstance(item, dict)]
    worker_count = min(ai_max_workers(), len(taxonomy_items)) if ai_configured() and taxonomy_items else 1
    if progress_callback:
        progress_callback(
            {
                "stage": "model_review",
                "stage_label": "模型语义复核",
                "completed_items": 0,
                "total_items": len(taxonomy_items),
                "worker_count": worker_count,
            }
        )

    if ai_configured() and worker_count > 1:
        ordered_items: list[dict[str, Any] | None] = [None] * len(taxonomy_items)
        completed_count = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(build_kts_candidate_item, item, source_index): index
                for index, item in enumerate(taxonomy_items)
            }
            for future in as_completed(futures):
                index = futures[future]
                ordered_items[index] = future.result()
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
        items = [item for item in ordered_items if item is not None]
    else:
        items = []
        for item in taxonomy_items:
            items.append(build_kts_candidate_item(item, source_index))
            if progress_callback:
                progress_callback(
                    {
                        "stage": "model_review",
                        "stage_label": "模型语义复核",
                        "completed_items": len(items),
                        "total_items": len(taxonomy_items),
                        "worker_count": worker_count,
                    }
                )

    model_statuses = [
        str(item.get("model_review", {}).get("status", ""))
        for item in items
        if isinstance(item.get("model_review"), dict)
    ]
    return {
        "phase": "v0.4-kts-candidates",
        "taxonomy_id": taxonomy.get("taxonomy_id", ""),
        "taxonomy_version": taxonomy.get("version", ""),
        "source_index_updated_at": source_index.get("updated_at", ""),
        "summary": {
            "taxonomy_item_count": len(items),
            "candidate_item_count": sum(1 for item in items if item["candidate_count"]),
            "no_candidate_item_count": sum(1 for item in items if not item["candidate_count"]),
            "candidate_count": sum(int(item["candidate_count"]) for item in items),
            "rule_candidate_count": sum(int(item["rule_candidate_count"]) for item in items),
            "model_review": {
                "worker_count": worker_count,
                "reviewed_item_count": model_statuses.count("reviewed"),
                "scanned_item_count": sum(
                    1 for status in model_statuses if status.startswith("semantic_scan")
                ),
                "unavailable_item_count": model_statuses.count("model_unavailable"),
                "error_item_count": model_statuses.count("model_error"),
                "added_candidate_count": sum(
                    int(item.get("model_review", {}).get("candidate_added_count", 0))
                    for item in items
                    if isinstance(item.get("model_review"), dict)
                ),
            },
        },
        "items": items,
    }


def build_kts_candidate_item(item: dict[str, Any], source_index: dict[str, Any]) -> dict[str, Any]:
    rule_candidates = build_item_candidates(item, source_index)
    candidates, model_review = apply_ai_semantic_recall(item, rule_candidates, source_index)
    return {
        "taxonomy_id": item.get("id", ""),
        "group": item.get("group", ""),
        "label": item.get("label", ""),
        "template_labels": item.get("template_labels", {}),
        "document_types": item.get("document_types", []),
        "extraction_mode": item.get("extraction_mode", "evidence"),
        "absence_checks": item.get("absence_checks", []),
        "retrieval_status": "candidate_found" if candidates else "no_candidate",
        "rule_candidate_count": len(rule_candidates),
        "candidate_count": len(candidates),
        "query_terms": collect_terms(item),
        "model_review": model_review,
        "candidates": candidates,
    }


def candidate_quote_for_extraction(candidate: dict[str, Any]) -> str:
    if candidate.get("ai_quote_verified"):
        return str(candidate.get("ai_quote") or "")
    return str(candidate.get("source_quote") or "")


def candidate_for_extraction_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "file_name": candidate.get("file_name", ""),
        "source_locator": candidate.get("source_locator", ""),
        "quote": candidate_quote_for_extraction(candidate),
        "context": str(candidate.get("text") or "")[:1400],
        "relevance": candidate.get("ai_relevance", ""),
    }


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_extracted_facts(value: Any) -> dict[str, list[str]]:
    facts = value if isinstance(value, dict) else {}
    return {
        "summary_points": normalize_string_list(facts.get("summary_points")),
        "key_terms": normalize_string_list(facts.get("key_terms")),
        "unclear_points": normalize_string_list(facts.get("unclear_points")),
    }


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


def ensure_rule_absences(draft_content: str, missing_labels: list[str]) -> str:
    if not missing_labels:
        return draft_content
    addition = "、".join(missing_labels)
    if not draft_content:
        return f"股东协议无{addition}的明确约定。"

    text = draft_content.rstrip()
    ending = "。" if text.endswith("。") else ""
    base = text[:-1] if ending else text
    marker = "的明确约定"
    if marker in base:
        return base.replace(marker, f"、{addition}{marker}", 1) + "。"
    if base.startswith("股东协议无"):
        return f"{base}、{addition}的明确约定。"
    return f"{draft_content} 股东协议未见{addition}的明确约定。"


def extract_absence_checks_with_ai(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    checks = build_absence_check_results(item, source_index)
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

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要起草助手。"
                "你的任务是核对股东协议中若干常见投资人权利是否有明确约定。"
                "你必须区分普通权利条款和仅作为其他特殊条款一部分出现的文字。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "为KTS事项“其他”判断若干常见权利是否未约定，并起草表格“内容”列。",
                    "rules": [
                        "只依据search_results和related_evidence，不得使用外部知识或猜测。",
                        "必须逐项判断search_results中的每一个label，并在draft_content中列明所有判断为未见明确约定的事项，不得遗漏。",
                        "keyword_hit_count为0时，可判断对应事项未见明显约定。",
                        "如命中片段只是定义、上下文引用或其他事项的一部分，而不是明确权利义务，也应判断为未见明确约定。",
                        "常规回购权必须区别于道德合规、利益输送、股权代持、资金往来等触发的特殊回购权；仅有特殊回购权时，常规回购权仍判断为未约定。",
                        "创始人全职付出与不竞争义务应分别判断；不得因不竞争义务存在就推定创始人全职付出已约定，反之亦然。",
                        "draft_content应接近参考模板文风，优先写成“股东协议无……”的简洁句式。",
                        "如不能确认某事项是否未约定，status设为needs_review，并在unclear_points和review_notes说明。",
                        "输出JSON格式：{\"status\":\"drafted|needs_review|unclear\",\"extracted_facts\":{\"summary_points\":[\"...\"],\"key_terms\":[\"...\"],\"unclear_points\":[\"...\"]},\"draft_content\":\"...\",\"review_notes\":[\"...\"]}",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "group": item.get("group", ""),
                    },
                    "search_results": checks,
                    "related_evidence": [
                        candidate_for_extraction_ai(candidate)
                        for candidate in candidates[:MAX_EXTRACTION_EVIDENCE]
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
    draft_content = ensure_rule_absences(draft_content, missing_rule_labels)
    if status == "drafted" and not draft_content:
        status = "needs_review"
    review_notes = normalize_string_list(response.get("review_notes"))
    if missing_rule_labels:
        review_notes.append(
            "系统根据全篇关键词或常规触发近邻规则补充未见明确约定事项："
            + "、".join(missing_rule_labels)
            + "。"
        )
    return {
        "status": status,
        "extracted_facts": normalize_extracted_facts(response.get("extracted_facts")),
        "draft_content": draft_content,
        "review_notes": review_notes,
    }


def extract_facts_with_ai(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    source_index: dict[str, Any],
) -> dict[str, Any]:
    if item.get("extraction_mode") == "absence_check":
        return extract_absence_checks_with_ai(item, candidates, source_index)
    if not candidates:
        return {
            "status": "unclear",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["未找到候选原文；当前版本不直接认定未见约定，需律师复核。"],
        }
    if not ai_configured():
        return {
            "status": "needs_review",
            "extracted_facts": normalize_extracted_facts({}),
            "draft_content": "",
            "review_notes": ["已找到候选原文，但模型抽取未完成，需律师复核。"],
        }

    messages = [
        {
            "role": "system",
            "content": (
                "你是融资交易KTS摘要起草助手。"
                "你只能依据给定候选证据抽取事实和起草摘要，不得编造未给出的交易条件。"
                "文风应接近律师交易文件主要条款摘要：简洁、准确、法言法语。"
                "必须输出JSON。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "基于候选证据为单个KTS事项抽取事实，并起草事项内容。",
                    "rules": [
                        "只依据给定evidence，不得使用外部知识或猜测。",
                        "如证据不足以形成摘要，status设为unclear，draft_content留空。",
                        "draft_content应是一段可放入KTS表格“内容”列的中文摘要，避免写成审查意见。",
                        "如存在不确定、冲突或需律师确认之处，写入unclear_points和review_notes。",
                        "输出JSON格式：{\"status\":\"drafted|needs_review|unclear\",\"extracted_facts\":{\"summary_points\":[\"...\"],\"key_terms\":[\"...\"],\"unclear_points\":[\"...\"]},\"draft_content\":\"...\",\"review_notes\":[\"...\"]}",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "template_labels": item.get("template_labels", {}),
                        "group": item.get("group", ""),
                    },
                    "evidence": [
                        candidate_for_extraction_ai(candidate)
                        for candidate in candidates[:MAX_EXTRACTION_EVIDENCE]
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
    return {
        "status": status,
        "extracted_facts": normalize_extracted_facts(response.get("extracted_facts")),
        "draft_content": draft_content,
        "review_notes": normalize_string_list(response.get("review_notes")),
    }


def build_kts_extraction_item(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    candidates = [candidate for candidate in item.get("candidates", []) if isinstance(candidate, dict)]
    evidence: list[dict[str, Any]] = []
    verified_count = 0
    for candidate in candidates[:MAX_EXTRACTION_EVIDENCE]:
        quote = candidate_quote_for_extraction(candidate)
        verification = verify_quote(quote, candidate, source_index)
        if verification.get("verified"):
            verified_count += 1
        evidence.append(
            {
                "candidate_id": candidate.get("candidate_id", ""),
                "file_name": candidate.get("file_name", ""),
                "source_locator": candidate.get("source_locator", ""),
                "quote": quote,
                "score": candidate.get("score", 0),
                "retrieval_channels": candidate.get("retrieval_channels", []),
                "ai_relevance": candidate.get("ai_relevance", ""),
                **verification,
            }
        )

    extraction = extract_facts_with_ai(item, candidates, source_index)
    return (
        {
            "taxonomy_id": item.get("taxonomy_id", ""),
            "group": item.get("group", ""),
            "label": item.get("label", ""),
            "template_labels": item.get("template_labels", {}),
            "status": extraction["status"],
            "candidate_count": len(candidates),
            "model_review": item.get("model_review", {}),
            "source_evidence": evidence,
            "extracted_facts": extraction["extracted_facts"],
            "draft_content": extraction["draft_content"],
            "review_notes": extraction["review_notes"],
        },
        verified_count,
    )


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

    verified_counts: list[int] = [0] * len(source_items)
    if ai_configured() and worker_count > 1:
        ordered_items: list[dict[str, Any] | None] = [None] * len(source_items)
        completed_count = 0
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(build_kts_extraction_item, item, source_index): index
                for index, item in enumerate(source_items)
            }
            for future in as_completed(futures):
                index = futures[future]
                ordered_items[index], verified_counts[index] = future.result()
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
        items = [item for item in ordered_items if item is not None]
    else:
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

    return {
        "phase": "v0.4-kts-extraction",
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
            "model_extraction": {
                "worker_count": worker_count,
            },
        },
        "items": items,
    }
