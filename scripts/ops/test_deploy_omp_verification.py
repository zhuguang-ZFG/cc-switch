import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

deploy = importlib.import_module("deploy-omp-verification")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omp-verification-deploy-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agent, self.baseline = self.root / "agent", self.root / "baseline"
        (self.agent / "extensions").mkdir(parents=True)
        self.baseline.mkdir()
        self.existing = {"$schema": "fixture", "mcpServers": {"other": {"env": {"SECRET": "fixture"}}},
                         "disabledServers": ["disabled"], "unknown": {"preserve": True}}
        (self.agent / "mcp.json").write_text(json.dumps(self.existing), encoding="utf-8")
        for name in ("config.yml", "models.yml"):
            (self.agent / name).write_text("fixture", encoding="utf-8")
        (self.baseline / "mcp.json").write_bytes((self.agent / "mcp.json").read_bytes())
        (self.baseline / "baseline.json").write_text(json.dumps({
            name: {"sha256": deploy.digest(self.agent / name)} for name in ("mcp.json", "config.yml", "models.yml")}), encoding="utf-8")
        dep = "omp-model-routing-observability.js"
        (self.agent / "extensions" / dep).write_bytes((deploy.REPO / "scripts/ops" / dep).read_bytes())

    def apply(self):
        executable = Path(sys.executable)
        return deploy.deploy(self.baseline, executable, executable, executable, self.agent, self.root)

    def test_preserves_existing_config_and_exact_rollback(self):
        before = (self.agent / "mcp.json").read_bytes()
        manifest = self.apply()
        mcp = json.loads((self.agent / "mcp.json").read_text())
        entry = mcp["mcpServers"].pop("gitnexus")
        self.assertEqual(mcp, self.existing)
        self.assertEqual(entry["args"][-1], "mcp")
        policy = json.loads((self.agent / "task-verification-policy.json").read_text())
        self.assertEqual(policy["projects"][0]["root"], str(self.root.resolve()))
        self.assertTrue(Path(policy["projects"][0]["checks"][0]["command"]).is_absolute())
        deploy.rollback(manifest, self.agent)
        self.assertEqual((self.agent / "mcp.json").read_bytes(), before)
        self.assertFalse((self.agent / "extensions/omp-task-verification.js").exists())

    def test_partial_write_failure_restores_all_files(self):
        real = deploy.replace_verified
        calls = 0

        def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("fixture failure")
            return real(*args)

        with patch.object(deploy, "replace_verified", fail_once):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                self.apply()
        self.assertEqual(json.loads((self.agent / "mcp.json").read_text()), self.existing)
        self.assertFalse((self.agent / "task-verification-policy.json").exists())

    def test_baseline_drift_refuses_any_writes(self):
        (self.agent / "config.yml").write_text("user change")
        with self.assertRaisesRegex(RuntimeError, "baseline drift"):
            self.apply()
        self.assertFalse((self.agent / "extension-backups").exists())

    def test_rollback_refuses_drift_and_corrupt_backup(self):
        manifest = self.apply()
        path = self.agent / "mcp.json"
        after = path.read_bytes()
        path.write_text("user change")
        with self.assertRaisesRegex(RuntimeError, "Runtime drift"):
            deploy.rollback(manifest, self.agent)
        self.assertTrue((self.agent / "task-verification-policy.json").exists())
        path.write_bytes(after)
        (manifest.parent / "mcp.json").write_text("corrupt backup")
        with self.assertRaisesRegex(RuntimeError, "Backup mismatch"):
            deploy.rollback(manifest, self.agent)
        self.assertEqual(path.read_bytes(), after)


if __name__ == "__main__":
    unittest.main()
