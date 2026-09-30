from __future__ import annotations

import json
import time
from typing import Any


def _sse_text(value: Any) -> str:
    """将 SSE 字段转为可拼接字符串.

    上游首包常见 ``content: null``; ``dict.get(key, "")`` 在键存在且值为 None 时仍返回 None.
    """
    return value if isinstance(value, str) else ""


async def aggregate_sse(text: str) -> dict[str, Any]:
    """把上游 chat SSE 聚合成单个 OpenAI chat.completion 响应."""
    result: dict[str, Any] = {"id": f"chatcmpl-{time.time_ns()}", "object": "chat.completion", "created": int(time.time()),
                              "model": "", "choices": [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]}
    content: list[str] = []
    reasoning: list[str] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        result["id"] = chunk.get("id", result["id"])
        result["model"] = chunk.get("model", result["model"])
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            content.append(_sse_text(delta.get("content")))
            reasoning.append(_sse_text(delta.get("reasoning_content")))
            if choice.get("finish_reason"):
                result["choices"][0]["finish_reason"] = choice["finish_reason"]
    message = result["choices"][0]["message"]
    message["content"] = "".join(content)
    if any(reasoning):
        message["reasoning_content"] = "".join(reasoning)
    return result
