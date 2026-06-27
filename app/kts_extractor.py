"""KTS candidate retrieval and evidence-first extraction draft."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
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
TRANSACTION_CANDIDATE_CHARS = 6500
TRANSACTION_CANDIDATE_AFTER_BLOCKS = 14
SHAREHOLDER_RESERVED_CANDIDATE_CHARS = 6000
SHAREHOLDER_RESERVED_CANDIDATE_AFTER_BLOCKS = 16
MAX_EXTRACTION_CONTEXT_CHARS = 4200
EXTRACTION_CONTEXT_LEAD_CHARS = 500
MAX_AI_REVIEW_CANDIDATES = 8
MAX_AI_SCAN_SHARDS = 120
MAX_EXTRACTION_EVIDENCE = MAX_CANDIDATES_PER_ITEM
TRANSACTION_ARRANGEMENT_ID = "spa.transaction_arrangement"
ROFR_TAG_ID = "sha.rofr_tag"
SOURCE_BLOCK_OVERLAP_DUPLICATE_RATIO = 0.70
EVIDENCE_TEXT_SIMILARITY_DUPLICATE_RATIO = 0.82
EVIDENCE_QUOTE_SIMILARITY_DUPLICATE_RATIO = 0.86
MIN_SIMILARITY_TEXT_CHARS = 80
MAX_ADAPTIVE_MODEL_RETRIES = 2
ADAPTIVE_RETRY_SLEEP_SECONDS = 2
MAX_ABSENCE_SNIPPETS_PER_CHECK = 8
ABSENCE_SNIPPET_RADIUS = 180
MAX_STYLE_POLISH_CHARS_PER_ITEM = 1800
MAX_STYLE_POLISH_FIELD_VALUE_CHARS = 260
MAX_STYLE_POLISH_REVIEW_NOTE_CHARS = 180
STYLE_POLISH_TIMEOUT_SECONDS = 240
TAXONOMY_PATH = CAPABILITIES_DIR / "spa_sha_kts" / "kts_taxonomy.json"
CONTENT_SCHEMA_PATH = CAPABILITIES_DIR / "spa_sha_kts" / "kts_content_schema.json"
ProgressCallback = Callable[[dict[str, Any]], None]

EXTRA_TERMS = {
    "spa.transaction_arrangement": [
        "交易安排",
        "融资额",
        "投资款",
        "投前估值",
        "投后估值",
        "股权结构",
        "签署方",
        "共同订立",
        "协议各方",
        "截至本协议签署日",
        "签署日前",
        "持股比例",
        "股东持股",
    ],
    "spa.closing": ["交割日", "付款通知", "出资证明", "工商变更", "股东名册"],
    "spa.closing_conditions": ["交割先决条件", "先决条件", "重大不利", "投委会", "尽职调查", "法律意见书"],
    "spa.representations_warranties": [
        "陈述及保证",
        "陈述保证",
        "资料真实",
        "知识产权",
        "合法合规",
        "过渡期保证",
        "正常开展业务",
        "事先书面同意",
    ],
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
        "每一轮次投资人",
        "特定投资人",
        "多数投资人",
        "特别决议",
        "保护性事项",
    ],
    "sha.preemptive_right": ["优先认购权", "优先认购", "新增注册资本", "新增发行", "二次认购权"],
    "sha.transfer_restriction": [
        "股权转让限制",
        "转股限制",
        "锁定",
        "竞对",
        "QIPO",
        "在合格上市之前",
        "经全体投资人同意",
        "拟向任何第三方",
        "转让通知",
        "拟售股权",
        "转股方",
        "预期买方",
    ],
    "sha.rofr_tag": ["优先购买权", "共同出售权", "共售权", "随售权", "二次购买权", "ROFR"],
    "sha.drag_along": ["领售权", "拖售权", "强制出售", "被领售股东", "领售股东", "drag-along"],
    "sha.anti_dilution": ["反稀释", "棘轮", "全棘轮", "加权平均", "广义加权平均", "价格调整"],
    "sha.founder_obligations": ["创始人", "核心人员", "全职", "持续任职", "不竞争", "竞业", "受限股权", "股权成熟", "过错理由"],
    "sha.esop": ["员工股权激励", "期权池", "员工持股", "ESOP", "后续融资"],
    "sha.information_audit": ["信息权", "检查权", "查看核对", "经营记录", "访问", "审计权", "独立审计权", "财务报表", "预算"],
    "sha.redemption": ["特殊回购权", "回购触发事项", "回购义务人", "连带回购责任", "回购价款", "利益输送"],
    "sha.registration_rights": ["登记权", "注册权", "registration rights", "要求登记", "附带登记"],
    "sha.mfn_special_rights": ["最惠国", "更优惠", "同等享有", "Side Letter", "新项目优先投资权", "优先征询权", "优先投资"],
    "sha.dividend": ["分红权", "利润分配", "股利", "不得分红"],
    "sha.liquidation_preference": ["优先清算", "清算事件", "视同清算", "清算顺位", "优先清算额", "剩余财产"],
    "sha.other": ["常规回购权", "领售权", "最惠国待遇", "创始人全职", "全职付出", "不竞争义务", "竞业限制"],
}

DEFAULT_OUTPUT_POLICY = {
    "category": "mandatory_check_default_output",
    "instruction": "该事项属于常规完整KTS的默认输出项；必须抽取并用简洁KTS要点表达。",
}
OPTIONAL_OUTPUT_CATEGORIES = {"optional_conditional_output"}

OUTPUT_POLICY_BY_ITEM = {
    "spa.expenses": {
        "category": "mandatory_check_conditional_output",
        "instruction": "必须检查费用和税费，但只突出投资人费用承担、特殊税费或缺失提示；普通各自承担不要铺开。",
    },
    "spa.compliance": {
        "category": "conditional_output",
        "instruction": "仅在存在廉洁、反腐败、利益输送、代持或特殊违约金/回购联动时输出；普通合规套话应压缩。",
    },
    "spa.other": {
        "category": "conditional_output",
        "instruction": "只输出未被其他SPA事项覆盖且有实质影响的剩余条款；模板性保密、通知、生效条款应简写或不展开。",
    },
    "sha.preemptive_right": {
        "category": "mandatory_check_default_output",
        "instruction": "必须检查优先认购权；如无二次认购或例外异常，应在注释中提示。",
    },
    "sha.transfer_restriction": {
        "category": "mandatory_check_default_output",
        "instruction": "必须独立检查普通转股限制，不得用优先购买/共售程序或特殊回购后的自由转让替代。",
    },
    "sha.rofr_tag": {
        "category": "mandatory_check_default_output",
        "instruction": "必须与普通转股限制分开写；只保留ROFR、二次购买、共售比例和控制权变更共售等高价值点。",
    },
    "sha.drag_along": {
        "category": "optional_conditional_output",
        "instruction": "仅在存在领售/拖售/强制出售安排时输出；写清权利人、触发条件、被领售主体和出售范围。",
    },
    "sha.founder_obligations": {
        "category": "optional_conditional_output",
        "instruction": "仅在存在创始人/核心人员股权成熟、持续服务、全职、不竞争、保密或IP归属义务时输出；按义务类型总结。",
    },
    "sha.esop": {
        "category": "conditional_output",
        "instruction": "存在ESOP预留、估值里程碑、定向增发或审批安排时输出；必须覆盖全部编号里程碑。",
    },
    "sha.registration_rights": {
        "category": "optional_conditional_output",
        "instruction": "仅在存在登记权/注册权安排时输出；未见时不输出空行。",
    },
    "sha.mfn_special_rights": {
        "category": "optional_conditional_output",
        "instruction": "仅在存在最惠国、Side Letter、新项目优先投资权、优先征询权或类似特殊投资人权利时输出。",
    },
    "sha.other": {
        "category": "conditional_output",
        "instruction": "只在常规回购、领售、最惠国、创始人全职或不竞争义务等常见事项未见明确约定时输出缺失结论；已找到的事项应由对应专门条款承接。",
    },
}


def output_policy_for_item(item: dict[str, Any]) -> dict[str, str]:
    item_id = str(item.get("id") or item.get("taxonomy_id") or "")
    policy = OUTPUT_POLICY_BY_ITEM.get(item_id, DEFAULT_OUTPUT_POLICY)
    return dict(policy)


def optional_conditional_item(item: dict[str, Any]) -> bool:
    return output_policy_for_item(item).get("category") in OPTIONAL_OUTPUT_CATEGORIES


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
    enriched["output_policy"] = output_policy_for_item(item)
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
    before: int = 5,
    after: int = 4,
    max_chars_override: int | None = None,
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
    max_chars = (
        max_chars_override
        if max_chars_override is not None
        else MAX_TABLE_CANDIDATE_CHARS
        if any(block.get("kind") == "table_row" for block in selected)
        else MAX_CANDIDATE_CHARS
    )
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
        if item_id == TRANSACTION_ARRANGEMENT_ID:
            after_blocks = TRANSACTION_CANDIDATE_AFTER_BLOCKS
            max_chars_override = TRANSACTION_CANDIDATE_CHARS
        elif item_id == "sha.shareholder_reserved_matters":
            after_blocks = SHAREHOLDER_RESERVED_CANDIDATE_AFTER_BLOCKS
            max_chars_override = SHAREHOLDER_RESERVED_CANDIDATE_CHARS
        else:
            after_blocks = 4
            max_chars_override = None
        text, block_ids = build_candidate_text(
            document,
            shard,
            after=after_blocks,
            max_chars_override=max_chars_override,
        )
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


def block_text(block: dict[str, Any]) -> str:
    return str(block.get("normalized_text") or block.get("text") or "").strip()


def candidate_from_blocks(
    item_id: str,
    document: dict[str, Any],
    blocks: list[dict[str, Any]],
    suffix: str,
    score: int,
    reasons: list[str],
    retrieval_channel: str,
) -> dict[str, Any] | None:
    selected = [block for block in blocks if isinstance(block, dict) and block_text(block)]
    if not selected:
        return None
    text = "\n".join(block_text(block) for block in selected)
    quote_block = selected[-1]
    source_block_ids = [str(block.get("block_id") or "") for block in selected if block.get("block_id")]
    return {
        "candidate_id": f"{item_id}-{suffix}",
        "taxonomy_id": item_id,
        "score": score,
        "reasons": reasons,
        "retrieval_channels": [retrieval_channel],
        "doc_id": document.get("doc_id", ""),
        "file_name": document.get("file_name", ""),
        "document_role": document.get("document_role", {}),
        "document_type": document.get("document_type", {}),
        "shard_ids": [],
        "source_block_ids": source_block_ids,
        "source_span": source_span_for_blocks(document, source_block_ids),
        "source_locator": quote_block.get("source_locator", ""),
        "source_quote": exact_quote(block_text(quote_block), 180),
        "text": text,
        "character_count": len(text),
    }


def is_transaction_party_summary_block(text: str) -> bool:
    if not text:
        return False
    excluded_terms = (
        "统一社会信用代码",
        "住所",
        "执行事务合伙人",
        "委派代表",
        "法定代表人",
        "身份证号码",
        "身份证号",
    )
    if any(term in text for term in excluded_terms):
        return False
    if "本协议" in text and any(term in text for term in ("共同订立", "签署", "签订", "各方")):
        return True
    if re.match(r"^[甲乙丙丁戊己庚辛壬癸]方", text):
        return True
    if any(term in text for term in ("合称为", "统称为", "现有股东", "创始股东", "协议各方")):
        return True
    return False


def transaction_header_candidate(
    item_id: str,
    document: dict[str, Any],
) -> dict[str, Any] | None:
    raw_blocks = [block for block in document.get("raw_blocks", []) if isinstance(block, dict)]
    selected: list[dict[str, Any]] = []
    for block in raw_blocks[:120]:
        text = block_text(block)
        if selected and text.startswith("鉴于"):
            break
        if block.get("kind") != "paragraph":
            continue
        if is_transaction_party_summary_block(text):
            selected.append(block)
    return candidate_from_blocks(
        item_id,
        document,
        selected,
        "STRUCT-PARTIES",
        100,
        ["结构化候选：协议首部签署方"],
        "structural_header",
    )


def table_blocks_after_heading(
    raw_blocks: list[dict[str, Any]],
    heading_index: int,
) -> list[dict[str, Any]]:
    selected = [raw_blocks[heading_index]]
    table_started = False
    for block in raw_blocks[heading_index + 1 :]:
        if block.get("kind") == "table_row":
            selected.append(block)
            table_started = True
            continue
        if table_started:
            break
        if block.get("kind") == "paragraph" and block_text(block):
            break
    return selected if table_started else []


def transaction_cap_table_candidates(
    item_id: str,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_blocks = [block for block in document.get("raw_blocks", []) if isinstance(block, dict)]
    candidates: list[dict[str, Any]] = []
    seen_table_keys: set[tuple[str, str]] = set()
    for index, block in enumerate(raw_blocks):
        text = block_text(block)
        if "股权结构" not in text and "持股" not in text:
            continue
        blocks = table_blocks_after_heading(raw_blocks, index)
        if not blocks:
            continue
        table_key_value = table_key(blocks[1])
        if table_key_value and table_key_value in seen_table_keys:
            continue
        if table_key_value:
            seen_table_keys.add(table_key_value)
        is_pre_closing = any(term in text for term in ("签署日", "签署前", "签署日前"))
        suffix = "STRUCT-PRE-CAP" if is_pre_closing else f"STRUCT-CAP-{len(candidates) + 1:02d}"
        reason = "结构化候选：签署日前股权结构表" if is_pre_closing else "结构化候选：股权结构表"
        candidate = candidate_from_blocks(
            item_id,
            document,
            blocks,
            suffix,
            95 if is_pre_closing else 88,
            [reason],
            "structural_cap_table",
        )
        if candidate:
            candidates.append(candidate)
    return candidates[:3]


def transaction_arrangement_structural_candidates(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    item_id = str(item.get("id") or item.get("taxonomy_id") or "")
    if item_id != TRANSACTION_ARRANGEMENT_ID:
        return []
    candidates: list[dict[str, Any]] = []
    for document in source_index.get("documents", []):
        if not isinstance(document, dict) or not allowed_document_for_item(item, document):
            continue
        header = transaction_header_candidate(item_id, document)
        if header:
            candidates.append(header)
        candidates.extend(transaction_cap_table_candidates(item_id, document))
    return candidates


def sha_party_definition_candidates(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    item_id = str(item.get("id") or item.get("taxonomy_id") or "")
    if item_id != ROFR_TAG_ID:
        return []
    candidates: list[dict[str, Any]] = []
    for document in source_index.get("documents", []):
        if not isinstance(document, dict) or not allowed_document_for_item(item, document):
            continue
        for block in document.get("raw_blocks", [])[:140]:
            if not isinstance(block, dict) or block.get("kind") != "paragraph":
                continue
            text = block_text(block)
            if not (
                "合称" in text
                and "甲方" in text
                and "乙方一" in text
                and "乙方二" in text
                and "乙方三" in text
                and "或" in text
            ):
                continue
            candidate = candidate_from_blocks(
                item_id,
                document,
                [block],
                "STRUCT-DEFINITIONS",
                98,
                ["结构化候选：股东协议首部主体定义"],
                "structural_definitions",
            )
            if candidate:
                candidates.append(candidate)
            break
    return candidates


def structural_candidates_for_item(
    item: dict[str, Any],
    source_index: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        *transaction_arrangement_structural_candidates(item, source_index),
        *sha_party_definition_candidates(item, source_index),
    ]


def prepend_unique_candidates(
    structural_candidates: list[dict[str, Any]],
    rule_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, tuple[str, ...], str]] = set()
    for candidate in [*structural_candidates, *rule_candidates]:
        key = (
            str(candidate.get("doc_id") or ""),
            tuple(source_block_id_list(candidate)),
            normalize_evidence_quote(candidate.get("source_quote")),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(candidate)
    return merged


def ai_item_brief(item: dict[str, Any]) -> str:
    terms = "、".join(collect_terms(item)[:12])
    output_policy = output_policy_for_item(item)
    return (
        f"KTS事项：{item.get('label', '')}\n"
        f"事项ID：{item.get('id', item.get('taxonomy_id', ''))}\n"
        f"输出策略：{output_policy.get('category', '')}；{output_policy.get('instruction', '')}\n"
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
    structural_candidates = structural_candidates_for_item(item, source_index)
    candidates = prepend_unique_candidates(structural_candidates, rule_candidates)
    return {
        "taxonomy_id": item.get("id", ""),
        "group": item.get("group", ""),
        "label": item.get("label", ""),
        "template_labels": item.get("template_labels", {}),
        "content_schema": item.get("content_schema", {}),
        "output_policy": output_policy_for_item(item),
        "document_types": item.get("document_types", []),
        "extraction_mode": item.get("extraction_mode", "evidence"),
        "absence_checks": item.get("absence_checks", []),
        "retrieval_status": "candidate_found" if candidates else "no_candidate",
        "rule_candidate_count": len(rule_candidates),
        "candidate_count": len(candidates),
        "query_terms": collect_terms(item),
        "model_review": {"status": "skipped_unified"},
        "candidates": candidates,
    }


def candidate_quote_for_extraction(candidate: dict[str, Any]) -> str:
    if candidate.get("ai_quote_verified"):
        return str(candidate.get("ai_quote") or "")
    return str(candidate.get("source_quote") or "")


def candidate_context_for_extraction(candidate: dict[str, Any]) -> str:
    text = str(candidate.get("text") or "")
    if len(text) <= MAX_EXTRACTION_CONTEXT_CHARS:
        return text

    quote = candidate_quote_for_extraction(candidate).strip()
    needles = [quote[:120], quote[:80], quote[:40]]
    position = -1
    for needle in needles:
        if not needle:
            continue
        position = text.find(needle)
        if position >= 0:
            break
    if position < 0:
        return text[:MAX_EXTRACTION_CONTEXT_CHARS]

    start = max(0, position - EXTRACTION_CONTEXT_LEAD_CHARS)
    end = min(len(text), start + MAX_EXTRACTION_CONTEXT_CHARS)
    if end - start < MAX_EXTRACTION_CONTEXT_CHARS:
        start = max(0, end - MAX_EXTRACTION_CONTEXT_CHARS)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."
    return excerpt


def candidate_for_extraction_ai(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "file_name": candidate.get("file_name", ""),
        "document_role": candidate.get("document_role", {}),
        "source_locator": candidate.get("source_locator", ""),
        "quote": candidate_quote_for_extraction(candidate),
        "context": candidate_context_for_extraction(candidate),
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
                "absence_ok": bool(field.get("absence_ok")),
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


def truncate_for_style_polish(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def field_values_for_style_polish(item: dict[str, Any]) -> list[dict[str, Any]]:
    extracted_facts = item.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return []
    field_values = extracted_facts.get("field_values", [])
    if not isinstance(field_values, list):
        return []
    normalized: list[dict[str, Any]] = []
    for field in field_values:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        label = str(field.get("label") or "").strip()
        if not key and not label:
            continue
        normalized.append(
            {
                "key": key,
                "label": label,
                "status": str(field.get("status") or "").strip(),
                "value": truncate_for_style_polish(
                    field.get("value"),
                    MAX_STYLE_POLISH_FIELD_VALUE_CHARS,
                ),
                "note": truncate_for_style_polish(
                    field.get("note"),
                    MAX_STYLE_POLISH_REVIEW_NOTE_CHARS,
                ),
            }
        )
    return normalized


def review_notes_for_style_polish(item: dict[str, Any], key: str) -> list[str]:
    values = item.get(key, [])
    if not isinstance(values, list):
        return []
    return [
        truncate_for_style_polish(value, MAX_STYLE_POLISH_REVIEW_NOTE_CHARS)
        for value in values
        if str(value or "").strip()
    ]


def item_for_style_polish(item: dict[str, Any]) -> dict[str, Any]:
    content_schema = item.get("content_schema", {})
    drafting_guidance = ""
    if isinstance(content_schema, dict):
        drafting_guidance = str(content_schema.get("drafting_guidance") or "")
    return {
        "taxonomy_id": item.get("taxonomy_id", ""),
        "group": item.get("group", ""),
        "label": item.get("label", ""),
        "status": item.get("status", ""),
        "drafting_guidance": drafting_guidance,
        "output_policy": output_policy_for_item(item),
        "field_values": field_values_for_style_polish(item),
        "review_notes": review_notes_for_style_polish(item, "review_notes"),
        "missing_or_unclear": review_notes_for_style_polish(item, "missing_or_unclear"),
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
    if len(polished) < max(12, int(len(original) * 0.25)):
        return False, "too_short"
    missing_tokens = sorted(important_fact_tokens(original) - important_fact_tokens(polished))
    if missing_tokens:
        return False, "missing_fact_tokens:" + "、".join(missing_tokens[:8])
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
                "你的任务是把工作底稿式内容压缩为律师认可的KTS摘要。"
                "你可以重组、断句、分点并删除重复或低价值程序性细节，但不得新增或改变任何交易事实。"
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
                        "必须参考field_values确认每个事项的关键事实，不要只机械改写draft_content。",
                        "不得删除影响法律或商业判断的金额、比例、期限、主体、触发条件、门槛、例外和明确否定结论。",
                        "遵守每个事项的output_policy：条件输出项压缩低价值内容；缺失检查项只保留明确未见事项和必要提示。",
                        "目标长度：普通事项120-220字，复杂事项尽量不超过300字；超过4行时应合并相近要点或删除低价值程序细节。",
                        "可以删除重复表述、定义性铺陈、通知程序、模板套话和明显低价值原文细节。",
                        "可以把长句拆成更自然的短句，也可以把若干低层级字段合并成一个KTS要点。",
                        "不得在draft_content中出现“模型”“候选证据”“证据不完整”“建议核对原文”“完整文本”“完整条款”等工作底稿口吻。",
                        "如存在确会影响采用的硬复核点，可用简洁【待核：...】呈现；纯流程性核对提示不要写入正文。",
                        "对于absence_ok=true或output_policy要求检查缺失的字段，明确未见事项可简写为【注：未见...】。",
                        "保留律师KTS摘要文风：结论先行、简洁、准确、法言法语。",
                        "保留清晰的“要点：内容”分行结构；优先3-4个要点，可以合并低层级字段，但不要把所有内容糊成一段，也不要改变主项-子项层级的事实关系。",
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


def combined_candidate_text(candidates: list[dict[str, Any]]) -> str:
    return "\n".join(str(candidate.get("text") or "") for candidate in candidates)


def candidate_ids_with_text_markers(
    candidates: list[dict[str, Any]],
    markers: tuple[str, ...],
) -> list[str]:
    ids: list[str] = []
    for candidate in candidates:
        text = str(candidate.get("text") or "")
        if all(marker in text for marker in markers):
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id:
                ids.append(candidate_id)
    return ids[:4]


def has_transaction_signing_party_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = combined_candidate_text(candidates)
    return (
        any(term in text for term in ("合称为", "统称为", "协议各方", "每一方单称"))
        and any(term in text for term in ("现有股东", "创始股东", "公司"))
        and any(term in text for term in ("甲方", "乙方", "丙方", "本协议"))
    )


def has_transaction_cap_table_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = combined_candidate_text(candidates)
    return (
        any(term in text for term in ("股权结构", "持股情况", "持股比例", "股权比例"))
        and "注册资本" in text
        and "%" in text
        and "|" in text
    )


def transaction_pre_closing_cap_table_text(candidates: list[dict[str, Any]]) -> str:
    structured_pre_texts = [
        str(candidate.get("text") or "")
        for candidate in candidates
        if "STRUCT-PRE-CAP" in str(candidate.get("candidate_id") or "")
    ]
    if structured_pre_texts:
        return "\n".join(structured_pre_texts)
    pre_texts = [
        str(candidate.get("text") or "")
        for candidate in candidates
        if (
            any(term in str(candidate.get("text") or "") for term in ("签署日", "签署前", "签署日前"))
            and "股权结构" in str(candidate.get("text") or "")
        )
    ]
    if pre_texts:
        return "\n".join(pre_texts)
    return combined_candidate_text(candidates)


def transaction_cap_table_summary(candidates: list[dict[str, Any]]) -> str:
    text = transaction_pre_closing_cap_table_text(candidates)
    capital_values = re.findall(
        r"注册资本(?:为|由|变更为|增加至)?[^0-9]{0,12}([0-9][0-9,]*(?:\.\d+)?)\s*元",
        text,
    )
    rows = re.findall(
        r"\|\s*(\[[^\]\n]+\])\s*\|\s*([0-9][0-9,]*(?:\.\d+)?)\s*\|\s*([0-9]+(?:\.\d+)?%)",
        text,
    )
    ranked_rows: list[tuple[float, str, str]] = []
    for holder, _amount, percentage in rows:
        try:
            ranked_rows.append((float(percentage.rstrip("%")), holder, percentage))
        except ValueError:
            continue
    ranked_rows.sort(reverse=True)
    top_rows: list[tuple[float, str, str]] = []
    seen_holders: set[str] = set()
    for pct, holder, percentage in ranked_rows:
        if holder in seen_holders:
            continue
        seen_holders.add(holder)
        top_rows.append((pct, holder, percentage))
        if len(top_rows) >= 3:
            break

    parts: list[str] = []
    if capital_values:
        parts.append(f"签署日前注册资本{capital_values[0]}元")
    if top_rows:
        top_holders = "、".join(f"{holder}{percentage}" for _pct, holder, percentage in top_rows)
        parts.append(f"主要股东包括{top_holders}")
    if len(capital_values) >= 2 and capital_values[-1] != capital_values[0]:
        parts.append(f"增资完成后注册资本{capital_values[-1]}元")
    if parts:
        return "；".join(parts) + "。"
    return "协议前言/股权结构表列明签署日前注册资本、现有股东持股比例及本次增资完成后的股权结构。"


def normalize_amount_text(value: str) -> str:
    return value.strip().strip("【】[]").replace(",", ",")


def transaction_valuation_text(candidates: list[dict[str, Any]]) -> str:
    text = combined_candidate_text(candidates)
    patterns = [
        r"(投前估值为(?:人民币)?\s*【?([0-9][0-9,]*(?:\.\d+)?)】?\s*(亿元|亿|万元|元)?(?:人民币)?)",
        r"(投后估值为(?:人民币)?\s*【?([0-9][0-9,]*(?:\.\d+)?)】?\s*(亿元|亿|万元|元)?(?:人民币)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        full_text = re.sub(r"\s+", "", match.group(1))
        return full_text.replace("为人民币", "为人民币").replace("元人民币", "元")
    return ""


def transaction_financing_amount_text(candidates: list[dict[str, Any]]) -> str:
    text = combined_candidate_text(candidates)
    patterns = [
        r"合计向[^。\n]{0,80}?缴付人民币【?([0-9][0-9,]*(?:\.\d+)?)】?元",
        r"合计缴付人民币【?([0-9][0-9,]*(?:\.\d+)?)】?元",
        r"以【?([0-9][0-9,]*(?:\.\d+)?)】?元人民币的增资价款",
        r"增资价款(?:总额|合计)?(?:为|共计|合计)?人民币?【?([0-9][0-9,]*(?:\.\d+)?)】?元",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return normalize_amount_text(match.group(1))
    return ""


def transaction_financing_payer(candidates: list[dict[str, Any]]) -> str:
    text = combined_candidate_text(candidates)
    match = re.search(r"(?P<payer>\[[^\]\n]+\])将以【?[0-9][0-9,]*(?:\.\d+)?】?元人民币的增资价款", text)
    if match:
        return match.group("payer")
    return "投资方"


def transaction_capital_change_value(candidates: list[dict[str, Any]]) -> str:
    text = combined_candidate_text(candidates)
    match = re.search(
        r"注册资本将由【?([0-9][0-9,]*(?:\.\d+)?)】?元人民币增加至【?([0-9][0-9,]*(?:\.\d+)?)】?元人民币"
        r"(?:[,，]即新增【?([0-9][0-9,]*(?:\.\d+)?)】?元人民币的注册资本)?",
        text,
    )
    if not match:
        return ""
    before = normalize_amount_text(match.group(1))
    after = normalize_amount_text(match.group(2))
    added = normalize_amount_text(match.group(3) or "")
    value = f"注册资本由人民币{before}元增加至人民币{after}元"
    if added:
        value += f"，新增人民币{added}元注册资本"
    return value + "。"


def transaction_summary_line(candidates: list[dict[str, Any]]) -> str:
    valuation = transaction_valuation_text(candidates)
    financing = transaction_financing_amount_text(candidates)
    capital_change = transaction_capital_change_value(candidates)
    payer = transaction_financing_payer(candidates)
    parts: list[str] = []
    if valuation:
        parts.append("公司" + valuation)
    if financing:
        parts.append(f"{payer}以人民币{financing}元增资价款认购新增注册资本")
    if capital_change:
        parts.append(capital_change.rstrip("。"))
    if not parts:
        return ""
    return "交易安排：" + "；".join(parts) + "。"


def decimal_from_amount(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def transaction_financing_amount(candidates: list[dict[str, Any]]) -> Decimal | None:
    amount_text = transaction_financing_amount_text(candidates)
    if not amount_text:
        return None
    return decimal_from_amount(amount_text)


def extract_transaction_investor_amounts(candidates: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in combined_candidate_text(candidates).splitlines():
        text = line.strip()
        match = None
        if re.match(r"^\([ivxlcdm]+\)", text, flags=re.IGNORECASE):
            match = re.search(
                r"^\([ivxlcdm]+\)\s*(?P<investor>.+?)向[^。\n]{0,120}?缴付(?:等值于)?人民币【?(?P<amount>[0-9][0-9,]*(?:\.\d+)?)】?元",
                text,
                flags=re.IGNORECASE,
            )
        if match is None:
            match = re.search(
                r"^(?P<investor>\[[^\]\n]+\]|[^；;。\n]{2,60}?)投资【?(?P<amount>[0-9][0-9,]*(?:\.\d+)?)】?元(?:人民币)?认购",
                text,
            )
        if not match:
            continue
        investor = match.group("investor").strip(" ，,")
        amount = normalize_amount_text(match.group("amount"))
        key = (investor, amount)
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
    return rows


def transaction_investor_amounts_are_complete(
    rows: list[tuple[str, str]],
    candidates: list[dict[str, Any]],
) -> bool:
    if len(rows) < 3:
        return False
    total = transaction_financing_amount(candidates)
    if total is None:
        return len(rows) >= 8
    subtotal = Decimal("0")
    for _investor, amount in rows:
        parsed = decimal_from_amount(amount)
        if parsed is None:
            return False
        subtotal += parsed
    return abs(subtotal - total) <= Decimal("1")


def transaction_investor_amounts_value(rows: list[tuple[str, str]]) -> str:
    return "；".join(f"{investor}人民币{amount}元" for investor, amount in rows) + "。"


def transaction_investor_amounts_line(rows: list[tuple[str, str]]) -> str:
    if len(rows) <= 6:
        summary = "；".join(f"{investor}：人民币{amount}元" for investor, amount in rows)
    else:
        first = "；".join(f"{investor}：人民币{amount}元" for investor, amount in rows[:6])
        remaining = "、".join(investor for investor, _amount in rows[6:])
        summary = f"{first}；其余投资方包括{remaining}。"
    return "投资方明细：" + summary.rstrip("。") + "。"


def upsert_extracted_field(
    extracted_facts: dict[str, Any],
    key: str,
    label: str,
    value: str,
    source_candidate_ids: list[str],
    note: str,
) -> bool:
    field_values = extracted_facts.setdefault("field_values", [])
    if not isinstance(field_values, list):
        field_values = []
        extracted_facts["field_values"] = field_values
    target: dict[str, Any] | None = None
    for field in field_values:
        if isinstance(field, dict) and str(field.get("key") or "") == key:
            target = field
            break
    if target is None:
        target = {"key": key, "label": label}
        field_values.append(target)
    old_status = str(target.get("status") or "")
    old_value = str(target.get("value") or "")
    system_generated = "系统根据" in str(target.get("note") or "")
    if (
        old_status == "found"
        and old_value
        and not system_generated
        and not any(term in old_value for term in ("未见", "缺失", "不明确", "仅见", "不足", "无法确认"))
    ):
        return False
    target.update(
        {
            "key": key,
            "label": label,
            "status": "found",
            "value": value,
            "source_candidate_ids": source_candidate_ids,
            "note": note,
        }
    )
    return True


def weaken_found_field_if_missing_terms(
    extracted_facts: dict[str, Any],
    key: str,
    required_terms: tuple[str, ...],
) -> None:
    field_values = extracted_facts.get("field_values", [])
    if not isinstance(field_values, list):
        return
    for field in field_values:
        if not isinstance(field, dict) or str(field.get("key") or "") != key:
            continue
        value = str(field.get("value") or "")
        if str(field.get("status") or "") == "found" and not all(term in value for term in required_terms):
            field["status"] = "unclear"
            note = str(field.get("note") or "").strip()
            field["note"] = (note + " " if note else "") + "系统检测到关键术语缺失，需由守卫重写。"


def replace_or_insert_kts_line(
    draft_content: str,
    line: str,
    prefixes: tuple[str, ...],
    insert_index: int,
) -> str:
    lines = [existing for existing in draft_content.splitlines() if existing.strip()]
    for index, existing in enumerate(lines):
        if existing.strip().startswith(prefixes):
            lines[index] = line
            return "\n".join(lines)
    insert_at = min(max(insert_index, 0), len(lines))
    lines.insert(insert_at, line)
    return "\n".join(lines)


def remove_stale_transaction_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【(?:注|待核)[：:][^】]*(?:签署方|Cap Table|股权结构|现有股东结构|投前/投后估值|投前估值|投后估值|整体融资额|投资方|投资金额)[^】]*】",
        "",
        draft_content,
    )
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(term in stripped for term in ("签署方", "Cap Table", "现有股东结构")) and any(
            marker in stripped for marker in ("未见", "缺失", "待确认", "不完整")
        ):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def remove_stale_transaction_review_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(
                term in note
                for term in (
                    "签署方",
                    "Cap Table",
                    "现有股东结构",
                    "投资方",
                    "投资金额",
                    "投前/投后估值",
                    "投前估值",
                    "投后估值",
                    "整体融资额",
                    "融资额",
                )
            )
            and any(marker in note for marker in ("未见", "缺失", "未被模型提取", "待确认", "需确认", "需要律师确认", "复核"))
        )
    ]


def remove_stale_transaction_investor_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【注[：:][^】]*(?:投资方|投资金额|完整投资方清单|认购金额)[^】]*】",
        "",
        draft_content,
    )
    lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(term in stripped for term in ("投资方", "投资金额")) and any(
            marker in stripped for marker in ("已见部分", "不完整", "需确认", "待确认", "截断")
        ):
            if "签署方" in stripped:
                lines.append("签署方：增资协议由投资方、现有股东、公司及创始股东等相关方共同签署。")
            continue
        lines.append(stripped)
    return "\n".join(lines)


def guard_transaction_investor_amounts(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> bool:
    rows = extract_transaction_investor_amounts(candidates)
    if not transaction_investor_amounts_are_complete(rows, candidates):
        return False
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return False
    fixed = upsert_extracted_field(
        extracted_facts,
        "investors_and_amounts",
        "投资方及投资金额",
        transaction_investor_amounts_value(rows),
        candidate_ids_with_text_markers(candidates, ("本次增资", "增资款")),
        "系统根据本次增资分项投资人及金额清单补足，且分项金额合计与整体增资款一致。",
    )
    draft_content = remove_stale_transaction_investor_draft_notes(str(extraction.get("draft_content") or ""))
    investor_line = transaction_investor_amounts_line(rows)
    if "投资方明细" not in draft_content or any(marker in draft_content for marker in ("已见部分投资方", "完整投资方清单")):
        draft_content = replace_or_insert_kts_line(
            draft_content,
            investor_line,
            ("投资方明细", "投资方及金额"),
            2,
        )
        fixed = True
    if fixed:
        extraction["draft_content"] = draft_content
        extraction["review_notes"] = remove_stale_transaction_review_notes(extraction.get("review_notes", []))
    return fixed


def guard_transaction_arrangement(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    valuation = transaction_valuation_text(candidates)
    if valuation:
        fixed = upsert_extracted_field(
            extracted_facts,
            "valuation",
            "投前/投后估值",
            valuation + "。",
            candidate_ids_with_text_markers(candidates, ("投前估值",)),
            "系统根据本次增资总体方案补足估值字段。",
        ) or fixed
    financing_amount = transaction_financing_amount_text(candidates)
    if financing_amount:
        fixed = upsert_extracted_field(
            extracted_facts,
            "financing_amount",
            "整体融资额",
            f"人民币{financing_amount}元。",
            candidate_ids_with_text_markers(candidates, ("增资价款",)),
            "系统根据本次增资总体方案补足整体融资额字段。",
        ) or fixed
    capital_change = transaction_capital_change_value(candidates)
    if capital_change:
        fixed = upsert_extracted_field(
            extracted_facts,
            "capital_change",
            "注册资本变化",
            capital_change,
            candidate_ids_with_text_markers(candidates, ("注册资本", "增加至")),
            "系统根据本次增资总体方案补足注册资本变化字段。",
        ) or fixed

    if has_transaction_signing_party_evidence(candidates):
        fixed = upsert_extracted_field(
            extracted_facts,
            "signing_parties",
            "签署方",
            "增资协议由投资方、现有股东、公司及创始股东等相关方共同签署。",
            candidate_ids_with_text_markers(candidates, ("现有股东", "创始股东")),
            "系统根据协议首部签署方定义补足。",
        ) or fixed

    if has_transaction_cap_table_evidence(candidates):
        fixed = upsert_extracted_field(
            extracted_facts,
            "cap_table",
            "现有股东结构/Cap Table",
            transaction_cap_table_summary(candidates),
            candidate_ids_with_text_markers(candidates, ("股权结构", "注册资本")),
            "系统根据协议前言/股权结构表补足。",
        ) or fixed

    draft_content = remove_stale_transaction_draft_notes(str(extraction.get("draft_content") or ""))
    summary_line = transaction_summary_line(candidates)
    if summary_line:
        draft_content = replace_or_insert_kts_line(
            draft_content,
            summary_line,
            ("交易安排", "本次交易", "增资安排"),
            0,
        )
    if has_transaction_signing_party_evidence(candidates) and "签署方" not in draft_content:
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "签署方：增资协议由投资方、现有股东、公司及创始股东等相关方共同签署。",
            ("签署方", "协议各方"),
            1,
        )
    if has_transaction_cap_table_evidence(candidates):
        cap_line = "股权结构：" + transaction_cap_table_summary(candidates).rstrip("。") + "。"
        draft_content = replace_or_insert_kts_line(
            draft_content,
            cap_line,
            ("股权结构", "现有股东结构", "Cap Table", "股东安排"),
            2,
        )
    if guard_transaction_investor_amounts(extraction, candidates):
        draft_content = str(extraction.get("draft_content") or draft_content)
        if has_transaction_signing_party_evidence(candidates) and "签署方" not in draft_content:
            draft_content = replace_or_insert_kts_line(
                draft_content,
                "签署方：增资协议由投资方、现有股东、公司及创始股东等相关方共同签署。",
                ("签署方", "协议各方"),
                1,
            )
    if fixed or draft_content != str(extraction.get("draft_content") or ""):
        extraction["draft_content"] = draft_content
        extraction["review_notes"] = remove_stale_transaction_review_notes(extraction.get("review_notes", []))


def remove_spa_other_workpaper_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if not draft_content:
        return
    cleaned = draft_content
    cleaned = cleaned.replace("候选证据未见", "未见")
    cleaned = cleaned.replace("，但受让方范围在现有证据中未完整显示", "")
    cleaned = cleaned.replace("；受让方范围在现有证据中未完整显示", "")
    cleaned = cleaned.replace("但受让方范围在现有证据中未完整显示。", "。")
    cleaned = re.sub(
        r"【注[：:][^】]*(?:9\.7|转让条款|需核对全文|完整文本|未完整显示)[^】]*】",
        "【注：未见适用法律条款。】",
        cleaned,
    )
    cleaned = re.sub(
        r"【注[：:][^】]*(?:权利义务转让|转让限制)[^】]*(?:未完整显示|不完整|全文)[^】]*】",
        "【注：未见适用法律条款；权利义务转让限制可结合第9.7条确认。】",
        cleaned,
    )
    cleaned = re.sub(
        r"【注[：:][^】]*(?:排他|独家|适用法律|权利义务转让)[^】]*(?:完整限制条件不清|不清|不完整|证据不完整)[^】]*】",
        "【注：未见排他/独家安排及适用法律约定；权利义务转让限制可结合第9.7条确认。】",
        cleaned,
    )
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())
    if cleaned != draft_content:
        extraction["draft_content"] = cleaned

    review_notes = normalize_string_list(extraction.get("review_notes"))
    filtered_notes = [
        note
        for note in review_notes
        if "draft_content" not in note
        and "候选证据" not in note
        and "核对全文" not in note
        and "全文" not in note
        and "再定稿" not in note
        and "证据不完整" not in note
        and "完整第9.7条" not in note
        and "建议律师确认" not in note
        and not ("未见适用法律条款" in note and "适用" in cleaned)
    ]
    if "适用法律" not in cleaned and any("适用法律" in note or "9.7" in note for note in review_notes):
        concise_note = "未见适用法律条款；权利义务转让的受让方范围可结合第9.7条确认。"
        if concise_note not in filtered_notes:
            filtered_notes.append(concise_note)
    extraction["review_notes"] = filtered_notes


def remove_board_reserved_workpaper_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if draft_content:
        cleaned = re.sub(
            r"【待核[：:][^】]*(?:投资人董事席位|在任情况|实际行使)[^】]*】",
            "",
            draft_content,
        )
        cleaned = re.sub(
            r"【注[：:][^】]*(?:投资人董事席位|在任情况|实际行使)[^】]*】",
            "",
            cleaned,
        )
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    extraction["review_notes"] = [
        note
        for note in notes
        if not (
            any(term in note for term in ("投资人董事", "席位", "实际行使", "在任情况"))
            and any(marker in note for marker in ("确认", "复核", "未见", "未体现"))
        )
    ]


def clean_anti_dilution_review_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if draft_content:
        cleaned = re.sub(
            r"【待核[：:]第3\.5\.4第\(3\)项是否确为反稀释例外。?】",
            "【注：第3.5.4第(3)项作为反稀释例外的口径可结合协议版本确认。】",
            draft_content,
        )
        cleaned = re.sub(
            r"【待核[：:][^】]*3\.5\.4[^】]*(?:反稀释例外|清算剩余财产分配|文本误植)[^】]*】",
            "【注：第3.5.4第(3)项作为反稀释例外的口径可结合协议版本确认。】",
            cleaned,
        )
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    extraction["review_notes"] = [
        note
        for note in notes
        if not (
            "3.5.4" in note
            and any(marker in note for marker in ("核对", "复核", "误植"))
        )
    ]


def clean_rofr_tag_workpaper_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if draft_content:
        cleaned = re.sub(
            r"【注[：:][^】]*(?:共同出售权|共售)[^】]*(?:未完整|无法确认|不明确|不完整)[^】]*】",
            "【注：未见控制权变更全额共售安排。】",
            draft_content,
        )
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    extraction["review_notes"] = [
        note
        for note in notes
        if not (
            any(term in note for term in ("共同出售权", "共售权", "共售比例"))
            and any(marker in note for marker in ("截断", "无法确认", "不明确", "不完整", "未完整"))
        )
    ]


def clean_esop_review_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if draft_content:
        cleaned = re.sub(
            r"【待核[：:][^】]*(?:两项10%|累计|审批机构|占位符)[^】]*】",
            "【注：两项10%额度是否累计适用、审批机构口径可结合协议定义确认。】",
            draft_content,
        )
        cleaned = cleaned.replace("占位符所指主体", "协议定义所指主体")
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    extraction["review_notes"] = [
        note.replace("重点复核", "关注").replace("占位符", "协议定义")
        for note in notes
    ]


def normalize_conventional_kts_labels(item: dict[str, Any]) -> None:
    draft_content = str(item.get("draft_content") or "")
    if not draft_content:
        return
    replacements = {
        "排他安排": "排他期承诺",
        "排他/独家安排": "排他期承诺",
        "独家安排": "排他期承诺",
    }
    lines: list[str] = []
    changed = False
    for line in draft_content.splitlines():
        stripped = line.strip()
        for old, new in replacements.items():
            if stripped.startswith(old + "："):
                stripped = new + "：" + stripped.split("：", 1)[1]
                changed = True
                break
        lines.append(stripped)
    if changed:
        item["draft_content"] = "\n".join(line for line in lines if line)


def extracted_field_value(extracted_facts: dict[str, Any], key: str) -> str:
    field_values = extracted_facts.get("field_values", [])
    if not isinstance(field_values, list):
        return ""
    for field in field_values:
        if not isinstance(field, dict) or str(field.get("key") or "") != key:
            continue
        if str(field.get("status") or "") == "found":
            return str(field.get("value") or "").strip()
    return ""


def extracted_field_status(extracted_facts: dict[str, Any], key: str) -> str:
    field_values = extracted_facts.get("field_values", [])
    if not isinstance(field_values, list):
        return ""
    for field in field_values:
        if isinstance(field, dict) and str(field.get("key") or "") == key:
            return str(field.get("status") or "").strip()
    return ""


def remove_internal_candidate_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if "候选证据" not in note and "draft_content" not in note and "系统检测" not in note
    ]


def guard_post_closing_covenants_summary(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    lines = [line for line in draft_content.splitlines() if line.strip()]
    if len(draft_content) <= 320 and len(lines) <= 4:
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return
    if not extracted_field_value(extracted_facts, "use_of_proceeds"):
        return
    if not extracted_field_value(extracted_facts, "capital_contribution"):
        return

    compact_lines = [
        "资金用途：限业务拓展、研发、生产、资本支出及主营业务；偿债需股东会全票同意，对外投资/委托贷款/证券期货需[公司或组织_BH]同意。",
        "实缴承诺：相关现有股东应于第一次交割日后三年内实缴；[公司或组织_BF]应于2029年12月31日前实缴。",
    ]
    if extracted_field_value(extracted_facts, "non_compete_and_priority"):
        compact_lines.append(
            "竞业/业务唯一性：核心人员受竞业限制；[公司或组织_AO]须确保公司为[公司或组织_BD]相关业务唯一实体并列为最高优先级项目。"
        )
    if extracted_field_value(extracted_facts, "service_and_team") or extracted_field_value(extracted_facts, "continued_service"):
        compact_lines.append(
            "团队/IP/任职：落实知识产权权属或授权、团队保密/IP/竞业安排；两名创始股东承诺八年或合格上市后一周年孰早前不主动离职。"
        )
    missing_notes: list[str] = []
    if extracted_field_status(extracted_facts, "ip_transfer") == "not_found":
        missing_notes.append("IP转移")
    if extracted_field_status(extracted_facts, "regulatory_milestones") == "not_found":
        missing_notes.append("业务许可/备案里程碑")
    if missing_notes:
        compact_lines.append("【注：未见" + "及".join(missing_notes) + "安排。】")

    compact = "\n".join(compact_lines)
    if len(compact) < len(draft_content):
        extraction["draft_content"] = compact
        extraction["review_notes"] = remove_internal_candidate_notes(extraction.get("review_notes", []))
        extraction["lawyer_notes"] = remove_internal_candidate_notes(extraction.get("lawyer_notes", []))


def has_board_composition_core_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = combined_candidate_text(candidates)
    return (
        "五(5)名" in text
        and "各推选一(1)名" in text
        and "推选两(2)名" in text
        and "董事长" in text
    )


def remove_stale_board_review_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            ("本方" in note and any(marker in note for marker in ("确认", "对应主体", "不明", "复核")))
            or ("观察员" in note and "背景" in note)
        )
    ]


def remove_board_client_identity_draft_note(draft_content: str) -> str:
    cleaned = draft_content.replace("需确认本方对应主体；", "")
    cleaned = cleaned.replace("需确认本方对应主体，", "")
    cleaned = cleaned.replace("需确认本方对应主体。", "")
    cleaned = re.sub(r"；?本方对应主体不明。?", "", cleaned)
    cleaned = re.sub(r"【注[：:][^】]*本方对应主体不明[^】]*】", "【注：未见观察员席位授予安排。】", cleaned)
    cleaned = cleaned.replace("【注：】", "")
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def guard_board_composition(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_board_composition_core_evidence(candidates):
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return
    fixed = upsert_extracted_field(
        extracted_facts,
        "investor_board_right",
        "本方董事席位/观察员",
        "组织_W、组织_Z各推选1名董事，二者委派董事合称组织_AK董事；未见独立观察员委派权。",
        candidate_ids_with_text_markers(candidates, ("合称", "董事")),
        "系统根据董事会构成条款补足投资人董事席位，并将客户身份未知视为非阻塞信息。",
    )
    draft_content = remove_board_client_identity_draft_note(str(extraction.get("draft_content") or ""))
    if draft_content != extraction.get("draft_content"):
        extraction["draft_content"] = draft_content
        fixed = True
    if fixed:
        extraction["review_notes"] = remove_stale_board_review_notes(extraction.get("review_notes", []))
        extraction["lawyer_notes"] = remove_stale_board_review_notes(extraction.get("lawyer_notes", []))
        extraction["missing_or_unclear"] = remove_stale_board_review_notes(extraction.get("missing_or_unclear", []))


def has_rofr_holder_alias_definition(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "甲方、乙方一、乙方二、乙方三合称" in compact_text
        and "组织_AP" in compact_text
        and "组织_AK" in compact_text
        and "或" in compact_text
    )


def remove_stale_rofr_review_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(term in note for term in ("优先购买权人", "主体表述不一致", "占位表述不一致"))
            and any(marker in note for marker in ("核对", "确认", "不一致", "占位"))
        )
    ]


def remove_stale_rofr_draft_note(draft_content: str) -> str:
    cleaned = draft_content.replace("优先购买权人占位表述不一致；", "")
    cleaned = cleaned.replace("；优先购买权人占位表述不一致", "")
    cleaned = cleaned.replace("优先购买权人主体表述不一致；", "")
    cleaned = cleaned.replace("；优先购买权人主体表述不一致", "")
    cleaned = cleaned.replace("【注：】", "")
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def replace_rofr_holder_line(draft_content: str) -> str:
    line = (
        "优先购买权：合格上市前，定义为甲方及乙方一至三的投资人（AP/AK）可在同等条件下优先购买拟售股权；"
        "购买回复期为收到转让通知后10个工作日。"
    )
    return replace_or_insert_kts_line(
        draft_content,
        line,
        ("优先购买权",),
        0,
    )


def guard_rofr_tag(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_rofr_holder_alias_definition(candidates):
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return
    fixed = upsert_extracted_field(
        extracted_facts,
        "rofr_holder",
        "优先购买权人",
        "股东协议首部定义中，甲方、乙方一、乙方二、乙方三合称AP或AK；第3.3条项下优先购买权人即该等投资人。",
        candidate_ids_with_text_markers(candidates, ("甲方", "乙方一", "合称")),
        "系统根据股东协议首部主体定义消除AP/AK别名造成的伪冲突。",
    )
    draft_content = remove_stale_rofr_draft_note(str(extraction.get("draft_content") or ""))
    draft_content = replace_rofr_holder_line(draft_content)
    if draft_content != extraction.get("draft_content"):
        extraction["draft_content"] = draft_content
        fixed = True
    if fixed:
        extraction["review_notes"] = remove_stale_rofr_review_notes(extraction.get("review_notes", []))


def has_rofr_tag_along_evidence(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "3.3.5" in compact_text
        and "共售股东" in compact_text
        and "共同出售权" in compact_text
        and "转股方拟向预期买方出售的股权数乘以一个分数" in compact_text
    )


def remove_stale_tag_along_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(term in note for term in ("共同出售权", "共售权", "共同出售比例", "共同出售权人", "共售比例"))
            and any(marker in note for marker in ("缺失", "未见", "不完整", "完整文本", "未被模型提取", "需要律师确认", "建议补充", "复核"))
        )
    ]


def replace_rofr_tag_along_line(draft_content: str) -> str:
    line = (
        "共同出售权：未行使或放弃优先购买权的投资人可在购买回复期届满前发出共售通知；"
        "共售数量按“拟售股权数×共售股东持股注册资本/(转股方持股注册资本+实际共售股东持股注册资本总和)”计算。"
    )
    return replace_or_insert_kts_line(
        draft_content,
        line,
        ("共同出售权", "共售权", "共同出售", "共售比例"),
        3,
    )


def guard_rofr_tag_along_terms(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_rofr_tag_along_evidence(candidates):
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = upsert_extracted_field(
        extracted_facts,
        "tag_holder",
        "共同出售权人",
        "未行使或放弃第3.3条优先购买权的投资人，可在购买回复期届满前发出共售通知并作为共售股东行使共同出售权。",
        candidate_ids_with_text_markers(candidates, ("3.3.5", "共售股东")),
        "系统根据第3.3.5条共同出售权条款补足。",
    )
    fixed = upsert_extracted_field(
        extracted_facts,
        "tag_ratio",
        "共同出售比例",
        "共售股权数量不超过转股方拟出售股权数乘以一个分数：分子为该共售股东持有的注册资本金额，分母为转股方持有注册资本金额加实际行使共同出售权的全体投资人持有注册资本金额总和。",
        candidate_ids_with_text_markers(candidates, ("共售股权", "分子", "分母")),
        "系统根据第3.3.5条共售比例公式补足。",
    ) or fixed
    if not fixed:
        return
    extraction["draft_content"] = replace_rofr_tag_along_line(str(extraction.get("draft_content") or ""))
    extraction["review_notes"] = remove_stale_tag_along_notes(extraction.get("review_notes", []))


def has_representations_authority_evidence(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "4.6签约授权" in compact_text
        and "完全法律权利、能力" in compact_text
        and "签署本次增资交易文件" in compact_text
    )


def has_representations_capital_legality_evidence(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "4.7" in compact_text
        and "增资款足额且合法" in compact_text
        and "资金来源符合国家法律" in compact_text
    )


def remove_stale_representations_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(term in note for term in ("签署授权", "法律能力", "增资款来源", "增资款及持股合法性"))
            and any(marker in note for marker in ("未见", "未体现", "未被模型提取", "缺失"))
        )
    ]


def ensure_representations_core_lines(draft_content: str) -> str:
    lines = [line for line in draft_content.splitlines() if line.strip()]
    core_line = (
        "签约及出资合法性：各方具备签署、履行交易文件的法律能力及授权；投资方增资款足额且来源合法，相关主体不存在代持、委托持股或禁止持股。"
    )
    info_line = "资料真实准确：公司方提供资料在重大方面真实、准确、完整，不存在未披露重大事项或限制本次增资的其他交易安排。"

    new_lines: list[str] = []
    inserted_core = False
    inserted_info = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("签约及出资合法性", "持股及资料真实性", "增资款及持股合法性")):
            if not inserted_core:
                new_lines.append(core_line)
                inserted_core = True
            if not inserted_info:
                new_lines.append(info_line)
                inserted_info = True
            continue
        if stripped.startswith("资料真实准确"):
            if not inserted_info:
                new_lines.append(info_line)
                inserted_info = True
            continue
        if "未见签署授权" in stripped or "增资款来源合法性" in stripped:
            continue
        new_lines.append(stripped)
    if not inserted_core:
        new_lines.insert(0, core_line)
    if not inserted_info:
        new_lines.insert(1, info_line)
    return "\n".join(new_lines)


def guard_representations_core_fields(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    if has_representations_authority_evidence(candidates):
        fixed = upsert_extracted_field(
            extracted_facts,
            "authority",
            "签署授权和法律能力",
            "各方均具有签署并履行协议及交易文件的完全法律权利、能力，并已取得所需权利或授权；签署交易文件不违反对其有约束力的法律文件或构成不履行。",
            candidate_ids_with_text_markers(candidates, ("4.6", "签约授权")),
            "系统根据第4.6条签约授权补足。",
        ) or fixed
    if has_representations_capital_legality_evidence(candidates):
        fixed = upsert_extracted_field(
            extracted_facts,
            "capital_legality",
            "增资款及持股合法性",
            "投资方已准备足够资金或充分资金安排，可按约支付增资价款，且资金来源符合法律法规要求；相关主体不存在代持、委托持股或禁止持股情形。",
            candidate_ids_with_text_markers(candidates, ("4.7", "增资款")),
            "系统根据第4.7条增资款足额且合法及第4.8条真实性保证补足。",
        ) or fixed
    if not fixed:
        return
    extraction["draft_content"] = ensure_representations_core_lines(str(extraction.get("draft_content") or ""))
    extraction["review_notes"] = remove_stale_representations_notes(extraction.get("review_notes", []))


def has_shareholder_unanimous_matter_evidence(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "1.1.7" in compact_text
        and "事项应当包括" in compact_text
        and "组织_AP" in compact_text
        and "批准分红或任何利润分配" in compact_text
    )


def remove_stale_shareholder_reserved_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(term in note for term in ("全体投资人同意事项", "全体投资人", "1.1.7机制", "多数投资人同意事项", "每一轮次投资人", "第8.2条"))
            and any(marker in note for marker in ("确认", "未直接", "需要律师确认", "未被模型提取", "缺失", "截断", "复核"))
        )
    ]


def has_shareholder_dual_majority_evidence(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "未经每一轮次" in compact_text
        and "三分之二" in compact_text
        and "下列(1)项" in compact_text
        and "下列(2)-(12)项" in compact_text
        and "(12)" in compact_text
    )


def guard_shareholder_dual_majority_matters(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_shareholder_dual_majority_evidence(candidates):
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    fixed = upsert_extracted_field(
        extracted_facts,
        "approval_mechanism",
        "通过机制",
        "第8.2条采用双多数机制：第(1)项须每一轮次投资人多数同意；第(2)-(12)项须投资人多数同意。两类多数均指相关投资人合计持股三分之二或以上的一名或多名投资人。",
        candidate_ids_with_text_markers(candidates, ("8.2", "三分之二")),
        "系统根据第8.2条双多数机制补足。",
    ) or fixed
    fixed = upsert_extracted_field(
        extracted_facts,
        "unanimous_matters",
        "特定/每轮投资人同意事项",
        "第(1)项须每一轮次投资人多数同意，内容为修改投资人享有的股东权利、优先权或设置限制，或使其他股东享有更优先/相同权利，或达成对投资人不利的约定。",
        candidate_ids_with_text_markers(candidates, ("下列(1)项", "股东权利")),
        "系统根据第8.2条第(1)项补足。",
    ) or fixed
    fixed = upsert_extracted_field(
        extracted_facts,
        "majority_matters",
        "多数投资人同意事项",
        "第(2)-(12)项须投资人多数同意，覆盖章程修改、增减资/稀释性发行、减资回购注销、解散清算、分红及利润分配、合并分立重组/控制权变更/重大资产处置、上市方案、董事会构成调整、主营业务重大变化、发行数字资产及双方认可的其他重大事项。",
        candidate_ids_with_text_markers(candidates, ("下列(2)-(12)项", "发行任何数字货币")),
        "系统根据第8.2条第(2)-(12)项补足。",
    ) or fixed
    if not fixed:
        return

    draft_content = str(extraction.get("draft_content") or "")
    draft_content = replace_or_insert_kts_line(
        draft_content,
        "通过机制：第(1)项须每一轮次投资人多数同意；第(2)-(12)项须投资人多数同意；两类多数均为相关投资人合计持股三分之二或以上。",
        ("通过机制", "同意机制"),
        0,
    )
    draft_content = replace_or_insert_kts_line(
        draft_content,
        "保护事项：第(1)项覆盖修改投资人权利或设置不利限制；第(2)-(12)项覆盖章程修改、增减资及稀释性发行、减资回购注销、清算分红、重组/控制权变更、上市方案、董事会构成、主营业务重大变化、发行数字资产及其他重大事项。",
        ("保护事项", "多数事项", "特定/每轮投资人同意事项"),
        1,
    )
    if "[[公司或组织_AE]或组织_G]" in combined_candidate_text(candidates) and "[商标品牌_G]" in combined_candidate_text(candidates):
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "特别否决：第(2)/(5)/(10)项中涉及整体性变更主营或核心业务的事项，还需[[公司或组织_AE]或组织_G]和[商标品牌_G]同意；后续融资新增投资人董事后该单独否决权终止。",
            ("特别否决", "特定否决权"),
            2,
        )
    draft_content = re.sub(r"【注[：:][^】]*(?:候选证据|本方能否|多数门槛|完整文本|缺失序号)[^】]*】", "", draft_content)
    extraction["draft_content"] = "\n".join(line.rstrip() for line in draft_content.splitlines() if line.strip())
    extraction["review_notes"] = remove_stale_shareholder_reserved_notes(extraction.get("review_notes", []))


def guard_shareholder_reserved_matters(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if has_shareholder_dual_majority_evidence(candidates):
        guard_shareholder_dual_majority_matters(extraction, candidates)
        return
    if not has_shareholder_unanimous_matter_evidence(candidates):
        return
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return
    fixed = upsert_extracted_field(
        extracted_facts,
        "unanimous_matters",
        "特定/每轮投资人同意事项",
        "1.1.7项下事项须包括AP/投资人同意方可通过，包括修改章程、增减注册资本、清算/解散/终止、实质改变或终止主营业务、批准分红或任何利润分配。",
        candidate_ids_with_text_markers(candidates, ("1.1.7", "批准分红")),
        "系统根据第1.1.7条保护性事项补足；匿名化文本以AP/投资人同意表述。",
    )
    if not fixed:
        return
    extraction["draft_content"] = replace_or_insert_kts_line(
        str(extraction.get("draft_content") or ""),
        "特定投资人同意事项：1.1.7项下事项须包括AP/投资人同意，覆盖章程修改、增减注册资本、清算/解散/终止、实质改变或终止主营业务、分红或利润分配。",
        ("全体/特定投资人同意事项", "全体投资人同意事项", "特定投资人同意事项"),
        1,
    )
    extraction["review_notes"] = remove_stale_shareholder_reserved_notes(extraction.get("review_notes", []))


def has_liquidation_event_definition(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "3.4.1如果发生以下任何事件" in compact_text
        and "清算事件" in compact_text
        and "控制权发生变更" in compact_text
        and "全部或实质上全部资产" in compact_text
    )


def has_liquidation_new_project_make_whole(candidates: list[dict[str, Any]]) -> bool:
    compact_text = re.sub(r"\s+", "", combined_candidate_text(candidates))
    return (
        "3.4.5" in compact_text
        and "10年内" in compact_text
        and "新项目" in compact_text
        and "差额部分" in compact_text
        and "零对价股权转让或增发股权" in compact_text
    )


def remove_stale_liquidation_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            any(term in note for term in ("清算事件", "特殊补偿", "新项目"))
            and any(marker in note for marker in ("定义不完整", "未完整", "需要律师确认", "未被模型提取", "复核原协议"))
        )
    ]


def guard_liquidation_preference(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    if has_liquidation_event_definition(candidates):
        fixed = upsert_extracted_field(
            extracted_facts,
            "liquidation_events",
            "清算事件",
            "包括法定清算、解散或关闭；公司被兼并、收购或类似控制权变更且原股东在存续实体持股或表决权低于50%；公司全部或实质上全部资产出售，或全部/实质上全部知识产权许可或出售给第三方。",
            candidate_ids_with_text_markers(candidates, ("3.4.1", "清算事件")),
            "系统根据第3.4.1条清算事件定义补足。",
        ) or fixed
    if has_liquidation_new_project_make_whole(candidates):
        weaken_found_field_if_missing_terms(extracted_facts, "special_make_whole", ("10年", "新项目"))
        fixed = upsert_extracted_field(
            extracted_facts,
            "special_make_whole",
            "特殊补偿/新项目权益",
            "如清算所得不超过清算优先款，自清算事件起10年内相关义务人从事新项目且投资方拟投资的，清算优先款与已得款项差额视为其对新项目投资，并以零对价转让或增发取得等值权益。",
            candidate_ids_with_text_markers(candidates, ("3.4.5", "新项目")),
            "系统根据第3.4.5条新项目差额权益安排补足。",
        ) or fixed
    if not fixed:
        return
    draft_content = str(extraction.get("draft_content") or "")
    if has_liquidation_event_definition(candidates):
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "清算事件：包括法定清算/解散/关闭、控制权变更致原股东持股或表决权低于50%、全部或实质全部资产出售，以及全部/实质全部知识产权许可或出售。",
            ("清算事件", "清算触发"),
            0,
        )
    if has_liquidation_new_project_make_whole(candidates):
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "特殊安排：法定分配偏离约定时由超额取得方再分配；如清算所得不超过清算优先款，10年内特定新项目下差额可视为投资并取得等值权益。",
            ("特殊安排", "特殊补偿", "新项目权益"),
            4,
        )
    extraction["draft_content"] = draft_content
    extraction["review_notes"] = remove_stale_liquidation_notes(extraction.get("review_notes", []))


def has_price_reset_anti_dilution_formula(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    formula_markers = [
        "调整后每单位认购价格=低价增资时",
        "调整后每单位认购价格＝低价增资时",
        "调整后每单位认购价格等于低价增资时",
    ]
    return any(marker in compact_text for marker in formula_markers)


def guard_anti_dilution_method(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_price_reset_anti_dilution_formula(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    corrected = False
    field_values = extracted_facts.get("field_values", [])
    if isinstance(field_values, list):
        for field in field_values:
            if not isinstance(field, dict) or str(field.get("key") or "") != "method":
                continue
            value = str(field.get("value") or "")
            if "加权平均" in value and "全棘轮" not in value and "价格重设" not in value:
                field["value"] = "公式显示调整后每单位认购价格等于低价增资价格，属于价格重设/接近全棘轮；非加权平均。"
                field["status"] = "found"
                corrected = True

    draft_content = str(extraction.get("draft_content") or "")
    if "广义加权平均" in draft_content and "全棘轮" not in draft_content and "价格重设" not in draft_content:
        extraction["draft_content"] = draft_content.replace("广义加权平均", "价格重设/接近全棘轮")
        corrected = True

    notes = extraction.get("review_notes", [])
    if not isinstance(notes, list):
        notes = []
    guard_note = "系统校验：反稀释公式显示调整后每单位认购价格等于低价增资价格，应按价格重设/接近全棘轮理解，需律师核对。"
    if corrected or "全棘轮" not in draft_content:
        if guard_note not in notes:
            notes.append(guard_note)
    extraction["review_notes"] = notes


def has_redemption_compliance_trigger(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    redemption_link = (
        "第2.3条履行回购义务" in compact_text
        or "履行回购义务" in compact_text
        or ("要求任意" in compact_text and "回购义务" in compact_text)
    )
    compliance_terms = [
        "代持",
        "利益输送",
        "资金往来",
        "不当利益",
        "现金或现金等价物",
        "礼品及其它有形或无形利益",
    ]
    breach_terms = ["违反本条", "违反本第6.1.5条", "如违反本条"]
    return (
        redemption_link
        and any(term in compact_text for term in compliance_terms)
        and any(term in compact_text for term in breach_terms)
    )


def remove_stale_trigger_missing_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    stale_markers = ["触发事项本身缺失", "回购触发事项", "触发事项缺失"]
    return [
        note
        for note in normalized
        if not any(marker in note for marker in stale_markers)
    ]


def has_redemption_price_formula(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    investment_formula = (
        "股权回购价款=回购股权对应的投资总额" in compact_text
        and "1+【8】%×n" in compact_text
        and ("已取得的股息或分红" in compact_text or "股息或分红" in compact_text)
    )
    nav_formula = (
        "股权回购协议签订日前最近一期经审计" in compact_text
        and "净资产" in compact_text
        and "要求回购的股权比例" in compact_text
    )
    return investment_formula and nav_formula


def remove_stale_redemption_price_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            "价格" in note
            and any(marker in note for marker in ("未完整", "不完整", "需核对第2.3条全文", "需要律师确认"))
        )
    ]


def replace_or_insert_redemption_price_line(draft_content: str) -> str:
    price_line = (
        "价格及付款：回购价款按两项孰高确定：回购股权对应投资总额×(1+8%×投资年数)-已取得股息/分红，"
        "或最近一期经审计净资产×要求回购股权比例；回购通知后60日内全额支付。"
    )
    lines = [line for line in draft_content.splitlines() if line.strip()]
    if not lines:
        return price_line
    price_prefixes = ("价格", "回购价格", "价格及付款", "回购价款")
    for index, line in enumerate(lines):
        if line.strip().startswith(price_prefixes):
            lines[index] = price_line
            return "\n".join(lines)
    insert_at = 1 if lines and lines[0].strip().startswith(("触发", "触发事项", "触发及义务人")) else 0
    lines.insert(insert_at, price_line)
    return "\n".join(lines)


def remove_stale_redemption_price_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【注[：:][^】]*(?:价格公式|完整价格|第2\.3条全文|第2\.3条完整)[^】]*】",
        "",
        draft_content,
    )
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def guard_redemption_price_formula(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_redemption_price_formula(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    price_value = (
        "回购价款按两种价格较高者确定：1）回购股权对应投资总额×(1+8%×投资年数)-已取得股息或分红；"
        "2）股权回购协议签订日前最近一期经审计净资产×要求回购股权比例。"
    )
    field_values = extracted_facts.get("field_values", [])
    if isinstance(field_values, list):
        for field in field_values:
            if not isinstance(field, dict) or str(field.get("key") or "") != "price_formula":
                continue
            value = str(field.get("value") or "")
            if field.get("status") != "found" or "投资总额" not in value or "净资产" not in value:
                field["status"] = "found"
                field["value"] = price_value
                field["note"] = "系统根据第2.3条两项回购价款公式补足。"
                fixed = True

    draft_content = str(extraction.get("draft_content") or "")
    if "投资总额" not in draft_content or "净资产" not in draft_content or "仅见其中一项" in draft_content:
        draft_content = replace_or_insert_redemption_price_line(draft_content)
        fixed = True
    if "价格公式" in draft_content or "第2.3条全文" in draft_content or "第2.3条完整" in draft_content:
        draft_content = remove_stale_redemption_price_draft_notes(draft_content)
        fixed = True
    extraction["draft_content"] = draft_content

    if fixed:
        extraction["review_notes"] = remove_stale_redemption_price_notes(extraction.get("review_notes", []))


def has_dividend_special_approval(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "1.1.7" in compact_text
        and "事项应当包括" in compact_text
        and "同意方可通过" in compact_text
        and "批准分红或任何利润分配" in compact_text
    )


def remove_stale_dividend_approval_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            "分红" in note
            and "门槛" in note
            and any(marker in note for marker in ("完整", "核对", "确认"))
        )
    ]


def remove_stale_dividend_approval_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【注[：:][^】]*(?:分红批准|表决门槛|批准门槛)[^】]*】",
        "",
        draft_content,
    )
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def replace_or_insert_dividend_approval_line(draft_content: str) -> str:
    approval_line = "批准机制：公司原则上不得分红；分红或任何利润分配须经股东会批准，并作为1.1.7项下事项需包括特定投资人同意。"
    lines = [line for line in draft_content.splitlines() if line.strip()]
    if not lines:
        return approval_line
    for index, line in enumerate(lines):
        if line.strip().startswith(("批准机制", "分红批准", "批准")):
            lines[index] = approval_line
            return "\n".join(lines)
    lines.insert(0, approval_line)
    return "\n".join(lines)


def guard_dividend_approval(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_dividend_special_approval(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    approval_value = "公司原则上不得分红；批准分红或任何利润分配属于1.1.7项下事项，须经股东会批准并包括特定投资人同意。"
    fixed = False
    field_values = extracted_facts.get("field_values", [])
    if isinstance(field_values, list):
        for field in field_values:
            if not isinstance(field, dict) or str(field.get("key") or "") != "approval":
                continue
            value = str(field.get("value") or "")
            if "1.1.7" not in value or "特定投资人同意" not in value:
                field["status"] = "found"
                field["value"] = approval_value
                field["note"] = "系统根据1.1.7项下分红/利润分配保护性事项补足。"
                fixed = True

    draft_content = str(extraction.get("draft_content") or "")
    if "1.1.7" not in draft_content or "特定投资人同意" not in draft_content:
        draft_content = replace_or_insert_dividend_approval_line(draft_content)
        fixed = True
    if "分红批准" in draft_content or "表决门槛" in draft_content or "批准门槛" in draft_content:
        draft_content = remove_stale_dividend_approval_draft_notes(draft_content)
        fixed = True
    extraction["draft_content"] = draft_content

    if fixed:
        extraction["review_notes"] = remove_stale_dividend_approval_notes(extraction.get("review_notes", []))


def has_information_inspection_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        any(term in compact_text for term in ("查看核对", "检查", "访问"))
        and any(term in compact_text for term in ("财务账簿", "经营记录", "资产"))
        and ("信息权人" in compact_text or "信息权和检查权" in compact_text)
    )


def remove_stale_information_inspection_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            "检查权" in note
            and any(marker in note for marker in ("未见", "未被模型提取", "具体安排", "完整第7条", "核对"))
        )
    ]


def remove_stale_information_inspection_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【注[：:][^】]*(?:检查权|独立审计权|费用承担)[^】]*】",
        "",
        draft_content,
    )
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def guard_information_audit_inspection(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_information_inspection_evidence(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    inspection_value = (
        "信息权人可在正常工作时间且不影响公司正常经营的前提下，查看核对公司及子公司的资产、财务账簿和其他经营记录，"
        "并可就经营事项与董事、监事、高级管理人员或专业服务机构沟通/访问。"
    )
    fixed = upsert_extracted_field(
        extracted_facts,
        "inspection",
        "检查权",
        inspection_value,
        candidate_ids_with_text_markers(candidates, ("查看核对", "财务账簿")),
        "系统根据第7.3条检查权条款补足。",
    )

    draft_content = str(extraction.get("draft_content") or "")
    if "查看核对" not in draft_content and "财务账簿" not in draft_content:
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "检查权：信息权人可在正常工作时间且不影响经营的前提下，查看核对公司及子公司的资产、财务账簿和经营记录，并可沟通/访问相关人员或机构。",
            ("检查权", "检查安排"),
            2,
        )
        fixed = True
    if "检查权" in draft_content and any(marker in draft_content for marker in ("未见检查权", "未见检查")):
        draft_content = remove_stale_information_inspection_draft_notes(draft_content)
        fixed = True
    if fixed:
        extraction["draft_content"] = draft_content
        extraction["review_notes"] = remove_stale_information_inspection_notes(extraction.get("review_notes", []))


def has_redemption_obligor_definition(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "回购义务人" in compact_text
        and "回购权人" in compact_text
        and (
            "及/或" in compact_text
            or "连带回购责任" in compact_text
            or "应承担连带回购责任" in compact_text
        )
    )


def remove_stale_redemption_obligor_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            "回购义务人" in note
            and any(marker in note for marker in ("未显示", "需结合", "确认", "复核", "不明确"))
        )
    ]


def remove_stale_redemption_obligor_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【注[：:][^】]*(?:回购义务人|义务人的具体主体)[^】]*】",
        "",
        draft_content,
    )
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def guard_redemption_obligor(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_redemption_obligor_definition(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    obligor_value = (
        "回购权人可向公司及/或相关创始人/持股平台要求回购；持股平台范围排除其他员工间接持股部分。"
    )
    fixed = upsert_extracted_field(
        extracted_facts,
        "obligor",
        "回购义务人",
        obligor_value,
        candidate_ids_with_text_markers(candidates, ("回购义务人", "回购权人")),
        "系统根据回购义务人定义及连带责任条款补足。",
    )

    draft_content = remove_stale_redemption_obligor_draft_notes(str(extraction.get("draft_content") or ""))
    if "回购义务人" not in draft_content and "义务人" not in draft_content:
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "义务人：回购权人可向公司及/或相关创始人/持股平台要求回购；公司未按期足额支付时，相关创始人承担连带回购责任。",
            ("义务人", "回购义务人"),
            1,
        )
        fixed = True
    elif "义务人" in draft_content and "未显示" in draft_content:
        draft_content = replace_or_insert_kts_line(
            draft_content,
            "义务人：回购权人可向公司及/或相关创始人/持股平台要求回购；公司未按期足额支付时，相关创始人承担连带回购责任。",
            ("义务人", "回购义务人"),
            1,
        )
        fixed = True
    if fixed:
        extraction["draft_content"] = draft_content
        extraction["review_notes"] = remove_stale_redemption_obligor_notes(extraction.get("review_notes", []))


def guard_redemption_trigger(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_redemption_compliance_trigger(candidates):
        return

    trigger_text = (
        "违反业务行为道德合规/廉洁条款，包括提供或接受不当利益，或除投资合作及经同意合作外存在代持、利益输送、资金往来等利益安排，并触发第2.3条回购义务。"
    )
    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    field_values = extracted_facts.get("field_values", [])
    if isinstance(field_values, list):
        for field in field_values:
            if not isinstance(field, dict) or str(field.get("key") or "") != "trigger":
                continue
            value = str(field.get("value") or "")
            if field.get("status") != "found" or "触发事件" in value or not value:
                field["status"] = "found"
                field["value"] = trigger_text
                field["note"] = "系统根据前置廉洁/业务行为道德合规条款与第2.3条回购义务联动补足。"

    draft_content = str(extraction.get("draft_content") or "")
    trigger_line = f"触发事项：{trigger_text}"
    lines = [line for line in draft_content.splitlines() if line.strip()]
    replaced = False
    trigger_prefixes = ("触发事项：", "触发及义务人：", "触发及义务：", "触发：")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(trigger_prefixes):
            continue
        if "触发事件" in stripped or not any(
            marker in stripped
            for marker in ("廉洁", "道德合规", "反腐败", "不当利益", "代持", "利益输送", "资金往来")
        ):
            lines[index] = trigger_line
        replaced = True
        break
    if not replaced:
        lines.insert(0, trigger_line)
    trigger_indices = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith(trigger_prefixes)
    ]
    if len(trigger_indices) > 1:
        preferred_index = next(
            (
                index
                for index in trigger_indices
                if "义务人" in lines[index] or "可要求" in lines[index]
            ),
            trigger_indices[0],
        )
        lines = [
            line
            for index, line in enumerate(lines)
            if index == preferred_index or index not in trigger_indices
        ]
    extraction["draft_content"] = "\n".join(lines)

    notes = remove_stale_trigger_missing_notes(extraction.get("review_notes", []))
    guard_note = "系统校验：回购触发事项来自前置廉洁/业务行为道德合规条款与第2.3条回购义务的联动，仍需律师核对适用范围。"
    if guard_note not in notes:
        notes.append(guard_note)
    extraction["review_notes"] = notes


def has_transition_covenant_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    if "过渡期" not in compact_text:
        return False
    normal_course = "正常地开展业务" in compact_text or "正常开展业务" in compact_text
    consent_restriction = (
        "未经" in compact_text
        and "事先书面同意" in compact_text
        and "不得" in compact_text
    )
    return normal_course or consent_restriction


def transition_covenant_candidate_ids(candidates: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for candidate in candidates:
        text = normalize_for_match(str(candidate.get("text") or ""))
        compact_text = re.sub(r"\s+", "", text)
        if "过渡期" not in compact_text:
            continue
        if "正常地开展业务" in compact_text or (
            "未经" in compact_text
            and "事先书面同意" in compact_text
            and "不得" in compact_text
        ):
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id:
                ids.append(candidate_id)
    return ids[:3]


def remove_stale_transition_missing_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    return [
        note
        for note in normalized
        if not (
            "过渡期" in note
            and any(marker in note for marker in ("未见", "未被模型提取", "补充", "复核"))
        )
    ]


def draft_mentions_transition_covenant(draft_content: str) -> bool:
    text = normalize_for_match(draft_content)
    return "过渡期" in text or "正常经营" in text or "正常地开展业务" in text


def add_transition_covenant_line(draft_content: str) -> str:
    transition_line = "过渡期限制：过渡期内，公司应按过往惯例正常经营；除完成本次增资交易外，未经投资方事先书面同意不得实施约定限制事项。"
    lines = [line for line in draft_content.splitlines() if line.strip()]
    if not lines:
        return transition_line
    insert_at = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("【")),
        len(lines),
    )
    lines.insert(insert_at, transition_line)
    return "\n".join(lines)


def guard_representations_transition_covenant(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    if not has_transition_covenant_evidence(candidates):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    fixed = False
    field_values = extracted_facts.setdefault("field_values", [])
    if not isinstance(field_values, list):
        field_values = []
        extracted_facts["field_values"] = field_values

    transition_value = (
        "过渡期内，公司应按过往惯例正常开展业务；除完成本次增资交易外，未经投资方事先书面同意不得实施约定限制事项。"
    )
    source_candidate_ids = transition_covenant_candidate_ids(candidates)
    transition_field = None
    for field in field_values:
        if isinstance(field, dict) and str(field.get("key") or "") == "transition_covenants":
            transition_field = field
            break
    if transition_field is None:
        transition_field = {
            "key": "transition_covenants",
            "label": "过渡期限制事项",
        }
        field_values.append(transition_field)
    if transition_field.get("status") != "found" or not transition_field.get("value"):
        transition_field["status"] = "found"
        transition_field["value"] = transition_value
        transition_field["source_candidate_ids"] = source_candidate_ids
        transition_field["note"] = "系统根据过渡期保证条款补足。"
        fixed = True

    draft_content = str(extraction.get("draft_content") or "")
    if not draft_mentions_transition_covenant(draft_content):
        extraction["draft_content"] = add_transition_covenant_line(draft_content)
        fixed = True

    if fixed:
        notes = remove_stale_transition_missing_notes(extraction.get("review_notes", []))
        extraction["review_notes"] = notes
        if str(extraction.get("status") or "") == "needs_review" and not any(
            marker in note
            for note in notes
            for marker in ("需律师", "待确认", "建议律师", "需要律师")
        ):
            extraction["status"] = "drafted"


def clean_representations_absence_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    transition_absent = "未见" in draft_content and "过渡期限制" in draft_content
    if draft_content:
        cleaned = draft_content
        cleaned = cleaned.replace("明确过渡期限制事项", "过渡期限制事项")
        cleaned = re.sub(r"；?公司及现有股东签署授权具体条款未完整呈现", "", cleaned)
        cleaned = re.sub(r"；?附件I完整条款未全部展示", "", cleaned)
        cleaned = cleaned.replace("需复核是否存在其他重点或非惯常陈述保证", "如附件I另有非惯常陈述保证可补充")
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    filtered: list[str] = []
    for note in notes:
        if any(marker in note for marker in ("候选证据", "未全部展示", "需复核", "现有候选证据")):
            continue
        if "过渡期限制事项为本KTS边界内应检查项" in note:
            transition_absent = True
            continue
        filtered.append(note)
    if transition_absent and "未见过渡期限制事项的，已作为缺失检查结论处理。" not in filtered:
        filtered.append("未见过渡期限制事项的，已作为缺失检查结论处理。")
    extraction["review_notes"] = filtered


def clean_redemption_review_tone(extraction: dict[str, Any]) -> None:
    draft_content = str(extraction.get("draft_content") or "")
    if draft_content:
        cleaned = re.sub(
            r"【注[：:][^】]*(?:需核对原文|完整定义|引用条款内容)[^】]*】",
            "【注：回购价格中的I按回购权人实际投资成本口径理解。】",
            draft_content,
        )
        cleaned = re.sub(
            r"【待核[：:][^】]*(?:10%违约金|逾期违约金|关系)[^】]*】",
            "【注：第4.0.7条10%违约金可能与逾期违约金并行适用。】",
            cleaned,
        )
        extraction["draft_content"] = "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())

    notes = normalize_string_list(extraction.get("review_notes"))
    filtered = [
        note
        for note in notes
        if not any(marker in note for marker in ("候选证据", "建议律师复核", "需律师复核", "仍需律师核对", "系统校验"))
    ]
    extraction["review_notes"] = filtered


def normalize_redemption_subpoint_labels(item: dict[str, Any]) -> None:
    draft_content = str(item.get("draft_content") or "")
    if not draft_content:
        return
    lines: list[str] = []
    changed = False
    for raw_line in draft_content.splitlines():
        line = raw_line.strip()
        if line.startswith("触发事项："):
            line = "回购触发事项：" + line.split("：", 1)[1]
            changed = True
        elif line.startswith("义务人及价格："):
            body = line.split("：", 1)[1]
            split_match = re.search(r"；\s*价格(?P<price>按.+)", body)
            if split_match:
                obligor = body[: split_match.start()].strip().rstrip("。")
                obligor = re.sub(r"^回购义务人为", "", obligor).strip()
                lines.append("回购义务人：" + obligor.rstrip("。") + "。")
                lines.append("回购价格：" + split_match.group("price").rstrip("。") + "。")
                changed = True
                continue
            line = "回购义务人及价格：" + body
            changed = True
        elif line.startswith("义务人与行权："):
            line = "回购义务人及行权：" + line.split("：", 1)[1]
            changed = True
        elif line.startswith("价格与付款："):
            body = line.split("：", 1)[1]
            split_match = re.search(r"；\s*(?P<deadline>(?:义务人|回购义务人).+)", body)
            if split_match:
                price = body[: split_match.start()].strip().rstrip("。")
                price = re.sub(r"^回购价格为", "", price).strip()
                lines.append("回购价格：" + price + "。")
                lines.append("回购期限：" + split_match.group("deadline").rstrip("。") + "。")
                changed = True
                continue
            line = "回购价格及付款期限：" + body
            changed = True
        elif line.startswith("行使及付款："):
            line = "回购期限：" + line.split("：", 1)[1]
            changed = True
        elif line.startswith("行使期限及付款："):
            line = "回购期限：" + line.split("：", 1)[1]
            changed = True
        elif line.startswith("回购价格及付款期限："):
            body = line.split("：", 1)[1]
            split_match = re.search(r"；\s*(?P<deadline>(?:义务人|回购义务人).+)", body)
            if split_match:
                price = body[: split_match.start()].strip().rstrip("。")
                price = re.sub(r"^回购价格为", "", price).strip()
                lines.append("回购价格：" + price + "。")
                lines.append("回购期限：" + split_match.group("deadline").rstrip("。") + "。")
                changed = True
                continue
            changed = True
        elif line.startswith("回购价格：回购价格为"):
            line = "回购价格：" + line.split("：", 1)[1].removeprefix("回购价格为")
            changed = True
        elif line.startswith(("逾期及顺位：", "逾期与顺位：")):
            line = "逾期责任及顺位：" + line.split("：", 1)[1]
            changed = True
        lines.append(line)
    if changed:
        item["draft_content"] = "\n".join(line for line in lines if line)


FOUNDER_VESTING_LINE = (
    "股权成熟：创始人/相关高管持有的受限股权分4年成熟；"
    "特定人员分别自天使轮增资交割日或全职加入并签署劳动合同日起，每满1年成熟25%；"
    "约定收购/兼并且收购方同意或完成IPO时，全部加速成熟。"
)
FOUNDER_SERVICE_LINE = (
    "持续服务：自天使轮增资交割日至IPO后一周年，相关创始人/核心人员在全职加入前应投入剩余实质性全部工作时间和精力；"
    "全职加入后应投入实质性全部工作时间和精力，均不得在公司/集团外任职、投资或提供服务；"
    "经投资人同意的研究机构任职例外，但不得实质影响其对公司的职责和经营管理。"
)
FOUNDER_BREACH_LINE = (
    "离职/过错后果：成熟期内主动离职、不续签或因过错被解职的，受限股权无论是否成熟均按约定无偿或以法定最低价格转让；"
    "其他离职的未成熟部分同样适用，已成熟部分保留但放弃投票权/董事提名等管理权。"
)
FOUNDER_NON_COMPETE_LINE = (
    "竞业及保密/IP：限制期至离职后两年或不再持股后两年孰晚；"
    "限制投资、参与或协助竞争业务，招揽客户/员工，以及为非公司目的披露或使用公司商业、财务、交易、知识产权及其他保密信息。"
)


def has_founder_vesting_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "受限股权" in compact_text
        and "分4年成熟" in compact_text
        and "每满一(1)年" in compact_text
        and "25%" in compact_text
        and "加速成熟" in compact_text
    )


def has_founder_service_commitment_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "自天使轮增资交割日起" in compact_text
        and "首次公开发行后一(1)年" in compact_text
        and "全职加入" in compact_text
        and "实质性全部工作时间和精力" in compact_text
        and "不得" in compact_text
        and "之外任职或投资或提供服务" in compact_text
        and "其他研究机构" in compact_text
        and "实质不利影响" in compact_text
    )


def has_founder_breach_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "主动" in compact_text
        and "离职" in compact_text
        and "过错理由" in compact_text
        and "无偿或以法律允许的最低价格" in compact_text
        and "表决权" in compact_text
        and "董事提名权" in compact_text
    )


def has_founder_non_compete_scope_evidence(candidates: list[dict[str, Any]]) -> bool:
    text = normalize_for_match(combined_candidate_text(candidates))
    compact_text = re.sub(r"\s+", "", text)
    return (
        "限制期" in compact_text
        and "解除劳动(服务)关系之后两(2)年" in compact_text
        and "不直接或者间接持有" in compact_text
        and "竞争性活动" in compact_text
        and "竞争关系" in compact_text
        and "劝说" in compact_text
        and "商业秘密或保密信息" in compact_text
    )


def set_extracted_field(
    extracted_facts: dict[str, Any],
    key: str,
    label: str,
    value: str,
    source_candidate_ids: list[str],
    note: str,
) -> None:
    field_values = extracted_facts.setdefault("field_values", [])
    if not isinstance(field_values, list):
        field_values = []
        extracted_facts["field_values"] = field_values
    target: dict[str, Any] | None = None
    for field in field_values:
        if isinstance(field, dict) and str(field.get("key") or "") == key:
            target = field
            break
    if target is None:
        target = {"key": key, "label": label}
        field_values.append(target)
    target.update(
        {
            "key": key,
            "label": label,
            "status": "found",
            "value": value,
            "source_candidate_ids": source_candidate_ids,
            "note": note,
        }
    )


def founder_obligations_source_ids(
    candidates: list[dict[str, Any]],
    markers: tuple[str, ...],
) -> list[str]:
    ids = candidate_ids_with_text_markers(candidates, markers)
    if ids:
        return ids
    return [
        str(candidate.get("candidate_id") or "")
        for candidate in candidates
        if str(candidate.get("candidate_id") or "").startswith("sha.founder_obligations-")
    ][:4]


def founder_obligations_compact_draft(
    include_vesting: bool,
    include_service: bool,
    include_breach: bool,
    include_non_compete: bool,
) -> str:
    lines: list[str] = []
    if include_vesting:
        lines.append(FOUNDER_VESTING_LINE)
    if include_service:
        lines.append(FOUNDER_SERVICE_LINE)
    if include_breach:
        lines.append(FOUNDER_BREACH_LINE)
    if include_non_compete:
        lines.append(FOUNDER_NON_COMPETE_LINE)
    return "\n".join(lines)


def remove_stale_founder_obligations_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    filtered: list[str] = []
    for note in normalized:
        if "已按当前事项边界概括违约后果" in note:
            continue
        if re.search(r"C\d{2}", note) and any(term in note for term in ("核心证据", "无关", "未纳入摘要")):
            continue
        if any(term in note for term in ("C06", "候选片段", "证据片段", "截断", "未完整", "完整呈现")) and any(
            topic in note
            for topic in ("IPO", "持续义务", "持续任职", "全职", "承诺", "竞业", "保密", "IP", "知识产权")
        ):
            continue
        if any(term in note for term in ("无法确认承诺对象", "具体义务", "完整例外", "零散保密/IP", "独立保密/IP归属协议条款")):
            continue
        filtered.append(note)
    return filtered


def clean_founder_obligations_draft_notes(draft_content: str) -> str:
    cleaned = re.sub(
        r"【(?:注|待核)[：:][^】]*(?:IPO|持续义务|承诺对象|具体义务|例外|未完整|截断)[^】]*】",
        "",
        draft_content,
    )
    return "\n".join(line.rstrip() for line in cleaned.splitlines() if line.strip())


def refresh_founder_obligations_status(extraction: dict[str, Any]) -> None:
    notes = normalize_string_list(extraction.get("review_notes"))
    if not notes and str(extraction.get("status") or "") == "needs_review":
        extraction["status"] = "drafted"


def founder_fact_value(item: dict[str, Any], key: str) -> str:
    extracted_facts = item.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return ""
    return extracted_field_value(extracted_facts, key)


def founder_facts_support_compact_draft(item: dict[str, Any]) -> tuple[bool, bool, bool, bool]:
    vesting = founder_fact_value(item, "vesting")
    service = founder_fact_value(item, "service_commitment")
    breach = founder_fact_value(item, "breach_consequence")
    non_compete = founder_fact_value(item, "non_compete")
    confidentiality = founder_fact_value(item, "confidentiality_ip")
    return (
        "4年" in vesting and "25%" in vesting,
        "IPO后一周年" in service and "实质性全部工作时间" in service,
        "无偿或以法定最低价格" in breach and "投票权/董事提名" in breach,
        "离职后两年" in non_compete and "保密信息" in confidentiality,
    )


def clean_founder_obligations_review_tone(item: dict[str, Any]) -> None:
    draft_content = clean_founder_obligations_draft_notes(str(item.get("draft_content") or ""))
    include_vesting, include_service, include_breach, include_non_compete = founder_facts_support_compact_draft(item)
    if include_service and include_non_compete and (
        "未完整" in draft_content
        or "截断" in draft_content
        or "持续服务：" not in draft_content
        or "竞业及保密/IP：" not in draft_content
    ):
        draft_content = founder_obligations_compact_draft(
            include_vesting,
            include_service,
            include_breach,
            include_non_compete,
        )
    if draft_content:
        item["draft_content"] = draft_content
    item["review_notes"] = remove_stale_founder_obligations_notes(item.get("review_notes", []))
    item["lawyer_notes"] = remove_stale_founder_obligations_notes(item.get("lawyer_notes", []))
    item["missing_or_unclear"] = remove_stale_founder_obligations_notes(item.get("missing_or_unclear", []))


def guard_founder_obligations(
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> None:
    has_vesting = has_founder_vesting_evidence(candidates)
    has_service = has_founder_service_commitment_evidence(candidates)
    has_breach = has_founder_breach_evidence(candidates)
    has_non_compete = has_founder_non_compete_scope_evidence(candidates)
    if not (has_service or has_non_compete):
        return

    extracted_facts = extraction.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return

    if has_vesting:
        set_extracted_field(
            extracted_facts,
            "vesting",
            "股权成熟/兑现",
            "创始人/相关高管持有的受限股权分4年成熟；特定人员分别自天使轮增资交割日或全职加入并签署劳动合同日起，每满1年成熟25%；约定收购/兼并且收购方同意或完成IPO时，全部加速成熟。",
            founder_obligations_source_ids(candidates, ("受限股权", "分4年成熟")),
            "系统根据股权成熟条款压缩为KTS要点。",
        )
    if has_service:
        set_extracted_field(
            extracted_facts,
            "service_commitment",
            "持续任职/全职投入",
            "自天使轮增资交割日至IPO后一周年，相关创始人/核心人员在全职加入前应投入剩余实质性全部工作时间和精力；全职加入后应投入实质性全部工作时间和精力，均不得在公司/集团外任职、投资或提供服务；经投资人同意的研究机构任职例外，但不得实质影响其对公司的职责和经营管理。",
            founder_obligations_source_ids(candidates, ("首次公开发行后一(1)年", "实质性全部工作时间")),
            "系统根据完整全职投入承诺补足，消除候选片段截断造成的伪复核。",
        )
    if has_breach:
        set_extracted_field(
            extracted_facts,
            "breach_consequence",
            "违约后果",
            "成熟期内主动离职、不续签或因过错被解职的，受限股权无论是否成熟均须无偿或以法定最低价格转让；其他离职的未成熟部分同样适用，已成熟部分保留但放弃投票权/董事提名等管理权。",
            founder_obligations_source_ids(candidates, ("无偿或以法律允许的最低价格", "董事提名权")),
            "系统根据离职/过错后果条款压缩为KTS要点。",
        )
    if has_non_compete:
        set_extracted_field(
            extracted_facts,
            "non_compete",
            "不竞争/竞业限制",
            "限制期至离职后两年或不再持股后两年孰晚；限制投资、参与或协助竞争业务，以及招揽客户或员工。",
            founder_obligations_source_ids(candidates, ("限制期", "竞争性活动")),
            "系统根据竞业限制期和竞争性活动清单补足。",
        )
        set_extracted_field(
            extracted_facts,
            "confidentiality_ip",
            "保密/IP归属",
            "不得为非公司目的披露或使用公司商业、财务、交易、知识产权及其他商业秘密或保密信息；违反保密、竞业限制及知识产权保护协议项下义务构成过错理由。",
            founder_obligations_source_ids(candidates, ("商业秘密或保密信息",)),
            "系统根据竞业/保密清单及过错理由条款补足。",
        )

    compact = founder_obligations_compact_draft(has_vesting, has_service, has_breach, has_non_compete)
    if compact:
        extraction["draft_content"] = compact
    extraction["review_notes"] = remove_stale_founder_obligations_notes(extraction.get("review_notes", []))
    extraction["lawyer_notes"] = remove_stale_founder_obligations_notes(extraction.get("lawyer_notes", []))
    extraction["missing_or_unclear"] = remove_stale_founder_obligations_notes(extraction.get("missing_or_unclear", []))
    facts_missing = normalize_string_list(extracted_facts.get("missing_or_unclear"))
    extracted_facts["missing_or_unclear"] = remove_stale_founder_obligations_notes(facts_missing)
    facts_lawyer_notes = normalize_string_list(extracted_facts.get("lawyer_notes"))
    extracted_facts["lawyer_notes"] = remove_stale_founder_obligations_notes(facts_lawyer_notes)
    summary_points = normalize_string_list(extracted_facts.get("summary_points"))
    extracted_facts["summary_points"] = remove_stale_founder_obligations_notes(summary_points)
    refresh_founder_obligations_status(extraction)


def apply_deterministic_quality_guards(
    item: dict[str, Any],
    extraction: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    item_id = str(item.get("taxonomy_id") or item.get("id") or "")
    if item_id == "spa.transaction_arrangement":
        guard_transaction_arrangement(extraction, candidates)
    if item_id == "spa.other":
        remove_spa_other_workpaper_tone(extraction)
    if item_id == "spa.post_closing_covenants":
        guard_post_closing_covenants_summary(extraction)
    if item_id == "sha.board_composition":
        guard_board_composition(extraction, candidates)
    if item_id == "sha.board_reserved_matters":
        remove_board_reserved_workpaper_tone(extraction)
    if item_id == "sha.rofr_tag":
        guard_rofr_tag(extraction, candidates)
        guard_rofr_tag_along_terms(extraction, candidates)
    if item_id == "spa.representations_warranties":
        guard_representations_core_fields(extraction, candidates)
        guard_representations_transition_covenant(extraction, candidates)
        clean_representations_absence_tone(extraction)
    if item_id == "sha.shareholder_reserved_matters":
        guard_shareholder_reserved_matters(extraction, candidates)
    if item_id == "sha.anti_dilution":
        guard_anti_dilution_method(extraction, candidates)
        clean_anti_dilution_review_tone(extraction)
    if item_id == "sha.information_audit":
        guard_information_audit_inspection(extraction, candidates)
    if item_id == "sha.redemption":
        guard_redemption_trigger(extraction, candidates)
        guard_redemption_obligor(extraction, candidates)
        guard_redemption_price_formula(extraction, candidates)
        clean_redemption_review_tone(extraction)
    if item_id == "sha.dividend":
        guard_dividend_approval(extraction, candidates)
    if item_id == "sha.liquidation_preference":
        guard_liquidation_preference(extraction, candidates)
    if item_id == "sha.founder_obligations":
        guard_founder_obligations(extraction, candidates)
    return extraction


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


def field_absence_counts_as_handled(item: dict[str, Any], field: dict[str, Any]) -> bool:
    if bool(field.get("absence_ok")):
        return True
    return output_policy_for_item(item).get("category") == "mandatory_check_absence_output"


def build_schema_coverage(item: dict[str, Any], extracted_facts: dict[str, Any]) -> dict[str, Any]:
    fields = schema_fields(item)
    values_by_key = field_value_map(extracted_facts)
    coverage_fields: list[dict[str, Any]] = []
    required_total = 0
    required_found = 0
    required_handled = 0
    required_absent_ok = 0
    required_not_applicable = 0
    required_unclear = 0
    required_missing = 0

    for field in fields:
        key = str(field.get("key") or "").strip()
        label = str(field.get("label") or key).strip()
        required = bool(field.get("required"))
        absence_ok = field_absence_counts_as_handled(item, field)
        value = values_by_key.get(key) or values_by_key.get(f"label:{label}") or {}
        status = str(value.get("status") or "unclear").strip()
        if status not in {"found", "not_found", "unclear", "not_applicable"}:
            status = "unclear"
        if required:
            required_total += 1
            if status == "found":
                required_found += 1
                required_handled += 1
            elif status == "not_found":
                if absence_ok:
                    required_absent_ok += 1
                    required_handled += 1
                else:
                    required_missing += 1
            elif status == "not_applicable":
                required_not_applicable += 1
                required_handled += 1
            else:
                required_unclear += 1
        coverage_fields.append(
            {
                "key": key,
                "label": label,
                "required": required,
                "absence_ok": absence_ok,
                "status": status,
                "value": str(value.get("value") or "").strip(),
                "note": str(value.get("note") or "").strip(),
            }
        )

    if required_total == 0:
        status = "not_configured"
    elif required_handled == required_total:
        status = "complete"
    elif required_handled > 0:
        status = "partial"
    else:
        status = "weak"
    return {
        "status": status,
        "required_total": required_total,
        "required_found": required_found,
        "required_handled": required_handled,
        "required_absent_ok": required_absent_ok,
        "required_not_applicable": required_not_applicable,
        "required_missing": required_missing,
        "required_unclear": required_unclear,
        "fields": coverage_fields,
    }


def has_found_schema_field(extracted_facts: dict[str, Any]) -> bool:
    values = extracted_facts.get("field_values", [])
    if not isinstance(values, list):
        return False
    return any(isinstance(value, dict) and str(value.get("status") or "") == "found" for value in values)


def optional_conditional_absent(
    item: dict[str, Any],
    extraction: dict[str, Any],
) -> bool:
    return (
        optional_conditional_item(item)
        and not str(extraction.get("draft_content") or "").strip()
        and not has_found_schema_field(extraction.get("extracted_facts", {}) if isinstance(extraction.get("extracted_facts"), dict) else {})
    )


def optional_absent_schema_coverage() -> dict[str, Any]:
    return {
        "status": "not_configured",
        "required_total": 0,
        "required_found": 0,
        "required_handled": 0,
        "required_absent_ok": 0,
        "required_not_applicable": 0,
        "required_missing": 0,
        "required_unclear": 0,
        "fields": [],
    }


def schema_coverage_review_notes(coverage: dict[str, Any]) -> list[str]:
    fields = coverage.get("fields", [])
    if not isinstance(fields, list):
        return []
    missing_labels = [
        str(field.get("label") or "")
        for field in fields
        if (
            isinstance(field, dict)
            and field.get("required")
            and field.get("status") == "not_found"
            and not field.get("absence_ok")
        )
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


HARD_REVIEW_MARKERS = [
    "模型抽取失败",
    "证据不足",
    "候选证据不足",
    "未找到候选",
    "无法确认",
    "主体不一致",
    "占位符",
    "对应主体",
    "需核对原文",
    "需核对全文",
    "完整文本",
    "完整条款",
    "证据不完整",
    "证据截断",
    "未完整显示",
    "需确认本方",
    "需确认投资人董事席位",
    "本方能否",
    "版本不一致",
    "版本冲突",
    "证据冲突",
    "主体冲突",
    "待核",
]


def has_hard_review_marker(draft_content: str, review_notes: list[str]) -> bool:
    combined = str(draft_content or "") + "\n" + "\n".join(review_notes)
    return any(marker in combined for marker in HARD_REVIEW_MARKERS)


def normalize_final_status(
    status: str,
    coverage: dict[str, Any],
    draft_content: str,
    review_notes: list[str],
) -> str:
    if status == "unclear":
        return status
    coverage_status = coverage.get("status")
    if coverage_status not in {"complete", "not_configured"}:
        return "needs_review"
    if int(coverage.get("required_missing", 0) or 0) > 0:
        return "needs_review"
    if int(coverage.get("required_unclear", 0) or 0) > 0:
        return "needs_review"
    if has_hard_review_marker(draft_content, review_notes):
        return "needs_review"
    if status == "needs_review":
        return "drafted"
    return "drafted"


def residual_rights_fallback_content(item: dict[str, Any]) -> str:
    if str(item.get("taxonomy_id") or "") != "sha.other":
        return ""
    coverage = item.get("schema_coverage", {})
    if not isinstance(coverage, dict):
        return ""
    fields = coverage.get("fields", [])
    if not isinstance(fields, list):
        return ""
    found_labels: list[str] = []
    absent_labels: list[str] = []
    for field in fields:
        if not isinstance(field, dict) or not field.get("required"):
            continue
        label = str(field.get("label") or "").strip()
        if not label:
            continue
        status = str(field.get("status") or "")
        if status == "found":
            found_labels.append(label)
        elif status == "not_found":
            absent_labels.append(label)

    parts: list[str] = []
    if absent_labels:
        parts.append("未见" + "、".join(absent_labels) + "的明确约定")
    if not parts:
        return ""
    return "；".join(parts) + "。"


def ensure_required_draft_content(items: list[dict[str, Any]]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        existing = str(item.get("draft_content") or "").strip()
        uninformative_residual = (
            str(item.get("taxonomy_id") or "") == "sha.other"
            and any(marker in existing for marker in ("未见需列明", "无需列明", "未见需要列明"))
        )
        if existing and not uninformative_residual:
            continue
        fallback = residual_rights_fallback_content(item)
        if not fallback:
            continue
        item["draft_content"] = fallback
        style_polish = item.get("style_polish", {})
        if not isinstance(style_polish, dict):
            style_polish = {}
        item["style_polish"] = {
            **style_polish,
            "postprocess_fallback": "residual_rights_content",
        }


def format_decimal_amount(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    text = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return text


def parse_transaction_investor_amount_value(value: str) -> list[tuple[str, Decimal]]:
    rows: list[tuple[str, Decimal]] = []
    for part in re.split(r"[；;]\s*", value.rstrip("。")):
        text = part.strip()
        if not text:
            continue
        match = re.search(r"(?P<investor>.+?)人民币(?P<amount>[0-9][0-9,]*(?:\.\d+)?)元", text)
        if not match:
            continue
        amount = decimal_from_amount(match.group("amount"))
        if amount is None:
            continue
        rows.append((match.group("investor").strip(" ：:"), amount))
    return rows


def compact_transaction_investor_line(extracted_facts: dict[str, Any]) -> str:
    value = extracted_field_value(extracted_facts, "investors_and_amounts")
    rows = parse_transaction_investor_amount_value(value)
    if len(rows) <= 3:
        return ""
    total = sum((amount for _investor, amount in rows), Decimal("0"))
    ranked = sorted(rows, key=lambda row: row[1], reverse=True)
    top_rows = ranked[:3]
    rest_total = sum((amount for _investor, amount in ranked[3:]), Decimal("0"))
    top_text = "、".join(
        f"{investor}人民币{format_decimal_amount(amount)}元" for investor, amount in top_rows
    )
    return (
        f"投资方明细：共{len(rows)}名投资方，合计人民币{format_decimal_amount(total)}元；"
        f"主要包括{top_text}，其余{len(rows) - len(top_rows)}名合计人民币{format_decimal_amount(rest_total)}元。"
    )


def concise_transaction_valuation(value: str) -> str:
    text = value.strip().rstrip("。")
    if not text:
        return ""
    parts = [
        part.strip()
        for part in re.split(r"[；;]", text)
        if part.strip()
        and "候选证据" not in part
        and not ("未" in part and any(term in part for term in ("投前", "投后", "估值")))
    ]
    if not parts:
        return ""
    return parts[0].rstrip("。") + "。"


def concise_transaction_money(value: str) -> str:
    text = value.strip()
    match = re.search(r"人民币\s*([0-9][0-9,]*(?:\.\d+)?)\s*元", text)
    if match:
        return f"人民币{match.group(1)}元。"
    return text


def ensure_transaction_core_terms_after_polish(item: dict[str, Any]) -> None:
    if str(item.get("taxonomy_id") or "") != "spa.transaction_arrangement":
        return
    extracted_facts = item.get("extracted_facts", {})
    if not isinstance(extracted_facts, dict):
        return
    valuation = concise_transaction_valuation(extracted_field_value(extracted_facts, "valuation"))
    financing_amount = concise_transaction_money(extracted_field_value(extracted_facts, "financing_amount"))
    capital_change = extracted_field_value(extracted_facts, "capital_change")
    draft_content = str(item.get("draft_content") or "")
    draft_content = draft_content.replace("候选证据未见", "未见")
    draft_content = re.sub(r"；?候选证据[^。；;\n]*(?:。|；|;)?", "。", draft_content)
    draft_content = re.sub(
        r"本轮融资额为(?:本次增资)?投资方合计缴付(人民币[0-9][0-9,]*(?:\.\d+)?元)",
        r"本轮融资额为\1",
        draft_content,
    )
    draft_content = draft_content.replace("。本轮融资额", "；本轮融资额")
    compact_draft = re.sub(r"\s+", "", draft_content)
    missing_valuation = bool(valuation) and "估值" not in compact_draft
    financing_number = re.sub(r"\D", "", financing_amount)
    missing_financing = bool(financing_number) and financing_number not in re.sub(r"\D", "", compact_draft)
    changed = draft_content != str(item.get("draft_content") or "")

    if missing_valuation or missing_financing:
        parts: list[str] = []
        if valuation:
            parts.append("公司" + valuation.rstrip("。"))
        if financing_amount:
            amount = financing_amount.rstrip("。")
            parts.append(f"本轮融资额为{amount}")
        if capital_change and not any(term in compact_draft for term in ("注册资本", "股权结构")):
            parts.append(capital_change.rstrip("。"))
        core_line = "交易安排：" + "；".join(parts) + "。"
        draft_content = replace_or_insert_kts_line(
            draft_content,
            core_line,
            ("交易安排", "本次交易", "增资安排"),
            0,
        )
        changed = True

    lines = [line for line in draft_content.splitlines() if line.strip()]
    has_separate_capital_line = any(
        line.strip().startswith(("注册资本", "股权结构", "注册资本及")) for line in lines[1:]
    )
    if valuation and financing_amount and has_separate_capital_line:
        concise_core = f"交易安排：公司{valuation.rstrip('。')}；本轮融资额为{financing_amount.rstrip('。')}。"
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(("交易安排", "本次交易", "增资安排")) and "注册资本" in stripped:
                lines[index] = concise_core
                draft_content = "\n".join(lines)
                changed = True
                break

    compact_investor_line = compact_transaction_investor_line(extracted_facts)
    if compact_investor_line:
        lines = [line for line in draft_content.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if line.strip().startswith(("投资方明细", "投资方及金额")) and len(line) > 160:
                lines[index] = compact_investor_line
                draft_content = "\n".join(lines)
                changed = True
                break

    if changed:
        item["draft_content"] = draft_content


def refresh_final_statuses(items: list[dict[str, Any]]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        item["status"] = normalize_final_status(
            str(item.get("status") or "needs_review"),
            item.get("schema_coverage", {}) if isinstance(item.get("schema_coverage"), dict) else {},
            str(item.get("draft_content") or ""),
            normalize_string_list(item.get("review_notes")),
        )


def remove_nonblocking_workpaper_review_notes(notes: Any) -> list[str]:
    normalized = normalize_string_list(notes)
    nonblocking_prefixes = (
        "已按",
        "已剔除",
        "摘要已",
        "当前摘要已",
        "已完成",
        "已将",
    )
    return [note for note in normalized if not note.startswith(nonblocking_prefixes)]


def apply_post_polish_quality_guards(items: list[dict[str, Any]]) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("taxonomy_id") or item.get("id") or "")
        if item_id == "spa.transaction_arrangement":
            ensure_transaction_core_terms_after_polish(item)
        elif item_id == "spa.other":
            remove_spa_other_workpaper_tone(item)
            normalize_conventional_kts_labels(item)
        elif item_id == "sha.rofr_tag":
            clean_rofr_tag_workpaper_tone(item)
        elif item_id == "sha.board_reserved_matters":
            remove_board_reserved_workpaper_tone(item)
        elif item_id == "sha.anti_dilution":
            clean_anti_dilution_review_tone(item)
        elif item_id == "sha.esop":
            clean_esop_review_tone(item)
        elif item_id == "sha.redemption":
            clean_redemption_review_tone(item)
            normalize_redemption_subpoint_labels(item)
        elif item_id == "sha.founder_obligations":
            clean_founder_obligations_review_tone(item)
        item["review_notes"] = remove_nonblocking_workpaper_review_notes(item.get("review_notes", []))


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
                        "遵守kts_item.output_policy：draft_content只列明确未见的事项；已找到明确约定的事项不得写入“无...”清单。",
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
                        "output_policy": output_policy_for_item(item),
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
                        "遵守kts_item.output_policy：默认输出项必须提炼高价值要点；条件输出项只有在证据有实质内容时输出；缺失检查项不得把已找到的事项写成未见。",
                        "draft_content普通事项目标120-240字，复杂事项尽量不超过320字；超过5行时合并相近要点，不展开通知和程序细节。",
                        "文风应接近律师交易文件主要条款摘要：简洁、准确、法言法语；常规表述应压缩为KTS概括词，例如“排他期承诺”“费用承担”“Long Stop Date”。",
                    ],
                    "kts_item": {
                        "taxonomy_id": item.get("taxonomy_id", ""),
                        "label": item.get("label", ""),
                        "template_labels": item.get("template_labels", {}),
                        "group": item.get("group", ""),
                        "output_policy": output_policy_for_item(item),
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
                "文风应接近律师认可的交易文件主要条款摘要：先提炼交易效果和谈判关注点，"
                "不要把原文改写成长段落，也不要把字段抽取表直接搬进正文。"
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
                        "必须遵守kts_item.output_policy：默认输出项必须提炼高价值要点；条件输出项只写实质条款；缺失检查项只列明确未见事项。",
                        "费用、税费、违约追责费用、违约赔偿、解除救济等内容，只有在当前KTS事项明确要求时才可纳入；否则应留给对应事项。",
                        "draft_content是给律师和客户看的KTS内容列，不是字段抽取表。应优先输出2-6个高价值要点；每个要点格式为“要点：摘要”，要点名可来自字段标签，但应合并相关字段。",
                        "普通事项目标总长120-240字，复杂事项尽量不超过320字；超过5行时应合并相近要点，并删除低价值通知、定义、程序和重复细节。",
                        "每个要点一般不超过80字，复杂事项不超过120字；只保留影响交易判断的主体、金额、比例、期限、门槛、触发条件、例外和缺失提示。",
                        "低价值程序、定义、通知细节、重复适用法表述和原文铺陈只保留在extracted_facts，不写入draft_content。",
                        "除content_schema字段标记absence_ok=true，或kts_item.output_policy要求缺失提示外，status为not_found的字段不要写入draft_content，不要出现未见约定/未载明/待确认等表述。",
                        "对absence_ok=true的字段，如证据未见明确约定，应作为已检查的缺失结论处理，可在draft_content或【注：...】中简洁提示，不要写成模型漏抽。",
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
                        "output_policy": output_policy_for_item(item),
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
    extraction = apply_deterministic_quality_guards(item, extraction, candidates)
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
    if optional_conditional_absent(item, extraction):
        schema_coverage = optional_absent_schema_coverage()
        review_notes = []
        status = "drafted"
    else:
        review_notes = [
            *extraction["review_notes"],
            *schema_coverage_review_notes(schema_coverage),
        ]
        status = normalize_final_status(
            extraction["status"],
            schema_coverage,
            extraction["draft_content"],
            review_notes,
        )
    return (
        {
            "taxonomy_id": item.get("taxonomy_id", ""),
            "group": item.get("group", ""),
            "label": item.get("label", ""),
            "template_labels": item.get("template_labels", {}),
            "content_schema": item.get("content_schema", {}),
            "output_policy": output_policy_for_item(item),
            "status": status,
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
    apply_post_polish_quality_guards(items)
    ensure_required_draft_content(items)
    refresh_final_statuses(items)

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
                "required_field_handled_count": sum(
                    int(item.get("schema_coverage", {}).get("required_handled", 0))
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
