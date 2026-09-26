import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildReport,
  findingsFromFile,
  groupFindings,
  priorityFor,
  renderReport,
  sourceOf,
  summarize,
  titleFor,
  weekOf,
} from "./report.mjs";

// Trimmed from real `snyk ... --json-file-output` runs on 2026-09-26 (CLI 1.1307.4):
// pnpm.json from a two-package workspace pinning old lodash, axios and minimist;
// python.json from a venv with python-multipart 0.0.20 and requests 2.31.0;
// code.json from `snyk code test` on this repository's main, one of its results ignored
// in Snyk's UI; the two image-*.json from `snyk container test` on a stale hub API image
// and on python:3.12.0-slim-bookworm, which share a Debian 12 base.
const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures");
const load = (name) => JSON.parse(readFileSync(join(FIXTURES, name), "utf8"));
const allFixtures = () => readdirSync(FIXTURES).sort().map((name) => [name, load(name)]);
const meta = { week: "2026-09-21", repo: "letsrebase/rebase", sha: "ee6d1f707c0ffee", runUrl: "https://github.com/letsrebase/rebase/actions/runs/1" };

test("names each scan after its file", () => {
  assert.deepEqual(sourceOf("pnpm.json"), { kind: "pnpm", target: null });
  assert.deepEqual(sourceOf("dir/image-pigrocrm-api.json"), { kind: "image", target: "pigrocrm-api" });
  assert.equal(sourceOf("image-.json"), null);
  assert.equal(sourceOf("report.md"), null);
  assert.equal(sourceOf("other.json"), null);
});

test("reads every project of an --all-projects array", () => {
  const { findings } = findingsFromFile("pnpm.json", load("pnpm.json"));
  assert.equal(findings.length, 6);
  assert.deepEqual([...new Set(findings.map((f) => f.target))].sort(), ["packages/app", "packages/site"]);
  const axios = findings.find((f) => f.package === "axios");
  assert.equal(axios.severity, "critical");
  assert.deepEqual(axios.fixedIn, ["0.31.1", "1.15.1"]);
  assert.deepEqual(axios.cves, ["CVE-2026-42035"]);
});

test("reads a single-project object, the pip route's shape", () => {
  const { findings } = findingsFromFile("python.json", load("python.json"));
  assert.deepEqual(
    findings.map((f) => `${f.severity} ${f.package}@${f.version}`),
    ["high python-multipart@0.0.20", "medium python-multipart@0.0.20", "medium requests@2.31.0"],
  );
  assert.ok(findings.every((f) => f.kind === "python" && f.target === null));
});

test("maps Snyk Code levels to severities and skips what was ignored in Snyk's UI", () => {
  const { findings } = findingsFromFile("code.json", load("code.json"));
  assert.deepEqual(
    findings.map((f) => `${f.severity} ${f.id} ${f.location}`),
    [
      "high javascript/HardcodedNonCryptoSecret projects/pigrocrm/apps/web/src/lib/registerHandoff.ts:22",
      "low python/InsecureHash projects/hub/packages/core/src/rebase_core/campaigns/render.py:44",
      "medium python/OR projects/hub/apps/api/src/rebase_api/routers/campaigns.py:40",
    ],
  );
  assert.ok(!findings.some((f) => f.id === "javascript/DOMXSS"), "the accepted DOMXSS ignore is not reported");
});

test("still reports a finding whose ignore is only under review", () => {
  const sarif = load("code.json");
  const domxss = sarif.runs[0].results.find((r) => r.ruleId === "javascript/DOMXSS");
  domxss.suppressions[0].status = "underReview";
  const { findings } = findingsFromFile("code.json", sarif);
  assert.ok(findings.some((f) => f.id === "javascript/DOMXSS" && f.severity === "medium"));
});

test("reads the applications a container scan may carry next to the OS packages", () => {
  const image = load("image-python-base.json");
  const withApp = { ...image, vulnerabilities: [], applications: [load("python.json")] };
  const { findings } = findingsFromFile("image-rebase-api.json", withApp);
  assert.equal(findings.length, 3);
  assert.ok(findings.every((f) => f.kind === "image" && f.target === "rebase-api"));
});

test("names the workspace root and skips license issues", () => {
  const [root, app] = load("pnpm.json");
  const license = { ...app.vulnerabilities[0], id: "snyk:lic:npm:axios:MIT", type: "license", severity: "high" };
  const { findings } = findingsFromFile("pnpm.json", [{ ...root, vulnerabilities: [app.vulnerabilities[1]] }, { ...app, vulnerabilities: [license] }]);
  assert.deepEqual(findings.map((f) => `${f.target} ${f.id}`), ["root SNYK-JS-LODASH-1040724"]);
});

test("records a project the scan could not test instead of dropping it", () => {
  const data = [load("pnpm.json")[1], { ok: false, error: "Could not detect package manager", path: "projects/x" }];
  const { findings, errors } = findingsFromFile("pnpm.json", data);
  assert.equal(findings.length, 4);
  assert.deepEqual(errors, [{ kind: "pnpm", target: "projects/x", message: "Could not detect package manager" }]);
});

test("one row per advisory, across projects, images and a package's version spellings", () => {
  const { rows } = buildReport(allFixtures());
  const lodash = rows.find((r) => r.id === "SNYK-JS-LODASH-1040724");
  assert.deepEqual([...lodash.targets.get("pnpm")].sort(), ["packages/app", "packages/site"]);
  assert.equal(rows.filter((r) => r.id === "SNYK-JS-LODASH-1040724").length, 1);

  const zlib = rows.find((r) => r.id === "SNYK-DEBIAN12-ZLIB-6008963");
  assert.deepEqual([...zlib.targets.get("image")].sort(), ["python-base", "rebase-api"]);

  const utilLinux = rows.filter((r) => r.id === "SNYK-DEBIAN12-UTILLINUX-19513270");
  assert.equal(utilLinux.length, 1, "four paths and two version spellings are one advisory");
  assert.deepEqual([...utilLinux[0].versions].sort(), ["1:2.38.1-5+deb12u3", "2.38.1-5+deb12u3"]);
});

test("merges the same advisory found by two kinds of scan into one row", () => {
  const python = findingsFromFile("python.json", load("python.json")).findings;
  const inImage = python.map((f) => ({ ...f, kind: "image", target: "rebase-api" }));
  const rows = groupFindings([...python, ...inImage]);
  assert.equal(rows.length, 3);
  assert.deepEqual([...rows[0].targets.keys()], ["python", "image"]);
});

test("orders rows by severity, then source, then package", () => {
  const { rows } = buildReport(allFixtures());
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  for (let i = 1; i < rows.length; i++) assert.ok(order[rows[i - 1].severity] <= order[rows[i].severity]);
  assert.deepEqual(
    rows.filter((r) => r.severity === "critical").map((r) => r.id),
    ["SNYK-JS-AXIOS-16298058", "SNYK-DEBIAN12-PERL-17960064", "SNYK-DEBIAN12-ZLIB-6008963"],
  );
});

test("counts every advisory once, and lows per source", () => {
  const { rows } = buildReport(allFixtures());
  const { counts, lowsByKind, worst } = summarize(rows);
  assert.deepEqual(counts, { critical: 3, high: 6, medium: 4, low: 3 });
  assert.deepEqual(lowsByKind, { pnpm: 1, python: 0, image: 1, code: 1 });
  assert.equal(worst, "critical");
});

test("priority follows the worst severity, and lows alone file nothing", () => {
  assert.equal(priorityFor("critical"), 1);
  assert.equal(priorityFor("high"), 2);
  assert.equal(priorityFor("medium"), 3);
  assert.equal(priorityFor(null), null);
  const lowOnly = groupFindings(findingsFromFile("code.json", load("code.json")).findings.filter((f) => f.severity === "low"));
  assert.equal(summarize(lowOnly).worst, null);
});

test("names the week by its Monday, in UTC", () => {
  assert.equal(weekOf(new Date("2026-09-28T06:00:00Z")), "2026-09-28");
  assert.equal(weekOf(new Date("2026-09-26T08:00:00Z")), "2026-09-21");
  assert.equal(weekOf(new Date("2026-10-04T23:59:00Z")), "2026-09-28");
  const title = titleFor("2026-09-28");
  assert.match(title, /^Fix /);
  assert.ok(title.length < 80);
});

test("renders one table per severity at medium and above, with lows as counts", () => {
  const built = buildReport(allFixtures());
  const { markdown } = renderReport({ rows: built.rows, errors: built.errors, meta: { ...meta, scanned: built.scanned } });
  assert.match(markdown, /^\*\*Snyk, week of 2026-09-21\.\*\* `main` at \[`ee6d1f7`\]/);
  assert.match(markdown, /\*\*3 critical, 6 high, 4 medium\*\*/);
  assert.ok(markdown.indexOf("**Critical**") < markdown.indexOf("**High**"));
  assert.ok(markdown.indexOf("**High**") < markdown.indexOf("**Medium**"));
  assert.doesNotMatch(markdown, /\*\*Low\*\*\n\n\|/);
  assert.doesNotMatch(markdown, /<br>/, "Linear would print raw HTML as text");
  assert.match(markdown, /\*\*Low\*\*, counted per source: pnpm 1, python 0, image 1, code 1\./);
  assert.match(
    markdown,
    /\| pnpm \(packages\/app, packages\/site\) \| `lodash@4\.17\.20` \| Code Injection \| \[SNYK-JS-LODASH-1040724\]\(https:\/\/security\.snyk\.io\/vuln\/SNYK-JS-LODASH-1040724\), CVE-2021-23337 \| 4\.17\.21 \|/,
  );
  assert.match(markdown, /\| image \(python-base, rebase-api\) \| `zlib@1:1\.2\.13\.dfsg-1` \| .* \| no fix yet \|/);
  assert.match(
    markdown,
    /\| code \| \[`projects\/pigrocrm\/apps\/web\/src\/lib\/registerHandoff\.ts:22`\]\(https:\/\/github\.com\/letsrebase\/rebase\/blob\/ee6d1f707c0ffee\/projects\/pigrocrm\/apps\/web\/src\/lib\/registerHandoff\.ts#L22\) \| Hardcoded Non-Cryptographic Secret \| `javascript\/HardcodedNonCryptoSecret`, CWE-547 \| fix in code \|/,
  );
  assert.match(markdown, /Scanned: pnpm workspace \(3 projects\), Python deps from uv\.lock, image python-base, image rebase-api, Snyk Code\./);
  assert.match(markdown, /\n9 of them can be fixed today: a fixed version exists, or the finding is in our own code\.\n/);
});

test("escapes a pipe in a cell so the table keeps its columns", () => {
  const rows = groupFindings([
    { kind: "pnpm", target: "web", key: "X|a", id: "X", severity: "high", title: "a | b", package: "a", version: "1", location: null, cves: [], cwes: [], fixedIn: [] },
  ]);
  const { markdown } = renderReport({ rows, meta });
  assert.match(markdown, /\| a \\\| b \|/);
});

test("cuts the least severe rows to fit, and says so", () => {
  const many = [];
  for (let i = 0; i < 400; i++) {
    many.push({ kind: "image", target: "api", key: `M${i}|p`, id: `SNYK-M-${i}`, severity: "medium", title: "t".repeat(80), package: "p", version: "1", location: null, cves: [], cwes: [], fixedIn: ["2"] });
  }
  many.push({ kind: "image", target: "api", key: "C|p", id: "SNYK-C-1", severity: "critical", title: "worst", package: "p", version: "1", location: null, cves: [], cwes: [], fixedIn: [] });
  const rows = groupFindings(many);
  const { markdown, summary } = renderReport({ rows, meta, maxLength: 20000 });
  assert.ok(markdown.length <= 20000, `length ${markdown.length}`);
  assert.match(markdown, /SNYK-C-1/);
  assert.match(markdown, /\.\.\.and \d+ more rows, left out to fit here/);
  assert.equal(summary.counts.medium, 400, "the counts stay whole");
  assert.equal(renderReport({ rows, meta }).markdown.includes("more rows"), false);
});

test("reports a clean week without tables", () => {
  const { markdown, summary } = renderReport({ rows: [], meta: { ...meta, scanned: ["Snyk Code"] } });
  assert.equal(summary.worst, null);
  assert.match(markdown, /\*\*0 critical, 0 high, 0 medium\*\*/);
  assert.doesNotMatch(markdown, /\| Source \|/);
});
