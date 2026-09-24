---
name: client-repo-onboarding
description: Use when starting a new rebase-managed (not forward-deployed) client engagement, "create a client repo for X", "onboard client X", "set up a new contract for X". Reads the engagement's spec document, or asks for it and the fields it is missing; creates the Linear project and, when new, the initiative; runs `new-client-repo.mjs`; states the steps that stay a human's own click. Triggers on starting an engagement, on the Italian «crea un repo cliente», «prepara un nuovo cliente», «fai il setup di X su Linear».
---

# Onboarding a new client engagement

`tooling/client-repo-starter/README.md` is the material: what gets written into the
repository, the model (initiative = macroprogetto, project = contract, one Linear team
per client), the known Free-plan gap. This skill is the order of operations, so the
whole setup, the Linear half included, runs from one instruction instead of a person
doing that half by hand each time. Where it and the README disagree, the README is
right and this skill has a bug.

## Before anything: the spec

A client engagement starts from a spec, somebody's account of what the contract is and
who is on it: a Notion page, a Google Doc, a paragraph Lorenzo or Ivan just typed. Ask
for it if it was not given, which document has it, or is there one yet. Reading it (or
the paragraph typed instead) has to answer, before the first Linear call:

| Field | Feeds | Missing? |
|---|---|---|
| Client / macroprogetto name | `--project-name`, the initiative name | Ask whether this is a brand-new macroprogetto or one already exists (`list_initiatives` first, since a name close to an existing one is probably the same relationship, not a second one). |
| One-line description | `--one-liner` | Ask for one sentence, the shape `docs/tracker.md` uses for an initiative or a project summary. |
| The contract itself | `--linear-project`, named with a verb (`MVP delivery`, `Booking flow rebuild`) | Ask what this specific contract covers, distinct from the macroprogetto it lives under. |
| New repository or an existing checkout | `--create-repo` vs `--target-dir` | When the macroprogetto is new, `--create-repo`. When it already exists, find the repository already tied to it before choosing either flag (`get_project` on one of its existing projects, `links`, or ask): README § The model is one GitHub repository per macroprogetto, never a second one for a second contract, so an existing macroprogetto always gets `--target-dir` on that same checkout, not `--create-repo` with a new slug. |
| Worktree / Greptile | `--worktree`/`--greptile` | Default on / off (README § Configurable per contract); ask only when the spec signals otherwise (more than one person committing, or the client already runs Greptile). |
| Who is on it | the Linear project's members | Lorenzo and whoever else the spec names; both members always, even a contract with one worker (`docs/tracker.md` § Where things are). |

Never guess a field the spec does not answer and nobody has given: a wrong
`--linear-prefix` or team name is expensive to unwind once issues already exist under
it. Ask, in one batch, naming exactly which fields are missing, not "tell me more
about this client."

## Step 1: the Linear structures, done here

`list_teams` and `list_initiatives` first, so what is missing is known before anything
is asked. Two things this step cannot create, and both are asked for in the same
message when either is missing, never one after the other:

- **The client's own team**, if `list_teams` does not already show it: a person clicks
  "New team" in `linear.app/letsrebase` settings, since the MCP surface has no
  team-creation call (README § Doing the setup). Say whether the Free plan's two-team
  cap is already spent (does another client hold the second slot alongside `rebase`?),
  since a second team needs a Business-plan upgrade first, not this skill's call to
  make.
- **The initiative**, if `list_initiatives` does not already show this macroprogetto: a
  person creates it the same way, since initiatives cannot be created from the MCP
  surface either (`docs/tracker.md` § API details).

Once both exist (already, or handed back after asking): `save_project`, named a verb
and the work it does, `summary` one sentence, `lead` set, `addInitiatives` with the
initiative id, `addTeams` with the team id. Then both members, with the GraphQL
`projectUpdate` mutation and `memberIds` (`docs/tracker.md` § API details:
`save_project` has no member field): use whatever GraphQL access this session has to
`api.linear.app/graphql`, or ask the person to add the second member in the Linear UI
when none is available, and say so rather than leaving the project with one member
silently. Read the project back with `includeMembers: true` before moving on:
`docs/tracker.md` § Where things are says a project without both members is
incomplete.

## Step 2: the repository

Run `new-client-repo.mjs` with every field the two steps above resolved, never asking
the person to restate what the spec and the Linear structures already gave:

```bash
node tooling/client-repo-starter/new-client-repo.mjs \
  --repo <slug> --org letsrebase \
  --project-name <NAME> --one-liner "<one-liner>" \
  --linear-team <team> --linear-prefix <PREFIX> \
  --initiative <initiative> --linear-project "<contract name>" \
  [--create-repo | --target-dir <path>] [--worktree|--no-worktree] [--greptile|--no-greptile]
```

Read its own output rather than assuming what it did: it prints what it wrote, what it
left alone, and any further checklist item that still needs a person or a token scope
this session does not have.

A retrofit (`--target-dir`, no `--create-repo`) only writes files into that checkout;
the script does not branch or open anything on its own. Before running it this way,
put the checkout on a branch first (a worktree, when that repository's own
conventions ask for one, otherwise a plain branch), never `main`. After the script
writes, commit and open a pull request there with that repository's own commit and PR
conventions (README § Retrofitting), reviewed like any other change: this skill never
pushes straight to that repository's own `main`.

## Step 3: what stays a human's click

Named above already, and none of it reachable from here, either because no API reaches
it or because who gets access to a client's own data or repository is not this
session's call to make (root `AGENTS.md` § What a human decides):

- **The team**, always (Step 1).
- **The initiative**, only when the macroprogetto was new (Step 1). An engagement under
  an initiative that already existed needs nothing further here.
- **The freelancer's invite**: GitHub collaborator, Linear workspace member, told the
  Free-plan visibility gap plainly (README § Doing the setup, step 3, and § The known
  gap; `tooling/client-repo-starter/docs/onboarding-freelance.md` for the full
  message).

State to the person, once the rest is done, exactly which of these are still open:
none of them are silently skipped just because the repository and the project exist.

## Keeping it running: project updates

The generated repository's own `AGENTS.md` and its copy of the `linear-content` skill
already carry the mechanics and the tone (three sentences, what moved, the health word
and why, the next visible thing; English, first person, no selling). Post one, or
remind the freelancer's agent to, whenever the board alone would mislead a reader of
this contract: a milestone slips, health changes, a decision is taken, a release ships.
Not one that only restates the issue list, and not skipped when it would matter: the
client's own board is read by Lorenzo or Ivan without opening every card, the same
reason `rebase`'s own board works this way (`docs/tracker.md` § Project updates).
