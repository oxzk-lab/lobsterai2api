"""账号 Redis 存储与账号池持久化测试。"""

from __future__ import annotations

import json
import unittest
from typing import Any

from app.domain.auth import Auth, parse_auth
from app.domain.account_pool import AccountPool
from app.infrastructure.account_store import RedisAccountStore


class MemoryPersistence:
    """测试用内存存储, 行为对齐 Redis auths/data。"""

    def __init__(self) -> None:
        """初始化空的 auths Hash 与 data 文档。"""
        self.auths: dict[str, str] = {}
        self.data: str | None = None

    async def load_auths(self) -> list[Auth]:
        """读取全部账号凭证。"""
        result: list[Auth] = []
        for payload in self.auths.values():
            auth = parse_auth(json.loads(payload))
            if auth:
                result.append(auth)
        return result

    async def save_auth(self, auth: Auth) -> None:
        """写入单个账号凭证。"""
        self.auths[auth.uid] = json.dumps(auth.to_document(), ensure_ascii=False)

    async def load_state(self) -> dict[str, Any]:
        """读取账号池运行状态。"""
        if not self.data:
            return {}
        loaded = json.loads(self.data)
        return loaded if isinstance(loaded, dict) else {}

    async def save_state(self, data: dict[str, Any]) -> None:
        """写入账号池运行状态。"""
        self.data = json.dumps(data, ensure_ascii=False)

    async def close(self) -> None:
        """测试存储无需关闭连接。"""
        return None


class AuthDocumentTest(unittest.TestCase):
    """账号文档序列化测试。"""

    def test_roundtrip_nested_document(self) -> None:
        """嵌套文档可写回 Redis 后再解析。"""
        auth = Auth(
            access_token="token",
            refresh_token="refresh",
            expires_at=123,
            uid="92889",
            user_id="92889",
            nickname="tester",
            uuid="uuid",
            first_keyfrom="1",
            latest_keyfrom="2",
        )
        parsed = parse_auth(auth.to_document())
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.uid, "92889")
        self.assertEqual(parsed.access_token, "token")
        self.assertEqual(parsed.refresh_token, "refresh")
        self.assertEqual(parsed.nickname, "tester")

    def test_parse_flat_document(self) -> None:
        """扁平格式仍可解析。"""
        parsed = parse_auth({"accessToken": "abc", "uid": "1", "nickname": "n"})
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.access_token, "abc")
        self.assertEqual(parsed.uid, "1")


class RedisKeyTest(unittest.TestCase):
    """Redis 键名测试。"""

    def test_auths_and_data_keys(self) -> None:
        """auths 与 data 使用约定前缀。"""
        store = RedisAccountStore("redis://127.0.0.1:6379/0", "lb2a")
        self.assertEqual(store.auths_key, "lb2a:auths")
        self.assertEqual(store.data_key, "lb2a:data")
        self.assertEqual(store.login_key("abc"), "lb2a:login:abc")


class AccountPoolStorageTest(unittest.IsolatedAsyncioTestCase):
    """账号池 Redis 语义测试。"""

    async def test_save_reload_credits_and_auth(self) -> None:
        """凭证与运行状态都能从存储恢复。"""
        store = MemoryPersistence()
        pool = AccountPool(store)
        auth = Auth(access_token="token", uid="92889", nickname="tester")
        await store.save_auth(auth)
        await pool.reload()
        entry = pool.entries["92889"]
        entry.credits = 697
        await pool.save()

        restored = AccountPool(store)
        await restored.reload()
        self.assertEqual(restored.entries["92889"].credits, 697)
        self.assertEqual(restored.entries["92889"].auth.access_token, "token")
        self.assertEqual(restored.entries["92889"].auth.nickname, "tester")

    async def test_sync_drops_removed_accounts(self) -> None:
        """存储中删除的账号会从内存池移除。"""
        store = MemoryPersistence()
        pool = AccountPool(store)
        await store.save_auth(Auth(access_token="a", uid="1"))
        await store.save_auth(Auth(access_token="b", uid="2"))
        await pool.reload()
        self.assertEqual(set(pool.entries), {"1", "2"})
        del store.auths["2"]
        await pool.reload()
        self.assertEqual(set(pool.entries), {"1"})


class VercelConfigTest(unittest.TestCase):
    """Vercel 配置测试。"""

    def test_cron_triggers_checkin(self) -> None:
        """定时任务指向签到接口。"""
        from pathlib import Path

        config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(config["regions"], ["sfo1"])
        self.assertEqual(config["crons"][0]["path"], "/checkin")
        self.assertEqual(config["crons"][0]["schedule"], "0 1 * * *")

    def test_checkin_allows_get_for_cron(self) -> None:
        """Vercel Cron 只发 GET, 签到路由必须接受 GET。"""
        from app.api.routes.checkin import router

        methods: set[str] = set()
        for route in router.routes:
            if getattr(route, "path", None) == "/checkin":
                methods |= set(getattr(route, "methods", None) or [])
        self.assertIn("GET", methods)
        self.assertIn("POST", methods)

    def test_root_route_exists(self) -> None:
        """根路由 GET / 已注册。"""
        from app.api.routes.health import router

        paths = {getattr(route, "path", None) for route in router.routes}
        self.assertIn("/", paths)

    def test_favicon_ico_exists(self) -> None:
        """仓库包含可被浏览器识别的 favicon.ico。"""
        from pathlib import Path

        payload = Path("public/favicon.ico").read_bytes()
        self.assertGreaterEqual(len(payload), 22)
        self.assertEqual(payload[:4], b"\x00\x00\x01\x00")

    def test_vercel_executes_main_before_package_init(self) -> None:
        """Vercel 先 exec app/main.py, 包 __init__ 不能回导入 main.app。"""
        import importlib.util
        import sys
        from pathlib import Path

        for name in [key for key in sys.modules if key == "app" or key.startswith("app.")]:
            del sys.modules[name]
        spec = importlib.util.spec_from_file_location("app.main", Path("app/main.py"))
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["app.main"] = module
        spec.loader.exec_module(module)
        self.assertTrue(hasattr(module, "app"))


if __name__ == "__main__":
    unittest.main()
