#!/usr/bin/env python3
"""Local browser server for Legal Workbench."""

from __future__ import annotations

import argparse
import email.policy
import json
import mimetypes
import re
import sys
import tempfile
import threading
import time
import webbrowser
from difflib import SequenceMatcher
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ai_client import test_connection
from capability_registry import get_capability, load_capabilities
from config import APP_NAME, APP_VERSION, DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR, ensure_runtime_dirs
from docx_parser import DocxParseError, parse_docx_file
from kts_extractor import build_kts_candidates, build_kts_extraction
from source_index import build_source_index
from work_state import (
    load_current_kts_candidates,
    load_current_kts_extraction,
    load_current_parse,
    load_current_source_index,
    save_current_kts_candidates,
    save_current_kts_extraction,
    save_current_parse,
    save_current_source_index,
    timestamp,
)


MAX_UPLOAD_BYTES = 120 * 1024 * 1024
REVIEW_STATUSES = {"pending", "ai_reviewed", "confirmed"}
MAX_REVIEW_CONTENT_CHARS = 20000
MAX_REVIEW_NOTE_CHARS = 4000
PUBLIC_EVIDENCE_TEXT_SIMILARITY_DUPLICATE_RATIO = 0.82
PUBLIC_EVIDENCE_QUOTE_SIMILARITY_DUPLICATE_RATIO = 0.86
MIN_PUBLIC_EVIDENCE_SIMILARITY_CHARS = 80
RUN_PROGRESS: dict[str, dict[str, object]] = {}
RUN_PROGRESS_LOCK = threading.Lock()


def clamp_percent(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def start_run_progress(run_id: str, file_count: int) -> None:
    if not run_id:
        return
    now = timestamp()
    with RUN_PROGRESS_LOCK:
        RUN_PROGRESS[run_id] = {
            "run_id": run_id,
            "status": "running",
            "stage": "queued",
            "stage_label": "准备处理",
            "stage_index": 0,
            "stage_count": 4,
            "progress_percent": 1,
            "file_count": file_count,
            "parsed_file_count": 0,
            "completed_items": 0,
            "total_items": 0,
            "started_at": now,
            "started_at_epoch": time.time(),
            "updated_at": now,
            "message": "已收到文件，准备开始处理。",
        }


def update_run_progress(run_id: str, **fields: object) -> None:
    if not run_id:
        return
    with RUN_PROGRESS_LOCK:
        current = RUN_PROGRESS.setdefault(
            run_id,
            {
                "run_id": run_id,
                "status": "running",
                "started_at": timestamp(),
                "started_at_epoch": time.time(),
            },
        )
        current.update(fields)
        if "progress_percent" in current:
            current["progress_percent"] = clamp_percent(float(current["progress_percent"]))  # type: ignore[arg-type]
        current["updated_at"] = timestamp()


def get_run_progress(run_id: str) -> dict[str, object] | None:
    with RUN_PROGRESS_LOCK:
        progress = RUN_PROGRESS.get(run_id)
        if progress is None:
            return None
        public = dict(progress)
    started_at_epoch = public.get("started_at_epoch")
    if isinstance(started_at_epoch, (int, float)):
        public["elapsed_seconds"] = max(0, int(time.time() - started_at_epoch))
    public.pop("started_at_epoch", None)
    return public


def json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def public_source_index(record: dict[str, object]) -> dict[str, object]:
    documents = []
    raw_documents = record.get("documents", [])
    if not isinstance(raw_documents, list):
        raw_documents = []
    for document in raw_documents:
        if not isinstance(document, dict):
            continue
        documents.append(
            {
                "file_name": document.get("file_name", ""),
                "document_type": document.get("document_type", {}),
                "raw_block_count": document.get("raw_block_count", 0),
                "search_shard_count": document.get("search_shard_count", 0),
                "warning_count": len(document.get("warnings", []))
                if isinstance(document.get("warnings", []), list)
                else 0,
            }
        )
    return {
        "updated_at": record.get("updated_at", ""),
        "phase": record.get("phase", ""),
        "summary": record.get("summary", {}),
        "documents": documents,
    }


def public_kts_candidates(record: dict[str, object]) -> dict[str, object]:
    return {
        "updated_at": record.get("updated_at", ""),
        "phase": record.get("phase", ""),
        "taxonomy_id": record.get("taxonomy_id", ""),
        "taxonomy_version": record.get("taxonomy_version", ""),
        "summary": record.get("summary", {}),
    }


def public_documents_from_parse_record(parse_record: dict[str, object] | None) -> list[dict[str, object]]:
    if parse_record is None:
        return []
    result = parse_record.get("result", {})
    if not isinstance(result, dict):
        return []
    raw_documents = result.get("documents", [])
    if not isinstance(raw_documents, list):
        return []

    public_documents: list[dict[str, object]] = []
    for document in raw_documents:
        if not isinstance(document, dict):
            continue
        public_document = {
            "file_name": document.get("file_name", ""),
            "file_size": document.get("file_size", 0),
            "status": document.get("status", ""),
        }
        if document.get("document_type"):
            public_document["document_type"] = document["document_type"]
        if document.get("error"):
            public_document["error"] = document["error"]
        public_documents.append(public_document)
    return public_documents


def public_current_kts_review(
    parse_record: dict[str, object] | None,
    source_index_record: dict[str, object] | None,
    candidates_record: dict[str, object] | None,
    extraction_record: dict[str, object],
) -> dict[str, object]:
    parse_result = parse_record.get("result", {}) if isinstance(parse_record, dict) else {}
    if not isinstance(parse_result, dict):
        parse_result = {}
    capability_id = "spa_sha_kts"
    if isinstance(parse_record, dict):
        capability_id = str(parse_record.get("capability_id") or capability_id)

    current = {
        "updated_at": extraction_record.get("updated_at", ""),
        "capability_id": capability_id,
        "phase": extraction_record.get("phase", "v0.5-kts-review-current"),
        "kts_extraction": public_kts_extraction(extraction_record),
        "result": {
            "status": parse_result.get("status", "parsed"),
            "message": "已恢复上次 KTS 复核结果。",
            "capability": parse_result.get("capability", {}),
            "documents": public_documents_from_parse_record(parse_record),
        },
    }
    if source_index_record is not None:
        current["source_index"] = public_source_index(source_index_record)
    if candidates_record is not None:
        current["kts_candidates"] = public_kts_candidates(candidates_record)
    return current


def normalize_review_status(value: object) -> str:
    status = str(value or "pending").strip()
    return status if status in REVIEW_STATUSES else "pending"


def clamp_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def evidence_quality(item: dict[str, object]) -> dict[str, int]:
    source_evidence = item.get("source_evidence", [])
    if not isinstance(source_evidence, list):
        source_evidence = []

    verified_quote_count = 0
    quote_count = 0
    for evidence in source_evidence:
        if not isinstance(evidence, dict):
            continue
        quote = str(evidence.get("quote") or "").strip()
        if quote:
            quote_count += 1
        if quote and evidence.get("verified"):
            verified_quote_count += 1
    return {
        "evidence_count": len(source_evidence),
        "quote_count": quote_count,
        "verified_quote_count": verified_quote_count,
    }


def public_kts_confidence(item: dict[str, object]) -> dict[str, str]:
    quality = evidence_quality(item)
    status = str(item.get("status") or "").strip()
    draft_content = str(item.get("draft_content") or "").strip()

    if status == "drafted" and draft_content and quality["verified_quote_count"] > 0:
        return {
            "confidence_level": "high",
            "confidence_label": "高",
            "confidence_reason": "已形成内容摘要，且至少有一条已核验原文依据。",
        }
    if draft_content and quality["verified_quote_count"] > 0:
        return {
            "confidence_level": "medium",
            "confidence_label": "中",
            "confidence_reason": "已有内容摘要和已核验原文依据，但仍需要律师确认表述。",
        }
    if draft_content and quality["quote_count"] > 0:
        return {
            "confidence_level": "medium",
            "confidence_label": "中",
            "confidence_reason": "已有内容摘要和候选原文依据，但证据尚未全部核验。",
        }
    if quality["evidence_count"] > 0:
        return {
            "confidence_level": "low",
            "confidence_label": "低",
            "confidence_reason": "仅找到候选证据，尚未形成稳定内容摘要。",
        }
    if status == "unclear":
        return {
            "confidence_level": "low",
            "confidence_label": "低",
            "confidence_reason": "原文中未识别到足够明确的对应约定。",
        }
    return {
        "confidence_level": "low",
        "confidence_label": "低",
        "confidence_reason": "缺少可直接采用的摘要或原文依据。",
    }


def public_human_review(item: dict[str, object], default_status: str = "pending") -> dict[str, object]:
    review = item.get("human_review", {})
    if not isinstance(review, dict):
        review = {}
    has_saved_review = any(key in review for key in ("status", "content", "note", "updated_at"))
    review_status = normalize_review_status(review.get("status") if has_saved_review else default_status)
    return {
        "review_status": review_status,
        "review_content": str(review.get("content") or ""),
        "review_note": str(review.get("note") or ""),
        "review_updated_at": str(review.get("updated_at") or ""),
        "review_is_default": not has_saved_review,
    }


def normalize_evidence_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def public_evidence_similarity(left: str, right: str) -> float:
    if (
        len(left) < MIN_PUBLIC_EVIDENCE_SIMILARITY_CHARS
        or len(right) < MIN_PUBLIC_EVIDENCE_SIMILARITY_CHARS
    ):
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def is_duplicate_evidence_quote(normalized_quote: str, seen_quotes: list[str]) -> bool:
    if not normalized_quote:
        return True
    for seen_quote in seen_quotes:
        if normalized_quote == seen_quote:
            return True
        if len(normalized_quote) >= 30 and len(seen_quote) >= 30:
            if normalized_quote in seen_quote or seen_quote in normalized_quote:
                return True
    return False


def is_duplicate_public_evidence(
    normalized_quote: str,
    normalized_context: str,
    source_block_ids: set[str],
    seen_evidence: list[tuple[str, str, set[str]]],
) -> bool:
    for seen_quote, seen_context, seen_block_ids in seen_evidence:
        if is_duplicate_evidence_quote(normalized_quote, [seen_quote]):
            return True
        if (
            public_evidence_similarity(normalized_quote, seen_quote)
            >= PUBLIC_EVIDENCE_QUOTE_SIMILARITY_DUPLICATE_RATIO
        ):
            return True
        if (
            public_evidence_similarity(normalized_context, seen_context)
            >= PUBLIC_EVIDENCE_TEXT_SIMILARITY_DUPLICATE_RATIO
        ):
            return True
        if source_block_ids and seen_block_ids:
            if source_block_ids & seen_block_ids:
                return True
    return False


def public_source_block_ids(evidence: dict[str, object]) -> set[str]:
    values = evidence.get("source_block_ids", [])
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value)}


def public_source_evidence(source_evidence: object) -> list[dict[str, object]]:
    if not isinstance(source_evidence, list):
        return []

    public_evidence: list[dict[str, object]] = []
    seen_evidence: list[tuple[str, str, set[str]]] = []
    for evidence in source_evidence:
        if not isinstance(evidence, dict):
            continue
        quote = str(evidence.get("quote") or "")
        context = str(evidence.get("context") or "")
        normalized_quote = normalize_evidence_text(quote)
        normalized_context = normalize_evidence_text(context)
        block_ids = public_source_block_ids(evidence)
        if is_duplicate_public_evidence(
            normalized_quote,
            normalized_context,
            block_ids,
            seen_evidence,
        ):
            continue
        seen_evidence.append((normalized_quote, normalized_context, block_ids))
        public_evidence.append(
            {
                "file_name": evidence.get("file_name", ""),
                "source_locator": evidence.get("source_locator", ""),
                "quote": quote,
                "context": context,
                "tables": evidence.get("tables", []),
                "ai_relevance": evidence.get("ai_relevance", ""),
            }
        )
    return public_evidence


def public_kts_extraction(record: dict[str, object]) -> dict[str, object]:
    items = []
    raw_items = record.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        source_evidence = item.get("source_evidence", [])
        review_notes = item.get("review_notes", [])
        public_evidence = public_source_evidence(source_evidence)
        confidence = public_kts_confidence(item)
        default_review_status = (
            "ai_reviewed" if confidence["confidence_level"] == "high" else "pending"
        )
        human_review = public_human_review(item, default_review_status)
        items.append(
            {
                "taxonomy_id": item.get("taxonomy_id", ""),
                "group": item.get("group", ""),
                "label": item.get("label", ""),
                "draft_content": item.get("draft_content", ""),
                "source_evidence_count": len(source_evidence) if isinstance(source_evidence, list) else 0,
                "source_evidence_display_count": len(public_evidence),
                "source_evidence": public_evidence,
                "extracted_facts": item.get("extracted_facts", {}),
                "review_notes": review_notes if isinstance(review_notes, list) else [],
                **confidence,
                **human_review,
            }
        )

    review_summary = {
        "confirmed_count": sum(1 for item in items if item["review_status"] == "confirmed"),
        "pending_count": sum(
            1 for item in items if item["review_status"] != "confirmed"
        ),
        "high_confidence_count": sum(1 for item in items if item["confidence_level"] == "high"),
        "medium_confidence_count": sum(1 for item in items if item["confidence_level"] == "medium"),
        "low_confidence_count": sum(1 for item in items if item["confidence_level"] == "low"),
    }
    return {
        "updated_at": record.get("updated_at", ""),
        "phase": record.get("phase", ""),
        "taxonomy_id": record.get("taxonomy_id", ""),
        "taxonomy_version": record.get("taxonomy_version", ""),
        "summary": record.get("summary", {}),
        "review_summary": review_summary,
        "items": items,
    }


def apply_kts_review_updates(
    record: dict[str, object],
    review_items: list[object],
) -> dict[str, object]:
    updates: dict[str, dict[str, object]] = {}
    for raw_item in review_items:
        if not isinstance(raw_item, dict):
            continue
        taxonomy_id = str(raw_item.get("taxonomy_id") or "").strip()
        if not taxonomy_id:
            continue
        updates[taxonomy_id] = raw_item

    if not updates:
        raise ValueError("没有可保存的复核结果。")

    now = timestamp()
    raw_items = record.get("items", [])
    if not isinstance(raw_items, list):
        raise ValueError("当前 KTS 结果结构异常。")

    matched_count = 0
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        taxonomy_id = str(item.get("taxonomy_id") or "")
        update = updates.get(taxonomy_id)
        if update is None:
            continue
        matched_count += 1
        item["human_review"] = {
            "status": normalize_review_status(update.get("review_status")),
            "content": clamp_text(update.get("review_content"), MAX_REVIEW_CONTENT_CHARS),
            "note": clamp_text(update.get("review_note"), MAX_REVIEW_NOTE_CHARS),
            "updated_at": now,
        }

    if matched_count == 0:
        raise ValueError("未匹配到任何 KTS 事项。")
    return record


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "LegalWorkbench/0.5"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[LegalWorkbench] " + fmt % args + "\n")

    def send_json(self, data: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, rel_path: str) -> None:
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Static file not found")
            return

        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix in {".html", ".css", ".js"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def read_multipart_body(self) -> tuple[dict[str, str], list[dict[str, object]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("请使用 multipart/form-data 上传文件。")

        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("上传内容为空。")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("上传内容过大，请分批上传。")

        body = self.rfile.read(length)
        message = BytesParser(policy=email.policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + body
        )
        if not message.is_multipart():
            raise ValueError("上传格式无法识别。")

        fields: dict[str, str] = {}
        files: list[dict[str, object]] = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            field_name = part.get_param("name", header="content-disposition") or ""
            file_name = part.get_filename()
            if file_name:
                content = part.get_payload(decode=True) or b""
                if content:
                    files.append(
                        {
                            "field_name": field_name,
                            "file_name": file_name,
                            "content": content,
                        }
                    )
                continue

            try:
                fields[field_name] = str(part.get_content())
            except LookupError:
                fields[field_name] = (part.get_payload(decode=True) or b"").decode(
                    "utf-8", errors="replace"
                )
        return fields, files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path in {"/", "/index.html"}:
            self.send_static("index.html")
            return
        if path.startswith("/static/"):
            self.send_static(path.removeprefix("/static/"))
            return
        if path == "/api/health":
            self.send_json({"ok": True, "name": APP_NAME, "version": APP_VERSION})
            return
        if path == "/api/ai/test":
            self.send_json(test_connection())
            return
        if path.startswith("/api/runs/") and path.endswith("/progress"):
            run_id = path.removeprefix("/api/runs/").removesuffix("/progress").strip("/")
            progress = get_run_progress(run_id)
            if progress is None:
                self.send_json({"ok": False, "error": "Run progress not found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json({"ok": True, "progress": progress})
            return
        if path == "/api/kts-review/current":
            extraction_record = load_current_kts_extraction()
            if extraction_record is None:
                self.send_json({"ok": False, "status": "empty", "message": "暂无可继续的复核结果。"})
                return
            self.send_json(
                {
                    "ok": True,
                    "current": public_current_kts_review(
                        load_current_parse(),
                        load_current_source_index(),
                        load_current_kts_candidates(),
                        extraction_record,
                    ),
                }
            )
            return
        if path == "/api/capabilities":
            self.send_json([cap.public_dict() for cap in load_capabilities()])
            return
        if path.startswith("/api/capabilities/"):
            capability_id = path.removeprefix("/api/capabilities/").strip("/")
            capability = get_capability(capability_id)
            if capability is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Capability not found")
                return
            self.send_json(capability.raw)
            return
        if path == "/api/debug/current-parse":
            self.send_json(load_current_parse() or {"status": "empty"})
            return
        if path == "/api/debug/current-source-index":
            self.send_json(load_current_source_index() or {"status": "empty"})
            return
        if path == "/api/debug/current-kts-candidates":
            self.send_json(load_current_kts_candidates() or {"status": "empty"})
            return
        if path == "/api/debug/current-kts-extraction":
            self.send_json(load_current_kts_extraction() or {"status": "empty"})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/documents/upload":
            try:
                fields, files = self.read_multipart_body()
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if not files:
                self.send_json({"ok": False, "error": "请选择至少一个 Word 文件。"}, HTTPStatus.BAD_REQUEST)
                return

            run_id = str(fields.get("run_id") or "")
            start_run_progress(run_id, len(files))
            capability_id = str(fields.get("capability_id") or "spa_sha_kts")
            capability = get_capability(capability_id)
            if capability is None:
                update_run_progress(run_id, status="error", message="Unknown capability")
                self.send_json({"ok": False, "error": "Unknown capability"}, HTTPStatus.BAD_REQUEST)
                return

            documents: list[dict[str, object]] = []
            update_run_progress(
                run_id,
                stage="parse",
                stage_label="读取正文及表格",
                stage_index=1,
                progress_percent=5,
                message="正在读取 Word 正文和表格。",
            )
            for index, file_info in enumerate(files, start=1):
                original_name = str(file_info["file_name"])
                content = file_info["content"]
                suffix = "".join(Path(original_name).suffixes) or ".docx"
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                        temp_file.write(content)  # type: ignore[arg-type]
                        temp_path = Path(temp_file.name)

                    parsed = parse_docx_file(temp_path, original_name)
                    documents.append(parsed)
                except DocxParseError as exc:
                    documents.append(
                        {
                            "file_name": original_name,
                            "file_size": len(content),  # type: ignore[arg-type]
                            "status": "error",
                            "error": str(exc),
                        }
                    )
                finally:
                    if temp_path is not None:
                        temp_path.unlink(missing_ok=True)
                update_run_progress(
                    run_id,
                    parsed_file_count=index,
                    progress_percent=5 + (15 * index / max(1, len(files))),
                    message=f"已读取 {index}/{len(files)} 个文件。",
                )

            error_count = sum(1 for item in documents if item.get("status") == "error")
            debug_record = save_current_parse(
                {
                    "capability_id": capability_id,
                    "phase": "v0.3-docx-intake",
                    "input": {
                        "capability_id": capability_id,
                        "party_role": fields.get("party_role", ""),
                        "matter_notes": fields.get("matter_notes", ""),
                        "file_names": [str(item["file_name"]) for item in documents],
                    },
                    "result": {
                        "status": "partial_error" if error_count else "parsed",
                        "message": (
                            f"已解析 {len(documents) - error_count} 个文件，{error_count} 个文件未能解析。"
                            if error_count
                            else f"已解析 {len(documents)} 个文件，可继续核对文档结构。"
                        ),
                        "capability": capability.public_dict(),
                        "documents": documents,
                    },
                }
            )
            update_run_progress(
                run_id,
                stage="source_index",
                stage_label="建立原文证据索引",
                stage_index=2,
                progress_percent=25,
                message="正在建立原文证据索引。",
            )
            source_index_record = save_current_source_index(build_source_index(debug_record))
            update_run_progress(
                run_id,
                stage="model_review",
                stage_label="模型语义复核",
                stage_index=3,
                progress_percent=30,
                message="正在逐项复核 KTS 候选证据。",
            )

            def update_kts_progress(progress: dict[str, object]) -> None:
                completed = int(progress.get("completed_items", 0) or 0)
                total = int(progress.get("total_items", 0) or 0)
                percent = 30 + (60 * completed / max(1, total))
                update_run_progress(
                    run_id,
                    stage="model_review",
                    stage_label="模型语义复核",
                    stage_index=3,
                    progress_percent=percent,
                    completed_items=completed,
                    total_items=total,
                    worker_count=progress.get("worker_count", 1),
                    message=f"已完成 {completed}/{total} 个 KTS 事项。",
                )

            candidates_record = save_current_kts_candidates(
                build_kts_candidates(source_index_record, progress_callback=update_kts_progress)
            )
            update_run_progress(
                run_id,
                stage="extraction",
                stage_label="生成摘要",
                stage_index=4,
                progress_percent=94,
                message="正在生成 KTS 摘要。",
            )

            def update_extraction_progress(progress: dict[str, object]) -> None:
                completed = int(progress.get("completed_items", 0) or 0)
                total = int(progress.get("total_items", 0) or 0)
                update_run_progress(
                    run_id,
                    stage="extraction",
                    stage_label="生成摘要",
                    stage_index=4,
                    progress_percent=90 + (9 * completed / max(1, total)),
                    completed_items=completed,
                    total_items=total,
                    worker_count=progress.get("worker_count", 1),
                    message=f"已处理 {completed}/{total} 个 KTS 事项。",
                )

            extraction_record = save_current_kts_extraction(
                build_kts_extraction(
                    candidates_record,
                    source_index_record,
                    progress_callback=update_extraction_progress,
                )
            )
            update_run_progress(
                run_id,
                status="completed",
                stage="completed",
                stage_label="处理完成",
                stage_index=4,
                progress_percent=100,
                completed_items=candidates_record.get("summary", {}).get("taxonomy_item_count", 0),
                total_items=candidates_record.get("summary", {}).get("taxonomy_item_count", 0),
                message="处理完成。",
            )
            public_current = {
                "updated_at": debug_record["updated_at"],
                "capability_id": debug_record["capability_id"],
                "phase": debug_record["phase"],
                "source_index": public_source_index(source_index_record),
                "kts_candidates": public_kts_candidates(candidates_record),
                "kts_extraction": public_kts_extraction(extraction_record),
                "result": {
                    "status": debug_record["result"]["status"],
                    "message": debug_record["result"]["message"],
                    "capability": debug_record["result"]["capability"],
                    "documents": public_documents_from_parse_record(debug_record),
                },
            }
            self.send_json({"ok": error_count == 0, "current": public_current})
            return

        if path == "/api/kts-review/save":
            try:
                data = self.read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            extraction_record = load_current_kts_extraction()
            if extraction_record is None:
                self.send_json({"ok": False, "error": "请先生成 KTS 中间表。"}, HTTPStatus.BAD_REQUEST)
                return

            review_items = data.get("items", [])
            if not isinstance(review_items, list):
                self.send_json({"ok": False, "error": "复核结果格式不正确。"}, HTTPStatus.BAD_REQUEST)
                return

            try:
                updated_record = apply_kts_review_updates(extraction_record, review_items)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            updated_record.pop("updated_at", None)
            saved_record = save_current_kts_extraction(updated_record)
            self.send_json(
                {
                    "ok": True,
                    "current_kts_extraction": public_kts_extraction(saved_record),
                }
            )
            return

        if path == "/api/workbench/check":
            try:
                data = self.read_json_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            capability_id = str(data.get("capability_id") or "spa_sha_kts")
            capability = get_capability(capability_id)
            if capability is None:
                self.send_json({"ok": False, "error": "Unknown capability"}, HTTPStatus.BAD_REQUEST)
                return

            self.send_json(
                {
                    "ok": True,
                    "current": {
                        "updated_at": timestamp(),
                        "capability_id": capability_id,
                        "phase": "workbench-check",
                        "result": {
                            "status": "placeholder",
                            "message": "工作台已经连通。后续版本会接入 KTS 生成和 Word 导出。",
                            "capability": capability.public_dict(),
                        },
                    },
                }
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local Legal Workbench server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_runtime_dirs()
    server = ThreadingHTTPServer((args.host, args.port), WorkbenchHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"[LegalWorkbench] Serving {url}")
    if args.open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[LegalWorkbench] Stopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
