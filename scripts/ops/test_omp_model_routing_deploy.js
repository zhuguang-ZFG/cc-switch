import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const deployScript = join(here, "deploy-omp-model-routing-observability.ps1");
const sha256 = content => createHash("sha256").update(content).digest("hex").toUpperCase();

function deploy(source, destination, backups) {
  execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", deployScript, "-SourcePath", source, "-DestinationPath", destination, "-BackupRoot", backups], { encoding: "utf8" });
}

test("deploys the routing extension with absence/backup markers and hashes", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-deploy-"));
  try {
    const source = join(root, "source.js");
    const destination = join(root, "agent", "extensions", "routing.js");
    const backups = join(root, "backups");
    const first = "export default 'first';\n";
    const second = "export default 'second';\n";
    writeFileSync(source, first);
    deploy(source, destination, backups);
    assert.equal(readFileSync(destination, "utf8"), first);
    const firstDir = join(backups, readdirSync(backups)[0]);
    assert.equal(readFileSync(join(firstDir, "destination.absent"), "ascii").trim(), "Destination did not exist before deployment.");
    writeFileSync(source, second);
    deploy(source, destination, backups);
    const dirs = readdirSync(backups).map(name => join(backups, name));
    const replacement = dirs.find(dir => {
      try { return readFileSync(join(dir, "previous.js"), "utf8") === first; } catch { return false; }
    });
    assert.ok(replacement);
    const manifest = JSON.parse(readFileSync(join(replacement, "deployment.json"), "ascii"));
    assert.equal(manifest.sourceSha256, sha256(second));
    assert.equal(manifest.destinationSha256, sha256(second));
    assert.equal(manifest.previousSha256, sha256(first));
    assert.equal(manifest.restartPerformed, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
