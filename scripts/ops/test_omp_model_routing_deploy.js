import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const deployScript = join(here, "deploy-omp-model-routing-observability.ps1");
const sha256 = content => createHash("sha256").update(content).digest("hex").toUpperCase();

function deploy(source, destination, probeSource, probeDestination, backups) {
  execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", deployScript, "-SourcePath", source, "-DestinationPath", destination, "-ProbeSourcePath", probeSource, "-ProbeDestinationPath", probeDestination, "-BackupRoot", backups], { encoding: "utf8" });
}

test("deploys the routing and canary probe extensions as a verified pair", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-deploy-"));
  try {
    const source = join(root, "source.js");
    const probeSource = join(root, "probe-source.js");
    const destination = join(root, "agent", "extensions", "routing.js");
    const probeDestination = join(root, "agent", "canary", "probe.js");
    const backups = join(root, "backups");
    const first = "export default 'first';\n";
    const second = "export default 'second';\n";
    const firstProbe = "export default 'first-probe';\n";
    const secondProbe = "export default 'second-probe';\n";
    writeFileSync(source, first);
    writeFileSync(probeSource, firstProbe);
    deploy(source, destination, probeSource, probeDestination, backups);
    assert.equal(readFileSync(destination, "utf8"), first);
    assert.equal(readFileSync(probeDestination, "utf8"), firstProbe);
    const firstDir = join(backups, readdirSync(backups)[0]);
    assert.equal(readFileSync(join(firstDir, "destination.absent"), "ascii").trim(), "Destination did not exist before deployment.");
    assert.equal(readFileSync(join(firstDir, "probe-destination.absent"), "ascii").trim(), "Probe destination did not exist before deployment.");
    writeFileSync(source, second);
    writeFileSync(probeSource, secondProbe);
    const legacyProbe = join(root, "agent", "extensions", "omp-model-tool-canary-probe.js");
    writeFileSync(legacyProbe, "export default 'legacy-discovered-probe';\n");
    deploy(source, destination, probeSource, probeDestination, backups);
    assert.equal(readFileSync(probeDestination, "utf8"), secondProbe);
    assert.equal(existsSync(legacyProbe), false);
    const dirs = readdirSync(backups).map(name => join(backups, name));
    const replacement = dirs.find(dir => {
      try {
        return readFileSync(join(dir, "previous.js"), "utf8") === first && readFileSync(join(dir, "previous-probe.js"), "utf8") === firstProbe;
      } catch { return false; }
    });
    assert.ok(replacement);
    const manifest = JSON.parse(readFileSync(join(replacement, "deployment.json"), "ascii"));
    assert.equal(manifest.sourceSha256, sha256(second));
    assert.equal(manifest.destinationSha256, sha256(second));
    assert.equal(manifest.previousSha256, sha256(first));
    assert.equal(manifest.probeSourceSha256, sha256(secondProbe));
    assert.equal(manifest.probeDestinationSha256, sha256(secondProbe));
    assert.equal(manifest.previousProbeSha256, sha256(firstProbe));
    assert.equal(manifest.legacyProbeRemoved, true);
    assert.equal(manifest.legacyProbeSha256, sha256("export default 'legacy-discovered-probe';\n"));
    assert.equal(manifest.restartPerformed, false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("restores the main extension when probe replacement fails", () => {
  const root = mkdtempSync(join(tmpdir(), "omp-routing-deploy-rollback-"));
  try {
    const source = join(root, "source.js");
    const probeSource = join(root, "probe-source.js");
    const destination = join(root, "agent", "extensions", "routing.js");
    const probeDestination = join(root, "agent", "canary", "probe.js");
    const backups = join(root, "backups");
    writeFileSync(source, "export default 'known-good';\n");
    writeFileSync(probeSource, "export default 'known-good-probe';\n");
    deploy(source, destination, probeSource, probeDestination, backups);

    writeFileSync(source, "export default 'candidate';\n");
    writeFileSync(probeSource, "export default 'candidate-probe';\n");
    const invalidProbeDestination = join(root, "agent", "extensions");
    assert.throws(() => deploy(source, destination, probeSource, invalidProbeDestination, backups));
    assert.equal(readFileSync(destination, "utf8"), "export default 'known-good';\n");
    assert.equal(readFileSync(probeDestination, "utf8"), "export default 'known-good-probe';\n");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
