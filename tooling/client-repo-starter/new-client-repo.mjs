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
  const out = { worktree: true, greptile: false, coderabbit: false, force: false, createRepo: false };
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
      case "--coderabbit": out.coderabbit = true; break;
      case "--no-coderabbit": out.coderabbit = false; break;
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
  ? "work in a git worktree of its own (`git worktree add -b <branch> ../<repo>-<name> origin/main`), never directly in the shared checkout: more than one person can be committing to this repository at once, and a worktree is what keeps your commit yours."
  : "a plain branch checkout is fine here (`git checkout -b <branch> origin/main`); this contract has one person committing at a time, so nothing needs a worktree's isolation: revisit if that changes.";

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
      "   worktree's isolation: revisit if that changes.",
    ].join("\n");

const REVIEW_GATE_POLICY = args.greptile && args.coderabbit
  ? "Greptile and CodeRabbit both review every PR here (each already configured on this repository): Greptile first, then CodeRabbit's own review, then CodeRabbit checks Greptile's findings against the code. Every finding from either, and every adversarial verdict, gets fixed or answered before merging."
  : args.greptile
  ? "Greptile reviews every PR here (app.greptile.com is already configured on this repository): wait for its check run and its review on the sha you want to merge, fix or reply to every finding, and do not merge under a full-score review or with a finding neither fixed nor answered."
  : args.coderabbit
  ? "CodeRabbit reviews every PR here (app.coderabbit.ai is already configured on this repository, .coderabbit.yaml): wait for its review on the sha you want to merge, and fix or reply to every finding before merging."
  : "No automated second reviewer is configured on this repository yet. A PR merges once CI is green and, when the change is non-trivial, a fresh read of the diff (your own second pass, or another agent's) has not turned up something CI cannot catch.";

const GREPTILE_LOOP_LINES = [
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
  "      --jq '.check_runs[] | select(.name == \"Greptile Review\" and .status == \"completed\" and (.conclusion == \"success\" or .conclusion == \"failure\")) | \"\\(.conclusion)\\t\\(.output.summary)\"')",
  "  rid=$(gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/reviews \\",
  "      --jq \".[] | select(.user.login == \\\"greptile-apps[bot]\\\" and .commit_id == \\\"$sha\\\") | .id\" | tail -n 1)",
  "  [ -n \"$run$rid\" ] && break; sleep 30",
  "done",
  "[ -n \"$rid\" ] && gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments \\",
  "    --jq \".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \\\"\\(.id) \\(.path):\\(.line // .original_line) \\(.body)\\\"\"",
  "echo \"$run\" | cut -f2-",
  "{ gh pr view <n> --json body -q .body",
  "  gh api repos/" + args.org + "/" + args.repo + "/issues/<n>/comments \\",
  "      --jq '.[] | select(.user.login == \"greptile-apps[bot]\") | .body'; } \\",
  "  | grep -oE 'Confidence Score: [0-9]/5' | tail -n 1",
  "if [ \"$(echo \"$run\" | head -n1 | cut -f1)\" = success ]; then echo \"check run passes on $sha\"",
  "elif [ -z \"$run\" ] && [ -n \"$rid\" ]; then echo \"no check run for $sha, only a review: judge from the score and findings above\"",
  "else echo \"check run does not pass on $sha yet\"; fi",
  "```",
  "",
  "Nothing printed after a completed run means it is not there yet: read again",
  "before going on. Each finding is either **fixed**, in a commit that names it, or",
  "**answered**, with a reply on its thread (`gh api",
  "repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments/<id>/replies -F body=@file`: never `-f`",
  "with a backtick in the body, since bash reads it as a command substitution). An",
  "answered finding still counts against the score until the thread is resolved (`gh api",
  "graphql -f query='mutation { resolveReviewThread(input:{threadId:\"<id>\"}) {",
  "thread { isResolved } } }'`, the id from the PR's `reviewThreads`) and",
  "`@greptileai` is commented once after. After a fix, push, wait for the run on",
  "the new sha, read again: the check run's own `conclusion` decides it when a check",
  "run exists (the same signal the merge gate itself reads, sha-scoped, so a push",
  "can't leave it stale); the score line above is what to judge from on the rarer",
  "run that leaves only a review with no check run. The loop ends when the check run",
  "on the sha that will merge reads `success`, or, on a review-only run, when the",
  "score reads full marks. Ten minutes with no run on the head sha: comment",
  "`@greptileai` once, which re-triggers it, and wait again.",
];

const CODERABBIT_LOOP_LINES = [
  "",
  "**Then wait for CodeRabbit's review and act on every finding.** It reviews the PR",
  "once it is open and again on every push (settings in `.coderabbit.yaml`): a",
  "`CodeRabbit` commit status (`Review in progress`, then `Review completed`, or",
  "`Review skipped: ...` with the reason, a draft PR among them), inline findings",
  "when it found something (a review by `coderabbitai[bot]` whose body opens",
  "`Actionable comments posted: N`; that body can also carry `Outside diff range`",
  "and `Nitpick` items that are not inline comments, so read it whole), and one",
  "summary comment on the PR, edited in place on every round, with a `Merge Risk`",
  "line and a pre-merge checks table; a check listed under `Failed checks` is a",
  "finding too. A clean run leaves only the status and the summary.",
  "",
  "```bash",
  "sha=$(git rev-parse HEAD)",
  "for i in $(seq 20); do   # ten minutes, then @coderabbitai review",
  "  st=$(gh api \"repos/" + args.org + "/" + args.repo + "/commits/$sha/statuses\" \\",
  "      --jq '[.[] | select(.context == \"CodeRabbit\")][0].description // \"\"')",
  "  [ \"$st\" = \"Review completed\" ] && break; sleep 30",
  "done",
  "echo \"$st\"",
  "crid=$(gh api --paginate \"repos/" + args.org + "/" + args.repo + "/pulls/<n>/reviews?per_page=100\" \\",
  "    --jq \".[] | select(.user.login == \\\"coderabbitai[bot]\\\" and .commit_id == \\\"$sha\\\" and (.body | test(\\\"Actionable comments posted\\\"))) | .id\" | tail -n 1)",
  "[ -n \"$crid\" ] && gh api repos/" + args.org + "/" + args.repo + "/pulls/<n>/reviews/$crid --jq .body",
  "[ -n \"$crid\" ] && gh api --paginate \"repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments?per_page=100\" \\",
  "    --jq \".[] | select(.pull_request_review_id == $crid and .in_reply_to_id == null) | \\\"\\(.id) \\(.path):\\(.line // .original_line) \\(.body)\\\"\"",
  "```",
  "",
  "A missing `crid` after a completed run means that round raised nothing, not that",
  "the review is late. Each finding is either **fixed**, in a commit that names it, or",
  "**answered**: an inline one on its thread (`gh api",
  "repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments/<id>/replies -F body=@file`), one with no",
  "thread (an `Outside diff range` or `Nitpick` item, a failed pre-merge check) in one",
  "PR comment addressed to `@coderabbitai`, one line per item. The wait keys on",
  "`Review completed` alone: `draft pull request` means it waits for the PR to be",
  "marked ready, and no status, or a summary saying the reviews are paused (it pauses",
  "itself after five reviewed commits on one PR, to spare the hourly allowance), is",
  "`gh pr comment <n> --body '@coderabbitai review'`, then wait again. When it answers",
  "that it is rate limited instead of reviewing, wait for the window it names.",
];

const ADVERSARIAL_LOOP_LINES = [
  "",
  "**Two reviewers read every push, and CodeRabbit then reads Greptile.** Greptile",
  "and CodeRabbit both start by themselves when the PR opens and again on every push.",
  "Each sha goes through the same round: Greptile's review (a), CodeRabbit's own",
  "review (b), then CodeRabbit against Greptile (c), then every finding fixed or",
  "answered (d). Run (a) and (b) as background jobs next to the CI watch; (c) waits",
  "for both.",
  "",
  "**a. Greptile.** It reviews the PR once",
  ...GREPTILE_LOOP_LINES.slice(2),
  "",
  "**b. CodeRabbit, its own review.** It reviews the PR",
  ...CODERABBIT_LOOP_LINES.slice(2),
  "",
  "**c. CodeRabbit against Greptile.** It comes after CodeRabbit's own review on",
  "purpose: its own findings are not shaped by Greptile's, and then it checks",
  "Greptile's against the code. A pass is due when (a) and (b) are both done for the",
  "sha and Greptile raised a finding on it, or you answered a Greptile finding since",
  "the last pass. Until the verdict is in, reply to none of Greptile's threads: with",
  "`chat.auto_reply` on, CodeRabbit answers replies in any thread by itself, which can",
  "resolve a Greptile thread before any pass was asked for. One PR comment hands it",
  "Greptile's side and asks for a verdict on each item:",
  "",
  "```bash",
  "sha=$(git rev-parse HEAD); short=$(git rev-parse --short=9 HEAD)",
  "rid=$(gh api --paginate \"repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments?per_page=100\" \\",
  "    --jq \".[] | select(.user.login == \\\"greptile-apps[bot]\\\" and .original_commit_id == \\\"$sha\\\" and .in_reply_to_id == null) | .pull_request_review_id\" | tail -n 1)",
  "{ echo \"@coderabbitai Adversarial pass on Greptile's review of $short. You have reviewed this commit on your own; now act as Greptile's adversary. For each item below, check the claim against the code at $short yourself and answer **confirmed**, **refuted** or **partly**, with the evidence: file and line, or the script you ran and what it printed. Do not take Greptile's reasoning or mine on trust, and do not agree to be agreeable: a finding that does not hold is refuted, and an answer of mine that does not hold is wrong. Then list any defect in this diff that neither review raised. Start your reply with the line \\`Adversarial verdict on $short\\`, then one line per item, then the misses.\"",
  "  echo; echo \"Greptile's findings on $short:\"",
  "  [ -n \"$rid\" ] && gh api --paginate \"repos/" + args.org + "/" + args.repo + "/pulls/<n>/comments?per_page=100\" \\",
  "      --jq \".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \\\"- \\(.html_url) \\(.path):\\(.line // .original_line) \\(.body | split(\\\"<details>\\\")[0] | gsub(\\\"<[^>]*>\\\"; \\\"\\\") | gsub(\\\"\\\\\\\\s+\\\"; \\\" \\\"))\\\"\"",
  "} > adversary.md",
  "```",
  "",
  "Each line carries the finding's link, its `path:line` and the whole finding up to",
  "Greptile's own fix prompt. Then add by hand, under `Greptile findings I answered",
  "since the last pass:`, one line per answer: the thread's link and the answer in a",
  "sentence. When Greptile raised nothing new and there is no new answer, there is no",
  "pass. Post it and wait for the reply, a new comment by the bot that opens with the",
  "line it was asked for:",
  "",
  "```bash",
  "short=$(git rev-parse --short=9 HEAD); since=$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "gh pr comment <n> --body-file adversary.md",
  "for i in $(seq 40); do   # twenty minutes",
  "  verdict=$(gh api --paginate \"repos/" + args.org + "/" + args.repo + "/issues/<n>/comments?per_page=100\" \\",
  "      --jq \".[] | select(.user.login == \\\"coderabbitai[bot]\\\" and .created_at >= \\\"$since\\\" and any(.body | split(\\\"\\n\\\")[]; test(\\\"^\\\\\\\\s*Adversarial verdict on $short\\\\\\\\s*\\$\\\"))) | .body\")",
  "  [ -n \"$verdict\" ] && break; sleep 30",
  "done",
  "printf '%s\\n' \"$verdict\"",
  "```",
  "",
  "The verdict line is matched as a line of its own, wherever it sits: a quote of the",
  "request (`> ...`) does not match. Read it through: every item listed has a verdict;",
  "one without is asked for again in a reply to the verdict. Twenty minutes with no",
  "verdict: post the same comment once more and wait again; still nothing, say so on",
  "the card and tell the person before you merge, as with a Greptile that does not",
  "review.",
  "",
  "**d. What each finding and verdict does.** Every finding, from either reviewer, is",
  "either **fixed**, in a commit that names it, or **answered** with the reason the",
  "code stays as it is. An inline finding is answered on its thread. One with no",
  "thread (an `Outside diff range` or `Nitpick` item, a failed pre-merge check, a miss",
  "the adversarial pass named) is answered in one PR comment addressed to",
  "`@coderabbitai`, one line per item. The adversarial verdict decides which way each",
  "Greptile finding goes:",
  "",
  "- **Confirmed**: fix it. Read the code first all the same: a confirmation is a",
  "  second opinion, not proof.",
  "- **Refuted**, and the evidence holds when you check it yourself: answer on",
  "  Greptile's thread with that evidence and a link to CodeRabbit's verdict.",
  "- **Partly**: fix the part that holds, answer the rest.",
  "- **An answer of yours called wrong**: it is a finding again; fix it, or answer",
  "  with what the verdict missed.",
  "- **A miss** CodeRabbit names: a finding like any other.",
  "- **A refutation you do not accept**, or one Greptile answers by raising the",
  "  finding again: the two reviewers disagree, and that is a decision for whoever",
  "  leads this contract, as a `**Decision for the lead**` line on the card. The PR",
  "  does not merge over it.",
  "",
  "An answered Greptile finding still counts against the score until Greptile reads",
  "the thread as closed: resolve it (`gh api graphql -f query='mutation {",
  "resolveReviewThread(input:{threadId:\"<id>\"}) { thread { isResolved } } }'`, the id",
  "from the PR's `reviewThreads`) and comment `@greptileai` once after. A Greptile",
  "thread CodeRabbit resolved is neither a verdict nor Greptile closing it: the",
  "`@greptileai` still goes out. CodeRabbit resolves its own thread when it accepts an",
  "answer; one it keeps open after your answer is a disagreement for the card, like a",
  "finding raised again after an answer, which is not closed by repeating the answer.",
  "After a fix, push, and the round starts again on the new sha. Before the merge,",
  "list the threads still open (the author is `greptile-apps` or `coderabbitai`",
  "here, without `[bot]`):",
  "",
  "```bash",
  "gh api graphql --paginate -f query='query($endCursor: String) { repository(owner: \"" + args.org + "\", name: \"" + args.repo + "\") { pullRequest(number: <n>) { reviewThreads(first: 100, after: $endCursor) { pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { nodes { author { login } url } } } } } } }' \\",
  "  --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved | not) | \"\\(.comments.nodes[0].author.login) \\(.comments.nodes[0].url)\"' \\",
  "  || echo \"thread query failed: the gate is not met\"",
  "```",
  "",
  "**The loop ends** on the sha that will merge when: Greptile's run on it raised",
  "nothing new and its check run is `success`; CodeRabbit's status on it reads",
  "`Review completed`, its review of it raised nothing new and none of its pre-merge",
  "checks failed without an answer; the command above prints nothing; every verdict of",
  "the last adversarial pass was acted on; and no `**Decision for the lead**` is open.",
];

const REVIEWER_BLOCK = args.greptile && args.coderabbit
  ? ADVERSARIAL_LOOP_LINES.join("\n")
  : args.greptile
  ? GREPTILE_LOOP_LINES.join("\n")
  : args.coderabbit
  ? CODERABBIT_LOOP_LINES.join("\n")
  : "";

const REVIEWER_MERGE_CLAUSE = args.greptile && args.coderabbit
  ? " and Greptile reads full marks and CodeRabbit's review has nothing open"
  : args.greptile
  ? " and Greptile reads full marks"
  : args.coderabbit
  ? " and CodeRabbit's review has nothing open"
  : "";

const CODERABBIT_YAML = `# yaml-language-server: $schema=https://coderabbit.ai/integrations/schema.v2.json
#
# CodeRabbit's review of this repository. Reads AGENTS.md and CLAUDE.md as review
# guidelines; nothing here repeats a rule already stated there.

language: en-US

reviews:
  profile: chill
  high_level_summary_in_walkthrough: true
  in_progress_fortune: false
  auto_review:
    enabled: true
    drafts: false
  finishing_touches:
    docstrings:
      enabled: false
  pre_merge_checks:
    title:
      mode: warning
      requirements: >-
        Conventional Commits, as .claude/skills/pr-creation § Title says:
        \`type(scope): subject\`. \`type\` is one of feat, fix, refactor, perf, test,
        docs, ci, chore, style, revert. The subject starts with a lowercase letter
        and has no Title Case.
    custom_checks:
      - name: PR body follows the template
        mode: warning
        instructions: >-
          Read the PR description, leaving out every block a bot generated. Pass
          when what remains contains the headings \`## What this changes\`, \`## How
          I verified it\` and \`## Screenshots and video\`, in that order, and its
          last non-empty line is exactly \`Linear: ${args.linearPrefix}-<number>.\`. Otherwise fail,
          naming each missing heading or quoting the last line. Pass without
          checking when the PR was opened by a bot account.

knowledge_base:
  linear:
    usage: enabled
    team_keys:
      - ${args.linearPrefix}

chat:
  auto_reply: true
  art: false
`;

// Line-level substitutions: a line whose trimmed content is exactly the key is
// replaced wholesale (and dropped entirely when the value is empty).
const BLOCK_SUBSTITUTIONS = {
  "{{WORKTREE_BLOCK}}": WORKTREE_BLOCK,
  "{{GREPTILE_BLOCK}}": REVIEWER_BLOCK,
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
  "{{GREPTILE_MERGE_CLAUSE}}": REVIEWER_MERGE_CLAUSE,
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

if (args.coderabbit) {
  const dest = join(targetDir, ".coderabbit.yaml");
  if (existsSync(dest) && !args.force) {
    skipped.push(".coderabbit.yaml");
  } else {
    writeFileSync(dest, CODERABBIT_YAML);
    written.push(".coderabbit.yaml");
  }
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

// ---- add the repository to the shared self-hosted runner pool ----------
// Every private client repository is eligible for private-clients (see
// docs/ci-runner-pool.md); this is the automatic half of that integration,
// scoping the repository into the runner group so its own CI workflow can
// move to runs-on: [self-hosted, linux, x64] whenever it is written.

function ghJson(cmdArgs) {
  return JSON.parse(execFileSync("gh", cmdArgs, { encoding: "utf8" }));
}

function addToRunnerPool(org, repo) {
  let groupId;
  try {
    const groups = ghJson(["api", `orgs/${org}/actions/runner-groups`]).runner_groups;
    const group = groups.find((g) => g.name === "private-clients");
    if (!group) {
      console.log("No 'private-clients' runner group found on this org; skipping pool provisioning (see docs/ci-runner-pool.md).");
      return false;
    }
    groupId = group.id;
  } catch {
    console.log("Could not query the org's runner groups (no admin:org scope on this token?); skipping pool provisioning.");
    return false;
  }
  let repoId;
  try {
    repoId = ghJson(["api", `repos/${org}/${repo}`]).id;
  } catch {
    console.log(`${org}/${repo} is not on GitHub yet; add it to the private-clients runner group once it is (docs/ci-runner-pool.md).`);
    return false;
  }
  if (!args.createRepo && !retrofitRemoteMatches(targetDir, org, repo)) {
    console.log(`${targetDir}'s own git remote does not point at ${org}/${repo}; skipping pool provisioning rather than scoping the wrong repository (check --org/--repo against the checkout's remote, then add it by hand if this was intentional).`);
    return false;
  }
  try {
    execFileSync("gh", ["api", "-X", "PUT", `orgs/${org}/actions/runner-groups/${groupId}/repositories/${repoId}`]);
  } catch (err) {
    console.log(`Could not add ${org}/${repo} to the private-clients runner group (${err.message}); scope it by hand (docs/ci-runner-pool.md).`);
    return false;
  }
  console.log(`Added ${org}/${repo} to the private-clients runner group: its own CI workflow can use runs-on: [self-hosted, linux, x64] whenever it is written.`);
  return true;
}

function retrofitRemoteMatches(dir, org, repo) {
  let remote;
  try {
    remote = execFileSync("git", ["-C", dir, "remote", "get-url", "origin"], { encoding: "utf8" }).trim();
  } catch {
    return false;
  }
  return new RegExp(`[:/]${org}/${repo}(\\.git)?$`).test(remote);
}

const runnerPoolReady = addToRunnerPool(args.org, args.repo);

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
5. Once this repository has its own CI workflow, set runs-on: [self-hosted, linux, x64]
   on its jobs; ${runnerPoolReady
     ? `${args.org}/${args.repo} is already scoped into the private-clients runner group, so the workflow file is the only remaining step.`
     : `it still needs to be scoped into the private-clients runner group first (docs/ci-runner-pool.md) -- not done automatically this run, see the note above.`}
`);
