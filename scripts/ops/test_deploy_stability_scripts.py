"""Exercise deployment and rollback exclusively in temporary directories."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("deploy_stability", Path(__file__).with_name("deploy-stability-scripts.py"))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.runtime = root / "runtime"
        self.repo = root / "repo"
        for name, relative in deploy.FILES.items():
            path = self.runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old\n")
            source = self.repo / "scripts/ops" / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"new\n")
        for relative in ("agent/config.yml", "agent/models.yml", "guardian/secrets.json"):
            (self.runtime / relative).write_text("private fixture", encoding="utf-8")
        for name in ("supervisor-status.json", "heartbeat.json"):
            (self.runtime / "guardian" / name).write_text(json.dumps({"pid": 123, "ts": "fixture"}), encoding="utf-8")
        for name in ("guardian.log", "proxies-supervisor.log", "watchdog.log"):
            (self.runtime / "guardian" / name).write_text("private log fixture", encoding="utf-8")
        for name, value in (("RUNTIME", self.runtime), ("REPO", self.repo)):
            patcher = patch.object(deploy, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(deploy.subprocess, "check_output", return_value=b"old\n")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_verified_deploy_and_rollback_preserve_configuration(self):
        deploy.deploy()
        manifest = next(self.runtime.glob("guardian/backups/*/deployment.json"))
        self.assertNotIn("private", manifest.read_text(encoding="utf-8"))
        for relative in deploy.FILES.values():
            self.assertEqual((self.runtime / relative).read_bytes(), b"new\n")
        deploy.rollback(manifest)
        for relative in deploy.FILES.values():
            self.assertEqual((self.runtime / relative).read_bytes(), b"old\n")
        self.assertEqual((self.runtime / "agent/config.yml").read_text(), "private fixture")

    def test_failure_after_first_replacement_rolls_back_all_files(self):
        original = deploy.os.replace
        count = 0

        def fail_second(source, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected replacement failure")
            original(source, destination)

        with patch.object(deploy.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                deploy.deploy()
        for relative in deploy.FILES.values():
            self.assertEqual((self.runtime / relative).read_bytes(), b"old\n")

    def test_unreviewed_runtime_drift_aborts_before_backup_or_writes(self):
        path = self.runtime / next(iter(deploy.FILES.values()))
        path.write_bytes(b"user change")
        with self.assertRaisesRegex(RuntimeError, "drift"):
            deploy.deploy()
        self.assertFalse((self.runtime / "guardian/backups").exists())
        self.assertEqual(path.read_bytes(), b"user change")


if __name__ == "__main__":
    unittest.main()
