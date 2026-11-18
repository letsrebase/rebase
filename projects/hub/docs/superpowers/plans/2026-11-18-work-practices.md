# Work Practices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The work profile carries a `practices` block: for each practice on a closed list, in how many sessions it was seen out of how many it could have been seen in, across how many products and months, with the seven code-counted practices shipping first and the six model-classified ones only after the quality gate.

**Architecture:** The fork (`letsrebase/rebase-verify`) counts seven practices deterministically from the normalised sessions (`src/practices.mjs`) and publishes the list as `share/practices.json`; a later, separately gated step has the person's own model classify six more from the person's own messages. The hub vendors `share/practices.json`, rechecks the block's structure in `work_profiles/practices.py`, drops and counts unknown ids (`unknown_practices`, migration 0060), and shows the rows, each with its method mark, to the admin and the person. Nothing reads a practice in the cloud, the card, the narrative or any `ORDER BY`/`WHERE`.

**Tech Stack:** fork: Node ESM >= 20, `node --test`, no runtime dependencies. Hub: Python (pytest, ruff, mypy, SQLAlchemy 2, Alembic, Pydantic), React + vitest (`pnpm` package `hub`).

**Spec:** `projects/hub/docs/superpowers/specs/2026-11-18-work-practices-in-the-work-profile-design.md` (monorepo worktree `/home/dev/.cache/tmp/wt-1142`). Executors read it with this plan; it is the contract.

## Repos, branches, release order

| Part | Card | Repo | Branch (from the card) | Commit style |
|---|---|---|---|---|
| A (tasks A1 to A4) | REB-1143 | Fork `/home/dev/projects/letsrebase/rebase-verify`, a worktree cut from the fork's `main` (`upstream/main` has no `src/tech-usage.mjs`, which the block follows) | `fiorelorenzo/reb-1143-count-seven-work-practices-from-tool` | Conventional Commits, last line `REB-1143.` |
| B (B0 to B5) | REB-1144 | Monorepo, a worktree cut from `origin/main` | `fiorelorenzo/reb-1144-recheck-and-show-the-practices-block` | Conventional Commits, scope `hub`, `REB-1144.` as the footer |
| C (C1) | REB-1145 | Monorepo, on the REB-1144 branch so the texts ship with the views | same as B | `REB-1145.` as the footer |
| D (D1 to D6) | REB-1146 | Fork, then monorepo | `fiorelorenzo/reb-1146-classify-six-practices-with-the` | `REB-1146.` |
| E (E1) | REB-1147 | `Play-New/apply-new` issue | none | upstream's style |

Wherever a later task's commit block says `REB-1142.`, use the card of its part from this table.

Order: Part A (fork, code practices), Part B (hub), Part C (texts, Lorenzo's approval), then the release (**the hub tag needs Lorenzo's explicit ok**), then Part D (model practices, gated on a written reference set, Lorenzo's choice of 2026-11-18), then the upstream issue. Part D can follow in a later release; Parts A to C never depend on it.

## Global Constraints

- Everything in English except product strings (Italian). No em dashes. Conventional Commits.
- Rubric id: `rebase-practices/v1`. Timezone of the block: the profile's (`tz`), the same as `techUsage.timezone`.
- No adjective, no score, no percentage, no total across practices, no sort/filter/threshold in the hub reads a practice. Nothing about tone, feelings, politeness, language level or personality.
- Practices are **not** in `CloudTalentRead`, the card source text (`work_profiles/source.py`), `talent_schemas`, the talent list, the narrative input, or any `ORDER BY`/`WHERE`.
- The unit is the session as the adapters define it. A practice whose denominator is 0 is omitted. An adapter that cannot show a practice is left out of its denominator, never counted «not seen».
- Seven code practices (id, Italian hub label, denominator):
  - `checks_before_commit`, «Verifica prima del commit», sessions with a commit
  - `tests_with_changes`, «Test insieme al codice», sessions that edited a source file
  - `failure_reproduced_before_fix`, «Riproduce il problema prima di correggerlo», sessions where a check command failed
  - `plan_before_edit`, «Piano prima delle modifiche», sessions with an edit
  - `reviews_changes`, «Rilettura delle modifiche prima del commit», sessions with a commit or a pull request
  - `design_documents`, «Scrive documenti di progetto», sessions with an edit
  - `security_checks`, «Controlli di sicurezza», sessions with an edit
- Six model practices (Part D only): `asks_for_alternatives` «Chiede alternative»; `pushes_back_with_reason` «Corregge l'agente motivando»; `states_done_criteria` «Dice quando il lavoro è finito»; `gives_context_and_constraints` «Dà contesto e vincoli»; `raises_risks` «Considera i rischi»; `limits_scope` «Delimita il lavoro». Denominator: sessions classified. Their `method` is `"model"`; the code ones `"code"`.
- Path rules (identical in code and `share/practices.json`): test files by `test/`, `tests/`, `__tests__/`, `spec/`, `*.test.*`, `*.spec.*`, `*_test.*`, `test_*.py`; source = code extension, not test, documentation or configuration. Design documents: under `adr/`, `docs/adr/`, `docs/design/`, `docs/specs/`, `docs/rfc/`, `rfcs/` or any `specs/` directory; files `ADR-*`, `*-adr.md`, `RFC*`, `*-design.md`, `*-spec.md`, `DESIGN.md`. Plan files: under any `plans/` directory; files `PLAN.md`, `*-plan.md`, `plan.md`; a spec file counts as a plan too (spec § 2a; an ADR or other design document does not).
- Check vocabulary (one exported, tested list, minimum): pytest, ruff, mypy, go test, go vet, cargo test, cargo clippy, mvn test, gradle test, dotnet test, phpunit, rspec, tsc, eslint, vitest, jest, playwright, and build commands (`npm run build`, `pnpm build`, `yarn build`, `cargo build`, `go build`, `make`-less). Security: `npm audit`, `pnpm audit`, `yarn audit`, `pip-audit`, `trivy`, `semgrep`, `gitleaks`, `bandit`, `cargo audit`, `govulncheck`.
- Diff display: `git diff`, `git show`, `gh pr diff`. `cmd` is capped at 240 chars by every adapter, so a check at the end of a long chain can be missed; the published definition says so. `reviews_changes` says the diff appeared in the session, not that anyone read it.
- Model classification (Part D): up to 60 sessions, only sessions with >= 2 messages from the person, round-robin across products then months; person's messages only; at most 12 per session (the first, then evenly spaced); each cut to 600 characters; through `redactText` and `scrubProperNouns`; written to `out/practices-input.json`; output per session six booleans, nothing else; per-session answers to `out/practices-detail.json` (never sent).
- Hub recheck: rubric known; `method` equals the vendored one; `0 <= seen <= of`; `of <= volume.sessions`, and `of <= 60` for a model practice; `products <= volume.products` and `products <= seen`; `months <= seen`, `months >= 1` when `seen >= 1`, months no more than the window spans; no id twice; refusals name the practice and the rule, in Italian. Unknown id: dropped and counted in `work_profiles.unknown_practices` (migration 0060), one muted line to the admin.
- Marks, verbatim: «contato dal codice» (code), «dal modello della persona, non ricontrollato» (model). View copy: «in 18 sessioni su 25», products, months; section title «Come lavora». No bar, no colour scale, no total. Existing tokens and components only.
- Quality gate thresholds (Part D): stability >= 90% per practice (two runs, same 40 real sessions, never committed); agreement >= 90% per practice with a written reference set (>= 8 positive and >= 8 near-miss sessions per practice, Italian and English, expected answers fixed when written, checked by a blind second reader; Lorenzo chose this on 2026-11-18 and does not review the set). A practice failing its checks is left out of v1.
- The frozen fixture test (`test/fixture.test.mjs`) passes unmodified. The block is optional and nullable like `techUsage`.
- Fork texts, the privacy page and the DPIA section need Lorenzo's approval before merge. The hub tag needs his explicit ok.

## Review Focus

1. A session with no timestamps, no messages or a missing `toolUses`/`toolResults` array (hand-built bundles, pi, cursor): must not throw and must count as not in the denominator when the field is missing. Pinned in Task A2.
2. Windows paths (`C:\proj\tests\a.test.ts`) and redacted `⟨user⟩` paths in the path rules: classified like POSIX. Pinned in Task A1.
3. A command chain truncated at 240 chars, a `git commit` inside `&&` chains, a check run via `npx`/`uv run`/`python -m`: recognised where visible, never throws. Pinned in Task A1/A2.
4. A hub payload with `practices` not an object, `items` not a list, a bool or float for `seen`, an id twice, an unknown id mixed with known ones: refusal in Italian naming the rule for malformed, silent drop+count for unknown only. Pinned in Task B1/B2.
5. A stored profile from before a rule change, or with the block `null`: the views show no section and never 500. Pinned in Task B3.

---

# Part A: Fork, the code practices (card 1)

All commands from `/home/dev/projects/letsrebase/rebase-verify`. Branch: `fiorelorenzo/reb-1142-code-practices`.

### Task A1: Rules, vocabulary and the published list

**Files:**
- Create: `src/practices-rules.mjs`, `scripts/emit-practices.mjs`, `share/practices.json`, `test/practices-rules.test.mjs`, `test/practices-artifact.test.mjs`
- Modify: `package.json` (script `"practices": "node scripts/emit-practices.mjs"`)

**Interfaces:**
- Produces (`src/practices-rules.mjs`):
  - `export const RUBRIC = "rebase-practices/v1"`
  - `export const CHECK_PATTERNS: Array<{ id: string, re: RegExp }>` and `export const SECURITY_PATTERNS`, `export const DIFF_PATTERNS` (same shape)
  - `export function isCheckCommand(cmd: string): boolean`; `isSecurityCommand(cmd)`; `isDiffCommand(cmd)`; `isCommitCommand(cmd)` (`git commit` as the executable of a top-level sub-command)
  - `export function isTestPath(path: string): boolean`; `isSourcePath(path)`; `isDesignDocPath(path)`; `isPlanPath(path)` (all treat `\` as `/`)
  - `export const CODE_PRACTICES: Array<{ id, method: "code", definition: string, denominator: string, needs: string[] }>` in list order; `needs` names session fields (`"toolUses.cmd"`, `"toolUses.path"`, `"toolResults.isError"`)
  - `export const ADAPTERS_WITHOUT: { failure_reproduced_before_fix: string[] }` = `["pi", "cursor"]`
  - `export function practicesArtifact(): { rubric: string, practices: Array<{ id, method, definition, denominator }>, rules: { testPaths: string[], designPaths: string[], planPaths: string[], checks: string[], security: string[], diff: string[] } }`
- Reuses `splitTopLevel` from `src/digest.mjs` (export it; no other change to digest).

- [ ] **Step 1: Write the failing tests** (`test/practices-rules.test.mjs`)

```js
test("isTestPath", () => {
  for (const p of ["test/a.mjs", "src/__tests__/a.ts", "spec/x.rb", "a.test.ts", "a.spec.js", "foo_test.go", "pkg/test_x.py", "C:\\proj\\tests\\a.ts"]) assert.equal(isTestPath(p), true, p);
  for (const p of ["src/testing-utils.ts", "contest.py", "src/a.ts"]) assert.equal(isTestPath(p), false, p);
});
test("isSourcePath: code extension, not test, doc or config", () => {
  assert.equal(isSourcePath("src/a.ts"), true);
  assert.equal(isSourcePath("src/a.test.ts"), false);
  assert.equal(isSourcePath("README.md"), false);
  assert.equal(isSourcePath("package.json"), false);
  assert.equal(isSourcePath("⟨user⟩\\proj\\src\\a.py"), true);
});
test("design and plan paths", () => {
  for (const p of ["docs/adr/0001-x.md", "a/rfcs/1.md", "x/specs/y.md", "docs/design/z.md", "ADR-003.md", "foo-adr.md", "RFC12.md", "a-design.md", "a-spec.md", "DESIGN.md"]) assert.equal(isDesignDocPath(p), true, p);
  for (const p of ["docs/plans/a.md", "PLAN.md", "a-plan.md", "plan.md", "docs/specs/s.md"]) assert.equal(isPlanPath(p), true, p);
  assert.equal(isDesignDocPath("src/design.ts"), false);
  assert.equal(isPlanPath("src/plant.ts"), false);
});
test("check vocabulary covers every ecosystem the stack detection knows", () => {
  for (const c of ["pytest -q", "uv run pytest", "python -m pytest", "ruff check .", "mypy src", "go test ./...", "go vet ./...", "cargo test", "cargo clippy", "mvn test", "gradle test", "./gradlew test", "dotnet test", "phpunit", "rspec", "tsc --noEmit", "npx eslint .", "vitest run", "jest", "playwright test", "npm run build", "pnpm build"]) assert.equal(isCheckCommand(c), true, c);
  for (const c of ["cat pytest-notes.md", "echo jest", "git status", "ls tests"]) assert.equal(isCheckCommand(c), false, c);
});
test("security, diff and commit commands", () => {
  for (const c of ["npm audit", "pip-audit", "trivy fs .", "semgrep --config auto", "gitleaks detect", "govulncheck ./..."]) assert.equal(isSecurityCommand(c), true, c);
  for (const c of ["git diff", "git diff --staged", "git show HEAD", "gh pr diff 12"]) assert.equal(isDiffCommand(c), true, c);
  assert.equal(isCommitCommand("git add . && git commit -m 'x'"), true);
  assert.equal(isCommitCommand("echo git commit"), false);
});
```

`test/practices-artifact.test.mjs`: `share/practices.json` deep-equals `practicesArtifact()` (message: "run `npm run practices`"); it lists exactly the seven code ids in order; every entry has non-empty `definition`; `reviews_changes.definition` contains "appeared in the session"; the rules lists equal the exported patterns' sources.

- [ ] **Step 2: Run** `node --test test/practices-rules.test.mjs test/practices-artifact.test.mjs`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the signatures above in `src/practices-rules.mjs`; `scripts/emit-practices.mjs` writes `practicesArtifact()` as sorted-key pretty JSON to `share/practices.json` (mirror `scripts/emit-tech-lexicon.mjs`). Anchor command patterns to a sub-command executable position (split with `splitTopLevel`, strip leading env assignments, allow `npx`/`uv run`/`python -m`/`pnpm exec` prefixes), so `cat pytest-notes.md` does not match. Model practices are **not** in the file in this task.
- [ ] **Step 4: Run** `npm run practices && node --test test/practices-rules.test.mjs test/practices-artifact.test.mjs`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/practices-rules.mjs src/digest.mjs scripts/emit-practices.mjs share/practices.json test/practices-*.test.mjs package.json
git commit -m "feat(practices): publish the list of code practices and the rules behind it" -m "REB-1142."
```

### Task A2: Counting the seven practices

**Files:**
- Create: `src/practices.mjs`, `test/practices.test.mjs`
- Modify: `src/digest.mjs` (export `repoKey` and `isEphemeral`, the filter `buildDigest` uses, so the practices sessions are exactly the digest's)

**Interfaces:**
- Consumes (A1): `isCheckCommand`, `isSecurityCommand`, `isDiffCommand`, `isCommitCommand`, `isTestPath`, `isSourcePath`, `isDesignDocPath`, `isPlanPath`, `CODE_PRACTICES`, `ADAPTERS_WITHOUT`, `RUBRIC`.
- Produces:
  - `export function normalizeCommand(cmd: string): string`
  - `export function sessionFacts(session): { edits: number, sourceEdits: number, testEdits: number, commits: number, prs: number, failedCommands: number, seen: Record<PracticeId, boolean>, denom: Record<PracticeId, boolean> }` (pure, one session)
  - `export function computeCodePractices(parsed, { tz = DEFAULT_TZ } = {}): { items: PracticeItem[] } | null` where `PracticeItem = { id, method: "code", seen, of, products, months }`, in `CODE_PRACTICES` order, denominators of 0 omitted, `null` when no item remains. `products` = distinct `repoKey` of sessions where seen; `months` = distinct `monthKeyFor(tz)` of the first timestamped message of those sessions (sessions with none do not add a month).
  - `export function recordedPracticesTz(profile): string` (as `recordedTechUsageTz`)
  - Internal: `PracticeSession = { id: string, product: string, month: string | null, source: string }` list exported as `export function practiceSessions(parsed, { tz }): PracticeSession[]` (non-ephemeral, same filter as `buildDigest`); Part D reuses it.

**Seen rules** (message order is the event order; an event is a tool use with the result looked up by `forId`): Edit = tool name in `Edit|Write|NotebookEdit|MultiEdit` (add the names the adapters map to; `apply_patch` paths map to `Edit`).
- `checks_before_commit`: for some commit at event i, a check ran after the last edit before i (or anywhere before i when no edit precedes). Denominator: commit present.
- `tests_with_changes`: a `isSourcePath` edit and a `isTestPath` edit. Denominator: a source edit.
- `failure_reproduced_before_fix`: see the algorithm below. Denominator: a failed check command (a Bash command with `isError === true` that is a check); sessions whose `source` is in `ADAPTERS_WITHOUT.failure_reproduced_before_fix` are never in the denominator.
- `plan_before_edit`: before the first edit, a tool named `ExitPlanMode` or `TodoWrite`, or an edit/write to an `isPlanPath` file. Denominator: an edit exists.
- `reviews_changes`: before a commit or a `gh pr create`, a diff command ran. Denominator: commit or `gh pr create` present.
- `design_documents`: an edit to an `isDesignDocPath` file. Denominator: an edit exists.
- `security_checks`: any `isSecurityCommand`. Denominator: an edit exists.
- A practice whose needed field is missing in the session (no `toolUses` array, no `cmd`, `toolResults` absent for the failure rule) is not in its denominator.

```js
// failure_reproduced_before_fix: the algorithm the tests do not determine.
export function normalizeCommand(cmd) {
  // 1. take the first sub-command that is a check (splitTopLevel), strip leading env assignments;
  // 2. tokens = whitespace-split; drop every token after the first token that looks like a path
  //    (contains "/" or "\\" or ends in a code extension) and every flag after it;
  // 3. lower-case the runner, join with single spaces.
  // "pytest tests/a.py -q" and "pytest" and "pytest tests/ -x" -> "pytest".
}
// For each failed command f (isError true, normalised key k, event index i): seen when an edit
// exists at index j > i and a later command with the same key k succeeds (isError false) at index > j.
```

- [ ] **Step 1: Write the failing tests** (`test/practices.test.mjs`, a local `session(source, events)` helper building messages like `sess` in `test/factories.mjs`; events `{ tool, path, cmd, err }`)

```js
test("normalizeCommand", () => {
  assert.equal(normalizeCommand("pytest tests/a.py -q"), "pytest");
  assert.equal(normalizeCommand("pytest"), "pytest");
  assert.equal(normalizeCommand("FOO=1  npx   vitest run test/a.test.ts"), "npx vitest run");
  assert.equal(normalizeCommand("cd x && go test ./pkg/..."), "go test");
});
test("checks_before_commit: seen only when the check ran after the last edit", () => {
  const ok = [edit("src/a.ts"), bash("npm test"), bash("git commit -m x")];
  const stale = [bash("npm test"), edit("src/a.ts"), bash("git commit -m x")];
  assert.equal(sessionFacts(session("claude-code", ok)).seen.checks_before_commit, true);
  assert.equal(sessionFacts(session("claude-code", stale)).seen.checks_before_commit, false);
  assert.equal(sessionFacts(session("claude-code", [edit("a.ts")])).denom.checks_before_commit, false);
});
test("tests_with_changes", () => { /* src + test edit -> seen; src only -> denom true, seen false; test only -> denom false */ });
test("failure_reproduced_before_fix", () => {
  const s = [bash("pytest tests/a.py", { err: true }), edit("src/a.py"), bash("pytest", { err: false })];
  assert.equal(sessionFacts(session("claude-code", s)).seen.failure_reproduced_before_fix, true);
  assert.equal(sessionFacts(session("claude-code", [bash("pytest", { err: true }), bash("pytest", { err: false })])).seen.failure_reproduced_before_fix, false);
});
test("pi and cursor sessions are not in the failure denominator, claude-code and codex are", () => {
  for (const src of ["pi", "cursor"]) assert.equal(sessionFacts(session(src, [bash("pytest", { err: true })])).denom.failure_reproduced_before_fix, false);
  for (const src of ["claude-code", "codex", "opencode", "kimi"]) assert.equal(sessionFacts(session(src, [bash("pytest", { err: true })])).denom.failure_reproduced_before_fix, true);
});
test("plan_before_edit: plan file, ExitPlanMode or TodoWrite before the first edit", () => { /* three seen cases, one edit-first not-seen case */ });
test("reviews_changes: git diff, git show or gh pr diff before commit or gh pr create", () => { /* seen, not seen, denom via gh pr create */ });
test("design_documents and security_checks: denominator is sessions with an edit", () => { /* docs/adr/1.md seen; npm audit seen; a session with no edit has neither denominator */ });
test("computeCodePractices counts seen, of, products and months in the tz and omits denominator 0", () => {
  // 3 sessions in 2 repos: Europe/Rome month boundary 2026-04-30T23:30Z -> "2026-05"
  assert.deepEqual(computeCodePractices(parsed, { tz: "Europe/Rome" }).items.find(i => i.id === "checks_before_commit"), { id: "checks_before_commit", method: "code", seen: 2, of: 3, products: 2, months: 2 });
  assert.equal(computeCodePractices({ sessions: [] }), null);
});
test("a session without toolUses, timestamps or messages never throws and leaves every denominator", () => {
  assert.doesNotThrow(() => computeCodePractices({ sessions: [{ source: "pi", cwdRedacted: "/h/p/x", messages: [{ role: "user", textRedacted: "hi" }] }, { source: "cursor", messages: [] }] }));
});
test("ephemeral sandboxes (/tmp/...) are not counted, so of never exceeds volume.sessions", () => { /* ... */ });
```

- [ ] **Step 2: Run** `node --test test/practices.test.mjs`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the signatures above in `src/practices.mjs` per the seen rules and the algorithm. `practiceSessions` uses `isEphemeral` and `repoKey` exported from `src/digest.mjs`.
- [ ] **Step 4: Run** `node --test test/practices.test.mjs test/digest.test.mjs`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src/practices.mjs src/digest.mjs test/practices.test.mjs
git commit -m "feat(practices): count the seven code practices per session" -m "REB-1142."
```

### Task A3: The block in the profile, its structure check, groundedness and markdown

**Files:**
- Modify: `src/profile.mjs` (`assembleProfile` takes `practices`, emits `practices: practices || null`, `renderMarkdown` section), `src/consistency.mjs` (`assessStructure`), `src/groundedness.mjs` (support pool), `bin/rebase-verify.mjs` (`loadProfileInputs` computes it; the three callers pass it through, like `techUsage`)
- Test: `test/practices-block.test.mjs`; `test/groundedness.test.mjs`, `test/consistency.test.mjs` (additions)

**Interfaces:**
- Consumes: `computeCodePractices`, `RUBRIC`, `CODE_PRACTICES`, `practicesArtifact` (A1, A2).
- Produces: `assembleProfile({ ..., practices })`: `profile.practices = { rubric: RUBRIC, timezone: tz, items }`; `computeCodePractices` returns `{ items }` and `loadProfileInputs` wraps it with rubric and timezone via `export function practicesBlock(items, { tz }): { rubric, timezone, items } | null` in `src/practices.mjs`.

- [ ] **Step 1: Write the failing tests**

```js
// test/practices-block.test.mjs
test("the block is absent on older profiles and the frozen fixture passes unmodified", () => { /* run npm-level: assessStructure(baseProfile()).issues is [] */ });
test("assessStructure refuses a malformed block, naming the practice and the rule", () => {
  const p = baseProfile();
  p.practices = { rubric: "rebase-practices/v1", timezone: "UTC", items: [{ id: "checks_before_commit", method: "code", seen: 5, of: 4, products: 1, months: 1 }] };
  assert.ok(assessStructure(p).issues.some((i) => /practices\.checks_before_commit/.test(i) && /seen/.test(i)));
});
test("assessStructure rules", () => {
  // each case mutates one field of a valid block and expects one issue:
  // rubric "other/v1"; method "model" for a code id; id unknown; id twice; of > volume.sessions (236);
  // products > volume.products (30); products > seen; months > seen; months 0 with seen 1;
  // months > months in the window (window 2026-01..2026-05 -> 5); a bool or 1.5 for seen;
  // items not a list; timezone invalid; timezone != intensity.timezone.
});
test("a model practice with of > 60 is refused", () => { /* id asks_for_alternatives with of 61 */ });
test("renderMarkdown prints 'in 18 sessions of 25' lines with the method, no total", () => { /* ... */ });
// test/groundedness.test.mjs: a narrative citing "18 of 25" and "4 months" is supported when practices has seen 18, of 25, months 4.
```

- [ ] **Step 2: Run** `node --test test/practices-block.test.mjs test/groundedness.test.mjs test/consistency.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the structure check in `assessStructure` after the `techUsage` block, with the same messages' style (`practices.<id>: ...`); the known ids and methods come from `practicesArtifact().practices` (so Part D's additions flow in); add `seen`, `of`, `products`, `months` to the support pool with `addNumber`. Wire `bin/rebase-verify.mjs`: `loadProfileInputs` returns `practices`, `cmdGenerate`/`cmdFinalize` pass it to `assembleWithGroundedness`. `submit` already sends the whole profile minus `repoLabel`; add a `submit.test.mjs` assertion that `practices` is in the dry-run payload.
- [ ] **Step 4: Run** `npm test`. Expected: all PASS, `test/fixture.test.mjs` unmodified.
- [ ] **Step 5: Commit**

```bash
git add src bin test
git commit -m "feat(practices): carry the practices block in the profile and check its structure" -m "REB-1142."
```

### Task A4: Fork texts for the code practices (Lorenzo approves before merge)

**Files:**
- Modify: `README.md` (section "Work practices": the list, the definitions, the path rules, `reviews_changes` caveat, the 240-char cap, adapters left out, counts only), `PRIVACY.md` (what is read: tool calls and file paths already read; what leaves: counts only), `.claude/commands/rebase-verify.md` (a line in the finalize step telling the person the profile now carries counts of practices and where to read them in `out/profile.md`)
- Test: `test/docs-consistency.test.mjs` (existing; extend)

- [ ] **Step 1: Write the failing test**: every id in `share/practices.json` appears in `README.md` with its definition's first sentence; `PRIVACY.md` contains "practices" and "counts"; no em dash in the three files.
- [ ] **Step 2: Run** `node --test test/docs-consistency.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Write the texts.** The README section is generated from the artifact by hand but the test pins the ids. State: nothing about tone or personality, no score, no total.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Hold for review.** Commit on the branch, open the PR, and **do not merge until Lorenzo approves the three texts**.

```bash
git add README.md PRIVACY.md .claude/commands/rebase-verify.md test/docs-consistency.test.mjs
git commit -m "docs(practices): tell a freelancer which practices are counted before they consent" -m "REB-1142."
```

---

# Part B: Hub (card 4), monorepo `/home/dev/.cache/tmp/wt-1142`

Commands from the monorepo root. Branch: the existing `fiorelorenzo/reb-1142-design-the-practices-block-of-the-work`.

### Task B0: Bring REB-1141 into the worktree

The spec says `drop_unknown` and the `unknown_technologies` column are merged before this work. The worktree is cut before that merge (see Self-review, contradiction 1).

- [ ] **Step 1:** `git fetch origin && git merge origin/main` in the worktree.
- [ ] **Step 2: Verify:** `git ls-files projects/hub/packages/core/migrations/versions | tail -1` prints `0059_work_profile_unknown_technologies.py`; `grep -n "def drop_unknown" projects/hub/packages/core/src/rebase_core/work_profiles/tech_usage.py` finds one hit.
- [ ] **Step 3:** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_tech_usage.py`. Expected: PASS.

### Task B1: Vendoring and the recheck

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/work_profiles/practices.json` (verbatim copy of the fork's `share/practices.json`), `projects/hub/packages/core/src/rebase_core/work_profiles/practices.py`, `projects/hub/packages/core/tests/test_work_profile_practices.py`
- Modify: `work_profiles/structure.py` (call `practices.issues(payload)` after `tech_usage.issues`), `work_profiles/VENDORED.txt` (paragraph for `practices.json`: fork commit, refresh command, test)

**Interfaces:**
- Consumes: the fork file from A1 (copy it from the merged fork commit; record that commit in `VENDORED.txt`).
- Produces (`work_profiles/practices.py`):
  - `RUBRIC = "rebase-practices/v1"`, `MAX_MODEL_SAMPLE = 60`
  - `def practice_methods() -> dict[str, str]` (id to `"code"`/`"model"`, from the vendored file, cached)
  - `PRACTICE_LABELS: dict[str, str]` (Italian labels of the seven code ids; Part D adds six)
  - `def issues(payload: dict[str, Any]) -> list[str]` (the recheck; unknown ids are no issue)
  - `def drop_unknown(payload: dict[str, Any]) -> int` (removes unknown ids from `practices.items` in place, returns how many; 0 when absent or malformed)
  - `def reads_true(item: Any) -> bool` (the view's guard on a stored row)

- [ ] **Step 1: Write the failing tests** (`test_work_profile_practices.py`, helper `_block(*items)` and `_profile(block)` over the existing `work_profile_helpers`)

```python
def test_a_sound_block_has_no_issue() -> None:
    assert issues(_profile(_item("checks_before_commit", seen=18, of=25, products=3, months=4))) == []
def test_absent_or_null_block_is_no_issue() -> None:
    assert issues({}) == [] and issues({"practices": None}) == []
@pytest.mark.parametrize("change, rule", [
    ({"seen": 26}, "seen"), ({"seen": -1}, "seen"), ({"seen": True}, "seen"), ({"seen": 1.5}, "seen"),
    ({"of": 237}, "of"), ({"products": 31}, "prodotti"), ({"products": 19}, "prodotti"),
    ({"months": 19}, "mesi"), ({"months": 0}, "mesi"), ({"months": 6}, "mesi"),
    ({"method": "model"}, "metodo"),
])
def test_each_rule_names_the_practice_and_the_rule_in_italian(change, rule) -> None:
    found = issues(_profile(_item("checks_before_commit", **{"seen": 18, "of": 25, "products": 18, "months": 4} | change)))
    assert found and "checks_before_commit" in found[0] and rule in found[0]
def test_rubric_timezone_and_shape() -> None: ...  # rubric "x/v1", bad IANA tz, tz != intensity.timezone, items not a list, block not an object
def test_an_id_twice_is_an_issue() -> None: ...
def test_months_cannot_exceed_the_windows_span() -> None: ...  # window 2026-01..2026-03, months 4
def test_a_model_practice_is_capped_at_60() -> None: ...  # needs a vendored model id: use monkeypatch on practice_methods
def test_an_unknown_id_is_no_issue_and_drop_unknown_removes_it_and_counts() -> None:
    profile = _profile(_item("checks_before_commit"), _item("acme_secret"), _item("Tests_With_Changes"))
    assert issues(profile) == []
    assert drop_unknown(profile) == 2
    assert [i["id"] for i in profile["practices"]["items"]] == ["checks_before_commit"]
    assert drop_unknown(profile) == 0 and drop_unknown({}) == 0
def test_vendored_file_matches_the_fork_list_and_every_id_has_an_italian_label() -> None:
    assert set(practice_methods()) == set(PRACTICE_LABELS)
def test_structure_refuses_a_bad_block(hub_session) -> None: ...  # via WorkProfileService.submit, message in Italian, nothing stored
```

- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** `practices.py` per the Interfaces (`seen`, `of`, `products`, `months` integers, not bool, via the same `_count` as `tech_usage`; months window from `payload["window"]` with `tech_usage.month_span`). Add the call in `structure.py`.
- [ ] **Step 4: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py projects/hub/packages/core/tests/test_work_profile_intake.py && uv run ruff check projects/hub && uv run mypy`. Expected: PASS, clean.
- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core
git commit -m "feat(hub): recheck the practices block of a work profile" -m "REB-1142."
```

### Task B2: Unknown practices dropped and counted (migration 0060)

**Files:**
- Create: `projects/hub/packages/core/migrations/versions/0060_work_profile_unknown_practices.py` (pattern of 0059: `revision = "0060"`, `down_revision = "0059"`, `ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS unknown_practices INTEGER NOT NULL DEFAULT 0`, downgrade `DROP COLUMN IF EXISTS`), `projects/hub/packages/core/tests/test_migration_0060_work_profile_unknown_practices.py` (copy the shape of the existing migration tests)
- Modify: `models/work_profiles.py` (`unknown_practices: Mapped[int]`, default 0, server_default `text("0")`), `work_profiles/intake.py` (after `drop_unknown(stored)` for technologies: `unknown_practices = practices.drop_unknown(stored)`; `row.unknown_practices = ...`; add to the read at the `unknown_technologies=row.unknown_technologies` site), `work_profiles/schemas.py` (`WorkProfileAdminRead.unknown_practices: int`), `AGENTS.md` of the hub (one sentence beside the REB-1141 one)
- Test: `test_work_profile_practices.py` (additions)

**Interfaces:**
- Consumes: `practices.drop_unknown` (B1).
- Produces: `WorkProfileAdminRead.unknown_practices: int`; `WorkProfile.unknown_practices`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_profile_with_an_unknown_practice_is_accepted_and_the_id_is_never_stored(hub_session) -> None:
    clock = Clock()
    freelancer_id = _submit(hub_session, clock, _profile(_item("checks_before_commit"), _item("acme_secret")), "ada@studio.it")
    row = hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).one()
    assert [i["id"] for i in row.payload["practices"]["items"]] == ["checks_before_commit"]
    assert "acme_secret" not in json.dumps(row.payload)
    assert row.unknown_practices == 1
    assert WorkProfileService(hub_session, settings(), clock).admin_read(freelancer_id).unknown_practices == 1
def test_a_resubmission_without_unknowns_resets_the_count(hub_session) -> None: ...
```

Migration test: upgrade 0059 to 0060 adds the column with default 0; downgrade drops it.

- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py projects/hub/packages/core/tests/test_migration_0060_work_profile_unknown_practices.py`. Expected: FAIL.
- [ ] **Step 3: Implement** as listed. A malformed block is refused by B1 before `drop_unknown` runs; only well-formed unknown ids reach the drop.
- [ ] **Step 4: Run** the same command, then `uv run pytest -q projects/hub/packages/core/tests/test_openapi_contract.py && uv run ruff check projects/hub && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add projects/hub
git commit -m "feat(hub): drop unknown practices from a work profile and count them" -m "REB-1142."
```

### Task B3: Rows for the admin and the person, and the groundedness port

**Files:**
- Modify: `work_profiles/view.py` (`WorkProfilePractice` model, `practice_rows`, `practices(payload)`, `render_profile` field `practices`), `member_schemas.py` (`WorkProfileMemberRead.practices`), `members.py` (fill like `technologies`, empty when expired or rejected), `work_profiles/groundedness.py` (support pool), `AGENTS.md` (one paragraph: not in cloud, card, narrative, `ORDER BY`)
- Test: `test_work_profile_practices.py`, `test_work_profile_groundedness.py`, `test_work_profile_member_cloud.py` (additions), `test_work_profile_card_source.py` (addition), `test_openapi_contract.py`

**Interfaces:**
- Consumes: `practices.PRACTICE_LABELS`, `practices.practice_methods`, `practices.reads_true` (B1).
- Produces:
  - `class WorkProfilePractice(ResponseModel)`: `id: str`, `label: str`, `method: Literal["code", "model"]`, `seen: int`, `of: int`, `products: int`, `months: int`. In `schema_common` style; `method` an alias in `rebase_core.vocabulary` built from `PRACTICE_METHODS = ("code", "model")` in `models`.
  - `def practice_rows(rows: Any) -> list[WorkProfilePractice]` in **list order** of the vendored file (not sorted by count), rows failing `reads_true` or listed twice are left out
  - `def practices(payload: dict[str, Any]) -> list[WorkProfilePractice]`
  - `WorkProfileView.practices: list[WorkProfilePractice]`, `WorkProfileMemberRead.practices: list[WorkProfilePractice]`; **not** added to `CloudTalentRead`, `talent_schemas`, `source.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_rows_follow_the_list_order_not_the_counts() -> None:
    rows = practice_rows([_item("security_checks", seen=20, of=20), _item("checks_before_commit", seen=1, of=5)])
    assert [r.id for r in rows] == ["checks_before_commit", "security_checks"]
    assert rows[0].label == "Verifica prima del commit" and rows[0].method == "code"
def test_a_row_the_intake_would_refuse_or_a_duplicate_is_left_out_and_nothing_raises() -> None:
    assert practice_rows([_item("checks_before_commit", seen=9, of=5), _item("nope"), "x", None]) == []
    assert practices({"practices": "x"}) == [] and practices({}) == []
def test_admin_read_and_member_read_carry_the_rows(hub_session) -> None: ...
def test_member_read_has_no_rows_when_expired_or_rejected(hub_session) -> None: ...
def test_practices_are_nowhere_a_company_or_the_card_reads() -> None:
    assert "practices" not in CloudTalentRead.model_fields
    assert "practices" not in WorkProfileCloudView.model_fields   # the view a company reads
    # card source text for a profile with a practices block equals that of the same profile without it
def test_no_query_orders_or_filters_by_a_practice() -> None: ...  # grep the sources of talents.py, work_profiles/*.py, members.py for "practices" next to order_by/where
def test_the_support_pool_holds_seen_of_products_months() -> None:
    profile = {**_profile(_item("checks_before_commit", seen=18, of=25, products=3, months=4))}
    assert assess_groundedness({**profile, "summary": "Verifica prima del commit in 18 sessioni su 25."}).anomalies == []
```

(The groundedness test uses the existing helper and numeric-anchor extraction in `test_work_profile_groundedness.py`; the fork's matching vector is in A3 and the two files share the sentence `18 of 25`.)

- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py projects/hub/packages/core/tests/test_work_profile_groundedness.py projects/hub/packages/core/tests/test_work_profile_member_cloud.py projects/hub/packages/core/tests/test_work_profile_card_source.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the signatures above. Pool: in `groundedness.py` beside the `techUsage` loop, add each item's `seen`, `of`, `products`, `months` as numbers, mirroring the fork's A3 change.
- [ ] **Step 4: Run** the same four files plus `uv run pytest -q projects/hub/packages/core/tests/test_openapi_contract.py`, then `uv run ruff check projects/hub && uv run mypy`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add projects/hub
git commit -m "feat(hub): read the practices rows for the admin and the person" -m "REB-1142."
```

### Task B4: The admin's «Come lavora» section and the muted line

**Files:**
- Modify: `projects/hub/apps/web/src/pages/admin/freelancer/WorkProfile.tsx` (a `PracticesPanel` after the technologies block; the muted `unknown_practices` line beside `unknown_technologies` at the existing `profile.unknown_technologies > 0` site), `projects/hub/apps/web/src/lib/api-types.ts` (regenerated)
- Create: `projects/hub/apps/web/src/components/Practices.tsx` (shared row list, used by B5), `projects/hub/apps/web/src/components/Practices.test.tsx`
- Test: `projects/hub/apps/web/src/pages/admin/freelancer/WorkProfile.test.tsx` (additions)

**Interfaces:**
- Consumes: `WorkProfilePractice`, `WorkProfileAdminRead.unknown_practices` from the regenerated types (B2, B3).
- Produces: `export function PracticeRows({ practices }: { practices: WorkProfilePractice[] }): JSX.Element | null` in `components/Practices.tsx` (null for an empty list); constants `MARK_CODE = 'contato dal codice'`, `MARK_MODEL = 'dal modello della persona, non ricontrollato'`.

- [ ] **Step 1:** `cd projects/hub/apps/web && pnpm run generate:api`; check `api-types.ts` has `practices` and `unknown_practices`.
- [ ] **Step 2: Write the failing tests**

```tsx
it('renders one row per practice in list order, with the label, the counts and the mark', () => {
  render(<PracticeRows practices={[row('checks_before_commit', 18, 25, 3, 4, 'code'), row('asks_for_alternatives', 11, 60, 4, 5, 'model')]} />)
  expect(screen.getByText('Verifica prima del commit')).toBeInTheDocument()
  expect(screen.getByText(/in 18 sessioni su 25/)).toBeInTheDocument()
  expect(screen.getByText(/3 prodotti/)).toBeInTheDocument()
  expect(screen.getByText(/4 mesi/)).toBeInTheDocument()
  expect(screen.getByText('contato dal codice')).toBeInTheDocument()
  expect(screen.getByText('dal modello della persona, non ricontrollato')).toBeInTheDocument()
})
it('has no bar, no meter and no total', () => { expect(screen.queryByRole('progressbar')).toBeNull(); expect(screen.queryByText(/totale/i)).toBeNull() })
it('renders nothing for an empty list', ...)
// WorkProfile.test.tsx
it('shows the «Come lavora» section when the profile has practices and hides it when not', ...)
it('shows one muted line for 1 and for 3 unknown practices (singular and plural), none for 0', ...)
```

- [ ] **Step 3: Run** `pnpm --filter hub exec vitest run Practices WorkProfile`. Expected: FAIL.
- [ ] **Step 4: Implement** the components with existing tokens only (copy the structure of `UsageBadges`/`TechUsage.tsx`); the unknown line copy: «1 pratica non riconosciuta da questa versione del hub è stata tolta dal profilo.» / «N pratiche non riconosciute da questa versione del hub sono state tolte dal profilo.». Run `pnpm --filter hub exec vitest run Practices WorkProfile`, then `pnpm --filter hub exec tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web
git commit -m "feat(hub): show how a freelancer works on the admin's review panel" -m "REB-1142."
```

### Task B5: The person's own view

**Files:**
- Modify: `projects/hub/apps/web/src/pages/member/WorkProfile.tsx` (a «Come lavora» section after technologies, `PracticeRows` plus a link to the published list: `https://github.com/letsrebase/rebase-verify/blob/main/README.md#work-practices`, the README anchor from A4)
- Test: `projects/hub/apps/web/src/pages/member/WorkProfile.test.tsx` (additions)

**Interfaces:** Consumes `PracticeRows`, `MARK_*` (B4), `WorkProfileMemberRead.practices` (B3).

- [ ] **Step 1: Write the failing tests**: the member page shows the same rows and marks; shows a link named «l'elenco delle pratiche» with that href; shows nothing when `practices` is empty; shows nothing when the profile is expired or rejected.
- [ ] **Step 2: Run** `pnpm --filter hub exec vitest run member/WorkProfile`. Expected: FAIL.
- [ ] **Step 3: Implement.** Reuse `PracticeRows` unchanged (the admin and the person see the same rows with the same marks).
- [ ] **Step 4: Run** `pnpm --filter hub exec vitest run member/WorkProfile && pnpm --filter hub exec tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web
git commit -m "feat(hub): show a freelancer the practices counted in their own profile" -m "REB-1142."
```

---

# Part C: Public text and DPIA (card 5, Lorenzo's approval), then the release

### Task C1: Privacy page paragraph and the DPIA section for the code practices

**Files:**
- Modify: `projects/website/src/privacy.html` (a paragraph under the work profile section: the profile carries counts of ways of working counted by code on the person's machine; only counts reach us), `projects/hub/docs/privacy/work-profile-dpia.md` (new section «Practices (REB-1142)» before «Sign-off»: processing, the code method, the denominators, adapters left out of a denominator, Cursor's coarse timestamps can shift a month at a boundary, `reviews_changes` meaning, residual risk and the marks)

- [ ] **Step 1: Write the check**: the DPIA has a heading starting `## Practices (REB-1142)`; the privacy page contains the paragraph; the DPIA names Cursor and the month boundary; `python3 tooling/english-lint/english_lint.py projects/hub/docs/privacy/work-profile-dpia.md` is clean.
- [ ] **Step 2: Run** `python3 tooling/english-lint/english_lint.py projects/hub/docs/privacy/work-profile-dpia.md`; and `grep -c "Practices (REB-1142)" projects/hub/docs/privacy/work-profile-dpia.md`. Expected: lint passes; grep prints `0` before the edit.
- [ ] **Step 3: Write both texts.** The privacy page is Italian public text.
- [ ] **Step 4: Run** the same commands. Expected: lint passes; grep prints `1`.
- [ ] **Step 5: Hold.** Commit and **do not merge until Lorenzo approves the privacy page paragraph and the DPIA section**.

```bash
git add projects/website/src/privacy.html projects/hub/docs/privacy/work-profile-dpia.md
git commit -m "docs(hub): say what the practices block counts, in the privacy page and the DPIA" -m "REB-1142."
```

### Task C2: Release (waiting for Lorenzo)

- [ ] **Step 1:** All three approvals are in (fork texts A4, privacy page and DPIA C1) and the fork PR is merged into the fork's `main`. Refresh `practices.json` in the hub from that merged commit if it moved, update `VENDORED.txt`, and re-run `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py`.
- [ ] **Step 2:** Merge the monorepo branch to `main` (preview deploys from `main`).
- [ ] **Step 3: Production.** **The tag `hub-v<next>` is cut only after Lorenzo's explicit ok.** Hub production ships with the next release, never before.

---

# Part D: Model practices (card 2, with their texts), gated on the written reference set

Parts A to C ship without Part D. Task D1 to D3 build the machinery and can be written now; the gate (D4) decides whether the six ids enter the published list, and D5 and D6 only run for the practices that pass. Branch: `fiorelorenzo/reb-1142-model-practices`, cut from the fork's `main` after A3.

### Task D1: The sampler and `practices-input.json`

**Files:**
- Create: `src/practices-sample.mjs`, `test/practices-sample.test.mjs`
- Modify: `src/profile-llm.mjs` (export `scrubProperNouns`), `bin/rebase-verify.mjs` (`prepare` and `generate` write `out/practices-input.json` and say so; the file is listed with `narrative-input.json` as deleted at the end)

**Interfaces:**
- Consumes: `practiceSessions` (A2), `redactText` (`src/redact.mjs`), `scrubProperNouns`.
- Produces: `export const MAX_SESSIONS = 60, MAX_MESSAGES = 12, MAX_CHARS = 600`; `export function sampleSessions(parsed, { tz }): Array<{ id: string, product: string, month: string, messages: string[] }>`; `export function buildPracticesInput(parsed, { tz }): { rubric: string, practices: Array<{ id, definition }>, sessions: Array<{ id: string, messages: string[] }> }` (no product or month in the file the model reads; they stay in the detail file).

```js
// The sampler: round-robin across products, then months inside each product.
// 1. eligible = practiceSessions(...) with >= 2 user messages (role "user", non-empty textRedacted).
// 2. group eligible by product, sort products by name; inside a product group by month, sort months ascending,
//    each (product, month) bucket keeps session order.
// 3. loop: for each product in order, pop the next session from its next non-empty month bucket (months rotate);
//    stop at 60 or when all are empty.
// 4. messages: the person's own only; if more than 12, keep the first and 11 evenly spaced
//    (index round(k * (n - 1) / 11) for k = 0..11, deduplicated); each cut to 600 chars, then redactText, then scrubProperNouns.
```

- [ ] **Step 1: Write the failing tests**

```js
test("at most 60 sessions, only sessions with >= 2 user messages, never the agent's text or tool output", ...);
test("round-robin: with 3 products of 100, 10 and 1 eligible sessions the sample is 20+? -> 60 = 30 + 10 + 1 ... ", () => {
  // 70 sessions in product a, 10 in b, 1 in c -> the sample has all of c and b and 49 of a; product a never fills it first
});
test("months rotate inside a product", ...);
test("a session with 30 user messages keeps 12: the first and evenly spaced ones", ...);
test("each message is at most 600 chars, redacted and proper nouns scrubbed", () => {
  // "Mail me at a@b.io. Ask Marco about Stripe" -> no address, 'Marco' replaced by ⟨name⟩ (not sentence-initial)
});
test("the input file holds neither product labels nor months", ...);
test("an empty or all-ineligible log gives an empty sessions list and writes no file", ...);
```

- [ ] **Step 2: Run** `node --test test/practices-sample.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** per the algorithm; the `practices` list in the input comes from `share/practices.json` entries with `method: "model"` (empty until D5; the step writes no file when empty).
- [ ] **Step 4: Run** `node --test test/practices-sample.test.mjs test/redact.test.mjs`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src bin test
git commit -m "feat(practices): sample the person's own messages for the model step" -m "REB-1142."
```

### Task D2: Validation, the merged block and the detail file

**Files:**
- Create: `test/practices-model.test.mjs`
- Modify: `src/practices.mjs`, `bin/rebase-verify.mjs` (`finalize` reads `out/practices.json` when it exists; refuses the step on a bad file with exit 2 and a message naming the session and id)

**Interfaces:**
- Produces: `export function validateModelAnswers(answers: unknown, sample: Array<{ id: string }>, ids: string[]): { ok: true } | { ok: false, error: string }` (every sampled session present, every id present, booleans only, no extra keys, no other session); `export function computeModelPractices(answers, sample, ids): PracticeItem[]` with `method: "model"`, `of` = sessions classified, `products` and `months` from the sample's `product`/`month`; `export function practicesDetail(answers, sample): Array<{ session: string, date: string, product: string, answers: Record<string, boolean> }>` written to `out/practices-detail.json` (never sent; listed in `submit`'s excluded set test).

- [ ] **Step 1: Write the failing tests**: `validateModelAnswers` refuses a missing session, a missing id, `"yes"`, `1`, an extra key (a reason string), an unknown session; accepts a full set; `computeModelPractices` gives `{ id, method: "model", seen: 2, of: 3, products: 2, months: 2 }` on a hand-built sample; `finalize` with a bad `out/practices.json` exits 2 and writes no `candidate.json`; `out/practices-detail.json` is never in the `submit` payload.
- [ ] **Step 2: Run** `node --test test/practices-model.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement.** `finalize` appends the model items after the code items in list order inside the single `practices` block (`rubric` unchanged).
- [ ] **Step 4: Run** `node --test test/practices-model.test.mjs test/practices-block.test.mjs`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src bin test
git commit -m "feat(practices): validate the model's answers and carry them as counts" -m "REB-1142."
```

### Task D3: The two paths: API key and slash command

**Files:**
- Create: `src/practices-llm.mjs`, `test/practices-llm.test.mjs`
- Modify: `bin/rebase-verify.mjs` (`generate` with `ANTHROPIC_API_KEY`), `.claude/commands/rebase-verify.md` (a new step after the narrative: tells the person what is classified, from which messages, that it is their own model, that only counts leave; the agent reads `out/practices-input.json`, writes `out/practices.json`, runs `finalize`)

**Interfaces:**
- Consumes: `buildPracticesInput` (D1), `validateModelAnswers` (D2), the call helper pattern of `callAnthropic` in `src/profile-llm.mjs` (export it if private).
- Produces: `export const PRACTICES_BATCH = 10`; `export function practicesPrompt(practices): string` (fixed, built from `share/practices.json` definitions and examples, ends with the instruction that tone, feelings and wording quality are never grounds for a yes); `export async function classifyPractices(input, { call }): Promise<Record<string, Record<string, boolean>>>` (batches of `PRACTICES_BATCH` sessions, validates each answer, throws on an invalid one); `generate` prints the same runtime warning the narrative step prints before the first call.

- [ ] **Step 1: Write the failing tests** with a fake `call`: 25 sessions give 3 calls; the prompt contains each model practice's definition and the sentence about tone; an invalid answer throws; no `ANTHROPIC_API_KEY` leaves `out/practices.json` unwritten and prints the slash-command hint; `.claude/commands/rebase-verify.md` contains "practices-input.json" and "practices.json" (docs-consistency).
- [ ] **Step 2: Run** `node --test test/practices-llm.test.mjs test/docs-consistency.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** as listed.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add src bin .claude test
git commit -m "feat(practices): classify the six model practices with the person's own model" -m "REB-1142."
```

### Task D4: The quality gate (reference: a written set, Lorenzo's choice of 2026-11-18)

The six model practices ship only if their checks pass: stability and agreement with a written reference set the builder writes and checks with a blind second reader (spec § 3). Lorenzo does not review the set.

**Files:**
- Create: `scripts/practices-gate.mjs`, `test/practices-gate.test.mjs`
- Create: `test/fixtures/practices-reference/<id>.<it|en>.json` and `test/practices-reference.test.mjs`
- Never committed: the 40 real sessions, the labels, `out/`

**Interfaces:**
- Consumes: `classifyPractices` (D3), `sampleSessions` (D1).
- Produces: `export function agreement(answers, expected, id): { matched: number, of: number, rate: number }`; `export function stability(runA, runB, id): { same: number, of: number, rate: number }`; `export const STABILITY_MIN = 0.9`; `export const AGREEMENT_MIN = 0.9`; `export function gateReport(...)` returning per-practice `{ id, stability, agreement, pass }` as numbers; `node scripts/practices-gate.mjs stability` (two runs on the 40 most recent real sessions of the developer's own logs, prints numbers, writes nothing to the repo); `node scripts/practices-gate.mjs agreement` (runs the classification on the written set, prints numbers).

- [ ] **Step 1: Write the failing tests** (unit tests of the pure functions, no model): `agreement` counts matches per practice; `stability` 36 of 40 is `0.9` and passes, 35 of 40 fails; `pass` requires stability >= 0.9 **and** agreement >= the option's threshold; a practice missing a reference is `pass: false`.
- [ ] **Step 2: Run** `node --test test/practices-gate.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions and the `stability` subcommand.
- [ ] **Step 4: Run** `node --test test/practices-gate.test.mjs`, then **run the stability check** locally: `node scripts/practices-gate.mjs stability` and record the six rates. Expected: PASS for the unit tests; a number per practice.
- [ ] **Step 5: Commit** the harness (no data):

```bash
git add scripts/practices-gate.mjs test/practices-gate.test.mjs
git commit -m "feat(practices): measure the model practices before they ship" -m "REB-1142."
```

- [ ] **Step 6: Write the reference set.** Per practice, >= 8 positive and >= 8 near-miss short sessions, Italian and English, each written with its expected answers for all six practices, in `test/fixtures/practices-reference/`. `test/practices-reference.test.mjs` asserts the counts per practice and language (>= 8 + >= 8 each) and that every session carries six booleans; it runs the classification against the set only when `RUN_PRACTICES_GATE=1` (the classification needs a model).
- [ ] **Step 7: Blind second reading.** A second reader (a fresh agent that is given the rubric and the sessions with the expected answers stripped) labels every session; any session where its labels differ from the expected answers is rewritten until both agree, or dropped, and the test still finds >= 8 + >= 8 per practice. Record the number rewritten and dropped on the card.
- [ ] **Step 8: Run the gate**: `RUN_PRACTICES_GATE=1 node scripts/practices-gate.mjs agreement` and the stability check. A practice ships at agreement >= 90% and stability >= 90%; one that fails is left out of v1, and if all six fail, D5 and D6 do not run.
- [ ] **Step 9: Report** the numbers per practice on the card (REB-1142), as numbers, with the reference used.

### Task D5: Publish the practices that passed (runs only for those)

**Files:**
- Modify: `src/practices-rules.mjs` (`MODEL_PRACTICES`: the passing ids with `method: "model"`, `definition`, two positive and two negative examples, and the instruction about tone, feelings and wording quality; `practicesArtifact()` appends them in the spec's order), `share/practices.json` (`npm run practices`), `README.md`, `PRIVACY.md` (what the classification reads, own model, counts only; **Lorenzo approves**), `.claude/commands/rebase-verify.md` (the step in D3 now lists the ids), `test/practices-artifact.test.mjs`

**Interfaces:** Consumes the gate report (D4). Produces: `share/practices.json` entries for each passing id; the failed ones are not in the file.

- [ ] **Step 1: Write the failing test**: the artifact lists the seven code ids then exactly the passing model ids in the spec's order; each model entry has `method: "model"`, two `positive` and two `negative` examples and the tone instruction; the README names every listed id.
- [ ] **Step 2: Run** `node --test test/practices-artifact.test.mjs test/docs-consistency.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the entries and run `npm run practices`.
- [ ] **Step 4: Run** `npm test`. Expected: PASS.
- [ ] **Step 5: Hold for Lorenzo's approval of the README/PRIVACY text, then commit.**

```bash
git add src share README.md PRIVACY.md .claude test
git commit -m "feat(practices): publish the model practices that passed the gate" -m "REB-1142."
```

### Task D6: Hub: the passing model practices, labels, DPIA §7 (runs only for those; Lorenzo approves)

**Files:**
- Modify (monorepo): `work_profiles/practices.json` (copy of D5's file, `VENDORED.txt` commit), `work_profiles/practices.py` (`PRACTICE_LABELS` gains the passing ids' Italian labels from the Global Constraints), `projects/hub/docs/privacy/work-profile-dpia.md` (the section «Practices (REB-1142)» gains: the model method, the quality gate with its reference and the numbers, the statement that the hub can only bound `seen <= of <= 60` and never re-derive; § 7 rewritten to admit model-classified counts with the reasoning; the written-set limitation if option 2), `projects/website/src/privacy.html` (the sentence about the person's model)
- Test: `test_work_profile_practices.py`

- [ ] **Step 1: Write the failing tests**: every vendored id has an Italian label (existing test); a model item with `seen 11, of 60` is accepted and rendered with the model mark; `of 61` is refused with the practice and the rule named.
- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the refresh and labels; write the DPIA and privacy text.
- [ ] **Step 4: Run** `uv run pytest -q projects/hub/packages/core/tests/test_work_profile_practices.py projects/hub/packages/core/tests/test_openapi_contract.py && uv run ruff check projects/hub && uv run mypy && python3 tooling/english-lint/english_lint.py projects/hub/docs/privacy/work-profile-dpia.md`. Expected: PASS.
- [ ] **Step 5: Hold.** The model practices reach production only when all three are present: § 7 rewritten, **Lorenzo's reading** that counts of moves in a conversation are not an evaluation of the candidate under Annex III 4(a) (and the lawyer's at the DPIA sign-off), and the statement above. If any is missing at release, the code practices ship alone. The tag needs his explicit ok.

```bash
git add projects/hub projects/website
git commit -m "feat(hub): carry the model practices that passed the gate" -m "REB-1142."
```

---

# Part E: Upstream issue (card 6)

### Task E1: One issue in `Play-New/apply-new`

- [ ] **Step 1:** When the fork's Parts A and D (what shipped) are on the fork's `main`, open one issue in `Play-New/apply-new` proposing the `practices` block and the list, linking the fork's implementation and the ADR-001 sentence it follows («future number-about-a-person features must be anchored counts»). Per upstream's `CONTRIBUTING.md` an issue comes before any pull request; no pull request is opened here.
- [ ] **Step 2:** Record the issue link on REB-1142.

---

## Spec mapping

| Spec section | Task |
|---|---|
| Goals 1, 4 | A3, B1 to B5 |
| Goal 2 | A2 (code), D1 to D3 (model) |
| Goal 3 | A1 (published list), A4, D5 |
| Goal 5, § 6 DPIA/privacy page | C1, D6 |
| Non-goals, Anti-goals (no score, not in cloud/card/narrative/`ORDER BY`, adapters out of denominators, marks) | Global Constraints; B3 tests; A2 denominator tests; B4/B5 marks |
| § 1 Approaches | none (decision record) |
| § 2a code practices, vocabulary, paths, normalisation, adapters, Cursor | A1, A2, C1 (Cursor in DPIA) |
| § 2b model practices | D1 to D5 |
| § 3 input, two paths, output, detail file | D1, D2, D3 |
| § 3 quality gate | D4 (waiting for Lorenzo's reference) |
| § 4 payload, optional/nullable, `assessStructure`, groundedness (fork) | A3 |
| § 4 groundedness (hub port) | B3 |
| § 5 vendoring, recheck | B1 |
| § 5 unknown practices, `unknown_practices`, 0060 | B2 |
| § 5 admin panel / person's view / not in cloud | B4, B5, B3 |
| § 6 fork texts | A4, D5 |
| § 6 «What approving this widens» | D6 |
| § 7 Upstream | E1 |
| § 8 cards 1 to 6 | A1 to A3 (1); D1 to D4 (2); A4, D5 (3); B0 to B5, D6 (4); C1, C2 (5); E1 (6) |
| § 9 calls, § 10 left to the builder | Global Constraints; builder choices in A1, A2, B4 |

## Self-review

1. **Spec coverage:** every section maps to a task in the table above; no gap.
2. **Contradictions with the code, and choices the spec leaves open (also reported to Main):**
   - The worktree branch does not contain REB-1141 (no `0059_work_profile_unknown_technologies.py`, no `drop_unknown` in `tech_usage.py`, newest hub migration is `0058_login_method.py`), although the spec § 5 says it is merged first; `origin/main` has it (merge `c2cdb2dec`, PR #944). Task B0 merges `origin/main`.
   - Spec § 2a says «sub-agent side chains folded into their parent». `src/adapters/claude-code.mjs` groups by `sessionId` (lines 114 to 150) and has no sidechain handling; only `opencode.mjs:200` and `kimi.mjs:28` roll sub-agents up. The plan counts sessions as the adapters emit them.
   - Spec § 3 says input goes through `scrubProperNouns`; it is a private function (`src/profile-llm.mjs:73`), exported in D1.
   - `volume.sessions` (`src/profile.mjs:203`) sums the digest's non-ephemeral project sessions, so `of <= volume.sessions` holds only if the practice sessions use the same filter; `repoKey` and `isEphemeral` in `src/digest.mjs` are private, exported in A2.
   - Spec § 2a «after the last file edit before a git commit» is silent when no edit precedes the commit; the plan counts any check before the commit then (A2).
   - The spec's `share/practices.json` carries model definitions, but the six may not pass the gate (open choice); the plan publishes only passing ids (D5), so the seven code practices ship without them and the hub drops any unknown id.
   - The fork's `upstream/main` has no `src/tech-usage.mjs`, so the branch is cut from the fork's `main` (the spec's «build on our branch and merge into our `main`»), contrary to AGENTS.md's default for upstream-able changes.
3. **Type consistency:** `PracticeItem` `{ id, method, seen, of, products, months }` is the same in A2, A3, D2, B1, B3; `practiceSessions` (A2) is consumed by D1; `PracticeRows`/`MARK_*` (B4) by B5.
4. **Review Focus:** each of the five lines has its test in A1/A2/B1/B2/B3.
5. **Proportion:** the plan holds signatures, test names and assertions; bodies only for the failure-reproduction normalisation (A2) and the sampler (D1).
