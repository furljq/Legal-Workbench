#!/usr/bin/env python3
"""Local browser server for Legal Workbench."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ai_client import public_config, test_connection
from capability_registry import get_capability, load_capabilities
from config import APP_NAME, APP_VERSION, DEFAULT_HOST, DEFAULT_PORT, STATIC_DIR, ensure_runtime_dirs
from run_store import list_runs, save_run_record


def json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "LegalWorkbench/0.2"

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
        if path == "/api/runs":
            self.send_json(list_runs())
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/runs/dry-run":
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

            record = save_run_record(
                {
                    "capability_id": capability_id,
                    "phase": "v0.2-dry-run",
                    "input": data,
                    "result": {
                        "status": "placeholder",
                        "message": "工作台已经连通。后续版本会接入文件解析、KTS 生成和 Word 导出。",
                        "capability": capability.public_dict(),
                    },
                }
            )
            self.send_json({"ok": True, "run": record})
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
