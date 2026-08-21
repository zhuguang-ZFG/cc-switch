import os
import sys
import json
import sqlite3
import time
import socket
import tempfile
import logging
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
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
        self.test_timeouts = []
        self.enable_calls = []
        self.disable_calls = []

    def update_channel(self, channel):
        self.updates.append(channel.copy())
        return True

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_channels(self):
        return list(self.channels.values())
    def test_channel(self, channel_id, timeout=None):
        self.test_calls.append(channel_id)
        self.test_timeouts.append(timeout)
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


class NewAPIClientUpdateTests(unittest.TestCase):
    def test_update_channel_hydrates_masked_key_from_local_ssot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'gorouter', 'fixture-real-key')"
                )
                connection.commit()

            client = guardian.NewAPIClient("http://127.0.0.1:3002", "token", "1")
            with patch.object(guardian, "NEWAPI_DB", database):
                with patch.object(
                    client,
                    "_request",
                    return_value={"success": True},
                ) as request:
                    self.assertTrue(
                        client.update_channel(
                            {
                                "id": 57,
                                "name": "gorouter",
                                "key": "sk-***masked***",
                                "status": 2,
                                "weight": 0,
                            }
                        )
                    )

            payload = request.call_args.args[2]
            self.assertEqual(payload["key"], "fixture-real-key")
            self.assertNotIn("status", payload)

    def test_update_channel_fails_closed_when_masked_key_cannot_be_hydrated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            client = guardian.NewAPIClient("http://127.0.0.1:3002", "token", "1")
            with patch.object(
                guardian, "NEWAPI_DB", Path(temp_dir) / "missing.db"
            ):
                with patch.object(client, "_request") as request:
                    self.assertFalse(
                        client.update_channel(
                            {
                                "id": 57,
                                "key": "sk-***masked***",
                                "weight": 0,
                            }
                        )
                    )
            request.assert_not_called()

    def test_update_channel_rejects_reused_channel_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'replacement', 'fixture-real-key')"
                )
                connection.commit()

            client = guardian.NewAPIClient("http://127.0.0.1:3002", "token", "1")
            with patch.object(guardian, "NEWAPI_DB", database):
                with patch.object(client, "_request") as request:
                    self.assertFalse(
                        client.update_channel(
                            {
                                "id": 57,
                                "name": "stale-channel",
                                "key": "sk-***masked***",
                                "weight": 0,
                            }
                        )
                    )
            request.assert_not_called()

    def test_update_channel_checks_identity_even_when_key_is_unmasked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'replacement', 'current-key')"
                )
                connection.commit()

            client = guardian.NewAPIClient("http://127.0.0.1:3002", "token", "1")
            with patch.object(guardian, "NEWAPI_DB", database):
                with patch.object(client, "_request") as request:
                    self.assertFalse(
                        client.update_channel(
                            {
                                "id": 57,
                                "name": "stale-channel",
                                "key": "stale-unmasked-key",
                                "weight": 0,
                            }
                        )
                    )
            request.assert_not_called()

    def test_update_channel_rejects_unmasked_key_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'gorouter', 'current-key')"
                )
                connection.commit()

            client = guardian.NewAPIClient("http://127.0.0.1:3002", "token", "1")
            with patch.object(guardian, "NEWAPI_DB", database):
                with patch.object(client, "_request") as request:
                    self.assertFalse(
                        client.update_channel(
                            {
                                "id": 57,
                                "name": "gorouter",
                                "key": "stale-unmasked-key",
                                "weight": 0,
                            }
                        )
                    )
            request.assert_not_called()


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
    engine._probe_soft_failures = {}
    engine._pinned_scan_offset = 0
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


def unused_port():
    """取一个本机当前未监听的端口（绑定后立即释放，供真实 TCP 探测判 down）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

    def test_agents_command_reports_running_and_completed_sessions(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.send = Mock()
        guardian_instance = Mock()
        guardian_instance.get_subagent_status.return_value = [
            {"name": "Build", "model": "zg-newapi/opencode-go", "status": "running", "age_sec": 7},
            {"name": "Review", "model": "claude-opus-5", "status": "completed", "age_sec": 12},
        ]

        bot._cmd_agents(guardian_instance)

        text = bot.send.call_args.args[0]
        self.assertIn("Build", text)
        self.assertIn("running", text)
        self.assertIn("Review", text)
        self.assertIn("completed", text)

    def test_agents_command_reports_empty_roster(self):
        bot = guardian.TelegramBot.__new__(guardian.TelegramBot)
        bot.send = Mock()
        guardian_instance = Mock()
        guardian_instance.get_subagent_status.return_value = []

        bot._cmd_agents(guardian_instance)

        self.assertIn("没有近期 subagent", bot.send.call_args.args[0])


class FullHealthScanTests(unittest.TestCase):
    def test_pinned_channel_is_disabled_instead_of_reweighted(self):
        engine = make_engine()
        engine._full_scan_offset = 0
        engine.newapi.channels[92] = {
            "id": 92,
            "name": "zzzcoding-gpt-5.6-sol",
            "status": 1,
            "weight": 15,
            "priority": 60,
            "models": "gpt-5.6-sol",
        }
        engine.newapi.test_results.extend([(False, "timed out")] * 3)

        with patch.object(guardian.logger, "warning"), patch.object(
            guardian.logger, "info"
        ):
            for _ in range(3):
                engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
                engine.full_health_scan()

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(engine.newapi.disable_calls, [92])
        self.assertEqual(engine.state["weight_history"]["92"]["weight"], 15)
        self.assertEqual(
            engine.state["disabled_channels"][0]["reason"],
            "full_scan: timed out",
        )

    def test_deadline_defers_unscanned_channels_without_skipping_rotation(self):
        engine = make_engine()
        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
        engine._full_scan_offset = 0
        for channel_id in (45, 46, 47):
            engine.newapi.channels[channel_id] = {
                "id": channel_id,
                "name": f"channel-{channel_id}",
                "status": 1,
                "weight": 5,
                "priority": 40,
                "models": "gpt-5.6-sol",
            }
            engine.newapi.test_results.append((True, "ok"))

        with patch.object(guardian.time, "monotonic", side_effect=[100.0, 105.0]):
            engine.full_health_scan(deadline=105.0)

        self.assertEqual(engine.newapi.test_calls, [45])
        self.assertEqual(engine.newapi.test_timeouts, [5])
        self.assertEqual(engine._full_scan_offset, 1)

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


    def test_full_scan_agentic_only_rejection_does_not_accumulate_failures(self):
        """非 agentic 探针被拒是探针不相容，不得降权或禁用可用渠道。"""
        engine = make_engine()
        engine._full_scan_offset = 0
        engine.newapi.channels[57] = {
            "id": 57,
            "name": "agentic-only",
            "status": 1,
            "weight": 6,
            "priority": 40,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append(
            (False, "HTTP 403 non_agentic_blocked: This relay only serves agentic clients")
        )

        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
        engine.full_health_scan()

        self.assertNotIn(57, engine._probe_soft_failures)
        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(engine.newapi.disable_calls, [])
class ChannelFailureScanTests(unittest.TestCase):
    def test_error_scan_disables_pinned_channel_after_three_soft_failures(self):
        # ch83 做 pinned 样例：ch48 自 2026-08-21 起在 AUTO_BAN_RECOVERY_EXCLUSIONS
        # 中，其禁用记录按策略不入恢复队列，无法再断言 disabled_channels 条目。
        engine = make_engine()
        engine._scan_offset = 0
        engine.newapi.channels[83] = {
            "id": 83,
            "name": "muyuan-sol",
            "status": 1,
            "weight": 13,
            "priority": 51,
            "models": "gpt-5.6-sol",
        }
        engine.newapi.test_results.extend(
            [(False, "503 Endpoint is unavailable")] * 3
        )

        for expected_disables in (0, 0, 1):
            engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
            engine.scan_error_channels()
            self.assertEqual(len(engine.newapi.disable_calls), expected_disables)

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(engine.state["weight_history"]["83"]["weight"], 13)
        self.assertEqual(
            engine.state["disabled_channels"][0]["reason"],
            "error_scan: 503 Endpoint is unavailable",
        )
        self.assertNotIn(83, engine._probe_soft_failures)

    def test_error_scan_success_resets_pinned_soft_failure_streak(self):
        engine = make_engine()
        engine._scan_offset = 0
        engine.newapi.channels[48] = {
            "id": 48,
            "name": "opencode-go-muse",
            "status": 1,
            "weight": 12,
            "priority": 51,
            "models": "muse-spark-1.2-contributor",
        }
        engine.newapi.test_results.extend(
            [
                (False, "503 Endpoint is unavailable"),
                (True, "ok"),
                (False, "503 Endpoint is unavailable"),
                (False, "503 Endpoint is unavailable"),
            ]
        )

        for _ in range(4):
            engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
            engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine._probe_soft_failures[48], 2)

    def test_error_scan_drops_streak_for_channel_disabled_between_scans(self):
        engine = make_engine()
        engine._scan_offset = 0
        channel = {
            "id": 48,
            "name": "opencode-go-muse",
            "status": 1,
            "weight": 12,
            "priority": 51,
            "models": "muse-spark-1.2-contributor",
        }
        engine.newapi.channels[48] = channel
        engine.newapi.test_results.append((False, "503 Endpoint is unavailable"))

        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine.scan_error_channels()
        self.assertEqual(engine._probe_soft_failures[48], 1)

        channel["status"] = 2
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine.scan_error_channels()

        self.assertNotIn(48, engine._probe_soft_failures)

    def test_error_scan_soft_failure_does_not_accumulate_for_regular_channel(self):
        engine = make_engine()
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45,
            "name": "agentrouter",
            "status": 1,
            "weight": 5,
            "priority": 40,
            "models": "gpt-5.6-sol",
        }
        engine.newapi.test_results.extend([(False, "503 upstream unavailable")] * 3)

        for _ in range(3):
            engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
            engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine.newapi.updates, [])
        self.assertNotIn(45, engine._probe_soft_failures)

    def test_error_and_full_scans_share_pinned_soft_failure_streak(self):
        engine = make_engine()
        engine._scan_offset = 0
        engine._full_scan_offset = 0
        engine.newapi.channels[92] = {
            "id": 92,
            "name": "zzzcoding-gpt-5.6-sol",
            "status": 1,
            "weight": 15,
            "priority": 60,
            "models": "gpt-5.6-sol",
        }
        engine.newapi.test_results.extend([(False, "upstream 503")] * 3)

        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
        engine.full_health_scan()
        for _ in range(2):
            engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
            engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [92])
        self.assertEqual(
            engine.state["disabled_channels"][0]["reason"],
            "error_scan: upstream 503",
        )

    def test_error_scan_reserves_slots_for_pinned_and_regular_channels(self):
        engine = make_engine()
        engine._scan_offset = 0
        for channel_id, name in (
            (48, "opencode-go-muse"),
            (83, "muyuan-sol"),
            (91, "jianzhile-gpt-5.6-sol"),
            (92, "zzzcoding-gpt-5.6-sol"),
        ):
            engine.newapi.channels[channel_id] = {
                "id": channel_id,
                "name": name,
                "status": 1,
                "weight": 5,
            }
        for channel_id in range(100, 106):
            engine.newapi.channels[channel_id] = {
                "id": channel_id,
                "name": f"regular-{channel_id}",
                "status": 1,
                "weight": 5,
            }
        engine.newapi.test_results.extend([(True, "ok")] * 10)

        for _ in range(2):
            engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
            engine.scan_error_channels()

        self.assertEqual(
            engine.newapi.test_calls,
            [48, 83, 100, 101, 102, 91, 92, 103, 104, 105],
        )

    def test_error_scan_404_invalid_request_does_not_disable(self):
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[73] = {
            "id": 73, "name": "codex-relay", "status": 1, "weight": 5,
            "priority": 50, "models": "gpt-5.5",
        }
        engine.newapi.test_results.append((
            False,
            'bad response status code 404, body: {"error":{"type":"invalid_request_error"}}',
        ))
        engine.telegram = Mock()

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertNotIn("73", engine.state["weight_history"])
        engine.telegram.send_alert.assert_not_called()

    def test_precise_invalid_api_key_still_disables(self):
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[73] = {
            "id": 73, "name": "codex-relay", "status": 1, "weight": 5,
            "priority": 50, "models": "gpt-5.5",
        }
        engine.newapi.test_results.append((False, "invalid_api_key"))
        engine.telegram = Mock()

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [73])
        engine.telegram.send_alert.assert_called_once()

    def test_error_scan_rate_limit_does_not_disable(self):
        """429/rate limit 是瞬态：错误扫描不得禁用渠道、不写 weight_history"""
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "HTTP 429 too many requests, retry later"))
        engine.telegram = Mock()

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertNotIn("45", engine.state["weight_history"])
        engine.telegram.send_alert.assert_not_called()

    def test_full_scan_rate_limit_does_not_accumulate_failures(self):
        """全量扫描中 429 不累计永久失败计数、不降权、不禁用"""
        engine = make_engine()
        engine._full_scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "rate limit exceeded"))

        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
        engine.full_health_scan()

        self.assertNotIn(45, engine._probe_soft_failures)
        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(engine.newapi.disable_calls, [])

    def test_error_scan_401_still_disables(self):
        """401/402 等硬错误仍立即禁用"""
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "invalid token (401)"))
        engine.telegram = Mock()

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [45])
        self.assertIn("45", engine.state["weight_history"])
        engine.telegram.send_alert.assert_called_once()

    def test_full_scan_402_still_disables(self):
        """全量扫描中 402 硬错误仍立即禁用"""
        engine = make_engine()
        engine._full_scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "余额不足 (402)"))
        engine.telegram = Mock()

        engine._full_scan_count = guardian.FULL_SCAN_INTERVAL - 1
        engine.full_health_scan()

        self.assertEqual(engine.newapi.disable_calls, [45])

    def test_request_id_lookup_failure_does_not_block_alert(self):
        """日志查询抛异常：告警照发，主循环不受影响"""
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "invalid token (401)"))

        def boom(*_args, **_kwargs):
            raise RuntimeError("logs API down")

        engine.newapi.get_logs = boom
        engine.telegram = Mock()

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [45])
        engine.telegram.send_alert.assert_called_once()

    def test_alert_includes_request_ids_from_logs(self):
        """日志命中时告警附带 request_id / upstream_request_id"""
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "agentrouter", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "invalid token (401)"))
        engine.newapi.get_logs = lambda limit=20: [
            {
                "channel_id": 45,
                "content": "invalid token (401)",
                "request_id": "req-abc",
                "upstream_request_id": "up-xyz",
            }
        ]
        engine.telegram = Mock()

        engine.scan_error_channels()

        text = engine.telegram.send_alert.call_args.args[1]
        self.assertIn("req-abc", text)
        self.assertIn("up-xyz", text)


class HtmlEscapeTests(unittest.TestCase):
    def test_channel_name_with_html_is_escaped_in_disable_alert(self):
        """渠道名含 HTML 标签时转义，避免注入/破坏 Telegram 格式"""
        engine = make_engine()
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.channels[45] = {
            "id": 45, "name": "bad<b>chan</b>", "status": 1, "weight": 15, "priority": 50,
            "models": "claude-opus-5",
        }
        engine.newapi.test_results.append((False, "invalid token (401)"))
        engine.telegram = Mock()

        engine.scan_error_channels()

        text = engine.telegram.send_alert.call_args.args[1]
        self.assertIn("bad&lt;b&gt;chan&lt;/b&gt;", text)
        self.assertNotIn("bad<b>chan", text)


class PowerShellInvocationTests(unittest.TestCase):
    def test_proxy_kill_uses_argv_without_shell(self):
        """杀进程用 argv + shell=False，并按实际脚本名识别 node 进程。"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine.health = Mock()
        engine.health.check_local_endpoint.return_value = (True, "端口可达")

        with (
            patch.object(guardian.subprocess, "run") as run,
            patch.object(guardian.subprocess, "Popen"),
            patch.object(guardian.time, "sleep"),
        ):
            engine.restart_local_proxy("agentrouter", 8788)

        args = run.call_args.args[0]
        self.assertIsInstance(args, list)
        self.assertEqual(args[0], "powershell")
        self.assertIn("-Command", args)
        self.assertIn("shell", run.call_args.kwargs)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertIn(r"agentrouter\-proxy\.py", args[-1])

    def test_proxy_start_uses_env_keys_and_shared_bind_host(self):
        cases = (
            ("agentrouter", 8788, "AGENTROUTER_PROXY_KEY", "agent-secret"),
        )

        with patch.multiple(
            guardian,
            LOCAL_PROXY_BIND_HOST="0.0.0.0",
            AGENTROUTER_PROXY_KEY="agent-secret",
        ):
            for name, port, env_name, secret in cases:
                with self.subTest(name=name):
                    engine = make_engine({
                        "restart_counts": {},
                        "restarted_proxies": {},
                    })
                    engine.health = Mock()
                    engine.health.check_local_endpoint.return_value = (True, "端口可达")

                    with (
                        patch.object(guardian.subprocess, "run"),
                        patch.object(guardian.subprocess, "Popen") as popen,
                        patch.object(guardian.time, "sleep"),
                        patch.object(guardian.Path, "exists", return_value=True),
                    ):
                        engine.restart_local_proxy(name, port)

                    cmd = popen.call_args.args[0]
                    env = popen.call_args.kwargs["env"]
                    self.assertNotIn("--api-key", cmd)
                    self.assertNotIn(secret, cmd)
                    self.assertEqual(env[env_name], secret)
                    self.assertTrue(cmd[0].endswith("/python.exe"))
                    self.assertIn("--host", cmd)
                    self.assertEqual(cmd[cmd.index("--host") + 1], "0.0.0.0")

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


class ChannelTestProfileTests(unittest.TestCase):
    def test_muse_uses_streaming_responses_probe(self):
        client = guardian.NewAPIClient("https://example.invalid", "token", "1")
        client._request = Mock(return_value={"success": True})

        self.assertEqual(client.test_channel(48, timeout=22), (True, "测试通过"))

        client._request.assert_called_once_with(
            "GET",
            "/api/channel/test/48?model=muse-spark-1.2-contributor"
            "&endpoint_type=openai-response&stream=true",
            timeout=22,
        )

    def test_jianzhile_uses_streaming_responses_probe(self):
        client = guardian.NewAPIClient("https://example.invalid", "token", "1")
        client._request = Mock(return_value={"success": True})

        self.assertEqual(client.test_channel(91, timeout=22), (True, "测试通过"))

        client._request.assert_called_once_with(
            "GET",
            "/api/channel/test/91?model=jianzhile-codex-gpt-5.6-sol"
            "&endpoint_type=openai-response&stream=true",
            timeout=22,
        )

    def test_zzzcoding_uses_streaming_responses_probe(self):
        client = guardian.NewAPIClient("https://example.invalid", "token", "1")
        client._request = Mock(return_value={"success": True})

        self.assertEqual(client.test_channel(92, timeout=22), (True, "测试通过"))

        client._request.assert_called_once_with(
            "GET",
            "/api/channel/test/92?model=zzzcoding-codex-gpt-5.6-sol"
            "&endpoint_type=openai-response&stream=true",
            timeout=22,
        )

    def test_normal_channel_keeps_default_probe(self):
        client = guardian.NewAPIClient("https://example.invalid", "token", "1")
        client._request = Mock(return_value={"success": True})

        self.assertEqual(client.test_channel(7), (True, "测试通过"))

        client._request.assert_called_once_with(
            "GET", "/api/channel/test/7", timeout=guardian.TEST_CHANNEL_TIMEOUT
        )


class WeightAdjustmentTests(unittest.TestCase):
    def test_pinned_routing_channel_is_not_dynamically_reweighted(self):
        engine = make_engine()
        channel = {
            "id": 92,
            "name": "zzzcoding-gpt-5.6-sol",
            "status": 1,
            "weight": 15,
        }
        add_samples(engine, 92, guardian.WEIGHT_ADJUST_WINDOW, healthy=False)

        engine._auto_adjust_weights([channel])

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(channel["weight"], 15)

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
        # P3-15：未采取任何动作 → 保留样本窗口继续滚动，不清零
        self.assertEqual(len(engine.channel_perf[7]), guardian.WEIGHT_ADJUST_WINDOW)

    def test_keeps_window_when_already_degraded_channel_stays_bad(self):
        """已降权渠道继续不达标 → 不重复降权也不清窗（P3-15）"""
        engine = make_engine(
            {"degraded_channels": {"7": {"name": "bad", "original_weight": 10}}}
        )
        channel = {"id": 7, "name": "bad", "status": 1, "weight": 5}
        add_samples(engine, 7, guardian.WEIGHT_ADJUST_WINDOW, healthy=False)

        engine._auto_adjust_weights([channel])

        self.assertEqual(engine.newapi.updates, [])
        self.assertEqual(len(engine.channel_perf[7]), guardian.WEIGHT_ADJUST_WINDOW)

    def test_clears_window_after_a_degrade_action(self):
        """真正降权后清窗，避免同一窗口重复触发（P3-15 保留原语义）"""
        engine = make_engine()
        channel = {"id": 7, "name": "slow", "status": 1, "weight": 10}
        add_samples(engine, 7, guardian.WEIGHT_ADJUST_WINDOW, healthy=False)

        engine._auto_adjust_weights([channel])

        self.assertEqual(len(engine.newapi.updates), 1)
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
    def test_imports_newapi_auto_bans_into_recovery_queue(self):
        engine = make_engine(
            {
                "disabled_channels": [],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[80] = {
            "id": 80,
            "name": "auto-banned",
            "status": 3,
            "auto_ban": 1,
            "weight": 10,
            "priority": 50,
        }

        self.assertEqual(engine._sync_newapi_auto_bans(), 1)

        self.assertEqual([record["id"] for record in engine.state["disabled_channels"]], [80])
        self.assertFalse(engine.state["disabled_channels"][0]["manual"])
        self.assertEqual(engine.state["weight_history"]["80"]["weight"], 10)

        engine.check_and_enable_recovered_channels()
        self.assertEqual(engine.newapi.test_calls, [])

    def test_auto_ban_import_skips_manual_and_policy_exclusions(self):
        engine = make_engine(
            {
                "disabled_channels": [],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels = {
            2: {"id": 2, "name": "excluded", "status": 3, "auto_ban": 1},
            74: {"id": 74, "name": "sharedchat-quarantine", "status": 3, "auto_ban": 1},
            7: {"id": 7, "name": "manual", "status": 2, "auto_ban": 1},
            8: {"id": 8, "name": "not-auto-ban", "status": 3, "auto_ban": 0},
        }

        self.assertEqual(engine._sync_newapi_auto_bans(), 0)
        self.assertEqual(engine.state["disabled_channels"], [])

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

    def test_recovery_does_not_rewrite_omp_roles(self):
        """渠道恢复只维护 NewAPI 健康状态，不得改写人工维护的 OMP 路由策略。"""
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
        engine._auto_join_pool = lambda channel_id, name: not joined.append((channel_id, name))
        engine._update_omp_roles = Mock()

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.test_calls, [7, 7, 7])
        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine.state["disabled_channels"], [])
        self.assertEqual(joined, [(7, "recovered")])
        engine._update_omp_roles.assert_not_called()

    def test_agentic_only_rejection_does_not_count_as_recovery_success(self):
        """探针不相容不能证明恢复，禁止因此自动启用渠道。"""
        record = {
            "id": 57,
            "name": "agentic-only",
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
        engine.newapi.channels[57] = {"id": 57, "name": "agentic-only", "status": 2}
        engine.newapi.test_results.extend(
            [(False, "403 non_agentic_blocked: relay only serves agentic clients")] * 3
        )

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.enable_calls, [])
        self.assertIn(record, engine.state["disabled_channels"])

    def test_auto_enabled_agentic_only_channel_is_left_unchanged(self):
        """探针全部不相容时无健康结论，不得改变已自动启用渠道的状态；
        但须计入退避（2026-08-11 评审 P1-5，防止饿死恢复队列）。"""
        record = {
            "id": 86,
            "name": "agentic-only",
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
        engine.newapi.channels[86] = {"id": 86, "name": "agentic-only", "status": 1}
        engine.newapi.test_results.extend(
            [(False, "403 non_agentic_blocked: relay only serves agentic clients")] * 3
        )

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine.newapi.enable_calls, [])
        self.assertEqual(record["recovery_failures"], 1)
        self.assertIn(record, engine.state["disabled_channels"])

    def test_real_recovery_failure_is_not_hidden_by_probe_incompatibility(self):
        """只要存在真实失败，已自动启用但未稳定的渠道仍须重新禁用。"""
        record = {
            "id": 87,
            "name": "mixed-failure",
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
        engine.newapi.channels[87] = {"id": 87, "name": "mixed-failure", "status": 1}
        engine.newapi.test_results.extend(
            [
                (False, "403 non_agentic_blocked: only serves agentic clients"),
                (False, "quota exceeded"),
                (False, "403 non_agentic_blocked: only serves agentic clients"),
            ]
        )

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.disable_calls, [87])
        self.assertEqual(record["recovery_failures"], 1)

    def test_recovery_preserves_current_manual_priority(self):
        """恢复可还原历史 weight，但 priority 必须保留当前人工策略值。"""
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
            "models": "claude-opus-5",
            "weight": 1,
            "priority": 57,
        }
        engine._balance_pool_weights = lambda _models, _channel_id, weight: weight

        self.assertTrue(engine._auto_join_pool(7, "recovered"))

        self.assertEqual(engine.newapi.updates[0]["weight"], 10)
        self.assertEqual(engine.newapi.updates[0]["priority"], 57)
        self.assertEqual(engine.state["joined_channels"]["7"]["priority"], 57)

    def test_recovery_restores_pinned_weight_instead_of_stale_history(self):
        engine = make_engine(
            {
                "weight_history": {"92": {"weight": 24, "priority": 60}},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels[92] = {
            "id": 92,
            "name": "zzzcoding-gpt-5.6-sol",
            "status": 1,
            "models": "gpt-5.6-sol",
            "weight": 12,
            "priority": 60,
        }
        engine._balance_pool_weights = Mock(
            side_effect=AssertionError("pinned channel must not be pool-balanced")
        )

        self.assertTrue(engine._auto_join_pool(92, "zzzcoding-gpt-5.6-sol"))

        self.assertEqual(engine.newapi.updates[0]["weight"], 15)
        self.assertEqual(engine.state["joined_channels"]["92"]["weight"], 15)

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


class ChannelIdentityTests(unittest.TestCase):
    def test_reused_channel_id_clears_all_legacy_recovery_state(self):
        engine = make_engine(
            {
                "disabled_channels": [
                    {"id": 48, "name": "opencode-go-luna", "manual": False}
                ],
                "weight_history": {"48": {"weight": 20, "priority": 51}},
                "degraded_channels": {
                    "48": {"name": "opencode-go-luna", "original_weight": 20}
                },
                "joined_channels": {"48": {"models": ["opencode-go"]}},
            }
        )
        engine.channel_perf[48].append({"healthy": True})
        engine._last_channel_tests[48] = 123
        engine._probe_soft_failures[48] = 2

        cleared = engine.reconcile_channel_identities(
            [
                {
                    "id": 48,
                    "name": "opencode-go-muse",
                    "type": 1,
                    "base_url": "https://opencode.ai",
                    "models": "muse-spark-1.2-contributor",
                    "model_mapping": "{}",
                    "test_model": "muse-spark-1.2-contributor",
                    "status": 1,
                    "weight": 12,
                    "priority": 51,
                }
            ]
        )

        self.assertEqual(cleared, 1)
        self.assertEqual(engine.state["disabled_channels"], [])
        for key in ("weight_history", "degraded_channels", "joined_channels"):
            self.assertNotIn("48", engine.state[key])
        self.assertNotIn(48, engine.channel_perf)
        self.assertNotIn(48, engine._last_channel_tests)
        self.assertNotIn(48, engine._probe_soft_failures)
        self.assertEqual(engine.state["channel_identities"]["48"]["name"], "opencode-go-muse")

    def test_mutable_posture_does_not_change_channel_identity(self):
        base = {
            "id": 92,
            "name": "zzzcoding-gpt-5.6-sol",
            "type": 1,
            "base_url": "https://api.zzzcoding.org/",
            "models": "zg-gpt-5.6-sol,gpt-5.6-sol",
            "model_mapping": '{"zg-gpt-5.6-sol":"gpt-5.6-sol"}',
            "test_model": "gpt-5.6-sol",
            "status": 1,
            "weight": 15,
            "priority": 60,
        }
        changed = {
            **base,
            "status": 3,
            "weight": 7,
            "priority": 55,
            "models": "gpt-5.6-sol,zg-gpt-5.6-sol",
        }

        self.assertEqual(
            engine_identity := guardian.AutoFixEngine._channel_identity(base),
            guardian.AutoFixEngine._channel_identity(changed),
        )
        self.assertEqual(len(engine_identity), 64)

    def test_quarantine_enforcement_disables_and_zeroes_reenabled_channel(self):
        engine = make_engine()
        engine.newapi.channels[39] = {
            "id": 39,
            "name": "ai-168661-grok",
            "status": 1,
            "weight": 10,
            "models": "grok-4.5",
        }

        enforced = engine.enforce_quarantine(list(engine.newapi.channels.values()))

        self.assertEqual(enforced, 1)
        self.assertEqual(engine.newapi.disable_calls, [39])
        self.assertEqual(engine.newapi.updates[0]["weight"], 0)


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

    def test_stability_agentic_only_rejection_does_not_count_as_failure(self):
        """稳定性窗口中的 non_agentic_blocked 不累计回滚失败。"""
        engine = make_engine(
            {
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {
                    "57": {
                        "time": datetime.now().isoformat(),
                        "models": ["claude-opus-5"],
                        "weight": 6,
                        "priority": 40,
                        "stability_checks": 0,
                        "stability_fails": 1,
                    }
                },
            }
        )
        engine.newapi.test_results.append(
            (False, "403 non_agentic_blocked: relay only serves agentic clients")
        )
        engine._stability_count = guardian.JOIN_STABILITY_CHECK_INTERVAL - 1

        engine._check_joined_channels_stability()

        self.assertEqual(engine.state["joined_channels"]["57"]["stability_fails"], 1)
        self.assertEqual(engine.newapi.disable_calls, [])
        self.assertEqual(engine.state.get("disabled_channels", []), [])

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

        with patch.object(guardian.time, "sleep"):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.disable_calls, [7])
        self.assertIn(record, engine.state["disabled_channels"])


class ProxyRestartTests(unittest.TestCase):
    def test_constructor_retains_health_checker(self):
        health = Mock()
        with patch.object(guardian.AutoFixEngine, "_load_state", return_value={}):
            engine = guardian.AutoFixEngine(Mock(), Mock(), health)

        self.assertIs(engine.health, health)

    def test_anyrouter_removed_from_local_proxies(self):
        """上游 key 失效的 anyrouter 不再受 Guardian 重启管理"""
        self.assertNotIn("anyrouter", guardian.LOCAL_PROXIES)
        engine = make_engine({"restart_counts": {}, "restarted_proxies": {}})

        with (
            patch.object(guardian.subprocess, "run") as run,
            patch.object(guardian.subprocess, "Popen") as popen,
            patch.object(guardian.time, "sleep"),
        ):
            restarted = engine.restart_local_proxy("anyrouter", 8789)

        self.assertFalse(restarted)
        run.assert_not_called()
        popen.assert_not_called()

    def test_newapi_restart_requires_three_consecutive_failures(self):
        """单次/两次瞬态失败不得触发破坏性重启；连续 3 次才重启"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
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
        g.health.check_local_proxy.return_value = (True, "ok", True)
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
        self.assertEqual(g.autofix.state.get("newapi_fail_streak"), 0)

    def test_local_proxy_restart_not_blocked_by_alert_cooldown(self):
        """代理故障时自愈重启不受告警冷却限制；冷却只控通知"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (False, "down", False)
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

        for _ in range(3):
            g._check_cycle()

        # 当前只管理 agentrouter 一个本地代理
        self.assertEqual(g.autofix.restart_local_proxy.call_count, 1)
        g.telegram.send_alert.assert_not_called()

    def test_successful_local_proxy_restart_does_not_send_stale_failure_alert(self):
        """重启函数已验证成功并自行通知后，不得再用重启前的失败结果告警"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (False, "down", False)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.restart_local_proxy.return_value = True
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        for _ in range(3):
            g._check_cycle()

        self.assertEqual(g.autofix.restart_local_proxy.call_count, 1)
        g.telegram.send_alert.assert_not_called()

    def test_failed_local_proxy_restart_sends_failure_alert(self):
        """重启未验证时仍发送故障告警"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (False, "down", False)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.restart_local_proxy.return_value = False
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        for _ in range(3):
            g._check_cycle()

        self.assertEqual(g.telegram.send_alert.call_count, 1)
        self.assertTrue(
            all(call.args[0] == "本地代理故障" for call in g.telegram.send_alert.call_args_list)
        )
    def test_open_proxy_breaker_does_not_send_duplicate_cycle_alert(self):
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (False, "down", False)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {
            "restart_counts": {name: 3 for name in guardian.LOCAL_PROXIES},
            "proxy_fail_streaks": {name: 2 for name in guardian.LOCAL_PROXIES},
        }
        g.autofix.restart_local_proxy.return_value = False
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        self.assertEqual(g.autofix.restart_local_proxy.call_count, 1)
        g.telegram.send_alert.assert_not_called()


    def test_newapi_restart_disabled_alerts_and_reports_delivery(self):
        """自动重启已禁用：只告警不重启；返回值＝告警是否投递成功（P2-11）"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.telegram.send_alert.return_value = True

        self.assertTrue(engine.restart_newapi_container())
        engine.telegram.send_alert.assert_called_once()
        self.assertEqual(
            engine.telegram.send_alert.call_args.args[0], "NewAPI 健康检查失败"
        )

    def test_newapi_restart_reports_false_when_alert_undelivered(self):
        """Telegram 熔断/投递失败 → 返回 False，供调用方保留重试机会（P2-11）"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()
        engine.telegram.send_alert.return_value = False

        self.assertFalse(engine.restart_newapi_container())

    def test_newapi_restart_disabled_never_calls_subprocess(self):
        """禁用桩不得调用 subprocess：远端 VPS 已删除，不存在 SSH 重启路径"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()

        with (
            patch.object(guardian.subprocess, "run") as run,
            patch.object(guardian.subprocess, "Popen") as popen,
        ):
            engine.restart_newapi_container()

        run.assert_not_called()
        popen.assert_not_called()

    def test_newapi_restart_disabled_does_not_write_restart_state(self):
        """禁用桩不写 newapi_restart_time / newapi_restart_fail_time 冷却状态"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine._save_state = Mock()
        engine.telegram = Mock()

        engine.restart_newapi_container()

        self.assertNotIn("newapi_restart_time", engine.state)
        self.assertNotIn("newapi_restart_fail_time", engine.state)

    def test_restart_verified_with_real_listening_port(self):
        """真实路径：真实监听 socket + 真实 check_local_endpoint → 报成功并清零计数"""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            engine = make_engine({
                "restart_counts": {"agentrouter": 1},
                "restarted_proxies": {},
            })
            engine.health = guardian.HealthChecker(Mock())
            engine.telegram = Mock()

            with (
                patch.object(guardian, "LOCAL_PROXY_PROBE_HOST", "127.0.0.1"),
                patch.object(guardian.subprocess, "run"),
                patch.object(guardian.subprocess, "Popen"),
                patch.object(guardian.time, "sleep"),
                patch.object(guardian.Path, "exists", return_value=True),
            ):
                ok = engine.restart_local_proxy("agentrouter", port)
        finally:
            listener.close()

        self.assertTrue(ok)
        self.assertEqual(engine.state["restart_counts"]["agentrouter"], 0)
        self.assertIn("agentrouter", engine.state["restarted_proxies"])
        self.assertNotIn("agentrouter", engine.state.get("restart_alerted", {}))
        alert = engine.telegram.send_alert.call_args.args
        self.assertEqual(alert[0], "本地代理重启")
        self.assertIn("验证端口可达", alert[1])

    def test_fail_streak_survives_guardian_restart(self):
        """失败计数持久化在 state：Guardian 崩溃重启后计数保留，补足 3 次即触发告警"""
        state = {
            "restart_counts": {},
            "newapi_fail_streak": 2,
        }

        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()

        # state 里已有 2 次计数 → 本次失败即达 3 次门槛，触发告警；
        # streak 保留不清零（告警频率由 AlertManager 冷却控制）
        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)
        self.assertEqual(state["newapi_fail_streak"], 3)

    def test_newapi_outage_alert_cooled_down_across_cycles(self):
        """NewAPI 持续故障：告警走真实 AlertManager 冷却，故障期内不逐轮重发"""
        state = {"restart_counts": {}}

        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix.get_balance_trend.return_value = None
        g.alerts = guardian.AlertManager(Mock())  # error 级冷却 1 分钟
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        for _ in range(4):  # streak 1,2,3,4：仅首次越门槛告警，其余被冷却挡住
            g._check_cycle()

        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)
        self.assertEqual(state["newapi_fail_streak"], 4)

    def test_newapi_outage_alerts_once_even_after_cooldown(self):
        """持续故障跨过通用 1 分钟冷却后仍只告警一次，直到恢复。"""
        state = {"restart_counts": {}, "newapi_fail_streak": 2}
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix.get_balance_trend.return_value = None
        g.alerts = guardian.AlertManager(Mock())
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()
        g.alerts.last_alerts["newapi_health"] -= timedelta(minutes=2)
        g._check_cycle()

        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)
        self.assertTrue(state["newapi_outage_alerted"])

    def test_newapi_recovery_rearms_outage_alert(self):
        """恢复会清除故障段标记，下一次新故障可再次告警。"""
        state = {
            "restart_counts": {},
            "newapi_fail_streak": 9,
            "newapi_outage_alerted": True,
        }
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.side_effect = [(True, "ok"), (False, "down")]
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, -1, -1)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = state
        g.autofix.get_balance_trend.return_value = None
        g.alerts = guardian.AlertManager(Mock())
        g.telegram = Mock()
        g._maybe_daily_report = Mock()

        g._check_cycle()
        self.assertEqual(state["newapi_fail_streak"], 0)
        self.assertNotIn("newapi_outage_alerted", state)

        state["newapi_fail_streak"] = guardian.NEWAPI_FAIL_THRESHOLD - 1
        g._check_cycle()
        self.assertEqual(g.autofix.restart_newapi_container.call_count, 1)
        self.assertTrue(state["newapi_outage_alerted"])


    def test_fail_streak_persisted_on_every_failure(self):
        """streak 每次递增都必须 _save_state，Guardian 崩溃后计数保留"""
        state = {
            "restart_counts": {},
            "newapi_fail_streak": 0,
        }
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
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

    def test_newapi_outage_skips_dependent_channel_work(self):
        """NewAPI 不可达时不得继续扇出渠道 API 请求，保留本地代理检查。"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.newapi = Mock()
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}, "newapi_fail_streak": 0}
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()

        g._check_cycle()

        g.health.check_local_proxy.assert_called()
        g.newapi.get_channels.assert_not_called()
        g.autofix.scan_error_channels.assert_not_called()
        g.autofix.check_and_enable_recovered_channels.assert_not_called()
        g.autofix._check_joined_channels_stability.assert_not_called()
        g.health.check_error_rate.assert_not_called()
        g.health.check_balance.assert_not_called()

    def test_restart_unverified_when_port_never_binds(self):
        """真实路径：启动后端口不可达 → 不报成功、不清零计数、返回 False"""
        engine = make_engine({
            "restart_counts": {},
            "restarted_proxies": {},
        })
        engine.health = guardian.HealthChecker(Mock())
        engine.telegram = Mock()

        with (
            patch.object(guardian, "LOCAL_PROXY_PROBE_HOST", "127.0.0.1"),
            patch.object(guardian.subprocess, "run"),
            patch.object(guardian.subprocess, "Popen"),
            patch.object(guardian.time, "sleep"),
            patch.object(guardian.Path, "exists", return_value=True),
        ):
            ok = engine.restart_local_proxy("agentrouter", unused_port())

        self.assertFalse(ok)
        # 未验证 → 计数递增而非清零，restart_alerted 不得被误清
        self.assertEqual(engine.state["restart_counts"]["agentrouter"], 1)
        self.assertNotIn("agentrouter", engine.state.get("restart_alerted", {}))
        alert = engine.telegram.send_alert.call_args.args
        self.assertEqual(alert[0], "本地代理重启未验证")

    def test_local_proxy_probe_disabled_returns_healthy(self):
        """本地代理探针已禁用：统一返回健康三元组，避免误报告警"""
        health = guardian.HealthChecker(Mock())

        ok, msg, alive = health.check_local_proxy(8788, "agentrouter")

        self.assertTrue(ok)
        self.assertTrue(alive)
        self.assertIn("探针已禁用", msg)
        self.assertIn("agentrouter", msg)

    def test_local_proxy_probe_disabled_makes_no_network_io(self):
        """探针禁用后不得发起任何网络 I/O（urllib/socket 均不被调用）"""
        health = guardian.HealthChecker(Mock())

        with (
            patch.object(guardian.urllib.request, "urlopen") as urlopen,
            patch.object(guardian.socket, "create_connection") as create_conn,
        ):
            health.check_local_proxy(8788, "agentrouter")

        urlopen.assert_not_called()
        create_conn.assert_not_called()

    def test_check_local_endpoint_real_socket(self):
        """轻量存活检查：真实 TCP 探测——监听中端口可达，未监听端口不可达"""
        health = guardian.HealthChecker(Mock())
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        try:
            with patch.object(guardian, "LOCAL_PROXY_PROBE_HOST", "127.0.0.1"):
                ok, msg = health.check_local_endpoint(port, "agentrouter")
                self.assertTrue(ok)
                self.assertIn("可达", msg)
                ok, msg = health.check_local_endpoint(unused_port(), "agentrouter")
                self.assertFalse(ok)
                self.assertIn("不可达", msg)
        finally:
            listener.close()

    def test_restart_success_reports_port_verified(self):
        """重启验证改用独立 TCP 探测：端口可达即成功，清零计数并报告端口可达"""
        engine = make_engine({"restart_counts": {}, "restarted_proxies": {}})
        engine.health = Mock()
        engine.health.check_local_endpoint.return_value = (True, "端口可达")
        engine.telegram = Mock()

        with patch.object(guardian.subprocess, "run"), patch.object(
            guardian.subprocess, "Popen"
        ), patch.object(guardian.time, "sleep"):
            recovered = engine.restart_local_proxy("agentrouter", 8788)

        self.assertTrue(recovered)
        self.assertEqual(engine.state["restart_counts"]["agentrouter"], 0)
        alert = engine.telegram.send_alert.call_args.args
        self.assertEqual(alert[0], "本地代理重启")
        self.assertIn("验证端口可达", alert[1])

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
        g.health.check_local_proxy.return_value = (True, "ok", True)
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
        g.health.check_local_proxy.return_value = (True, "ok", True)
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


    def test_budget_exceeded_skips_low_priority_steps(self):
        """预算耗尽：高优先级步骤仍执行，低优先级步骤跳过"""
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok", True)
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
        g.health.check_local_proxy.return_value = (True, "ok", True)
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

    def test_critical_maintenance_runs_before_budgeted_full_scan(self):
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok", True)
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
        order = []
        g.autofix.periodic_ability_fix.side_effect = lambda: order.append("ability")
        g.autofix.cleanup_stale_state.side_effect = lambda: order.append("cleanup")
        g._maybe_daily_report = Mock(side_effect=lambda: order.append("report"))
        g.autofix.full_health_scan.side_effect = lambda _deadline: order.append("scan")

        g._check_cycle()

        self.assertEqual(order, ["ability", "cleanup", "report", "scan"])

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
        self.assertRegex(replaces[0][1], r"heartbeat\.json\.\d+\.\d+\.tmp$")  # 重试版唯一 tmp 名
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

    def test_heartbeat_retry_succeeds_after_transient_replace_failure(self):
        """WinError 5 瞬时失败：换唯一 tmp 名重试，第二次成功且不记错误日志"""
        attempts = []

        def flaky_replace(src, dst):
            attempts.append(str(src))
            if len(attempts) == 1:
                raise OSError("sharing violation")

        with (
            patch.object(guardian.Path, "write_text"),
            patch.object(guardian.os, "replace", side_effect=flaky_replace),
            patch.object(guardian.logger, "error") as err,
            patch.object(guardian.time, "sleep"),
        ):
            guardian._write_heartbeat()

        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0], attempts[1])  # 每次尝试换不同 tmp 名
        err.assert_not_called()


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


    def test_state_corruption_recovers_last_good_snapshot(self):
        """主状态损坏时恢复上一份快照，保留禁用原因而非回到空默认值"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        last_good = guardian.Path.home() / ".omp" / "guardian" / "state.json.last-good"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        last_good.write_text(json.dumps({
            "schema_version": 1,
            "disabled_channels": [{"id": 45, "name": "agentrouter", "manual": False, "reason": "timeout"}],
        }), encoding="utf-8")
        state_file.write_text("{ broken", encoding="utf-8")

        with patch.object(guardian.logger, "error"):
            engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            loaded = engine._load_state()

        self.assertEqual(loaded["disabled_channels"][0]["id"], 45)
        self.assertEqual(loaded["disabled_channels"][0]["reason"], "timeout")

        engine.state = loaded
        lifecycle = engine.derive_channel_lifecycle([
            {"id": 45, "name": "agentrouter", "status": 2, "weight": 0},
        ])
        self.assertEqual([item["id"] for item in lifecycle["disabled_auto"]], [45])
        self.assertEqual(lifecycle["disabled_orphan"], [])

    def test_save_state_uses_process_scoped_atomic_files_and_snapshot(self):
        """状态主文件与 last-good 快照均由进程隔离临时文件原子替换"""
        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        engine.state = {"schema_version": 1, "disabled_channels": [{"id": 3}]}
        with patch.object(guardian.os, "replace") as replace:
            engine._save_state()

        self.assertEqual(replace.call_count, 2)
        destinations = {str(call.args[1]) for call in replace.call_args_list}
        self.assertIn(str(guardian.STATE_FILE), destinations)
        self.assertIn(str(guardian.STATE_BACKUP_FILE), destinations)
        for call in replace.call_args_list:
            self.assertIn(f".{os.getpid()}.tmp", str(call.args[0]))

    def test_state_oserror_not_backed_up(self):
        """state.json 读 I/O 错误（非内容损坏）：不搬文件、有限重试，
        仍失败则拒绝带空状态启动（2026-08-11 评审 P1-3 契约变更）"""
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
            patch.object(guardian.time, "sleep"),
            patch.object(guardian.logger, "error") as err,
        ):
            engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            with self.assertRaises(RuntimeError):
                engine._load_state()

        self.assertGreaterEqual(loads.call_count, 2)  # 重试语义
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
            key=lambda p: (p.stat().st_mtime_ns, p.name),
        )
        self.assertEqual(len(backups), 5)  # 保留最后 5 份
        # 最旧的两份（第 1、2 次）被清理
        remaining = {b.read_bytes() for b in backups}
        self.assertNotIn(b"{ bad 0", remaining)
        self.assertNotIn(b"{ bad 1", remaining)

    def test_state_backup_retention_tie_breaks_same_mtime_by_name(self):
        """全部备份同 mtime 时：按文件名（序号）tie-break，保留序号最大的 5 份"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()

        # 预置 7 份同 mtime 备份，内容=序号
        fixed_ts = "20260803000000000"
        same_epoch = 1754188800.0  # 固定 mtime
        for i in range(7):
            p = state_file.parent / f"state.json.corrupt-{fixed_ts}-1-{i}"
            p.write_bytes(f"content-{i}".encode())
            os.utime(p, (same_epoch, same_epoch))

        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        # 触发一次损坏产生第 8 份新备份
        state_file.write_text("{ tie", encoding="utf-8")
        with patch.object(guardian.logger, "error"):
            with patch.object(guardian, "datetime", wraps=guardian.datetime) as dt:
                dt.now.return_value = datetime.fromisoformat("2026-07-22T00:00:00.000000")
                engine._load_state()

        backups = state_file.parent.glob("state.json.corrupt-*")
        names = sorted(p.name for p in backups)
        # 保留 5 份：序号最大的 4 个预置（3/4/5/6）+ 新备份
        self.assertEqual(len(names), 5)
        self.assertNotIn(f"state.json.corrupt-{fixed_ts}-1-0", names)  # 序号 0 被删
        self.assertNotIn(f"state.json.corrupt-{fixed_ts}-1-1", names)  # 序号 1 被删
        # 序号 2-6 保留（2/3/4/5/6 共 5 份，其中新生成约占了最新，保留逻辑保序号大者）
        self.assertIn(f"state.json.corrupt-{fixed_ts}-1-6", names)

    def test_state_backup_retention_never_deletes_newest_on_coarse_mtime(self):
        """粗粒度 FS(mtime 全同) + 名字槽回收：最新取证必须留存(回归:曾被误删)"""
        state_file = guardian.Path.home() / ".omp" / "guardian" / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        for old in state_file.parent.glob("state.json.corrupt-*"):
            old.unlink()

        engine = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
        fake_now = datetime.fromisoformat("2026-07-22T00:00:00.000000")
        real_stat = Path.stat

        class _CoarseStat:
            """模拟低精度文件系统：所有 corrupt 备份 mtime_ns 相同。"""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, item):
                return getattr(self._inner, item)

            @property
            def st_mtime_ns(self):
                return 1754188800_000000000

        def coarse_stat(self, *args, **kwargs):
            st = real_stat(self, *args, **kwargs)
            if self.name.startswith("state.json.corrupt-"):
                return _CoarseStat(st)
            return st

        with patch.object(guardian.logger, "error"):
            with patch.object(guardian, "datetime", wraps=guardian.datetime) as dt:
                dt.now.return_value = fake_now
                with patch.object(Path, "stat", coarse_stat):
                    for i in range(7):
                        state_file.write_text(f"{{ bad {i}", encoding="utf-8")
                        engine._load_state()

        remaining = {
            p.read_bytes() for p in state_file.parent.glob("state.json.corrupt-*")
        }
        self.assertEqual(len(remaining), 5)
        # 最新取证（第 7 次损坏）必须在
        self.assertIn(b"{ bad 6", remaining)

class OmpRoleTests(unittest.TestCase):
    def test_localhost_role_endpoint_is_actively_probed_with_key(self):
        """本地代理只绑 127.0.0.1：OMP 角色端点只探 localhost，不探 Tailscale"""
        agent_dir = Path.home() / ".omp" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "config.yml").write_text(
            "modelRoles:\n  slow: agentrouter/claude-opus-5:xhigh\nsymbolPreset: ascii\n",
            encoding="utf-8",
        )
        (agent_dir / "models.yml").write_text(
            "providers:\n  agentrouter:\n    baseUrl: http://127.0.0.1:8788/v1\n",
            encoding="utf-8",
        )
        engine = make_engine()
        engine._omp_check_count = guardian.OMP_ROLE_CHECK_INTERVAL - 1
        engine.telegram.send_alert = Mock()

        with patch.object(
            guardian.AutoFixEngine, "_probe_endpoint", return_value=False
        ) as probe:
            engine.check_omp_roles_health()

        probe.assert_called_once_with("http://127.0.0.1:8788/v1")
        engine.telegram.send_alert.assert_called_once()


    @staticmethod
    def _ok_response(status=200):
        """_probe_endpoint 用 `with opener.open(...) as resp`，需支持上下文管理协议。"""
        resp = MagicMock()
        resp.__enter__.return_value = Mock(status=status)
        return resp

    def test_probe_endpoint_treats_500_as_down(self):
        """HTTPError 500 是服务端故障，不算端点存活"""
        with patch.object(
            guardian._PROBE_OPENER,
            "open",
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
            guardian._PROBE_OPENER,
            "open",
            side_effect=guardian.urllib.error.HTTPError(
                "http://127.0.0.1:8788/v1/models", 401, "Unauthorized", None, None
            ),
        ):
            self.assertTrue(
                guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:8788/v1")
            )

    def test_probe_endpoint_sends_correct_bearer_key_by_port(self):
        """按端口选择 Bearer key：agentrouter 8788 → AGENTROUTER_PROXY_KEY"""
        with patch.object(
            guardian._PROBE_OPENER,
            "open",
            return_value=self._ok_response(),
        ), patch.object(
            guardian.urllib.request,
            "Request",
            wraps=guardian.urllib.request.Request,
        ) as mk_req:
            self.assertTrue(
                guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:8788/v1")
            )
            auth = mk_req.call_args.kwargs["headers"]["Authorization"]
            self.assertEqual(auth, f"Bearer {guardian.AGENTROUTER_PROXY_KEY}")
            self.assertIn("127.0.0.1:8788", mk_req.call_args.args[0])

    def test_probe_endpoint_uses_newapi_probe_key_on_loopback_newapi_ports(self):
        """NEWAPI_PROBE_KEY 配置后，NewAPI 本机端口（含 TTFT 网关 3003）
        探测用真实 token 而非 'any'，消除 relay 日志 401 噪音"""
        with patch.object(guardian, "NEWAPI_PROBE_KEY", "sk-probe-token"), patch.object(
            guardian._PROBE_OPENER,
            "open",
            return_value=self._ok_response(),
        ), patch.object(
            guardian.urllib.request,
            "Request",
            wraps=guardian.urllib.request.Request,
        ) as mk_req:
            guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:3002/v1")
            auth = mk_req.call_args.kwargs["headers"]["Authorization"]
            self.assertEqual(auth, "Bearer sk-probe-token")

            guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:3003")
            auth = mk_req.call_args.kwargs["headers"]["Authorization"]
            self.assertEqual(auth, "Bearer sk-probe-token")

    def test_probe_endpoint_never_sends_real_key_to_non_loopback(self):
        """非 loopback 探测一律 'any'：真实 key 不得离开本机（防重定向泄露）"""
        with patch.object(guardian, "NEWAPI_PROBE_KEY", "sk-probe-token"), patch.object(
            guardian._PROBE_OPENER,
            "open",
            return_value=self._ok_response(),
        ), patch.object(
            guardian.urllib.request,
            "Request",
            wraps=guardian.urllib.request.Request,
        ) as mk_req:
            guardian.AutoFixEngine._probe_endpoint("https://api.example.com:3002/v1")
            auth = mk_req.call_args.kwargs["headers"]["Authorization"]
            self.assertEqual(auth, "Bearer any")

    def test_probe_endpoint_falls_back_to_any_without_probe_key(self):
        """NEWAPI_PROBE_KEY 未配置时维持 'any' 原语义（任何响应算存活）"""
        with patch.object(guardian, "NEWAPI_PROBE_KEY", ""), patch.object(
            guardian._PROBE_OPENER,
            "open",
            return_value=self._ok_response(),
        ), patch.object(
            guardian.urllib.request,
            "Request",
            wraps=guardian.urllib.request.Request,
        ) as mk_req:
            guardian.AutoFixEngine._probe_endpoint("http://127.0.0.1:3002/v1")
            auth = mk_req.call_args.kwargs["headers"]["Authorization"]
            self.assertEqual(auth, "Bearer any")

    def test_probe_opener_never_follows_redirect_with_bearer_header(self):
        """P3-20：探测 opener 拒绝跟随 3xx —— Authorization 头不得重放到重定向目标。

        用两个真实 loopback HTTPServer 验证：源站 302 指向"外部"站点，
        外部站点记录收到的 Authorization。断言外部站点零请求，且 3xx 按
        HTTPError 抛出（<500 仍判存活，语义不变）。
        """
        leaked_auth = []

        class SinkHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                leaked_auth.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", sink_url)
                self.end_headers()

            def log_message(self, *args):
                pass

        sink = HTTPServer(("127.0.0.1", 0), SinkHandler)
        src = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        sink_url = f"http://127.0.0.1:{sink.server_port}/sink"
        threading.Thread(target=sink.serve_forever, daemon=True).start()
        threading.Thread(target=src.serve_forever, daemon=True).start()
        try:
            req = guardian.urllib.request.Request(
                f"http://127.0.0.1:{src.server_port}/v1/models",
                headers={"Authorization": "Bearer sk-probe-token"},
            )
            with self.assertRaises(guardian.urllib.error.HTTPError) as caught:
                guardian._PROBE_OPENER.open(req, timeout=5)
            self.assertEqual(caught.exception.code, 302)
            # 3xx <500 → _probe_endpoint 仍判存活（P3-20 不改变存活语义）
            self.assertTrue(caught.exception.code < 500)
        finally:
            src.shutdown()
            sink.shutdown()
            src.server_close()
            sink.server_close()

        self.assertEqual(leaked_auth, [], "Bearer token leaked to redirect target")




class WatchdogContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(__file__).with_name("watchdog.ps1").read_text(encoding="utf-8")

    def test_stale_threshold_tolerates_slow_guardian_cycles(self):
        self.assertIn('$staleSec = 180', self.source)
        self.assertIn("'^pythonw?(\\.exe)?$'", self.source)

    def test_restart_uses_canonical_task_with_backoff(self):
        self.assertIn('$guardianTask = "NewAPI Guardian"', self.source)
        self.assertIn('$restartBackoffSec = 300', self.source)
        self.assertIn('Start-ScheduledTask -TaskName $guardianTask', self.source)
        self.assertIn(r'Local\CCSwitchGuardianWatchdog', self.source)
        self.assertNotIn('hub restart=on-failure', self.source)


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



class Step6ChannelPerfTests(unittest.TestCase):
    """2026-08-11 评审 P1-1/P1-2：主循环 step 6（慢渠道检测 + 性能记录）曾是唯一
    生产调用点，被删后 channel_perf 恒空、权重调整/慢渠道禁用全链路失效。"""

    def _make_guardian(self):
        g = guardian.Guardian.__new__(guardian.Guardian)
        engine = make_engine(
            {
                "disabled_channels": [],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        g.autofix = engine
        g.health = guardian.HealthChecker(engine.newapi)
        return g, engine

    def test_step6_records_each_distinct_test_result_once(self):
        g, engine = self._make_guardian()
        channel = {
            "id": 76,
            "name": "c",
            "status": 1,
            "weight": 5,
            "response_time": 100,
            "test_time": 111,
        }

        g._check_channels_health([channel])
        self.assertEqual(len(engine.channel_perf[76]), 1)

        # 同一 test_time 轮询不重复记录（去重语义保留）
        g._check_channels_health([channel])
        self.assertEqual(len(engine.channel_perf[76]), 1)

        channel["test_time"] = 222
        g._check_channels_health([channel])
        self.assertEqual(len(engine.channel_perf[76]), 2)

    def test_step6_disables_slow_channel_after_active_test_fails(self):
        g, engine = self._make_guardian()
        engine.newapi.test_results.append((False, "probe timeout"))

        for t in (1, 2, 3):
            channel = {
                "id": 77,
                "name": "slow",
                "status": 1,
                "weight": 5,
                "response_time": guardian.CHANNEL_SLOW_THRESHOLD_MS + 1,
                "test_time": t,
            }
            g._check_channels_health([channel])

        # 3 份不同慢结果触发主动测试；测试失败 → 禁用并排队（非降权路径）
        self.assertEqual(engine.newapi.disable_calls, [77])
        self.assertEqual(
            [r["id"] for r in engine.state["disabled_channels"]], [77]
        )


class StateLoadHardeningTests(unittest.TestCase):
    """2026-08-11 评审 P1-3：_load_state 遇 OSError 静默降级空 defaults，
    随后 _save_state 双写覆盖 last-good → 状态+备份双丢。"""

    def _engine(self):
        return guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)

    def test_oserror_retries_then_refuses_empty_start(self):
        engine = self._engine()
        bad = Mock()
        bad.exists.return_value = True
        bad.read_text.side_effect = OSError("file locked")

        with patch.object(guardian, "STATE_FILE", bad), patch.object(
            guardian.time, "sleep"
        ):
            with self.assertRaises(RuntimeError):
                engine._load_state()

        self.assertGreaterEqual(bad.read_text.call_count, 2)

    def test_oserror_recovers_on_retry(self):
        engine = self._engine()
        flaky = Mock()
        flaky.exists.return_value = True
        flaky.read_text.side_effect = [
            OSError("file locked"),
            json.dumps({"disabled_channels": [{"id": 1, "name": "x"}]}),
        ]

        with patch.object(guardian, "STATE_FILE", flaky), patch.object(
            guardian.time, "sleep"
        ):
            state = engine._load_state()

        self.assertEqual(state["disabled_channels"], [{"id": 1, "name": "x"}])
        # 默认值合并语义不变
        self.assertIn("weight_history", state)


class CleanupGuardTests(unittest.TestCase):
    """2026-08-11 评审 P1-4：get_channels 失败返回 [] 时 cleanup 会把
    disabled/degraded/weight_history 全量误删且不可自愈。"""

    def test_empty_channel_list_skips_cleanup(self):
        engine = make_engine(
            {
                "disabled_channels": [
                    {"id": 99, "name": "x", "time": "2026-08-01T00:00:00"}
                ],
                "weight_history": {
                    "99": {"weight": 5, "time": "2026-08-01T00:00:00"}
                },
                "degraded_channels": {"99": {"time": "2026-08-01T00:00:00"}},
                "joined_channels": {},
            }
        )
        engine._cleanup_count = guardian.STATE_CLEANUP_INTERVAL - 1

        engine.cleanup_stale_state()  # FakeNewAPI.channels 为空 → 模拟 API 失败

        self.assertEqual(len(engine.state["disabled_channels"]), 1)
        self.assertIn("99", engine.state["weight_history"])
        self.assertIn("99", engine.state["degraded_channels"])


class RecoveryBackoffTests(unittest.TestCase):
    """2026-08-11 评审 P1-5：探针全 incompatible 时 failures 恒 0、退避恒 5min，
    队首记录持续烧恢复配额饿死其后渠道。"""

    def test_incompatible_probes_still_grow_backoff(self):
        engine = make_engine(
            {
                "disabled_channels": [
                    {
                        "id": 88,
                        "name": "probe-incompatible",
                        "time": "2026-08-10T00:00:00",
                        "manual": False,
                        "recovery_failures": 0,
                    }
                ],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        for _ in range(guardian.RECOVERY_TEST_COUNT):
            engine.newapi.test_results.append(
                (False, "HTTP 403 non_agentic_blocked: only serves agentic clients")
            )

        engine.check_and_enable_recovered_channels()

        record = engine.state["disabled_channels"][0]
        self.assertEqual(record["recovery_failures"], 1)
        # 探针不兼容不是恢复：不启用、不入池
        self.assertEqual(engine.newapi.enable_calls, [])


class ExclusionEnforcementTests(unittest.TestCase):
    """2026-08-11 评审 P1-6：AUTO_BAN_RECOVERY_EXCLUSIONS 只在导入时检查，
    Guardian 自禁用路径与恢复循环绕过排除语义。"""

    def test_append_disabled_skips_policy_excluded_auto_record(self):
        excluded = sorted(guardian.AUTO_BAN_RECOVERY_EXCLUSIONS)[0]
        engine = make_engine(
            {
                "disabled_channels": [],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )

        engine._append_disabled(
            {
                "id": excluded,
                "name": "x",
                "reason": "full_scan: boom",
                "time": "2026-08-11T00:00:00",
                "manual": False,
            }
        )
        self.assertEqual(engine.state["disabled_channels"], [])

        # 显式人工记录仍允许（/disable 命令语义不受排除集限制）
        engine._append_disabled(
            {
                "id": excluded,
                "name": "x",
                "reason": "manual",
                "time": "2026-08-11T00:00:00",
                "manual": True,
            }
        )
        self.assertEqual(len(engine.state["disabled_channels"]), 1)

    def test_recovery_never_reenables_excluded_channel(self):
        excluded = sorted(guardian.AUTO_BAN_RECOVERY_EXCLUSIONS)[0]
        engine = make_engine(
            {
                "disabled_channels": [
                    {
                        "id": excluded,
                        "name": "excluded",
                        "time": "2026-08-10T00:00:00",
                        "manual": False,
                        "recovery_failures": 0,
                    }
                ],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        for _ in range(guardian.RECOVERY_TEST_COUNT):
            engine.newapi.test_results.append((True, "ok"))

        engine.check_and_enable_recovered_channels()

        self.assertEqual(engine.newapi.enable_calls, [])
        self.assertEqual(engine.newapi.test_calls, [])


class KeywordBoundaryTests(unittest.TestCase):
    """2026-08-11 评审 P2-7：数字关键词必须按词边界匹配，不得裸子串。"""

    def test_request_id_containing_402_is_not_a_balance_failure(self):
        msg = 'upstream error, request_id: req_20260810123402abc'
        self.assertIsNone(guardian._matched_disable_keyword(msg))

    def test_timestamp_digits_containing_401_do_not_disable(self):
        self.assertIsNone(
            guardian._matched_disable_keyword("failed at 2026-08-10T13:24:01.4012Z")
        )

    def test_real_402_still_disables(self):
        self.assertEqual(guardian._matched_disable_keyword("HTTP 402 Payment Required"), "402")
        self.assertEqual(guardian._matched_disable_keyword('{"code":402}'), "402")
        self.assertEqual(guardian._matched_disable_keyword("(402)"), "402")

    def test_real_401_still_disables(self):
        self.assertEqual(guardian._matched_disable_keyword("status=401 unauthorized"), "401")

    def test_text_keywords_keep_substring_semantics(self):
        """文本关键词仍走子串：quota_exceeded 一类复合词不得漏判"""
        self.assertEqual(guardian._matched_disable_keyword("quota_exceeded"), "quota")
        self.assertEqual(guardian._matched_disable_keyword("余额不足，请充值"), "余额不足")

    def test_request_id_containing_429_is_not_transient_rate_limit(self):
        """反向：request_id 里的 429 不得把真·硬错误当瞬态限流跳过"""
        msg = "余额不足 (402), request_id: req-429abc"
        self.assertFalse(guardian._is_transient_rate_limit(msg))
        self.assertEqual(guardian._matched_disable_keyword(msg), "余额不足")

    def test_real_429_still_transient(self):
        self.assertTrue(guardian._is_transient_rate_limit("HTTP 429 too many requests"))
        self.assertTrue(guardian._is_transient_rate_limit("rate limit exceeded"))

    def test_prepay_quota_failure_disables(self):
        """2026-08-20：预扣费额度失败（403）是余额耗尽，必须命中禁用词。

        tabitoken 拆分当日 key#1/key#3 余额低于预扣门槛，403 报文不含旧词表
        任何关键词，渠道持续接客导致 OMP 反复故障路由。
        """
        msg = "预扣费额度失败, 用户剩余额度: ＄0.209590, 需要预扣费额度: ＄0.800000"
        self.assertEqual(guardian._matched_disable_keyword(msg), "预扣费额度失败")
        self.assertFalse(guardian._is_transient_rate_limit(msg))

    def test_daily_free_credits_exhausted_disables(self):
        """sotamodel 免费日额度耗尽：虽带 429 但属硬错误，不得当瞬态限流跳过。"""
        msg = "bad response status code 429, message: daily_free_credits_exhausted"
        self.assertEqual(
            guardian._matched_disable_keyword(msg), "daily_free_credits_exhausted"
        )
        self.assertFalse(guardian._is_transient_rate_limit(msg))

    def test_quota_exhausted_429_is_hard_failure(self):
        msg = "HTTP 429 quota exhausted: insufficient_quota"
        self.assertFalse(guardian._is_transient_rate_limit(msg))
        self.assertEqual(guardian._matched_disable_keyword(msg), "quota")

    def test_error_scan_ignores_channel_whose_only_402_is_in_request_id(self):
        """端到端：错误扫描不得因 request_id 含 402 而禁用健康渠道"""
        engine = make_engine({"weight_history": {}, "degraded_channels": {}, "disabled_channels": []})
        engine.newapi.channels = {
            45: {"id": 45, "name": "agentrouter", "status": 1, "weight": 5, "used_quota": 1}
        }
        engine._scan_count = guardian.ERROR_SCAN_INTERVAL - 1
        engine._scan_offset = 0
        engine.newapi.test_results.append(
            (False, "upstream 500, request_id: req_20260810123402xyz")
        )

        engine.scan_error_channels()

        self.assertEqual(engine.newapi.disable_calls, [])


class GlobalMutexTests(unittest.TestCase):
    """2026-08-11 评审 P2-8：单实例互斥必须跨会话（Global\\），不得只在会话内生效。"""

    @unittest.skipUnless(sys.platform == "win32", "Windows-only mutex path")
    def test_uses_global_namespace(self):
        import ctypes

        created = []

        class FakeKernel:
            def __init__(self):
                self.CreateMutexW = Mock(side_effect=self._create)
                self.CloseHandle = Mock()

            def _create(self, _attrs, _initial, name):
                created.append(name)
                return 1234

        fake = FakeKernel()
        with patch.object(ctypes, "WinDLL", return_value=fake), patch.object(
            ctypes, "get_last_error", return_value=0
        ), patch.object(ctypes, "set_last_error"):
            handle = guardian._acquire_single_instance()

        self.assertEqual(handle, 1234)
        self.assertEqual(created, ["Global\\NewAPIGuardian"])

    @unittest.skipUnless(sys.platform == "win32", "Windows-only mutex path")
    def test_already_exists_reports_duplicate(self):
        import ctypes

        fake = Mock()
        fake.CreateMutexW = Mock(return_value=1234)
        with patch.object(ctypes, "WinDLL", return_value=fake), patch.object(
            ctypes, "get_last_error", return_value=183
        ), patch.object(ctypes, "set_last_error"):
            self.assertIsNone(guardian._acquire_single_instance())
        fake.CloseHandle.assert_called_once_with(1234)

    @unittest.skipUnless(sys.platform == "win32", "Windows-only mutex path")
    def test_access_denied_treated_as_duplicate_not_local_fallback(self):
        """Global 名字存在但 DACL 拒绝 → 按重复实例退出，绝不改跑 Local 绕过互斥"""
        import ctypes

        created = []

        fake = Mock()
        fake.CreateMutexW = Mock(side_effect=lambda _a, _i, name: created.append(name) or 0)
        with patch.object(ctypes, "WinDLL", return_value=fake), patch.object(
            ctypes, "get_last_error", return_value=5
        ), patch.object(ctypes, "set_last_error"), patch.object(guardian.logger, "warning"):
            self.assertIsNone(guardian._acquire_single_instance())

        self.assertEqual(created, ["Global\\NewAPIGuardian"])

    @unittest.skipUnless(sys.platform == "win32", "Windows-only mutex path")
    def test_missing_privilege_falls_back_to_local_and_logs_error(self):
        import ctypes

        created = []
        errors = iter([1314, 0, 0])
        handles = iter([0, 4321])

        def _create(_attrs, _initial, name):
            created.append(name)
            return next(handles)

        fake = Mock()
        fake.CreateMutexW = Mock(side_effect=_create)
        with patch.object(ctypes, "WinDLL", return_value=fake), patch.object(
            ctypes, "get_last_error", side_effect=lambda: next(errors)
        ), patch.object(ctypes, "set_last_error"), patch.object(
            guardian.logger, "error"
        ) as err:
            handle = guardian._acquire_single_instance()

        self.assertEqual(handle, 4321)
        self.assertEqual(created, ["Global\\NewAPIGuardian", "Local\\NewAPIGuardian"])
        err.assert_called()


class StepIsolationTests(unittest.TestCase):
    """2026-08-11 评审 P2-10：单步异常不得吞掉后续全部步骤。"""

    @staticmethod
    def _make_guardian():
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (True, "ok")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.health.check_error_rate.return_value = (True, 0.0, 0, 0)
        g.health.check_balance.return_value = (True, 100, 200)
        g.newapi = Mock()
        g.newapi.get_channels.return_value = []
        g.autofix = Mock()
        g.autofix.state = {"restart_counts": {}}
        g.autofix.get_balance_trend.return_value = None
        g.alerts = Mock()
        g.alerts.should_alert.return_value = False
        g.telegram = Mock()
        g._maybe_daily_report = Mock()
        return g

    def test_failing_early_step_does_not_skip_later_steps(self):
        g = self._make_guardian()
        g.autofix.scan_error_channels.side_effect = RuntimeError("boom")

        with patch.object(guardian.logger, "error") as err:
            g._check_cycle()

        err.assert_called()
        # 后续步骤照常执行（原实现会全部丢失）
        g.autofix.check_and_enable_recovered_channels.assert_called_once()
        g.autofix.full_health_scan.assert_called_once()
        g.autofix.export_metrics.assert_called_once()
        g._maybe_daily_report.assert_called_once()

    def test_failing_weight_step_leaves_metrics_export_with_safe_default(self):
        """weight adjust 抛异常 → channels 保持 []，metrics 导出不得 NameError"""
        g = self._make_guardian()
        g.newapi.get_channels.side_effect = [RuntimeError("parse fail"), []]

        with patch.object(guardian.logger, "error"):
            g._check_cycle()

        g.autofix.export_metrics.assert_called_once()
        self.assertEqual(g.autofix.export_metrics.call_args.args[0], [])

    def test_step_failure_alerts_with_step_name(self):
        g = self._make_guardian()
        g.alerts.should_alert.return_value = True
        g.autofix.full_health_scan.side_effect = RuntimeError("scan boom")

        with patch.object(guardian.logger, "error"):
            g._check_cycle()

        titles = [c.args[0] for c in g.telegram.send_alert.call_args_list]
        self.assertIn("Guardian 步骤异常", titles)
        keys = [c.args[0] for c in g.alerts.should_alert.call_args_list]
        self.assertIn("step_full health scan", keys)

    def test_every_step_refreshes_heartbeat(self):
        """P2-9：每步执行前刷心跳，长周期不得被 watchdog 误判卡死"""
        g = self._make_guardian()

        with patch.object(guardian, "_write_heartbeat") as hb:
            g._check_cycle()

        # 步骤数远多于 1：证明不是只在 cycle 开头写一次
        self.assertGreaterEqual(hb.call_count, 10)


class RecoveryHeartbeatTests(unittest.TestCase):
    """2026-08-11 评审 P2-9：恢复探测批次中途必须刷心跳。"""

    def test_recovery_probes_refresh_heartbeat_each_attempt(self):
        engine = make_engine(
            {
                "disabled_channels": [
                    {
                        "id": 91,
                        "name": "recovering",
                        "time": "2026-08-10T00:00:00",
                        "manual": False,
                        "recovery_failures": 0,
                    }
                ],
                "weight_history": {},
                "degraded_channels": {},
                "joined_channels": {},
            }
        )
        engine.newapi.channels = {91: {"id": 91, "name": "recovering", "status": 0}}
        for _ in range(guardian.RECOVERY_TEST_COUNT):
            engine.newapi.test_results.append((False, "timeout"))

        with patch.object(guardian, "_write_heartbeat") as hb, patch.object(
            guardian.time, "sleep"
        ):
            engine.check_and_enable_recovered_channels()

        self.assertEqual(hb.call_count, guardian.RECOVERY_TEST_COUNT)


class OutageAlertPersistenceTests(unittest.TestCase):
    """2026-08-11 评审 P2-11：告警投递失败不得钉死"已告警"标记。"""

    @staticmethod
    def _make_guardian(state):
        g = guardian.Guardian.__new__(guardian.Guardian)
        g.health = Mock()
        g.health.check_newapi.return_value = (False, "down")
        g.health.check_local_proxy.return_value = (True, "ok", True)
        g.newapi = Mock()
        g.autofix = Mock()
        g.autofix.state = state
        g.alerts = Mock()
        g.alerts.should_alert.return_value = True
        g.telegram = Mock()
        return g

    def test_undelivered_alert_leaves_flag_unset_for_retry(self):
        state = {"restart_counts": {}, "newapi_fail_streak": guardian.NEWAPI_FAIL_THRESHOLD - 1}
        g = self._make_guardian(state)
        g.autofix.restart_newapi_container.return_value = False

        with patch.object(guardian.logger, "warning"):
            g._check_cycle()

        self.assertNotIn("newapi_outage_alerted", state)

        # 下一轮仍会重试告警
        g.autofix.restart_newapi_container.reset_mock()
        with patch.object(guardian.logger, "warning"):
            g._check_cycle()
        g.autofix.restart_newapi_container.assert_called_once()

    def test_delivered_alert_persists_flag_and_stops_repeating(self):
        state = {"restart_counts": {}, "newapi_fail_streak": guardian.NEWAPI_FAIL_THRESHOLD - 1}
        g = self._make_guardian(state)
        g.autofix.restart_newapi_container.return_value = True

        with patch.object(guardian.logger, "warning"):
            g._check_cycle()

        self.assertTrue(state["newapi_outage_alerted"])

        g.autofix.restart_newapi_container.reset_mock()
        with patch.object(guardian.logger, "warning"):
            g._check_cycle()
        g.autofix.restart_newapi_container.assert_not_called()


class DegradeUpdateAtomicityTests(unittest.TestCase):
    """2026-08-11 评审 P3-16：update 失败不得留下未落库的本地权重。"""

    def test_failed_update_leaves_caller_channel_untouched(self):
        engine = make_engine()
        engine.newapi.update_channel = lambda _c: False
        channel = {"id": 7, "name": "slow", "status": 1, "weight": 10}

        self.assertFalse(engine.degrade_channel_weight(channel, "test"))
        self.assertEqual(channel["weight"], 10)

    def test_successful_update_writes_back_new_weight(self):
        engine = make_engine()
        channel = {"id": 7, "name": "slow", "status": 1, "weight": 10}

        self.assertTrue(engine.degrade_channel_weight(channel, "test"))
        self.assertEqual(channel["weight"], 5)
        self.assertEqual(engine.newapi.updates[0]["weight"], 5)


class RequestIdAttributionTests(unittest.TestCase):
    """2026-08-11 评审 P3-17：无 channel_id 归属的日志不得贡献 request_id。"""

    @staticmethod
    def _engine_with_logs(logs):
        engine = make_engine()
        engine.newapi.get_logs = lambda *_a, **_kw: logs
        return engine

    def test_unattributed_log_is_not_credited_to_channel(self):
        engine = self._engine_with_logs(
            [{"channel_id": None, "content": "boom", "request_id": "req-other"}]
        )
        self.assertEqual(engine._error_request_ids(45, "boom"), "")

    def test_matching_channel_log_still_yields_request_id(self):
        engine = self._engine_with_logs(
            [{"channel_id": 45, "content": "boom", "request_id": "req-45"}]
        )
        self.assertEqual(engine._error_request_ids(45, "boom"), "req-45")

    def test_other_channel_log_is_filtered(self):
        engine = self._engine_with_logs(
            [{"channel_id": 73, "content": "boom", "request_id": "req-73"}]
        )
        self.assertEqual(engine._error_request_ids(45, "boom"), "")


def tearDownModule():
    logging.shutdown()
    _TEST_HOME.cleanup()


if __name__ == "__main__":
    unittest.main()


class OpusEmptyResponseTests(unittest.TestCase):
    """justwoker opus 空响应率监控：样本口径、阈值告警与冷却。"""

    @staticmethod
    def _make_db(rows):
        tmp = tempfile.TemporaryDirectory()
        db = Path(tmp.name) / "new-api.db"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(
                "CREATE TABLE logs ("
                "id INTEGER PRIMARY KEY, created_at INTEGER, type INTEGER, "
                "model_name TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, "
                "channel_id INTEGER)"
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO logs (created_at, type, model_name, prompt_tokens, "
                    "completion_tokens, channel_id) VALUES (?, ?, ?, ?, ?, ?)",
                    row,
                )
            conn.commit()
        return db, tmp

    @staticmethod
    def _rows(total, empty, now):
        rows = []
        for i in range(total):
            completion = 0 if i < empty else 100
            rows.append(
                (int(now) - i * 60, 2, "claude-opus-5", 1500, completion, 94)
            )
        return rows

    def test_sample_too_small_skips_alert(self):
        """样本不足阈值时不告警，也不消耗告警冷却。"""
        now = time.time()
        db, tmp = self._make_db(self._rows(5, 0, now))
        with patch.object(guardian, "NEWAPI_DB", db):
            g = guardian.Guardian.__new__(guardian.Guardian)
            g.autofix = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            g.alerts = Mock()
            g.alerts.should_alert.return_value = True
            g.telegram = Mock()
            g._step_opus_empty_response()
        g.alerts.should_alert.assert_not_called()
        g.telegram.send_alert.assert_not_called()
        tmp.cleanup()

    def test_rate_below_threshold_does_not_alert(self):
        """空轮率低于阈值时只记录不告警。"""
        now = time.time()
        db, tmp = self._make_db(self._rows(100, 10, now))
        with patch.object(guardian, "NEWAPI_DB", db):
            g = guardian.Guardian.__new__(guardian.Guardian)
            g.autofix = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            g.alerts = Mock()
            g.alerts.should_alert.return_value = True
            g.telegram = Mock()
            g._step_opus_empty_response()
        g.alerts.should_alert.assert_not_called()
        g.telegram.send_alert.assert_not_called()
        tmp.cleanup()

    def test_rate_above_threshold_sends_alert(self):
        """空轮率超过阈值时通过 Telegram 告警。"""
        now = time.time()
        db, tmp = self._make_db(self._rows(100, 25, now))
        with patch.object(guardian, "NEWAPI_DB", db):
            g = guardian.Guardian.__new__(guardian.Guardian)
            g.autofix = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            g.alerts = Mock()
            g.alerts.should_alert.return_value = True
            g.telegram = Mock()
            g._step_opus_empty_response()
        g.alerts.should_alert.assert_called_once_with(
            "opus_empty_response", "warning"
        )
        g.telegram.send_alert.assert_called_once()
        title, message, level = g.telegram.send_alert.call_args.args
        self.assertIn("opus 空响应率超标", title)
        self.assertIn("25.0%", message)
        self.assertEqual(level, "warning")
        tmp.cleanup()

    def test_alert_cooldown_suppresses_duplicate(self):
        """同一故障段内告警冷却避免重复刷群。"""
        now = time.time()
        db, tmp = self._make_db(self._rows(100, 30, now))
        with patch.object(guardian, "NEWAPI_DB", db):
            g = guardian.Guardian.__new__(guardian.Guardian)
            g.autofix = guardian.AutoFixEngine.__new__(guardian.AutoFixEngine)
            g.alerts = guardian.AlertManager(Mock())
            g.telegram = Mock()
            g._step_opus_empty_response()
            g._step_opus_empty_response()
        self.assertEqual(g.telegram.send_alert.call_count, 1)
        tmp.cleanup()
