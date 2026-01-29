"""
Provider-agnostic chat normalization helpers.

Providers should pass provider-specific payloads through this module to create
NormalizedMessage objects with structured parts and correct roles.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .model import (
    NormalizationDiagnostics,
    NormalizedMessage,
    NormalizedPart,
    NormalizedRole,
)
from .util import stringify_content

_ROLE_ALIASES: dict[str, NormalizedRole] = {
    "system": "system",
    "developer": "system",
    "user": "user",
    "human": "user",
    "assistant": "assistant",
    "ai": "assistant",
    "model": "assistant",
    "gemini": "assistant",
    "tool": "tool",
    "function": "tool",
}


@dataclass
class Normalizer:
    provider: str
    diagnostics: NormalizationDiagnostics = field(default_factory=NormalizationDiagnostics)
    _sequence: int = field(default=0, init=False, repr=False)

    def normalize_message(
        self,
        payload: Any,
        *,
        timestamp: datetime | None = None,
        role: str | None = None,
        name: str | None = None,
        latency_ms: float | None = None,
        provider_meta: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> NormalizedMessage | None:
        """
        Normalize a provider message-ish payload into a NormalizedMessage.

        If payload cannot be parsed into any message parts, returns None and
        increments diagnostics.skipped_events.
        """
        self.diagnostics.total_events += 1
        if not isinstance(payload, dict):
            self.diagnostics.skipped_events += 1
            return None

        extracted_role = _extract_role(payload, role)
        extracted_name = _extract_name(payload, name)
        extracted_latency = _extract_latency_ms(payload, latency_ms)
        extracted_timestamp = timestamp or _extract_timestamp(payload)

        parts: list[NormalizedPart] = []
        parts.extend(_parts_from_content(_extract_content(payload)))
        parts.extend(_parts_from_openai_tool_calls(payload))
        parts.extend(_parts_from_openai_function_call(payload))
        parts.extend(_parts_from_gemini_function(payload))
        parts.extend(_parts_from_tool_result_payload(payload))

        parts = _compact_parts(parts)
        if not parts:
            self.diagnostics.skipped_events += 1
            return None

        normalized_role = _resolve_role(extracted_role, parts)
        if extracted_role and extracted_role.strip().lower() in {"user", "human"}:
            if normalized_role == "tool" and not _ROLE_ALIASES.get(extracted_role.strip().lower()):
                # should not happen, but keep the warning noisy if it does
                self.diagnostics.warnings.append(
                    f"{self.provider}: role override '{extracted_role}' -> 'tool'"
                )
            elif normalized_role == "tool":
                self.diagnostics.warnings.append(
                    f"{self.provider}: role override '{extracted_role}' -> 'tool'"
                )

        msg_id = (
            _clean_str(message_id)
            or _clean_str(payload.get("id"))
            or _stable_message_id(
                provider=self.provider,
                role=normalized_role,
                timestamp=extracted_timestamp,
                parts=parts,
                sequence=self._next_sequence(),
            )
        )

        self.diagnostics.parsed_events += 1
        return NormalizedMessage(
            id=msg_id,
            role=normalized_role,
            name=extracted_name,
            timestamp=extracted_timestamp,
            parts=parts,
            latency_ms=extracted_latency,
            provider_meta=provider_meta,
        )

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value


def render_legacy_content(message: NormalizedMessage) -> str:
    """Render a NormalizedMessage into a readable single-string legacy content."""
    chunks: list[str] = []
    for part in message.parts:
        if part.kind == "text" and part.text:
            chunks.append(part.text)
            continue
        if part.kind == "code" and part.text:
            lang = part.language or ""
            fence = f"```{lang}".rstrip()
            chunks.append(f"{fence}\n{part.text}\n```")
            continue
        if part.kind == "tool-call":
            tool_name = part.tool_name or "tool"
            args = _safe_json(part.arguments)
            chunks.append(f"[tool-call] {tool_name} {args}".strip())
            continue
        if part.kind == "tool-result":
            tool_name = part.tool_name or "tool"
            out = _safe_json(part.output)
            chunks.append(f"[tool-result] {tool_name} {out}".strip())
            continue
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _extract_content(payload: dict[str, Any]) -> Any:
    if "content" in payload:
        return payload.get("content")
    if "parts" in payload:
        return payload.get("parts")
    if "message" in payload and isinstance(payload.get("message"), dict):
        nested = payload.get("message") or {}
        if "content" in nested or "parts" in nested:
            return nested.get("content") if "content" in nested else nested.get("parts")
    return None


def _extract_role(payload: dict[str, Any], override: str | None) -> str | None:
    if _clean_str(override):
        return override
    for key in ("role", "author", "speaker", "sender", "type"):
        value = payload.get(key)
        if _clean_str(value):
            return str(value)
    message = payload.get("message")
    if isinstance(message, dict):
        value = message.get("role") or message.get("type")
        if _clean_str(value):
            return str(value)
    return None


def _extract_name(payload: dict[str, Any], override: str | None) -> str | None:
    if _clean_str(override) and override is not None:
        return override.strip()
    for key in ("name", "tool_name"):
        value = payload.get(key)
        if _clean_str(value):
            return str(value).strip()
    return None


def _extract_latency_ms(payload: dict[str, Any], override: float | None) -> float | None:
    if isinstance(override, int | float):
        return float(override)
    for key in ("latency_ms", "latencyMs", "duration_ms", "durationMs"):
        value = payload.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _extract_timestamp(payload: dict[str, Any]) -> datetime | None:
    # Providers should generally supply timestamps explicitly; this is best-effort.
    value = (
        payload.get("timestamp")
        or payload.get("created_at")
        or payload.get("time")
        or payload.get("ts")
    )
    # Avoid importing parse_timestamp from util to keep normalize.py focused; callers already do it.
    if isinstance(value, datetime):
        return value
    return None


def _resolve_role(role: str | None, parts: list[NormalizedPart]) -> NormalizedRole:
    lowered = (role or "").strip().lower()
    base = _ROLE_ALIASES.get(lowered)

    has_tool_result = any(part.kind == "tool-result" for part in parts)
    has_tool_call = any(part.kind == "tool-call" for part in parts)

    if has_tool_result:
        return "tool"
    if base in {"system", "user", "assistant", "tool"}:
        return base
    if has_tool_call:
        return "assistant"
    # Default to assistant to avoid mis-attributing provider events as user messages.
    return "assistant"


def _parts_from_content(content: Any) -> list[NormalizedPart]:
    if content is None:
        return []
    if isinstance(content, str):
        text = content.strip()
        return [NormalizedPart(kind="text", text=text)] if text else []
    if isinstance(content, dict):
        return _parts_from_content_dict(content)
    if isinstance(content, list):
        parts: list[NormalizedPart] = []
        for item in content:
            parts.extend(_parts_from_content(item))
        return parts
    text = stringify_content(content).strip()
    return [NormalizedPart(kind="text", text=text)] if text else []


def _parts_from_content_dict(item: dict[str, Any]) -> list[NormalizedPart]:
    kind = (str(item.get("type") or item.get("kind") or "")).strip().lower()

    if kind in {"text", "input_text", "output_text"}:
        text = _clean_str(item.get("text") or item.get("content") or item.get("value")) or ""
        text = text.strip()
        return [NormalizedPart(kind="text", text=text)] if text else []

    if kind in {"code", "input_code", "output_code"}:
        text = _clean_str(item.get("text") or item.get("code") or item.get("content")) or ""
        text = text.strip()
        if not text:
            return []
        language = _clean_str(item.get("language") or item.get("lang"))
        return [
            NormalizedPart(kind="code", text=text, language=language.strip() if language else None)
        ]

    if kind in {"tool_use", "tool-call", "tool_call", "function_call"}:
        tool_name = (
            _clean_str(item.get("name") or item.get("tool_name") or item.get("tool")) or None
        )
        args = item.get("input") if "input" in item else item.get("arguments") or item.get("args")
        call_id = _clean_str(item.get("id"))
        return [
            NormalizedPart(
                kind="tool-call",
                tool_name=tool_name.strip() if tool_name else None,
                arguments=args,
                id=call_id.strip() if call_id else None,
            )
        ]

    if kind in {"tool_result", "tool-result", "tool_output", "function_response"}:
        tool_name = (
            _clean_str(item.get("name") or item.get("tool_name") or item.get("tool")) or None
        )
        out = item.get("output") if "output" in item else item.get("content") or item.get("result")
        call_id = _clean_str(item.get("tool_use_id") or item.get("id"))
        return [
            NormalizedPart(
                kind="tool-result",
                tool_name=tool_name.strip() if tool_name else None,
                output=out,
                id=call_id.strip() if call_id else None,
            )
        ]

    # Gemini parts can have functionCall/functionResponse nested objects.
    if "functionCall" in item and isinstance(item.get("functionCall"), dict):
        call = item.get("functionCall") or {}
        tool_name = _clean_str(call.get("name")) or None
        args = call.get("args") if "args" in call else call.get("arguments")
        return [NormalizedPart(kind="tool-call", tool_name=tool_name, arguments=args)]

    if "functionResponse" in item and isinstance(item.get("functionResponse"), dict):
        resp = item.get("functionResponse") or {}
        tool_name = _clean_str(resp.get("name")) or None
        out = resp.get("response") if "response" in resp else resp.get("output")
        return [NormalizedPart(kind="tool-result", tool_name=tool_name, output=out)]

    # Fallback: attempt to render any text-like keys.
    if "text" in item and _clean_str(item.get("text")):
        return [NormalizedPart(kind="text", text=str(item.get("text")).strip())]
    text = stringify_content(item).strip()
    return [NormalizedPart(kind="text", text=text)] if text else []


def _parts_from_openai_tool_calls(payload: dict[str, Any]) -> list[NormalizedPart]:
    calls = payload.get("tool_calls")
    if not isinstance(calls, list):
        return []
    parts: list[NormalizedPart] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        call_id = _clean_str(call.get("id"))
        func_val = call.get("function")
        function = func_val if isinstance(func_val, dict) else {}
        tool_name = _clean_str(function.get("name") or call.get("name"))
        args_raw = (
            function.get("arguments") if isinstance(function, dict) else call.get("arguments")
        )
        args: Any = args_raw
        if isinstance(args_raw, str):
            parsed = _maybe_json(args_raw)
            args = parsed if parsed is not None else args_raw
        parts.append(
            NormalizedPart(
                kind="tool-call",
                id=call_id.strip() if call_id else None,
                tool_name=tool_name.strip() if tool_name else None,
                arguments=args,
            )
        )
    return parts


def _parts_from_openai_function_call(payload: dict[str, Any]) -> list[NormalizedPart]:
    call = payload.get("function_call")
    if not isinstance(call, dict):
        return []
    tool_name = _clean_str(call.get("name"))
    args_raw = call.get("arguments")
    args: Any = args_raw
    if isinstance(args_raw, str):
        parsed = _maybe_json(args_raw)
        args = parsed if parsed is not None else args_raw
    if not tool_name and args is None:
        return []
    return [NormalizedPart(kind="tool-call", tool_name=tool_name, arguments=args)]


def _parts_from_gemini_function(payload: dict[str, Any]) -> list[NormalizedPart]:
    # Some Gemini transcripts store function call/response at the message level.
    if "functionCall" in payload and isinstance(payload.get("functionCall"), dict):
        call = payload.get("functionCall") or {}
        tool_name = _clean_str(call.get("name"))
        args = call.get("args") if "args" in call else call.get("arguments")
        return [NormalizedPart(kind="tool-call", tool_name=tool_name, arguments=args)]
    if "functionResponse" in payload and isinstance(payload.get("functionResponse"), dict):
        resp = payload.get("functionResponse") or {}
        tool_name = _clean_str(resp.get("name"))
        out = resp.get("response") if "response" in resp else resp.get("output")
        return [NormalizedPart(kind="tool-result", tool_name=tool_name, output=out)]
    return []


def _parts_from_tool_result_payload(payload: dict[str, Any]) -> list[NormalizedPart]:
    kind = (str(payload.get("type") or "")).strip().lower()
    if kind not in {"tool_result", "tool-result", "tool_output", "tool-output"}:
        return []
    tool_name = _clean_str(payload.get("tool_name") or payload.get("name"))
    out = (
        payload.get("output")
        if "output" in payload
        else payload.get("content") or payload.get("result")
    )
    call_id = _clean_str(payload.get("tool_use_id") or payload.get("id"))
    if tool_name is None and out is None:
        return []
    return [
        NormalizedPart(
            kind="tool-result",
            tool_name=tool_name.strip() if tool_name else None,
            output=out,
            id=call_id.strip() if call_id else None,
        )
    ]


def _compact_parts(parts: list[NormalizedPart]) -> list[NormalizedPart]:
    compacted: list[NormalizedPart] = []
    for part in parts:
        if part.kind in {"text", "code"} and part.text is not None:
            stripped = part.text.strip()
            if not stripped:
                continue
            compacted.append(
                NormalizedPart(
                    kind=part.kind,
                    text=stripped,
                    language=part.language,
                    tool_name=part.tool_name,
                    arguments=part.arguments,
                    output=part.output,
                    id=part.id,
                )
            )
        else:
            compacted.append(part)
    return compacted


def _stable_message_id(
    *,
    provider: str,
    role: NormalizedRole,
    timestamp: datetime | None,
    parts: Iterable[NormalizedPart],
    sequence: int,
) -> str:
    hasher = hashlib.sha1()
    hasher.update(provider.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(role.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update((timestamp.isoformat() if timestamp else "").encode("utf-8"))
    hasher.update(b"\0")
    for part in parts:
        hasher.update(part.kind.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((part.text or "").encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((part.language or "").encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((part.tool_name or "").encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_safe_json(part.arguments).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_safe_json(part.output).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((part.id or "").encode("utf-8"))
        hasher.update(b"\0")
    hasher.update(str(sequence).encode("utf-8"))
    return f"{provider}:{hasher.hexdigest()}"


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)


def _maybe_json(value: str) -> Any | None:
    stripped = value.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None
