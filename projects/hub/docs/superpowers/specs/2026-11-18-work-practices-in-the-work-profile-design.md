# Work practices in the work profile

Card: REB-1142, project «Read how a freelancer works from conversations, git and GitHub»
(P-REB-92), milestone «Count work practices from the conversations». Status: draft for
Lorenzo's approval.

## Why

The work profile (`letsrebase/rebase-verify`, our fork of Play New's apply-new) says a good
deal about what a person uses and how much they lean on agents, and little about how they
work. Lorenzo asked on 2026-11-18 for the profile to carry everything it can honestly extract,
and named how a person works as the most important part, beside the technologies.

The conversations with coding agents are the closest source we have to that: they show whether
a person runs the checks before committing, writes tests with the code, reproduces a failure
before fixing it, asks for alternatives, pushes back on the agent with a reason, says when the
work is done. Git and GitHub show what was delivered; the conversations show how.

Two rules bound what we can do with them, and both are already written down: no numeric score
about a person (`docs/design/DECISIONS.md`, 2026-11-18, REB-1132), and the line drawn in § 7 of
the DPIA (`projects/hub/docs/privacy/work-profile-dpia.md`): practices described with counts
are in scope, rating a person's architecture, organisation or soft skills is not, and anything
read from tone or wording is a guess about character (AI Act Art. 5(1)(f) prohibits inferring
emotions at work; Annex III point 4 covers evaluating candidates).

## Goals

1. The profile carries a `practices` block: for each practice on a closed list, how many
   sessions it was seen in, out of how many it could have been seen in, across how many
   products and months.
2. Code counts every practice that tool calls and file paths can show. The person's own model,
   on the person's machine, classifies only the practices that need the person's words to be
   read.
3. The list is published in the fork with a definition per practice, so a freelancer knows
   what is counted before running the tool, and sees the counts before sending.
4. The hub rechecks the block's structure, drops a practice it does not know (counting it, as
   REB-1141 does for technologies), and shows the block to the admin and to the person.
5. The DPIA gets a section for the new processing, and the privacy page a paragraph.

## Non-goals

- **No adjective and no score.** The output is «seen in 18 of 25 sessions», never «rigorous»,
  never a percentage presented as a grade, never a total across practices. No sort, filter or
  threshold in the hub reads a practice.
- **Nothing about tone, feelings, politeness, language level or personality.** The model is
  asked whether a move happened in the session (a question, a correction with a reason), never
  how the person sounded.
- **No model in the hub.** The hub cannot read the conversations and does not try to redo the
  classification.
- **Not in the cloud, not in the anonymous card, not in the narrative** in this milestone. The
  admin and the person see the block; companies do not. Milestone 4 decides whether practices
  feed the card, after the DPIA is signed off.
- **No new source.** Git history and GitHub are milestones 2 and 3.

## Anti-goals

It fails, even if it works, when: a practice reads as a judgement of the person; a freelancer
learns from the admin that something was counted they were never told about; a conversation's
text, a quote or a project name leaves the machine through this block; the admin cannot tell a
count made by code from a classification made by the person's model; a practice that cannot be
seen in an adapter's logs counts that adapter's sessions as «not seen».

## 1. Approaches considered

Lorenzo chose on 2026-11-18 between three.

- **Code only.** Fixed rules on commands, paths and phrases («alternativa», «pro e contro»).
  Same input, same output, lowest regulatory risk; brittle across languages and idioms, and it
  misses exactly the moves Lorenzo cares about.
- **The person's model only.** Their model reads each sampled session and answers yes or no on
  a closed list. Understands meaning in any language; the result moves between runs, costs the
  person tokens, and nobody can redo it.
- **Mixed (chosen).** Code counts what tool calls show; the model classifies only what needs
  the words, on a closed published list, and its counts are marked as such everywhere they are
  shown. The richest result that stays a description.

## 2. The list, version `rebase-practices/v1`

The unit is the session (a session as the adapters already define it, sub-agent side chains
folded into their parent). Each practice has a **denominator**: the sessions where the
practice was possible. «Seen in 18 of 25» means 25 sessions could have shown it and 18 did.

### 2a. Counted by code

| id | Italian label (hub) | Seen when, in one session | Denominator |
|---|---|---|---|
| `checks_before_commit` | Verifica prima del commit | after the last file edit before a `git commit`, a check ran: tests, linter, type check or build | sessions with a commit |
| `tests_with_changes` | Test insieme al codice | both a source file and a test file were edited | sessions that edited a source file |
| `failure_reproduced_before_fix` | Riproduce il problema prima di correggerlo | a command failed, files were edited, then the same command succeeded | sessions where a command failed |
| `plan_before_edit` | Piano prima delle modifiche | a plan move came before the first edit: plan mode, a task list, or writing a plan or spec file (path rule below) | sessions with an edit |
| `reviews_changes` | Rilettura delle modifiche prima del commit | before a commit or a pull request, the diff was displayed in the session (`git diff`, `git show`, `gh pr diff`), whether the person or the agent asked for it | sessions with a commit or a pull request |
| `design_documents` | Scrive documenti di progetto | a design document was written or edited (path rule below) | sessions with an edit |
| `security_checks` | Controlli di sicurezza | a dependency audit or a security scanner ran | sessions with an edit |

Details the builder must respect:

- **A check is broader than today's `checksRun`** in `src/digest.mjs`, which only knows the
  JavaScript tools. The check vocabulary covers at least the ecosystems the stack detection
  knows (pytest, ruff, mypy, go test and vet, cargo test and clippy, mvn and gradle test,
  dotnet test, phpunit, rspec, tsc, eslint, vitest, jest, playwright, build commands). It is
  one exported list, tested.
- **Test files** are recognised by path (`test/`, `tests/`, `__tests__/`, `spec/`, `*.test.*`,
  `*.spec.*`, `*_test.*`, `test_*.py`); source files are code files by extension that are not
  tests, documentation or configuration.
- **«The same command»** for `failure_reproduced_before_fix` is the command with arguments
  after the first file path dropped and whitespace normalised; a test runner invoked on a
  narrower path still matches its broader form.
- **Design documents and plan files are recognised by path**, and the rule is published with the
  list, since a freelancer consents to it. Design documents: anything under `adr/`, `docs/adr/`,
  `docs/design/`, `docs/specs/`, `docs/rfc/`, `rfcs/` or a `specs/` directory, and files named
  `ADR-*`, `*-adr.md`, `RFC*`, `*-design.md`, `*-spec.md`, `DESIGN.md`. Plan files: anything
  under a `plans/` directory and files named `PLAN.md`, `*-plan.md`, `plan.md`; a spec file
  counts as a plan too. The code and `share/practices.json` hold the same patterns, tested.
- **`reviews_changes` says what the code can see**: the diff appeared in the session. It cannot
  tell whether the person read it, and its published definition says so.
- **Adapters that cannot show a practice are left out of its denominator**, never counted as
  «not seen». From the inventory of 2026-11-18: `pi` stubs tool results, so it has no failure
  signal; `codex` keeps no error text but does keep a failure flag; `cursor` reports a failure
  only when its flag is present and reads an absent flag as success, so it is left out of
  `failure_reproduced_before_fix` until a real Cursor log shows a failed command setting it;
  `cmd` is capped at 240 characters in every adapter, so a check at the end of a long chained
  command can be missed. Each practice declares the session fields it needs, and a session
  whose adapter does not fill them is not in the denominator.
- **Cursor's timestamps are coarse** (a message inherits the last user turn's time), so its
  sessions' months can be off at a month boundary. The DPIA section says so.
- Practices the tool already counts elsewhere (reverts in `landing`, delegation, plan-mode uses
  in agentic literacy) are not repeated here; `plan_before_edit` is about order within a
  session, which nothing counts today.

### 2b. Classified by the person's model

| id | Italian label (hub) | Seen when, in the person's own messages in one session |
|---|---|---|
| `asks_for_alternatives` | Chiede alternative | asks for options or trade-offs before choosing one |
| `pushes_back_with_reason` | Corregge l'agente motivando | rejects or corrects what the agent proposed or did, and says why |
| `states_done_criteria` | Dice quando il lavoro è finito | states acceptance criteria, the expected behaviour, or the check that proves it |
| `gives_context_and_constraints` | Dà contesto e vincoli | explains the goal, the constraints, or who the work is for, before asking for changes |
| `raises_risks` | Considera i rischi | raises a risk: security, data loss, cost, performance, compatibility, rollout |
| `limits_scope` | Delimita il lavoro | cuts, splits or defers part of the work explicitly |

The denominator of every model practice is the number of sessions classified. Each practice in
`share/practices.json` carries its English definition, two positive and two negative examples,
and the instruction that tone, feelings and wording quality are never grounds for a yes.

## 3. The classification step

**Input.** Up to 60 sessions per profile, drawn from sessions with at least two messages from
the person, spread round-robin across products and then months so one product cannot fill the
sample. From each session, only the person's own messages: never the agent's, never tool
output. At most 12 messages per session (the first, then evenly spaced), each cut to 600
characters, passed through `redact.mjs` and `scrubProperNouns`. Written to
`out/practices-input.json`, which holds real text and is deleted with `narrative-input.json`.

**Two paths, like the narrative.**

- With `ANTHROPIC_API_KEY`: `bin/rebase-verify.mjs` sends the sessions in batches to the
  person's own key, with the same runtime warning the narrative step gives, and a fixed prompt
  built from `share/practices.json`.
- With the slash command `/rebase-verify` (the default path): a new step in
  `.claude/commands/rebase-verify.md` has the person's agent read `out/practices-input.json` and
  write `out/practices.json`, then `finalize` reads it.

**Output.** Per session, an object of six booleans keyed by practice id: no quotes, no reasons,
no free text. `finalize` validates it (every sampled session, every id, booleans only) and
refuses the step otherwise. The per-session answers stay in `out/practices-detail.json`, with the
session's date and product label, so the person can see what was counted where; that file is
never sent.

**What we cannot guarantee, and say.** The hub cannot redo this step. In the slash-command path
the person's own agent answers, and the person can steer it or edit the file. The block marks
every model practice with `"method": "model"`, and every surface that shows it says it was
classified by the person's model and not rechecked. The code practices are the ones that carry
weight.

**Quality gate before release.** The ground truth is a person's, never a model's: a person
hand-labels the six model practices on 40 sessions of their own logs, locally, from a labelling
file the tool writes (the person's messages of each sampled session and six yes/no fields), and
the classification runs twice on the same sessions. A practice whose agreement with the hand
labels is under 80%, or that flips between the two runs on more than 10% of sessions, is left
out of v1. The labelled set is never committed (it is that person's prompts); the result is
reported on the card as numbers. Who labels is Lorenzo's call (§ 9); if no person labels, the
six model practices do not ship and v1 is the code practices alone.

## 4. The payload

```json
"practices": {
  "rubric": "rebase-practices/v1",
  "timezone": "Europe/Rome",
  "items": [
    { "id": "checks_before_commit", "method": "code", "seen": 18, "of": 25, "products": 3, "months": 4 },
    { "id": "asks_for_alternatives", "method": "model", "seen": 11, "of": 60, "products": 4, "months": 5 }
  ]
}
```

Optional and nullable, like `techUsage`, so older profiles stay valid and the frozen fixture
passes unmodified. `months` are distinct calendar months, in the profile's timezone, with at
least one session where the practice was seen. A practice whose denominator is 0 is omitted.
The fork's `assessStructure` checks the block the way it checks `techUsage`, and the narrative
step does not receive it in this milestone.

The numbers join the groundedness support pool on both sides (`src/groundedness.mjs` and the
hub's port), so a narrative that cites «18 of 25» is supported.

## 5. The hub

- **Vendoring.** `share/practices.json` (ids, method, English definitions) is generated or
  checked in the fork and copied verbatim into `work_profiles/practices.json`, like the two
  technology files (`VENDORED.txt`). The Italian labels are the hub's own strings.
- **Recheck** (`work_profiles/practices.py`, called from `structure`): the rubric is one the hub
  knows; each item's `method` is the one its id has in the vendored list; `0 ≤ seen ≤ of`;
  `of ≤ volume.sessions`, and `of ≤ 60` for a model practice; `products ≤ volume.products` and
  `products ≤ seen`; `months ≤ seen`, `months ≥ 1` when `seen ≥ 1`, and no more months than the
  profile's window spans; no id twice. A refusal names the practice and the rule, in Italian.
- **Unknown practices are dropped and counted**, never stored, exactly like unknown
  technologies since REB-1141 (`tech_usage.drop_unknown` and the `unknown_technologies` column,
  merged before this work starts): a column `unknown_practices` beside it, shown to the admin as
  one muted line.
- **The admin's review panel** gets a «Come lavora» section: one row per practice in list order,
  the Italian label, «in 18 sessioni su 25», the products and months, and a mark per row,
  «contato dal codice» or «dal modello della persona, non ricontrollato». No bar, no colour
  scale, no total.
- **The person's own view** in the member area shows the same rows with the same marks, and a
  link to the published list.
- **Not** in `CloudTalentRead`, the card source text (`work_profiles/source.py`), the talent
  list or any query's `ORDER BY` / `WHERE`.

## 6. Texts and privacy

- **The fork**: `README.md` gets a section listing the practices and both methods; `PRIVACY.md`
  says which messages the classification reads, that it runs with the person's own model, and
  what leaves (counts only); the slash command's new step tells the person what is being
  classified before it runs. These are texts a freelancer reads before consenting, so Lorenzo
  approves them before the merge.
- **The privacy page** (`projects/website/src/privacy.html`): a paragraph under the work profile
  section saying the profile now carries counts of ways of working, which are counted by code
  and which by the person's model on their machine, and that only the counts reach us. Public
  text: Lorenzo approves it.
- **The DPIA** gains a section «Practices (REB-1142)»: the processing, the two methods, the
  denominators, the quality gate, and the residual risk that model counts are read as more
  reliable than they are, with the marks as the measure.

**What approving this widens.** The two halves of the list do not sit in the same place against
the DPIA as it stands. The seven code practices are what § 7 already scopes in: practices
counted «without a model». The six model practices go past it: § 7 imagined counts from tool
calls only, and it calls anything inferred from wording «a guess about character». They stay
on this side of that line only because the model is asked whether a move happened (a question
asked, a correction with a reason, a risk named), never how the person is, and because the
quality gate drops any practice a person's labels do not confirm. Shipping them still needs
three things the code practices do not: § 7 rewritten to admit model-classified counts and the
reasoning above, Lorenzo's reading that counts of moves in a conversation are not an evaluation
of the candidate under Annex III 4(a) (and the lawyer's, at the sign-off the DPIA already
asks for), and the plain statement, here and in the DPIA, that the hub can only bound these
numbers (`seen ≤ of ≤ 60`), never re-derive them. They are not scores in the sense of
REB-1132 (no number about a person beyond a count of sessions), which is the line the fork's
`AGENTS.md` draws for model-written numbers. If any of the three is not there at release, the
code practices ship alone.

## 7. Upstream

The block fits ADR-001 as written («future number-about-a-person features must be anchored
counts»), so it is upstream-able under the fork's own rule. A behaviour change needs an issue
first in `Play-New/apply-new`: one issue proposing the block and the list, opened when the
fork's implementation exists to point at. We build on our branch and merge it into our `main`
first; upstream may reshape it.

## 8. Cards, in order

1. Fork: the code practices, the block, `assessStructure`, groundedness pool, `share/practices.json`
   and its test.
2. Fork: the classification step, both paths, the detail file, the quality gate's numbers.
3. Fork texts: README, PRIVACY, slash command (Lorenzo's review).
4. Hub: vendoring, recheck, `unknown_practices`, admin and member views, groundedness port.
5. Privacy page paragraph and DPIA section (Lorenzo's review), then a release.
6. The upstream issue.

## 9. Calls made in this design, and the one open question

Calls (mine, for Lorenzo to check):

- The sample is 60 sessions; the person's messages only; 12 messages of 600 characters each.
- Practices stay out of the cloud, the anonymous card and the narrative until milestone 4.
- The six model practices and seven code practices above are the whole v1 list; a practice that
  fails the quality gate is dropped rather than shipped with a caveat.
- The admin and the person see the same rows with the same marks.
- `reviews_changes` counts a diff displayed in the session, whoever asked for it, and says so.
- Cursor sessions are left out of `failure_reproduced_before_fix` until a real Cursor log proves
  its failure flag.
- The design-document and plan-file path rules in § 2a.

Open, Lorenzo's: who hand-labels the 40 sessions of the quality gate (§ 3).

## 10. Left to the builder

The exact check vocabulary and test-path patterns, the batching of the API path, the
normalisation of «the same command», the layout of the two views within the existing panels
(existing tokens and components only), and the names of internal functions.
