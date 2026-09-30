from __future__ import annotations

import time
from typing import Any


def input_to_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("input", "")
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    messages: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            content = item.get("content", "")
            if isinstance(content, list):
                content = "".join(part.get("text") or "" for part in content if isinstance(part, dict))
            messages.append({"role": item.get("role", "user"), "content": content})
        elif item.get("role"):
            messages.append({"role": item["role"], "content": item.get("content", "")})
    return messages


def response_request(payload: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": payload.get("model", ""),
        "messages": input_to_messages(payload),
        "stream": bool(payload.get("stream", True)),
    }
    for key in ("temperature", "top_p", "max_output_tokens", "tools", "tool_choice"):
        if key in payload:
            request["max_tokens" if key == "max_output_tokens" else key] = payload[key]
    if payload.get("instructions"):
        request["messages"].insert(0, {"role": "system", "content": payload["instructions"]})
    return request


def chat_to_response(chat: dict[str, Any], requested_model: str = "") -> dict[str, Any]:
    choice = (chat.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    response_id = chat.get("id", f"resp_{time.time_ns()}")
    return {
        "id": response_id,
        "object": "response",
        "created_at": chat.get("created", int(time.time())),
        "status": "completed",
        "model": chat.get("model") or requested_model,
        "output": [{
            "id": f"msg_{response_id}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "output_text": text,
        "error": None,
        "incomplete_details": None,
        "metadata": {},
        "parallel_tool_calls": True,
        "usage": chat.get("usage"),
    }
