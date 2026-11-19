# rebase-verify from npx with any coding agent: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `npx rebase-verify` runs an interactive, coloured wizard that reads the person's agent logs (oh-my-pi included), lets them pick the coding agent and model that write the narrative and classify the practices over ACP with live progress and a local log, and records which agent and model answered, which the hub shows.

**Architecture:** A new `src/agents/` layer in the fork (registry of agents, an ACP runner on `@agentclientprotocol/sdk`, a JSON extractor) feeds the existing narrative and practices code through a `call`-shaped adapter; a `@clack/prompts` wizard in `bin/` drives the same functions the subcommands use; a new `omp` source wraps the pi reader. The hub accepts and shows `practices.classifiedBy`.

**Tech Stack:** fork: Node ESM >= 20, `node --test`, runtime deps `@clack/prompts@1.9.0` and `@agentclientprotocol/sdk@2.8.0`. Hub: Python (pytest, ruff, mypy), React + vitest.

**Spec:** `projects/hub/docs/superpowers/specs/2026-11-18-rebase-verify-npx-and-acp-design.md` (monorepo). Spike evidence: `/home/dev/.cache/tmp/acp-spike/FINDINGS.md`, `/home/dev/.cache/tmp/acp-spike/spike.mjs`, `/home/dev/.cache/tmp/acp-harness-research.md`.

## Repos and branches

| Part | Repo | Branch | Commits |
|---|---|---|---|
| F1 to F7 | fork `/home/dev/projects/letsrebase/rebase-verify`, worktree `/home/dev/.cache/tmp/wt-1149` cut from `origin/main` | `fiorelorenzo/reb-1149-run-rebase-verify-from-npx-with-any` | Conventional Commits, last line `REB-1149.` |
| H1 | monorepo, worktree `/home/dev/.cache/tmp/wt-1149-hub` cut from `origin/main` | same name | Conventional Commits, scope `hub`, footer `REB-1149.` |

Fork-only change (runtime dependencies, rebase naming). Never push until the controller says so.

## Global Constraints

- English everywhere in the fork (CLI copy included), no em dashes; Italian only for hub product strings.
- Node >= 20; the suite runs on Node 20, 22, 26 and Windows; spawn paths via `fileURLToPath`, never `URL.pathname`.
- Runtime dependencies allowed: exactly `@clack/prompts` 1.9.0 and `@agentclientprotocol/sdk` 2.8.0, pinned. Agent adapters are started with `npx -y <pkg>@<pinned version>`, never added as dependencies: `@agentclientprotocol/claude-agent-acp` 0.108.1, `@agentclientprotocol/codex-acp` 2.7.0.
- Privacy rules of the fork hold: redaction before anything else reads the logs, `.env` never read, nothing sent to rebase before `submit --yes`. The agent receives only what the API path sends today (narrative input; the practices sample).
- The agent never runs tools during a narrative or classification turn: tools off at spawn where possible (omp `--no-tools --no-rules --no-skills --no-extensions` before `acp`; Claude `_meta.claudeCode.options: { tools: [], settingSources: [], persistSession: false }`; Codex mode `read-only` plus the appended instruction), and a tripwire: any `tool_call` event cancels the turn and fails the step.
- Model chosen only via the `configOptions` entry with `category: "model"` and `session/set_config_option`; never `session/set_model`.
- Every run's cwd is a fresh empty dir named `rebase-verify-agent-run-<random>` under `os.tmpdir()`; every adapter skips sessions whose raw cwd contains `rebase-verify-agent-run-`.
- The frozen fixture test (`test/fixture.test.mjs`) passes unmodified. Non-interactive subcommands keep their behaviour and flags.
- Texts a freelancer reads (README, PRIVACY, slash command, consent copy, hub copy, privacy page, DPIA) need Lorenzo's approval before merge.

## Review Focus

1. No TTY (piped, CI): the wizard must never start; `rebase-verify` behaves as `generate` today. Pinned in F6.
2. The chosen agent is not logged in or not installed: a one-line message with its login or install hint, exit code 2, no stack trace, no half-written `candidate.json`. Pinned in F3/F6.
3. The agent appends text after the JSON, fences it, or answers with no JSON: first balanced object extracted, else a clear failure naming the agent. Pinned in F3.
4. Ctrl-C during a turn: `session/cancel`, child process killed, temp dir removed, exit 130. Pinned in F3.
5. A transcript of our own run left in `~/.omp/agent/sessions` or `~/.codex/sessions` is never read back as the person's work, and cleanup never deletes a file whose path or recorded cwd lacks the run's dir name. Pinned in F2/F4.

---

### Task F1: Package, dependencies, CI

**Files:** `package.json`, `package-lock.json`, `.github/workflows/test.yml`, `AGENTS.md`, `.npmignore` or `files`, `test/package.test.mjs`

- Version `0.1.0`; `dependencies` with the two pinned packages; `files` whitelist (`bin/`, `src/`, `share/`, `.claude/commands/`, `README.md`, `PRIVACY.md`, `SECURITY.md`, `LICENSE`); `bin` unchanged; `engines.node >=20`.
- CI: `npm ci` before `npm test` in every leg. AGENTS.md: the fork now has runtime dependencies and its own CI step (say why: npx distribution, Lorenzo's call 2026-11-18).
- `--version` prints the package version.
- Test: `npm pack --dry-run --json` lists no file from `test/`, `out/`, `docs/`, `scripts/`; `--version` prints `0.1.0`.
- Commit `build: publish rebase-verify on npm with its first runtime dependencies`.

### Task F2: oh-my-pi as a source, and our own runs skipped everywhere

**Files:** Create `src/adapters/omp.mjs`, `test/omp-adapter.test.mjs`; Modify `src/sources.mjs`, `src/adapters/pi.mjs` (export what omp reuses), `bin/rebase-verify.mjs` (`--omp-root`, `--no-omp`), `src/digest.mjs` or a shared helper (`isAgentRun(cwdRaw)`), README Sources row.

- **Interfaces:** `export function defaultOmpRoot(): string` (`~/.omp/agent/sessions`); `export function readOmp(root): Bundle` with `source: "omp"`; `export const AGENT_RUN_MARKER = "rebase-verify-agent-run-"`; `export function isAgentRun(cwd: string): boolean`.
- omp tool names mapped to the canonical ones (read the pi map; extend for omp's `read`, `bash`, `edit`, `write`, `grep`, `find`, `glob`, `task`, `todo`, `ast_edit`, `eval`, `browser`, `web_search`; establish the real names by counting tool names over the local `~/.omp/agent/sessions` with a throwaway script that prints names and counts only, never content).
- Leave out omp's advisor transcripts (`*__advisor*.jsonl`) and subagent transcripts (find how omp stores them by listing file names only), with a test on hand-built files.
- Every adapter's sessions with `isAgentRun(cwdRaw)` are dropped in `readAllSources` (one place), with a test per source kind using a hand-built bundle.
- Commit `feat(sources): read oh-my-pi sessions and skip rebase-verify's own agent runs`.

### Task F3: The ACP runner

**Files:** Create `src/agents/acp.mjs`, `src/agents/json-answer.mjs`, `test/fixtures/fake-acp-agent.mjs` (an ACP agent on the SDK's agent side, scripted by env: answer text, emit a tool_call, require auth, offer models, hang until cancel), `test/acp.test.mjs`, `test/json-answer.test.mjs`. Reuse `/home/dev/.cache/tmp/acp-spike/spike.mjs` as reference for SDK usage.

- **Interfaces:**
  - `export function firstJsonObject(text: string): object` (first balanced `{...}`, string-aware; throws `NoJsonError`).
  - `export async function startAgent(spec, { onEvent, logFile, signal }): Promise<AgentSession>` where `spec = { command, args, env?, sessionMeta?, systemPromptVia: "meta" | "param" | "inline", mode? }`.
  - `AgentSession = { models: Array<{ id, name }>, currentModel: string | null, setModel(id), prompt(text, { system }) => Promise<{ text, usage }>, cancel(), close() }`.
  - `export class ToolUseError`, `export class AgentAuthError` (`-32000`, or `-32603` whose details say no model is selected), `export class AgentStartError` (spawn ENOENT).
- Behaviour: fresh `mkdtemp(join(tmpdir(), AGENT_RUN_MARKER))` cwd; `clientCapabilities: {}`; every permission request answered with the reject option; `tool_call` during a prompt → `session/cancel`, then `ToolUseError`; events appended as JSON lines to `logFile` (timestamped, direction, method); `AbortSignal` → cancel, kill, remove dir; `close()` kills the child and removes the dir.
- Tests against the fake agent only (no network): models listed and set via `set_config_option`; text assembled from chunks; tool_call trips; auth error mapped; cancel returns; log file written; temp dir removed.
- Commit `feat(agents): drive a coding agent over ACP with tools refused`.

### Task F4: The agent registry, detection and transcript cleanup

**Files:** Create `src/agents/registry.mjs`, `test/registry.test.mjs`.

- **Interfaces:** `export const AGENTS: AgentEntry[]`; `AgentEntry = { id, label, tested: boolean, source: string | null, detect(): { installed: boolean, how: string }, spec({ model }): spec for startAgent, loginHint: string, cleanup(runDir): string[] }`; `export function availableAgents({ sessionsBySource, env }): Array<AgentEntry & { sessions: number }>` ordered as the wizard shows them; `export function defaultAgent(list)`.
- Entries: `omp` (`omp --no-tools --no-rules --no-skills --no-extensions acp`, source `omp`, tested), `claude-code` (`npx -y @agentclientprotocol/claude-agent-acp@0.108.1`, `_meta` above, source `claude-code`, tested, installed when `claude` is on PATH or `~/.claude` exists), `codex` (`npx -y @agentclientprotocol/codex-acp@2.7.0`, mode `read-only`, systemPrompt append, source `codex`, tested), and untested `opencode` (`opencode acp`), `gemini` (`gemini --acp`), `cursor` (`agent acp`), `copilot` (`copilot --acp`), `kimi` (`kimi acp`), `qwen` (`qwen --acp`). Detection by PATH lookup that works on Windows (`PATHEXT`), never by running the binary.
- `cleanup(runDir)`: omp removes `~/.omp/agent/sessions/<dir whose name contains the run dir's basename>`; codex removes rollout files under `~/.codex/sessions/**` whose first line's recorded cwd contains the run dir's basename (read only the first line); claude returns nothing (persistSession off). Tests on temp homes: matching files removed, a file without the marker kept.
- Default agent: the tested installed agent with the most sessions; else the installed one with the most; `anthropic-api` offered when `ANTHROPIC_API_KEY` is set.
- Commit `feat(agents): list the coding agents rebase-verify can drive`.

### Task F5: Narrative and practices through the chosen agent

**Files:** Modify `src/profile-llm.mjs`, `src/practices-llm.mjs`, `src/practices.mjs` (block field), `src/profile.mjs` (`generatedBy`), `src/consistency.mjs` (`assessStructure` accepts `practices.classifiedBy` and `generatedBy`), `bin/rebase-verify.mjs` (`generate --agent <id> [--model <id>]`); Create `src/agents/run.mjs`, `test/agent-run.test.mjs`.

- **Interfaces:** `export async function runWithAgent({ agent, model, narrativeInput, practicesInput, onStatus, logFile, signal }): Promise<{ narrative, practicesAnswers, used: { agent, model } }>` (one ACP session; narrative turn first with the narrative system prompt, then the practices batches of `PRACTICES_BATCH` with `practicesPrompt`, each answer through `firstJsonObject` then the validators; one retry of a batch on invalid JSON, then fail).
- `parseAnswer` in `practices-llm.mjs` and the narrative parse use `firstJsonObject`.
- Profile: `practices.classifiedBy = { agent, model }` when model items exist; top-level `generatedBy = { narrative: { agent, model } }`; `anthropic-api` for the key path with the fork's `MODEL`; `slash-command` with model `null` for the slash path (the agent writes `out/practices.json` itself).
- `generate --agent omp --model <id>` runs the whole flow without the wizard and writes the profile; `out/agent-log.jsonl` written.
- Tests with the fake agent: profile carries both records; a tool_call aborts with exit 2 and no `candidate.json`; first-balanced-object parsing on chatter; hub-side bounds (`classifiedBy.agent` <= 40 chars, `model` <= 120) checked by `assessStructure`.
- Commit `feat(agents): write the narrative and classify the practices with the chosen agent`.

### Task F6: The wizard

**Files:** Create `src/wizard.mjs`, `test/wizard.test.mjs`; Modify `bin/rebase-verify.mjs` (no subcommand + TTY + no `--yes` → wizard; `--agent`, `--model`, `--yes`).

- Steps and copy per the spec's «The wizard» section, with `@clack/prompts`: `intro`, `spinner` per source with counts, `multiselect` sources, `select` agent (hints: sessions, tested), `spinner` while models load, `select` model (default current), `note` consent + `confirm`, `spinner` with live status from `onStatus` (phase, chars, batch n/m, tokens or cost), `note` profile summary, `select` submit now / exit, `outro`. `isCancel` anywhere → `cancel()` and exit 130.
- The wizard calls the same functions the subcommands call; no profile logic in `wizard.mjs`.
- Test the decision logic with injected prompt functions (no real TTY): default agent and model chosen; cancel exits 130; no TTY never calls the wizard (spawn the bin with stdin not a TTY).
- Real run check (controller, after the task): `script -qc` pty run against a temp HOME with a small synthetic log and the real `omp` agent, screenshot of the terminal kept for the PR.
- Commit `feat(cli): an interactive wizard for npx rebase-verify`.

### Task F7: Texts (Lorenzo approves)

**Files:** `README.md` (lead with `npx rebase-verify`; agents table: how each is started, tested or not, tools off, transcripts; `--agent/--model/--yes`; what `out/agent-log.jsonl` holds), `PRIVACY.md` (the chosen agent's provider receives the narrative input and the sample; the local log; agent and model recorded in the profile and shown to rebase's admins; the adapters for Claude and Codex are downloaded from npm when picked), `.claude/commands/rebase-verify.md` (records `slash-command`; still works from a clone), `test/docs-consistency.test.mjs`.
- Commit `docs: run rebase-verify from npx with the agent you choose`.

### Task H1: The hub shows who classified the practices

**Files (monorepo):** `projects/hub/packages/core/src/rebase_core/work_profiles/practices.py` (accept optional `classifiedBy: { agent: str <= 40, model: str | null <= 120 }`, refuse other shapes in Italian), the view rows and both web views (admin and member: «dal modello della persona, non ricontrollato · <agent>, <model>» on model rows), `projects/hub/docs/privacy/work-profile-dpia.md` § 8, `projects/website/src/privacy.html` (one sentence: the person picks the agent and model; it is recorded and shown), `projects/hub/apps/web/src/pages/member/WorkProfile.tsx` instructions (`npx rebase-verify`; behind the publish: keep the clone instructions until Lorenzo says the package is out, so this edit is its own commit to land after publishing), tests.
- Run: practices tests, web unit tests for both views, ruff, mypy, english-lint.
- Commits `feat(hub): show which agent and model classified the practices`, then `docs(hub): ...` for the texts.

## Self-review

- Spec coverage: distribution F1; wizard F6; runner F3; registry F4; tripwire F3/F5; transcripts F2/F4; recording F5/H1; omp F2; texts F7/H1.
- Types: `AgentSession`, `AgentEntry`, `{ agent, model }` records are named once here and consumed by F5, F6, H1.
