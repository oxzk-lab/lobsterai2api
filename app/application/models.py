from __future__ import annotations

import time
from typing import Any

from app.application.static_models import STATIC_MODELS
from app.domain.account_pool import AccountPool
from app.infrastructure.upstream import Upstream


class ModelService:
    """Provide the public model catalog without exposing pool internals to routes."""

    def __init__(self, pool: AccountPool, upstream: Upstream | None):
        self.pool = pool
        self.upstream = upstream

    async def list_models(self) -> dict[str, Any]:
        ids: list[str] = []
        if self.upstream:
            entry = self.pool.pick(set())
            if entry:
                ids = await self.upstream.models(entry.auth)

        data = STATIC_MODELS if not ids else [
            {"id": model_id, "object": "model", "created": int(time.time()), "owned_by": "lobsterai"}
            for model_id in ids
        ]
        return {"object": "list", "data": data}
