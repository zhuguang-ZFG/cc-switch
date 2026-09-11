import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.ops import test_configure_codex_agent_any as fixture
from scripts.ops import configure_codex_sharedchat as configure


class SharedChatConfigurationTests(unittest.TestCase):
    def create_database(self, path):
        fixture.CodexPoolConfigurationTests().create_database(path)
        shared = configure.sharedchat_payload("fixture-sharedchat-key")
        with closing(sqlite3.connect(path)) as db:
            db.execute('ALTER TABLE abilities ADD COLUMN "group" TEXT DEFAULT "default"')
            columns = ",".join(f'"{key}"' for key in shared)
            placeholders = ",".join("?" for _ in shared)
            db.execute(f"INSERT INTO channels (id,{columns}) VALUES (?,{placeholders})", (128, *shared.values()))
            db.execute("UPDATE channels SET priority=40 WHERE id=126")
            db.execute("UPDATE abilities SET priority=40 WHERE channel_id=126")
            db.executemany("INSERT INTO abilities VALUES (128,?,1,60,5,'default')", [(model,) for model in sorted(configure.MODELS)])
            db.commit()

    def test_three_sources_have_only_two_reachable_tiers(self):
        mutations = (
            None,
            "UPDATE channels SET priority=50 WHERE id=126",
            "UPDATE abilities SET priority=50 WHERE channel_id=126",
            "UPDATE abilities SET enabled=0 WHERE channel_id=128",
            "INSERT INTO abilities VALUES (92,'gpt-6-astra',1,60,15,'default')",
            "UPDATE channels SET models='gpt-5.6-sol' WHERE id=128",
            "UPDATE options SET value='2' WHERE key='RetryTimes'",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                if mutation:
                    with closing(sqlite3.connect(path)) as db:
                        db.execute(mutation)
                        db.commit()
                    with self.assertRaises(RuntimeError):
                        configure.verify_projection(path, 128, 127)
                else:
                    result = configure.verify_projection(path, 128, 127)
                    self.assertEqual({row["priority"] for row in result["astra_routes"]}, {60, 40})
                    self.assertEqual(result["max_attempts_per_gateway_request"], 2)
                    self.assertEqual({row["channel_id"] for row in result["astra_routes"]}, {126, 127, 128})

    def test_sharedchat_maps_quota_locally_and_preserves_the_requested_model(self):
        data = configure.sharedchat_payload("fixture-key")
        self.assertEqual(data["models"], "gpt-6-astra,gpt-5.6-sol")
        self.assertEqual(data["model_mapping"], "")
        self.assertEqual(data["base_url"], "https://new.sharedchat.cc/codex")
        self.assertEqual(json.loads(data["status_code_mapping"]), {"402": "503", "403": "503", "429": "503"})
        self.assertNotIn("key", configure.channel_tools.safe_channel_summary(data))
        for key in ("", " ", "sk-***masked***"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                configure.sharedchat_payload(key)

    def test_sharedchat_label_does_not_change_model_or_credentials(self):
        original = b'model_provider="any"\nmodel="gpt-6-astra"\n[model_providers.any]\nname="Any / Agent GPT"\nbase_url="http://127.0.0.1:3002/v1"\nwire_api="responses"\nexperimental_bearer_token="fixture-key"\n'
        result = configure.pool.config_with_pool_label(original, configure.POOL_LABEL)
        self.assertEqual(result, original.replace(b'name="Any / Agent GPT"', b'name = "SharedChat / Any / Agent GPT"'))


if __name__ == "__main__":
    unittest.main()
