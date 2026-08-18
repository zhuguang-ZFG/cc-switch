import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
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
const deployScript = join(here, "deploy-omp-sota-escalation.ps1");

function sha256(content) {
  return createHash("sha256").update(content).digest("hex").toUpperCase();
}

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
      "-SourcePath",
      source,
      "-DestinationPath",
      destination,
      "-BackupRoot",
      backups,
    ],
    { encoding: "utf8" },
  );
}

test("deploys SOTA extension atomically with verified backup metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-sota-deploy-"));
  try {
    const source = join(root, "source.js");
    const destination = join(root, "agent", "extensions", "extension.js");
    const backups = join(root, "backups");
    const first = "export default 'first';\n";
    const second = "export default 'second';\n";

    writeFileSync(source, first);
    deploy(source, destination, backups);
    assert.equal(readFileSync(destination, "utf8"), first);
    const firstBackup = readdirSync(backups).map((name) => join(backups, name));
    assert.equal(firstBackup.length, 1);
    assert.equal(
      readFileSync(join(firstBackup[0], "destination.absent"), "ascii").trim(),
      "Destination did not exist before deployment.",
    );

    writeFileSync(source, second);
    deploy(source, destination, backups);
    assert.equal(readFileSync(destination, "utf8"), second);
    const backupDirectories = readdirSync(backups).map((name) => join(backups, name));
    const replacementBackup = backupDirectories.find((entry) => {
      try {
        return readFileSync(join(entry, "previous.js"), "utf8") === first;
      } catch {
        return false;
      }
    });
    assert.ok(replacementBackup);
    const manifest = JSON.parse(
      readFileSync(join(replacementBackup, "deployment.json"), "ascii"),
    );
    assert.equal(manifest.sourceSha256, sha256(second));
    assert.equal(manifest.destinationSha256, sha256(second));
    assert.equal(manifest.previousSha256, sha256(first));
    assert.equal(manifest.restartPerformed, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
