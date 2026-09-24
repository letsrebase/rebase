# The client repo starter kit

What a rebase-managed (not forward-deployed) client engagement gets before the first
freelancer touches code: a repository that already knows how to use Linear, how to
commit, and how to open a PR, the same way `letsrebase/rebase` does it, whatever
coding agent or harness the freelancer runs. This directory is the material; the
setup itself is a person's job (§ Doing the setup), not a fully automated one, because
two of its steps (creating a Linear team, inviting a freelancer) have no API this kit
can drive.

## When this applies

rebase runs two shapes of engagement (`AGENTS.md` § What this repository is, for the
product monorepo's own framing): **forward-deployed**, where a freelancer works
inside a client's own tools, and **internally-managed consulting**, where rebase owns
the tracker and the conventions so every freelancer across every contract works the
same way. This kit is for the second shape only. A forward-deployed engagement uses
whatever the client already runs; nothing here applies to it.

## The model

- **One Linear team per client**, separate from `rebase` (the product team) and from
  every other client's team. A Free-plan workspace gets two teams total, so today
  that is `rebase` plus one client team at a time; a second client's team waits for a
  Business-plan upgrade rather than sharing a team with the first.
- **Initiative = macroprogetto.** A product or a client relationship that outlives
  any single contract. Permanent, the same way `Website` or `PigroCRM` is permanent
  in the product team.
- **Project = one contract.** Scoped work with an end; it closes when the contract's
  scope ships. A macroprogetto that runs an MVP contract and then a build-out
  contract is one initiative with two projects, in order.
- **Milestone = a phase inside a contract.** Not an issue; costs nothing, shows
  progress on its own.
- **Issue = one agent run, one branch, one PR.**
- **One GitHub repository per macroprogetto**, not per contract and not per client.
  A macroprogetto's contracts land in the same repository, one after another; a
  client running two distinct macroprogetti gets two repositories. Decided
  2026-09-24 (`docs/design/DECISIONS.md`), because a contract is a slice of Linear
  work, not a reason to fork a codebase.
- **Private by default**, under the `letsrebase` GitHub org, unless the contract says
  otherwise.

Full reasoning and the alternatives considered: `docs/design/DECISIONS.md`,
2026-09-24 rows.

## The known gap, until the plan changes

Linear's Free plan has no guest role and no private teams: every invited member is
an Admin and sees every team in the workspace, `rebase`'s own product work included.
A client's own team keeps that client's initiatives and projects out of another
client's board, so there is no cross-client leak under this model, only a leak into
`rebase`'s own internal roadmap — and only while the Free plan's two-team cap keeps
at most one client team open at a time (`docs/design/DECISIONS.md`, 2026-09-24).
Decided 2026-09-24: accepted for now, revisited the day a Business-plan upgrade is
worth its per-seat cost. Until then, the repository's own `AGENTS.md` tells every
freelancer the boundary is a courtesy, not an enforced one, and the setup checklist
below says the same to the person doing the inviting.

## What is in `template/`

Copied into the new (or existing) repository, with its placeholders resolved:

```
AGENTS.md                       the portable core: read by every harness that reads
                                 AGENTS.md natively (Claude Code, Codex, Cursor,
                                 Windsurf, omp, ...), self-sufficient on its own
CLAUDE.md                       @AGENTS.md, plus Claude-Code-only rules if any appear
.github/PULL_REQUEST_TEMPLATE.md   identical to the monorepo's own, it is already generic
.claude/skills/
  linear-ticket/SKILL.md        the Linear calls, in order, with the API's own quirks
  linear-content/SKILL.md       how a card, a comment, an update is written
  pr-creation/SKILL.md          branch, commit, PR body, review loop
```

`.claude/skills/` is read by Claude Code and omp; a harness with no skill mechanism
still gets the whole contract from `AGENTS.md` alone, which is why that file is never
just a pointer to the skills — the skills are the detailed procedure, `AGENTS.md` is
the part every harness can read on its own. If a harness needs its own filename to
pick the rules up (the way Claude Code wants `CLAUDE.md` and Warp wants `WARP.md`),
add a thin pointer file in that shape (`@AGENTS.md` plus anything genuinely specific
to that harness), never a second copy of the rules.

Everything in `template/` is written in English and in the second person, the shape
a fresh repository's own conventions file takes; it is not rebase-internal prose and
carries no `docs/tracker.md`-style narrative history, on purpose — a freelancer reads
it once before their first card, not to learn how the rule was arrived at.

## Doing the setup

If your harness reads `.claude/skills/`, use `client-repo-onboarding`: it reads the
engagement's spec document (asking for it, or for the fields it is missing), does the
Linear calls below itself, and runs the script. What follows is that same procedure by
hand — read it either way, since it is also what a harness with no skill mechanism
falls back to.

For a genuinely new repository:

1. **Create the Linear structures**, by hand, or let `client-repo-onboarding` do it
   from a spec document instead (`linear-rebase` MCP server, the workspace's own
   hosted MCP, or the web app):
   this client's own team, if it does not exist yet — Linear's MCP surface has no
   team-creation call, so this one step is always a human clicking "New team" in
   `linear.app/letsrebase` settings, once per client, on the Free plan's second and
   last team slot (check no other client already holds it; a second client's team
   needs a Business-plan upgrade first); then the initiative (if the macroprogetto is
   new) and the first project (the contract), with `save_project`, the same fields
   `docs/tracker.md` uses for the product team: a lead, both members if more than one
   of us is involved, an outcome name that is a verb and the work it does.
2. **Run the script**:
   ```bash
   node tooling/client-repo-starter/new-client-repo.mjs \
     --repo point --org letsrebase \
     --project-name POINT --one-liner "padel and tennis session recording platform" \
     --linear-team Point --linear-prefix POINT \
     --initiative "POINT" --linear-project "MVP delivery" \
     --create-repo --worktree --no-greptile
   ```
   `--create-repo` creates a fresh private GitHub repository and pushes the
   scaffold as its first commit. Omit it to write the files into an existing
   checkout instead (see § Retrofitting below); the script never overwrites a file
   that already exists unless `--force` is given, and always prints what it left
   alone.
3. **Invite the freelancer**: GitHub collaborator on the repo, Linear member on the
   workspace (§ The known gap — say so to them, in the same message). Point them at
   the repository's own `AGENTS.md`.

## Retrofitting an existing repository

The pilot is `letsrebase/point`: real code, a real contract, no Linear and no
`AGENTS.md` yet. The script's file-writing step is the same one line without
`--create-repo`, pointed at the existing checkout, and it is additive: an existing
`README.md` or CI workflow is never touched, and content already in a file the
template would otherwise write (`point`'s own `WARP.md`) is folded into the new
`AGENTS.md`'s own § This codebase rather than discarded — done by hand for the pilot,
since folding real prose is not a mechanical merge a script should attempt. The
retrofit lands as a branch and a pull request like any other change, reviewed before
it merges, not pushed straight to `main` on a repository someone else is actively
committing to.

That additive rule cuts the other way once a repository already carries its own
`AGENTS.md`: a later change to `template/AGENTS.md` (a new rule, like this PR's
project-update paragraph) does not reach that repository just because the script
runs again, since an existing file is skipped without `--force`. Carrying a template
change into an already-retrofitted repository is the same by-hand fold as the pilot's
own `WARP.md` merge above, not a second run of the script, and it lands as its own
small PR on that repository, reviewed the same way.

## CI: the shared self-hosted runner pool

Every private client repository is eligible for `private-clients`, the shared
self-hosted Actions runner pool documented in `docs/ci-runner-pool.md`; a hosted
GitHub Actions runner bills the org's own plan, and `letsrebase/point` already hit
that plan's payment block once. `new-client-repo.mjs` scopes the repository into
the runner group automatically, in both the `--create-repo` and the retrofit path,
the moment the repository exists on GitHub; it prints whether that step actually
ran or has to be done by hand later (no `admin:org` scope on the token, or the
repository does not exist on GitHub yet).

What the script does not do: write the repository's own CI workflow. That is
contract-specific (a Node stack, a Python one, both, a different service mix
entirely), so there is no CI template in `template/`. Once one exists, its jobs use
`runs-on: [self-hosted, linux, x64]` rather than `ubuntu-latest`; `docs/ci-runner-pool.md`
carries what changes on a self-hosted host that a hosted runner's isolated VM hides,
most importantly a service container's host port: three runner instances share one
Docker daemon, so a fixed `5432:5432` collides the moment two Postgres-backed jobs
land at once, where `letsrebase/point`'s own `ci.yml` is the worked example of the
dynamic-port fix.

## Configurable per contract

Two knobs the script takes, because not every contract carries the same weight:

- `--worktree` / `--no-worktree` (default: on). Recommended on always: more than one
  person — us, a freelancer, an agent each runs — can be committing to the same
  repository, and a worktree is what keeps one person's uncommitted state off
  another's.
- `--greptile` / `--no-greptile` (default: off). On only for a contract that already
  has Greptile configured on the repository (app.greptile.com); the generated
  `pr-creation` skill includes or omits the review-gate section accordingly. Off by
  default because most client repositories will not have it wired up on day one.

Commit conventions and the PR template are never configurable: every contract uses
the same shape `letsrebase/rebase` does (`docs/design/DECISIONS.md`, 2026-09-24).
