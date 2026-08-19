"""Unit tests for bounded NewAPI/OMP update helpers."""
from __future__ import annotations

import importlib.util
import json
import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
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
fix_jianzhile = load(
    "fix_jianzhile_codex_channel", "fix_jianzhile_codex_channel.py"
)
zzzcoding_sol = load(
    "add_zzzcoding_sol_channel_for_tests", "add_zzzcoding_sol_channel.py"
)
zzzcoding_posture = load(
    "update_zzzcoding_sol_primary_for_tests", "update_zzzcoding_sol_primary.py"
)
zzzcoding_verify = load(
    "verify_zzzcoding_sol_primary_for_tests", "verify_zzzcoding_sol_primary.py"
)
quarantine = load(
    "quarantine_newapi_channels_for_tests", "quarantine_newapi_channels.py"
)
muse_posture = load(
    "repair_muse_channel_posture_for_tests", "repair_muse_channel_posture.py"
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
                def http_json(url, **kwargs):
                    method = kwargs.get("method", "GET")
                    calls.append((method, url, kwargs.get("body")))
                    if method == "POST" and url.endswith("/status"):
                        return 200, {"success": True}
                    raise AssertionError(f"unexpected call: {method} {url}")

            quarantine.set_channel_posture(
                FakeSmoke,
                {},
                {
                    "id": 57,
                    "name": "gorouter",
                    "key": "sk-***masked***",
                    "status": 1,
                    "weight": 0,
                },
                Path(temp_dir) / "missing.db",
                2,
                0,
            )

            self.assertFalse(
                any(
                    method == "PUT" and url.endswith("/api/channel/")
                    for method, url, _ in calls
                )
            )

    def test_key_hydration_failure_happens_before_status_mutation(self) -> None:
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

            calls: list[str] = []

            class FakeSmoke:
                NEWAPI_BASE = "http://127.0.0.1:3002"

                @staticmethod
                def http_json(url, **kwargs):
                    calls.append(kwargs.get("method", "GET"))
                    return 200, {"success": True}

            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                quarantine.set_channel_posture(
                    FakeSmoke,
                    {},
                    {
                        "id": 57,
                        "name": "gorouter",
                        "key": "sk-***masked***",
                        "status": 1,
                        "weight": 5,
                    },
                    database,
                    2,
                    0,
                )
            self.assertEqual(calls, [])

    def test_restore_enabled_puts_weight_before_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "new-api.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'gorouter', 'fixture-real-key')"
                )
                connection.commit()

            calls: list[str] = []

            class FakeSmoke:
                NEWAPI_BASE = "http://127.0.0.1:3002"

                @staticmethod
                def http_json(url, **kwargs):
                    calls.append(kwargs.get("method", "GET"))
                    return 200, {"success": True}

            quarantine.set_channel_posture(
                FakeSmoke,
                {},
                {
                    "id": 57,
                    "name": "gorouter",
                    "key": "sk-***masked***",
                    "status": 2,
                    "weight": 0,
                },
                database,
                1,
                5,
            )
            self.assertEqual(calls, ["PUT", "POST"])

    def test_nonzero_weight_put_uses_unmasked_ssot_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, key TEXT)"
                )
                connection.execute(
                    "INSERT INTO channels VALUES (57, 'gorouter', 'fixture-real-key')"
                )
                connection.commit()
            finally:
                connection.close()
            calls: list[tuple[str, str, dict | None]] = []

            class FakeSmoke:
                NEWAPI_BASE = "http://127.0.0.1:3002"

                @staticmethod
                def http_json(url, **kwargs):
                    method = kwargs.get("method", "GET")
                    body = kwargs.get("body")
                    calls.append((method, url, body))
                    if method == "POST" and url.endswith("/status"):
                        return 200, {"success": True}
                    if method == "PUT" and url.endswith("/api/channel/"):
                        return 200, {"success": True}
                    raise AssertionError(f"unexpected call: {method} {url}")

            quarantine.set_channel_posture(
                FakeSmoke,
                {},
                {
                    "id": 57,
                    "name": "gorouter",
                    "key": "sk-***masked***",
                    "status": 1,
                    "weight": 5,
                },
                database,
                2,
                0,
            )

            put = next(body for method, url, body in calls if method == "PUT")
            self.assertEqual(put["key"], "fixture-real-key")
            self.assertEqual(put["weight"], 0)

    def test_key_hydration_rejects_reused_channel_id(self) -> None:
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

            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                quarantine.hydrate_key(
                    {"id": 57, "name": "gorouter", "key": "unmasked-key"},
                    database,
                )

    def test_key_hydration_rejects_unmasked_key_drift(self) -> None:
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

            with self.assertRaisesRegex(RuntimeError, "key mismatch"):
                quarantine.hydrate_key(
                    {"id": 57, "name": "gorouter", "key": "stale-key"},
                    database,
                )

    def test_single_asterisk_key_is_treated_as_masked(self) -> None:
        self.assertFalse(quarantine.usable_key("sk-*masked"))

    def test_quarantine_readback_requires_disabled_abilities(self) -> None:
        class FakeSmoke:
            NEWAPI_BASE = "http://127.0.0.1:3002"

            @staticmethod
            def http_json(_url, **_kwargs):
                return 200, {"data": {"status": 2, "weight": 0}}

        with patch.object(
            quarantine,
            "read_ability_posture",
            return_value=[("gpt-5.6-sol", 1, 50, 0)],
        ):
            self.assertFalse(
                quarantine.verify_quarantined(
                    FakeSmoke, {}, Path("missing.db"), 57
                )
            )

    def test_safe_backup_summary_excludes_secret_fields(self) -> None:
        summary = quarantine.safe_channel_summary(
            {
                "id": 57,
                "name": "gorouter",
                "key": "fixture-secret",
                "channel_info": b"secret-blob",
                "settings": "secret-settings",
                "weight": 5,
            }
        )
        self.assertEqual(summary, {"id": 57, "name": "gorouter", "weight": 5})


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


class ZzzcodingSolChannelTests(unittest.TestCase):
    def test_channel_payload_is_disabled_until_responses_probe_passes(self) -> None:
        payload = zzzcoding_sol.channel_payload("opaque-secret")

        self.assertEqual(payload["status"], 2)
        self.assertEqual(payload["auto_ban"], 1)
        self.assertEqual(payload["priority"], 60)
        self.assertEqual(payload["weight"], 15)
        self.assertEqual(payload["test_model"], zzzcoding_sol.CODEX_MODEL)
        self.assertEqual(
            json.loads(payload["model_mapping"]), zzzcoding_sol.MODEL_MAPPING
        )
        self.assertIs(
            json.loads(payload["param_override"])["parallel_tool_calls"], False
        )

    def test_existing_channel_refresh_preserves_key_and_forces_probe_shape(self) -> None:
        original = {
            "id": 92,
            "name": zzzcoding_sol.CHANNEL_NAME,
            "status": 2,
            "key": "opaque-secret",
            "priority": 10,
            "weight": 1,
        }

        updated = zzzcoding_sol.desired_existing_channel(original)

        self.assertNotIn("status", updated)
        self.assertEqual(updated["key"], "opaque-secret")
        self.assertEqual(updated["priority"], 60)
        self.assertIs(
            json.loads(updated["param_override"])["parallel_tool_calls"], False
        )

    def test_update_peer_preserves_channel_info_and_updates_abilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, "
                    "priority INTEGER, weight INTEGER, channel_info BLOB)"
                )
                connection.execute(
                    "CREATE TABLE abilities (channel_id INTEGER, priority INTEGER, "
                    "weight INTEGER)"
                )
                marker = b'{"is_multi_key":true}'
                connection.execute(
                    "INSERT INTO channels VALUES (91, ?, 26, 15, ?)",
                    (zzzcoding_sol.PEER_NAME, marker),
                )
                connection.execute("INSERT INTO abilities VALUES (91, 26, 15)")
                connection.commit()

                original = zzzcoding_sol.update_peer_posture(connection, 50, 5)
                row = connection.execute(
                    "SELECT priority, weight, channel_info FROM channels WHERE id=91"
                ).fetchone()
                ability = connection.execute(
                    "SELECT priority, weight FROM abilities WHERE channel_id=91"
                ).fetchone()

            self.assertEqual(original, (26, 15))
            self.assertEqual(row, (50, 5, marker))
            self.assertEqual(ability, (50, 5))

    def test_online_backup_is_integrity_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE marker (value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('ok')")
                connection.commit()

            backup = zzzcoding_sol.online_backup(db_path)
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone(), ("ok",)
                )
                self.assertEqual(
                    connection.execute("SELECT value FROM marker").fetchone(), ("ok",)
                )


class ZzzcodingSolPrimaryPostureTests(unittest.TestCase):
    def make_database(self, path: Path) -> bytes:
        marker = b'{"is_multi_key":true}'
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, "
                "status INTEGER, priority INTEGER, weight INTEGER, channel_info BLOB)"
            )
            connection.execute(
                "CREATE TABLE abilities (`group` TEXT, model TEXT, channel_id INTEGER, "
                "enabled INTEGER, priority INTEGER, weight INTEGER)"
            )
            for channel_id, name, priority, weight, _ in zzzcoding_posture.TARGETS:
                old_priority = {92: 50, 91: 60, 83: 40}[channel_id]
                old_weight = {92: 5, 91: 15, 83: 2}[channel_id]
                status = 2 if channel_id == 83 else 1
                connection.execute(
                    "INSERT INTO channels VALUES (?, ?, ?, ?, ?, ?)",
                    (channel_id, name, status, old_priority, old_weight, marker),
                )
                for model in zzzcoding_posture.EXPECTED_MODELS[channel_id]:
                    connection.execute(
                        "INSERT INTO abilities VALUES ('default', ?, ?, 1, ?, ?)",
                        (model, channel_id, old_priority, old_weight),
                    )
            connection.commit()
        return marker

    def test_write_and_restore_are_bounded_and_preserve_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            marker = self.make_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                original = zzzcoding_posture.read_state(connection)
                zzzcoding_posture.write_targets(connection)
                zzzcoding_posture.verify_targets(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT channel_info FROM channels WHERE id=92"
                    ).fetchone(),
                    (marker,),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM channels WHERE id=83"
                    ).fetchone(),
                    (2,),
                )
                zzzcoding_posture.restore_state(connection, original)
                self.assertEqual(zzzcoding_posture.read_state(connection), original)

    def test_read_state_rejects_wrong_channel_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            self.make_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("UPDATE channels SET name='wrong' WHERE id=92")
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "expected"):
                    zzzcoding_posture.read_state(connection)

    def test_preflight_allows_only_disabled_channel_probe_failures(self) -> None:
        state = {
            "channels": {
                92: ("zzzcoding", 1, 60, 15, "blob"),
                91: ("jianzhile", 2, 55, 5, "blob"),
            }
        }

        def probe(_smoke, _headers, channel_id):
            if channel_id == 91:
                raise RuntimeError("fixture upstream failure")

        with patch.object(zzzcoding_posture, "management_probe", side_effect=probe):
            failures = zzzcoding_posture.preflight_management_probes(
                object(), {}, state, allow_disabled_probe_failures=True
            )

        self.assertEqual(len(failures[91]), 2)
        self.assertNotIn(92, failures)

    def test_preflight_never_waives_enabled_channel_failure(self) -> None:
        state = {
            "channels": {
                92: ("zzzcoding", 1, 60, 15, "blob"),
                91: ("jianzhile", 2, 55, 5, "blob"),
            }
        }

        with patch.object(
            zzzcoding_posture,
            "management_probe",
            side_effect=RuntimeError("fixture upstream failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "ch92 failed 2/2"):
                zzzcoding_posture.preflight_management_probes(
                    object(), {}, state, allow_disabled_probe_failures=True
                )

    def test_chat_sse_parser_requires_semantic_text_done_and_usage(self) -> None:
        payload = (
            b'data: {"choices":[{"delta":{"content":"CH92-"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"PRIMARY-OK"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":9,'
            b'"completion_tokens":4}}\n\n'
            b'data: [DONE]\n\n'
        )
        self.assertEqual(
            zzzcoding_verify.parse_chat_sse(payload),
            ("CH92-PRIMARY-OK", True, 9, 4),
        )


class MuseChannelPostureTests(unittest.TestCase):
    @staticmethod
    def make_database(path: Path) -> bytes:
        marker = b'{"is_multi_key":false}'
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE channels (id INTEGER PRIMARY KEY, name TEXT, "
                "status INTEGER, type INTEGER, priority INTEGER, weight INTEGER, "
                "channel_info BLOB, base_url TEXT, models TEXT)"
            )
            connection.execute(
                "CREATE TABLE abilities (`group` TEXT, model TEXT, channel_id INTEGER, "
                "enabled INTEGER, priority INTEGER, weight INTEGER)"
            )
            connection.execute(
                "INSERT INTO channels VALUES (?, ?, 1, 1, 51, 13, ?, ?, ?)",
                (
                    muse_posture.CHANNEL_ID,
                    muse_posture.CHANNEL_NAME,
                    marker,
                    muse_posture.CHANNEL_BASE_URL,
                    muse_posture.MODEL,
                ),
            )
            connection.execute(
                "INSERT INTO abilities VALUES ('default', ?, ?, 1, 51, 13)",
                (muse_posture.MODEL, muse_posture.CHANNEL_ID),
            )
            connection.commit()
        return marker

    def test_write_and_restore_preserve_identity_status_and_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            marker = self.make_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                original = muse_posture.read_state(connection)
                muse_posture.write_target(connection)
                muse_posture.verify_target(connection, original)
                self.assertEqual(
                    connection.execute(
                        "SELECT status, channel_info FROM channels WHERE id=48"
                    ).fetchone(),
                    (1, marker),
                )
                muse_posture.restore_state(connection, original)
                self.assertEqual(muse_posture.read_state(connection), original)

    def test_read_state_rejects_channel_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "new-api.db"
            self.make_database(db_path)
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("UPDATE channels SET name='replacement' WHERE id=48")
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "expected"):
                    muse_posture.read_state(connection)

    def test_response_text_reads_nested_responses_content(self) -> None:
        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "O"},
                        {"type": "output_text", "text": "K"},
                    ],
                }
            ]
        }
        self.assertEqual(muse_posture.response_text(payload), "OK")

    def test_aggregate_probe_requires_semantics_usage_and_channel_attribution(self) -> None:
        class FakeSmoke:
            NEWAPI_BASE = "http://local.invalid"
            request_body = None

            @staticmethod
            def http_json(*_args, **_kwargs):
                FakeSmoke.request_body = _kwargs.get("body")
                return 200, {
                    "output_text": "OK",
                    "usage": {"input_tokens": 4, "output_tokens": 1},
                }

        with patch.object(muse_posture, "latest_log_id", return_value=10), patch.object(
            muse_posture, "require_log_attribution"
        ) as attribution:
            muse_posture.aggregate_probe(FakeSmoke, Path("fixture.db"), "token")

        attribution.assert_called_once_with(Path("fixture.db"), 10)
        self.assertEqual(
            FakeSmoke.request_body["max_output_tokens"],
            muse_posture.SEMANTIC_MAX_OUTPUT_TOKENS,
        )

    def test_aggregate_probe_rejects_reasoning_only_incomplete_response(self) -> None:
        class FakeSmoke:
            NEWAPI_BASE = "http://local.invalid"

            @staticmethod
            def http_json(*_args, **_kwargs):
                return 200, {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output_text": "OK",
                    "usage": {"input_tokens": 4, "output_tokens": 128},
                }

        with patch.object(muse_posture, "latest_log_id", return_value=10):
            with self.assertRaisesRegex(
                RuntimeError, "returned incomplete output"
            ):
                muse_posture.aggregate_probe(
                    FakeSmoke, Path("fixture.db"), "token"
                )


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
