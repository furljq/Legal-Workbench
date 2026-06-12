#!/usr/bin/env python3
"""Local browser server for Legal Workbench."""

from __future__ import annotations

import argparse
import email.policy
import json
import mimetypes
import sys
import tempfile
import threading
import webbrowser
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ai_client import public_config, test_connection
from capability_registry import get_capability, load_capabilities
from config import APP_NAME, APP_VERSION, DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR, ensure_runtime_dirs
from docx_parser import DocxParseError, parse_docx_file
from work_state import load_current_parse, save_current_parse, timestamp


MAX_UPLOAD_BYTES = 120 * 1024 * 1024


def json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "LegalWorkbench/0.3"

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
        if path == "/api/ai/config":
            self.send_json(public_config())
            return
        if path == "/api/ai/test":
            self.send_json(test_connection())
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

            capability_id = str(fields.get("capability_id") or "spa_sha_kts")
            capability = get_capability(capability_id)
            if capability is None:
                self.send_json({"ok": False, "error": "Unknown capability"}, HTTPStatus.BAD_REQUEST)
                return

            documents: list[dict[str, object]] = []
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
            public_documents = []
            for document in documents:
                public_document = {
                    "file_name": document["file_name"],
                    "file_size": document["file_size"],
                    "status": document["status"],
                }
                if document.get("document_type"):
                    public_document["document_type"] = document["document_type"]
                if document.get("error"):
                    public_document["error"] = document["error"]
                public_documents.append(public_document)

            public_current = {
                "updated_at": debug_record["updated_at"],
                "capability_id": debug_record["capability_id"],
                "phase": debug_record["phase"],
                "result": {
                    "status": debug_record["result"]["status"],
                    "message": debug_record["result"]["message"],
                    "capability": debug_record["result"]["capability"],
                    "documents": public_documents,
                },
            }
            self.send_json({"ok": error_count == 0, "current": public_current})
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
