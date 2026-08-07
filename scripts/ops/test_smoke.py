"""newapi-local-smoke.py 的单元测试（unittest 风格，与 test_guardian.py 一致）。

被测文件名带连字符，无法直接 import，用 importlib 按路径加载。
admin_auth 的全部 HTTP/文件交互都经模块级 http_json/read_json，测试就地替换，
不发起真实网络请求。
"""
import importlib.util
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

_MODULE_PATH = Path(__file__).parent / "newapi-local-smoke.py"
_spec = importlib.util.spec_from_file_location("newapi_local_smoke", _MODULE_PATH)
smoke = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = smoke
_spec.loader.exec_module(smoke)

CACHE = {"token": "cached-tok", "user_id": 7}
CREDS = {"username": "u", "password": "p"}
LOGIN_OK = {"data": {"access_token": "new-tok", "id": 9}}


def fake_read_json(cache=CACHE):
    """按路径分发：token 缓存返回 cache（None 表示缓存缺失/损坏），其余返回登录凭据。"""

    def _read(path):
        if "admin-token-cache" in str(getattr(path, "name", path)):
            if cache is None:
                raise FileNotFoundError("no cache")
            return cache
        return CREDS

    return _read


class AdminAuthTests(unittest.TestCase):
    def test_cached_token_reused_on_200(self):
        """缓存校验 200 → 直接复用，不发登录请求"""
        calls = []

        def fake_http(url, **kwargs):
            calls.append(url)
            return 200, {}

        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("cached-tok", "7"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("/api/user/login", calls[0])

    def test_401_triggers_relogin(self):
        """确定性鉴权失败 401 → 重新登录并返回新 token"""
        self._assert_relogin_on(401)

    def test_403_keeps_cache_and_fails_run(self):
        """403 权限问题（重登无用）→ 本次检查失败，但保留缓存、不重新登录"""
        self._assert_cache_kept_on(403)

    def _assert_relogin_on(self, check_status):
        calls = []

        def fake_http(url, **kwargs):
            calls.append(url)
            if "/api/user/login" in url:
                return 200, LOGIN_OK
            return check_status, {}

        writes = []
        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
            patch.object(
                type(smoke.TOKEN_CACHE), "write_text",
                lambda self_path, text, **kw: writes.append(text) or len(text),
            ),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("new-tok", "9"))
        self.assertEqual(len(calls), 2)
        self.assertIn("/api/user/login", calls[1])
        self.assertTrue(writes, "登录成功后应回写 token 缓存")

    def test_429_keeps_cache_and_fails_run(self):
        """429 限流 → 本次检查失败，但保留缓存、不重新登录"""
        self._assert_cache_kept_on(429)

    def test_500_keeps_cache_and_fails_run(self):
        """5xx 服务端故障 → 本次检查失败，但保留缓存、不重新登录"""
        self._assert_cache_kept_on(500)

    def _assert_cache_kept_on(self, check_status):
        calls = []

        def fake_http(url, **kwargs):
            calls.append(url)
            return check_status, {}

        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
            patch.object(type(smoke.TOKEN_CACHE), "write_text") as write,
        ):
            with self.assertRaises(RuntimeError):
                smoke.admin_auth()

        self.assertEqual(len(calls), 1, "不得发起登录请求")
        write.assert_not_called()

    def test_network_error_keeps_cache_and_fails_run(self):
        """网络异常（URLError）→ 向上抛出让本次检查 FAIL，不重新登录"""
        calls = []

        def fake_http(url, **kwargs):
            calls.append(url)
            raise urllib.error.URLError("connection refused")

        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
            patch.object(type(smoke.TOKEN_CACHE), "write_text") as write,
        ):
            with self.assertRaises(urllib.error.URLError):
                smoke.admin_auth()

        self.assertEqual(len(calls), 1)
        write.assert_not_called()

    def test_login_failure_raises(self):
        """无缓存 + 登录被拒 → RuntimeError，不写缓存"""
        def fake_http(url, **kwargs):
            return 200, {"success": False, "message": "bad credentials"}

        with (
            patch.object(smoke, "read_json", fake_read_json(cache=None)),
            patch.object(smoke, "http_json", fake_http),
            patch.object(type(smoke.TOKEN_CACHE), "write_text") as write,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                smoke.admin_auth()

        self.assertIn("login failed", str(ctx.exception))
        write.assert_not_called()

    def test_cache_write_failure_still_returns_token(self):
        """缓存写失败（磁盘错误）只降级为不缓存，登录结果仍正常返回"""
        def fake_http(url, **kwargs):
            return 200, LOGIN_OK

        with (
            patch.object(smoke, "read_json", fake_read_json(cache=None)),
            patch.object(smoke, "http_json", fake_http),
            patch.object(
                type(smoke.TOKEN_CACHE), "write_text", side_effect=OSError("disk full")
            ),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("new-tok", "9"))

    def test_channel_policy_allows_agentrouter_claude_aggregate_models(self):
        channels = [
            {
                "id": 45,
                "name": "agentrouter",
                "models": "claude-opus-5,gpt-5.6-sol",
            }
        ]

        self.assertEqual(smoke.channel_policy_violations(channels), [])

    def test_channel_policy_rejects_codebuddy_sol_but_allows_hy3(self):
        channels = [
            {
                "id": 44,
                "name": "codebuddy",
                "models": "hy3-preview-agent,zg-hy3-preview-agent,gpt-5.6-sol,zg-wb-gpt-5.6-sol",
                "model_mapping": '{"zg-hy3-preview-agent":"hy3-preview-agent","zg-wb-gpt-5.6-sol":"gpt-5.6-sol"}',
            }
        ]

        self.assertEqual(
            smoke.channel_policy_violations(channels),
            ["44:codebuddy=gpt-5.6-sol,zg-wb-gpt-5.6-sol"],
        )

    def test_channel_policy_allows_explicit_agentrouter_claude_aliases(self):
        channels = [
            {
                "id": 45,
                "name": "agentrouter",
                "models": "zg-agent-claude-opus-5,zg-agent-claude-opus-4-8,zg-agent-gpt-5.6-sol",
                "model_mapping": '{"zg-agent-claude-opus-5":"claude-opus-5","zg-agent-claude-opus-4-8":"claude-opus-4-8","zg-agent-gpt-5.6-sol":"gpt-5.6-sol"}',
            }
        ]

        self.assertEqual(smoke.channel_policy_violations(channels), [])

    def test_channel_policy_rejects_unmapped_zg_alias(self):
        channels = [
            {
                "id": 45,
                "name": "agentrouter",
                "models": "gpt-5.6-sol,zg-gpt-5.6-sol",
            }
        ]

        self.assertEqual(
            smoke.channel_policy_violations(channels),
            ["45:agentrouter=unmapped_aliases:zg-gpt-5.6-sol"],
        )

    def test_live_agentrouter_fallback_is_not_expected_disabled(self):
        channels = [
            {"id": 45, "name": "agentrouter", "status": 1},
            {"id": 62, "name": "centos-eo-gpt", "status": 1},
        ]

        self.assertEqual(
            smoke.expected_disabled_violations(channels),
            ["62:centos-eo-gpt"],
        )

    def test_agentrouter_sol_fallback_posture_is_valid(self):
        channels = [
            {
                "id": 45,
                "name": "agentrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
                "models": "gpt-5.6-sol,zg-agent-gpt-5.6-sol",
                "model_mapping": '{"zg-agent-gpt-5.6-sol":"gpt-5.6-sol"}',
            },
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
                "models": "gpt-5.6-sol,zg-gpt-5.6-sol",
                "model_mapping": '{"zg-gpt-5.6-sol":"gpt-5.6-sol"}',
            },
        ]

        self.assertEqual(smoke.fallback_posture_violations(channels), [])

    def test_agentrouter_primary_tier_or_excess_weight_is_rejected(self):
        channels = [
            {
                "id": 45,
                "name": "agentrouter",
                "status": 1,
                "priority": 50,
                "weight": 10,
                "models": "gpt-5.6-sol",
            },
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
            },
        ]

        self.assertEqual(
            smoke.fallback_posture_violations(channels),
            ["45:agentrouter=priority=50,weight=10"],
        )

    def test_agentrouter_disabled_or_missing_is_rejected(self):
        self.assertEqual(
            smoke.fallback_posture_violations(
                [
                    {"id": 45, "name": "agentrouter", "status": 2, "priority": 40, "weight": 5},
                    {"id": 72, "name": "anyrouter", "status": 1, "priority": 40, "weight": 5},
                ]
            ),
            ["45:agentrouter=status=2"],
        )
        self.assertEqual(
            smoke.fallback_posture_violations([]), ["45:missing", "72:missing"]
        )

    def test_anyrouter_sol_fallback_posture_is_registered(self):
        channels = [
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
                "models": "gpt-5.6-sol,zg-gpt-5.6-sol",
                "model_mapping": '{"zg-gpt-5.6-sol":"gpt-5.6-sol"}',
            },
            {
                "id": 45,
                "name": "agentrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
            },
        ]

        self.assertEqual(smoke.fallback_posture_violations(channels), [])
        self.assertEqual(smoke.channel_policy_violations(channels), [])

    def test_anyrouter_primary_drift_rejected_and_claude_allowed(self):
        channels = [
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 50,
                "weight": 5,
                "models": "gpt-5.6-sol,claude-opus-5",
            },
            {
                "id": 45,
                "name": "agentrouter",
                "status": 1,
                "priority": 40,
                "weight": 5,
            },
        ]

        self.assertEqual(
            smoke.fallback_posture_violations(channels),
            ["72:anyrouter=priority=50"],
        )
        self.assertEqual(smoke.channel_policy_violations(channels), [])

    def test_main_fails_on_invalid_channels_response(self):
        """主流程：渠道接口 HTTP 500 或非法 items 时必须返回失败。"""
        self.addCleanup(smoke.failures.clear)
        for channel_status, channel_body in ((500, {}), (200, {"data": {"items": {}}})):
            with self.subTest(channel_status=channel_status, channel_body=channel_body):
                def fake_http(url, **kwargs):
                    if url.endswith("/api/status"):
                        return 200, {}
                    if "/api/channel/?p=0&page_size=1" in url:
                        return 200, {}
                    if "/api/channel/?p=0&page_size=200" in url:
                        return channel_status, channel_body
                    if "/v1/chat/completions" in url:
                        return 200, {"choices": [{"message": {"content": "OK"}}]}
                    raise AssertionError(f"unexpected URL: {url}")

                with (
                    patch.object(smoke, "http_json", fake_http),
                    patch.object(smoke, "read_json", fake_read_json()),
                    patch.object(smoke.socket, "create_connection"),
                    patch.object(smoke, "log"),
                ):
                    smoke.failures.clear()
                    self.assertEqual(smoke.main(), 1)


if __name__ == "__main__":
    unittest.main()
