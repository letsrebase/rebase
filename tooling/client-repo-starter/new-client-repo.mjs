#!/usr/bin/env node
// Scaffolds a client repository from tooling/client-repo-starter/template/,
// resolving its placeholders and, optionally, creating the GitHub repository.
// Dependency-free on purpose: this is a scaffolding tool, not a package.
//
// Usage:
//   node tooling/client-repo-starter/new-client-repo.mjs \
//     --repo point --org letsrebase \
//     --project-name POINT --one-liner "padel and tennis session recording platform" \
//     --linear-team Point --linear-prefix POINT \
//     --initiative POINT --linear-project "MVP delivery" \
//     --create-repo --worktree --no-greptile
//
// Retrofitting an existing checkout instead of creating a repo:
//   node tooling/client-repo-starter/new-client-repo.mjs \
//     --repo point --target-dir ../point \
//     --project-name POINT --one-liner "..." \
//     --linear-team Point --linear-prefix POINT \
//     --initiative POINT --linear-project "MVP delivery"
//
// --linear-team names the Linear team this client gets on its own (one team per
// client, never shared): on the Free plan there is only one such slot alongside
// "rebase" itself, so check no other client already holds it first.

import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const HERE = dirname(fileURLToPath(import.meta.url));
const TEMPLATE_DIR = join(HERE, "template");

function parseArgs(argv) {
  const out = { worktree: true, greptile: false, force: false, createRepo: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--repo": out.repo = argv[++i]; break;
      case "--org": out.org = argv[++i]; break;
      case "--target-dir": out.targetDir = argv[++i]; break;
      case "--project-name": out.projectName = argv[++i]; break;
      case "--one-liner": out.oneLiner = argv[++i]; break;
      case "--linear-team": out.linearTeam = argv[++i]; break;
      case "--linear-prefix": out.linearPrefix = argv[++i]; break;
      case "--initiative": out.initiative = argv[++i]; break;
      case "--linear-project": out.linearProject = argv[++i]; break;
      case "--worktree": out.worktree = true; break;
      case "--no-worktree": out.worktree = false; break;
      case "--greptile": out.greptile = true; break;
      case "--no-greptile": out.greptile = false; break;
      case "--force": out.force = true; break;
      case "--create-repo": out.createRepo = true; break;
      default:
        console.error(`Unknown argument: ${a}`);
        process.exit(1);
    }
  }
  return out;
}

function fail(msg) {
  console.error(`error: ${msg}`);
  process.exit(1);
}

const args = parseArgs(process.argv.slice(2));

if (!args.repo) fail("--repo is required");
if (!args.projectName) fail("--project-name is required");
if (!args.oneLiner) fail("--one-liner is required");
if (!args.linearTeam) fail("--linear-team is required (one Linear team per client, never a shared default)");
if (!args.linearPrefix) fail("--linear-prefix is required");
if (!args.initiative) fail("--initiative is required");
if (!args.linearProject) fail("--linear-project is required");
if (args.createRepo && args.targetDir) fail("--create-repo and --target-dir are mutually exclusive");
if (!args.createRepo && !args.targetDir) fail("give --target-dir (retrofit) or --create-repo (new repository)");

args.org = args.org || "letsrebase";
args.linearPrefix = args.linearPrefix.toUpperCase();

const targetDir = args.createRepo ? join(process.cwd(), args.repo) : args.targetDir;

// ---- placeholder values -----------------------------------------------

const WORKTREE_POLICY = args.worktree
  ? "work in a git worktree of its own (`git worktree add -b <branch> ../<repo>-<name> origin/main`), never directly in the shared checkout — more than one person can be committing to this repository at once, and a worktree is what keeps your commit yours."
  : "a plain branch checkout is fine here (`git checkout -b <branch> origin/main`); this contract has one person committing at a time, so nothing needs a worktree's isolation — revisit if that changes.";

const WORKTREE_BLOCK = args.worktree
  ? [
      "4. **Work in a git worktree of its own**, never the shared checkout: other",
      "   sessions write to the same index, and a worktree is what keeps your commit",
      "   yours.",
      "",
      "   ```bash",
      "   git fetch origin",
      "   git worktree add -b <branch> ../<repo>-<name> origin/main",
      "   cd ../<repo>-<name>",
      "   ```",
      "",
      "   Remove it once the PR merges (`git worktree remove ../<repo>-<name>`); never",
      "   remove a worktree with uncommitted work still in it.",
    ].join("\n")
  : [
      "4. **A plain branch checkout is fine here**: `git checkout -b <branch> origin/main`.",
      "   This contract has one person committing at a time, so nothing needs a",
      "   worktree's isolation — revisit if that changes.",
    ].join("\n");

const REVIEW_GATE_POLICY = args.greptile
  ? "Greptile reviews every PR here (app.greptile.com is already configured on this repository): wait for its check run and its review on the sha you want to merge, fix or reply to every finding, and do not merge under a full-score review or with a finding neither fixed nor answered."
  : "No automated second reviewer is configured on this repository yet. A PR merges once CI is green and, when the change is non-trivial, a fresh read of the diff (your own second pass, or another agent's) has not turned up something CI cannot catch.";

const GREPTILE_BLOCK = args.greptile
  ? [
      "",
      "**Then iterate on Greptile until it scores full marks.** It reviews the PR once",
      "it is open: inline findings with a severity badge, and a summary headed",
      "`Confidence Score: N/5`, on the PR's own description or as a bot comment,",
      "depending on this repository's Greptile setting (PR Summaries, in",
      "app.greptile.com). Read both. A run shows on the commit as the `Greptile",
      "Review` check run, and, when there are findings, as a review by the bot too;",
      "a clean run can leave only the check run, and a run can leave only the",
      "review, so wait for either, as a background job next to the CI watch:",
      "",
      "```bash",
      "sha=$(git rev-parse HEAD)",
      "for i in $(seq 20); do   # ten minutes, then the @greptileai nudge below",
      "  run=$(gh api \"repos/" + args.org + "/" + args.repo + "/commits/$sha/check-runs\" \\",
      "      --jq '.check_runs[] | select(.name == \"Greptile Review\" and .status == \"completed\") | .output.summary')",
      "  rid=$(gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/reviews \\",
      "      --jq \".[] | select(.user.login == \\\"greptile-apps[bot]\\\" and .commit_id == \\\"$sha\\\") | .id\" | tail -n 1)",
      "  [ -n \"$run$rid\" ] && break; sleep 30",
      "done",
      "n=$(echo \"$run\" | grep -oE '[0-9]+ comments' | grep -oE '[0-9]+')",
      "for i in $(seq 12); do   # the check run counted findings: the review is on its way",
      "  { [ \"${n:-0}\" = 0 ] || [ -n \"$rid\" ]; } && break; sleep 10",
      "  rid=$(gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/reviews \\",
      "      --jq \".[] | select(.user.login == \\\"greptile-apps[bot]\\\" and .commit_id == \\\"$sha\\\") | .id\" | tail -n 1)",
      "done",
      "[ -n \"$rid\" ] && gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments \\",
      "    --jq \".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \\\"\\(.id) \\(.path):\\(.line // .original_line) \\(.body)\\\"\"",
      "{ gh pr view <n> --json body -q .body",
      "  gh api repos/" + args.org + "/" + args.repo + "/issues/<n>/comments \\",
      "      --jq '.[] | select(.user.login == \"greptile-apps[bot]\") | .body'; } \\",
      "  | grep -oE 'Confidence Score: [0-9]/5' | tail -n 1",
      "```",
      "",
      "Nothing printed after a completed run means the summary is not there yet, not a",
      "score of zero: read again before going on. Each finding is either **fixed**, in",
      "a commit that names it, or **answered**, with a reply on its thread (`gh api",
      "repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments/<id>/replies -F body=@file`: never `-f`",
      "with a backtick in the body, since bash reads it as a command substitution). An",
      "answered finding still counts against the score until the thread is resolved (`gh api",
      "graphql -f query='mutation { resolveReviewThread(input:{threadId:\"<id>\"}) {",
      "thread { isResolved } } }'`, the id from the PR's `reviewThreads`) and",
      "`@greptileai` is commented once after. After a fix, push, wait for the run on",
      "the new sha, read again. The loop ends when the run on the sha that will merge",
      "raises nothing new and the score reads full marks; the PR does not merge",
      "before that. Ten minutes with no run on the head sha: comment `@greptileai`",
      "once, which re-triggers it, and wait again.",
    ].join("\n")
  : "";

const GREPTILE_MERGE_CLAUSE = args.greptile ? " and Greptile reads full marks" : "";

// Line-level substitutions: a line whose trimmed content is exactly the key is
// replaced wholesale (and dropped entirely when the value is empty).
const BLOCK_SUBSTITUTIONS = {
  "{{WORKTREE_BLOCK}}": WORKTREE_BLOCK,
  "{{GREPTILE_BLOCK}}": GREPTILE_BLOCK,
};

// Inline substitutions: replaced wherever they occur inside a line.
const INLINE_SUBSTITUTIONS = {
  "{{PROJECT_NAME}}": args.projectName,
  "{{ONE_LINER}}": args.oneLiner,
  "{{LINEAR_TEAM}}": args.linearTeam,
  "{{LINEAR_PREFIX}}": args.linearPrefix,
  "{{LINEAR_INITIATIVE}}": args.initiative,
  "{{LINEAR_PROJECT}}": args.linearProject,
  "{{WORKTREE_POLICY}}": WORKTREE_POLICY,
  "{{REVIEW_GATE_POLICY}}": REVIEW_GATE_POLICY,
  "{{GREPTILE_MERGE_CLAUSE}}": GREPTILE_MERGE_CLAUSE,
};

// ---- walk the template, resolve placeholders, write into the target ----

function listFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...listFiles(full));
    else out.push(full);
  }
  return out;
}

function resolveFile(text) {
  const lines = text.split("\n");
  const result = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed in BLOCK_SUBSTITUTIONS) {
      const value = BLOCK_SUBSTITUTIONS[trimmed];
      if (value !== "") result.push(value);
      continue; // an empty block drops the line entirely
    }
    let resolved = line;
    for (const [key, value] of Object.entries(INLINE_SUBSTITUTIONS)) {
      resolved = resolved.split(key).join(value);
    }
    result.push(resolved);
  }
  return result.join("\n");
}

mkdirSync(targetDir, { recursive: true });

const written = [];
const skipped = [];
for (const src of listFiles(TEMPLATE_DIR)) {
  const rel = relative(TEMPLATE_DIR, src);
  const dest = join(targetDir, rel);
  if (existsSync(dest) && !args.force) {
    skipped.push(rel);
    continue;
  }
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, resolveFile(readFileSync(src, "utf8")));
  written.push(rel);
}

console.log(`Wrote ${written.length} file(s) into ${targetDir}:`);
for (const f of written) console.log(`  ${f}`);
if (skipped.length) {
  console.log(`Left ${skipped.length} existing file(s) alone (pass --force to overwrite):`);
  for (const f of skipped) console.log(`  ${f}`);
}

// ---- optionally create the GitHub repository ---------------------------

if (args.createRepo) {
  const run = (cmd, cmdArgs, opts = {}) => execFileSync(cmd, cmdArgs, { cwd: targetDir, stdio: "inherit", ...opts });
  const hasGit = existsSync(join(targetDir, ".git"));
  if (!hasGit) run("git", ["init"]);
  run("git", ["add", "-A"]);
  try {
    run("git", ["commit", "-m", "chore(repo): scaffold from client-repo-starter"]);
  } catch {
    console.log("Nothing to commit (scaffold matched an existing checkout exactly).");
  }
  run("gh", [
    "repo", "create", `${args.org}/${args.repo}`,
    "--private", "--source=.", "--remote=origin", "--push",
  ]);
  console.log(`Created https://github.com/${args.org}/${args.repo} and pushed the scaffold.`);
}

// ---- the checklist this script cannot do for you ------------------------

console.log(`
Next, by hand or with an agent holding the linear-rebase MCP server:

1. If the "${args.linearTeam}" Linear team does not exist yet in linear.app/letsrebase,
   create it (Settings -> Teams -> New team). Linear's MCP surface has no
   team-creation call. Once per client, never shared: check no other client already
   holds the Free plan's second and last team slot before creating a new one -- a
   second client's team needs a Business-plan upgrade first.
2. Create (or confirm) the "${args.initiative}" initiative and the "${args.linearProject}"
   project inside team "${args.linearTeam}", with a lead and every member involved
   (save_project). Add the first milestone(s) if the contract's phases are known.
3. Invite the freelancer: GitHub collaborator on ${args.org}/${args.repo}, Linear
   member on linear.app/letsrebase. Tell them, in the same message, that this
   workspace is on the Free plan (AGENTS.md § Tracker: Linear § Known limitation):
   they will see rebase's own internal roadmap too, not just this team.
4. Point them at the repository's own AGENTS.md.
`);
