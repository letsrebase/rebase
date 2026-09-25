# AGENTS.md: working on {{PROJECT_NAME}}

{{PROJECT_NAME}} is a rebase-managed consulting engagement: {{ONE_LINER}}. This file
carries the conventions every agent and every person follows here, whatever tool you
are running: Claude Code, Codex, Cursor, Windsurf, omp, or a plain shell. It is
self-sufficient on its own: read it fully before the first file changes. Facts
specific to this codebase (stack, commands, architecture, the things that look odd
until you have read the spec that explains them) live in § This codebase, below; keep
that section current as the project grows rather than letting it go stale.

If your harness reads `.claude/skills/`, three skills carry the same rules as
detailed, ordered procedure: `linear-ticket`, `linear-content`, `pr-creation`. Where
this file and a skill disagree, this file is right and the skill has a bug. If your
harness does not read skills, this file is the whole contract.

## Where this fits

rebase runs two shapes of engagement: forward-deployed, where a freelancer works
inside the client's own tools, and internally-managed consulting, where rebase owns
the tracker and the repository conventions so every freelancer across every contract
works the same way. This repository is the second shape. The rules below mirror the
ones `letsrebase/rebase`, rebase's own product monorepo, uses on itself
(`docs/tracker.md`, `docs/design/DECISIONS.md`), adapted for a single repository
instead of a monorepo with several products in it.

## Tracker: Linear

Workspace `letsrebase`, team **{{LINEAR_TEAM}}** (issue prefix `{{LINEAR_PREFIX}}-N`).
Work that is not on the board did not happen: there is no other tracker for this
engagement, and a GitHub issue here is not one either. Four levels:

- **Initiative**: the macroprogetto, `{{LINEAR_INITIATIVE}}`. Permanent.
- **Project**: one contract inside it, `{{LINEAR_PROJECT}}` today. Closes when the
  contract's scope ships.
- **Milestone**: a coherent outcome inside the contract. Not an issue.
- **Issue**: one agent run, one branch, one pull request.

Every issue carries a `type` label, an `area:*` label, a priority, an estimate and an
**assignee**, never empty: an unowned card reads as free to whoever else is on this
contract. `type` and `area:*` are each an enforced group (`list_issue_labels` with
`includeGroups: true` is the source of the exact names); a second label from the same
group is silently dropped, so check first rather than guessing.

**Before the first file changes**: find the card, or file it when the work will
outlive this run and no card exists yet. Read the open cards next to it, same
`area:*`, same screen, route, table or file by name, so you do not duplicate or
collide with something already in progress. Move the card to `In Progress` with
yourself as assignee in the same call that records what the scan found (or that it
found nothing).

**While you work**: the card follows the work, never rebuilt from memory at the end.
A comment at every turn a reader could not infer from the diff alone: a finding that
changes the plan, a scope that grew or narrowed, something you are waiting on. Not
one per commit, and never "working on it".

**Project updates.** Post one on the contract's Linear project whenever something
happened that the issue list alone would not show: a milestone slipped, a health
change (`onTrack`, `atRisk`, `offTrack`), a decision taken, a release shipped.
`save_status_update` with `type: "project"`, the project id in a field called
`project`, and a `health` value (each is easy to get wrong once: omitting `type` or
`health` fails validation, and `projectId` fails too even though the id is right
there). Three sentences at most, in
the `linear-content` skill's shape when your harness reads it: what moved, the health
and why, the next visible thing. An update that only restates the board is noise, but
skipping one when the board alone would mislead whoever reads only the update is the
more common mistake.

**A project does not close itself.** Nothing moves it to `Completed` when its last
issue does. An issue can also archive on its own schedule regardless of the
project's own state, so a `list_issues` query on that project without
`includeArchived: true` can read empty on a project that is actually finished, not
only on one nobody has touched. Re-check with that flag before trusting either
reading. Every issue terminal is not by itself proof the contract shipped: a
project whose issues are all `Canceled` or `Duplicate`, with nothing `Done`,
delivered nothing, and is itself `Canceled`, not `Completed`. Move it to whichever
is true yourself once you have checked.

**A card assigned to somebody else stays untouched.** Comment at most, never an
assignee or status change, however you were asked for it: this is the only thing
that keeps two people on the same contract off the same card. A card with no
assignee belongs to whoever filed it; a card labelled `parallel` and unclaimed may be
picked up by whoever is free.

**No Linear tool in your session?** `linear.app/letsrebase`, logged in with the
account you were invited with. Build the new-issue URL with every field
(`https://linear.app/letsrebase/team/{{LINEAR_PREFIX}}/new?title=..&labels=..`), or
read and write through the page itself. No card, no branch, whichever surface you
use: an issue filed after the work loses the reasons for it, and a PR against no card
is worse than no PR at all.

**Known limitation, on purpose, for now.** This workspace is on Linear's Free plan:
no guest role, no private teams, so every invited member is an Admin and can see
every team in the workspace, `rebase`'s own product work included. This repository
has its own Linear team, `{{LINEAR_TEAM}}`, not a team shared with another client, so
that separation is real; what is not yet real is a technical wall stopping anyone
from reading past it, since Free plan has no guest role to scope a member to one
team, and the plan's two-team cap means a second client's team cannot exist
alongside this one until a Business-plan upgrade happens, so today the only thing
actually reachable past this contract is `rebase`'s own internal roadmap. Stay
inside `{{LINEAR_TEAM}}` / `{{LINEAR_PROJECT}}` regardless of what the tool lets you
read.

## Commits

Conventional Commits, English, first person, written the way a person writes:

```
feat(api): a booking can be cancelled up to 2 hours before start
fix(mobile): the camera no longer bricks on a rollback reclaim
```

`type` is one of `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `ci`, `chore`,
`style`, `revert`. No em dashes, no "not just X but Y", no emoji, and never an AI
co-author trailer. The subject says what is true after the commit; the body says why
when it is not obvious: the reason is the part nobody can reconstruct later. The
last line names the issue: `{{LINEAR_PREFIX}}-N.` Commit with a pathspec
(`git add <files>`, never `-A`): more than one person can be working this repository
at once, and a bare `-A` picks up somebody else's uncommitted file.

## Pull requests

One Linear card before one branch, always: {{WORKTREE_POLICY}}

The branch is the issue's own `gitBranchName`, read from Linear, never typed by hand.

**Title**: `type(scope): what is true now`, under 72 characters: the reader's
outcome, not a description of what you touched.

**Body**, in this order, the same template `letsrebase/rebase` uses
(`.github/PULL_REQUEST_TEMPLATE.md`):

- **What this changes**: one paragraph, first person, the problem and what is true
  now.
- **How I verified it**: the command you ran and what it said, never "tests pass".
  If a UI changed, what you looked at; a video or a screenshot pair when a person
  could see or do the thing.
- **Anything a reviewer should look at twice**: delete when there is genuinely
  nothing.
- **Screenshots and video**: never deleted; say why there is none when there is
  none.

Last line: `Linear: {{LINEAR_PREFIX}}-N.` No placeholder ("not filed yet", "TBD"):
a PR without its card waits, and the person hears why.

{{REVIEW_GATE_POLICY}}

Comment the PR URL on the card the moment it opens. Close the card only against
evidence you actually exercised, on the surface the issue is about: a green check
run, a request you made and what came back, a page you opened and what it showed.

## What a person here decides, not an agent

Repository visibility, who gets access to it or to Linear, anything a client reads,
and any production deploy. Everything else a tool or this repository can answer,
look up instead of asking.

## This codebase

<!-- Replace this whole section with what an agent needs before touching the code:
the stack, how to run it locally, where the pieces live, the things that look odd
until you have read the spec that explains them. Point at files and specs rather
than repeating them. Keep it current: it is read before the source, every time. -->
