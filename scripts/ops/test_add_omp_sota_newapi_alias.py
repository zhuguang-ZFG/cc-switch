from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("add_omp_sota_newapi_alias.py")
SPEC = importlib.util.spec_from_file_location("add_omp_sota_newapi_alias", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
sota = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sota)


def channel_fixture(**overrides):
    channel = {
        "id": 75,
        "name": "omp-sota-sotamodel",
        "status": 1,
        "key": "fixture-secret-never-print",
        "models": "claude-opus-5,claude-sonnet-4-8",
        "model_mapping": '{"keep":"claude-sonnet-4-8"}',
        "priority": 50,
        "weight": 8,
    }
    channel.update(overrides)
    return channel


class OmpSotaNewApiAliasTests(unittest.TestCase):
    def test_refuses_alias_on_non_dedicated_channel(self):
        # strict isolation (2026-08-20): adding to a shared pool is refused
        with self.assertRaisesRegex(ValueError, "strict isolation"):
            sota.plan_channel_update(
                channel_fixture(name="tabitoken"), 75, "claude-opus-5"
            )
        # removal from a shared channel stays allowed (drift cleanup path)
        alias = "omp-sota-claude-opus-5"
        drifted = channel_fixture(
            name="tabitoken",
            models=f"claude-opus-5,{alias}",
            model_mapping=json.dumps({alias: "claude-opus-5"}),
        )
        _, _, changed = sota.plan_channel_update(
            drifted, 75, "claude-opus-5", remove=True
        )
        self.assertTrue(changed)

    def test_plans_exact_marker_and_preserves_existing_mapping(self):
        updated, alias, changed = sota.plan_channel_update(
            channel_fixture(), 75, "claude-opus-5"
        )
        self.assertTrue(changed)
        self.assertEqual(alias, "omp-sota-claude-opus-5")
        self.assertNotIn("status", updated)
        self.assertIn(alias, updated["models"].split(","))
        mapping = json.loads(updated["model_mapping"])
        self.assertEqual(mapping["keep"], "claude-sonnet-4-8")
        self.assertEqual(mapping[alias], "claude-opus-5")
        self.assertEqual(updated["key"], "fixture-secret-never-print")

    def test_is_idempotent_and_rejects_conflicts_or_missing_base(self):
        alias = "omp-sota-claude-opus-5"
        ready = channel_fixture(
            models=f"claude-opus-5,{alias}",
            model_mapping=json.dumps({alias: "claude-opus-5"}),
        )
        _, _, changed = sota.plan_channel_update(ready, 75, "claude-opus-5")
        self.assertFalse(changed)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            sota.plan_channel_update(
                channel_fixture(model_mapping=json.dumps({alias: "other"})),
                75,
                "claude-opus-5",
            )
        with self.assertRaisesRegex(ValueError, "does not expose base"):
            sota.plan_channel_update(
                channel_fixture(models="claude-sonnet-4-8"), 75, "claude-opus-5"
            )

    def test_plans_symmetric_removal(self):
        alias = "omp-sota-claude-opus-5"
        ready = channel_fixture(
            models=f"claude-opus-5,{alias}",
            model_mapping=json.dumps({alias: "claude-opus-5"}),
        )
        updated, _, changed = sota.plan_channel_update(
            ready, 75, "claude-opus-5", remove=True
        )
        self.assertTrue(changed)
        self.assertNotIn(alias, updated["models"].split(","))
        self.assertNotIn(alias, json.loads(updated["model_mapping"]))
        readback = {**updated, "status": 1}
        self.assertTrue(
            sota.verify_projection(
                readback, [], 75, "claude-opus-5", alias, present=False
            )
        )

    def test_requires_enabled_channel_real_key_and_exact_alias(self):
        with self.assertRaisesRegex(ValueError, "enabled"):
            sota.plan_channel_update(channel_fixture(status=2), 75, "claude-opus-5")
        with self.assertRaisesRegex(ValueError, "masked"):
            sota.plan_channel_update(channel_fixture(key="sk-***"), 75, "claude-opus-5")
        with self.assertRaisesRegex(ValueError, "exactly"):
            sota.build_alias("claude-opus-5", "omp-sota-other")

    def test_fake_api_success_and_failed_readback_rollback(self):
        original = channel_fixture()
        updated, alias, _ = sota.plan_channel_update(original, 75, "claude-opus-5")
        readback = {**updated, "status": 1}
        rows = [(alias, 1, 50, 8)]
        puts = []

        success = sota.apply_and_verify(
            original,
            updated,
            channel_id=75,
            base_model="claude-opus-5",
            alias=alias,
            put_channel=lambda payload: puts.append(payload) is None,
            read_channel=lambda: readback,
            read_ability_rows=lambda: rows,
        )
        self.assertTrue(success["verified"])
        self.assertEqual(len(puts), 1)

        puts.clear()
        failed = sota.apply_and_verify(
            original,
            updated,
            channel_id=75,
            base_model="claude-opus-5",
            alias=alias,
            put_channel=lambda payload: puts.append(payload) is None,
            read_channel=lambda: readback,
            read_ability_rows=lambda: [],
        )
        self.assertFalse(failed["verified"])
        self.assertTrue(failed["rollbackAttempted"])
        self.assertTrue(failed["restored"])
        self.assertEqual(puts[-1]["models"], original["models"])
        self.assertNotIn("status", puts[-1])

    def test_sqlite_backup_and_ability_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE abilities "
                    "(model TEXT, channel_id INTEGER, enabled INTEGER, priority INTEGER, weight INTEGER)"
                )
                connection.execute(
                    "INSERT INTO abilities VALUES (?, ?, ?, ?, ?)",
                    ("omp-sota-claude-opus-5", 75, 1, 50, 8),
                )
                connection.commit()
            finally:
                connection.close()

            backup = sota.online_backup(database, root / "backups", "fixture")
            self.assertGreater(backup.stat().st_size, 0)
            rows = sota.read_abilities(database, 75)
            self.assertEqual(rows, [("omp-sota-claude-opus-5", 1, 50, 8)])
            readback = channel_fixture(
                models="claude-opus-5,omp-sota-claude-opus-5",
                model_mapping='{"omp-sota-claude-opus-5":"claude-opus-5"}',
            )
            self.assertTrue(
                sota.verify_projection(
                    readback,
                    rows,
                    75,
                    "claude-opus-5",
                    "omp-sota-claude-opus-5",
                )
            )


    def test_multi_key_detection_reads_channel_info_blob(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "new-api.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE channels (id INTEGER, channel_info TEXT)")
                connection.execute(
                    "INSERT INTO channels VALUES (75, ?)",
                    (json.dumps({"is_multi_key": True, "multi_key_size": 3}),),
                )
                connection.execute(
                    "INSERT INTO channels VALUES (93, ?)",
                    (json.dumps({"is_multi_key": False}),),
                )
                connection.execute("INSERT INTO channels VALUES (99, NULL)")
                connection.commit()
            finally:
                connection.close()
            self.assertTrue(sota.is_multi_key_channel(database, 75))
            self.assertFalse(sota.is_multi_key_channel(database, 93))
            self.assertFalse(sota.is_multi_key_channel(database, 99))
            self.assertFalse(sota.is_multi_key_channel(database, 1234))


if __name__ == "__main__":
    unittest.main()
