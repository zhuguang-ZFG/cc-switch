import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

deploy = importlib.import_module("deploy-omp-problem-solving")


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omp-deploy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.agent = Path(self.tmp.name) / "agent"
        (self.agent / "extensions").mkdir(parents=True)
        self.old = self.agent / "extensions/omp-sota-escalation.js"
        self.old.write_bytes(b"old source\n")
        (self.agent / "extensions/omp-model-routing-observability.js").write_bytes(
            (deploy.REPO / "scripts/ops/omp-model-routing-observability.js").read_bytes())
        for name in ("config.yml", "models.yml"):
            (self.agent / name).write_text("fixture", encoding="utf-8")

    def test_verified_deployment_and_rollback(self):
        manifest = deploy.deploy(self.agent, base=b"old source\n")
        data = json.loads(manifest.read_text())
        self.assertEqual(len(data["files"]), 4)
        self.assertGreater(data["files"][1]["backup_size"], 0)
        deploy.rollback(manifest, self.agent)
        self.assertEqual(self.old.read_bytes(), b"old source\n")
        self.assertFalse((self.agent / "sota-review-policy.json").exists())

    def test_partial_failure_restores_all_paths(self):
        original = deploy.replace_verified
        calls = 0

        def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("fixture replacement failure")
            return original(*args)

        with patch.object(deploy, "replace_verified", fail_once):
            with self.assertRaisesRegex(RuntimeError, "fixture replacement"):
                deploy.deploy(self.agent, base=b"old source\n")
        self.assertEqual(self.old.read_bytes(), b"old source\n")
        self.assertFalse((self.agent / "extensions/review/omp-review-guard.js").exists())

    def test_drift_refuses_overwrite(self):
        with self.assertRaisesRegex(RuntimeError, "drift"):
            deploy.deploy(self.agent, base=b"wrong\n")
        manifest = deploy.deploy(self.agent, base=b"old source\n")
        self.old.write_bytes(b"user changed this\n")
        with self.assertRaisesRegex(RuntimeError, "drift"):
            deploy.rollback(manifest, self.agent)
        self.assertEqual(self.old.read_bytes(), b"user changed this\n")


if __name__ == "__main__":
    unittest.main()
