from __future__ import annotations

import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from kts_extractor import (  # noqa: E402
    apply_post_polish_quality_guards,
    apply_deterministic_quality_guards,
    build_kts_candidate_item,
    build_schema_coverage,
    candidate_context_for_extraction,
    ensure_required_draft_content,
    item_for_style_polish,
    normalize_final_status,
    schema_coverage_review_notes,
    validate_polished_content,
)
from kts_docx_exporter import export_items  # noqa: E402


def test_anti_dilution_price_reset_guard() -> None:
    extraction = {
        "draft_content": "反稀释方式：广义加权平均。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "method",
                    "label": "反稀释方式",
                    "status": "found",
                    "value": "广义加权平均",
                }
            ]
        },
        "review_notes": [],
    }
    candidates = [
        {
            "text": "调整后每单位认购价格 = 低价增资时公司每一元注册资本的认购价格"
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.anti_dilution"},
        extraction,
        candidates,
    )

    assert "价格重设/接近全棘轮" in extraction["draft_content"]
    method = extraction["extracted_facts"]["field_values"][0]
    assert method["status"] == "found"
    assert "非加权平均" in method["value"]
    assert any("反稀释公式" in note for note in extraction["review_notes"])


def test_redemption_compliance_trigger_guard() -> None:
    extraction = {
        "draft_content": "触发事项：约定发生“触发事件”时可要求回购。\n回购义务人：相关主体承担回购义务。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "trigger",
                    "label": "回购触发事项",
                    "status": "unclear",
                    "value": "触发事件",
                    "note": "需确认。",
                }
            ]
        },
        "review_notes": ["触发事项本身缺失，建议律师复核完整协议。"],
    }
    candidates = [
        {
            "text": (
                "除投资合作及经事先书面同意建立的其他业务合作关系之外，"
                "不存在代 持、利益 输送、资金 往来等利益安排。"
                "如 违反 本条，有权 要求 任意 回购义务人按照本协议第 2.3 条 履行 回购义务。"
            )
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.redemption"},
        extraction,
        candidates,
    )

    assert extraction["draft_content"].splitlines()[0].startswith("触发事项：违反业务行为道德合规/廉洁条款")
    trigger = extraction["extracted_facts"]["field_values"][0]
    assert trigger["status"] == "found"
    assert "代持、利益输送、资金往来" in trigger["value"]
    assert all("触发事项本身缺失" not in note for note in extraction["review_notes"])
    assert all("系统校验" not in note for note in extraction["review_notes"])


def test_redemption_guard_does_not_duplicate_existing_trigger_line() -> None:
    extraction = {
        "draft_content": (
            "触发事项：违反业务行为道德合规/廉洁条款，包括代持、利益输送、资金往来，并触发第2.3条回购义务。\n"
            "触发及义务人：相关主体违反廉洁条款时，投资人可要求其回购所持股权。\n"
            "回购价格：按净资产计算。"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "trigger",
                    "label": "回购触发事项",
                    "status": "found",
                    "value": "违反廉洁条款",
                }
            ]
        },
        "review_notes": [],
    }
    candidates = [
        {
            "text": (
                "不存在代持、利益输送、资金往来等利益安排。"
                "如违反本条，有权要求任意回购义务人按照本协议第2.3条履行回购义务。"
            )
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.redemption"},
        extraction,
        candidates,
    )

    lines = extraction["draft_content"].splitlines()
    assert len([line for line in lines if line.startswith("触发")]) == 1
    assert lines[0].startswith("触发及义务人：")


def test_absence_ok_required_field_counts_as_handled() -> None:
    item = {
        "taxonomy_id": "sha.preemptive_right",
        "content_schema": {
            "fields": [
                {
                    "key": "pro_rata_right",
                    "label": "按持股比例优先认购",
                    "required": True,
                },
                {
                    "key": "secondary_right",
                    "label": "二次认购权",
                    "required": True,
                    "absence_ok": True,
                },
            ]
        },
    }
    extracted_facts = {
        "field_values": [
            {
                "key": "pro_rata_right",
                "label": "按持股比例优先认购",
                "status": "found",
                "value": "投资人可按持股比例优先认购。",
            },
            {
                "key": "secondary_right",
                "label": "二次认购权",
                "status": "not_found",
                "value": "未见明确约定。",
            },
        ]
    }

    coverage = build_schema_coverage(item, extracted_facts)
    notes = schema_coverage_review_notes(coverage)

    assert coverage["status"] == "complete"
    assert coverage["required_found"] == 1
    assert coverage["required_handled"] == 2
    assert coverage["required_absent_ok"] == 1
    assert not notes


def test_representations_guard_fills_transition_covenant() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "签署及履约能力：各方具备签署及履行交易文件的能力和授权。\n"
            "信息披露：公司方提供资料真实、准确、完整。"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "transition_covenants",
                    "label": "过渡期限制事项",
                    "status": "not_found",
                    "value": "",
                    "note": "未见明确约定。",
                }
            ]
        },
        "review_notes": ["以下关键字段未见明确约定或未被模型提取：过渡期限制事项。"],
    }
    candidates = [
        {
            "candidate_id": "spa.representations_warranties-C09",
            "text": (
                "4.13过渡期保证。过渡期内，公司应以与过去惯例相符的方式正常地开展业务经营，"
                "且除了为完成本次增资交易所进行的外，未经投资方事先书面同意，不得对公司进行约定限制事项。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.representations_warranties"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "正常开展业务" in field["value"]
    assert "过渡期限制：" in extraction["draft_content"]
    assert all("过渡期限制事项" not in note for note in extraction["review_notes"])
    assert extraction["status"] == "drafted"


def test_redemption_price_formula_guard_fills_both_formulas() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "触发及义务人：违反廉洁条款时，投资人可要求回购。\n"
            "价格及付款：回购价款按两种价格孰高确定；现有证据仅见其中一项为最近一期经审计净资产×要求回购股权比例。\n"
            "【注：价格公式未完整显示，需核对第2.3条全文。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "price_formula",
                    "label": "回购价格",
                    "status": "unclear",
                    "value": "仅见净资产公式。",
                }
            ]
        },
        "review_notes": ["价格公式未完整显示，需核对第2.3条全文。"],
    }
    candidates = [
        {
            "text": (
                "股权回购价款应按以下两种价格较高者确定："
                "1股权回购价款=回购股权对应的投资总额×(1+【8】%×n)-已取得的股息或分红；"
                "2股权回购价款=股权回购协议签订日前最近一期经审计的公司净资产×投资人要求回购的股权比例。"
            )
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.redemption"},
        extraction,
        candidates,
    )

    price = extraction["extracted_facts"]["field_values"][0]
    assert price["status"] == "found"
    assert "投资总额" in price["value"]
    assert "净资产" in price["value"]
    assert "仅见其中一项" not in extraction["draft_content"]
    assert "投资总额×(1+8%×投资年数)" in extraction["draft_content"]
    assert "价格公式未完整显示" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_dividend_guard_fills_special_approval_threshold() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "批准机制：公司原则上不得分红，除非经股东会批准。\n"
            "分配比例：税后可分配利润按实缴出资比例分配。\n"
            "【注：分红批准的具体表决门槛需结合完整条款确认。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "approval",
                    "label": "分红批准机制",
                    "status": "found",
                    "value": "公司原则上不得分红，除非经股东会批准。",
                }
            ]
        },
        "review_notes": ["建议律师核对股东会批准分红的完整表决门槛。"],
    }
    candidates = [
        {
            "text": (
                "1.1.7 公司的以下事项应当包括特定投资人的同意方可通过："
                "(5) 批准分红或任何利润分配。"
            )
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.dividend"},
        extraction,
        candidates,
    )

    approval = extraction["extracted_facts"]["field_values"][0]
    assert "1.1.7" in approval["value"]
    assert "特定投资人同意" in approval["value"]
    assert "1.1.7项下事项" in extraction["draft_content"]
    assert "表决门槛" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_information_audit_guard_fills_inspection_right() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "信息权：公司应向信息权人提供年度、季度、月度财务报表及预算。\n"
            "【注：未见检查权具体安排、独立审计权及费用承担条款。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "inspection",
                    "label": "检查权",
                    "status": "not_found",
                    "value": "",
                    "note": "当前证据窗口未提供检查权实质条款。",
                }
            ]
        },
        "review_notes": ["候选证据未展示检查权、独立审计权及费用承担的实质条款，建议律师核对完整第7条。"],
    }
    candidates = [
        {
            "candidate_id": "sha.information_audit-C02",
            "text": (
                "7.3 自本协议签署之日起，信息权人有权在正常工作时间内且在不影响公司正常经营的前提下，"
                "对公司以及其子公司的资产、财务账簿和其它经营记录进行查看核对，"
                "并可就公司经营方面事宜与董事、监事、高级管理人员或专业服务机构沟通。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.information_audit"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "查看核对" in field["value"]
    assert "财务账簿" in extraction["draft_content"]
    assert "未见检查权" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_redemption_guard_fills_obligor_definition() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "触发事项：发生回购事件时回购权人可要求回购。\n"
            "回购价格：按投资成本加年单利与公允价值孰高确定。\n"
            "【注：候选证据未显示回购义务人的具体主体，需结合前文确认。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "obligor",
                    "label": "回购义务人",
                    "status": "unclear",
                    "value": "回购义务人主体不明确。",
                }
            ]
        },
        "review_notes": ["需律师复核回购义务人定义及完整回购事件清单。"],
    }
    candidates = [
        {
            "candidate_id": "sha.redemption-C02",
            "text": (
                "9.2 当任一回购事件发生后，任一投资人（“回购权人”）有权向公司及/或创始人"
                "（仅为本条之目的，就持股平台而言，不包含其他员工间接持有的公司股权）（“回购义务人”）"
                "要求回购。9.9 公司未能足额支付的，创始人应承担连带回购责任。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.redemption"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "公司及/或相关创始人/持股平台" in field["value"]
    assert "连带回购责任" in extraction["draft_content"]
    assert "候选证据未显示回购义务人" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_complete_soft_review_status_normalizes_to_drafted() -> None:
    coverage = {
        "status": "complete",
        "required_missing": 0,
        "required_unclear": 0,
    }

    status = normalize_final_status(
        "needs_review",
        coverage,
        "【注：未见二次认购权安排，建议确认是否需补充。】",
        ["因未见二次认购权，已按缺失检查项提示律师确认。"],
    )

    assert status == "drafted"


def test_complete_hard_review_status_stays_needs_review() -> None:
    coverage = {
        "status": "complete",
        "required_missing": 0,
        "required_unclear": 0,
    }

    status = normalize_final_status(
        "needs_review",
        coverage,
        "【注：优先购买权人占位符需核对。】",
        [],
    )

    assert status == "needs_review"


def test_drafted_hard_review_status_upgrades_to_needs_review() -> None:
    coverage = {
        "status": "complete",
        "required_missing": 0,
        "required_unclear": 0,
    }

    status = normalize_final_status(
        "drafted",
        coverage,
        "通过机制：须取得占位符所指投资人同意。",
        [],
    )

    assert status == "needs_review"


def test_pending_check_marker_upgrades_to_needs_review() -> None:
    coverage = {
        "status": "complete",
        "required_missing": 0,
        "required_unclear": 0,
    }

    status = normalize_final_status(
        "drafted",
        coverage,
        "交割安排：付款后完成交割。【待核：工商变更作为付款前条件的操作路径。】",
        [],
    )

    assert status == "needs_review"


def test_not_configured_schema_does_not_force_needs_review() -> None:
    coverage = {
        "status": "not_configured",
        "required_missing": 0,
        "required_unclear": 0,
    }

    status = normalize_final_status(
        "drafted",
        coverage,
        "保密与披露：按协议约定处理。",
        [],
    )

    assert status == "drafted"


def test_residual_rights_fallback_prevents_empty_sha_other_content() -> None:
    items = [
        {
            "taxonomy_id": "sha.other",
            "draft_content": "",
            "schema_coverage": {
                "fields": [
                    {"label": "常规回购权", "required": True, "status": "found"},
                    {"label": "领售权", "required": True, "status": "found"},
                    {"label": "最惠国待遇", "required": True, "status": "not_found", "absence_ok": True},
                ]
            },
        }
    ]

    ensure_required_draft_content(items)

    assert items[0]["draft_content"] == "未见最惠国待遇的明确约定。"
    assert items[0]["style_polish"]["postprocess_fallback"] == "residual_rights_content"


def test_docx_export_skips_empty_conditional_items_only() -> None:
    record = {
        "items": [
            {
                "taxonomy_id": "spa.compliance",
                "group": "SPA",
                "label": "道德合规特别约定",
                "draft_content": "",
                "status": "drafted",
                "output_policy": {"category": "conditional_output"},
            },
            {
                "taxonomy_id": "spa.transaction_arrangement",
                "group": "SPA",
                "label": "本次交易安排",
                "draft_content": "",
                "status": "drafted",
                "output_policy": {"category": "mandatory_check_default_output"},
            },
        ]
    }

    rows = export_items(record)

    assert [row["label"] for row in rows] == ["本次交易安排"]


def test_spa_other_workpaper_tone_is_cleaned() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "转让：投资人可随股权转让一并转让本协议项下权利义务，"
            "但受让方范围在现有证据中未完整显示。【注：未见适用法律条款；9.7转让条款需核对全文。】"
        ),
        "extracted_facts": {"field_values": []},
        "review_notes": [
            "draft_content仅保留具有实质影响的剩余条款。",
            "需律师复核适用法律及9.7转让和继承条款全文。",
        ],
    }

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.other"},
        extraction,
        [],
    )

    assert "现有证据" not in extraction["draft_content"]
    assert "需核对全文" not in extraction["draft_content"]
    assert "【注：未见适用法律条款。】" in extraction["draft_content"]
    assert all("draft_content" not in note for note in extraction["review_notes"])


def test_post_closing_covenants_guard_compacts_overlong_summary() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": (
            "增资款用途：限用于业务拓展、研发、生产、资本性支出及主营业务；偿债需股东会全票同意，对外投资、委托贷款及证券期货交易需[公司或组织_BH]同意。\n"
            "实缴承诺：[公司或组织_AI]、[公司或组织_AW]、[公司或组织_AL]应于第一次交割日后三年内完成实缴；[公司或组织_BF]应于2029年12月31日前完成实缴。\n"
            "竞业及业务优先级：核心人员承担竞业限制；[公司或组织_AO]需确保公司为[公司或组织_BD]开展主营或相似业务的唯一实体，并在约定期间为最高优先级项目。\n"
            "团队及持续任职：相关方承诺知识产权权属/使用合法，创始股东及核心人员任职、持股不违反第三方协议；创始股东[公司或组织_AZ]、[公司或组织_AN]承诺至本轮交割后八年或合格上市后一年孰早期间不主动离职。\n"
            "【注：未见知识产权转移、业务许可/备案里程碑安排。】"
        ),
        "extracted_facts": {
            "field_values": [
                {"key": "use_of_proceeds", "label": "增资款用途限制", "status": "found", "value": "用途限制及需[公司或组织_BH]同意。"},
                {"key": "capital_contribution", "label": "历史/现有股东实缴承诺", "status": "found", "value": "三年内实缴；[公司或组织_BF]于2029年12月31日前实缴。"},
                {"key": "non_compete_and_priority", "label": "竞业限制/业务唯一性", "status": "found", "value": "竞业限制及业务唯一性。"},
                {"key": "service_and_team", "label": "顾问/保密/IP/团队安排", "status": "found", "value": "知识产权权属、保密/IP/竞业安排。"},
                {"key": "continued_service", "label": "创始团队持续任职", "status": "found", "value": "八年或合格上市后一周年孰早前不主动离职。"},
                {"key": "ip_transfer", "label": "知识产权转移", "status": "not_found", "value": ""},
                {"key": "regulatory_milestones", "label": "业务许可/备案里程碑", "status": "not_found", "value": ""},
            ]
        },
        "review_notes": ["摘要仅基于候选证据C01及C05。", "未见业务许可、备案、卫星或发射相关交割后里程碑承诺。"],
        "lawyer_notes": ["第一次交割日具体日期未在候选证据中体现。"],
    }

    original_length = len(extraction["draft_content"])
    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.post_closing_covenants"},
        extraction,
        [],
    )

    assert len(extraction["draft_content"]) < original_length
    assert "2029年12月31日" in extraction["draft_content"]
    assert "[公司或组织_BH]同意" in extraction["draft_content"]
    assert "业务许可/备案里程碑" in extraction["draft_content"]
    assert "候选证据" not in "\n".join(extraction["review_notes"])


def test_style_polish_payload_includes_fields_and_review_context() -> None:
    item = {
        "taxonomy_id": "spa.representations_warranties",
        "group": "SPA",
        "label": "陈述及保证",
        "status": "needs_review",
        "content_schema": {"drafting_guidance": "仅写陈述保证边界内事项。"},
        "extracted_facts": {
            "field_values": [
                {
                    "key": "transition_covenants",
                    "label": "过渡期限制事项",
                    "status": "found",
                    "value": "过渡期内公司正常经营，未经投资方事先书面同意不得实施约定限制事项。",
                }
            ]
        },
        "review_notes": ["需核对4.13完整限制清单。"],
        "missing_or_unclear": ["常规陈述保证是否需结合LDD补充。"],
        "draft_content": "过渡期限制：过渡期内公司正常经营。",
    }

    payload = item_for_style_polish(item)

    assert payload["status"] == "needs_review"
    assert payload["field_values"][0]["key"] == "transition_covenants"
    assert "4.13" in payload["review_notes"][0]
    assert payload["missing_or_unclear"]


def test_style_polish_validation_allows_removing_workpaper_note() -> None:
    original = (
        "过渡期限制：过渡期内公司应按过往惯例正常经营。\n"
        "【注：过渡期限制清单可能未完整展示，建议核对原文。】"
    )
    polished = "过渡期限制：过渡期内公司应按过往惯例正常经营。"

    accepted, reason = validate_polished_content(original, polished)

    assert accepted, reason


def test_candidate_context_centers_on_source_quote() -> None:
    prefix = "前文背景。" * 1000
    anchor = (
        "3.4.5 如果发生清算事件，投资方所得不超过清算优先款，"
        "则十年内新项目投资时差额视为其投资额，并通过零对价转让或增发取得权益。"
    )
    suffix = "后续落实机制仍在同一条款内。"
    candidate = {
        "source_quote": "3.4.5 如果发生清算事件，投资方所得不超过清算优先款",
        "text": prefix + anchor + suffix,
    }

    context = candidate_context_for_extraction(candidate)

    assert len(context) < len(candidate["text"])
    assert "3.4.5 如果发生清算事件" in context
    assert "后续落实机制仍在同一条款内" in context
    assert context.startswith("...")


def _source_block(
    block_id: str,
    order: int,
    text: str,
    kind: str = "paragraph",
    table_index: int | None = None,
    row_index: int | None = None,
) -> dict:
    source = {"paragraph_index": order} if kind == "paragraph" else {
        "table_index": table_index,
        "row_index": row_index,
        "cells": text.split(" | "),
    }
    return {
        "block_id": block_id,
        "doc_id": "D01",
        "file_name": "增资协议.docx",
        "document_role": {"code": "spa", "label": "增资协议（SPA）"},
        "document_type": {"code": "capital_increase_agreement", "label": "增资协议"},
        "kind": kind,
        "order": order,
        "text": text,
        "normalized_text": text,
        "source": source,
        "source_locator": text[:80],
    }


def test_transaction_arrangement_adds_header_and_cap_table_candidates() -> None:
    raw_blocks = [
        _source_block("D01-B0001", 1, "《A轮增资协议》由以下各方共同订立。"),
        _source_block("D01-B0002", 2, "甲方(合称为“投资方”):"),
        _source_block("D01-B0003", 3, "乙方(合称为“现有股东”):"),
        _source_block("D01-B0004", 4, "丙方(公司):目标公司"),
        _source_block("D01-B0005", 5, "丁方:张三(创始股东)"),
        _source_block("D01-B0006", 6, "上述各方合称“协议各方”。"),
        _source_block("D01-B0007", 7, "鉴于:"),
        _source_block("D01-B0008", 8, "截至本协议签署日，公司注册资本为7,950,852.25元，股权结构为:"),
        _source_block("D01-B0009", 9, "序号 | 股东名称 | 认缴出资额 | 股权比例", "table_row", 1, 1),
        _source_block("D01-B0010", 10, "1 | [公司或组织_A] | 2,400,000 | 30.1854%", "table_row", 1, 2),
        _source_block("D01-B0011", 11, "2 | [公司或组织_B] | 2,000,000 | 25.1545%", "table_row", 1, 3),
        _source_block("D01-B0012", 12, "合计 | 7,950,852.25 | 100%", "table_row", 1, 4),
        _source_block("D01-B0013", 13, "本次增资完成后，公司股权结构如下:"),
        _source_block("D01-B0014", 14, "序号 | 股东名称 | 认缴出资额 | 股权比例", "table_row", 2, 1),
        _source_block("D01-B0015", 15, "1 | [公司或组织_A] | 2,400,000 | 25.80%", "table_row", 2, 2),
        _source_block("D01-B0016", 16, "合计 | 9,302,497.12 | 100%", "table_row", 2, 3),
    ]
    source_index = {
        "documents": [
            {
                "doc_id": "D01",
                "file_name": "增资协议.docx",
                "document_type": {"code": "capital_increase_agreement", "label": "增资协议"},
                "document_role": {"code": "spa", "label": "增资协议（SPA）"},
                "raw_blocks": raw_blocks,
                "search_shards": [],
            }
        ]
    }
    item = {
        "id": "spa.transaction_arrangement",
        "group": "SPA",
        "label": "本次交易安排",
        "document_types": ["capital_increase_agreement"],
    }

    record = build_kts_candidate_item(item, source_index)
    candidate_ids = [candidate["candidate_id"] for candidate in record["candidates"]]

    assert "spa.transaction_arrangement-STRUCT-PARTIES" in candidate_ids
    assert "spa.transaction_arrangement-STRUCT-PRE-CAP" in candidate_ids
    assert any(candidate_id.startswith("spa.transaction_arrangement-STRUCT-CAP") for candidate_id in candidate_ids)
    assert record["candidates"][0]["retrieval_channels"] == ["structural_header"]
    assert "现有股东" in record["candidates"][0]["text"]


def test_transaction_arrangement_guard_fills_signing_parties_and_cap_table() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "交易安排：公司投前估值10亿元，本轮融资额170,000,000元。\n"
            "【注：签署方、Cap Table未见明确约定，需律师核对。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "signing_parties",
                    "label": "签署方",
                    "status": "not_found",
                    "value": "未见明确约定。",
                },
                {
                    "key": "cap_table",
                    "label": "现有股东结构/Cap Table",
                    "status": "not_found",
                    "value": "未见明确约定。",
                },
            ]
        },
        "review_notes": ["签署方和Cap Table缺失，建议律师复核。"],
    }
    candidates = [
        {
            "candidate_id": "spa.transaction_arrangement-STRUCT-PARTIES",
            "text": (
                "《A轮增资协议》由以下各方共同订立。\n"
                "甲方(合称为“投资方”):\n"
                "乙方(合称为“现有股东”):\n"
                "丙方(公司):目标公司\n"
                "丁方:张三(创始股东)\n"
                "上述各方合称“协议各方”。"
            ),
        },
        {
            "candidate_id": "spa.transaction_arrangement-STRUCT-PRE-CAP",
            "text": (
                "截至本协议签署日，公司注册资本为7,950,852.25元，股权结构为:\n"
                "序号 | 股东名称 | 认缴出资额 | 股权比例\n"
                "1 | [公司或组织_A] | 2,400,000 | 30.1854%\n"
                "2 | [公司或组织_B] | 2,000,000 | 25.1545%\n"
                "合计 | 7,950,852.25 | 100%\n"
                "本次增资完成后，公司注册资本增加至9,302,497.12元。"
            ),
        },
        {
            "candidate_id": "spa.transaction_arrangement-STRUCT-CAP-02",
            "text": (
                "本次增资完成后，公司股权结构如下:\n"
                "序号 | 股东名称 | 认缴出资额 | 股权比例\n"
                "1 | [公司或组织_A] | 2,400,000 | 25.80%\n"
                "合计 | 9,302,497.12 | 100%"
            ),
        },
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.transaction_arrangement"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["signing_parties"]["status"] == "found"
    assert fields["cap_table"]["status"] == "found"
    assert "签署方：" in extraction["draft_content"]
    assert "股权结构：" in extraction["draft_content"]
    assert fields["cap_table"]["value"].count("[公司或组织_A]") == 1
    assert "未见明确约定" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_transaction_arrangement_guard_fills_complete_investor_amounts() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "交易安排：本次增资投前估值人民币4.5亿元，投资方合计缴付人民币172,019,700元。\n"
            "签署方及投资方：协议由投资方、现有股东、公司及创始股东等共同签署；已见部分投资方金额，但完整投资方清单仍需确认。"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "investors_and_amounts",
                    "label": "投资方及投资金额",
                    "status": "unclear",
                    "value": "已见部分投资方。",
                }
            ]
        },
        "review_notes": ["以下关键字段需要律师确认：投资方及投资金额。"],
    }
    candidates = [
        {
            "candidate_id": "spa.transaction_arrangement-C01",
            "text": (
                "(1) 各方同意，投资方合计向公司缴付人民币172,019,700元以认购新增注册资本，其中:\n"
                "(i) 投资人A向公司缴付人民币28,230,000元;\n"
                "(ii) 投资人B向公司缴付人民币30,870,000元;\n"
                "(iii) 投资人C向公司缴付人民币23,000,000元;\n"
                "(iv) 投资人D向公司缴付人民币12,281,076.92元;\n"
                "(v) 投资人E向公司缴付人民币7,718,923.08元;\n"
                "(vi) 投资人F向公司缴付人民币20,000,000元;\n"
                "(vii) 投资人G向公司缴付人民币20,000,000元;\n"
                "(viii) 投资人H向公司缴付人民币17,200,000元;\n"
                "(ix) 投资人I向公司缴付人民币5,700,000元;\n"
                "(x) 投资人J向公司缴付人民币7,019,700元。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.transaction_arrangement"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "投资人J人民币7,019,700元" in field["value"]
    assert "其余投资方包括投资人G、投资人H、投资人I、投资人J" in extraction["draft_content"]
    assert "完整投资方清单" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_transaction_arrangement_guard_fills_bracketed_investor_amounts() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "交易安排：投资方拟以货币方式增资。\n"
            "【待核：未见本次增资投前/投后估值、整体融资额、全部投资方及各自投资金额。】"
        ),
        "extracted_facts": {
            "field_values": [
                {"key": "valuation", "label": "投前/投后估值", "status": "not_found", "value": ""},
                {"key": "financing_amount", "label": "整体融资额", "status": "not_found", "value": ""},
                {"key": "investors_and_amounts", "label": "投资方及投资金额", "status": "unclear", "value": "仅见部分投资方。"},
            ]
        },
        "review_notes": [
            "以下关键字段未见明确约定或未被模型提取：投前/投后估值、整体融资额。",
            "以下关键字段需要律师确认：投资方及投资金额。",
        ],
    }
    candidates = [
        {
            "candidate_id": "spa.transaction_arrangement-C01",
            "text": (
                "1.1.1各方确认，公司投前估值为10亿元人民币。\n"
                "1.1.2 本次增资中，公司注册资本将由【7,950,852.25】元人民币增加至【9,302,497.12】元人民币，"
                "即新增【1,351,644.87】元人民币的注册资本；[公司或组织_BH]将以【170,000,000】元人民币的增资价款认购公司全部新增注册资本，其中:\n"
                "[公司或组织_AR]投资【50,000,000】元认购【397,542.61】元人民币的新增注册资本;\n"
                "[公司或组织_AH]投资【25,000,000】元认购【198,771.31】元人民币的新增注册资本;\n"
                "[公司或组织_BA]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本;\n"
                "[公司或组织_AX]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本;\n"
                "[公司或组织_BB]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本。\n"
                "[公司或组织_AY]投资【5,000,000】元认购【39,754.26】元人民币的新增注册资本;\n"
                "[公司或组织_AS]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本;\n"
                "[公司或组织_AK]投资【30,000,000】元认购【238,525.57】元人民币的新增注册资本;\n"
                "[公司或组织_AT]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本。\n"
                "[公司或组织_AQ]投资【10,000,000】元认购【79,508.52】元人民币的新增注册资本。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.transaction_arrangement"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["valuation"]["status"] == "found"
    assert "投前估值为10亿元" in fields["valuation"]["value"]
    assert fields["financing_amount"]["value"] == "人民币170,000,000元。"
    assert fields["investors_and_amounts"]["status"] == "found"
    assert "[公司或组织_AQ]人民币10,000,000元" in fields["investors_and_amounts"]["value"]
    assert "投前估值为10亿元" in extraction["draft_content"]
    assert "人民币170,000,000元增资价款" in extraction["draft_content"]
    assert "其余投资方包括[公司或组织_AY]、[公司或组织_AS]、[公司或组织_AK]、[公司或组织_AT]、[公司或组织_AQ]" in extraction["draft_content"]
    assert "待核" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_rofr_tag_adds_sha_definition_candidate() -> None:
    raw_blocks = [
        _source_block("D01-B0001", 1, "甲方、乙方一、乙方二、乙方三合称“AP”或“AK”。"),
        _source_block("D01-B0002", 2, "3.3 优先购买权与共同出售权"),
    ]
    source_index = {
        "documents": [
            {
                "doc_id": "D01",
                "file_name": "股东协议.docx",
                "document_type": {"code": "shareholders_agreement", "label": "股东协议"},
                "document_role": {"code": "sha", "label": "股东协议（SHA）"},
                "raw_blocks": raw_blocks,
                "search_shards": [],
            }
        ]
    }
    item = {
        "id": "sha.rofr_tag",
        "group": "SHA",
        "label": "优先购买权&共同出售权",
        "document_types": ["shareholders_agreement"],
    }

    record = build_kts_candidate_item(item, source_index)

    assert record["candidate_count"] == 1
    assert record["candidates"][0]["candidate_id"] == "sha.rofr_tag-STRUCT-DEFINITIONS"
    assert record["candidates"][0]["retrieval_channels"] == ["structural_definitions"]


def test_board_composition_guard_removes_client_identity_blocker() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "董事会构成：交易完成后董事会设5席，组织_W、组织_Z、组织_N各推选1名，组织_F推选2名。\n"
            "【注：需确认本方对应主体；未见明确观察员委派权。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "investor_board_right",
                    "label": "本方董事席位/观察员",
                    "status": "unclear",
                    "value": "需确认本方对应主体。",
                }
            ]
        },
        "review_notes": ["需律师确认本方是否对应组织_W、组织_Z或组织_AK，以及是否需要补充观察员权利。"],
    }
    candidates = [
        {
            "candidate_id": "sha.board_composition-C01",
            "text": (
                "本次交易完成后，董事会组成人数为五(5)名，组织_W、组织_Z、组织_N各推选一(1)名，"
                "组织_F推选两(2)名，由股东会选举产生。组织_W和组织_Z委派的董事合称为组织_AK董事。"
                "组织_F提名的董事担任董事长。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.board_composition"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "组织_W、组织_Z各推选1名董事" in field["value"]
    assert "需确认本方" not in extraction["draft_content"]
    assert "未见明确观察员委派权" in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_board_reserved_guard_removes_cross_item_seat_blocker() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "通过机制：董事会事项一般须二分之一以上董事通过；保护性事项还须任一名投资人董事同意。\n"
            "【待核：投资人董事席位及在任情况未见明确约定。】"
        ),
        "extracted_facts": {"field_values": []},
        "review_notes": ["建议律师确认“投资人董事”的定义、席位安排及是否已构成可实际行使的一票同意权。"],
    }

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.board_reserved_matters"},
        extraction,
        [],
    )

    assert "投资人董事同意" in extraction["draft_content"]
    assert "待核" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_anti_dilution_guard_converts_exception_check_to_note() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "反稀释方式：采用价格重设/接近全棘轮机制。\n"
            "【待核：第3.5.4第(3)项是否确为反稀释例外。】"
        ),
        "extracted_facts": {"field_values": []},
        "review_notes": ["建议律师重点复核第3.5.4第(3)项是否应列入反稀释例外。"],
    }

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.anti_dilution"},
        extraction,
        [],
    )

    assert "待核" not in extraction["draft_content"]
    assert "【注：第3.5.4第(3)项作为反稀释例外的口径可结合协议版本确认。】" in extraction["draft_content"]


def test_post_polish_guards_remove_soft_hard_markers() -> None:
    items = [
        {
            "taxonomy_id": "spa.transaction_arrangement",
            "draft_content": (
                "交易安排：投资方拟以货币方式增资。\n"
                "注册资本及结构：增资后注册资本为人民币9,302,497.12元。\n"
                "投资方明细：[公司或组织_A]：人民币50,000,000元；[公司或组织_B]：人民币25,000,000元；[公司或组织_C]：人民币10,000,000元；[公司或组织_D]：人民币5,000,000元；[公司或组织_E]：人民币4,000,000元；[公司或组织_F]：人民币3,000,000元。\n"
                "【注：候选证据未见ESOP来源安排。】"
            ),
            "extracted_facts": {
                "field_values": [
                    {"key": "valuation", "label": "投前/投后估值", "status": "found", "value": "投前估值为10亿元；候选证据未明确列示投后估值。"},
                    {"key": "financing_amount", "label": "整体融资额", "status": "found", "value": "本次增资投资方合计缴付人民币170,000,000元。"},
                    {"key": "capital_change", "label": "注册资本变化", "status": "found", "value": "注册资本由人民币7,950,852.25元增加至人民币9,302,497.12元。"},
                    {
                        "key": "investors_and_amounts",
                        "label": "投资方及投资金额",
                        "status": "found",
                        "value": "[公司或组织_A]人民币50,000,000元；[公司或组织_B]人民币25,000,000元；[公司或组织_C]人民币10,000,000元；[公司或组织_D]人民币5,000,000元；[公司或组织_E]人民币4,000,000元；[公司或组织_F]人民币3,000,000元。",
                    },
                ]
            },
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.esop",
            "draft_content": "审批要求：按协议约定批准。【待核：两项10%额度是否累计适用、审批机构占位符所指主体。】",
            "review_notes": ["两项10%额度可能导致较高稀释，建议律师重点复核。"],
        },
        {
            "taxonomy_id": "sha.redemption",
            "draft_content": (
                "触发事项：违反廉洁条款时可要求回购。\n"
                "义务人及价格：回购义务人为公司及/或创始人；价格按投资成本加收益与公允价值孰高确定。\n"
                "行使及付款：回购通知后60日内付款。\n"
                "逾期及顺位：逾期按每日万分之三支付违约金。【待核：第4.0.7条10%违约金与逾期违约金关系。】"
            ),
            "review_notes": ["需律师复核第4.0.7条10%违约金是否应作为并行救济强调。"],
        },
        {
            "taxonomy_id": "sha.rofr_tag",
            "draft_content": "共同出售权：共售比例按公式计算。【注：共同出售权条款未完整显示，暂无法确认共售权人、共售比例；未见控制权变更全额共售安排。】",
            "review_notes": [],
        },
        {
            "taxonomy_id": "spa.other",
            "draft_content": "排他安排：签署日至交割日，公司未经投资方同意不得与其他投资人签署融资文件。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    combined = "\n".join(str(item.get("draft_content") or "") for item in items)
    assert "待核" not in combined
    assert "占位符" not in combined
    assert "未完整显示" not in combined
    assert "投前估值为10亿元" in combined
    assert "人民币170,000,000元" in combined
    assert "交易安排：公司投前估值为10亿元；本轮融资额为人民币170,000,000元。" in combined
    assert "共6名投资方，合计人民币97,000,000元" in combined
    assert "主要包括[公司或组织_A]人民币50,000,000元、[公司或组织_B]人民币25,000,000元、[公司或组织_C]人民币10,000,000元" in combined
    assert "候选证据" not in combined
    assert "排他期承诺：签署日至交割日" in combined
    assert "排他安排：" not in combined
    assert "【注：两项10%额度是否累计适用、审批机构口径可结合协议定义确认。】" in combined
    assert "【注：第4.0.7条10%违约金可能与逾期违约金并行适用。】" in combined
    assert "回购触发事项：违反廉洁条款时可要求回购。" in combined
    assert "回购义务人：公司及/或创始人。" in combined
    assert "回购价格：按投资成本加收益与公允价值孰高确定。" in combined
    assert "行使期限及付款：回购通知后60日内付款。" in combined
    assert "逾期责任及顺位：逾期按每日万分之三支付违约金。" in combined
    assert "【注：未见控制权变更全额共售安排。】" in combined


def test_rofr_tag_guard_resolves_ap_ak_alias() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "优先购买权：合格上市前，转股方拟向第三方转让拟售股权时，权利人可在同等条件下优先购买。\n"
            "【注：优先购买权人占位表述不一致；未见控制权变更时全额共售安排。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "rofr_holder",
                    "label": "优先购买权人",
                    "status": "unclear",
                    "value": "AP/AK主体表述不一致。",
                }
            ]
        },
        "review_notes": ["因优先购买权人主体表述不一致，建议律师核对底稿或定义表后确认。"],
    }
    candidates = [
        {
            "candidate_id": "sha.rofr_tag-STRUCT-DEFINITIONS",
            "text": "甲方、乙方一、乙方二、乙方三合称“[[公司或组织_AI]或组织_AP]”或“[[公司或组织_AI]或组织_AK]”。",
        },
        {
            "candidate_id": "sha.rofr_tag-C01",
            "text": (
                "3.3.1 转股方应向公司和[[公司或组织_AI]或组织_AK]发出转让通知，"
                "[[公司或组织_AI]或组织_AP]有权在同等条件下购买拟售股权。"
            ),
        },
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.rofr_tag"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "AP或AK" in field["value"]
    assert "定义为甲方及乙方一至三的投资人" in extraction["draft_content"]
    assert "占位表述不一致" not in extraction["draft_content"]
    assert "未见控制权变更" in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_rofr_tag_guard_fills_tag_along_terms() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": "共同出售权：协议设有共同出售权，但未明确显示共售权人范围及共售比例。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "tag_holder",
                    "label": "共同出售权人",
                    "status": "unclear",
                    "value": "共同出售权人不明确。",
                },
                {
                    "key": "tag_ratio",
                    "label": "共同出售比例",
                    "status": "not_found",
                    "value": "未见共同出售比例。",
                },
            ]
        },
        "review_notes": ["共同出售权条款证据不完整，建议补充3.3.5完整文本后复核。"],
    }
    candidates = [
        {
            "candidate_id": "sha.rofr_tag-C02",
            "text": (
                "3.3.5 如任何投资人决定不行使或放弃第3.3条行使优先购买权，"
                "则该投资人有权在购买回复期届满前发出共售通知，称为共售股东，"
                "要求与转股方以同样价格、条款和条件共同出售。"
                "共售股东的共售股权的数量不超过转股方拟向预期买方出售的股权数乘以一个分数，"
                "分子为该共售股东持有的注册资本金额，分母为转股方持有注册资本金额加上"
                "实际行使共同出售权的所有投资人持有注册资本金额之总和。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.rofr_tag"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["tag_holder"]["status"] == "found"
    assert fields["tag_ratio"]["status"] == "found"
    assert "共售数量按" in extraction["draft_content"]
    assert "未明确显示" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_representations_core_guard_fills_authority_and_capital_legality() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": "持股及资料真实性：相关主体不存在代持，资料真实准确。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "authority",
                    "label": "签署授权和法律能力",
                    "status": "not_found",
                    "value": "",
                },
                {
                    "key": "capital_legality",
                    "label": "增资款及持股合法性",
                    "status": "found",
                    "value": "仅见不存在代持或禁止持股。",
                },
            ]
        },
        "review_notes": ["以下关键字段未见明确约定或未被模型提取：签署授权和法律能力。"],
    }
    candidates = [
        {
            "candidate_id": "spa.representations_warranties-C04",
            "text": (
                "4.6 签约授权。各方均具有完全法律权利、能力以签署和履行本协议之全部约定。"
                "各方已经取得了签署本次增资交易文件并履行义务的所有权利或授权。"
                "4.7 投资方增资款足额且合法，资金来源符合国家法律、法规的相关要求。"
                "4.8 相关主体不存在代持或委托持股，不存在禁止持股情况。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.representations_warranties"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["authority"]["status"] == "found"
    assert "法律权利、能力" in fields["authority"]["value"]
    assert "资金来源" in fields["capital_legality"]["value"]
    assert extraction["draft_content"].splitlines()[0].startswith("签约及出资合法性：")
    assert all("签署授权" not in note for note in extraction["review_notes"])

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.representations_warranties"},
        extraction,
        candidates,
    )
    assert extraction["draft_content"].count("资料真实准确：") == 1


def test_shareholder_reserved_guard_resolves_ap_required_matters() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": "通过机制：保护性事项分两套机制。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "unanimous_matters",
                    "label": "全体投资人同意事项",
                    "status": "unclear",
                    "value": "需确认是否为全体投资人同意事项。",
                }
            ]
        },
        "review_notes": ["以下关键字段需要律师确认：全体投资人同意事项。"],
    }
    candidates = [
        {
            "candidate_id": "sha.shareholder_reserved_matters-C01",
            "text": (
                "1.1.7 公司的以下事项应当包括[[公司或组织_AI]或组织_AP]的同意方可通过："
                "(1) 修改章程；(2) 增加或者减少注册资本；(3) 清算、解散、终止；"
                "(4) 实质改变或终止主营业务；(5) 批准分红或任何利润分配。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.shareholder_reserved_matters"},
        extraction,
        candidates,
    )

    field = extraction["extracted_facts"]["field_values"][0]
    assert field["status"] == "found"
    assert "AP/投资人同意" in field["value"]
    assert "特定投资人同意事项" in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_shareholder_reserved_guard_resolves_dual_majority_mechanism() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": "保护事项：候选证据未完整显示每一轮次多数的比例门槛。",
        "extracted_facts": {
            "field_values": [
                {
                    "key": "majority_matters",
                    "label": "多数投资人同意事项",
                    "status": "unclear",
                    "value": "未见另行区分跨轮次多数投资人同意事项。",
                }
            ]
        },
        "review_notes": ["以下关键字段需要律师确认：多数投资人同意事项。"],
    }
    candidates = [
        {
            "candidate_id": "sha.shareholder_reserved_matters-C01",
            "text": (
                "8.2 未经每一轮次投资人多数（三分之二或以上）事先书面同意，公司不得从事下列(1)项行为；"
                "未经投资人多数（三分之二或以上）事先书面同意，公司不得从事下列(2)-(12)项行为。"
                "(1) 修改投资人享有的股东权利、优先权或设置任何限制；"
                "(2) 修改章程；(3) 增加注册资本；(4) 减少注册资本或回购注销；"
                "(5) 解散清算；(6) 批准利润分配；(7) 合并分立重组或控制权变更；"
                "(8) 批准上市方案；(9) 变更董事会构成；(10) 变更主营业务；"
                "(11) 发行任何数字货币；(12) 其它共同认可的任何重大事项。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.shareholder_reserved_matters"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["majority_matters"]["status"] == "found"
    assert "第(2)-(12)项须投资人多数同意" in fields["majority_matters"]["value"]
    assert "三分之二或以上" in extraction["draft_content"]
    assert "未完整显示" not in extraction["draft_content"]
    assert not extraction["review_notes"]


def test_liquidation_preference_guard_fills_events_and_new_project() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "清算触发：清算事件具体范围未完整显示。\n"
            "特殊安排：如法定分配结果偏离约定，超额取得方应再次分配。"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "liquidation_events",
                    "label": "清算事件",
                    "status": "unclear",
                    "value": "清算事件定义不完整。",
                },
                {
                    "key": "special_make_whole",
                    "label": "特殊补偿/新项目权益",
                    "status": "found",
                    "value": "再次分配。",
                },
            ]
        },
        "review_notes": ["清算事件定义不完整，建议律师复核原协议第3.4条前文。"],
    }
    candidates = [
        {
            "candidate_id": "sha.liquidation_preference-C03",
            "text": (
                "3.4.1 如果发生以下任何事件（清算事件）：(1)清算、解散或者关闭等法定清算事由；"
                "(2) 公司被兼并、收购或其他类似导致公司控制权发生变更的交易，使原股东在存续实体中"
                "持股比例或表决权比例少于50%；(3) 公司全部或实质上全部资产被出售、全部知识产权或实质上"
                "全部知识产权被许可或出售给第三方。"
                "3.4.5 如果发生清算事件，自清算事件发生之日起10年内，若相关义务人从事新项目且投资方拟投资，"
                "清算优先款与所得总额的差额部分视为对新项目的投资，并以零对价股权转让或增发股权方式取得等值权益。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.liquidation_preference"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert fields["liquidation_events"]["status"] == "found"
    assert "持股或表决权低于50%" in fields["liquidation_events"]["value"]
    assert "10年内" in fields["special_make_whole"]["value"]
    assert extraction["draft_content"].splitlines()[0].startswith("清算事件：")
    assert "新项目" in extraction["draft_content"]
    assert not extraction["review_notes"]


if __name__ == "__main__":
    test_anti_dilution_price_reset_guard()
    test_redemption_compliance_trigger_guard()
    test_redemption_guard_does_not_duplicate_existing_trigger_line()
    test_absence_ok_required_field_counts_as_handled()
    test_representations_guard_fills_transition_covenant()
    test_redemption_price_formula_guard_fills_both_formulas()
    test_dividend_guard_fills_special_approval_threshold()
    test_complete_soft_review_status_normalizes_to_drafted()
    test_complete_hard_review_status_stays_needs_review()
    test_drafted_hard_review_status_upgrades_to_needs_review()
    test_not_configured_schema_does_not_force_needs_review()
    test_spa_other_workpaper_tone_is_cleaned()
    test_post_closing_covenants_guard_compacts_overlong_summary()
    test_style_polish_payload_includes_fields_and_review_context()
    test_style_polish_validation_allows_removing_workpaper_note()
    test_candidate_context_centers_on_source_quote()
    test_transaction_arrangement_adds_header_and_cap_table_candidates()
    test_transaction_arrangement_guard_fills_signing_parties_and_cap_table()
    test_rofr_tag_adds_sha_definition_candidate()
    test_board_composition_guard_removes_client_identity_blocker()
    test_rofr_tag_guard_resolves_ap_ak_alias()
    test_rofr_tag_guard_fills_tag_along_terms()
    test_representations_core_guard_fills_authority_and_capital_legality()
    test_shareholder_reserved_guard_resolves_ap_required_matters()
    test_liquidation_preference_guard_fills_events_and_new_project()
    print("ok")
