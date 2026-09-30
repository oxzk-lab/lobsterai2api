from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import get_container
from app.api.schemas import ChatCompletionRequest, ResponsesRequest
from app.application.chat import ChatService

router = APIRouter()


def service_error(exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": str(exc), "type": "api_error", "code": "upstream_error"}},
        status_code=503,
    )


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest) -> Any:
    container = get_container(request)
    payload = body.model_dump()
    service = ChatService(container)
    try:
        if body.stream:
            return StreamingResponse(service.stream_chat(payload), media_type="text/event-stream")
        return await service.chat(payload)
    except Exception as exc:
        return service_error(exc)


@router.post("/v1/responses")
async def responses(request: Request, body: ResponsesRequest) -> Any:
    container = get_container(request)
    payload = body.model_dump()
    service = ChatService(container)
    try:
        if body.stream:
            return StreamingResponse(service.stream_response(payload), media_type="text/event-stream")
        return await service.response(payload)
    except Exception as exc:
        return service_error(exc)
