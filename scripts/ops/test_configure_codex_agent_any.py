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
                configure.agent_payload([key])

    def create_database(self, path):
        agent = {**configure.agent_payload(["fixture-agent-key"]), "channel_info": None}
        any_channel = {**agent, "name": "any-gpt-6-astra", "models": "gpt-6-astra", "priority": 50}
        with closing(sqlite3.connect(path)) as db:
            fields = ",".join(f'"{key}"' for key in agent)
            typed_fields = ",".join(f'"{key}" {"INTEGER" if isinstance(value, int) else "TEXT"}' for key, value in agent.items())
            db.execute(f"CREATE TABLE channels (id INTEGER PRIMARY KEY, {typed_fields})")
            placeholders = ",".join("?" for _ in agent)
            for cid, data in ((126, any_channel), (127, agent)):
                db.execute(f"INSERT INTO channels (id,{fields}) VALUES (?,{placeholders})", (cid, *data.values()))
            db.execute('CREATE TABLE abilities (channel_id INTEGER,model TEXT,"group" TEXT,enabled INTEGER,priority INTEGER,weight INTEGER)')
            db.executemany('INSERT INTO abilities VALUES (?,?,?,?,?,?)', [(126, "gpt-6-astra", "default", 1, 50, 5), (127, "gpt-6-astra", "default", 1, 40, 5), (127, "gpt-5.6-sol", "default", 1, 40, 5)])
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


    def create_live_shaped_database(self, path):
        """ch127 as it actually reads on 2026-10-11: a third name and the agent group.

        deepseek-v4-flash carries real traffic (11/13/18 requests on 10-09/10-10/10-11) and the
        consumers of this pool reach it through the agent group. Both are operator additions, not
        projection drift, and asserting equality on either would demand a capacity regression.
        """
        self.create_database(path)
        with closing(sqlite3.connect(path)) as db:
            db.execute('UPDATE channels SET models=\'deepseek-v4-flash,gpt-6-astra,gpt-5.6-sol\', '
                       '"group"=\'default,agent\' WHERE id=127')
            db.executemany('INSERT INTO abilities VALUES (?,?,?,?,?,?)', [
                (127, "deepseek-v4-flash", "default", 1, 40, 5),
                (127, "deepseek-v4-flash", "agent", 1, 40, 5),
                (127, "gpt-6-astra", "agent", 1, 40, 5),
                (127, "gpt-5.6-sol", "agent", 1, 40, 5),
            ])
            db.commit()

    def test_operator_additions_are_not_projection_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.db"
            self.create_live_shaped_database(path)
            result = configure.verify_projection(path, 127)
            self.assertEqual(len(result["abilities"]), 7)
            self.assertEqual(result["agent_groups"], ["agent", "default"])
            self.assertEqual(
                result["extra_declared_models"],
                [(127, "deepseek-v4-flash", "agent"), (127, "deepseek-v4-flash", "default")])

    def test_additive_projection_still_fails_on_a_lost_pool_member(self):
        for mutation in (
            'UPDATE abilities SET priority=20 WHERE channel_id=127 AND model=\'gpt-6-astra\' AND "group"=\'agent\'',
            'DELETE FROM abilities WHERE channel_id=127 AND model=\'gpt-5.6-sol\' AND "group"=\'agent\'',
            "UPDATE channels SET models='deepseek-v4-flash,gpt-5.6-sol' WHERE id=127",
            'UPDATE channels SET "group"=\'agent\' WHERE id=127',
            "UPDATE channels SET model_mapping='{\"gpt-6-astra\":\"other\"}' WHERE id=127",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_live_shaped_database(path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute(mutation)
                    db.commit()
                with self.assertRaises(RuntimeError):
                    configure.verify_projection(path, 127)

    def test_blank_test_model_is_rejected_as_a_false_green_probe(self):
        """A blank test_model makes the fork probe the first declared name.

        On the live channel that first name is deepseek-v4-flash, which answers while both GPT
        names are degraded, so every health probe reads green on the sole agent-group leg.
        """
        for value in ("", "deepseek-v4-flash"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute("UPDATE channels SET test_model=? WHERE id=127", (value,))
                    db.commit()
                with self.assertRaisesRegex(RuntimeError, "test_model"):
                    configure.verify_projection(path, 127)

    def test_header_override_only_has_to_parse(self):
        """The Codex UA assertion was retired with the measurement behind it.

        Direct calls to the same upstream with codex_cli_rs/0.154.0 and with the claude-cli UA the
        channel carries now returned identical verdicts, and the PUT that would rewrite it
        round-trips an empty key and rebuilds abilities. A malformed header still breaks every
        request, so that is the contract.
        """
        for raw in ('{"User-Agent": "claude-cli/1.0.43 (external, cli)"}', "{}", None):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute("UPDATE channels SET header_override=? WHERE id=127", (raw,))
                    db.commit()
                configure.verify_projection(path, 127)
        for raw in ("not json", "[]"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute("UPDATE channels SET header_override=? WHERE id=127", (raw,))
                    db.commit()
                with self.assertRaises(RuntimeError):
                    configure.verify_projection(path, 127)

    def test_anyrouter_status_drift_reports_the_recorded_status_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.db"
            self.create_database(path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE channels SET status=2 WHERE id=126")
                db.execute("UPDATE abilities SET enabled=0 WHERE channel_id=126")
                db.commit()
            # enabled follows the channel's own status, so the leg rows going 0 are a consequence
            # of the disable and must be reported as the AnyRouter leg, never as pool drift.
            with self.assertRaisesRegex(RuntimeError, "AnyRouter leg"):
                configure.verify_projection(path, 127)
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE channels SET status=1, priority=41 WHERE id=126")
                db.execute("UPDATE abilities SET enabled=1, priority=41 WHERE channel_id=126")
                db.commit()
            with self.assertRaisesRegex(RuntimeError, "AnyRouter tier drifted"):
                configure.verify_projection(path, 127)

    def test_projection_multi_key_health(self):
        healthy_info = json.dumps({"is_multi_key": True, "multi_key_size": 2, "multi_key_mode": "polling"}).encode()
        for mutation in (
            None,
            "UPDATE channels SET key='sk-fixture-a' WHERE id=127",
            "UPDATE channels SET key='sk-fixture-a\nsk-fixture-b\nsk-fixture-c' WHERE id=127",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "fixture.db"
                self.create_database(path)
                with closing(sqlite3.connect(path)) as db:
                    db.execute("UPDATE channels SET key='sk-fixture-a\nsk-fixture-b' WHERE id=127", ())
                    db.execute("UPDATE channels SET channel_info=? WHERE id=127", (healthy_info,))
                    if mutation:
                        db.execute(mutation)
                    db.commit()
                if mutation:
                    with self.assertRaises(RuntimeError):
                        configure.verify_projection(path, 127)
                else:
                    configure.verify_projection(path, 127)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.db"
            self.create_database(path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE channels SET key='sk-fixture-a\nsk-fixture-b', channel_info=? WHERE id=127",
                           (json.dumps({"is_multi_key": True, "multi_key_size": 2}),))
                db.commit()
            with self.assertRaisesRegex(RuntimeError, "BLOB"):
                configure.verify_projection(path, 127)

if __name__ == "__main__":
    unittest.main()
