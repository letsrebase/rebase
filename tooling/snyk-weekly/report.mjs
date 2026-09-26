// Turns the JSON the weekly Snyk scan writes into one markdown report (REB-525).
// Pure functions only: no network, no file system, so every rule here has a test in
// report.test.mjs. Dependency-free on purpose, like tooling/client-repo-starter.
//
// The scan directory holds one file per scan, and the file name says what it is:
//   pnpm.json            snyk test --all-projects on the pnpm workspace
//   python.json          snyk test on the Python deps exported from uv.lock
//   code.json            snyk code test (SARIF)
//   image-<name>.json    snyk container test on one image the repository builds

export const SEVERITIES = ["critical", "high", "medium", "low"];
export const KINDS = ["pnpm", "python", "image", "code"];
const RANK = Object.fromEntries(SEVERITIES.map((s, i) => [s, i]));

// Snyk Code has no "critical"; its SARIF levels map onto the other three the way the
// CLI prints them (`[HIGH]` for error, `[MEDIUM]` for warning, `[LOW]` for note).
const SARIF_LEVELS = { error: "high", warning: "medium", note: "low" };

export function sourceOf(fileName) {
  const base = fileName.replace(/^.*\//, "").replace(/\.json$/, "");
  if (!fileName.endsWith(".json")) return null;
  if (base.startsWith("image-") && base.length > "image-".length) {
    return { kind: "image", target: base.slice("image-".length) };
  }
  if (base === "pnpm" || base === "python" || base === "code") return { kind: base, target: null };
  return null;
}

function severityOf(value) {
  const s = String(value ?? "").toLowerCase();
  return s in RANK ? s : "low";
}

// `snyk test --all-projects` writes an array with one object per project, a single
// project writes the object itself, and a container scan may carry the image's
// application projects under `applications`. All three are the same shape inside.
function projectsOf(data) {
  const top = Array.isArray(data) ? data : [data];
  return top.flatMap((p) => [p, ...(Array.isArray(p?.applications) ? p.applications : [])]).filter(Boolean);
}

// A workspace project is named by its folder (`projects/hub/apps/web`), since two of
// the package names (`web`, `hub`) say little on their own; the root is `root`.
function workspaceFolder(project) {
  const file = project.displayTargetFile ?? project.targetFile;
  if (!file) return project.projectName ?? null;
  const dir = file.replace(/\/?[^/]*$/, "");
  return dir || "root";
}

function openSourceFindings(data, { kind, target }) {
  const findings = [];
  const errors = [];
  for (const project of projectsOf(data)) {
    const where = kind === "image" ? target : kind === "pnpm" ? workspaceFolder(project) : null;
    if (project.error) {
      errors.push({ kind, target: where ?? project.path ?? null, message: String(project.error) });
      continue;
    }
    for (const v of project.vulnerabilities ?? []) {
      // With license policies on, Snyk lists license issues in the same array
      // (`type: "license"`); they are not vulnerabilities and this report skips them.
      if (v.type === "license") continue;
      const pkg = v.packageName ?? String(v.name ?? "").split("/")[0];
      findings.push({
        kind,
        target: where,
        key: `${v.id}|${pkg}`,
        id: v.id,
        severity: severityOf(v.severity),
        title: v.title ?? v.id,
        package: pkg,
        version: v.version ?? null,
        location: null,
        cves: v.identifiers?.CVE ?? [],
        cwes: v.identifiers?.CWE ?? [],
        fixedIn: v.fixedIn ?? [],
      });
    }
  }
  return { findings, errors };
}

// An ignore made in Snyk's UI comes back as a suppression; with Consistent Ignores that
// is the only kind there is (an inline `deepcode ignore` does nothing). `underReview` is
// an ignore somebody asked for and nobody approved yet, so it is still reported.
function isSuppressed(result) {
  return (result.suppressions ?? []).some((s) => !s.status || s.status === "accepted");
}

function codeFindings(sarif) {
  const findings = [];
  for (const run of sarif?.runs ?? []) {
    const rules = new Map((run.tool?.driver?.rules ?? []).map((r) => [r.id, r]));
    for (const result of run.results ?? []) {
      if (isSuppressed(result)) continue;
      const rule = rules.get(result.ruleId) ?? {};
      const physical = result.locations?.[0]?.physicalLocation ?? {};
      const file = physical.artifactLocation?.uri ?? "unknown";
      const line = physical.region?.startLine ?? null;
      const location = line ? `${file}:${line}` : file;
      const level = result.level ?? rule.defaultConfiguration?.level;
      findings.push({
        kind: "code",
        target: null,
        key: `code|${result.ruleId}|${location}`,
        id: result.ruleId,
        severity: SARIF_LEVELS[level] ?? "low",
        title: rule.shortDescription?.text ?? rule.name ?? result.ruleId,
        package: null,
        version: null,
        location,
        file,
        line,
        cves: [],
        cwes: rule.properties?.cwe ?? [],
        fixedIn: [],
      });
    }
  }
  return { findings, errors: [] };
}

export function findingsFromFile(fileName, data) {
  const source = sourceOf(fileName);
  if (!source) return { findings: [], errors: [] };
  return source.kind === "code" ? codeFindings(data) : openSourceFindings(data, source);
}

// One row per advisory: the same Snyk id on the same package, wherever it was found.
// The CRM's and the hub's API images share a base, so an OS advisory would otherwise
// appear once per image; a library both SPAs pin appears once per workspace project;
// and Debian spells one source package's version with and without its epoch
// (`1:2.38.1-5+deb12u3`, `2.38.1-5+deb12u3`), which is why the version is not part of
// the key but a set on the row.
export function groupFindings(findings) {
  const byKey = new Map();
  for (const f of findings) {
    let row = byKey.get(f.key);
    if (!row) {
      row = { ...f, versions: new Set(), targets: new Map() };
      delete row.target;
      delete row.version;
      byKey.set(f.key, row);
    }
    if (RANK[f.severity] < RANK[row.severity]) row.severity = f.severity;
    if (f.version) row.versions.add(f.version);
    if (!row.targets.has(f.kind)) row.targets.set(f.kind, new Set());
    if (f.target) row.targets.get(f.kind).add(f.target);
    for (const v of f.fixedIn) if (!row.fixedIn.includes(v)) row.fixedIn = [...row.fixedIn, v];
  }
  return [...byKey.values()].sort(compareRows);
}

function firstKind(row) {
  return Math.min(...[...row.targets.keys()].map((k) => KINDS.indexOf(k)));
}

function compareRows(a, b) {
  return (
    RANK[a.severity] - RANK[b.severity] ||
    firstKind(a) - firstKind(b) ||
    String(a.package ?? a.location).localeCompare(String(b.package ?? b.location)) ||
    a.id.localeCompare(b.id)
  );
}

export function summarize(rows) {
  const counts = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  const lowsByKind = Object.fromEntries(KINDS.map((k) => [k, 0]));
  for (const row of rows) {
    counts[row.severity] += 1;
    if (row.severity === "low") for (const kind of row.targets.keys()) lowsByKind[kind] += 1;
  }
  const worst = SEVERITIES.slice(0, 3).find((s) => counts[s] > 0) ?? null;
  const fixable = rows.filter((r) => r.severity !== "low" && (r.kind === "code" || r.fixedIn.length > 0)).length;
  return { counts, lowsByKind, worst, fixable };
}

// Linear's priority field: 1 Urgent, 2 High, 3 Medium. Nothing below medium is filed.
export function priorityFor(worst) {
  return { critical: 1, high: 2, medium: 3 }[worst] ?? null;
}

// The Monday of the week `date` falls in, UTC, as YYYY-MM-DD.
export function weekOf(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const back = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - back);
  return d.toISOString().slice(0, 10);
}

export const TITLE_PREFIX = "Fix the Snyk findings of the week of";

export function titleFor(week) {
  return `${TITLE_PREFIX} ${week}`;
}

function cell(text) {
  return String(text).replace(/\r?\n/g, " ").replace(/\|/g, "\\|");
}

function sourceCell(row) {
  return [...row.targets.keys()]
    .sort((a, b) => KINDS.indexOf(a) - KINDS.indexOf(b))
    .map((kind) => {
      const targets = [...row.targets.get(kind)].sort();
      return targets.length ? `${kind} (${targets.join(", ")})` : kind;
    })
    .join(", ");
}

function whereCell(row, meta) {
  if (row.kind === "code") {
    const label = `\`${cell(row.location)}\``;
    if (!meta.repo || !meta.sha || !row.line) return label;
    return `[${label}](https://github.com/${meta.repo}/blob/${meta.sha}/${encodeURI(row.file)}#L${row.line})`;
  }
  const versions = [...row.versions].sort();
  return `\`${cell(`${row.package}@${versions.join(" / ")}`)}\``;
}

// The id, then its CVEs or CWEs, joined by commas: Linear shows raw HTML as text, so a
// `<br>` would print on every row there.
function idCell(row) {
  if (row.kind === "code") return [`\`${cell(row.id)}\``, ...row.cwes].join(", ");
  const link = /^SNYK-/.test(row.id) ? `[${row.id}](https://security.snyk.io/vuln/${row.id})` : cell(row.id);
  return [link, ...row.cves].join(", ");
}

function fixedCell(row) {
  if (row.kind === "code") return "fix in code";
  return row.fixedIn.length ? cell(row.fixedIn.join(", ")) : "no fix yet";
}

export function renderRow(row, meta) {
  return `| ${cell(sourceCell(row))} | ${whereCell(row, meta)} | ${cell(row.title)} | ${idCell(row)} | ${fixedCell(row)} |`;
}

const TABLE_HEAD = "| Source | Package or location | Title | ID | Fixed in |\n|---|---|---|---|---|";

function countLine(counts) {
  return `${counts.critical} critical, ${counts.high} high, ${counts.medium} medium`;
}

export function headline(summary) {
  return countLine(summary.counts);
}

// The markdown every filing carries. `maxLength` keeps it under GitHub's 65,536
// characters for an issue body or a comment: rows are dropped from the bottom (the
// least severe first) and the report says how many, since the run's job summary always
// has the whole table.
export function renderReport({ rows, errors = [], meta, maxLength = Infinity }) {
  const summary = summarize(rows);
  const head = [];
  const commit = meta.sha ? `\`main\` at [\`${meta.sha.slice(0, 7)}\`](https://github.com/${meta.repo}/commit/${meta.sha})` : "`main`";
  const run = meta.runUrl ? `, [the run](${meta.runUrl})` : "";
  const artifact = meta.artifactUrl ? `, its JSON in [the run's artifact](${meta.artifactUrl})` : "";
  head.push(`**Snyk, week of ${meta.week}.** ${commit}${run}${artifact}.`);
  if (meta.scanned?.length) head.push(`Scanned: ${meta.scanned.join(", ")}.`);
  head.push(`**${countLine(summary.counts)}**, each advisory counted once however many places it was found.`);
  if (summary.worst) {
    head.push(`${summary.fixable} of them can be fixed today: a fixed version exists, or the finding is in our own code.`);
  }
  if (errors.length) {
    head.push(
      `**Did not scan:** ${errors.map((e) => `${e.kind}${e.target ? ` (${e.target})` : ""}: ${cell(e.message)}`).join("; ")}.`,
    );
  }

  const lows = `**Low**, counted per source: ${KINDS.map((k) => `${k} ${summary.lowsByKind[k]}`).join(", ")}.`;
  const filed = rows.filter((r) => r.severity !== "low");
  const rendered = filed.map((r) => renderRow(r, meta));

  const assemble = (keep) => {
    const parts = [head.join("\n")];
    for (const severity of SEVERITIES.slice(0, 3)) {
      const idx = filed.map((r, i) => (r.severity === severity && i < keep ? i : -1)).filter((i) => i >= 0);
      if (!idx.length) continue;
      const label = severity[0].toUpperCase() + severity.slice(1);
      parts.push(`**${label}**\n\n${TABLE_HEAD}\n${idx.map((i) => rendered[i]).join("\n")}`);
    }
    if (keep < filed.length) {
      parts.push(
        `...and ${filed.length - keep} more rows, left out to fit here; the run's job summary has the whole table.`,
      );
    }
    parts.push(lows);
    return parts.join("\n\n");
  };

  let keep = filed.length;
  let text = assemble(keep);
  if (text.length > maxLength) {
    // Drop rows from the bottom until it fits: one pass over the row lengths, then one
    // re-render, rather than re-rendering once per dropped row.
    let excess = text.length - maxLength + 200;
    while (keep > 0 && excess > 0) {
      keep -= 1;
      excess -= rendered[keep].length + 1;
    }
    text = assemble(keep);
    while (keep > 0 && text.length > maxLength) text = assemble(--keep);
  }
  return { markdown: text, summary };
}

// Everything above in one call: the files of a scan directory, as [name, parsed JSON]
// pairs, into grouped rows and the errors the JSON itself reported.
export function buildReport(files) {
  const findings = [];
  const errors = [];
  const scanned = [];
  for (const [name, data] of files) {
    const source = sourceOf(name);
    if (!source) continue;
    const out = findingsFromFile(name, data);
    findings.push(...out.findings);
    errors.push(...out.errors);
    scanned.push(describeScan(source, data));
  }
  scanned.sort((a, b) => a.order - b.order || a.label.localeCompare(b.label));
  return { rows: groupFindings(findings), errors, scanned: scanned.map((s) => s.label) };
}

function describeScan(source, data) {
  const order = KINDS.indexOf(source.kind);
  if (source.kind === "image") return { order, label: `image ${source.target}` };
  if (source.kind === "code") return { order, label: "Snyk Code" };
  if (source.kind === "python") return { order, label: "Python deps from uv.lock" };
  const projects = projectsOf(data).length;
  return { order, label: `pnpm workspace (${projects} ${projects === 1 ? "project" : "projects"})` };
}
