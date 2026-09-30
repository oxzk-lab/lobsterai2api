"""模型服务与未知模型回退测试。"""

from __future__ import annotations

import unittest
from typing import Any

from app.application.models import FALLBACK_MODEL, MODEL_MAP, ModelService, map_model
from app.domain.auth import Auth
from app.domain.persistence import AccountPersistence


class MemoryPersistence:
    """测试用内存存储, 行为对齐 Redis auths/data/models。"""

    def __init__(self) -> None:
        """初始化空的 auths Hash、data 与 models 缓存。"""
        self.auths: dict[str, str] = {}
        self.data: str | None = None
        self.models: str | None = None

    async def load_auths(self) -> list[Auth]:
        """读取全部账号凭证。"""
        return []

    async def save_auth(self, auth: Auth) -> None:
        """写入单个账号凭证。"""
        self.auths[auth.uid] = "{}"

    async def load_state(self) -> dict[str, Any]:
        """读取账号池运行状态。"""
        return {}

    async def save_state(self, data: dict[str, Any]) -> None:
        """写入账号池运行状态。"""
        self.data = None

    async def load_models(self) -> list[str]:
        """读取缓存的上游模型列表。"""
        import json

        if not self.models:
            return []
        return json.loads(self.models)

    async def save_models(self, models: list[str], ttl_seconds: int) -> None:
        """写入上游模型列表缓存。"""
        import json

        self.models = json.dumps([str(item) for item in models if item], ensure_ascii=False)

    async def close(self) -> None:
        """测试存储无需关闭连接。"""
        return None


class MapModelTest(unittest.TestCase):
    """map_model 回退与映射规则测试。"""

    def test_in_available_list_passthrough(self) -> None:
        """上游可用列表里的模型原样透传。"""
        self.assertEqual(map_model("glm-5", available={"glm-5", "glm-5.1"}), "glm-5")

    def test_unknown_model_falls_back(self) -> None:
        """不在可用列表的模型固定回退到 FALLBACK_MODEL。"""
        self.assertEqual(map_model("gpt-4o", available={"glm-5"}), FALLBACK_MODEL)
        self.assertEqual(map_model("", available=set()), FALLBACK_MODEL)

    def test_empty_available_falls_back_all(self) -> None:
        """上游不可用(空列表)时所有模型都回退。"""
        self.assertEqual(map_model("glm-5", available=set()), FALLBACK_MODEL)

    def test_map_table_rewrites_model(self) -> None:
        """MODEL_MAP 命中且目标可用时改写为目标模型。"""
        MODEL_MAP["gpt-4o"] = "deepseek-v4-pro"
        try:
            available = {"deepseek-v4-pro"}
            self.assertEqual(map_model("gpt-4o", available), "deepseek-v4-pro")
            self.assertEqual(map_model("gpt-4o", {"claude-sonnet"}), FALLBACK_MODEL)
        finally:
            MODEL_MAP.pop("gpt-4o")

    def test_none_available_skips_check(self) -> None:
        """available 为 None 表示跳过校验, 原样透传。"""
        self.assertEqual(map_model("any-model"), "any-model")


class FakeUpstream:
    """测试用上游, 只实现 models()。"""

    def __init__(self, ids: list[str], fail: bool = False) -> None:
        """初始化固定返回的模型列表与失败开关。"""
        self.ids = ids
        self.fail = fail
        self.calls = 0

    async def models(self, auth: Auth) -> list[str]:
        """模拟上游模型接口。"""
        self.calls += 1
        if self.fail:
            raise RuntimeError("upstream down")
        return self.ids


class FakeEntry:
    """测试用账号条目, 模拟 AccountPool.Entry。"""

    def __init__(self, auth: Auth) -> None:
        """初始化持有的凭证。"""
        self.auth = auth


class FakePool:
    """测试用账号池, 只实现 pick()。"""

    def __init__(self, has_account: bool = True) -> None:
        """初始化是否提供账号。"""
        self.has_account = has_account

    def pick(self, tried: set[str]) -> Any:
        """模拟挑选账号, 返回带 auth 属性的条目。"""
        return FakeEntry(Auth("token", uid="1")) if self.has_account else None


class ModelServiceTest(unittest.IsolatedAsyncioTestCase):
    """模型目录与缓存行为测试。"""

    async def test_empty_when_no_upstream(self) -> None:
        """无上游时返回空列表。"""
        service = ModelService(FakePool(), None, MemoryPersistence())
        result = await service.list_models()
        self.assertEqual(result, {"object": "list", "data": []})

    async def test_empty_when_no_account(self) -> None:
        """无可用账号时返回空列表。"""
        service = ModelService(FakePool(has_account=False), FakeUpstream(["glm-5"]), MemoryPersistence())
        result = await service.list_models()
        self.assertEqual(result, {"object": "list", "data": []})

    async def test_empty_when_upstream_fails(self) -> None:
        """上游请求失败时返回空列表而不是抛错。"""
        service = ModelService(FakePool(), FakeUpstream(["glm-5"], fail=True), MemoryPersistence())
        result = await service.list_models()
        self.assertEqual(result, {"object": "list", "data": []})

    async def test_success_returns_models_and_writes_cache(self) -> None:
        """获取成功时返回模型列表并写入缓存。"""
        store = MemoryPersistence()
        upstream = FakeUpstream(["glm-5", "glm-5.1"])
        service = ModelService(FakePool(), upstream, store)
        result = await service.list_models()
        self.assertEqual([entry["id"] for entry in result["data"]], ["glm-5", "glm-5.1"])
        self.assertEqual(await store.load_models(), ["glm-5", "glm-5.1"])

    async def test_available_models_uses_cache_before_upstream(self) -> None:
        """有缓存时直接用缓存, 不请求上游。"""
        store = MemoryPersistence()
        await store.save_models(["glm-5"], 3600)
        upstream = FakeUpstream(["glm-5.1"])
        service = ModelService(FakePool(), upstream, store)
        self.assertEqual(await service.available_models(), {"glm-5"})
        self.assertEqual(upstream.calls, 0)

    async def test_available_models_fetches_when_cache_empty(self) -> None:
        """无缓存时请求上游并回写缓存。"""
        store = MemoryPersistence()
        upstream = FakeUpstream(["glm-5"])
        service = ModelService(FakePool(), upstream, store)
        self.assertEqual(await service.available_models(), {"glm-5"})
        self.assertEqual(await store.load_models(), ["glm-5"])
        self.assertEqual(upstream.calls, 1)

    async def test_available_models_empty_when_upstream_fails(self) -> None:
        """无缓存且上游失败时返回空集合, 聊天请求将全部回退。"""
        service = ModelService(FakePool(), FakeUpstream(["glm-5"], fail=True), MemoryPersistence())
        self.assertEqual(await service.available_models(), set())


if __name__ == "__main__":
    unittest.main()
