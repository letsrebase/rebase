# rebase-verify from npx, with any coding agent over ACP

Card REB-1149, milestone «Run rebase-verify with npx and any coding agent» of project «Let
freelancers show how they work with a reviewed work profile». Fork: `letsrebase/rebase-verify`.

## Why

Today a freelancer clones the fork, opens Claude Code in it and runs `/rebase-verify`: the
tool assumes Claude Code, a clone, and an agent that follows a long slash command by hand. The
narrative and the four model practices can only come from Claude (the agent in the slash
command, or an Anthropic key). Lorenzo works in oh-my-pi, and freelancers use Codex, OpenCode,
Cursor, Gemini and others. He wants (2026-11-18):

- one command, `npx rebase-verify`, with a coloured interactive CLI and inline spinners like
  Svelte's `sv create`;
- oh-my-pi supported, with all the main harnesses;
- the freelancer choosing the agent and the model that write the report and run the analysis,
  with sensible defaults;
- the agents driven over ACP, so the run can be watched and logged.

## Decisions taken by Lorenzo (2026-11-18)

- Model practices may be classified by any model (option B). The profile records which agent
  and model answered, and the hub shows it. The gate figures stay as measured for
  `claude-opus-4-7`, and the texts say so.
- Runtime dependencies are allowed; the package is published on npm so that `npx` works.
- ACP is the transport to the agents.

## Goals

1. `npx rebase-verify` runs the whole flow interactively on a TTY: sources, agent, model,
   consent, narrative, practices, profile, review, submit.
2. Every non-interactive path keeps working: `generate`, `prepare`, `finalize`, `submit` and
   their flags, for scripts, CI and the existing slash command.
3. oh-my-pi is a source of its own, read from `~/.omp/agent/sessions`.
4. The agent that writes and classifies is chosen from those installed and logged in, over
   ACP, with live progress and a local log; any tool the agent asks to run is refused.
5. The profile records the agent and model that answered (`practices.classifiedBy`,
   `narrative.writtenBy`), and the hub shows the practices' one.
6. The runs rebase-verify starts never come back as the person's own sessions.

## Non-goals

- A quality gate per model. The gate figures are for one model and prompt; any other model is
  recorded, not measured.
- New metrics, new practices, new sources beyond oh-my-pi (Gemini CLI and others' logs are a
  later card).
- Publishing on npm from this box: it needs Lorenzo's npm account. The package is made ready
  (`npm publish --dry-run`); the publish is his step.

## Anti-goals

- An agent reading files, running commands or browsing during the narrative or the
  classification.
- Anything sent to rebase other than today's payload plus the two `{ agent, model }` records.
- A second copy of the profile logic in the wizard: the wizard calls the same functions the
  subcommands call.

## Constraints

- Fork-only change (adds runtime dependencies upstream refuses, names rebase). The oh-my-pi
  source is upstream-able and can be proposed with REB-1147.
- Node >= 20, Windows in CI.
- Privacy rules of the fork hold: redaction first, `.env` never read, nothing sent to rebase
  before `submit --yes`. What goes to the chosen agent's provider is the same text the slash
  command and the API path send today, and the consent screen says so before anything is sent.
- Texts a freelancer reads (README, PRIVACY, consent screen copy, hub copy, privacy page, DPIA)
  need Lorenzo's approval before merge.

## What the ACP spike showed (2026-11-18)

Evidence: `~/.cache/tmp/acp-spike/FINDINGS.md` (local, synthetic payloads only) and the
registry research in `~/.cache/tmp/acp-harness-research.md`. Tested: oh-my-pi 18.22.2
(`omp acp`, native), Claude Code (`@agentclientprotocol/claude-agent-acp` 0.108.1), Codex
(`@agentclientprotocol/codex-acp` 2.7.0; the `@zed-industries` adapters are archived or renamed).

- All three reuse the CLI's existing login, ran a 10-session classification batch and returned
  answers that pass `validateModelAnswers` (56 to 60 of 60 matched the fixtures, one sample
  each). Claude may append chatter after the JSON, so the parser takes the first balanced object.
- The model is chosen the same way on all three: the `configOptions` entry with
  `category: "model"`, set with `session/set_config_option`. `session/set_model` is not usable
  (omp rejects it). Model ids differ per agent and come from the offered list.
- Refusing `session/request_permission` does not block tools: in default setups all three ran
  read or shell tools without asking. What blocks them: `omp --no-tools acp`; Claude
  `_meta.claudeCode.options.tools: []`; Codex only the `read-only` mode plus an appended
  instruction (no tool call seen, no hard guarantee).
- All three write a transcript by default. Claude's `_meta.claudeCode.options.persistSession:
  false` prevents it; omp (`--no-session` ignored) and Codex have no working switch, so their
  transcript must be removed after the run.
- Live events (`agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `usage_update`) arrive
  on all three; cancel answers in under 60 ms; a missing login surfaces as `-32000` (Claude at
  prompt, Codex at `session/new`) or `-32603` "No model selected" (omp).
- Each prompt carries the agent's own context (omp about 35k tokens, Codex about 20k, Claude
  more), so a run costs more than a bare API call. Claude `settingSources: []` and omp
  `--no-rules --no-skills --no-extensions` trim it.
- The registry lists native ACP for Gemini CLI (`gemini --acp`), OpenCode (`opencode acp`),
  Cursor (`agent acp`), Copilot CLI (`copilot --acp`, preview), Kimi (`kimi acp`) and Qwen Code
  (`qwen --acp`); none of these was run here.

## Approaches considered

1. **ACP for every agent, tools blocked at spawn where the agent allows it, and a client-side
   tripwire everywhere** (chosen). One client, one event stream, one log format, one way to
   pick the model. Every main harness speaks ACP natively or through a maintained adapter.
2. ACP plus a headless fallback (`claude -p`, `codex exec`, `omp -p`) for agents without ACP.
   Two code paths for no agent we need today; dropped.
3. Headless only. No uniform live events, a parser per CLI output format, and no portable way
   to list models; dropped.

## Design

### Distribution

- Published on npm as `rebase-verify` (unscoped, free on 2026-11-18). Runtime dependencies:
  `@clack/prompts` (1.9.0, MIT) and `@agentclientprotocol/sdk` (2.8.0, Apache-2.0). The adapters
  for Claude and Codex are not dependencies: the CLI starts them with `npx -y <package>@<pinned
  version>` only when the person picks that agent, and says so.
- `npx rebase-verify` with no subcommand on a TTY starts the wizard; without a TTY it behaves
  as `generate` does today. `node bin/rebase-verify.mjs` keeps working after `npm install`.
- Lorenzo publishes from his npm account; the build ends at `npm publish --dry-run` green.

### The wizard (`@clack/prompts`, English like the rest of the CLI)

1. Intro with the tool name and version.
2. Reading sources: one spinner per source with its session count as it finishes; then a
   multiselect of the sources that have sessions, all selected by default.
3. Agent: a select of the agents found installed, each with its session count in the person's
   logs and «tested» or «not tested» beside it. Default: the tested agent with the most
   sessions; if none is installed, the one with the most sessions. If `ANTHROPIC_API_KEY` is set,
   «Anthropic API (your key)» is offered too, through today's code.
4. Model: a spinner while the agent starts and offers its models, then a select of them with
   the agent's current model as default.
5. Consent: a note saying exactly what goes to the chosen agent's provider (the narrative input
   and at most 60 sessions × 12 of the person's own messages, redacted), that rebase receives
   nothing until submit, that a local log of the exchange is written to `out/agent-log.jsonl`,
   and that the agent runs with its tools switched off. Confirm or stop.
6. Narrative, then practices: a spinner with live status from the event stream (what the agent
   is doing, characters received, batch n of m, tokens or cost when reported). Ctrl-C cancels
   the ACP turn and exits cleanly.
7. Profile: a note with the main counts and the path to `out/profile.md`; then a select:
   submit now (today's browser sign-in and `submit` flow) or exit.

The slash command's review questions (artefact links, swapping a project, vocabulary, narrative
refinements) stay in the slash command; the wizard does not ask them in v1.

Flags for scripts: `--agent <id>`, `--model <id>`, `--yes` (skip confirmations), and the
existing source flags. `generate --agent <id>` uses the same runner without the wizard.

### The ACP runner (`src/agents/`)

- `registry.mjs`: one entry per agent: id, label, how to start it (command and args), whether
  it is tested, how its tools are switched off and its context trimmed (spawn args or
  `session/new` `_meta`), how its system prompt is passed (Claude `_meta.systemPrompt`, Codex
  `systemPrompt` append, else inlined), its login hint, and how to find and remove the
  transcript a run leaves. v1 entries: omp, Claude Code, Codex (tested); OpenCode, Gemini CLI,
  Cursor, Copilot CLI, Kimi, Qwen Code (not tested, offered only when installed).
- `acp.mjs`: start the agent in a fresh empty directory named `rebase-verify-agent-run-<random>`
  under the OS temp dir; declare no fs or terminal capability; refuse every permission request;
  set the model through `session/set_config_option`; send prompts; stream events to a callback
  and to `out/agent-log.jsonl`; cancel on demand; close the session and the process.
- **Tripwire**: any `tool_call` event during a narrative or classification turn cancels the
  turn, discards its answer and fails the step with a message naming the agent. This is the
  guarantee for agents whose tools cannot be switched off at spawn.
- **Answers**: the first balanced JSON object in the reply, then the existing validators
  (`validateNarrative`, `validateModelAnswers`).
- **Our own transcripts**: Claude runs with `persistSession: false`. For omp and Codex the
  runner removes, after the run, only files whose path or recorded cwd contains the run's
  directory name. Independently, every adapter skips any session whose cwd contains
  `rebase-verify-agent-run-`, so a transcript left behind is never read as the person's work.

### What the profile records

- `practices.classifiedBy: { agent, model }` when model practices are present, and
  `generatedBy.narrative: { agent, model }` at the top level. Agent ids are the registry ids
  (`omp`, `claude-code`, `codex`, `anthropic-api`, ...), models the id the agent offered.
- The hub keeps unknown top-level fields (`extra="allow"`); its practices recheck must accept
  `classifiedBy` (strings, bounded length) and the views show it next to the model mark:
  «dal modello della persona, non ricontrollato · <agent>, <model>».

### oh-my-pi as a source

`src/adapters/omp.mjs` reads `~/.omp/agent/sessions` with the pi reader (it already parses them:
5,877 sessions, no malformed line), labels them `omp`, maps omp's own tool names to the
canonical ones, and leaves out omp's advisor and subagent transcripts, which the agent wrote.
Flags `--omp-root` and `--no-omp`, as for the other sources.

### Texts

README (install with `npx`, the agents and how they are started, what is recorded), PRIVACY
(the chosen agent's provider receives the narrative input and the sample; the local log; the
agent and model recorded in the profile), the hub's member page instructions (`npx
rebase-verify`, once published), the privacy page sentence and DPIA § 8 (any model may classify;
the gate figures hold for one model; the agent and model are shown). Lorenzo approves them.

## Left to the builder

- Spinner wording and the exact layout of the notes, within the steps above.
- How the model select shows long lists (search, grouping by provider).
- Version pins of the adapters, checked at build time.
- Whether the practices batches share one ACP session or open one each; one session with
  self-contained prompts is the default.
