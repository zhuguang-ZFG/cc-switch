"""newapi-local-smoke.py 的单元测试（unittest 风格，与 test_guardian.py 一致）。

被测文件名带连字符，无法直接 import，用 importlib 按路径加载。
admin_auth 的全部 HTTP/文件交互都经模块级 http_json/read_json，测试就地替换，
不发起真实网络请求。
"""
import ast
import importlib.util
import json
import re
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
    def test_pool_capacity_excludes_zero_weight_channels(self):
        channels = [
            {"id": 1, "status": 1, "weight": 0, "models": "critical"},
            {"id": 2, "status": 1, "weight": 5, "models": "critical,other"},
            {"id": 3, "status": 2, "weight": 5, "models": "critical"},
        ]
        self.assertEqual(smoke.serving_channel_ids(channels, "critical"), [2])

    def test_quarantine_policy_matches_guardian_recovery_exclusions(self):
        guardian_tree = ast.parse(
            Path(__file__).with_name("guardian.py").read_text(encoding="utf-8")
        )
        guardian_exclusions = None
        for node in guardian_tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name)
                and target.id == "AUTO_BAN_RECOVERY_EXCLUSIONS"
                for target in node.targets
            ):
                guardian_exclusions = set(ast.literal_eval(node.value))
                break

        self.assertEqual(guardian_exclusions, smoke.KNOWN_BROKEN_CHANNELS)

    def test_deployed_guardian_exclusions_match_repo_copy(self):
        """2026-08-20：仓库副本与部署副本（~/.omp/guardian/guardian.py）的
        排除集必须一致——当天部署副本漏更 75/98，tombstone 渠道面临被
        Guardian 恢复队列回捞的风险。部署文件缺失（CI 等环境）时跳过。
        """
        deployed = Path.home() / ".omp" / "guardian" / "guardian.py"
        if not deployed.exists():
            self.skipTest(f"deployed guardian not present: {deployed}")

        def _exclusions(path: Path) -> set:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name)
                    and target.id == "AUTO_BAN_RECOVERY_EXCLUSIONS"
                    for target in node.targets
                ):
                    return set(ast.literal_eval(node.value))
            return set()

        repo_set = _exclusions(Path(__file__).with_name("guardian.py"))
        deployed_set = _exclusions(deployed)
        self.assertEqual(
            repo_set,
            deployed_set,
            "部署副本排除集漂移：cp scripts/ops/guardian.py ~/.omp/guardian/ "
            "并重启 Guardian（apply-secrets-restart.ps1）",
        )

    def test_zero_output_billing_detection(self):
        """零输出计费侦测：GROUP BY/HAVING 过滤由 SQL 完成，假连接只回罐装行。"""

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class FakeConn:
            def __init__(self, rows):
                self._rows = rows

            def execute(self, *_args, **_kwargs):
                return FakeResult(self._rows)

            def close(self):
                pass

        with patch.object(
            smoke.sqlite3, "connect", return_value=FakeConn([(86, 3, 150000)])
        ):
            violations = smoke.zero_output_billing_violations()
        self.assertEqual(len(violations), 1)
        self.assertIn("channel=86", violations[0])
        self.assertIn("x3", violations[0])

        with patch.object(smoke.sqlite3, "connect", return_value=FakeConn([])):
            self.assertEqual(smoke.zero_output_billing_violations(), [])

    def test_disabled_ai168661_contracts_are_double_locked_and_quarantined(self):
        disabled = {
            channel_id
            for channel_id, contract in smoke.AI168661_CHANNEL_CONTRACTS.items()
            if contract["status"] == 2
        }
        self.assertTrue(disabled)
        self.assertTrue(disabled.issubset(smoke.KNOWN_BROKEN_CHANNELS))
        self.assertTrue(
            all(smoke.AI168661_CHANNEL_CONTRACTS[channel_id]["weight"] == 0 for channel_id in disabled)
        )

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

    def test_guardian_token_reused_without_password_login(self):
        """Guardian's long-lived token avoids creating a new server session."""
        calls = []

        def fake_read(path):
            if path == smoke.TOKEN_CACHE:
                raise FileNotFoundError("no session cache")
            if path == smoke.GUARDIAN_SECRETS:
                return {"newapi_token": "guardian-tok", "newapi_user": "7"}
            return CREDS

        def fake_http(url, **kwargs):
            calls.append((url, kwargs))
            return 200, {}

        with (
            patch.object(smoke, "read_json", fake_read),
            patch.object(smoke, "http_json", fake_http),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("guardian-tok", "7"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("/api/user/login", calls[0][0])
        self.assertEqual(
            calls[0][1]["headers"],
            {"Authorization": "Bearer guardian-tok", "New-Api-User": "7"},
        )

    def test_guardian_token_recovers_from_cached_permission_failure(self):
        """A stale session 403 can fall through to the long-lived token."""
        calls = []

        def fake_read(path):
            if path == smoke.TOKEN_CACHE:
                return {"token": "old-session", "user_id": 7}
            if path == smoke.GUARDIAN_SECRETS:
                return {"newapi_token": "guardian-tok", "newapi_user": "1"}
            return CREDS

        def fake_http(url, **kwargs):
            calls.append(url)
            if "/api/user/login" in url:
                raise AssertionError("password login is not allowed")
            if len(calls) == 1:
                return 403, {}
            return 200, {}

        with (
            patch.object(smoke, "read_json", fake_read),
            patch.object(smoke, "http_json", fake_http),
            patch.object(type(smoke.TOKEN_CACHE), "unlink"),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("guardian-tok", "1"))
        self.assertEqual(len(calls), 2)

    def test_expired_guardian_token_fails_without_password_login(self):
        """A stale long-lived token must not create recurring sessions."""
        calls = []

        def fake_read(path):
            if path == smoke.TOKEN_CACHE:
                raise FileNotFoundError("no session cache")
            if path == smoke.GUARDIAN_SECRETS:
                return {"newapi_token": "expired-guardian-tok"}
            return CREDS

        def fake_http(url, **kwargs):
            calls.append(url)
            if "/api/user/login" in url:
                return 200, LOGIN_OK
            return 401, {}

        with (
            patch.object(smoke, "read_json", fake_read),
            patch.object(smoke, "http_json", fake_http),
            patch.object(type(smoke.TOKEN_CACHE), "write_text"),
        ):
            with self.assertRaises(smoke._AdminAuthUnavailable):
                smoke.admin_auth()

        self.assertEqual(len(calls), 1)
        self.assertNotIn("/api/user/login", calls[0])
        self.assertIn("admin token auth", smoke.failures)
        smoke.failures.clear()

    def test_401_triggers_relogin(self):
        """确定性鉴权失败 401 → 丢弃过期缓存、重新登录并返回新 token"""
        self._assert_relogin_on(401)

    def test_403_drops_cache_and_fails_run(self):
        """403 权限问题（重登无用）→ FAIL 降级，删除缓存防永久毒化、不重新登录"""
        self._assert_validation_degraded(403, drop_cache=True)

    def _assert_relogin_on(self, check_status):
        calls = []
        unlinked = []

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
                type(smoke.TOKEN_CACHE), "unlink",
                lambda self_path, **kw: unlinked.append(str(self_path)) or None,
            ),
            patch.object(
                type(smoke.TOKEN_CACHE), "write_text",
                lambda self_path, text, **kw: writes.append(text) or len(text),
            ),
        ):
            token, user_id = smoke.admin_auth()

        self.assertEqual((token, user_id), ("new-tok", "9"))
        self.assertEqual(len(calls), 2)
        self.assertIn("/api/user/login", calls[1])
        self.assertTrue(unlinked, "401 应丢弃过期缓存再重新登录")
        self.assertTrue(writes, "登录成功后应回写 token 缓存")

    def test_429_keeps_cache_and_fails_run(self):
        """429 限流 → FAIL 降级，保留缓存、不重新登录"""
        self._assert_validation_degraded(429, drop_cache=False)

    def test_500_keeps_cache_and_fails_run(self):
        """5xx 服务端故障 → FAIL 降级，保留缓存、不重新登录"""
        self._assert_validation_degraded(500, drop_cache=False)

    def _assert_validation_degraded(self, check_status, *, drop_cache):
        """非 200 缓存校验（403/429/5xx）→ FAIL 降级：不中断、不重新登录。

        drop_cache=True（403）：删除缓存，下一轮重新登录，防缓存永久毒化；
        drop_cache=False（429/5xx）：保留缓存，防限流抖动烧掉 session 上限。
        """
        calls = []
        unlinked = []

        def fake_http(url, **kwargs):
            calls.append(url)
            return check_status, {}

        self.addCleanup(smoke.failures.clear)
        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
            patch.object(
                type(smoke.TOKEN_CACHE), "unlink",
                lambda self_path, **kw: unlinked.append(str(self_path)) or None,
            ),
            patch.object(type(smoke.TOKEN_CACHE), "write_text") as write,
        ):
            with self.assertRaises(smoke._AdminAuthUnavailable):
                smoke.admin_auth()

        self.assertEqual(len(calls), 1, "不得发起登录请求")
        self.assertIn("admin token auth", smoke.failures, "应记录显式 FAIL")
        write.assert_not_called()
        if drop_cache:
            self.assertTrue(unlinked, "403 应删除缓存，防旧 token 永久毒化")
        else:
            self.assertEqual(unlinked, [], "429/5xx 应保留缓存")

    def test_network_error_degrades_and_fails_run(self):
        """网络异常（URLError）→ FAIL 降级，保留缓存、不重新登录、不中断"""
        calls = []
        unlinked = []

        def fake_http(url, **kwargs):
            calls.append(url)
            raise urllib.error.URLError("connection refused")

        self.addCleanup(smoke.failures.clear)
        with (
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(smoke, "http_json", fake_http),
            patch.object(
                type(smoke.TOKEN_CACHE), "unlink",
                lambda self_path, **kw: unlinked.append(str(self_path)) or None,
            ),
        ):
            with self.assertRaises(smoke._AdminAuthUnavailable):
                smoke.admin_auth()

        self.assertEqual(len(calls), 1, "不得发起登录请求")
        self.assertIn("admin token auth", smoke.failures, "应记录显式 FAIL")
        self.assertEqual(unlinked, [], "网络抖动不得删除缓存")

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

    def test_ai168661_channel_posture_is_valid(self):
        channels = []
        for channel_id, expected in smoke.AI168661_CHANNEL_CONTRACTS.items():
            channels.append(
                {
                    "id": channel_id,
                    "name": expected["name"],
                    "type": 1,
                    "status": expected["status"],
                    "auto_ban": 1,
                    "base_url": "https://ai.168661.xyz",
                    "priority": expected["priority"],
                    "weight": expected["weight"],
                    "test_model": expected["test_model"],
                    "models": ",".join(expected["models"]),
                    "model_mapping": json.dumps(expected["mapping"]),
                }
            )

        self.assertEqual(smoke.ai168661_channel_violations(channels), [])

    def test_ai168661_channel_drift_and_missing_family_are_rejected(self):
        expected39 = smoke.AI168661_CHANNEL_CONTRACTS[39]
        expected78 = smoke.AI168661_CHANNEL_CONTRACTS[78]
        channels = [
            {
                "id": 39,
                "name": expected39["name"],
                "type": 1,
                "status": expected39["status"],
                "auto_ban": 1,
                "base_url": "https://ai.168661.xyz/v1",
                "priority": expected39["priority"],
                "weight": expected39["weight"],
                "test_model": expected39["test_model"],
                "models": ",".join((*expected39["models"], "grok-imagine-video")),
                "model_mapping": json.dumps(expected39["mapping"]),
            },
            {
                "id": 78,
                "name": expected78["name"],
                "type": 1,
                "status": 1,
                "auto_ban": 1,
                "base_url": "https://ai.168661.xyz",
                "priority": expected78["priority"],
                "weight": expected78["weight"],
                "test_model": expected78["test_model"],
                "models": ",".join(expected78["models"]),
                "model_mapping": "{}",
            },
        ]

        violations = smoke.ai168661_channel_violations(channels)
        self.assertEqual(len(violations), 2)
        self.assertNotIn("status=2", violations[0])
        self.assertIn("base_url=https://ai.168661.xyz/v1", violations[0])
        self.assertIn("grok-imagine-video", violations[0])
        self.assertIn("status=1", violations[1])
        self.assertIn("model_mapping=drifted", violations[1])

    def test_live_agentrouter_fallback_is_not_expected_disabled(self):
        channels = [
            {"id": 45, "name": "agentrouter", "status": 1},
            {"id": 62, "name": "centos-eo-gpt", "status": 1},
            {"id": 74, "name": "sharedchat-codex-sol", "status": 1},
        ]

        self.assertEqual(
            smoke.expected_disabled_violations(channels),
            [
                "62:centos-eo-gpt=re-entered service",
                "74:sharedchat-codex-sol=re-entered service",
            ],
        )

    def sol_posture_channels(self):
        channels = []
        for channel_id, expected in (
            (91, smoke.JIANZHILE_SOL_BACKUP_CONTRACT),
            (92, smoke.ZZZCODING_SOL_PRIMARY_CONTRACT),
        ):
            channels.append({"id": channel_id, **expected})
        expected = smoke.MUYUAN_SOL_FALLBACK_CONTRACT
        channels.append({
            "id": 83,
            "name": expected["name"],
            "type": expected["type"],
            "status": expected["status"],
            "auto_ban": expected["auto_ban"],
            "base_url": expected["base_url"],
            "priority": expected["priority"],
            "weight": expected["weight"],
            "test_model": expected["test_model"],
            "models": ",".join(expected["models"]),
            "model_mapping": json.dumps(expected["mapping"]),
            "header_override": json.dumps(expected["header_override"]),
        })
        return channels

    def test_zzzcoding_sol_primary_channel_contract(self):
        channels = self.sol_posture_channels()
        self.assertEqual(smoke.sol_primary_posture_violations(channels), [])
        channels[0]["priority"] = 26
        channels[1]["weight"] = 5
        self.assertEqual(
            smoke.sol_primary_posture_violations(channels),
            [
                "91:jianzhile-gpt-5.6-sol=priority=26",
                "92:zzzcoding-gpt-5.6-sol=weight=5",
            ],
        )

    def test_sol_auto_ban_outage_is_accepted_but_tier_locked(self):
        channels = self.sol_posture_channels()
        channels[0]["status"] = 3
        channels[1]["status"] = 2
        channels[2]["status"] = 3
        self.assertEqual(smoke.sol_primary_posture_violations(channels), [])
        channels[0]["auto_ban"] = 0
        self.assertEqual(
            smoke.sol_primary_posture_violations(channels),
            ["91:jianzhile-gpt-5.6-sol=auto_ban=0,status=3"],
        )
    def test_teamorouter_free_fallback_contract(self):
        expected = smoke.TEAMOROUTER_FREE_CONTRACT
        channel = {
            "id": 84,
            "name": expected["name"],
            "type": expected["type"],
            "status": expected["status"],
            "auto_ban": expected["auto_ban"],
            "base_url": expected["base_url"],
            "priority": expected["priority"],
            "weight": expected["weight"],
            "test_model": expected["test_model"],
            "models": ",".join(expected["models"]),
            "model_mapping": json.dumps(expected["mapping"]),
        }

        self.assertEqual(smoke.teamorouter_free_violations([channel]), [])

    def test_teamorouter_drift_and_auto_ban_posture(self):
        expected = smoke.TEAMOROUTER_FREE_CONTRACT
        channel = {
            "id": 84,
            "name": expected["name"],
            "type": expected["type"],
            "status": 3,
            "auto_ban": 1,
            "base_url": expected["base_url"],
            "priority": expected["priority"],
            "weight": expected["weight"],
            "test_model": expected["test_model"],
            "models": ",".join(expected["models"]),
            "model_mapping": json.dumps(expected["mapping"]),
        }

        self.assertEqual(smoke.teamorouter_free_violations([channel]), [])
        channel["priority"] = 50
        self.assertEqual(
            smoke.teamorouter_free_violations([channel]),
            ["84:teamorouter-deepseek-free=priority=50"],
        )
        self.assertEqual(
            smoke.teamorouter_free_violations([]),
            [],
        )


    def test_fallback_posture_is_valid(self):
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

    def test_backup_posture_accepts_enabled_low_tier_and_disabled_flap(self):
        channels = [
            {"id": 57, "name": "gorouter", "status": 1, "priority": 40, "weight": 5},
            {"id": 86, "name": "agentrouter-claude", "status": 1, "priority": 40, "weight": 13},
            {"id": 97, "name": "tabitoken-1", "status": 2, "priority": 40, "weight": 5},
        ]
        # 启用的低档位合规；97 禁用抖动不算违规（波动储备交给 Guardian）
        self.assertEqual(smoke.backup_posture_violations(channels), [])

    def test_backup_posture_rejects_primary_tier_drift(self):
        channels = [
            {"id": 94, "name": "justwoker-opus-1", "status": 1, "priority": 52, "weight": 8},
            {"id": 98, "name": "tabitoken-2", "status": 1, "priority": 40, "weight": 20},
        ]
        self.assertEqual(
            smoke.backup_posture_violations(channels),
            ["94:justwoker-opus-1=priority=52", "98:tabitoken-2=weight=20"],
        )

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
            # ch45 is in DEGRADED_ACCEPTED_DISABLED: its disabled state is an
            # accepted upstream-degradation posture, so only priority/weight
            # drift would be flagged; status=2 alone is not a violation.
            [],
        )
        self.assertEqual(
            smoke.fallback_posture_violations([]), ["45:missing", "72:missing"]
        )
        self.assertEqual(
            smoke.fallback_posture_violations(
                [
                    {"id": 45, "name": "agentrouter", "status": 2, "priority": 50, "weight": 10},
                    {"id": 72, "name": "anyrouter", "status": 1, "priority": 40, "weight": 5},
                ]
            ),
            # Degraded exemption covers status only; tier drift is still a
            # violation so the channel re-enters at the correct fallback tier.
            ["45:agentrouter=priority=50,weight=10"],
        )

    def test_anyrouter_claude_only_recovery_contract(self):
        channels = [
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 40,
                "weight": 2,
                "test_model": smoke.ANYROUTER_TEST_MODEL,
                "models": ",".join(smoke.ANYROUTER_CLAUDE_MODELS),
                "model_mapping": json.dumps(smoke.ANYROUTER_CLAUDE_MAPPING),
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

    def test_anyrouter_sol_or_missing_test_model_is_rejected(self):
        channels = [
            {
                "id": 72,
                "name": "anyrouter",
                "status": 1,
                "priority": 50,
                "weight": 5,
                "test_model": None,
                "models": "gpt-5.6-sol,claude-opus-5",
                "model_mapping": "{}",
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
        self.assertEqual(
            smoke.channel_policy_violations(channels),
            [
                "72:anyrouter=claude_only:missing=['claude-opus-4-8', "
                "'zg-agent-claude-opus-4-8', 'zg-agent-claude-opus-5', "
                "'zg-claude-opus-5'],extra=['gpt-5.6-sol']",
                "72:anyrouter=test_model=None",
                "72:anyrouter=model_mapping=drifted",
            ],
        )

    def test_option_policy_requires_automatic_channel_recovery(self):
        self.assertEqual(
            smoke.option_policy_violations(
                [
                    {"key": "AutomaticEnableChannelEnabled", "value": "true"},
                    {"key": "AutomaticDisableChannelEnabled", "value": "false"},
                    {"key": "channel_affinity_setting.enabled", "value": "true"},
                    {"key": "RetryTimes", "value": "1"},
                    {"key": "AutomaticDisableStatusCodes", "value": "401,402,403,502"},
                    {"key": "AutomaticRetryStatusCodes", "value": "408,500-503"},
                ]
            ),
            [],
        )
        self.assertEqual(
            smoke.option_policy_violations(
                [
                    {"key": "AutomaticEnableChannelEnabled", "value": "false"},
                    {"key": "AutomaticDisableChannelEnabled", "value": "false"},
                    {"key": "channel_affinity_setting.enabled", "value": "true"},
                    {"key": "RetryTimes", "value": "1"},
                    {"key": "AutomaticDisableStatusCodes", "value": "401,402,403,502"},
                    {"key": "AutomaticRetryStatusCodes", "value": "408,500-503"},
                ]
            ),
            ["AutomaticEnableChannelEnabled=false"],
        )
        self.assertEqual(
            smoke.option_policy_violations([]),
            [
                "AutomaticEnableChannelEnabled=missing",
                "AutomaticDisableChannelEnabled=missing",
                "channel_affinity_setting.enabled=missing",
                "RetryTimes=missing",
                "AutomaticDisableStatusCodes=missing",
                "AutomaticRetryStatusCodes=missing",
            ],
        )

    def test_affinity_rules_cover_canonical_and_zg_alias_models(self):
        rules = [
            {
                "name": name,
                "model_regex": [
                    "^(?:"
                    + "|".join(re.escape(model) for model in models)
                    + ")$"
                ],
                "enabled": name not in smoke.AFFINITY_DISABLED_RULES,
            }
            for name, models in smoke.AFFINITY_REQUIRED_MODELS.items()
        ]
        options = [
            {
                "key": "channel_affinity_setting.rules",
                "value": json.dumps(rules),
            }
        ]

        self.assertEqual(smoke.affinity_rule_violations(options), [])
    def test_affinity_disabled_rules_stay_disabled(self):
        """用户指示钉死机制：AFFINITY_DISABLED_RULES 内的规则必须 enabled=false。"""
        with patch.object(smoke, "AFFINITY_DISABLED_RULES", frozenset({"claude trace"})):
            rules = [
                {
                    "name": name,
                    "model_regex": [
                        "^(?:"
                        + "|".join(re.escape(model) for model in models)
                        + ")$"
                    ],
                    "enabled": name not in smoke.AFFINITY_DISABLED_RULES,
                }
                for name, models in smoke.AFFINITY_REQUIRED_MODELS.items()
            ]
            options = [
                {"key": "channel_affinity_setting.rules", "value": json.dumps(rules)}
            ]
            self.assertEqual(smoke.affinity_rule_violations(options), [])

    def test_affinity_disabled_rules_reject_reenable(self):
        base = {
            name: [
                "^(?:" + "|".join(re.escape(model) for model in models) + ")$"
            ]
            for name, models in smoke.AFFINITY_REQUIRED_MODELS.items()
        }
        with patch.object(smoke, "AFFINITY_DISABLED_RULES", frozenset({"claude trace"})):
            for scenario in ("explicit-true", "field-absent-defaults-true"):
                rules = [
                    {
                        "name": name,
                        "model_regex": patterns,
                        **(
                            {"enabled": True}
                            if scenario == "explicit-true" or name not in smoke.AFFINITY_DISABLED_RULES
                            else {}
                        ),
                    }
                    for name, patterns in base.items()
                ]
                options = [
                    {"key": "channel_affinity_setting.rules", "value": json.dumps(rules)}
                ]
                self.assertEqual(
                    smoke.affinity_rule_violations(options),
                    ["claude trace=must-stay-disabled"],
                    scenario,
                )

    def test_affinity_rules_reject_missing_zg_alias_and_invalid_regex(self):
        rules = [
            {
                "name": name,
                "model_regex": [
                    "^gpt-.*$" if name == "codex cli trace" else
                    "[" if name == "glm trace" else
                    "^(?:" + "|".join(re.escape(model) for model in models) + ")$"
                ],
                "enabled": name not in smoke.AFFINITY_DISABLED_RULES,
            }
            for name, models in smoke.AFFINITY_REQUIRED_MODELS.items()
        ]
        options = [
            {
                "key": "channel_affinity_setting.rules",
                "value": json.dumps(rules),
            }
        ]

        self.assertEqual(
            smoke.affinity_rule_violations(options),
            [
                "codex cli trace=missing:zg-gpt-5.6-sol",
                "glm trace=invalid-model-regex",
            ],
        )

    def test_critical_ability_postures_detect_missing_and_reset_rows(self):
        rows = [
            (channel_id, model, 1, priority, weight)
            for (channel_id, model), (priority, weight)
            in smoke.CRITICAL_ABILITY_POSTURES.items()
        ]
        self.assertEqual(smoke.critical_ability_posture_violations(rows), [])
        rows = [row for row in rows if row[:2] != (45, "gpt-5.6-sol")]
        rows = [
            (45, "zg-gpt-5.6-sol", 1, 39, 5)
            if row[:2] == (45, "zg-gpt-5.6-sol")
            else row
            for row in rows
        ]
        self.assertEqual(
            smoke.critical_ability_posture_violations(rows),
            [
                "45:gpt-5.6-sol=missing",
                "45:zg-gpt-5.6-sol=expected:enabled=1,priority=40,weight=5;"
                "actual=[(1, 39, 5)]",
            ],
        )

    def test_critical_ability_posture_accepts_disabled_auto_ban_rows(self):
        rows = [
            (channel_id, model, 0 if channel_id == 83 else 1, priority, weight)
            for (channel_id, model), (priority, weight)
            in smoke.CRITICAL_ABILITY_POSTURES.items()
        ]
        self.assertEqual(
            smoke.critical_ability_posture_violations(rows, {83}),
            [],
        )

    def test_critical_ability_posture_accepts_guardian_owned_disable(self):
        self.assertEqual(
            smoke.disabled_critical_ability_ids(
                [
                    (48, 2, 0),
                    (83, 2, 1),
                    (91, 1, 0),
                    (92, 2, 0),
                ],
                {48, 91},
            ),
            {48, 83},
        )

    def test_smoke_probe_skips_only_fully_attributed_disabled_routes(self):
        # 9048 是不属于任何策略排除集的合成渠道（ch48 自 2026-08-21 起已入
        # KNOWN_BROKEN_CHANNELS，不能再当"未归因"样例）。
        disabled_muse = {
            "id": 9048,
            "name": "test-unattributed",
            "status": 2,
            "auto_ban": 0,
            "weight": 12,
            "models": "muse-spark-1.2-contributor",
        }
        model = "muse-spark-1.2-contributor"

        reason = smoke.smoke_probe_skip_reason([disabled_muse], model, {9048})
        self.assertEqual(
            reason,
            "all declared channels attributed disabled ids=[9048]",
        )
        self.assertIsNone(smoke.smoke_probe_skip_reason([disabled_muse], model, set()))
        self.assertIsNone(
            smoke.smoke_probe_skip_reason(
                [{**disabled_muse, "status": 1}], model, {9048}
            )
        )
        self.assertIsNone(
            smoke.smoke_probe_skip_reason([disabled_muse], "missing-model", {9048})
        )

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
                    if url.endswith("/api/option/"):
                        return 200, {
                            "data": [
                                {"key": "AutomaticEnableChannelEnabled", "value": "true"}
                            ]
                        }
                    if "/api/channel/?p=0&page_size=200" in url:
                        return channel_status, channel_body
                    if "/v1/chat/completions" in url:
                        return 200, {"choices": [{"message": {"content": "OK"}}]}
                    if "/v1/responses" in url:
                        return 200, {"status": "completed", "output": []}
                    raise AssertionError(f"unexpected URL: {url}")

                with (
                    patch.object(smoke, "http_json", fake_http),
                    patch.object(smoke, "read_json", fake_read_json()),
                    patch.object(smoke.socket, "create_connection"),
                    patch.object(smoke, "log"),
                ):
                    smoke.failures.clear()
                    self.assertEqual(smoke.main(), 1)

    def test_main_403_on_cached_token_check_fails_but_continues(self):
        """P2-13：缓存 token 校验遇 403 → 记录 FAIL，后续检查（多 key/冒烟）照常执行。"""
        self.addCleanup(smoke.failures.clear)
        urls = []

        def fake_http(url, **kwargs):
            urls.append(url)
            if url.endswith("/api/status"):
                return 200, {}
            if "/api/channel/?p=0&page_size=1" in url:
                return 403, {}
            if "/v1/chat/completions" in url:
                return 200, {"choices": [{"message": {"content": "OK"}}]}
            if "/v1/responses" in url:
                return 200, {"status": "completed", "output": []}
            raise AssertionError(f"unexpected URL: {url}")

        with (
            patch.object(smoke, "http_json", fake_http),
            patch.object(smoke, "read_json", fake_read_json()),
            patch.object(
                type(smoke.TOKEN_CACHE), "unlink",
                lambda self_path, **kw: None,
            ),
            patch.object(smoke.socket, "create_connection"),
            patch.object(smoke, "log"),
        ):
            smoke.failures.clear()
            self.assertEqual(smoke.main(), 1)

        self.assertIn("admin token auth", smoke.failures, "403 应产生显式 FAIL 检查")
        self.assertFalse(
            any("/api/option/" in u for u in urls),
            "403 后不应继续调用依赖 admin token 的接口",
        )
        self.assertTrue(
            any("/v1/chat/completions" in u for u in urls),
            "403 后冒烟检查仍须执行",
        )


if __name__ == "__main__":
    unittest.main()
