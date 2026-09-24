import importlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

deploy = importlib.import_module("deploy-omp-solving-upgrade")


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="omp-upgrade-test-")
        self.addCleanup(self.tmp.cleanup)
        self.agent = Path(self.tmp.name)
        (self.agent / "extensions").mkdir()
        source = deploy.REPO / "scripts/ops"
        for name in ("omp-problem-solving.js", "omp-task-verification.js", "omp-model-routing-observability.js", "omp-sota-escalation.js"):
            shutil.copy2(source / name, self.agent / "extensions" / name)
        for name in ("config.yml", "models.yml", "mcp.json", "sota-review-policy.json"):
            (self.agent / name).write_text("unchanged")
        policy = json.loads((source / "task-verification-policy.json").read_text())
        project = policy["projects"][0]
        project["root"] = str(self.agent)
        project["repairOnFailure"] = False
        for check in project["checks"]:
            check["command"] = sys.executable
        (self.agent / "task-verification-policy.json").write_text(json.dumps(policy))
        self.before = {name: (self.agent / relative).read_bytes() for name, relative in deploy.FILES.items()}
        self.previous = self.agent / "previous.json"
        self.previous.write_text(json.dumps({"agent": str(self.agent), "files": [
            {"name": name, "after": deploy.digest(self.agent / relative)} for name, relative in deploy.FILES.items()],
            "existing_extensions": {"extensions/omp-problem-solving.js": deploy.digest(self.agent / "extensions/omp-problem-solving.js")}}))

    def test_deploy_preserves_protected_config_and_restores_exact_bytes(self):
        manifest = deploy.deploy(self.previous, self.agent)
        policy = json.loads((self.agent / "task-verification-policy.json").read_text())
        self.assertTrue(policy["projects"][0]["repairOnFailure"])
        self.assertEqual((self.agent / "mcp.json").read_text(), "unchanged")
        deploy.rollback(manifest, self.agent)
        for name, relative in deploy.FILES.items():
            self.assertEqual((self.agent / relative).read_bytes(), self.before[name])

    def test_partial_write_restores_pair_and_policy(self):
        real = deploy.replace_verified
        calls = 0
        def fail_once(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("fixture failure")
            return real(*args)
        with patch.object(deploy, "replace_verified", fail_once):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                deploy.deploy(self.previous, self.agent)
        for name, relative in deploy.FILES.items():
            self.assertEqual((self.agent / relative).read_bytes(), self.before[name])

    def test_deploy_and_rollback_refuse_user_drift(self):
        path = self.agent / "extensions/omp-problem-solving.js"
        path.write_text("user change")
        with self.assertRaisesRegex(RuntimeError, "baseline drift"):
            deploy.deploy(self.previous, self.agent)
        path.write_bytes(self.before["omp-problem-solving.js"])
        manifest = deploy.deploy(self.previous, self.agent)
        path.write_text("later user change")
        with self.assertRaisesRegex(RuntimeError, "Runtime drift"):
            deploy.rollback(manifest, self.agent)
        self.assertEqual(path.read_text(), "later user change")


if __name__ == "__main__":
    unittest.main()
