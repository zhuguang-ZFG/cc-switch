import { readFileSync } from "node:fs";
import { summarizeReports } from "./bugfix-cases.mjs";
const paths = process.argv.slice(2);
if (paths.length !== 2)
  throw new Error(
    "Usage: node scripts/ops/compare_omp_bugfix.mjs baseline.json current.json",
  );
const docs = paths.map((p) => JSON.parse(readFileSync(p, "utf8")));
if (docs.some((d) => d.schema !== 1 || !Array.isArray(d.reports)))
  throw new Error("Invalid report");
const identities = docs.map((d) =>
  d.reports
    .filter((r) => r.mode === "live")
    .map((r) => r.caseId + ":" + r.model + ":" + (r.caseHash ?? "legacy"))
    .sort()
    .join("|"),
);
if (!identities[0] || identities[0] !== identities[1])
  throw new Error(
    "Comparison requires the same live cases, models and repetition counts",
  );
const [before, after] = docs.map((d) => summarizeReports(d.reports));
console.log(
  JSON.stringify(
    {
      before,
      after,
      delta: {
        successRate: after.successRate - before.successRate,
        meanDurationMs: after.meanDurationMs - before.meanDurationMs,
        unintendedEditRate:
          after.unintendedEditRate - before.unintendedEditRate,
        costUsd: null,
      },
      note: "Small samples do not establish general coding capability.",
    },
    null,
    2,
  ),
);
