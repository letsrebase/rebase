#!/usr/bin/env node
// Files the weekly Snyk report (REB-525): reads the JSON the scans wrote, builds one
// markdown table (report.mjs) and files it on Linear and GitHub (filing.mjs). No AI and
// no dependencies: Node 22's fetch is the only client.
//
// Usage, from the repository root:
//   node tooling/snyk-weekly/snyk-weekly.mjs --dir <scan dir> [--dry-run]
//     [--repo letsrebase/rebase] [--sha <commit>] [--run-url <url>] [--artifact-url <url>]
//     [--week YYYY-MM-DD]
//
// Environment: GITHUB_TOKEN (issues: write), LINEAR_API_KEY (a personal API key of the
// workspace), and in Actions the usual GITHUB_* variables, which fill in the defaults.
// A dry run reads both trackers when it has the keys and writes nothing.

import { readFileSync, readdirSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { buildReport, renderReport, priorityFor, titleFor, weekOf, headline, TITLE_PREFIX } from "./report.mjs";
import { fileReport, GITHUB_MARKER, LINEAR_MARKER, REPORT_MAX, parseGithubLink, parseLinearLink } from "./filing.mjs";

// Team `rebase` (docs/tracker.md). Its labels are resolved by name at run time: `security`
// in the `type` group, `area:ci` in `Area`, `parallel` on its own.
const LINEAR_TEAM_ID = "b72b55a3-ba12-412f-a5a8-ab8f94079de8";
const LINEAR_LABELS = [
  { name: "security", group: "type" },
  { name: "parallel", group: null },
  { name: "area:ci", group: "area" },
];
const USER_AGENT = "letsrebase-snyk-weekly";

function parseArgs(argv) {
  const env = process.env;
  const out = {
    dryRun: false,
    repo: env.GITHUB_REPOSITORY ?? "letsrebase/rebase",
    sha: env.GITHUB_SHA ?? null,
    runUrl: env.GITHUB_RUN_ID
      ? `${env.GITHUB_SERVER_URL ?? "https://github.com"}/${env.GITHUB_REPOSITORY}/actions/runs/${env.GITHUB_RUN_ID}`
      : null,
    artifactUrl: null,
    week: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${a} needs a value`);
      return v;
    };
    switch (a) {
      case "--dir": out.dir = value(); break;
      case "--dry-run": out.dryRun = true; break;
      case "--repo": out.repo = value(); break;
      case "--sha": out.sha = value(); break;
      case "--run-url": out.runUrl = value(); break;
      case "--artifact-url": out.artifactUrl = value() || null; break;
      case "--week": out.week = value(); break;
      default: throw new Error(`Unknown argument: ${a}`);
    }
  }
  if (!out.dir) throw new Error("--dir is required");
  return out;
}

function readScans(dir) {
  return readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => [name, JSON.parse(readFileSync(join(dir, name), "utf8"))]);
}

async function request(url, init, what) {
  const res = await fetch(url, { ...init, headers: { "User-Agent": USER_AGENT, ...init.headers } });
  const text = await res.text();
  if (!res.ok) throw new Error(`${what}: HTTP ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

export function githubClient({ token, repo }) {
  const api = `https://api.github.com/repos/${repo}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
  const call = (path, method, body, what) =>
    request(`${api}${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined }, what);
  return {
    async findOpen() {
      const open = [];
      for (let page = 1; page <= 10; page++) {
        const batch = await call(`/issues?state=open&labels=security&per_page=100&page=${page}`, "GET", null, "listing open security issues");
        open.push(...batch.filter((i) => !i.pull_request && (i.body ?? "").includes(GITHUB_MARKER)));
        if (batch.length < 100) break;
      }
      if (!open.length) return null;
      const latest = open.sort((a, b) => b.number - a.number)[0];
      return { number: latest.number, url: latest.html_url, body: latest.body ?? "", linearIdentifier: parseLinearLink(latest.body) };
    },
    async ensureLabel() {
      const res = await fetch(`${api}/labels/security`, { headers: { "User-Agent": USER_AGENT, ...headers } });
      if (res.ok) return;
      if (res.status !== 404) throw new Error(`reading the security label: HTTP ${res.status}`);
      await call("/labels", "POST", { name: "security", color: "c92a2a", description: "Filed by the weekly Snyk scan" }, "creating the security label");
    },
    async create({ title, body }) {
      const issue = await call("/issues", "POST", { title, body, labels: ["security"] }, "creating the issue");
      return { number: issue.number, url: issue.html_url };
    },
    async updateBody(number, body) {
      await call(`/issues/${number}`, "PATCH", { body }, `updating issue #${number}`);
    },
    async comment(number, body) {
      await call(`/issues/${number}/comments`, "POST", { body }, `commenting on issue #${number}`);
    },
  };
}

export function linearClient({ apiKey, teamId, repo }) {
  // A personal API key goes in the header as it is, without `Bearer`.
  const gql = async (query, variables, what) => {
    const res = await request(
      "https://api.linear.app/graphql",
      { method: "POST", headers: { Authorization: apiKey, "Content-Type": "application/json" }, body: JSON.stringify({ query, variables }) },
      what,
    );
    if (res.errors?.length) throw new Error(res.errors.map((e) => e.message).join("; "));
    return res.data;
  };

  async function labelIds() {
    const data = await gql(
      `query($names: [String!]) { issueLabels(first: 100, filter: { name: { in: $names } }) { nodes { id name isGroup team { id } parent { name } } } }`,
      { names: LINEAR_LABELS.map((l) => l.name) },
      "resolving the labels",
    );
    return LINEAR_LABELS.map(({ name, group }) => {
      const match = data.issueLabels.nodes.find(
        (l) =>
          l.name === name &&
          !l.isGroup &&
          (!l.team || l.team.id === teamId) &&
          (group ? l.parent?.name?.toLowerCase() === group : !l.parent),
      );
      if (!match) throw new Error(`no label "${name}"${group ? ` in the group "${group}"` : ""} on the team`);
      return match.id;
    });
  }

  async function todoState() {
    const data = await gql(
      `query($teamId: String!) { team(id: $teamId) { states { nodes { id name type } } } }`,
      { teamId },
      "reading the workflow states",
    );
    const states = data.team.states.nodes;
    const todo = states.find((s) => s.name === "Todo") ?? states.find((s) => s.type === "unstarted");
    if (!todo) throw new Error("the team has no Todo state");
    return todo.id;
  }

  const CLOSED = ["completed", "canceled"];
  const withLink = (card) => ({ ...card, githubNumber: parseGithubLink(card.description, repo) });

  return {
    // The card the open GitHub issue names, when it is still open; otherwise the newest
    // open card of the team that carries the marker, the `security` label and the
    // weekly title, so a card that only quotes the marker (one about this workflow,
    // say) is never taken for last week's.
    async findOpen({ identifier = null } = {}) {
      if (identifier) {
        try {
          const data = await gql(
            `query($id: String!) { issue(id: $id) { id identifier url description priority createdAt state { type } } }`,
            { id: identifier },
            `reading ${identifier}`,
          );
          const card = data.issue;
          if (card && !CLOSED.includes(card.state.type)) return withLink(card);
        } catch {
          // Deleted, or not readable with this key: the search below decides.
        }
      }
      const data = await gql(
        `query($teamId: ID!, $marker: String!, $prefix: String!, $closed: [String!]) {
          issues(first: 20, filter: {
            team: { id: { eq: $teamId } },
            description: { contains: $marker },
            title: { startsWith: $prefix },
            labels: { name: { eq: "security" } },
            state: { type: { nin: $closed } }
          }) { nodes { id identifier url description priority createdAt } }
        }`,
        { teamId, marker: LINEAR_MARKER, prefix: TITLE_PREFIX, closed: CLOSED },
        "looking up the open card",
      );
      const nodes = data.issues.nodes;
      if (!nodes.length) return null;
      return withLink(nodes.sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]);
    },
    async create({ title, description, priority }) {
      const [ids, stateId] = await Promise.all([labelIds(), todoState()]);
      const data = await gql(
        `mutation($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier url } } }`,
        { input: { teamId, title, description, priority, labelIds: ids, stateId } },
        "creating the card",
      );
      if (!data.issueCreate.success) throw new Error("issueCreate answered success: false");
      return data.issueCreate.issue;
    },
    async update(id, description) {
      const data = await gql(
        `mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }`,
        { id, input: { description } },
        "updating the card",
      );
      if (!data.issueUpdate.success) throw new Error("issueUpdate answered success: false");
    },
    async setPriority(id, priority) {
      const data = await gql(
        `mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }`,
        { id, input: { priority } },
        "raising the priority",
      );
      if (!data.issueUpdate.success) throw new Error("issueUpdate answered success: false");
    },
    async comment(issueId, body) {
      const data = await gql(
        `mutation($input: CommentCreateInput!) { commentCreate(input: $input) { success } }`,
        { input: { issueId, body } },
        "commenting on the card",
      );
      if (!data.commentCreate.success) throw new Error("commentCreate answered success: false");
    },
  };
}

// A workflow command ends at the first newline, and `%` starts an escape.
function annotation(text) {
  return String(text).replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const log = (line) => console.log(line);
  const { rows, errors, scanned } = buildReport(readScans(args.dir));
  const week = args.week ?? weekOf(new Date());
  const meta = { week, repo: args.repo, sha: args.sha, runUrl: args.runUrl, artifactUrl: args.artifactUrl, scanned };

  // The whole table goes to the job summary, which has no practical limit; what is
  // filed is cut to fit GitHub's.
  const full = renderReport({ rows, errors, meta });
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${full.markdown}\n`);
  const { markdown: report, summary } = renderReport({ rows, errors, meta, maxLength: REPORT_MAX });

  log(`Snyk, week of ${week}: ${headline(summary)}${summary.worst ? "" : " (nothing to file)"}.`);
  log("----- report -----");
  log(report);
  log("----- filing -----");

  const githubToken = process.env.GITHUB_TOKEN;
  if (!githubToken && !args.dryRun) throw new Error("GITHUB_TOKEN is not set");
  const linearKey = process.env.LINEAR_API_KEY;
  const result = await fileReport({
    title: titleFor(week),
    report,
    summary,
    priority: priorityFor(summary.worst),
    scanErrors: errors.length,
    repo: args.repo,
    github: githubToken ? githubClient({ token: githubToken, repo: args.repo }) : null,
    linear: linearKey ? linearClient({ apiKey: linearKey, teamId: LINEAR_TEAM_ID, repo: args.repo }) : null,
    dryRun: args.dryRun,
    log,
  });

  // A scan that reported an error inside its JSON, or a Linear step that failed, turns
  // the job red after everything that could be filed was.
  const failures = [...result.failures, ...errors.map((e) => `scan ${e.kind}${e.target ? ` (${e.target})` : ""}: ${e.message}`)];
  for (const f of failures) console.log(`::error title=snyk-weekly::${annotation(f)}`);
  if (failures.length) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => {
    console.error(`::error title=snyk-weekly::${annotation(e.message)}`);
    process.exit(1);
  });
}
