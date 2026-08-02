import os
import sys
import json
import time
import tempfile
import logging
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from collections import defaultdict, deque
from pathlib import Path

_TEST_HOME = tempfile.TemporaryDirectory()
os.environ["HOME"] = _TEST_HOME.name
os.environ["USERPROFILE"] = _TEST_HOME.name
sys.path.insert(0, str(Path(__file__).parent))
import guardian


class FakeNewAPI:
    def __init__(self):
        self.updates = []
        self.channels = {}
        self.test_results = deque()
        self.test_calls = []
        self.enable_calls = []
        self.disable_calls = []

    def update_channel(self, channel):
        self.updates.append(channel.copy())
        return True

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_channels(self):
        return list(self.channels.values())
    def test_channel(self, channel_id):
        self.test_calls.append(channel_id)
        return self.test_results.popleft()

    def enable_channel(self, channel_id):
        self.enable_calls.append(channel_id)
        return True

    def disable_channel(self, channel_id):
        self.disable_calls.append(channel_id)
        return True

    def _request(self, *_args, **_kwargs):
        return {"data": {"7": ["deepseek-v4-flash"]}}


class FakeTelegram:
    def send_alert(self, *_args):
        return True


def make_engine(state=None):
    engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
    engine.newapi = FakeNewAPI()
    engine.telegram = FakeTelegram()
    engine.state = state or {
        "weight_history": {},
        "degraded_channels": {},
    }
    engine.channel_perf = defaultdict(
        lambda: deque(maxlen=guardian.WEIGHT_ADJUST_WINDOW)
    )
    engine._last_channel_tests = {}
    engine._cleanup_count = 0
    engine._full_scan_failures = {}
    engine._save_state = lambda: None
    return engine


def add_samples(engine, channel_id, count, *, healthy=True, response_time=1000):
    for index in range(count):
        engine.channel_perf[channel_id].append(
            {
                "time": index + 1,
                "response_time": response_time,
                "healthy": healthy,
            }
        )


class ChannelHealthTests(unittest.TestCase):
    def test_repeated_poll_of_one_slow_result_does_not_count_as_three_failures(self):
        newapi = FakeNewAPI()
        health = guardian.HealthChecker(newapi)
        channel = {
            "id": 45,
            "status": 1,
            "response_time": 131301,
            "test_time": 123,
        }

        results = [health.check_channel(channel) for _ in range(3)]

        self.assertTrue(all(healthy for healthy, _, _ in results))
        self.assertEqual(newapi.test_calls, [])

    def test_missing_test_time_still_dedupes_one_stale_slow_result(self):
        """兜底:NewAPI 未来不返回 test_time 时,同一份慢数据也不得重复计数触发主动复测。"""
        newapi = FakeNewAPI()
        health = guardian.HealthChecker(newapi)
        channel = {
            "id": 45,
            "status": 1,
            "response_time": 131301,
            "test_time": None,
        }

        results = [health.check_channel(channel) for _ in range(3)]

        self.assertTrue(all(healthy for healthy, _, _ in results))
        self.assertEqual(newapi.test_calls, [])

    def test_three_distinct_slow_results_trigger_one_active_test(self):
        newapi = FakeNewAPI()
        newapi.test_results.append((False, "timed out"))
        health = guardian.HealthChecker(newapi)

        results = [
            health.check_channel({
                "id": 45,
                "status": 1,
                "response_time": 131301,
                "test_time": test_time,
            })
            for test_time in (1, 2, 3)
        ]

        self.assertEqual([healthy for healthy, _, _ in results], [True, True, False])
        self.assertEqual(newapi.test_calls, [45])




class TelegramCommandTests(unittest.TestCase):
    def test_help_escapes_placeholder_tags_for_html_parse_mode(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.send = Mock(return_value=True)

        bot._cmd_help()

        text = bot.send.call_args.args[0]
        self.assertNotIn("<proxy>", text)
        self.assertNotIn("<channel_id>", text)
        self.assertIn("&lt;proxy&gt;", text)
        self.assertIn("&lt;channel_id&gt;", text)

    def test_ignores_commands_from_unauthorized_chat(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.get_updates = lambda timeout=1: [
            {"message": {"chat": {"id": 999999}, "text": "/disable 48"}}
        ]
        bot._cmd_disable = Mock()
        bot.send = Mock()

        bot.process_commands(object())

        bot._cmd_disable.assert_not_called()

    def test_processes_commands_from_authorized_chat(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.get_updates = lambda timeout=1: [
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 5345665818},
                    "text": "/status",
                }
            }
        ]
        bot._cmd_status = Mock()
        bot.send = Mock()

        bot.process_commands(object())

        bot._cmd_status.assert_called_once()

    def test_rejects_group_sender_when_no_whitelist_configured(self):
        """未配置白名单时，群组内发送者（from.id != chat.id）不得执行管理命令"""
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.get_updates = lambda timeout=1: [
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 999999},
                    "text": "/disable 48",
                }
            }
        ]
        bot._cmd_disable = Mock()
        bot.send = Mock()

        bot.process_commands(object())

        bot._cmd_disable.assert_not_called()

    def test_rejects_group_member_not_in_allowed_users(self):
        """chat 匹配但发送者不在白名单（群组场景）时拒绝执行"""
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.allowed_users = {"5345665818"}
        bot.get_updates = lambda timeout=1: [
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 999999},
                    "text": "/disable 48",
                }
            }
        ]
        bot._cmd_disable = Mock()
        bot.send = Mock()

        bot.process_commands(object())

        bot._cmd_disable.assert_not_called()

    def test_accepts_group_member_in_allowed_users(self):
        """chat 匹配且发送者在白名单时执行"""
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.allowed_users = {"5345665818", "100001"}
        bot.get_updates = lambda timeout=1: [
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 100001},
                    "text": "/status",
                }
            }
        ]
        bot._cmd_status = Mock()
        bot.send = Mock()

        bot.process_commands(object())

        bot._cmd_status.assert_called_once()

    def test_handler_error_does_not_drop_following_commands(self):
        """批内某条命令 handler 抛异常，不中断该批后续合法命令"""
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.chat_id = "5345665818"
        bot.get_updates = lambda timeout=1: [
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 5345665818},
                    "text": "/disable 48",
                }
            },
            {
                "message": {
                    "chat": {"id": 5345665818},
                    "from": {"id": 5345665818},
                    "text": "/status",
                }
            },
        ]
        bot._cmd_disable = Mock(side_effect=RuntimeError("boom"))
        bot._cmd_status = Mock()
        bot.send = Mock()

        with patch.object(guardian.logger, "exception"):
            bot.process_commands(object())

        bot._cmd_status.assert_called_once()


class FullHealthScanTests(unittest.TestCase):
    def test_requires_three_soft_failures_before_degrading(self):
        engine = make_engine()
        engine._full_scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45,
            "name": "agentrouter",
            "status": 1,
            "weight": 15,
            "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.extend([
            (False, "timed out"),
            (False, "timed out"),
            (False, "timed out"),
        ])

        for expected_updates in (0, 0, 1):
            engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
            engine.full_health_scan()
            self.assertEqual(len(engine.newapi.updates), expected_updates)

        self.assertEqual(engine.newapi.updates[0]["weight"], 7)


class RetryPolicyTests(unittest.TestCase):
    def make_client(self, value):
        client = guardian.NewAPIClient("https://example.invalid", "token", "1")
        updates = []

        def request(method, path, data=None, timeout=15):
            if method == "GET":
                return {
                    "data": [
                        {"key": "AutomaticRetryStatusCodes", "value": value}
                    ]
                }
            updates.append((method, path, data, timeout))
            return {"success": True}

        client._request = request
        return client, updates

    def test_excludes_402_without_dropping_neighboring_statuses(self):
        client, updates = self.make_client(
            "100-199,300-399,401-407,409-499,500-504,505-599"
        )

        self.assertTrue(client.exclude_retry_status_code(402))

        self.assertEqual(
            updates[0][2]["value"],
            "100-199,300-399,401,403-407,409-499,500-504,505-599",
        )

    def test_does_not_write_when_402_is_already_excluded(self):
        client, updates = self.make_client(
            "100-199,300-399,409-499,500-504,505-599"
        )

        self.assertTrue(client.exclude_retry_status_code(402))
        self.assertEqual(updates, [])


class WeightAdjustmentTests(unittest.TestCase):
    def test_records_each_newapi_test_result_once(self):
        engine = make_engine()
        channel = {"id": 7, "test_time": 100, "response_time": 900}

        self.assertTrue(engine._record_channel_perf(channel, True))
        self.assertFalse(engine._record_channel_perf(channel, True))

        channel["test_time"] = 101
        self.assertTrue(engine._record_channel_perf(channel, True))
        self.assertEqual(len(engine.channel_perf[7]), 2)

    def test_waits_for_a_complete_window_before_adjusting(self):
        engine = make_engine()
        channel = {"id": 7, "name": "slow", "status": 1, "weight": 10}
        add_samples(engine, 7, guardian.WEIGHT_ADJUST_WINDOW - 1, healthy=False)

        engine._auto_adjust_weights([channel])

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(channel["weight"], 10)

    def test_does_not_boost_an_unmanaged_healthy_channel(self):
        engine = make_engine()
        channel = {"id": 7, "name": "healthy", "status": 1, "weight": 10}
        add_samples(engine, 7, guardian.WEIGHT_ADJUST_WINDOW)

        engine._auto_adjust_weights([channel])

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(channel["weight"], 10)
        self.assertEqual(len(engine.channel_perf[7]), 0)

    def test_consumes_window_after_one_degraded_recovery_step(self):
        engine = make_engine(
            {
                "weight_history": {"7": {"weight": 10, "priority": 50}},
                "degraded_channels": {
                    "7": {"name": "recovering", "original_weight": 10}
                },
            }
        )
        channel = {"id": 7, "name": "recovering", "status": 1, "weight": 5}
        add_samples(engine, 7, guardian.WEIGHT_ADJUST_WINDOW)

        engine._auto_adjust_weights([channel])
        engine._auto_adjust_weights([channel])

        self.assertEqual([item["weight"] for item in engine.newapi.updates], [6])
        self.assertEqual(len(engine.channel_perf[7]), 0)

    def test_degrade_skipped_within_self_cooldown(self):
        """降权受自身冷却限制，不因告警冷却决定是否执行"""
        engine = make_engine()
        engine.state["degraded_channels"]["7"] = {
            "name": "slow",
            "original_weight": 10,
            "degraded_weight": 5,
            "reason": "test",
            "time": datetime.now().isoformat(),
        }
        channel = {"id": 7, "name": "slow", "status": 1, "weight": 5}

        ok = engine.degrade_channel_weight(channel, "again")

        self.assertFalse(ok)
        self.assertEqual(engine.newapi.updates, [])


class RecoveryTests(unittest.TestCase):
    def test_restores_saved_weight_even_when_disabled_channel_kept_nonzero_weight(self):
        engine = make_engine(
            {
                "weight_history": {"7": {"weight": 10, "priority": 50}},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "recovered",
            "status": 1,
            "models": "deepseek-v4-flash",
            "weight": 1,
            "priority": 50,
        }
        engine._balance_pool_weights = lambda _models, _channel_id, weight: weight

        engine._auto_join_pool(7, "recovered")

        self.assertEqual([item["weight"] for item in engine.newapi.updates], [10])


    def test_waits_for_recovery_cooldown(self):
        engine = make_engine(
            {
                "disabled_channels": [
                    {
                        "id": 7,
                        "name": "cooling",
                        "time": datetime.now().isoformat(),
                    }
                ],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )

        engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.test_calls, [])

    def test_rechecks_and_re_disables_newapi_auto_enabled_channel(self):
        record = {
            "id": 7,
            "name": "flapping",
            "time": (datetime.now() - timedelta(minutes=10)).isoformat(),
        }
        engine = make_engine(
            {
                "disabled_channels": [record],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "flapping",
            "status": 1,
        }
        engine.newapi.test_results.extend([(False, "quota")] * 3)

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.test_calls, [7, 7, 7])
        self.assertEqual(engine.newapi.disable_calls, [7])
        self.assertEqual(record["recovery_failures"], 1)
        self.assertIn(record, engine.state["disabled_channels"])

    def test_joins_only_after_two_of_three_checks_pass(self):
        record = {
            "id": 7,
            "name": "recovered",
            "time": (datetime.now() - timedelta(minutes=10)).isoformat(),
        }
        engine = make_engine(
            {
                "disabled_channels": [record],

                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "recovered",
            "status": 1,
        }
        engine.newapi.test_results.extend(
            [(True, "ok"), (False, "transient"), (True, "ok")]
        )
        joined = []
        roles = []
        engine._auto_join_pool = lambda channel_id, name: not joined.append((channel_id, name))
        engine._update_omp_roles = lambda channel_id, name: roles.append((channel_id, name))

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.test_calls, [7, 7, 7])
        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine.state["disabled_channels"], [])
        self.assertEqual(joined, [(7, "recovered")])
        self.assertEqual(roles, [(7, "recovered")])

    def test_joins_pool_with_channel_declared_models_not_api_models(self):
        """opencode-go 等 model_mapping 左侧别名不在 /api/models 中，仍须加入聚合池"""
        engine = make_engine(
            {
                "weight_history": {"48": {"weight": 5, "priority": 50}},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[48] = {
            "id": 48,
            "name": "opencode-go-flash",
            "status": 1,
            "models": "opencode-go",
            "weight": 5,
            "priority": 50,
        }
        engine._balance_pool_weights = lambda _m, _cid, w: w

        ok = engine._auto_join_pool(48, "opencode-go-flash")

        self.assertTrue(ok)
        self.assertEqual(
            engine.state["joined_channels"]["48"]["models"], ["opencode-go"]
        )

class PoolBalanceTests(unittest.TestCase):
    def test_caps_only_recovered_channel_to_peer_average(self):
        engine = make_engine()
        engine.newapi.channels = {
            channel_id: {
                "id": channel_id,
                "name": f"peer-{channel_id}",
                "status": 1,
                "models": "deepseek-v4-flash",
                "weight": 20,
            }
            for channel_id in range(1, 6)
        }

        balanced = engine._balance_pool_weights(["deepseek-v4-flash"], 7, 100)

        self.assertEqual(balanced, 20)
        self.assertEqual(engine.newapi.updates, [])

    def test_keeps_saved_weight_for_small_pool(self):
        engine = make_engine()
        engine.newapi.channels = {
            1: {
                "id": 1,
                "name": "peer",
                "status": 1,
                "models": "deepseek-v4-flash",
                "weight": 20,
            }
        }

        balanced = engine._balance_pool_weights(["deepseek-v4-flash"], 7, 10)

        self.assertEqual(balanced, 10)
        self.assertEqual(engine.newapi.updates, [])


class StateCleanupTests(unittest.TestCase):
    def test_preserves_aged_disabled_channel_and_its_saved_weight(self):
        old = (datetime.now() - timedelta(hours=guardian.STATE_MAX_AGE_HOURS + 1)).isoformat()
        record = {"id": 7, "name": "disabled", "time": old}
        engine = make_engine(
            {
                "disabled_channels": [record],
                "weight_history": {"7": {"weight": 10, "priority": 50, "time": old}},
                "degraded_channels": {},
                "joined_channels": {},
                "restarted_proxies": {},
                "restart_counts": {},
            }
        )
        engine.newapi.channels[7] = {"id": 7, "status": 2, "weight": 10}
        engine._cleanup_count = guardian.STATE_CLEANUP_INTERVAL - 1

        engine.cleanup_stale_state()

        self.assertEqual(engine.state["disabled_channels"], [record])
        self.assertIn("7", engine.state["weight_history"])

    def test_removes_aged_unmanaged_weight_history(self):
        old = (datetime.now() - timedelta(hours=guardian.STATE_MAX_AGE_HOURS + 1)).isoformat()
        engine = make_engine(
            {
                "disabled_channels": [],
                "weight_history": {"7": {"weight": 10, "priority": 50, "time": old}},
                "degraded_channels": {},
                "joined_channels": {},
                "restarted_proxies": {},
                "restart_counts": {},
            }
        )
        engine.newapi.channels[7] = {"id": 7, "status": 1, "weight": 10}
        engine._cleanup_count = guardian.STATE_CLEANUP_INTERVAL - 1

        engine.cleanup_stale_state()

        self.assertNotIn("7", engine.state["weight_history"])


class PoolJoinTests(unittest.TestCase):
    def test_same_weight_recovery_still_enters_stability_monitoring(self):
        engine = make_engine(
            {
                "weight_history": {"7": {"weight": 10, "priority": 50}},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "recovered",
            "status": 1,
            "models": "deepseek-v4-flash",
            "weight": 10,
            "priority": 50,
        }
        engine._balance_pool_weights = lambda *_args: 10

        self.assertTrue(engine._auto_join_pool(7, "recovered"))

        self.assertIn("7", engine.state["joined_channels"])
        self.assertEqual(engine.newapi.updates, [])

    def test_re_disables_channel_when_pool_join_fails(self):
        record = {
            "id": 7,
            "name": "recovered",
            "time": (datetime.now() - timedelta(minutes=10)).isoformat(),
        }
        engine = make_engine(
            {
                "disabled_channels": [record],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[7] = {"id": 7, "name": "recovered", "status": 1}
        engine.newapi.test_results.extend([(True, "ok")] * 3)
        engine._auto_join_pool = lambda *_args: False
        role_updates = []
        engine._update_omp_roles = lambda *_args: role_updates.append(True)

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.disable_calls, [7])
        self.assertIn(record, engine.state["disabled_channels"])
        self.assertEqual(role_updates, [])


class ProxyRestartTests(unittest.TestCase):
    def test_constructor_retains_health_checker(self):
        health = Mock()
        with patch.object(guardian.AutoFixEngine, "_load_state", return_value={}):
            engine = guardian.AutoFixEngine(Mock(), Mock(), health)

        self.assertIs(engine.health, health)

    def test_restarts_anyrouter_from_its_own_script(self):
        engine = make_engine({"restart_counts": {}, "restarted_proxies": {}})
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (True, "ok")

        with (
            patch.object(guardian.subprocess, "run"),
            patch.object(guardian.subprocess, "Popen") as popen,
            patch.object(guardian.time, "sleep"),
        ):
            restarted = engine.restart_local_proxy("anyrouter", 8789)

        self.assertTrue(restarted)
        command = popen.call_args.args[0]
        self.assertTrue(command[1].endswith("anyrouter-proxy.py"))
        self.assertTrue(popen.call_args.kwargs["cwd"].endswith("anyrouter-proxy"))

    def test_newapi_restart_requires_three_consecutive_failures(self):
        """单次/两次瞬态失败不得触发破坏性重启；连续 3 次才重启"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()
        g._check_cycle()
        self.assertEqual(g.autofix.restart_newapi_container.call_count, 0)

        g._check_cycle()
        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)

    def test_newapi_restart_streak_resets_on_recovery(self):
        """失败 2 次后恢复健康，计数清零，不触发重启"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.side_effect = [(False, "down"), (False, "down"), (True, "ok")]
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        for _ in range(3):
            g._check_cycle()

        self.assertEqual(g.autofix.restart_newapi_container.call_count, 0)

    def test_local_proxy_restart_not_blocked_by_alert_cooldown(self):
        """代理故障时自愈重启不受告警冷却限制；冷却只控通知"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (False, "down")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False  # 告警在冷却 → 不发通知
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        # 4 个本地代理全部失败时，重启必须全部执行
        self.assertEqual(g.autofix.restart_local_proxy.call_count, 4)
        g.telegram.send_alert.assert_not_called()

    def test_newapi_restart_success_enters_long_cooldown(self):
        """重启成功写 newapi_restart_time（长冷却）；失败只写 fail_time（短退避）"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.newapi.get_status = Mock(return_value=True)
        engine.telegram = Mock()

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)),
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertTrue(ok)
        self.assertIsNotNone(engine.state.get("newapi_restart_time"))
        self.assertNotIn("newapi_restart_fail_time", engine.state)
        engine._save_state.assert_called()

    def test_newapi_restart_failure_sets_short_backoff(self):
        """重启失败只写 fail_time 短退避，不写成功冷却"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()

        with (
            patch.object(
                guardian.subprocess,
                "run",
                return_value=guardian.subprocess.CompletedProcess(
                    ["ssh"], 1, stdout=b"", stderr=b"connection refused"
                ),
            ),
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        self.assertIsNone(engine.state.get("newapi_restart_time"))
        self.assertIsNotNone(engine.state.get("newapi_restart_fail_time"))
        engine._save_state.assert_called()

    def test_newapi_restart_timeout_sets_short_backoff(self):
        """SSH 超时（命令未执行）只写 fail_time 短退避"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()

        with (
            patch.object(
                guardian.subprocess,
                "run",
                side_effect=guardian.subprocess.TimeoutExpired("ssh", 30),
            ),
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        self.assertIsNone(engine.state.get("newapi_restart_time"))
        self.assertIsNotNone(engine.state.get("newapi_restart_fail_time"))
        engine._save_state.assert_called()

    def test_newapi_restart_uses_argv_ssh_without_local_fallback(self):
        """SSH 用 argv 调用、不含 StrictHostKeyChecking=no、无本地 podman fallback"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.newapi.get_status = Mock(return_value=True)

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)) as run,
            patch.object(guardian.time, "sleep"),
        ):
            engine.restart_newapi_container()

        args = run.call_args.args[0]
        self.assertEqual(args[0], "ssh")  # 顶层命令是 ssh，不是本地 podman fallback
        self.assertNotIn("StrictHostKeyChecking=no", args)
        self.assertEqual(run.call_args.kwargs.get("shell", False), False)
        self.assertEqual(run.call_count, 1)

    def test_newapi_restart_respects_failure_backoff(self):
        """重启失败进入 60s 退避：冷却期内再次调用被挡住，不执行 SSH"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": datetime.now().isoformat(),
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.newapi.get_status = Mock(return_value=True)

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)) as run,
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        run.assert_not_called()

    def test_newapi_restart_respects_success_cooldown(self):
        """重启成功后 30min 冷却：冷却期内再次调用被挡住，不执行 SSH"""
        engine = make_engine({
            "newapi_restart_time": datetime.now().isoformat(),
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.newapi.get_status = Mock(return_value=True)

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)) as run,
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        run.assert_not_called()

    def test_fail_streak_survives_guardian_restart(self):
        """失败计数持久化在 state：Guardian 崩溃重启后计数保留，补足 3 次即触发重启"""
        state = {
            "restart_counts": {},
            "newapi_fail_streak": 2,
        }

        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        # state 里已有 2 次计数 → 本次失败即达 3 次门槛，触发重启并清零
        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)
        self.assertEqual(state["newapi_fail_streak"], 0)


    def test_fail_streak_persisted_on_every_failure(self):
        """streak 每次递增都必须 _save_state，Guardian 崩溃后计数保留"""
        state = {
            "restart_counts": {},
            "newapi_fail_streak": 0,
        }
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix._save_state = Mock()
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        # 首次失败（streak=1，未达门槛）也必须持久化
        self.assertEqual(state["newapi_fail_streak"], 1)
        g.autofix._save_state.assert_called()

    def test_newapi_restart_verification_timeout_enters_long_cooldown(self):
        """SSH 命令已执行（rc=0）但 API 未恢复：进入长冷却，不得短退避反复重启"""
        engine = make_engine({
            "newapi_restart_time": None,
            "newapi_restart_fail_time": None,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.newapi.get_status = Mock(return_value=False)  # 验证超时

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)),
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        # 命令已执行 → 进入长冷却（restart_time），而不是只写 60s fail_time
        self.assertIsNotNone(engine.state.get("newapi_restart_time"))
        self.assertNotIn("newapi_restart_fail_time", engine.state)
        self.assertIs(engine.state.get("newapi_restart_verified"), False)
        engine._save_state.assert_called()
        engine.telegram.send_alert.assert_called_once()

    def test_newapi_restart_verification_timeout_blocks_second_call(self):
        """验证超时进入 30min 长冷却：冷却期内二次调用被挡住，不执行 SSH"""
        engine = make_engine({
            "newapi_restart_time": datetime.now().isoformat(),
            "newapi_restart_fail_time": None,
            "newapi_restart_verified": False,
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.newapi.get_status = Mock(return_value=False)

        with (
            patch.object(guardian.subprocess, "run", return_value=Mock(returncode=0)) as run,
            patch.object(guardian.time, "sleep"),
        ):
            ok = engine.restart_newapi_container()

        self.assertFalse(ok)
        run.assert_not_called()

    def test_local_proxy_401_treated_as_alive(self):
        """401/403 是鉴权问题，服务存活；不得触发重启"""
        health = guardian.HealthChecker(Mock())

        with patch.object(
            guardian.urllib.request,
            "urlopen",
            side_effect=guardian.urllib.error.HTTPError(
                "http://100.83.32.95:8788/v1/models", 401, "Unauthorized", None, None
            ),
        ):
            ok, msg = health.check_local_proxy(8788, "agentrouter")

        self.assertTrue(ok)
        self.assertIn("鉴权失败", msg)

    def test_local_proxy_403_treated_as_alive(self):
        """403 同 401：服务存活，不得触发重启"""
        health = guardian.HealthChecker(Mock())

        with patch.object(
            guardian.urllib.request,
            "urlopen",
            side_effect=guardian.urllib.error.HTTPError(
                "http://100.83.32.95:8788/v1/models", 403, "Forbidden", None, None
            ),
        ):
            ok, msg = health.check_local_proxy(8788, "agentrouter")

        self.assertTrue(ok)
        self.assertIn("鉴权失败", msg)

    def test_circuit_breaker_alert_sent_once(self):
        """断路器打开后重复调用只发一次告警，不刷屏"""
        engine = make_engine({
            "restart_counts": {"agentrouter": 3},
            "restarted_proxies": {"agentrouter": "x"},
        })
        engine.telegram = Mock()

        engine.restart_local_proxy("agentrouter", 8788)
        engine.restart_local_proxy("agentrouter", 8788)

        self.assertEqual(engine.telegram.send_alert.call_count, 1)

    def test_circuit_breaker_success_resets_alert_flag(self):
        """真实 _check_cycle 中代理恢复 → restart_counts 与 restart_alerted 一并复位"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {
            "restart_counts": {"agentrouter": 3},
            "restarted_proxies": {"agentrouter": "2026-08-03T02:00:00"},
            "restart_alerted": {"agentrouter": True},
        }
        g.autofix.get_balance_trend.return_value = None
        g.autofix._save_state = Mock()
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        self.assertEqual(g.autofix.state["restart_counts"]["agentrouter"], 0)
        self.assertNotIn("agentrouter", g.autofix.state.get("restart_alerted", {}))
        g.autofix._save_state.assert_called()

    def test_circuit_breaker_alert_reenabled_after_recovery(self):
        """恢复后再故障：断路器告警必须再次触发（残留标记已清理）"""
        # 第一段：3 次失败 → 断路器打开 → 告警一次
        engine = make_engine({
            "restart_counts": {"agentrouter": 3},
            "restarted_proxies": {"agentrouter": "x"},
        })
        engine.telegram = Mock()
        engine.restart_local_proxy("agentrouter", 8788)
        self.assertEqual(engine.telegram.send_alert.call_count, 1)

        # 第二段：恢复后 _check_cycle 复位（含 restart_alerted）
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = engine.state
        g.autofix.get_balance_trend.return_value = None
        g.autofix._save_state = Mock()
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()
        g._check_cycle()
        self.assertNotIn("agentrouter", g.autofix.state.get("restart_alerted", {}))

        # 第三段：再次 3 次失败 → 断路器告警必须重新触发
        engine.state["restart_counts"]["agentrouter"] = 3
        engine.telegram = Mock()
        engine.restart_local_proxy("agentrouter", 8788)
        self.assertEqual(engine.telegram.send_alert.call_count, 1)

    def test_url_with_userinfo_skips_omp_role_update(self):
        """带 userinfo 的 base_url（user@host）不符合纯 endpoint 契约，不得篡改 OMP 角色"""
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n  default: agentrouter/claude-opus-4-8:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "codebuddy",
            "models": "gpt-5.6-sol",
            "base_url": "http://user:pass@100.83.32.95:8787",
        }
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (True, "ok")
        engine.telegram.send_alert = Mock()

        engine._update_omp_roles(7, "codebuddy")

        updated = config_path.read_text(encoding="utf-8")
        self.assertIn("  default: agentrouter/claude-opus-4-8:xhigh\n", updated)

    def test_budget_exceeded_skips_low_priority_steps(self):
        """预算耗尽：高优先级步骤仍执行，低优先级步骤跳过"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        now = time.monotonic()
        # 第一次 monotonic() 设 deadline（now+90s），之后所有返回 now+91s（已超预算）
        with (
            patch.object(
                guardian.time, "monotonic",
                side_effect=[now, now + guardian.CYCLE_BUDGET_SEC + 1] + [now + guardian.CYCLE_BUDGET_SEC + 1] * 20,
            ),
            patch.object(guardian.logger, "warning"),
        ):
            g._check_cycle()

        # 低优先级步骤全部跳过（错误率/余额/指标/全扫/cleanup/报告）
        g.autofix.scan_error_channels.assert_not_called()
        g.autofix._auto_adjust_weights.assert_not_called()
        g.autofix.check_and_enable_recovered_channels.assert_not_called()
        g.autofix.check_omp_roles_health.assert_not_called()
        g.autofix.full_health_scan.assert_not_called()
        g.autofix.cleanup_stale_state.assert_not_called()
        g._maybe_daily_report.assert_not_called()
        g.health.check_error_rate.assert_not_called()
        g.health.check_balance.assert_not_called()
        # 稳定性检查是安全关键，不参与预算跳过
        g.autofix._check_joined_channels_stability.assert_called_once()


    def test_budget_ok_runs_low_priority_steps(self):
        """预算充足：低优先级步骤全部执行"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok")
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        g.autofix.scan_error_channels.assert_called_once()
        g.autofix.check_and_enable_recovered_channels.assert_called_once()
        g.autofix.full_health_scan.assert_called_once()
        g._maybe_daily_report.assert_called_once()

    def test_heartbeat_written_each_cycle(self):
        """run() 循环每轮写心跳文件"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.running = True
        g.newapi = Mock()
        g.newapi.exclude_retry_status_code.return_value = True
        g.telegram = Mock()
        g.telegram.send_alert.return_value = True
        g._check_cycle = Mock(side_effect=[None, KeyboardInterrupt])
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        heartbeat = guardian.Path.home() / ".omp" / "guardian" / "heartbeat.json"

        with patch.object(guardian.time, "sleep"):
            g.run()

        self.assertTrue(heartbeat.exists())
        data = json.loads(heartbeat.read_text(encoding="utf-8"))
        self.assertIn("ts", data)
        self.assertIn("pid", data)

    def test_heartbeat_write_failure_does_not_block_cycle(self):
        """心跳写失败只记日志，不阻断本轮 process_commands / _check_cycle"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.running = True
        g.newapi = Mock()
        g.newapi.exclude_retry_status_code.return_value = True
        g.telegram = Mock()
        g.telegram.send_alert.return_value = True
        g._check_cycle = Mock(side_effect=[None, KeyboardInterrupt])
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False

        with (
            patch.object(
                guardian.Path,
                "write_text",
                side_effect=OSError("disk full"),
            ),
            patch.object(guardian.logger, "error") as err,
            patch.object(guardian.time, "sleep"),
        ):
            g.run()

        # 心跳写失败被记录，但 _check_cycle 仍被调用
        err.assert_called()
        g._check_cycle.assert_called()

    def test_heartbeat_uses_atomic_replace(self):
        """心跳原子写：先写 tmp 再 os.replace，替换失败只记日志不阻断"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.running = True
        g.newapi = Mock()
        g.newapi.exclude_retry_status_code.return_value = True
        g.telegram = Mock()
        g.telegram.send_alert.return_value = True
        g._check_cycle = Mock(side_effect=[None, KeyboardInterrupt])
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False

        calls = []
        with (
            patch.object(
                guardian.Path, "write_text",
                side_effect=lambda *a, **k: calls.append(("write", str(a[0]))),
            ),
            patch.object(
                guardian.os, "replace",
                side_effect=lambda src, dst: calls.append(("replace", str(src), str(dst))),
            ),
            patch.object(guardian.logger, "error"),
            patch.object(guardian.time, "sleep"),
        ):
            g.run()

        # 顺序：write(tmp) 必须严格先于 replace(tmp, heartbeat.json)
        writes = [c for c in calls if c[0] == "write"]
        replaces = [c for c in calls if c[0] == "replace"]
        self.assertGreaterEqual(len(replaces), 1)
        self.assertGreaterEqual(len(writes), 1)
        self.assertTrue(replaces[0][2].endswith("heartbeat.json"))
        self.assertTrue(replaces[0][1].endswith("heartbeat.json.tmp"))
        write_idx = calls.index(writes[0])
        replace_idx = calls.index(replaces[0])
        self.assertLess(write_idx, replace_idx)  # tmp 写入先于原子替换
        g._check_cycle.assert_called()

    def test_heartbeat_replace_failure_does_not_block_cycle(self):
        """os.replace 失败（如目标被占用）：只记日志，不阻断本轮"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.running = True
        g.newapi = Mock()
        g.newapi.exclude_retry_status_code.return_value = True
        g.telegram = Mock()
        g.telegram.send_alert.return_value = True
        g._check_cycle = Mock(side_effect=[None, KeyboardInterrupt])
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False

        with (
            patch.object(
                guardian.os, "replace",
                side_effect=OSError("sharing violation"),
            ),
            patch.object(guardian.logger, "error") as err,
            patch.object(guardian.time, "sleep"),
        ):
            g.run()

        err.assert_called()
        g._check_cycle.assert_called()


    def test_state_corrupt_backed_up_not_silent(self):
        """state.json 损坏：快照备份留证 + 日志，不静默回默认值，原文件保留"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # 清理其他测试可能残留的备份
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()
        state_file.write_text("{ not valid json", encoding="utf-8")

        with patch.object(guardian.logger, "error") as err:
            engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            loaded = engine._load_state()

        self.assertEqual(loaded["newapi_fail_streak"], 0)
        err.assert_called()
        # 原文件保留（下次可重试），损坏字节快照到备份
        self.assertTrue(state_file.exists())
        backups = list(state_file.parent.glob("state.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"{ not valid json")

    def test_state_oserror_not_backed_up(self):
        """state.json 读 I/O 错误（非内容损坏）：不搬文件，只记录"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # 清理前一个测试可能残留的备份
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()
        state_file.write_text('{"ok": true}', encoding="utf-8")

        with (
            patch.object(
                guardian.json,
                "loads",
                side_effect=OSError("sharing violation"),
            ) as loads,
            patch.object(guardian.logger, "error") as err,
        ):
            engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            loaded = engine._load_state()

        loads.assert_called()
        err.assert_called()
        # 原文件未被动（未被搬走/改名）
        self.assertTrue(state_file.exists())
        self.assertEqual(state_file.read_text(encoding="utf-8"), '{"ok": true}')
        self.assertEqual(list(state_file.parent.glob("state.json.corrupt-*")), [])

    def test_state_backup_retention_bounded(self):
        """备份保留上限 5 个，不无限积累"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # 预置 6 个旧备份
        for i in range(6):
            (state_file.parent / f"state.json.corrupt-2026080300000{i}-1").write_bytes(b"x")
        state_file.write_text("{ bad", encoding="utf-8")

        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        with patch.object(guardian.logger, "error"):
            engine._load_state()

        backups = list(state_file.parent.glob("state.json.corrupt-*"))
        self.assertLessEqual(len(backups), 5)
    def test_state_backup_exclusive_create_no_overwrite(self):
        """同进程同 microsecond 两次损坏：独占创建防覆盖，两份取证都保留"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()
        state_file.write_text("{ bad one", encoding="utf-8")

        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        with patch.object(guardian.logger, "error"):
            fixed = datetime.now().strftime("%Y%m%d%H%M%S%f")
            fake_now = datetime.fromisoformat(
                f"{fixed[:8]}T{fixed[8:14]}.{fixed[14:]}"
            )
            # patch guardian 模块的 datetime 引用（模块属性可替换，类不可 patch）
            with patch.object(guardian, "datetime", wraps=guardian.datetime) as dt:
                dt.now.return_value = fake_now
                engine._load_state()
                engine._load_state()  # 同时间戳二次损坏

        backups = list(state_file.parent.glob("state.json.corrupt-*"))
        self.assertEqual(len(backups), 2)  # 无覆盖：两份都在
        contents = {b.read_bytes() for b in backups}
        self.assertIn(b"{ bad one", contents)

    def test_state_backup_retention_keeps_latest_five_by_mtime(self):
        """固定时间连续 7 次损坏：按 mtime 保留最后 5 份，最新取证不被误删"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()

        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        fixed = datetime.now().strftime("%Y%m%d%H%M%S%f")
        fake_now = datetime.fromisoformat(f"{fixed[:8]}T{fixed[8:14]}.{fixed[14:]}")
        with patch.object(guardian.logger, "error"):
            with patch.object(guardian, "datetime", wraps=guardian.datetime) as dt:
                dt.now.return_value = fake_now
                for i in range(7):
                    state_file.write_text(f"{{ bad {i}", encoding="utf-8")
                    engine._load_state()

        backups = sorted(
            state_file.parent.glob("state.json.corrupt-*"),
            key=lambda p: p.stat().st_mtime,
        )
        self.assertEqual(len(backups), 5)  # 保留最后 5 份
        # 最新一份（第 7 次）必须在保留列表内，且内容未被误删
        latest = backups[-1].read_bytes()
        self.assertEqual(latest, b"{ bad 6")
        # 最旧的两份（第 1、2 次）被清理
        remaining = {b.read_bytes() for b in backups}
        self.assertNotIn(b"{ bad 0", remaining)
        self.assertNotIn(b"{ bad 1", remaining)

class OmpRoleTests(unittest.TestCase):
    def test_codebuddy_recovery_restores_default_role_without_touching_task(self):
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n"
            "  default: agentrouter/claude-opus-4-8:xhigh\n"
            "  task: zg-newapi/gpt-5.6-sol:high\n"
            "symbolPreset: ascii\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "codebuddy",
            "models": "gpt-5.6-sol",
            "base_url": "http://100.83.32.95:8787",
        }
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (True, "ok")

        engine._update_omp_roles(7, "codebuddy")

        updated = config_path.read_text(encoding="utf-8")
        self.assertIn("  default: codebuddy/gpt-5.6-sol:max\n", updated)
        self.assertIn("  task: zg-newapi/gpt-5.6-sol:high\n", updated)

    def test_same_name_channel_with_wrong_port_skips_omp_role_update(self):
        """同名但 base_url 端口不匹配的渠道不得篡改 OMP 角色"""
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n  default: agentrouter/claude-opus-4-8:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "codebuddy",
            "models": "gpt-5.6-sol",
            "base_url": "https://other.example.com:9999",
        }
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (True, "ok")
        engine.telegram.send_alert = Mock()

        engine._update_omp_roles(7, "codebuddy")

        updated = config_path.read_text(encoding="utf-8")
        self.assertIn("  default: agentrouter/claude-opus-4-8:xhigh\n", updated)

    def test_same_port_wrong_host_skips_omp_role_update(self):
        """同端口但非本地 host 的渠道不得篡改 OMP 角色（严格 URL 校验）"""
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n  default: agentrouter/claude-opus-4-8:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "codebuddy",
            "models": "gpt-5.6-sol",
            "base_url": "https://unrelated.example.com:8787",
        }
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (True, "ok")
        engine.telegram.send_alert = Mock()

        engine._update_omp_roles(7, "codebuddy")

        updated = config_path.read_text(encoding="utf-8")
        self.assertIn("  default: agentrouter/claude-opus-4-8:xhigh\n", updated)

    def test_host_matching_but_wrong_scheme_skips_omp_role_update(self):
        """host/port 匹配但 scheme 非 http(s)（如 ftp、缺省协议）不得篡改 OMP 角色"""
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n  default: agentrouter/claude-opus-4-8:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        for bad_url in ("ftp://100.83.32.95:8787", "//100.83.32.95:8787"):
            with self.subTest(base_url=bad_url):
                engine = make_engine()
                engine.newapi.channels[7] = {
                    "id": 7,
                    "name": "codebuddy",
                    "models": "gpt-5.6-sol",
                    "base_url": bad_url,
                }
                engine.health = Mock()
                engine.health.check_local_proxy.return_value = (True, "ok")
                engine.telegram.send_alert = Mock()

                engine._update_omp_roles(7, "codebuddy")

                updated = config_path.read_text(encoding="utf-8")
                self.assertIn("  default: agentrouter/claude-opus-4-8:xhigh\n", updated)

    def test_recovered_channel_skips_omp_update_when_local_proxy_down(self):
        """渠道恢复但本地代理挂时，不切角色，发 warning"""
        config_path = Path.home() / ".omp" / "agent" / "config.yml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "modelRoles:\n  default: agentrouter/claude-opus-4-8:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine.newapi.channels[7] = {
            "id": 7,
            "name": "codebuddy",
            "models": "gpt-5.6-sol",
            "base_url": "http://100.83.32.95:8787",
        }
        engine.health = Mock()
        engine.health.check_local_proxy.return_value = (False, "down")
        engine.telegram.send_alert = Mock()

        engine._update_omp_roles(7, "codebuddy")

        updated = config_path.read_text(encoding="utf-8")
        self.assertIn("  default: agentrouter/claude-opus-4-8:xhigh\n", updated)
        engine.telegram.send_alert.assert_called_once()

    def test_tailscale_role_endpoint_is_actively_probed(self):
        agent_dir = Path.home() / ".omp" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "config.yml").write_text(
            "modelRoles:\n  slow: agentrouter/claude-opus-5:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        (agent_dir / "models.yml").write_text(
            "providers:\n  agentrouter:\n    baseUrl: http://100.83.32.95:8788/v1\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine._omp_check_count = guardian.OMP_ROLE_CHECK_INTERVAL - 1
        engine._probe_endpoint = Mock(return_value=False)
        engine.telegram.send_alert = Mock()

        engine.check_omp_roles_health()

        engine._probe_endpoint.assert_called_once_with("http://100.83.32.95:8788/v1")
        engine.telegram.send_alert.assert_called_once()

    def test_probe_endpoint_treats_500_as_down(self):
        """HTTPError 500 是服务端故障，不算端点存活"""
        with patch.object(
            guardian.urllib.request,
            "urlopen",
            side_effect=guardian.urllib.error.HTTPError(
                "http://127.0.0.1:8788/v1/models", 500, "Internal Server Error", None, None
            ),
        ):
            self.assertFalse(
                guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:8788/v1")
            )

    def test_probe_endpoint_treats_401_as_alive(self):
        """4xx 是客户端拒绝，服务存活"""
        with patch.object(
            guardian.urllib.request,
            "urlopen",
            side_effect=guardian.urllib.error.HTTPError(
                "http://127.0.0.1:8788/v1/models", 401, "Unauthorized", None, None
            ),
        ):
            self.assertTrue(
                guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:8788/v1")
            )


class DailyReportTests(unittest.TestCase):
    def test_daily_report_failure_does_not_mark_sent(self):
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.health = Mock()
        g.health.check_balance.return_value = (True, 100, 100)
        g.health.check_error_rate.return_value = (True, 0, 0, 0)
        g.alerts = Mock()
        g.alerts.send_daily_report.return_value = False
        g.autofix = Mock()
        g.autofix.state = {
            "last_daily_report": None,
            "disabled_channels": [],
            "restarted_proxies": {},
        }

        sent = g._maybe_daily_report()

        self.assertFalse(sent)
        self.assertIsNone(g.autofix.state["last_daily_report"])
        g.autofix._save_state.assert_not_called()

    def test_daily_report_success_marks_sent(self):
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.health = Mock()
        g.health.check_balance.return_value = (True, 100, 100)
        g.health.check_error_rate.return_value = (True, 0, 0, 0)
        g.alerts = Mock()
        g.alerts.send_daily_report.return_value = True
        g.autofix = Mock()
        g.autofix.state = {
            "last_daily_report": None,
            "disabled_channels": [],
            "restarted_proxies": {},
        }
        sent = g._maybe_daily_report()

        self.assertTrue(sent)
        self.assertEqual(g.autofix.state["last_daily_report"], datetime.now().strftime("%Y-%m-%d"))
        g.autofix._save_state.assert_called_once()

    def test_cmd_report_logs_failure_without_useless_resend(self):
        """日报发送失败（通道不可用）时不尝试无效重发，只记录日志"""
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.send = Mock(return_value=True)
        g = Mock()
        g._maybe_daily_report.return_value = False

        with self.assertLogs("guardian", level="ERROR"):
            bot._cmd_report(g)

        bot.send.assert_not_called()

    def test_cmd_report_reports_success_when_send_ok(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.send = Mock(return_value=True)
        g = Mock()
        g._maybe_daily_report.return_value = True

        bot._cmd_report(g)

        self.assertIn("已生成", bot.send.call_args.args[0])


def tearDownModule():
    logging.shutdown()
    _TEST_HOME.cleanup()


if __name__ == "__main__":
    unittest.main()
