"""Focused tests for Sol routing hardening helpers."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


OPS_DIR = Path(__file__).parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPS_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fallback = load("sol_fallback_editor_tests", "remove_omp_default_fallback.py")
dead_fallback = load("dead_fallback_editor_tests", "remove_omp_dead_fallback.py")
verifier = load("sol_verifier_tests", "verify_zzzcoding_sol_primary.py")
monitor = load("sol_monitor_tests", "monitor_sol_semantic.py")
campaign = load("sol_context_campaign_tests", "campaign_sol_context.py")
rollout = load("sol_agentrouter_rollout_tests", "rollout_agentrouter_sol.py")
posture = load("sol_posture_rollback_tests", "update_zzzcoding_sol_primary.py")
drill = load("sol_failover_drill_tests", "drill_sol_failover.py")


class OmpFallbackEditorTests(unittest.TestCase):
    def fixture(self, newline: str = "\n") -> str:
        return newline.join(
            (
                "modelRoles:",
                "  default: zg-newapi/k3:high",
                "  plan: zg-newapi/claude-opus-5:high",
                "retry:",
                "  modelFallback: true",
                "  fallbackChains:",
                "    zg-newapi/k3:",
                "      - zg-newapi/deepseek-v4-flash:high",
                "    plan:",
                "      - zg-newapi/k3:high",
                "disabledProviders:",
                "  - retired",
                "",
            )
        )

    def test_removes_only_exact_default_chain_and_preserves_crlf(self):
        original = self.fixture("\r\n")
        updated, selector, changed = fallback.remove_exact_default_chain(original)
        self.assertTrue(changed)
        self.assertEqual(selector, "zg-newapi/k3")
        self.assertNotIn("    zg-newapi/k3:\r\n", updated)
        self.assertIn("    plan:\r\n      - zg-newapi/k3:high\r\n", updated)
        self.assertNotIn("\n", updated.replace("\r\n", ""))

    def test_is_idempotent_and_rejects_selector_drift(self):
        updated, _, _ = fallback.remove_exact_default_chain(self.fixture())
        self.assertEqual(
            fallback.remove_exact_default_chain(updated),
            (updated, "zg-newapi/k3", False),
        )
        with self.assertRaisesRegex(RuntimeError, "modelRoles.default"):
            fallback.remove_exact_default_chain(
                self.fixture().replace("zg-newapi/k3:high", "zg-newapi/other:high", 1)
            )


class DeadFallbackEditorTests(unittest.TestCase):
    def fixture(self) -> str:
        return (
            "retry:\n"
            "  fallbackChains:\n"
            "    zg-newapi/agnes-2.5-flash:\n"
            "      - zg-newapi/agnes-2.0-flash\n"
            "      - zg-newapi/sensenova-6.7-flash-lite\n"
            "    vision:\n"
            "      - zg-newapi/agnes-2.5-flash\n"
        )

    def test_removes_only_dead_candidate_and_is_idempotent(self):
        updated, changed = dead_fallback.remove_exact_candidate(self.fixture())
        self.assertTrue(changed)
        self.assertNotIn("      - zg-newapi/agnes-2.0-flash\n", updated)
        self.assertIn("      - zg-newapi/sensenova-6.7-flash-lite\n", updated)
        self.assertIn("    vision:\n      - zg-newapi/agnes-2.5-flash\n", updated)
        self.assertEqual(dead_fallback.remove_exact_candidate(updated), (updated, False))

    def test_refuses_to_empty_or_guess_missing_chain(self):
        only_dead = self.fixture().replace(
            "      - zg-newapi/sensenova-6.7-flash-lite\n", ""
        )
        with self.assertRaisesRegex(RuntimeError, "refusing to empty"):
            dead_fallback.remove_exact_candidate(only_dead)
        with self.assertRaisesRegex(RuntimeError, "chain not found"):
            dead_fallback.remove_exact_candidate("retry:\n  fallbackChains:\n")

    def test_refuses_fallback_mapping_outside_retry(self):
        misplaced = self.fixture().replace("retry:\n", "other:\n")
        with self.assertRaisesRegex(RuntimeError, "retry mapping not found"):
            dead_fallback.remove_exact_candidate(misplaced)


class IncrementalSseTests(unittest.TestCase):
    def test_metrics_measure_semantic_ttft_gap_and_usage(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"SOL-"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"OK"}}]}\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2}}\n',
            b"data: [DONE]\n",
        ]
        with patch.object(
            verifier.time, "monotonic", side_effect=(1.0, 2.0, 2.1, 2.2, 3.0)
        ):
            metrics = verifier.read_incremental_sse(
                lines, status=200, started=0.0, header_ms=250
            )
        self.assertEqual(metrics.text, "SOL-OK")
        self.assertEqual(metrics.ttft_ms, 1000)
        self.assertEqual(metrics.max_semantic_gap_ms, 1000)
        self.assertEqual(metrics.total_ms, 3000)
        self.assertEqual((metrics.prompt_tokens, metrics.completion_tokens), (11, 2))
        self.assertTrue(metrics.done)

    def test_responses_usage_shape_is_supported(self):
        fragment = verifier.parse_sse_data(
            '{"type":"response.output_text.delta","delta":"OK"}'
        )
        usage = verifier.parse_sse_data(
            '{"type":"response.completed","response":{"usage":'
            '{"input_tokens":9,"output_tokens":1}}}'
        )
        self.assertEqual(fragment, ("OK", False, 0, 0))
        self.assertEqual(usage, ("", False, 9, 1))


class MonitorStateTests(unittest.TestCase):
    def test_alerts_only_on_second_consecutive_failure_and_resets(self):
        first, first_alert = monitor.next_failure_state(
            0, ok=False, timestamp="t1"
        )
        second, second_alert = monitor.next_failure_state(
            first["consecutive_failures"], ok=False, timestamp="t2"
        )
        reset, reset_alert = monitor.next_failure_state(
            second["consecutive_failures"], ok=True, timestamp="t3"
        )
        self.assertEqual(first["consecutive_failures"], 1)
        self.assertFalse(first_alert)
        self.assertEqual(second["consecutive_failures"], 2)
        self.assertTrue(second_alert)
        self.assertEqual(reset["consecutive_failures"], 0)
        self.assertFalse(reset_alert)

    def test_corrupt_state_is_explicit_and_does_not_leak_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text("secret-not-json", encoding="utf-8")
            self.assertEqual(monitor.read_failure_count(path), (0, True))


class MonitorClassificationTests(unittest.TestCase):
    def test_primary_success_is_ok(self):
        self.assertEqual(
            monitor.classify_probe_result(
                status=200,
                semantic_ok=True,
                done=True,
                prompt_tokens=10,
                completion_tokens=2,
                channel_id=92,
                expected_channel_id=92,
            ),
            ("primary", None),
        )

    def test_non_primary_success_is_actionable_route_failure(self):
        self.assertEqual(
            monitor.classify_probe_result(
                status=200,
                semantic_ok=True,
                done=True,
                prompt_tokens=10,
                completion_tokens=2,
                channel_id=45,
                expected_channel_id=92,
            ),
            ("non_primary", "primary_not_attributed"),
        )

    def test_missing_usage_and_attribution_have_stable_categories(self):
        self.assertEqual(
            monitor.classify_probe_result(
                status=200,
                semantic_ok=True,
                done=True,
                prompt_tokens=0,
                completion_tokens=2,
                channel_id=None,
                expected_channel_id=92,
            ),
            ("unattributed", "usage_missing"),
        )


class ContextCampaignTests(unittest.TestCase):
    def test_reproducible_rejection_binary_searches_then_tests_two_tool_points(self):
        calls: list[tuple[int, str]] = []
        records: list[dict] = []

        def probe(target: int, shape: str) -> dict:
            calls.append((target, shape))
            success = shape == "tool" or target <= 240_000
            return {
                "phase": shape,
                "success": success,
                "category": "ok" if success else "context_limit",
                "prompt_tokens": target if success else 0,
            }

        summary = campaign.run_channel_campaign(probe, records.append)
        self.assertEqual(summary["status"], "bounded")
        self.assertEqual(
            summary["boundary"], {"last_success": 240_000, "first_failure": 250_000}
        )
        self.assertEqual(summary["tool_status"], "complete")
        self.assertIn((280_000, "plain-recheck"), calls)
        self.assertEqual(calls[-2:], [(208_000, "tool"), (240_000, "tool")])

    def test_gateway_failure_is_inconclusive_and_skips_tools(self):
        calls: list[tuple[int, str]] = []

        def probe(target: int, shape: str) -> dict:
            calls.append((target, shape))
            return {
                "phase": shape,
                "success": False,
                "category": "upstream_or_gateway",
                "prompt_tokens": 0,
            }

        summary = campaign.run_channel_campaign(probe, lambda _: None)
        self.assertEqual(summary["status"], "inconclusive")
        self.assertEqual(summary["tool_status"], "skipped")
        self.assertEqual(calls, [(200_000, "plain")])

    def test_error_classification_and_tool_targets(self):
        self.assertEqual(
            campaign.classify_http_error(400, "maximum context length exceeded"),
            "context_limit",
        )
        self.assertEqual(campaign.classify_http_error(503, "overloaded"), "upstream_or_gateway")
        self.assertEqual(campaign.tool_targets(396_000), (364_000, 396_000))
        self.assertEqual(
            len(campaign.filler_prompt(10, "OK").rsplit("FILLER:\n", 1)[1].split()),
            10,
        )


class RegistrarContractTests(unittest.TestCase):
    def test_scheduled_task_is_bounded_single_instance_and_no_restart(self):
        source = (OPS_DIR / "register-sol-semantic-monitor-task.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 30)", source)
        self.assertIn("-ExecutionTimeLimit (New-TimeSpan -Minutes 5)", source)
        self.assertIn("-MultipleInstances IgnoreNew", source)
        self.assertNotIn("-RestartCount", source)
        self.assertIn("Get-FileHash -Algorithm SHA256", source)
        self.assertIn("if (-not $lastResult.ok", source)


class AgentRouterRolloutTests(unittest.TestCase):
    def test_read_channel_rejects_failure_domain_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "new-api.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE channels (id INTEGER, name TEXT, status INTEGER, "
                "priority INTEGER, weight INTEGER, channel_info BLOB, base_url TEXT)"
            )
            connection.execute(
                "CREATE TABLE abilities (channel_id INTEGER, model TEXT, enabled INTEGER, "
                "priority INTEGER, weight INTEGER)"
            )
            connection.execute(
                "INSERT INTO channels VALUES (45, 'agentrouter', 2, 40, 5, ?, ?)",
                (b"{}", "https://unexpected.example"),
            )
            connection.execute(
                "INSERT INTO abilities VALUES (45, 'gpt-5.6-sol', 0, 40, 5)"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(RuntimeError, "base_url"):
                rollout.read_channel(path)


class FailoverRollbackTests(unittest.TestCase):
    def test_restore_baseline_recovers_status_channels_and_abilities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "new-api.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, "
                "status INTEGER, priority INTEGER, weight INTEGER, channel_info BLOB)"
            )
            connection.execute(
                "CREATE TABLE abilities (`group` TEXT, model TEXT, channel_id INTEGER, "
                "enabled INTEGER, priority INTEGER, weight INTEGER)"
            )
            for channel_id, name, priority, weight, _ in posture.TARGETS:
                connection.execute(
                    "INSERT INTO channels VALUES (?, ?, 1, ?, ?, ?)",
                    (channel_id, name, priority, weight, b"{}"),
                )
                for model in posture.EXPECTED_MODELS[channel_id]:
                    connection.execute(
                        "INSERT INTO abilities VALUES ('default', ?, ?, 1, ?, ?)",
                        (model, channel_id, priority, weight),
                    )
            connection.commit()
            original = posture.read_state(connection)
            connection.execute("UPDATE channels SET priority=1, weight=1")
            connection.execute("UPDATE channels SET status=2 WHERE id=92")
            connection.execute("UPDATE abilities SET priority=1, weight=1")
            connection.commit()
            connection.close()

            class FakeSmoke:
                NEWAPI_BASE = "http://local.invalid"

                @staticmethod
                def http_json(_url, *, body, **_kwargs):
                    db = sqlite3.connect(path)
                    db.execute("UPDATE channels SET status=? WHERE id=92", (body["status"],))
                    db.commit()
                    db.close()
                    return 200, {"success": True}

            with patch.object(drill.time, "sleep"):
                drill.restore_baseline(
                    posture, FakeSmoke, {}, path, original, 1, cache_wait=75
                )
            connection = sqlite3.connect(path)
            self.assertEqual(posture.read_state(connection), original)
            connection.close()


if __name__ == "__main__":
    unittest.main()
