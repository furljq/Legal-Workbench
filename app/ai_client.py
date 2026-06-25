"""OpenAI-compatible model client for internal workbench skills."""

from __future__ import annotations

import importlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_API_TYPE = "chat_completions"
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT_SECONDS = 300
MAX_MODEL_WORKER_LIMIT = 16
API_TYPES = {"chat_completions", "responses"}
TRANSIENT_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
TRANSIENT_ERROR_KEYWORDS = (
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "temporarily",
    "try again",
    "connection reset",
    "connection aborted",
    "remote end closed",
)


class AIClientError(RuntimeError):
    """Raised when an internal model call cannot be completed."""


def local_config() -> Any | None:
    try:
        return importlib.import_module("ai_config")
    except ModuleNotFoundError:
        return None


def local_config_value(name: str, default: Any = None) -> Any:
    module = local_config()
    if module is None:
        return default
    return getattr(module, name, default)


def configured_value(name: str, env_name: str, default: Any = "") -> Any:
    env_value = os.environ.get(env_name)
    if env_value not in {None, ""}:
        return env_value
    value = local_config_value(name, default)
    return default if value in {None, ""} else value


def ai_configured() -> bool:
    return bool(str(configured_value("API_KEY", "LEGAL_WORKBENCH_API_KEY", "")).strip())


def ai_model() -> str:
    return str(configured_value("MODEL", "LEGAL_WORKBENCH_MODEL", DEFAULT_MODEL)).strip()


def ai_base_url() -> str:
    base_url = str(configured_value("BASE_URL", "LEGAL_WORKBENCH_BASE_URL", DEFAULT_BASE_URL)).strip()
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url.removesuffix("/chat/completions").rstrip("/")
    if base_url.endswith("/responses"):
        base_url = base_url.removesuffix("/responses").rstrip("/")
    return base_url


def ai_api_type() -> str:
    raw_value = str(configured_value("API_TYPE", "LEGAL_WORKBENCH_API_TYPE", DEFAULT_API_TYPE)).strip()
    normalized = raw_value.lower().replace("-", "_")
    aliases = {
        "chat": "chat_completions",
        "chat_completions": "chat_completions",
        "openai_chat": "chat_completions",
        "responses": "responses",
        "openai_responses": "responses",
        "ikuncode": "responses",
    }
    api_type = aliases.get(normalized, normalized)
    if api_type not in API_TYPES:
        raise AIClientError("Model API type must be chat_completions or responses.")
    return api_type


def ai_temperature(default: float = DEFAULT_TEMPERATURE) -> float:
    value = configured_value("TEMPERATURE", "LEGAL_WORKBENCH_TEMPERATURE", default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ai_timeout_seconds(default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    value = configured_value("TIMEOUT_SECONDS", "LEGAL_WORKBENCH_TIMEOUT_SECONDS", default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ai_max_workers(default: int = 8) -> int:
    value = configured_value("MAX_MODEL_WORKERS", "LEGAL_WORKBENCH_MODEL_MAX_WORKERS", default)
    try:
        return min(MAX_MODEL_WORKER_LIMIT, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def is_transient_ai_error(error: object) -> bool:
    text = str(error or "")
    match = re.search(r"HTTP\s+(\d+)", text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1)) in TRANSIENT_HTTP_CODES
        except ValueError:
            return False
    lowered = text.lower()
    return any(keyword in lowered for keyword in TRANSIENT_ERROR_KEYWORDS)


def api_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "LegalWorkbench/0.4",
    }


def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    if not ai_configured():
        raise AIClientError("Model service is unavailable.")

    api_key = str(configured_value("API_KEY", "LEGAL_WORKBENCH_API_KEY", "")).strip()
    chosen_temperature = ai_temperature() if temperature is None else temperature
    chosen_timeout = ai_timeout_seconds() if timeout_seconds is None else timeout_seconds
    if ai_api_type() == "responses":
        content = call_responses_http(messages, api_key, chosen_temperature, chosen_timeout)
    else:
        content = call_chat_completions_http(messages, api_key, chosen_temperature, chosen_timeout)
    return parse_json_object(content)


def test_connection(timeout_seconds: int = 60) -> dict[str, Any]:
    try:
        result = chat_json(
            [
                {"role": "system", "content": "你只输出JSON。"},
                {"role": "user", "content": '请返回 {"ok": true, "task": "legal_workbench_ai_test"}'},
            ],
            temperature=0,
            timeout_seconds=timeout_seconds,
        )
    except AIClientError as exc:
        return {
            "ok": False,
            "message": "模型服务测试失败。",
            "error": str(exc),
            "model": ai_model(),
            "api_type": safe_api_type(),
        }

    return {
        "ok": bool(result.get("ok")),
        "message": "模型服务可用。" if result.get("ok") else "模型服务返回内容未通过校验。",
        "model": ai_model(),
        "api_type": safe_api_type(),
    }


def safe_api_type() -> str:
    try:
        return ai_api_type()
    except AIClientError:
        return "invalid"


def _read_sse_stream(response, timeout_seconds: int) -> str:
    """Read Server-Sent Events stream and return concatenated content."""
    from http.client import IncompleteRead
    chunks: list[str] = []
    try:
        for raw_line in response:
            line = raw_line.strip()
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                delta_content = _extract_stream_delta(data)
                if delta_content:
                    chunks.append(delta_content)
    except IncompleteRead:
        pass
    except (OSError, TimeoutError) as exc:
        if not chunks:
            raise AIClientError(f"Stream read failed: {exc}") from exc
    return "".join(chunks)


def _extract_stream_delta(data: dict[str, Any]) -> str:
    """Extract content delta from a streaming chunk (chat_completions or responses)."""
    # chat_completions format
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content

    # responses format
    delta = data.get("delta")
    if isinstance(delta, str):
        return delta
    if isinstance(delta, dict):
        text = delta.get("text") or delta.get("content")
        if isinstance(text, str):
            return text

    # responses output_text_delta event
    if data.get("type") == "response.output_text.delta":
        text = data.get("delta")
        if isinstance(text, str):
            return text

    return ""


def call_chat_completions_http(
    messages: list[dict[str, str]],
    api_key: str,
    temperature: float,
    timeout_seconds: int,
) -> str:
    payload = {
        "model": ai_model(),
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": True,
    }
    request = urllib.request.Request(
        ai_base_url() + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=api_headers(api_key),
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
        content = _read_sse_stream(response, timeout_seconds)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AIClientError(f"Model service returned HTTP {exc.code}: {detail}") from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIClientError(f"Model service request failed: {exc}") from exc

    if not content:
        raise AIClientError("Model service response did not contain message content.")
    return content


def messages_to_prompt(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").upper()
        content = str(message.get("content") or "")
        if content:
            parts.append(f"{role}:\n{content}")
    parts.append("请只输出一个合法 JSON 对象，不要输出 Markdown 代码块或额外解释。")
    return "\n\n".join(parts)


def call_responses_http(
    messages: list[dict[str, str]],
    api_key: str,
    temperature: float,
    timeout_seconds: int,
) -> str:
    payload = {
        "model": ai_model(),
        "input": messages_to_prompt(messages),
        "temperature": temperature,
        "stream": True,
    }
    request = urllib.request.Request(
        ai_base_url() + "/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=api_headers(api_key),
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
        content = _read_sse_stream(response, timeout_seconds)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AIClientError(f"Model service returned HTTP {exc.code}: {detail}") from exc
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIClientError(f"Model service request failed: {exc}") from exc

    if not content:
        raise AIClientError("Model service response did not contain output text.")
    return content


def extract_responses_text(data: dict[str, Any]) -> str:
    nested = data.get("response")
    if isinstance(nested, dict):
        nested_text = extract_responses_text(nested)
        if nested_text:
            return nested_text

    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    pieces: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            pieces.append(item["text"])
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                pieces.append(text)
            elif isinstance(text, dict) and isinstance(text.get("value"), str):
                pieces.append(text["value"])
    return "\n".join(piece for piece in pieces if piece).strip()


def strip_code_fence(value: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def parse_json_object(value: str) -> dict[str, Any]:
    text = strip_code_fence(value)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                parsed, _ = decoder.raw_decode(text[match.start() :])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise AIClientError("Model service response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise AIClientError("Model service response JSON must be an object.")
    return parsed
