"""Unit tests for bounded NewAPI/OMP update helpers."""
from __future__ import annotations

import importlib.util
import json
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


OPS_DIR = Path(__file__).parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPS_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = load("newapi_local_smoke_for_updates", "newapi-local-smoke.py")
affinity = load("update_newapi_affinity_for_tests", "update_newapi_affinity.py")
omp_context = load("update_omp_model_context_for_tests", "update_omp_model_context.py")
anyrouter = load("split_anyrouter_channel_for_tests", "split_anyrouter_channel.py")
newapi_retry = load(
    "update_newapi_retry_budget_for_tests", "update_newapi_retry_budget.py"
)
anyrouter_timeout = load(
    "update_anyrouter_timeout_budget_for_tests", "update_anyrouter_timeout_budget.py"
)
muyuan_sol = load(
    "update_muyuan_sol_primary_for_tests", "update_muyuan_sol_primary.py"
)
quarantine = load(
    "quarantine_newapi_channels_for_tests", "quarantine_newapi_channels.py"
)


class AffinityUpdateTests(unittest.TestCase):
    def test_rule_updates_satisfy_smoke_contract(self) -> None:
        rules = [
            {"name": name, "model_regex": patterns}
            for name, patterns in affinity.RULE_UPDATES.items()
        ]
        options = [
            {
                "key": "channel_affinity_setting.rules",
                "value": json.dumps(rules),
            }
        ]

        self.assertEqual(smoke.affinity_rule_violations(options), [])

    def test_retry_budget_matches_smoke_contract(self) -> None:
        self.assertEqual(newapi_retry.TARGET_RETRY_TIMES, "1")
        self.assertEqual(newapi_retry.TARGET_RETRY_STATUS_CODES, "408,500-503")
        self.assertEqual(newapi_retry.TARGET_AUTOMATIC_DISABLE, "false")
        self.assertEqual(smoke.REQUIRED_OPTIONS["RetryTimes"], "1")
        self.assertEqual(
            smoke.REQUIRED_OPTIONS["AutomaticRetryStatusCodes"], "408,500-503"
        )
        self.assertEqual(
            smoke.REQUIRED_OPTIONS["AutomaticDisableChannelEnabled"], "false"
        )


class NewApiRetryBudgetTests(unittest.TestCase):
    def test_failed_readback_rolls_back_original_retry_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[dict | None] = []

            class FakeSmoke:
                NEWAPI_BASE = "http://127.0.0.1:3002"
                DEPLOY_DIR = Path(temp_dir)

                @staticmethod
                def admin_auth():
                    return "redacted", 1

                @staticmethod
                def http_json(_url, **kwargs):
                    calls.append(kwargs.get("body"))
                    if len(calls) == 1:
                        return 200, {
                            "data": [
                                {"key": "RetryTimes", "value": "3"},
                                {
                                    "key": "AutomaticRetryStatusCodes",
                                    "value": "408,429,500-504",
                                },
                                {
                                    "key": "AutomaticDisableChannelEnabled",
                                    "value": "<nil>",
                                },
                            ]
                        }
                    if len(calls) in (2, 3, 4):
                        return 200, {"success": True}
                    if len(calls) == 5:
                        return 500, {}
                    if len(calls) in (6, 7, 8):
                        return 200, {"success": True}
                    return 200, {
                        "data": [
                            {"key": "RetryTimes", "value": "3"},
                            {
                                "key": "AutomaticRetryStatusCodes",
                                "value": "408,429,500-504",
                            },
                            {
                                "key": "AutomaticDisableChannelEnabled",
                                "value": "<nil>",
                            },
                        ]
                    }

            with (
                patch.object(newapi_retry, "load_smoke", return_value=FakeSmoke),
                patch.object(sys, "argv", ["update_newapi_retry_budget.py", "--apply"]),
            ):
                self.assertEqual(newapi_retry.main(), 1)

            self.assertEqual(calls[1], {"key": "RetryTimes", "value": "1"})
            self.assertEqual(
                calls[2],
                {"key": "AutomaticRetryStatusCodes", "value": "408,500-503"},
            )
            self.assertEqual(
                calls[3],
                {"key": "AutomaticDisableChannelEnabled", "value": "false"},
            )
            self.assertEqual(calls[5], {"key": "RetryTimes", "value": "3"})
            self.assertEqual(
                calls[6],
                {
                    "key": "AutomaticRetryStatusCodes",
                    "value": "408,429,500-504",
                },
            )
            self.assertEqual(
                calls[7],
                {"key": "AutomaticDisableChannelEnabled", "value": "<nil>"},
            )
            self.assertIsNone(calls[8])


class QuarantineChannelTests(unittest.TestCase):
    def test_zero_weight_channel_does_not_replay_masked_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[tuple[str, str, dict | None]] = []

            class FakeSmoke:
                NEWAPI_BASE = "http://127.0.0.1:3002"
                DEPLOY_DIR = Path(temp_dir)

                @staticmethod
                def admin_auth():
                    return "redacted", 1

                @staticmethod
                def http_json(url, **kwargs):
                    method = kwargs.get("method", "GET")
                    calls.append((method, url, kwargs.get("body")))
                    if method == "GET" and url.endswith("/api/channel/57"):
                        reads = sum(
                            1
                            for called_method, called_url, _ in calls
                            if called_method == "GET"
                            and called_url.endswith("/api/channel/57")
                        )
                        return 200, {
                            "data": {
                                "id": 57,
                                "name": "gorouter",
                                "key": "sk-***masked***",
                                "status": 1 if reads == 1 else 2,
                                "weight": 0,
                            }
                        }
                    if method == "POST" and url.endswith("/status"):
                        return 200, {"success": True}
                    raise AssertionError(f"unexpected call: {method} {url}")

            with (
                patch.object(quarantine, "load_smoke", return_value=FakeSmoke),
                patch.object(
                    sys,
                    "argv",
                    ["quarantine_newapi_channels.py", "57", "--apply"],
                ),
            ):
                self.assertEqual(quarantine.main(), 0)

            self.assertFalse(
                any(
                    method == "PUT" and url.endswith("/api/channel/")
                    for method, url, _ in calls
                )
            )


class OmpContextUpdateTests(unittest.TestCase):
    def test_default_matches_official_opus5_context_window(self) -> None:
        self.assertEqual(omp_context.OFFICIAL_OPUS5_CONTEXT_WINDOW, 200_000)

    def test_finds_only_the_requested_provider_model(self) -> None:
        lines = (
            "providers:\n"
            "  zg-newapi:\n"
            "    models:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 200000\n"
            "  zg-newapi-anthropic:\n"
            "    models:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 110000\n"
        ).splitlines(keepends=True)

        self.assertEqual(
            omp_context.context_window_line(
                lines, "zg-newapi-anthropic", "claude-opus-5"
            ),
            (8, 110000),
        )

    def test_missing_or_duplicate_target_is_rejected(self) -> None:
        missing = ["providers:\n", "  zg-newapi-anthropic:\n"]
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            omp_context.context_window_line(
                missing, "zg-newapi-anthropic", "claude-opus-5"
            )

        duplicate = (
            "providers:\n"
            "  zg-newapi-anthropic:\n"
            "    - id: claude-opus-5\n"
            "      contextWindow: 110000\n"
            "      contextWindow: 110000\n"
        ).splitlines(keepends=True)
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            omp_context.context_window_line(
                duplicate, "zg-newapi-anthropic", "claude-opus-5"
            )


class MuyuanSolPrimaryTests(unittest.TestCase):
    def test_build_update_sets_target_tier_and_preserves_key(self) -> None:
        original = {
            "id": 83,
            "name": "muyuan-sol",
            "status": 1,
            "key": "opaque-secret",
            "models": "gpt-5.6-sol,zg-gpt-5.6-sol,zg-agent-gpt-5.6-sol",
            "model_mapping": '{"zg-gpt-5.6-sol":"gpt-5.6-sol"}',
            "test_model": "gpt-5.6-sol",
            "priority": 40,
            "weight": 2,
            "auto_ban": 1,
        }

        updated = muyuan_sol.build_update(original, 50, 5)

        self.assertNotIn("status", updated)
        self.assertEqual(updated["key"], "opaque-secret")
        self.assertEqual(updated["priority"], 50)
        self.assertEqual(updated["weight"], 5)
        self.assertEqual(updated["models"], original["models"])
        self.assertEqual(updated["model_mapping"], original["model_mapping"])

        demoted = muyuan_sol.build_update(
            {"id": 45, "name": "agentrouter", "key": "opaque-secret"}, 40, 5
        )
        self.assertEqual(demoted["priority"], 40)
        self.assertEqual(demoted["weight"], 5)

    def test_build_update_rejects_masked_or_missing_key(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "empty or masked"):
            muyuan_sol.build_update(
                {"id": 83, "name": "muyuan-sol", "key": "***"}, 50, 5
            )
        with self.assertRaisesRegex(RuntimeError, "missing id or name"):
            muyuan_sol.build_update({"key": "opaque-secret"}, 50, 5)

    def test_hydrate_channel_key_reads_by_channel_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            con = sqlite3.connect(db_path)
            try:
                con.execute("CREATE TABLE channels (id INTEGER PRIMARY KEY, key TEXT)")
                con.execute("INSERT INTO channels VALUES (83, 'opaque-secret')")
                con.commit()
            finally:
                con.close()

            class FakeSmoke:
                NEWAPI_DB = db_path

            hydrated = muyuan_sol.hydrate_channel_key(
                {"id": 83, "name": "muyuan-sol", "key": "sk-***"}, FakeSmoke, 83
            )

            self.assertEqual(hydrated["key"], "opaque-secret")

    def test_verify_abilities_enforces_sol_tier(self) -> None:
        ok_rows = [
            ("gpt-5.6-sol", 1, 40, 5),
            ("zg-gpt-5.6-sol", 1, 40, 5),
            ("zg-agent-gpt-5.6-sol", 1, 40, 5),
        ]
        self.assertTrue(muyuan_sol.verify_abilities(ok_rows, 40, 5))
        drifted = [("gpt-5.6-sol", 1, 51, 5)]
        self.assertFalse(muyuan_sol.verify_abilities(drifted, 40, 5))


class AnyRouterSplitTests(unittest.TestCase):
    def test_build_update_is_claude_only_and_preserves_secret(self) -> None:
        original = {
            "id": 72,
            "name": "anyrouter",
            "status": 2,
            "key": "opaque-secret",
            "models": "gpt-5.6-sol,claude-opus-5",
            "model_mapping": '{"zg-gpt-5.6-sol":"gpt-5.6-sol"}',
            "test_model": None,
            "priority": 40,
            "weight": 5,
            "auto_ban": 0,
        }

        updated = anyrouter.build_update(original, smoke)

        self.assertNotIn("status", updated)
        self.assertEqual(updated["key"], "opaque-secret")
        self.assertEqual(updated["test_model"], "claude-opus-5")
        self.assertEqual(updated["weight"], 2)
        self.assertEqual(
            set(updated["models"].split(",")),
            set(smoke.ANYROUTER_CLAUDE_MODELS),
        )
        self.assertEqual(
            json.loads(updated["model_mapping"]),
            smoke.ANYROUTER_CLAUDE_MAPPING,
        )

    def test_build_update_rejects_wrong_channel_or_masked_key(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "id 72"):
            anyrouter.build_update({"id": 73, "name": "anyrouter", "key": "x"}, smoke)
        with self.assertRaisesRegex(RuntimeError, "empty or masked"):
            anyrouter.build_update({"id": 72, "name": "anyrouter", "key": "***"}, smoke)

    def test_hydrate_channel_key_keeps_an_unmasked_key(self) -> None:
        channel = {"id": 72, "name": "anyrouter", "key": "opaque-secret"}
        self.assertIs(anyrouter.hydrate_channel_key(channel, smoke), channel)


class AnyRouterTimeoutBudgetTests(unittest.TestCase):
    def test_removes_only_the_anyrouter_slow_fallback(self) -> None:
        config = (
            "retry:\n"
            "  fallbackChains:\n"
            "    slow:\n"
            "      - zg-newapi/k3\n"
            "      - anyrouter/claude-opus-5\n"
            "    plan:\n"
            "      - anyrouter/claude-opus-5\n"
        )

        updated = anyrouter_timeout.transform_omp_config(config)

        self.assertNotIn("    slow:\n      - zg-newapi/k3\n      - anyrouter", updated)
        self.assertIn("    plan:\n      - anyrouter/claude-opus-5\n", updated)

    def test_bounds_both_proxy_timeouts_and_header(self) -> None:
        proxy = (
            "const a = { timeout: 600000, };\n"
            "const h = { 'x-stainless-timeout': '600', };\n"
            "const b = { timeout: 600000, };\n"
        )

        updated = anyrouter_timeout.transform_proxy(proxy)

        self.assertEqual(updated.count("timeout: 180000,"), 2)
        self.assertIn("'x-stainless-timeout': '180'", updated)
        self.assertNotIn("600000", updated)

    def test_proxy_transform_rejects_partial_timeout_state(self) -> None:
        proxy = (
            "const a = { timeout: 600000, };\n"
            "const h = { 'x-stainless-timeout': '180', };\n"
            "const b = { timeout: 180000, };\n"
        )

        with self.assertRaisesRegex(RuntimeError, "exactly two"):
            anyrouter_timeout.transform_proxy(proxy)

    def test_exact_text_io_preserves_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "proxy.cjs"
            path.write_bytes(b"first\r\nsecond\r\n")

            text = anyrouter_timeout.read_text_exact(path)
            anyrouter_timeout.write_atomic(path, text.replace("second", "changed"))

            self.assertEqual(path.read_bytes(), b"first\r\nchanged\r\n")


if __name__ == "__main__":
    unittest.main()
