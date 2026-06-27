from __future__ import annotations

import re
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
    output_policy_for_item,
    refresh_final_statuses,
    schema_coverage_review_notes,
    validate_polished_content,
)
from kts_docx_exporter import export_items  # noqa: E402


BARE_ORG_PLACEHOLDER_RE = re.compile(r"(?<![\[或])组织_[A-Z]{1,3}(?![\]A-Za-z])")


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
                    "label": "回购事项",
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

    assert extraction["draft_content"].splitlines()[0].startswith("回购事项：违反廉洁/反腐败/业务行为道德合规")
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
                    "label": "回购事项",
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
    assert len([line for line in lines if line.startswith("回购事项：")]) == 1
    assert lines[0].startswith("回购事项：")


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


def test_mergeable_core_output_policy_is_explicit_and_not_skipped() -> None:
    transaction_policy = output_policy_for_item({"taxonomy_id": "spa.transaction_arrangement"})
    shareholder_policy = output_policy_for_item({"taxonomy_id": "sha.shareholder_reserved_matters"})

    assert transaction_policy["category"] == "mandatory_check_mergeable_output"
    assert "签署方" in transaction_policy["instruction"]
    assert shareholder_policy["category"] == "mandatory_check_mergeable_output"
    assert "投资人权利适用门槛" in shareholder_policy["instruction"]

    rows = export_items(
        {
            "items": [
                {
                    "taxonomy_id": "spa.transaction_arrangement",
                    "group": "SPA",
                    "label": "本次交易安排",
                    "draft_content": "",
                    "status": "drafted",
                    "output_policy": transaction_policy,
                },
                {
                    "taxonomy_id": "spa.compliance",
                    "group": "SPA",
                    "label": "道德合规特别约定",
                    "draft_content": "",
                    "status": "drafted",
                    "output_policy": {"category": "conditional_output"},
                },
            ]
        }
    )

    assert [row["label"] for row in rows] == ["本次交易安排"]


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


def test_post_polish_compacts_dividend_approval_references() -> None:
    items = [
        {
            "taxonomy_id": "sha.dividend",
            "draft_content": "批准机制：公司原则上不得分红；批准分红或任何利润分配属于1.1.7项下事项，须经股东会批准并包括特定投资人同意。",
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.dividend",
            "draft_content": "批准机制：公司税后利润在依法弥补亏损、提取公积金后，须按协议第8条批准方可分配；批准或修改利润分配方案、弥补亏损方案及宣布、支付股息红利列入批准事项。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == "批准机制：公司原则上不得分红；任何利润分配须经股东会批准并取得特定投资人同意。"
    assert items[1]["draft_content"] == "批准机制：利润分配、弥补亏损及股息红利宣布/支付均须按保护性事项机制批准。"
    assert "1.1.7" not in items[0]["draft_content"]
    assert "第8条" not in items[1]["draft_content"]


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
    assert "创始股东责任：公司未按期足额支付时，相关创始人承担连带回购责任。" in extraction["draft_content"]
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


def test_refresh_final_statuses_demotes_soft_drafted_review_notes() -> None:
    items = [
        {
            "status": "drafted",
            "schema_coverage": {
                "status": "complete",
                "required_missing": 0,
                "required_unclear": 0,
            },
            "draft_content": "信息权：按协议约定提供年度报告。",
            "review_notes": ["建议律师确认是否需要补充月报。"],
            "lawyer_notes": ["既有律师提示。", "C06为其他事项，未纳入本事项摘要。"],
            "missing_or_unclear": ["未见明确月报安排。"],
        },
        {
            "status": "drafted",
            "schema_coverage": {
                "status": "complete",
                "required_missing": 0,
                "required_unclear": 0,
            },
            "draft_content": "清算权：按协议约定分配。",
            "review_notes": ["需核对全文。"],
            "lawyer_notes": [],
            "missing_or_unclear": ["完整文本未见。"],
        },
    ]

    refresh_final_statuses(items)

    assert items[0]["status"] == "drafted"
    assert items[0]["review_notes"] == []
    assert items[0]["lawyer_notes"] == ["既有律师提示。", "建议律师确认是否需要补充月报。"]
    assert items[0]["missing_or_unclear"] == []
    assert items[1]["status"] == "needs_review"
    assert items[1]["review_notes"] == ["需核对全文。"]
    assert items[1]["lawyer_notes"] == []
    assert items[1]["missing_or_unclear"] == ["完整文本未见。"]


def test_refresh_final_statuses_trims_drafted_lawyer_notes_by_priority() -> None:
    items = [
        {
            "status": "drafted",
            "schema_coverage": {
                "status": "complete",
                "required_missing": 0,
                "required_unclear": 0,
            },
            "draft_content": "董事会：按协议约定设置。",
            "review_notes": [],
            "lawyer_notes": [
                "仅作背景提示。",
                "未见董事席位安排，建议确认是否补充。",
                "该条为常规条款，提示客户知悉。",
                "税务补偿执行机制需确认。",
            ],
        },
        {
            "status": "needs_review",
            "schema_coverage": {
                "status": "complete",
                "required_missing": 0,
                "required_unclear": 0,
            },
            "draft_content": "清算权：需核对完整条款。",
            "review_notes": ["需核对全文。"],
            "lawyer_notes": ["提示一。", "提示二。", "提示三。"],
        },
    ]

    refresh_final_statuses(items)

    assert items[0]["status"] == "drafted"
    assert items[0]["lawyer_notes"] == [
        "未见董事席位安排，建议确认是否补充。",
        "税务补偿执行机制需确认。",
    ]
    assert items[1]["status"] == "needs_review"
    assert items[1]["lawyer_notes"] == ["提示一。", "提示二。", "提示三。"]


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

    assert items[0]["draft_content"] == "缺失事项：未见最惠国待遇的明确约定。"
    assert items[0]["style_polish"]["postprocess_fallback"] == "residual_rights_content"


def test_post_polish_converts_sha_other_note_only_absence_to_kts_lines() -> None:
    items = [
        {
            "taxonomy_id": "sha.other",
            "draft_content": "【注：股东协议未见常规回购权、领售权、最惠国待遇的明确约定；创始股东持续任职及不竞争义务已由“创始人及核心人员义务”事项承接。】",
            "review_notes": [],
            "lawyer_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "缺失事项：股东协议未见常规回购权、领售权、最惠国待遇的明确约定。\n"
        "已承接事项：创始股东持续任职及不竞争义务已由“创始人及核心人员义务”事项承接。"
    )


def test_sha_other_absence_policy_counts_missing_rights_as_handled() -> None:
    item = {
        "taxonomy_id": "sha.other",
        "content_schema": {
            "fields": [
                {
                    "key": "ordinary_redemption",
                    "label": "常规回购权",
                    "required": True,
                }
            ]
        },
    }
    extracted_facts = {
        "field_values": [
            {
                "key": "ordinary_redemption",
                "label": "常规回购权",
                "status": "not_found",
                "value": "未见明确约定。",
            }
        ]
    }

    coverage = build_schema_coverage(item, extracted_facts)

    assert coverage["status"] == "complete"
    assert coverage["required_handled"] == 1
    assert coverage["required_absent_ok"] == 1
    assert not schema_coverage_review_notes(coverage)


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


def test_docx_export_skips_empty_absence_check_items() -> None:
    record = {
        "items": [
            {
                "taxonomy_id": "sha.other",
                "group": "SHA",
                "label": "其他",
                "draft_content": "",
                "status": "drafted",
                "output_policy": {"category": "mandatory_check_absence_output"},
            },
            {
                "taxonomy_id": "sha.redemption",
                "group": "SHA",
                "label": "特殊回购权",
                "draft_content": "触发事项：按协议约定。",
                "status": "drafted",
                "output_policy": {"category": "mandatory_check_default_output"},
            },
        ]
    }

    rows = export_items(record)

    assert [row["label"] for row in rows] == ["特殊回购权"]


def test_docx_export_keeps_absence_check_content() -> None:
    record = {
        "items": [
            {
                "taxonomy_id": "sha.other",
                "group": "SHA",
                "label": "其他",
                "draft_content": "缺失事项：股东协议未见常规回购权、领售权、最惠国待遇的明确约定。",
                "status": "drafted",
                "output_policy": {"category": "mandatory_check_absence_output"},
            }
        ]
    }

    rows = export_items(record)

    assert rows[0]["content_lines"] == [
        "缺失事项：股东协议未见常规回购权、领售权、最惠国待遇的明确约定。"
    ]


def test_docx_export_keeps_pending_check_marker_unnumbered() -> None:
    record = {
        "items": [
            {
                "taxonomy_id": "spa.closing",
                "group": "SPA",
                "label": "交割及工商变更安排",
                "draft_content": "付款期限：先决条件满足后10个工作日内付款。【待核：工商变更作为付款前条件的交易顺序可操作性。】",
                "status": "needs_review",
                "output_policy": {"category": "mandatory_check_default_output"},
            }
        ]
    }

    rows = export_items(record)

    assert rows[0]["content_lines"] == [
        "1. 付款期限：先决条件满足后10个工作日内付款。",
        "【待核：工商变更作为付款前条件的交易顺序可操作性。】",
    ]


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
    assert "背景事实显示" not in extraction["draft_content"]
    assert "候选证据" not in "\n".join(extraction["review_notes"])


def test_post_closing_covenants_guard_replaces_stale_case_compact() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": (
            "资金用途：限业务拓展、研发、生产、资本支出及主营业务；偿债需股东会全票同意，对外投资/委托贷款/证券期货需[公司或组织_BH]同意。\n"
            "实缴承诺：相关现有股东应于第一次交割日后三年内实缴；[公司或组织_BF]应于2029年12月31日前实缴。\n"
            "团队/IP/任职：落实知识产权权属或授权、团队保密/IP/竞业安排；两名创始股东承诺八年或合格上市后一周年孰早前不主动离职。"
        ),
        "extracted_facts": {
            "summary_points": [
                "交割后三个月内应完成对相关主体100%股权收购或注销并办理工商变更登记。",
                "主营业务所需知识产权具备申请条件后六个月内应提交注册登记或申请。",
            ],
            "field_values": [
                {
                    "key": "use_of_proceeds",
                    "label": "增资款用途限制",
                    "status": "found",
                    "value": "交割日后，增资款应按经[商标品牌_H]或其提名董事批准的公司预算，用于主营业务发展及相关运营；未经[商标品牌_H]同意或协议另有约定，不得用于与主营业务无关用途，包括偿还公司任何债务。",
                },
                {
                    "key": "capital_contribution",
                    "label": "历史/现有股东实缴承诺",
                    "status": "found",
                    "value": "证据显示公司历次出资或增资及相关手续符合当时有效法律法规，不存在延迟出资、出资不实或抽逃出资；未见新增交割后补缴出资承诺。",
                },
                {
                    "key": "ip_transfer",
                    "label": "知识产权转移",
                    "status": "found",
                    "value": "交割日后，公司/创始方应促使员工及研发人员将与公司主营业务相关的无形资产合法转至公司名下，或由公司作为申请人提交登记/申请；未经[商标品牌_H]书面同意，不得处分或用于主营业务以外活动。",
                },
                {
                    "key": "regulatory_milestones",
                    "label": "业务许可/备案里程碑",
                    "status": "found",
                    "value": "投资方承诺提供必要文件，协助公司取得履行协议所需的政府批准、同意、许可、登记和备案；公司未来为境内外融资、取得特定政府许可牌照、IPO或各方商定目的进行架构调整时，方案须经相关各方协商并获[商标品牌_H]认可。",
                },
                {"key": "continued_service", "label": "创始团队持续任职", "status": "not_found", "value": "未见创始团队持续任职期限。"},
                {"key": "non_compete_and_priority", "label": "竞业限制/业务唯一性", "status": "unclear", "value": "未见明确竞业限制。"},
            ],
        },
        "review_notes": [],
        "lawyer_notes": [],
    }

    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.post_closing_covenants"},
        extraction,
        [],
    )

    assert "[公司或组织_BH]" not in extraction["draft_content"]
    assert "[公司或组织_BF]" not in extraction["draft_content"]
    assert "2029年12月31日" not in extraction["draft_content"]
    assert "资金用途：按经投资方或其提名董事批准的预算" in extraction["draft_content"]
    assert "IP/无形资产归属：" in extraction["draft_content"]
    assert "股权/架构整理：" in extraction["draft_content"]
    assert "创始团队持续任职期限" in extraction["draft_content"]


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
            ],
            "lawyer_notes": ["需确认本次增资的整体融资额、投前/投后估值及各投资方投资金额。"],
            "missing_or_unclear": ["完整投资方清单仍需确认。"],
        },
        "review_notes": ["以下关键字段需要律师确认：投资方及投资金额。"],
        "lawyer_notes": ["需确认本次增资的整体融资额、投前/投后估值及各投资方投资金额。"],
        "missing_or_unclear": ["完整投资方清单仍需确认。"],
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
    assert not extraction["lawyer_notes"]
    assert not extraction["missing_or_unclear"]
    assert not extraction["extracted_facts"]["lawyer_notes"]
    assert not extraction["extracted_facts"]["missing_or_unclear"]

    item = {"taxonomy_id": "spa.transaction_arrangement", **extraction}
    apply_post_polish_quality_guards([item])
    apply_deterministic_quality_guards(
        {"taxonomy_id": "spa.transaction_arrangement"},
        item,
        candidates,
    )
    apply_post_polish_quality_guards([item])
    assert item["draft_content"].count("投资方概览：") == 1
    assert item["draft_content"].count("其余投资方：") == 1


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


def test_post_polish_splits_board_composition_long_line() -> None:
    items = [
        {
            "taxonomy_id": "sha.board_composition",
            "draft_content": (
                "董事会构成：本次交易完成后，董事会由五名董事组成；组织_W、组织_Z、组织_N各推选一名董事，组织_F推选两名董事，由股东会选举产生。\n"
                "董事长：由组织_F提名的董事担任。\n"
                "【注：未见独立观察员委派权。】"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.board_composition",
            "draft_content": (
                "董事会构成：董事会7席，[[公司或组织_AE]或组织_C]委派4席并含董事长；"
                "[商标品牌_G]持股不低于8%时委派1席，[商标品牌_A]、[商标品牌_F]各委派1席。\n"
                "席位调整：A/F/G任一方持股低于5%即丧失董事委派权；持股不低于2%时可改派1名观察员。\n"
                "观察员：除已获董事席位投资人外，其他投资人中持股最高前两名可各委派1名观察员，交割后为[商标品牌_D]和[商标品牌_C]。\n"
                "子公司/集团公司：[商标品牌_A]、[商标品牌_F]、[商标品牌_G]可分别要求向其他[[公司或组织_AE]或组织_H]董事会委派1名董事。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "董事会规模：本次交易完成后，董事会设五名董事，由股东会选举产生。" in draft
    assert "一席委派方：组织_W、组织_Z、组织_N各推选一名董事。" in draft
    assert "两席委派方：组织_F推选两名董事。" in draft
    assert "董事会构成：本次交易完成后" not in draft
    assert draft.count("董事会规模：") == 1
    assert "董事长：由组织_F提名的董事担任。" in draft
    assert "未见独立观察员委派权" in draft

    draft = items[1]["draft_content"]
    assert "董事会规模：董事会7席。" in draft
    assert "四席委派方：[[公司或组织_AE]或组织_C]委派4席并含董事长。" in draft
    assert "其他席位：[商标品牌_G]持股不低于8%时委派1席，[商标品牌_A]、[商标品牌_F]各委派1席。" in draft
    assert "董事席位门槛：A/F/G任一方持股低于5%即丧失董事委派权。" in draft
    assert "观察员替代：持股不低于2%时可改派1名观察员。" in draft
    assert "观察员名额：除已获董事席位投资人外，其他投资人中持股最高前两名可各委派1名观察员。" in draft
    assert "交割后观察员：[商标品牌_D]和[商标品牌_C]。" in draft
    assert "集团公司董事：[商标品牌_A]、[商标品牌_F]、[商标品牌_G]可分别要求向其他集团公司董事会委派1名董事。" in draft
    assert "席位调整：" not in draft
    assert "子公司/集团公司：" not in draft


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


def test_anti_dilution_guard_fills_complete_exception_list() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": (
            "反稀释方式：采用价格重设/接近全棘轮机制。\n"
            "例外事项：员工激励或股权薪酬计划，经股东会通过的利润转增注册资本、资本公积转增股本等不适用。\n"
            "【注：第3.5.4第(3)项作为反稀释例外的口径可结合协议版本确认。】"
        ),
        "extracted_facts": {"field_values": []},
        "review_notes": ["建议律师重点复核第3.5.4第(3)项是否应列入反稀释例外。"],
        "lawyer_notes": ["3.5.4第(3)项关于清算剩余财产分配，表述上不像反稀释例外事项，建议核对材料版本或条款编号。"],
    }
    candidates = [
        {
            "candidate_id": "sha.anti_dilution-C06",
            "text": (
                "3.5.4在下列情况下，反稀释权人不享有本第3.5条下的反稀释权利："
                "（1）为实施任何员工激励计划或涉及股权的薪酬计划而新增的注册资本；"
                "（2）经股东会通过的，利润转增注册资本、资本公积转增股本等情况下新增的注册资本；或"
                "（3）经股东会批准公司改制为股份有限公司后的股份、红利或分拆等情况下进行转换而发行的股份、"
                "在合格上市中发行的证券、或类似的证券发行。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.anti_dilution"},
        extraction,
        candidates,
    )

    assert "股份制改制转换、合格上市发行及类似证券发行" in extraction["draft_content"]
    assert "第3.5.4第(3)项作为反稀释例外" not in extraction["draft_content"]
    assert not extraction["review_notes"]
    assert not extraction["lawyer_notes"]


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
                "价格与付款：回购价格为投资成本加收益与公允价值孰高；义务人应在三个月内付款。\n"
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
    assert "投资方概览：共6名投资方，合计人民币97,000,000元。" in combined
    assert "主要投资方：[公司或组织_A]人民币50,000,000元、[公司或组织_B]人民币25,000,000元、[公司或组织_C]人民币10,000,000元" in combined
    assert "候选证据" not in combined
    assert "排他期承诺：签署日至交割日" in combined
    assert "排他安排：" not in combined
    assert "【注：两项10%额度是否累计适用、审批机构口径可结合协议定义确认。】" in combined
    assert "【注：第4.0.7条10%违约金可能与逾期违约金并行适用。】" in combined
    assert "回购事项：违反廉洁条款时可要求回购。" in combined
    assert "回购义务人：公司及/或创始人。" in combined
    assert "回购价格：按投资成本加收益与公允价值孰高确定。" in combined
    assert "回购期限：回购通知后60日内付款。" in combined
    assert "回购价格：投资成本加收益与公允价值孰高。" in combined
    assert "回购价格：回购价格为" not in combined
    assert "回购期限：义务人应在三个月内付款。" in combined
    assert "逾期责任及顺位：逾期按每日万分之三支付违约金。" in combined
    assert "【注：未见控制权变更全额共售安排。】" in combined


def test_post_polish_deduplicates_redemption_trigger_lines() -> None:
    items = [
        {
            "taxonomy_id": "sha.redemption",
            "draft_content": (
                "回购触发事项：违反业务行为道德合规/廉洁条款，包括提供或接受不当利益，或存在代持、利益输送、资金往来等利益安排。\n"
                "回购触发事项：违反廉洁、反腐败及利益安排相关承诺时，投资方可要求其回购。\n"
                "回购价格：按投资成本加收益与公允价值孰高确定。"
            ),
            "review_notes": [
                "已仅基于high和medium证据起草。",
                "C07为股东名册信息，与特殊回购权无直接关联，未纳入摘要。",
            ],
        },
        {
            "taxonomy_id": "sha.redemption",
            "draft_content": (
                "回购触发事项：违反业务行为道德合规/廉洁条款，包括提供或接受不当利益，或除投资合作及经同意合作外存在代持、利益输送、资金往来等利益安排，并触发第2.3条回购义务。\n"
                "回购价格：按约定公式计算。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    lines = items[0]["draft_content"].splitlines()
    trigger_lines = [line for line in lines if line.startswith("回购事项：")]
    assert trigger_lines == [
        "回购事项：违反廉洁/反腐败/业务行为道德合规及利益安排承诺（包括不当利益、代持、利益输送、资金往来等）时，投资方可要求回购。"
    ]
    assert not items[0]["review_notes"]
    assert items[1]["draft_content"].splitlines()[0] == trigger_lines[0]


def test_post_polish_splits_redemption_exercise_and_payment_deadlines() -> None:
    items = [
        {
            "taxonomy_id": "sha.redemption",
            "draft_content": (
                "回购期限：触发事件发生后30日内通知投资方；"
                "回购义务人收到回购通知后1个月内签署相关协议，并于60日内全额支付回购价款。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert "行权期限：触发事件发生后30日内通知投资方。" in items[0]["draft_content"]
    assert "付款期限：回购义务人收到回购通知后1个月内签署相关协议，并于60日内全额支付回购价款。" in items[0]["draft_content"]


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
    assert "约定比例共同出售" in extraction["draft_content"]
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


def test_representations_core_guard_cleans_stale_lawyer_notes() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": "资料真实准确：公司方提供资料在重大方面真实、准确、完整。",
        "extracted_facts": {
            "field_values": [],
            "lawyer_notes": ["材料未见签署授权和法律能力相关陈述保证，建议结合协议第4条完整文本确认。"],
            "missing_or_unclear": ["增资款来源合法性未在材料中直接体现。"],
        },
        "review_notes": ["以下关键字段未见明确约定或未被模型提取：签署授权和法律能力。"],
        "lawyer_notes": [
            "材料未见签署授权和法律能力相关陈述保证，建议结合协议第4条完整文本确认。",
            "增资款来源合法性未在材料中直接体现，仅见持股合法性。",
        ],
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

    assert "签约及出资合法性：" in extraction["draft_content"]
    assert not extraction["review_notes"]
    assert not extraction["lawyer_notes"]
    assert not extraction["extracted_facts"]["lawyer_notes"]
    assert not extraction["extracted_facts"]["missing_or_unclear"]


def test_representations_core_guard_deduplicates_existing_legality_lines() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": (
            "签约及出资合法性：各方具备签署、履行交易文件的法律能力及授权；投资方增资款足额且来源合法。\n"
            "资料真实准确：公司方提供资料在重大方面真实、准确、完整。\n"
            "签约及持股合法性：各方具备签署、履行交易文件的法律权利、能力及授权；相关方确认不存在代持、委托持股或禁止持股情形。\n"
            "过渡期限制：过渡期内公司应按过往惯例正常经营。"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "authority",
                    "label": "签署授权和法律能力",
                    "status": "found",
                    "value": "各方具有签署授权和法律能力。",
                    "note": "模型已抽取。",
                },
                {
                    "key": "capital_legality",
                    "label": "增资款及持股合法性",
                    "status": "found",
                    "value": "投资方资金来源合法，相关主体不存在代持。",
                    "note": "模型已抽取。",
                },
            ]
        },
        "review_notes": ["本摘要已排除违约责任等非本KTS事项内容。"],
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
    apply_post_polish_quality_guards([{"taxonomy_id": "spa.representations_warranties", **extraction}])

    assert extraction["draft_content"].count("签约及出资合法性：") == 1
    assert "签约及持股合法性：" not in extraction["draft_content"]
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

    item = {"taxonomy_id": "sha.shareholder_reserved_matters", **extraction}
    apply_post_polish_quality_guards([item])
    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.shareholder_reserved_matters"},
        item,
        candidates,
    )
    apply_post_polish_quality_guards([item])
    assert item["draft_content"].count("投资人权利事项：") == 1
    assert item["draft_content"].count("重大保护事项：") == 1


def test_shareholder_reserved_guard_removes_client_veto_practicality_blocker() -> None:
    item = {
        "taxonomy_id": "sha.shareholder_reserved_matters",
        "content_schema": {
            "fields": [
                {"key": "approval_mechanism", "label": "通过机制", "required": True},
                {"key": "unanimous_matters", "label": "特定/每轮投资人同意事项", "required": True},
                {"key": "majority_matters", "label": "多数投资人同意事项", "required": True},
                {"key": "investor_threshold_definition", "label": "投资人权利适用门槛/定义", "required": False},
            ]
        },
    }
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "通过机制：股东会保护性事项分层设置；1.1.7事项需包括[[公司或组织_AI]或组织_AP]同意，"
            "1.1.8事项需三分之二以上表决权股东同意且包括多数[[公司或组织_AI]或组织_AK]同意。\n"
            "特定投资人事项：修改章程、增减注册资本、清算/解散/终止、主营业务实质变更或终止、分红及利润分配需获[[公司或组织_AI]或组织_AP]同意。\n"
            "多数投资人事项：合并分立并购重组、控制权变更、重大资产/权益处置或设负担、上市方案、董事会及ESOP，适用多数投资人同意机制。\n"
            "【注：多数[[公司或组织_AI]或组织_AK]为持有超过三分之二优先股的股东；本方能否单独veto需结合其优先股持股及身份确认。】"
        ),
        "extracted_facts": {
            "summary_points": [
                "特定投资人同意机制：1.1.7项下事项需包括[[公司或组织_AI]或组织_AP]同意方可通过。",
                "多数[[公司或组织_AI]或组织_AK]定义为持有超过三分之二优先股的股东；优先股指[[公司或组织_AI]或组织_AP]持有的股权。",
                "本方veto可行性取决于其是否可单独或联合阻却多数[[公司或组织_AI]或组织_AK]同意，现有证据未显示本方持股。",
            ],
            "unclear_points": [
                "[[公司或组织_AI]或组织_AP]、[[公司或组织_AI]或组织_AK]及本方之间的身份对应关系未在证据中直接展开。"
            ],
            "field_values": [
                {
                    "key": "approval_mechanism",
                    "label": "通过机制",
                    "status": "found",
                    "value": "存在分层保护性表决机制。",
                },
                {
                    "key": "unanimous_matters",
                    "label": "特定/每轮投资人同意事项",
                    "status": "found",
                    "value": "需包括[[公司或组织_AI]或组织_AP]同意。",
                },
                {
                    "key": "majority_matters",
                    "label": "多数投资人同意事项",
                    "status": "found",
                    "value": "需三分之二以上表决权并包括多数[[公司或组织_AI]或组织_AK]同意。",
                },
                {
                    "key": "investor_veto_practicality",
                    "label": "本方veto可行性",
                    "status": "unclear",
                    "value": "未显示本方是否持有足够优先股。",
                    "note": "需结合本方持股比例判断实际否决权。",
                },
            ],
            "lawyer_notes": [
                "保护性事项采用双层机制：部分事项由特定主体[[公司或组织_AI]或组织_AP]同意，部分事项由三分之二表决权加多数[[公司或组织_AI]或组织_AK]同意；KTS中不宜概括为全体投资人一致同意。",
                "如本方无法单独构成多数[[公司或组织_AI]或组织_AK]或无法阻却超过三分之二优先股同意，其对1.1.8事项的veto实际可行性需进一步确认。",
            ],
            "missing_or_unclear": [
                "未见本方持股比例、优先股持股比例及其是否属于[[公司或组织_AI]或组织_AP]/[[公司或组织_AI]或组织_AK]，无法判断本方单独veto能力。"
            ],
        },
        "review_notes": ["本方veto可行性证据不足，建议律师结合投资人清单和持股比例确认。"],
        "missing_or_unclear": [
            "未见本方持股比例、优先股持股比例及其是否属于[[公司或组织_AI]或组织_AP]/[[公司或组织_AI]或组织_AK]，无法判断本方单独veto能力。"
        ],
        "lawyer_notes": [
            "保护性事项采用双层机制：部分事项由特定主体[[公司或组织_AI]或组织_AP]同意，部分事项由三分之二表决权加多数[[公司或组织_AI]或组织_AK]同意；KTS中不宜概括为全体投资人一致同意。",
            "如本方无法单独构成多数[[公司或组织_AI]或组织_AK]或无法阻却超过三分之二优先股同意，其对1.1.8事项的veto实际可行性需进一步确认。",
        ],
    }
    candidates = [
        {
            "candidate_id": "sha.shareholder_reserved_matters-C01",
            "text": (
                "1.1.3 多数[[公司或组织_AI]或组织_AK]定义为持有超过三分之二优先股的股东；"
                "优先股指[[公司或组织_AI]或组织_AP]持有的股权。"
                "1.1.7 [公司或组织_AI]的以下事项应当包括[[公司或组织_AI]或组织_AP]的同意方可通过："
                "(1) 修改章程；(2) 增加或者减少注册资本；(3) 清算、解散、终止；"
                "(4) 实质改变或终止主营业务；(5) 批准分红或任何利润分配。"
                "1.1.8 以下事项必须经代表三分之二或以上表决权的股东同意，"
                "其中必须包括按持股比例计算的多数[[公司或组织_AI]或组织_AK]同意方可通过。"
            ),
        }
    ]

    apply_deterministic_quality_guards(item, extraction, candidates)
    extraction["taxonomy_id"] = "sha.shareholder_reserved_matters"
    apply_post_polish_quality_guards([extraction])

    assert "本方" not in extraction["draft_content"]
    assert "veto" not in extraction["draft_content"]
    assert "投资人门槛：" in extraction["draft_content"]
    assert "优先股指[[公司或组织_AI]或组织_AP]持有的股权" in extraction["draft_content"]
    assert "表决层级：" in extraction["draft_content"]
    assert "特定投资人同意机制：" in extraction["draft_content"]
    assert "多数投资人同意机制：" in extraction["draft_content"]
    assert "1.1.7事项：" not in extraction["draft_content"]
    assert not extraction["review_notes"]
    assert not extraction["missing_or_unclear"]
    assert all("本方" not in note and "veto" not in note for note in extraction["lawyer_notes"])

    facts = extraction["extracted_facts"]
    for key in ("summary_points", "unclear_points", "lawyer_notes", "missing_or_unclear"):
        assert all("本方" not in point for point in facts[key])
        assert all("veto" not in point for point in facts[key])

    fields = {field["key"]: field for field in facts["field_values"]}
    assert "investor_veto_practicality" not in fields
    assert fields["investor_threshold_definition"]["status"] == "found"
    assert "三分之二优先股" in fields["investor_threshold_definition"]["value"]

    coverage = build_schema_coverage(item, facts)
    assert normalize_final_status(
        "needs_review",
        coverage,
        extraction["draft_content"],
        extraction["review_notes"],
    ) == "drafted"


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


def test_liquidation_preference_guard_cleans_stale_lawyer_notes() -> None:
    extraction = {
        "status": "drafted",
        "draft_content": "清算触发：清算事件具体范围未完整显示。",
        "extracted_facts": {"field_values": []},
        "review_notes": ["清算事件定义不完整，建议律师复核原协议第3.4条前文。"],
        "lawyer_notes": ["清算事件定义在材料中未完整呈现，建议核对完整第3.4条及前文列举事件。"],
        "missing_or_unclear": ["清算事件未完整。"],
    }
    candidates = [
        {
            "candidate_id": "sha.liquidation_preference-C03",
            "text": (
                "3.4.1 如果发生以下任何事件（清算事件）：(1)清算、解散或者关闭等法定清算事由；"
                "(2) 公司被兼并、收购或其他类似导致公司控制权发生变更的交易，使原股东在存续实体中"
                "持股比例或表决权比例少于50%；(3) 公司全部或实质上全部资产被出售、全部知识产权或实质上"
                "全部知识产权被许可或出售给第三方。"
            ),
        }
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.liquidation_preference"},
        extraction,
        candidates,
    )

    assert extraction["draft_content"].startswith("清算事件：")
    assert not extraction["review_notes"]
    assert not extraction["lawyer_notes"]
    assert not extraction["missing_or_unclear"]


def test_post_polish_liquidation_review_focuses_cross_reference_issue() -> None:
    items = [
        {
            "taxonomy_id": "sha.liquidation_preference",
            "draft_content": (
                "清算事件：公司解散、清算、破产及视为清算事件触发优先清算。\n"
                "清算顺位及金额：本轮优先清算权人先于天使轮优先清算权人取得优先清算额。\n"
                "剩余分配：优先清算权人取得全部优先额后仍参与剩余财产分配。\n"
                "【待核：条款交叉引用“第11.1条”疑与清算分配条款编号不一致。】"
            ),
            "review_notes": ["需律师重点复核交叉引用编号、参与型优先清算安排及优先清算权人主体占位符。"],
            "missing_or_unclear": [
                "交叉引用“第11.1条”与当前上下文的清算分配条款编号不一致。",
                "未见优先清算额包含固定倍数、年单利或其他固定回报。",
                "未见新项目权益或零对价补偿安排。",
            ],
        }
    ]

    apply_post_polish_quality_guards(items)

    item = items[0]
    assert item["review_notes"] == ["需律师核对第10.2/10.3条对“第11.1条”的交叉引用是否为编号误植。"]
    assert item["missing_or_unclear"] == []
    assert "参与型优先清算" not in "\n".join(item["review_notes"])
    assert "主体占位符" not in "\n".join(item["review_notes"])
    assert item["draft_content"].count("【待核：") == 1


def test_founder_obligations_guard_completes_service_and_non_compete_summary() -> None:
    extraction = {
        "status": "needs_review",
        "draft_content": (
            "股权成熟：创始人/相关高管直接或间接持有的受限股权适用4年成熟期。\n"
            "持续服务及违约处理：成熟期内主动离职、不再续签劳动/服务协议或因过错被解职的，相关受限股权须转让。\n"
            "【注：另有至IPO后一年的相关承诺片段，但承诺对象、具体义务及例外未完整体现。】"
        ),
        "extracted_facts": {
            "field_values": [
                {
                    "key": "service_commitment",
                    "label": "持续任职/全职投入",
                    "status": "found",
                    "value": "另有候选证据显示自天使轮交割日至IPO后一年的相关承诺，但具体义务内容未完整呈现。",
                    "note": "C06片段不完整，无法进一步确认承诺对象及全职投入的完整表述。",
                }
            ],
            "summary_points": ["证据显示存在自天使轮交割日至IPO后一年的相关承诺，但候选片段未完整呈现。"],
            "lawyer_notes": ["C06显示存在相关承诺，但候选片段未完整呈现具体义务。"],
            "missing_or_unclear": ["C06关于承诺期限和内容的原文截断，无法确认承诺对象、具体义务及完整例外。"],
        },
        "review_notes": ["C06证据片段不完整，关于IPO后一年的持续义务需律师结合完整条款确认。"],
        "missing_or_unclear": ["C06关于承诺期限和内容的原文截断，无法确认承诺对象、具体义务及完整例外。"],
    }
    candidates = [
        {
            "candidate_id": "sha.founder_obligations-C01",
            "text": (
                "2.1 受限股权将分4年成熟；每满一(1)年，其所持受限股权总额中的25%予以成熟。"
                "公司被收购或兼并且届时收购方同意，或公司完成首次公开发行，则全部受限股权应加速成熟。"
                "2.2 在成熟期内，若任一创始人主动离职/不再续签劳动/服务协议或因过错理由被解职，"
                "则其应将受限股权无偿或以法律允许的最低价格转让。"
                "2.3 其他原因终止劳动关系的，未成熟股权适用前述安排，已成熟股权保留但放弃投票权及董事提名权。"
            ),
        },
        {
            "candidate_id": "sha.founder_obligations-C08",
            "text": (
                "0.1 创始人及核心人员承诺：自天使轮增资交割日起直至公司实现首次公开发行后一(1)年届满之日，"
                "在全职加入公司之前，除在投资人事先同意的其他研究机构任职期间合理必要的工作外，"
                "应为公司业务发展贡献剩余实质性全部工作时间和精力，不得在公司之外任职或投资或提供服务；"
                "自其全职加入公司之日起，应贡献实质性全部工作时间和精力，不得在公司之外任职或投资或提供服务，"
                "且研究机构任职不得造成实质不利影响。"
                "自本协议签署之日起至以下两者时间发生较晚者期间（限制期）内："
                "(A)解除劳动(服务)关系之后两(2)年届满之日；或(B)不直接或者间接持有公司任何股权之后两(2)年届满之日，"
                "不得直接或间接进行以下竞争性活动：(a). 投资、参与、协助或从事与公司业务形成竞争关系的业务或实体；"
                "(b). 劝说客户购买竞争服务；(c). 劝说或诱导员工离职；"
                "(d). 为了与公司无关的目的披露或使用公司商业秘密或保密信息。"
            ),
        },
    ]

    apply_deterministic_quality_guards(
        {"taxonomy_id": "sha.founder_obligations"},
        extraction,
        candidates,
    )

    fields = {field["key"]: field for field in extraction["extracted_facts"]["field_values"]}
    assert extraction["status"] == "drafted"
    assert "持续服务：" in extraction["draft_content"]
    assert "竞业及保密/IP：" in extraction["draft_content"]
    assert "IPO后一周年" in fields["service_commitment"]["value"]
    assert "离职后两年" in fields["non_compete"]["value"]
    assert "商业秘密或保密信息" in fields["confidentiality_ip"]["value"]
    combined = extraction["draft_content"] + "\n" + "\n".join(extraction["review_notes"])
    assert "未完整" not in combined
    assert "截断" not in combined
    assert not extraction["review_notes"]


def test_post_polish_guard_rewrites_founder_stale_review_tone() -> None:
    items = [
        {
            "taxonomy_id": "sha.founder_obligations",
            "draft_content": (
                "股权成熟：受限股权适用4年成熟期。\n"
                "持续服务及违约处理：候选片段未完整显示IPO后一年的义务。\n"
                "【注：另有至IPO后一年的相关承诺片段，但承诺对象、具体义务及例外未完整体现。】"
            ),
            "extracted_facts": {
                "field_values": [
                    {
                        "key": "vesting",
                        "label": "股权成熟/兑现",
                        "status": "found",
                        "value": "创始人/相关高管持有的受限股权分4年成熟，每满1年成熟25%。",
                    },
                    {
                        "key": "service_commitment",
                        "label": "持续任职/全职投入",
                        "status": "found",
                        "value": "自天使轮增资交割日至IPO后一周年，相关创始人/核心人员应投入实质性全部工作时间和精力。",
                    },
                    {
                        "key": "breach_consequence",
                        "label": "违约后果",
                        "status": "found",
                        "value": "受限股权须无偿或以法定最低价格转让，已成熟部分保留但放弃投票权/董事提名等管理权。",
                    },
                    {
                        "key": "non_compete",
                        "label": "不竞争/竞业限制",
                        "status": "found",
                        "value": "限制期至离职后两年或不再持股后两年孰晚。",
                    },
                    {
                        "key": "confidentiality_ip",
                        "label": "保密/IP归属",
                        "status": "found",
                        "value": "不得披露或使用商业秘密或保密信息。",
                    },
                ]
            },
            "review_notes": [
                "C01为本事项核心证据；C02仅用于识别创始人/创始股东主体。",
                "C06证据片段不完整，关于IPO后一年的持续义务需律师结合完整条款确认。",
            ],
            "lawyer_notes": ["候选片段未完整呈现具体义务。"],
            "missing_or_unclear": ["C06原文截断，无法确认承诺对象。"],
        }
    ]

    apply_post_polish_quality_guards(items)

    item = items[0]
    assert "持续服务：" in item["draft_content"]
    assert "外部任职限制：" in item["draft_content"]
    assert "其他离职后果：" in item["draft_content"]
    assert "竞业及保密/IP：" in item["draft_content"]
    assert "未完整" not in item["draft_content"]
    assert "截断" not in item["draft_content"]
    assert not item["review_notes"]
    assert not item["lawyer_notes"]
    assert not item["missing_or_unclear"]


def test_post_polish_splits_founder_service_long_line() -> None:
    items = [
        {
            "taxonomy_id": "sha.founder_obligations",
            "draft_content": (
                "股权成熟：创始人/相关高管持有的受限股权分4年成熟。\n"
                "持续服务：自天使轮增资交割日至IPO后一周年，相关创始人/核心人员在全职加入前应投入剩余实质性全部工作时间和精力；"
                "全职加入后应投入实质性全部工作时间和精力，均不得在公司/集团外任职、投资或提供服务；"
                "经投资人同意的研究机构任职例外，但不得实质影响其对公司的职责和经营管理。\n"
                "其他离职的未成熟部分同样适用，已成熟部分保留但放弃投票权/董事提名等管理权。\n"
                "竞业及保密/IP：限制期至离职后两年或不再持股后两年孰晚。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "持续服务：自天使轮增资交割日至IPO后一周年，相关创始人/核心人员在全职加入前后均应投入实质性全部工作时间和精力。" in draft
    assert "外部任职限制：全职加入前后均不得在公司/集团外任职、投资或提供服务" in draft
    assert "其他离职后果：其他离职的未成熟部分同样适用" in draft
    assert "持续服务：自天使轮增资交割日至IPO后一周年，相关创始人/核心人员在全职加入前应投入" not in draft
    assert draft.count("外部任职限制：") == 1


def test_post_polish_removes_nonblocking_workpaper_review_notes() -> None:
    items = [
        {
            "taxonomy_id": "spa.closing",
            "draft_content": "交割安排：按协议约定完成。",
            "review_notes": [
                "已剔除解除、违约责任及费用类内容，仅保留交割及工商变更安排。",
                "本摘要已排除违约赔偿、解除及商标使用等非本KTS事项内容。",
                "已仅基于high和medium证据形成摘要；C02未纳入当前摘要。",
                "未见过渡期限制事项的，已作为缺失检查结论处理。",
                "已排除股东会保护性事项及违约责任/费用承担内容。",
                "C07为反稀释权条款，未纳入本事项摘要。",
                "摘要未展开通知期限和视为放弃等程序性细节，已保留于extracted_facts。",
                "本事项已剔除知识产权陈述、违约赔偿和公司治理保留事项等其他KTS事项内容。",
                "摘要仅基于候选证据中的第3.4.5条起草。",
                "系统根据全篇关键词补充未见明确约定事项。",
                "固定优先分红为absence_ok字段，证据未见明确约定，已作为缺失检查项提示。",
                "工商变更未完成解除条款中的具体时限未在候选证据中体现。",
                "候选摘录中“每一轮次多数”的具体持股比例定义被截断，建议核对完整条款确认多数门槛。",
                "第8条批准机制的具体机构、表决门槛及是否需投资方同意未在证据窗口体现，建议核对完整第8条。",
                "回购价格公式中的I虽可结合上下文理解为回购权人支付成本，但证据窗口未完整展示定义，建议核对原文第9.3条完整公式定义。",
                "需律师重点复核工商变更登记作为付款先决条件的交易顺序。",
                "子公司董事会一致安排不明确，已作为需确认事项提示。",
            ],
            "lawyer_notes": [
                "材料未发现登记权/注册权安排；根据本事项输出政策，不宜起草登记权KTS正文。",
                "常规回购权已见未完成首次公开发行、严重违法/违约等触发事由，不仅是廉洁或特殊事项触发的回购权。",
                "需确认交割日安排。",
            ],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["review_notes"] == [
        "工商变更未完成解除条款中的具体时限未在材料中体现。",
        "“每一轮次多数”的具体持股比例定义未完整体现，需确认多数门槛。",
        "第8条批准机制的具体机构、表决门槛及是否需投资方同意未完整体现，需确认第8条。",
        "回购价格公式中的I虽可结合上下文理解为回购权人支付成本，但未完整体现定义，需确认第9.3条公式定义。",
        "需律师重点复核工商变更登记作为付款先决条件的交易顺序。",
        "需确认子公司董事会结构是否与公司董事会保持一致。",
    ]
    assert items[0]["lawyer_notes"] == ["需确认交割日安排。"]


def test_post_polish_deduplicates_missing_notes_already_in_review_notes() -> None:
    items = [
        {
            "taxonomy_id": "spa.closing",
            "draft_content": "交割安排：按协议约定完成。",
            "review_notes": [
                "需律师重点复核工商变更登记作为付款先决条件的交易顺序，以及股东名册/出资证明书是否应调整为交割时交付。",
            ],
            "missing_or_unclear": [
                "工商变更登记作为付款先决条件而非交割后事项，需确认顺序安排。",
                "未见当前证据明确付款通知发出与交割期限起算之间的具体时间关系。",
            ],
        },
        {
            "taxonomy_id": "sha.liquidation_preference",
            "draft_content": "清算顺位：按协议约定执行。",
            "review_notes": [
                "需律师核对第10.2/10.3条对“第11.1条”的交叉引用是否为编号误植。",
            ],
            "missing_or_unclear": [
                "第10.2/10.3条对“第11.1条”的交叉引用疑与清算分配条款编号不一致。",
                "未见固定倍数回报。",
            ],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["missing_or_unclear"] == [
        "未见当前证据明确付款通知发出与交割期限起算之间的具体时间关系。",
    ]
    assert items[1]["missing_or_unclear"] == []


def test_post_polish_normalizes_esop_milestone_labels() -> None:
    items = [
        {
            "taxonomy_id": "sha.esop",
            "draft_content": (
                "里程碑及额度：(1) 首发试验星发射成功、在轨运行卫星总算力达到25POPS，完成新一轮融资后可新增10%股权。\n"
                "里程碑及额度：(2) 两颗卫星发射成功并在轨稳定工作、在轨运行卫星总算力达到100POPS以上，完成新一轮融资后可新增10%股权。\n"
                "审批要求：相关定向增资需履行审批。【待核：两项10%额度是否累计适用、审批机构占位符需确认。】"
            ),
            "review_notes": [
                "已逐项覆盖候选证据显示的两个编号里程碑。",
                "两项10%额度可能导致较高稀释，且累计关系未明，建议律师关注。",
                "未将费用、违约责任或其他非ESOP特别约定事项写入摘要。",
            ],
        }
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "首发试验星里程碑：" in draft
    assert "首发试验星里程碑：首发试验星" not in draft
    assert "两星及算力里程碑：" in draft
    assert "里程碑及额度：(1)" not in draft
    assert "协议定义" in draft
    assert items[0]["review_notes"] == ["两项10%额度可能导致较高稀释，且累计关系未明，建议律师关注。"]


def test_post_polish_splits_long_confidentiality_and_information_lines() -> None:
    items = [
        {
            "taxonomy_id": "spa.other",
            "draft_content": (
                "保密及披露：协议条件、条款、存在性及因本次增资获悉的未公开信息均属保密信息；"
                "法律、监管要求及向股东、董事、雇员、关联方、顾问、潜在投资人等披露除外，"
                "披露方应确保接收方承担不低于协议标准的保密义务。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.information_audit",
            "draft_content": (
                "信息权：公司应于每一会计年度结束后90日内提供经投资方认可会计师事务所审计的年度财务报表和审计报告；"
                "每一会计季度结束后30日内提供未经审计季度财务合并报表和季度业务报告；"
                "每一会计年度结束前30日内提供下一年度运营预算和业务计划。\n"
                "检查权：投资方可在不影响正常经营、提前5个工作日书面通知后，现场了解业务、财务和管理情况，"
                "检查及复制账簿、凭证、会议记录等资料（涉密项目除外），并可由负有保密义务的会计师、律师等辅助。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert "保密范围：" in items[0]["draft_content"]
    assert "允许披露：" in items[0]["draft_content"]
    assert "保密及披露：" not in items[0]["draft_content"]
    assert "年度报告：" in items[1]["draft_content"]
    assert "季度报告：" in items[1]["draft_content"]
    assert "预算计划：" in items[1]["draft_content"]
    assert "检查程序：" in items[1]["draft_content"]
    assert "检查程序：投资方应提前5个工作日书面通知，且不得影响公司正常经营。" in items[1]["draft_content"]
    assert "检查范围：" in items[1]["draft_content"]
    assert "顾问协助：" in items[1]["draft_content"]
    assert "信息权：" not in items[1]["draft_content"]
    assert "检查权：" not in items[1]["draft_content"]
    assert "通知后。" not in items[1]["draft_content"]


def test_post_polish_compacts_spa_other_dispute_and_notice_language() -> None:
    items = [
        {
            "taxonomy_id": "spa.other",
            "draft_content": (
                "保密及披露：各方对交易磋商、履约、尽调取得的信息及协议内容承担保密义务，期限至原提供方公开为公众所知；"
                "公开披露增资事项和细节需取得相关投资方及核心人员书面同意。\n"
                "争议解决：争议发生后15日内协商不成的，提交[地址_H]国际经济贸易仲裁委员会（[地址_H]国际仲裁中心）"
                "在[地址_H]仲裁，中文，三名仲裁员，裁决终局。\n"
                "通知送达：通知应书面作出，电子通讯成功发送、专人签收或交速递后七日视为送达；"
                "地址或邮箱变更应在5日内通知，并作为仲裁、司法文书送达地址。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "spa.other",
            "draft_content": (
                "争议解决：争议先友好协商；争议发生后15日内未解决的，任一方可提交[地址_N]仲裁委员会"
                "按届时有效仲裁规则仲裁，非争议事项继续履行。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "spa.other",
            "draft_content": "公开披露：公开披露增资事项和细节需取得相关投资方及核心人员书面同意。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    first = items[0]["draft_content"]
    assert "保密范围：各方对交易磋商、履约、尽调取得的信息及协议内容承担保密义务，期限至原提供方公开为公众所知。" in first
    assert "公开披露：增资事项和细节需取得相关投资方及核心人员书面同意。" in first
    assert "争议解决：争议发生后15日内协商不成的，提交约定仲裁机构仲裁，仲裁裁决终局。" in first
    assert "通知送达：书面通知可通过电子通讯、专人或速递送达；地址/邮箱变更应提前通知，并作为仲裁及司法文书送达地址。" in first
    assert "保密及披露：" not in first
    assert "[地址_H]国际经济贸易仲裁委员会" not in first
    assert "三名仲裁员" not in first
    assert "速递后七日视为送达" not in first

    assert items[1]["draft_content"] == "争议解决：争议先友好协商；15日内未解决的，提交约定仲裁机构仲裁，非争议事项继续履行。"
    assert items[2]["draft_content"] == "公开披露：增资事项和细节需取得相关投资方及核心人员书面同意。"


def test_post_polish_summarizes_closing_conditions_mac_line() -> None:
    items = [
        {
            "taxonomy_id": "spa.closing_conditions",
            "draft_content": (
                "内部审批：公司须完成内部审批。\n"
                "重大不利事件：不存在任何限制、禁止或致使[公司或组织_AM]本次增资无法实施的重大不利事件。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "重大不利：不得存在限制、禁止或实质阻碍本次增资实施的事件。" in draft
    assert "不存在任何限制、禁止或致使" not in draft


def test_post_polish_normalizes_already_split_inspection_procedure() -> None:
    items = [
        {
            "taxonomy_id": "sha.information_audit",
            "draft_content": (
                "检查程序：投资方可在不影响正常经营、提前5个工作日书面通知后。\n"
                "检查范围：现场了解业务、财务和管理情况，检查及复制账簿、凭证、会议记录等资料（涉密项目除外）。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert "检查程序：投资方应提前5个工作日书面通知，且不得影响公司正常经营。" in items[0]["draft_content"]
    assert "通知后。" not in items[0]["draft_content"]


def test_post_polish_splits_information_audit_two_part_reports() -> None:
    items = [
        {
            "taxonomy_id": "sha.information_audit",
            "draft_content": (
                "信息权：公司应在会计年度结束后90日内提供经审计年度合并财报，并在每季度结束后45日内提供未经审计季度合并财报；"
                "每个会计年度开始前30日内提交下一年度综合预算及年度业务计划。\n"
                "检查权：信息权人可查阅复制章程、会议记录及财务会计报告，并可在正常工作时间、不影响经营前提下查看核对公司及子公司的资产、财务账簿和经营记录。\n"
                "独立审计权：信息权人确有必要并事先书面说明后，可派内部审计人员或聘请独立审计师审计，一年不超过一次；"
                "费用原则上由信息权人承担，发现财务造假或重大审计差异时由公司承担。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "年度报告：" in draft
    assert "季度报告：" in draft
    assert "预算计划：" in draft
    assert "基础查阅：" in draft
    assert "现场检查：" in draft
    assert "审计触发：" in draft
    assert "审计频次：" in draft
    assert "费用承担：" in draft
    assert "信息权：" not in draft
    assert "检查权：" not in draft
    assert "独立审计权：" not in draft


def test_post_polish_splits_reserved_matters_and_mfn_lines() -> None:
    items = [
        {
            "taxonomy_id": "sha.board_reserved_matters",
            "draft_content": (
                "金额门槛：借款、对外投资单笔超过人民币100万元或任一财务年度累计超过人民币500万元；"
                "资产处置或设负担单笔超过人民币200万元或年度累计超过人民币500万元；"
                "预算外费用单笔超过已批准年度预算总额5%或年度累计超过10%。\n"
                "保护事项：高管任免及薪酬、审计机构聘解及会计政策变更、关联交易、员工股权/期权计划及年度发放比例、年度预算/决算、业务计划、超过门槛的借款/投资、集团外第三方贷款、担保及重大资产处置均需投资人董事同意。\n"
                "资产处置：除需股东会批准的交易外，资产、业务、股份或权益处置及设置权利负担达上述门槛，或超出已批预算和经营计划的，需投资人董事同意。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.shareholder_reserved_matters",
            "draft_content": (
                "保护事项：第(1)项覆盖修改投资人权利或设置不利限制；"
                "第(2)-(12)项覆盖章程修改、增减资及稀释性发行、减资回购注销、清算分红、"
                "重组/控制权变更、上市方案、董事会构成、主营业务重大变化、发行数字资产及其他重大事项。\n"
                "重大交易：合并、分立、重组、变更形式、控制权变更、重大资产处置、视为清算事件、上市方案亦纳入保护事项。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.mfn_special_rights",
            "draft_content": (
                "最惠国待遇：除[[公司或组织_AE]或组织_AD]另有约定外，任一[[公司或组织_AE]或组织_K]"
                "如发现现有股东，或以不高于其适用原始认购价格认缴新增注册资本的未来股东，"
                "享有优于或超出其在协议项下权利、权益或待遇的更优权利，可主张自动同等享有。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.esop",
            "draft_content": (
                "首发试验星条件：发射成功并完成新一轮融资后。\n"
                "两星及算力里程碑：两颗卫星发射成功并在轨稳定工作、在轨运行卫星总算力达到100POPS以上（FP4精度下），"
                "且以不低于投前人民币60亿元估值完成新一轮融资后，公司有权向员工持股平台定向增资，使其新增持有公司10%股权。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert "借款/投资门槛：" in items[0]["draft_content"]
    assert "资产处置门槛：" in items[0]["draft_content"]
    assert "预算外费用门槛：" in items[0]["draft_content"]
    assert "治理保护事项：" in items[0]["draft_content"]
    assert "财务/资产事项：" in items[0]["draft_content"]
    assert "达到单笔人民币50万元或12个月累计人民币100万元门槛" in items[0]["draft_content"]
    assert "达上述门槛" not in items[0]["draft_content"]
    assert "投资人权利事项：" in items[1]["draft_content"]
    assert "重大保护事项：" in items[1]["draft_content"]
    assert "重大交易：" not in items[1]["draft_content"]
    assert "适用主体：" in items[2]["draft_content"]
    assert "触发情形：" in items[2]["draft_content"]
    assert "最惠国待遇：" not in items[2]["draft_content"]
    assert "两星及算力条件：" in items[3]["draft_content"]
    assert "两星及算力增发额度：" in items[3]["draft_content"]
    assert "后。" not in items[3]["draft_content"]


def test_post_polish_compacts_mfn_and_new_project_special_rights() -> None:
    items = [
        {
            "taxonomy_id": "sha.mfn_special_rights",
            "draft_content": (
                "新项目投资安排：清算事件发生且投资人所得不超过清算优先款的，自清算事件发生日起10年内，"
                "如义务人直接或间接从事新项目且投资人拟投资，清算优先款与已得款项差额视为投资人对新项目的投资。\n"
                "取得权益方式：义务人应按投资人认可的新项目届时估值，通过零对价转让或增发股权，使投资人取得等值股权或其他权益。\n"
                "适用范围：新项目包括义务人通过自身或关联方名义，单独或联合其他主体作为主要管理者之一，"
                "创办新企业、实体或并购存续企业等，且须独立于公司。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.mfn_special_rights",
            "draft_content": (
                "触发情形：如发现现有股东，或以不高于其适用原始认购价格认缴新增注册资本的未来股东，"
                "享有优于或超出其在协议项下权利、权益或待遇的更优权利。\n"
                "席位例外：最惠国待遇不适用于[[公司或组织_AE]或组织_G]及本轮领投方[商标品牌_G]基于投资比例享有的"
                "[[公司或组织_AE]或组织_AM]席位及相应表决权。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    new_project = items[0]["draft_content"]
    assert "触发条件：清算事件发生且投资人所得不超过清算优先款。" in new_project
    assert "投资期限：清算事件发生日起10年内。" in new_project
    assert "新项目条件：义务人直接或间接从事新项目且投资人拟投资。" in new_project
    assert "投资金额：清算优先款与已得款项差额视为投资人对新项目的投资。" in new_project
    assert "取得权益：按投资人认可的新项目估值，通过零对价转让或增发股权取得等值权益。" in new_project
    assert "新项目范围：义务人以自身或关联方名义，单独或联合他方作为主要管理者参与创设、并购的独立项目。" in new_project
    assert "新项目投资安排：" not in new_project
    assert "取得权益方式：" not in new_project

    mfn = items[1]["draft_content"]
    assert "触发情形：现有股东或低价新股东取得更优权利/待遇时触发。" in mfn
    assert (
        "席位例外：[[公司或组织_AE]或组织_G]及本轮领投方[商标品牌_G]按投资比例享有的"
        "[[公司或组织_AE]或组织_AM]席位及相应表决权不适用最惠国。"
    ) in mfn
    assert "以不高于其适用原始认购价格认缴新增注册资本" not in mfn
    assert "最惠国待遇不适用于" not in mfn


def test_post_polish_splits_remaining_long_substantive_lines() -> None:
    items = [
        {
            "taxonomy_id": "spa.closing",
            "draft_content": (
                "付款及交割：第四条先决条件满足或被投资方书面豁免后10个工作日内，或另行书面约定时间，"
                "各投资方分别向公司指定专用账户足额付款；足额支付即构成交割，付款完成日为交割日，各投资方付款义务及交割相互独立。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "spa.compliance",
            "draft_content": (
                "廉洁合规：公司、相关主体及其董事、管理人员、雇员在代表公司行事过程中不得参与腐败、贿赂、行贿，"
                "包括商业贿赂及向政府部门或官员提供财物或其他利益以影响决策，并须遵守适用反腐败、反贿赂及反洗钱规则。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.drag_along",
            "draft_content": (
                "领售触发：天使轮增资交割日起满3年后，如第三方收购公司全部或实质上全部业务/资产，"
                "或发生并购、重组等导致实际控制权变更交易，且公司整体估值不低于人民币2,488,078,800元，可触发领售安排。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.liquidation_preference",
            "draft_content": (
                "清算事件：公司解散、清算、破产及视为清算事件触发优先清算；视为清算事件包括控制权变更、50%以上表决权转移、"
                "全部或实质全部资产或业务处置，以及全部或实质全部知识产权排他许可或出售，参与该事件的优先清算权人一致同意可豁免。\n"
                "清算顺位及金额：依法清偿法定优先款项及债务后，剩余财产先向本轮优先清算权人支付本轮优先清算额，"
                "再向天使轮优先清算权人支付天使轮优先清算额；优先清算额为增资款加已宣布未付股息，不足时同顺位按应得金额比例分配。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.anti_dilution",
            "draft_content": (
                "触发及方式：交割日后公司发生新融资，且新增股东取得新增注册资本的新认购价格低于反稀释权人原始认购价格的，"
                "反稀释权人可要求按广义加权平均方式调整原始认购价格，公式为P2=P1*(A+B)/(A+C)。\n"
                "替代安排：无法实施时，反稀释权人可选择由组织_C无偿或象征性价格转让股权，或由公司现金补偿并用于对公司增资。"
            ),
            "source_evidence": [
                "无法实施时，反稀释权人可选择由[[公司或组织_AE]或组织_C]无偿或象征性价格转让股权。",
            ],
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.dividend",
            "draft_content": (
                "投资方优先：公司批准分配利润时，组织_H和组织_C应采取必要行动，确保组织_K优先于其他股东取得按两种方式计算金额中较高者确定的优先分红额；"
                "如因法律限制不能实现，获益股东应向受损组织_K让与相应比例金额。"
            ),
            "source_evidence": [
                "公司批准分配利润时，[[公司或组织_AE]或组织_H]和[[公司或组织_AE]或组织_C]应采取必要行动，确保[[公司或组织_AE]或组织_K]优先取得优先分红额。",
            ],
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert "付款期限：" in items[0]["draft_content"]
    assert "交割日：" in items[0]["draft_content"]
    assert "廉洁承诺：" in items[1]["draft_content"]
    assert "合规要求：" in items[1]["draft_content"]
    assert "触发时间：" in items[2]["draft_content"]
    assert "触发交易：" in items[2]["draft_content"]
    assert "估值门槛：" in items[2]["draft_content"]
    assert "法定清算事件：" in items[3]["draft_content"]
    assert "视同清算事件：" in items[3]["draft_content"]
    assert "清算顺位：" in items[3]["draft_content"]
    assert "优先清算额：" in items[3]["draft_content"]
    assert "触发情形：" in items[4]["draft_content"]
    assert "调整方式：" in items[4]["draft_content"]
    assert not BARE_ORG_PLACEHOLDER_RE.search(items[4]["draft_content"])
    assert "[[公司或组织_AE]或组织_C]" in items[4]["draft_content"]
    assert "分红协助义务：" in items[5]["draft_content"]
    assert "优先分红：" in items[5]["draft_content"]
    assert "法律限制补偿：" in items[5]["draft_content"]
    assert not BARE_ORG_PLACEHOLDER_RE.search(items[5]["draft_content"])
    assert "[[公司或组织_AE]或组织_H]" in items[5]["draft_content"]
    assert "[[公司或组织_AE]或组织_C]" in items[5]["draft_content"]
    assert "[[公司或组织_AE]或组织_K]" in items[5]["draft_content"]


def test_post_polish_compacts_anti_dilution_formula_and_compensation_lines() -> None:
    items = [
        {
            "taxonomy_id": "sha.anti_dilution",
            "draft_content": (
                "触发情形：公司以低于任一反稀释权人每单位认购价格进行增资扩股时触发，并以满足协议第1.1.5条及第1.2.4条为前提。\n"
                "调整方式：反稀释权人持股比例按投资总额、调整后每单位认购价格及低价增资后总注册资本计算；"
                "可由公司以人民币1元名义价格或法律允许最低对价发行股权，或由相关股东以同等低价转让所需股权。\n"
                "例外事项：员工激励或股权薪酬计划、经股东会通过的利润转增注册资本或资本公积转增股本、"
                "股份制改制转换、合格上市发行及类似证券发行等不适用。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.anti_dilution",
            "draft_content": (
                "触发及方式：交割日后公司发生新融资，且新增股东取得新增注册资本的新认购价格低于反稀释权人原始认购价格的，"
                "反稀释权人可要求按广义加权平均方式调整原始认购价格，公式为P2=P1*(A+B)/(A+C)。\n"
                "调整及补偿：按调整后认购价格重新确定反稀释权人在前轮融资中应取得的注册资本额；"
                "公司以无偿或象征性价格增发股权，或以经反稀释权人事先书面同意的其他合法方式补足。\n"
                "替代安排：无法实施时，反稀释权人可选择由[[公司或组织_AE]或组织_C]无偿或象征性价格转让股权，"
                "或由公司现金补偿并用于对公司增资。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.anti_dilution",
            "draft_content": "调整计算：按投资总额、调整后每单位认购价格及低价增资后总注册资本计算。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)
    apply_post_polish_quality_guards(items)

    price_reset = items[0]["draft_content"]
    assert "触发情形：公司以低于任一反稀释权人每单位认购价格进行增资扩股时触发。" in price_reset
    assert "触发前提：满足协议第1.1.5条及第1.2.4条。" in price_reset
    assert "调整计算：按投资总额、调整后每单位认购价格及低价增资后总注册资本确定持股比例。" in price_reset
    assert "补足方式：公司以人民币1元名义价格或法律允许最低对价发行股权，或相关股东以同等低价转让所需股权。" in price_reset
    assert "例外事项：员工激励/股权薪酬、利润或资本公积转增、股份制改制、合格上市发行及类似证券发行不适用。" in price_reset
    assert "调整方式：" not in price_reset

    weighted_average = items[1]["draft_content"]
    assert "触发情形：交割后新融资价格低于反稀释权人原始认购价格。" in weighted_average
    assert "调整方式：按广义加权平均方式调整原始认购价格。" in weighted_average
    assert "调整结果：按调整后认购价格重新确定反稀释权人在前轮融资中应取得的注册资本额。" in weighted_average
    assert "补足方式：公司以无偿/象征性价格增发股权，或以反稀释权人同意的其他合法方式补足。" in weighted_average
    assert "替代股权补偿：无法实施时，可由[[公司或组织_AE]或组织_C]无偿/象征性价格转让股权。" in weighted_average
    assert "替代现金补偿：公司现金补偿并用于对公司增资。" in weighted_average
    assert "公式为" not in weighted_average
    assert "调整及补偿：" not in weighted_average
    assert "替代安排：" not in weighted_average

    assert items[2]["draft_content"] == "调整计算：按投资总额、调整后每单位认购价格及低价增资后总注册资本确定持股比例。"


def test_post_polish_compacts_compliance_kts_language() -> None:
    items = [
        {
            "taxonomy_id": "spa.compliance",
            "draft_content": (
                "禁止行为：禁止项目公司方向[公司或组织_AJ]及其关联方、董事、高管、员工等相关人员直接或间接提供或承诺提供现金、现金等价物、礼品及其他利益；合法合理的小额公务招待及广告礼品除外。\n"
                "利益安排：除投资合作及经事先书面同意的其他业务合作外，[公司或组织_AO]与[公司或组织_AJ]之间不得存在代持、利益输送、资金往来等利益安排。\n"
                "违约后果：[公司或组织_AO]违反第6.1.5条的，[公司或组织_AJ]可终止投资合作关系、单方解除协议，并要求[公司或组织_AO]履行回购义务及由公司支付已付增资价款10%的违约金。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "廉洁承诺：项目公司方不得向投资方相关人员提供或承诺提供现金、礼品或其他不当利益；合理小额公务招待及广告礼品除外。\n"
        "利益安排：除投资合作及经同意合作外，不得存在代持、利益输送、资金往来等利益安排。\n"
        "违约后果：违反廉洁条款时，投资方可终止合作、解除协议，并要求回购及由公司支付已付增资价款10%的违约金。"
    )
    assert "现金等价物" not in items[0]["draft_content"]
    assert "第6.1.5条" not in items[0]["draft_content"]


def test_post_polish_compacts_termination_kts_language() -> None:
    items = [
        {
            "taxonomy_id": "spa.termination",
            "draft_content": (
                "违约解除：任一方根本违约致使协议目的无法实现的，任一非违约方可依法解除；一般违约经通知后30日内未补救或补救仍不符合约定的，守约方可通知解除。\n"
                "协商终止：各方经协商一致可终止本协议。\n"
                "工商变更未完成：若[公司或组织_AM]无法按协议约定时限办理完毕本次增资相关工商变更登记手续，[公司或组织_BH]有权单方面解除协议。"
            ),
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "违约解除：根本违约致协议目的无法实现时可依法解除；一般违约经通知后30日未有效补救的，守约方可解除。\n"
        "协商解除：各方协商一致可解除/终止。\n"
        "工商变更未完成：公司未按期完成本次增资工商变更登记的，投资方可单方解除。"
    )
    assert "本协议" not in items[0]["draft_content"]
    assert "若[公司或组织_AM]" not in items[0]["draft_content"]


def test_post_polish_compacts_preemptive_right_kts_language() -> None:
    items = [
        {
            "taxonomy_id": "sha.preemptive_right",
            "draft_content": (
                "认购权：公司增加注册资本、发行新股或后续融资时，相关投资人享有按其在公司持股比例认购新增注册资本或新发股权的优先权，认购价格、条款和条件应与其他潜在投资方、认购方实质相同。\n"
                "例外：公司为实施经股东会批准的员工持股计划而新增注册资本，以及因协议批准的股票分拆、股息支付和类似交易而发行股权或股份，不适用优先认购权。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.preemptive_right",
            "draft_content": (
                "认购比例：[[公司或组织_AE]或组织_K]及/或其合格关联方可按持股比例优先认购新增注册资本，额度以拟新增注册资本总额乘以其持股占届时全体股东持股总和的比例计算。\n"
                "二次认购：首次未足额认购的，已完全行权的权利人可按其在超额认购权人中的持股比例认购剩余新增注册资本，并可继续认购至售罄或无人继续行权。\n"
                "例外情形：优先认购权不适用于经第8条批准的员工股权/期权激励计划、反稀释保护项下增资及利润或资本公积等比例转增注册资本。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "认购权：相关投资人可按持股比例优先认购新增注册资本/新发股权，认购条件与第三方实质相同。\n"
        "例外事项：经批准的员工持股计划、股票分拆、股息支付及类似交易不适用。"
    )
    assert items[1]["draft_content"] == (
        "认购比例：权利人及/或合格关联方可按届时持股比例优先认购新增注册资本。\n"
        "二次认购：首次未足额认购时，已足额行权的权利人可按比例继续认购剩余额度。\n"
        "例外事项：员工股权/期权激励、反稀释保护及利润或资本公积转增等不适用。"
    )
    assert "拟新增注册资本总额乘以" not in items[1]["draft_content"]


def test_post_polish_compacts_rofr_tag_formula_language() -> None:
    items = [
        {
            "taxonomy_id": "sha.rofr_tag",
            "draft_content": (
                "共同出售权：未行使或放弃优先购买权的投资人可在购买回复期届满前发出共售通知；"
                "共售数量按“拟售股权数×共售股东持股注册资本/(转股方持股注册资本+实际共售股东持股注册资本总和)”计算。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.rofr_tag",
            "draft_content": (
                "共售比例及控制权变更：一般共售数量按未被优先购买股权乘以其持股占卖方及全体拟共售权人持股总和的比例计算；"
                "如出售导致[公司或组织_AE]控制权变更，共售权人可出售其持有的全部股权。"
            ),
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "共同出售权：未行使或放弃优先购买权的投资人可在购买回复期届满前发出共售通知，"
        "并按转股方及实际共售方持股口径计算的约定比例共同出售。"
    )
    assert items[1]["draft_content"] == (
        "共售比例及控制权变更：一般共售按卖方及拟共售权人持股比例计算；"
        "如出售导致控制权变更，共售权人可出售其全部股权。"
    )
    assert "拟售股权数×" not in items[0]["draft_content"]
    assert "一般共售数量按" not in items[1]["draft_content"]


def test_post_polish_compacts_transfer_restriction_internal_references() -> None:
    items = [
        {
            "taxonomy_id": "sha.transfer_restriction",
            "draft_content": (
                "受限转让：合格上市前，乙方四、乙方五、乙方六或丁方向第三方转让公司股权或接受购买要约，须经全体投资人同意。\n"
                "适用前提：上述转让另以遵守增资协议及第3.2条为前提。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.transfer_restriction",
            "draft_content": "同意门槛：上述转让或处分须经[[公司或组织_AE]或组织_G]和[商标品牌_G]事先书面同意。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    assert items[0]["draft_content"] == (
        "受限转让：合格上市前，特定现有股东/创始方或持股平台向第三方转让公司股权或接受购买要约，须经全体投资人同意。\n"
        "适用前提：受限转让仍须遵守增资协议及相关转股程序。"
    )
    assert items[1]["draft_content"] == "同意门槛：受限转让或处分须经[[公司或组织_AE]或组织_G]和[商标品牌_G]事先书面同意。"
    assert "乙方四" not in items[0]["draft_content"]
    assert "上述转让" not in items[0]["draft_content"] + items[1]["draft_content"]


def test_post_polish_normalizes_transaction_capital_and_signing_lines() -> None:
    items = [
        {
            "taxonomy_id": "spa.transaction_arrangement",
            "draft_content": (
                "交易安排：公司投前估值为10亿元；本轮融资额为人民币170,000,000元。\n"
                "注册资本及股权结构：签署日注册资本为7,950,852.25元；增资完成后合计9,302,497.12元，新增[公司或组织_L]持股2.56%、[公司或组织_V]持股0.85%。\n"
                "签署方：协议由甲方、现有股东、创始股东及其他各方共同订立。"
            ),
            "extracted_facts": {
                "field_values": [
                    {
                        "key": "capital_change",
                        "label": "注册资本变化",
                        "status": "found",
                        "value": "截至签署日注册资本为人民币7,950,852.25元；本次增资完成后认缴出资合计为人民币9,302,497.12元，新增人民币1,351,644.87元。",
                    }
                ]
            },
            "review_notes": [],
        }
    ]

    apply_post_polish_quality_guards(items)

    draft = items[0]["draft_content"]
    assert "注册资本变化：签署日注册资本为人民币7,950,852.25元；本次增资新增注册资本人民币1,351,644.87元；增资完成后注册资本为人民币9,302,497.12元。" in draft
    assert "新增[公司或组织_L]持股" not in draft
    assert "签署方：由本轮投资方（甲方）、现有股东、公司及创始股东等共同签署。" in draft


def test_post_polish_keeps_export_lines_readable_for_dense_kts_items() -> None:
    items = [
        {
            "taxonomy_id": "spa.transaction_arrangement",
            "group": "SPA",
            "label": "本次交易安排",
            "draft_content": (
                "主要投资方：[[公司或组织_AF]或组织_X]人民币30,870,000元、"
                "[[公司或组织_AF]或组织_Y]人民币28,230,000元、[商标品牌_D]人民币23,000,000元；"
                "其余7名合计人民币89,919,700元。"
            ),
            "extracted_facts": {},
            "review_notes": [],
        },
        {
            "taxonomy_id": "spa.liability",
            "group": "SPA",
            "label": "违约责任",
            "draft_content": (
                "一般赔偿：[公司或组织_AF]及[[公司或组织_AF]或组织_AB]就违反协议约定向投资方及其关联方等受偿方赔偿，使其免受损害；"
                "书面豁免情形除外。任何一方违约时，其他方亦可要求实际且全面履行。\n"
                "连带责任：各增资人仅就自身行为负责，不为其他增资人承担连带保证或连带赔偿责任；"
                "各公司方在协议项下责任和义务为共同且连带；投资方付款义务分别且不连带。\n"
                "特殊赔偿：未如实反映的现实或潜在债务由[[公司或组织_AF]或组织_AB]承担偿还及赔偿；"
                "架构调整导致投资方未来退出税基成本低于增资款的，[[公司或组织_AF]或组织_AA]连带补偿税赋成本增加，投资方未严格配合导致损失的除外。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.transfer_restriction",
            "group": "SHA",
            "label": "股权转让限制",
            "draft_content": (
                "受限主体及期间：自天使轮增资交割日至首次公开发行之日止，任何[[公司或组织_AE]或组织_C]，"
                "包括[[公司或组织_AE]或组织_AL]作为持股平台合伙人，未经同意不得实施受限转让或处分。\n"
                "允许例外：员工股权/期权激励计划、反稀释保护权、第9条回购权及经[[公司或组织_AE]或组织_K]事先书面同意的股权转让不受该限制；"
                "婚姻关系变动或继承等导致的持股实体层面处置不视为间接转让。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.redemption",
            "group": "SHA",
            "label": "特殊回购权",
            "draft_content": (
                "逾期责任及顺位：逾期未足额支付的，未付金额按6%年单利计违约金且继续履行；"
                "多名回购权人行权时，先[[公司或组织_AE]或组织_AA]、后[[公司或组织_AE]或组织_X]，同顺位资金不足按应付金额比例分配。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.mfn_special_rights",
            "group": "SHA",
            "label": "最惠国及特殊投资人权利",
            "draft_content": (
                "例外范围：最惠国待遇不适用于[[公司或组织_AE]或组织_G]及本轮领投方[商标品牌_G]基于投资比例享有的"
                "[[公司或组织_AE]或组织_AM]席位及相应表决权，战略方和产业方优先业务合作权利，以及后轮更高估值投资人的经济型权益优先顺位。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.esop",
            "group": "SHA",
            "label": "ESOP特别约定",
            "draft_content": "审批要求：员工股权激励计划、实质修订及上述定向增资需按协议由股东会或投资人批准，并符合公司法及章程。【注：两项10%额度是否累计适用、审批机构口径可结合协议定义确认。】",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)
    rows = export_items({"items": items})
    exported_lines = [line for row in rows for line in row["content_lines"]]

    assert all(len(line) <= 95 for line in exported_lines)
    assert "主要投资方1：" in items[0]["draft_content"]
    assert "未披露债务：" in items[1]["draft_content"]
    assert "违约赔偿：" in items[1]["draft_content"]
    assert "一般赔偿：" not in items[1]["draft_content"]
    assert "责任独立性：" in items[1]["draft_content"]
    assert "公司/创始人连带责任：各增资人仅就自身行为负责" not in items[1]["draft_content"]
    assert "限制期间：" in items[2]["draft_content"]
    assert "清偿顺位：" in items[3]["draft_content"]
    assert "后轮经济权益例外：" in items[4]["draft_content"]
    assert any(line.startswith("【注：") for line in exported_lines)


def test_post_polish_splits_inline_notes_and_liquidation_special_arrangements() -> None:
    items = [
        {
            "taxonomy_id": "sha.liquidation_preference",
            "draft_content": (
                "剩余及特殊安排：清算优先款付清后，剩余财产按全体股东出资比例分配；"
                "如清算所得不超过清算优先款，清算事件起10年内特定新项目下差额可视为投资并取得等值权益。\n"
                "特殊安排：法定分配偏离约定时由超额取得方再分配；"
                "如清算所得不超过清算优先款，10年内特定新项目下差额可视为投资并取得等值权益。"
            ),
            "review_notes": [],
        },
        {
            "taxonomy_id": "sha.esop",
            "draft_content": "合规要求：符合公司法及章程。【注：两项10%额度是否累计适用。】。",
            "review_notes": [],
        },
    ]

    apply_post_polish_quality_guards(items)

    liquidation = items[0]["draft_content"]
    assert "剩余分配：" in liquidation
    assert "法定分配偏离：" in liquidation
    assert liquidation.count("法定分配偏离：") == 1
    assert liquidation.count("新项目补偿：") == 1
    assert "剩余及特殊安排：" not in liquidation
    assert "特殊安排：" not in liquidation

    apply_post_polish_quality_guards(items)
    liquidation = items[0]["draft_content"]
    assert liquidation.count("法定分配偏离：") == 1
    assert liquidation.count("新项目补偿：") == 1

    esop = items[1]["draft_content"].splitlines()
    assert esop == [
        "合规要求：符合公司法及章程。",
        "【注：两项10%额度是否累计适用。】",
    ]


if __name__ == "__main__":
    test_anti_dilution_price_reset_guard()
    test_redemption_compliance_trigger_guard()
    test_redemption_guard_does_not_duplicate_existing_trigger_line()
    test_absence_ok_required_field_counts_as_handled()
    test_mergeable_core_output_policy_is_explicit_and_not_skipped()
    test_representations_guard_fills_transition_covenant()
    test_redemption_price_formula_guard_fills_both_formulas()
    test_dividend_guard_fills_special_approval_threshold()
    test_post_polish_compacts_dividend_approval_references()
    test_complete_soft_review_status_normalizes_to_drafted()
    test_complete_hard_review_status_stays_needs_review()
    test_drafted_hard_review_status_upgrades_to_needs_review()
    test_not_configured_schema_does_not_force_needs_review()
    test_refresh_final_statuses_demotes_soft_drafted_review_notes()
    test_refresh_final_statuses_trims_drafted_lawyer_notes_by_priority()
    test_residual_rights_fallback_prevents_empty_sha_other_content()
    test_post_polish_converts_sha_other_note_only_absence_to_kts_lines()
    test_sha_other_absence_policy_counts_missing_rights_as_handled()
    test_docx_export_skips_empty_conditional_items_only()
    test_docx_export_skips_empty_absence_check_items()
    test_docx_export_keeps_absence_check_content()
    test_docx_export_keeps_pending_check_marker_unnumbered()
    test_spa_other_workpaper_tone_is_cleaned()
    test_post_closing_covenants_guard_compacts_overlong_summary()
    test_style_polish_payload_includes_fields_and_review_context()
    test_style_polish_validation_allows_removing_workpaper_note()
    test_candidate_context_centers_on_source_quote()
    test_post_polish_deduplicates_redemption_trigger_lines()
    test_post_polish_splits_redemption_exercise_and_payment_deadlines()
    test_transaction_arrangement_adds_header_and_cap_table_candidates()
    test_transaction_arrangement_guard_fills_signing_parties_and_cap_table()
    test_rofr_tag_adds_sha_definition_candidate()
    test_board_composition_guard_removes_client_identity_blocker()
    test_post_polish_splits_board_composition_long_line()
    test_rofr_tag_guard_resolves_ap_ak_alias()
    test_rofr_tag_guard_fills_tag_along_terms()
    test_anti_dilution_guard_fills_complete_exception_list()
    test_representations_core_guard_fills_authority_and_capital_legality()
    test_representations_core_guard_cleans_stale_lawyer_notes()
    test_representations_core_guard_deduplicates_existing_legality_lines()
    test_shareholder_reserved_guard_resolves_ap_required_matters()
    test_shareholder_reserved_guard_resolves_dual_majority_mechanism()
    test_shareholder_reserved_guard_removes_client_veto_practicality_blocker()
    test_liquidation_preference_guard_fills_events_and_new_project()
    test_liquidation_preference_guard_cleans_stale_lawyer_notes()
    test_post_polish_liquidation_review_focuses_cross_reference_issue()
    test_founder_obligations_guard_completes_service_and_non_compete_summary()
    test_post_polish_guard_rewrites_founder_stale_review_tone()
    test_post_polish_splits_founder_service_long_line()
    test_post_polish_removes_nonblocking_workpaper_review_notes()
    test_post_polish_deduplicates_missing_notes_already_in_review_notes()
    test_post_polish_normalizes_esop_milestone_labels()
    test_post_polish_splits_long_confidentiality_and_information_lines()
    test_post_polish_compacts_spa_other_dispute_and_notice_language()
    test_post_polish_splits_reserved_matters_and_mfn_lines()
    test_post_polish_compacts_mfn_and_new_project_special_rights()
    test_post_polish_splits_remaining_long_substantive_lines()
    test_post_polish_compacts_anti_dilution_formula_and_compensation_lines()
    test_post_polish_compacts_compliance_kts_language()
    test_post_polish_compacts_termination_kts_language()
    test_post_polish_compacts_preemptive_right_kts_language()
    test_post_polish_compacts_rofr_tag_formula_language()
    test_post_polish_compacts_transfer_restriction_internal_references()
    test_post_polish_normalizes_transaction_capital_and_signing_lines()
    test_post_polish_keeps_export_lines_readable_for_dense_kts_items()
    test_post_polish_splits_inline_notes_and_liquidation_special_arrangements()
    print("ok")
