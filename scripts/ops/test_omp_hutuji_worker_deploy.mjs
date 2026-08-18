import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  cpSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const deployScript = join(here, "deploy-omp-hutuji-workers.ps1");
const templates = join(here, "omp-agents");

function deploy(source, destination, backups) {
  execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      deployScript,
      "-SourceRoot",
      source,
      "-DestinationRoot",
      destination,
      "-BackupRoot",
      backups,
    ],
    { encoding: "utf8", stdio: "pipe" },
  );
}

test("deploys both workers with rollback metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-hutuji-workers-"));
  try {
    const destination = join(root, "agents");
    const backups = join(root, "backups");
    cpSync(templates, join(root, "templates"), { recursive: true });
    const source = join(root, "templates");
    cpSync(source, destination, { recursive: true });
    writeFileSync(join(destination, "dsv4pro-worker.md"), "old worker\n");
    rmSync(join(destination, "hutuji-worker.md"));

    deploy(source, destination, backups);
    assert.equal(
      readFileSync(join(destination, "hutuji-worker.md"), "utf8"),
      readFileSync(join(source, "hutuji-worker.md"), "utf8"),
    );
    assert.equal(
      readFileSync(join(destination, "dsv4pro-worker.md"), "utf8"),
      readFileSync(join(source, "dsv4pro-worker.md"), "utf8"),
    );
    const backupDirectory = join(backups, readdirSync(backups)[0]);
    assert.equal(
      readFileSync(join(backupDirectory, "previous-dsv4pro-worker.md"), "utf8"),
      "old worker\n",
    );
    assert.equal(
      readFileSync(
        join(backupDirectory, "hutuji-worker.destination.absent"),
        "ascii",
      ).trim(),
      "Destination did not exist.",
    );
    const manifest = JSON.parse(
      readFileSync(join(backupDirectory, "deployment.json"), "ascii"),
    );
    assert.equal(manifest.restartPerformed, false);
    assert.equal(manifest.workers.length, 2);
    assert.equal(
      manifest.workers.every(
        (worker) => worker.sourceSha256 === worker.destinationSha256,
      ),
      true,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects a concrete worker model before touching destinations", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-hutuji-workers-invalid-"));
  try {
    const source = join(root, "templates");
    const destination = join(root, "agents");
    const backups = join(root, "backups");
    cpSync(templates, source, { recursive: true });
    cpSync(templates, destination, { recursive: true });
    const target = join(source, "hutuji-worker.md");
    writeFileSync(
      target,
      readFileSync(target, "utf8").replace(
        '"@task"',
        '"zg-newapi/gpt-5.6-luna"',
      ),
    );
    const before = readFileSync(join(destination, "hutuji-worker.md"), "utf8");
    assert.throws(() => deploy(source, destination, backups));
    assert.equal(
      readFileSync(join(destination, "hutuji-worker.md"), "utf8"),
      before,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
