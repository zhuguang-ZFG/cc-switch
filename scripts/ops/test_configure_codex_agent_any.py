import json
import sqlite3
import tempfile
import tomllib
import unittest
from contextlib import closing
from pathlib import Path

# This shared fixture supplies an isolated HOME and imports ops siblings.
from scripts.ops import test_codex_window_pool as _guardian_fixture
from scripts.ops import configure_codex_agent_any as configure


class CodexPoolConfigurationTests(unittest.TestCase):
    def test_pool_label_preserves_authentication_and_unrelated_settings(self):
        source = b'''model_provider = "any"\r
model = "gpt-6-astra"\r
model_reasoning_effort = "high"\r
\r
[model_providers.custom]\r
name = "Sub2API"\r
experimental_bearer_token = "fixture-old-key"\r
\r
[model_providers.any]\r
name = "Any-GPT"\r
base_url = "http://127.0.0.1:3002/v1"\r
wire_api = "responses"\r
experimental_bearer_token = "fixture-newapi-key"\r
\r
[features]\r
multi_agent = true\r
'''
        updated = configure.config_with_pool_label(source)
        self.assertEqual(updated, source.replace(b'name = "Any-GPT"', b'name = "Any / Agent GPT"'))
        data = tomllib.loads(updated.decode())
        self.assertEqual(data["model_providers"]["any"]["experimental_bearer_token"], "fixture-newapi-key")
        self.assertEqual(configure.config_with_pool_label(updated), updated)

    def test_changed_codex_route_is_not_overwritten(self):
        source = b'model_provider="custom"\nmodel="gpt-6-astra"\n'
        with self.assertRaisesRegex(ValueError, "default no longer matches"):
            configure.config_with_pool_label(source)

    def test_retry_mapping_is_channel_local_and_preserves_existing_entries(self):
        result = json.loads(configure.merge_retry_mapping('{"504":"503"}'))
        self.assertEqual(result, {"402": "503", "429": "503", "504": "503"})
        self.assertNotIn("400", result)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            configure.merge_retry_mapping('{"402":"200"}')

    def test_empty_or_masked_key_fails_before_channel_creation(self):
        for key in ("", " ", "sk-***masked***"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                configure.agent_payload(key)

    def create_database(self, path):
        agent = configure.agent_payload("fixture-agent-key")
        any_channel = {**agent, "name": "any-gpt-6-astra", "models": "gpt-6-astra", "priority": 50}
        with closing(sqlite3.connect(path)) as db:
            fields = ",".join(f'"{key}"' for key in agent)
            typed_fields = ",".join(f'"{key}" {"INTEGER" if isinstance(value, int) else "TEXT"}' for key, value in agent.items())
            db.execute(f"CREATE TABLE channels (id INTEGER PRIMARY KEY, {typed_fields})")
            placeholders = ",".join("?" for _ in agent)
            for cid, data in ((126, any_channel), (127, agent)):
                db.execute(f"INSERT INTO channels (id,{fields}) VALUES (?,{placeholders})", (cid, *data.values()))
            db.execute("CREATE TABLE abilities (channel_id INTEGER,model TEXT,enabled INTEGER,priority INTEGER,weight INTEGER)")
            db.executemany("INSERT INTO abilities VALUES (?,?,?,?,?)", [(126, "gpt-6-astra", 1, 50, 5), (127, "gpt-6-astra", 1, 40, 5), (127, "gpt-5.6-sol", 1, 40, 5)])
            db.execute("CREATE TABLE options (key TEXT, value TEXT)")
            db.executemany("INSERT INTO options VALUES (?,?)", [("RetryTimes", "1"), ("AutomaticRetryStatusCodes", "408,500-503")])
            db.commit()

    def test_projection_requires_real_enabled_abilities_and_bounded_retries(self):
        for mutation in (
            None,
            "UPDATE abilities SET enabled=0 WHERE channel_id=127",
            "UPDATE abilities SET priority=50 WHERE channel_id=127",
            "DELETE FROM abilities WHERE model='gpt-5.6-sol'",
            "UPDATE options SET value='5' WHERE key='RetryTimes'",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                if mutation:
                    with closing(sqlite3.connect(path)) as db:
                        db.execute(mutation)
                        db.commit()
                    with self.assertRaises(RuntimeError):
                        configure.verify_projection(path, 127)
                else:
                    result = configure.verify_projection(path, 127)
                    self.assertEqual(result["agent_id"], 127)
                    self.assertEqual(len(result["abilities"]), 3)


if __name__ == "__main__":
    unittest.main()
