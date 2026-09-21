from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from app.application.container import ApplicationContainer
from app.application.failure_policy import AccountFailurePolicy
from app.application.response_format import chat_to_response, response_request
from app.application.sse import aggregate_sse
from app.infrastructure.upstream import UpstreamError


class ChatService:
    def __init__(self, services: ApplicationContainer):
        self.services = services
        self.failure_policy = AccountFailurePolicy(services.pool, services.settings)

    async def _upstream_response(self, payload: dict[str, Any]) -> httpx.Response:
        if not self.services.upstream:
            raise RuntimeError("upstream base URL is not configured")
        body = json.dumps(payload, ensure_ascii=False).encode()
        tried: set[str] = set()
        last_error: Exception | None = None
        for _ in range(self.services.settings.max_account_retries):
            entry = self.services.pool.pick(tried)
            if not entry:
                break
            tried.add(entry.auth.uid)
            try:
                response = await self.services.upstream.chat(entry.auth, body)
                entry.errors = 0
                return response
            except UpstreamError as exc:
                last_error = exc
                await self.failure_policy.record(entry, exc)
        raise RuntimeError(str(last_error or "all accounts unavailable"))

    @asynccontextmanager
    async def _upstream_stream(self, payload: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        if not self.services.upstream:
            raise RuntimeError("upstream base URL is not configured")
        body = json.dumps(payload, ensure_ascii=False).encode()
        tried: set[str] = set()
        last_error: Exception | None = None
        for _ in range(self.services.settings.max_account_retries):
            entry = self.services.pool.pick(tried)
            if not entry:
                break
            tried.add(entry.auth.uid)
            entered = False
            try:
                async with self.services.upstream.chat_stream(entry.auth, body) as response:
                    entered = True
                    entry.errors = 0
                    yield response
                    return
            except UpstreamError as exc:
                if entered:
                    raise
                last_error = exc
                await self.failure_policy.record(entry, exc)
        raise RuntimeError(str(last_error or "all accounts unavailable"))

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._upstream_response({**payload, "stream": True})
        try:
            return await aggregate_sse(response.text)
        finally:
            await response.aclose()

    async def response(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_payload = response_request(payload)
        result = await self.chat(chat_payload)
        return chat_to_response(result, payload.get("model", ""))

    async def stream_chat(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with self._upstream_stream({**payload, "stream": True}) as response:
            async for chunk in response.aiter_bytes():
                yield chunk

    async def stream_response(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async with self._upstream_stream({**response_request(payload), "stream": True}) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices", []):
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield f"event: response.output_text.delta\ndata: {json.dumps({'type': 'response.output_text.delta', 'delta': text}, ensure_ascii=False)}\n\n".encode()
            yield b"event: response.completed\ndata: {\"type\":\"response.completed\"}\n\n"
