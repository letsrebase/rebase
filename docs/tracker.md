# The tracker

Linear is where the work is recorded, and using it is not optional. An agent that fixes
something real and leaves no trace has done half the job: the next person, human or
agent, starts from the board, and what is not there did not happen. Linear is the only
tracker for the work this repository does: a GitHub Project on another repository is not
part of its flow, and the one kind of GitHub issue that lives here, the `roadmap` label,
is the public roadmap rather than development work (`AGENTS.md` § Conventions).

## Where things are

| | |
|---|---|
| Workspace | `letsrebase`, the Linear slug since the workspace itself was renamed on 2026-09-15, the same day as the GitHub org |
| Team | **rebase**, issue prefix `REB-`. The team was `Orbiters` with prefix `ORB-` until 2026-09-15; the rename kept every issue's number, so an `ORB-193` you find in older text or history is `REB-193` today. One team, and that does not change |
| Initiative | a product, permanent: `Website`, `Hub`, `PigroCRM`, `Monorepo` |
| Project | a release, or a body of work with an end. It closes when it ships, which is what lets its issues archive. Named with a verb and the work it does (`Give every space its own team`): no initiative prefix, no version number, no state word |
| Milestone | the work that lands when it closes, named with a verb (`Cut both wizards to three screens`), inside a project's release. Not an issue; costs nothing, shows progress on its own |
| Issue | one agent run, one worktree; its PR is the milestone's when one is open, its own otherwise |
| Priority | Linear's own field: Urgent, High, Medium, Low. Never a label |
| Effort | Linear's own estimate field. Never a label |
| Statuses | `Backlog`, `Todo`, `In Progress`, `In Review`, `Done`, `Canceled` |
| Type labels | group **type**, exactly one, Linear enforces it because it is a group: `feature`, `fix`, `refactor`, `test`, `chore`, `ci`, `docs`, `design`, `security`, `spike` |
| Area labels | group **Area**, exactly one, Linear enforces it because it is a group: `area:api`, `area:brand`, `area:ci`, `area:core`, `area:hub`, `area:infra`, `area:mcp`, `area:repo`, `area:web`, `area:website` |
| Assignee | who owns the card and will do the work. A claim, not a hint: see § Who owns a card |
| Other flat labels | `flagship` for headline work, `parallel` for an issue that collides with nothing, in the files or between us, so whoever is free may pick it up whoever filed it, as long as nobody has claimed it yet |

The two lists above are the board's, checked against `list_issue_labels` with
`includeGroups: true` on 2026-09-10, and the board is the authority: an earlier version of
this page named `Bug` and `core`, and an issue filed with those names failed with "Could
not find labels" (REB-33); a later one still listed nine area labels after `area:hub` had
made them ten (REB-76). Five of the type labels carry a description on the board, and
it is the one to apply: `fix` is something that does not do what it says it does;
`feature` is new behaviour a user or an agent can observe; `refactor` is existing behaviour
made better with no new capability; `chore` is maintenance with no change in behaviour;
`docs` is documentation that stands on its own. `test`, `ci`, `design`, `security` and
`spike` mean what their names say.

The projects on the board, read with `list_projects` on 2026-09-16, 2026-09-17 and
2026-09-22. This table is a snapshot and the board is the authority: `list_projects` with
`team: "rebase"`, which answers completed projects too, is what to trust when the two
disagree. Opening or closing a project is a board action with no PR of its own, so whoever
does it adds or updates the row here, in the PR that ships the release or in one of its own.

| Initiative | Project | Lead | State on 2026-09-22 |
|---|---|---|---|
| `PigroCRM` | `Ship from CI, with gates that catch real defects` | Ivan | In Progress |
| `PigroCRM` | `Make a new space ready on day one` | Ivan | In Progress, opened 2026-09-12 |
| `Hub` | `Build a home for signups and the company flow` | Ivan | In Progress |
| `Hub` | `Align the wizard UI with the site` | Lorenzo | In Progress |
| `Website` | `Website v1 - the public site, live and correct on a phone` | Lorenzo | Completed, 2026-09-16 |
| `Website` | `Make the landing hold up everywhere` | Lorenzo | In Progress |
| `Monorepo` | `Open a preview of every change` | Lorenzo | In Progress |
| `Monorepo` | `Make the site visible to search` | Lorenzo | In Progress |
| `Monorepo` | `Monorepo hygiene v1 - CI cost, licence and the English rule` | Lorenzo | Completed, 2026-09-10 |
| `Monorepo` | `Rebrand v2 - orbiters leaves the code` | Lorenzo | Completed, 2026-09-16 (opened 2026-09-15) |
| `Monorepo` | `Clear the known defects from the trunk` | Lorenzo | In Progress, opened 2026-09-16 |
| `Hub` | `Hub v2 - one hub, and an admin is a member with one more section` | Lorenzo | Completed, 2026-09-22 (opened 2026-09-17) |
| `PigroCRM` | `Give every space its own team` | Lorenzo | Planned, opened 2026-09-17 |
| `Monorepo` | `Shared UI v1 - the hub and the CRM look like the site` | Lorenzo | Completed, 2026-09-22 (opened 2026-09-17) |
| `Website` | `Website v3 - routes in English` | Lorenzo | Completed, 2026-09-21 (opened and shipped the same day) |
| `Hub` | `Hub v3 - routes in English` | Lorenzo | Completed, 2026-09-21 (opened and shipped the same day) |
| `PigroCRM` | `PigroCRM v4 - routes in English` | Ivan | Completed, 2026-09-21 (opened and shipped the same day) |
| `Monorepo` | `Choose the wordmark that carries the meaning` | Lorenzo | Planned, opened 2026-09-22 |
| `Monorepo` | `Make the backlog readable at a glance` | Lorenzo | In Progress, opened 2026-09-22 |
| `PigroCRM` | `Bring mastro's ledger, invoice import and forecasting into PigroCRM` | Lorenzo | Planned, opened 2026-09-22 |

The open projects were renamed on 2026-09-22 to a verb and the work it does
(§ Naming); the completed rows keep the name each shipped under, since a record
is not rewritten.

`Monorepo hygiene v1` was where repository-wide work that belongs to no product went
(CI cost, the licence, this page). It is closed, and nothing has replaced it: a
repository-wide issue that fits no open `Monorepo` project is filed with no project,
which is what REB-131 and REB-136 did, until somebody opens the next repository-wide
`Monorepo` project with a scope it can reach.

**The plan cap counts every issue that is not archived, whatever its state.** The
workspace is on Linear's Free plan, 250 issues, and `save_issue` answers `You've exceeded
the free issue limit` past it. A Done issue keeps counting until it is archived, the team's
auto-archive period is six months, and an issue inside a project that is still open never
archives before that. On 2026-09-17 the board stood at 275 while the three projects above
were being filed; the 69 terminal issues (Done, Canceled, Duplicate) that sat in a
Completed project or in no project were archived by hand through the API, which is what
Linear would have done in six months and is undone from the archive view, and the count
went to 206. Before a bulk filing, read `teams { issueCount }` and, when there is no room,
archive terminal issues of Completed projects. Never delete an issue to make room: an
archived issue keeps its URL, its comments and its links, a deleted one does not.

Every project always carries a lead and both members, Lorenzo and Ivan, no matter who
leads it. A project created without a lead or without both members is incomplete. The MCP
surface cannot repair that, since `save_project` takes a `lead` and has no member field
(§ API details), but the GraphQL API can: `projectUpdate` with `memberIds` sets the full
member list, and the 2026-09-22 backlog audit used it to put both members on every live
project, which is what REB-137 had been waiting for a hand UI pass to do. At creation,
either add the second member with that call in the same breath, or read the project back
and fix it before its first issue lands.

This replaces the old rule that gave every monorepo project (`projects/pigrocrm`,
`projects/website`) its own permanent Linear project. Initiatives are the permanent
containers now, one per product, and a project is scoped work with an end inside one:
an issue in a project that never closes never archives, which is why a project needs a
scope it can actually reach rather than a standing label for a whole product.

Area labels stay a group, so exactly one per issue, and Linear enforces it. I tried to make
them flat on 2026-09-09, because in a monorepo a change genuinely spans two surfaces and
forcing one area drops the other from every area query. It does not work: **a label cannot
be taken out of a group from the MCP surface.** `save_issue_label` with `parent: null`
answers success and echoes `parent` unchanged, and the only thing the UI offers on a group
is Delete, which would take the children with it and they are already applied to every
issue here. So the rule that survives is the old one, and it has a useful side effect: an
issue that genuinely spans two areas is usually two issues, or belongs to the area that
owns the fix. Note that the personal workspace does differ here, where the area labels were
created flat from the start.

## Who owns a card

Two of us work this board, each running agents of their own, and the only thing that
keeps two agents off the same work is the **assignee**. It is a claim, not a hint.

- **A card assigned to somebody is theirs.** You do not assign it to yourself, do not
  move its status, do not open a PR for it. If you believe it should be yours, say so in
  a comment and stop there.
- **A card with no assignee belongs to whoever filed it**, until they say otherwise. An
  empty assignee is an omission, not an invitation.
- **A card labelled `parallel` and not yet claimed may be picked up by whoever is
  free**, whoever filed it. An assignee, or a status of `In Progress` or `In Review`,
  wins over the label: `parallel` says the work collides with nothing, neither in the
  files nor between us, not that somebody's started work is up for grabs.
- So the cards you may work are: assigned to you, or unassigned and filed by you, or
  labelled `parallel` and unclaimed. Nothing else, and there is no exception for "it is
  quick".
- **Being asked for a card by name does not make it yours.** The account this session
  writes as is not always the person talking to you. When they are its assignee and you
  are not, they reassign it, or add `parallel`, and then you proceed; you never make that
  change yourself, and until it is made the card is theirs.
- **The lead of a project does not own its issues.** The lead is who decides when a
  question is a decision; the assignee is who does the work.

`assignee` is therefore set on every issue you file, including work left for later,
because a card nobody owns is one the other agent will reasonably take. On an update it
is sent only when changing the owner is the point, since `save_issue` overwrites whatever
it is given (§ API details). There is no `createdBy` filter in the API either, so an
unassigned card's owner is not queryable without reading it.

## Reaching it

Two skills in `.claude/skills/` carry this contract to the moment it is needed:
`linear-ticket` (the calls, in order, with every field) and `linear-content` (how the
words are written). This file stays the source; a disagreement between it and a skill is
a bug in the skill.

The MCP server is `linear-rebase`, enrolled per client outside this repository. It is
the Linear surface to use: another Linear server on the same machine reaches another
company's board, not this one. When `linear-rebase` is not in the session, the web app
at `linear.app/letsrebase` is the fallback, as the `linear-ticket` skill says; a failing
or wrong-workspace connector is never a reason to start work or open a PR without its
card.

Before your first write in a session, two read calls. `list_projects` or `list_issues`,
to check the team name that comes back: two Linear workspaces are enrolled on this
machine and the failure mode of picking the wrong one is filing a client's work in the
wrong company's board. Then `get_user` with `"me"`, to learn **which of us this session
writes as**, because the token belongs to one account and both of us run agents against
this board. Keep its `id`, not the display name: ownership is decided by comparing that
id against `assigneeId` and `createdById`, which is one string equality against a value
that cannot be re-rendered. That account is what `assignee: "me"` means, what "yours"
means everywhere below, and it is not necessarily the person who is talking to you.

## The loop

**Before starting work.** Search the board for the thing you are about to do, and for
its neighbours (next paragraph), before the first file changes. Read who owns what comes
back, since a card that exists is not automatically available: an issue that is yours
you use, an issue that is somebody else's you leave. If nothing exists, and the work will
outlive this run, file one before you start rather than after: an issue written
afterwards is a summary, and it loses the reasons.

**Its neighbours.** The same card is one search; the cards your change can collide with
or settle are another, and it happens every time, whether the card is found or filed.
Neighbours are the open cards in the same `area:*`, and the ones whose title or body
names the same screen, route, table or file. One that is `In Progress` or `In Review`
under the other person, on the same surface, is work you do not overlap: narrow yours
to what theirs leaves alone, or wait for it, and say which on your card. One in
`Backlog` or `Todo` that your change would close is your card, if it is yours to take
(§ Who owns a card), and nothing new is filed; one that is not yours, or that your
change would break, make easier or has to land after, is linked to yours (`relatedTo`,
`blocks`, `blockedBy`, `duplicateOf` on `save_issue`) and named in the body, so it is
not rediscovered from scratch by the next reader. The scan comes before the `save_issue`
that files your card or moves it to `In Progress`, so its links and its **Adjacent**
paragraph land in that call and not a second one. "Nothing adjacent" is written too: a
scan that leaves no trace cannot be told from a scan that did not happen.

**When you start.** Check the card is yours (§ Who owns a card): assigned to you,
unassigned and filed by you, or labelled `parallel`. If it is somebody else's, leave it
alone, comment if you have something to add, and pick another. If it is yours, move it
to `In Progress` and set `assignee` to yourself in the same call, so the other agent can
see it is taken.

**While you work.** The card follows the work while it happens, never rebuilt from
memory at the end. A comment when you learn something that changes the issue: a
reproduction, a measurement, a cause that turned out to be different from the title, a
decision that is now the project's lead's. A comment when the work changes shape: a
scope dropped or added, a surface or a file you had not expected to touch, a neighbour
you found late. A comment when you stop on something outside your hands, saying what you
are waiting for and from whom, so a reader does not have to find you to know. Comments
are cheap and they are what makes an issue readable in a month, and a card that has read
`In Progress` for a day with nothing under it tells the other agent nothing when they
are deciding whether to touch the same files.

**Which PR.** A milestone in progress has one branch and one draft PR, opened when the
work starts and merged when it closes: hand-driven cards in that milestone commit onto
the milestone branch instead of opening a PR each. The milestone branch carries no
issue id on purpose, so Linear's automation stays out of it and the cards move by
hand: each to `In Progress` when its run starts, each to `Done` with the merge, with
the closing evidence in its own comment. The PR body lists the cards it lands. A card
outside an open milestone keeps its own branch and PR, and so do the two mechanisms
that verify a merged PR per card: the unattended pickup loop, and the Sencare repos,
whose staging pipeline reads one Jira key per commit.

**When your PR is open.** Comment the PR URL on the issue. The PR links itself to the
card within seconds, and since 2026-09-16 the team's automation moves the state too
(§ Commits and issues): the PR opening puts the card in `In Progress` (an `In Review`
set before that fires is overwritten, REB-247), and the merge sets `Done` the moment it
lands. On a milestone's draft PR none of that fires (§ Which PR): the URL goes on every
card the PR lists, and each card moves by hand at its run's start and at the merge. So
the evidence that would have closed the card goes in a comment before or right after
the merge, not in a state change you make. From here to the merge the card keeps
following the PR: the review's findings and what you did with them, Greptile's findings
on each push and whether each was fixed or answered (the PR merges only when its last
review leaves nothing open and its score reads 5/5), a CI run that went red and why, a
push that changed what the PR is. One line each is enough, and silence is not.

**When you finish.** `Done` means verified on the surface the issue is about, and the
comment that closes it says how. A green CI check closes a CI issue. A deploy issue
closes when the deploy has run and a request that exercises the new code came back
right, never on a 200 from an unchanged path. If you cannot verify it, say so on the
card and do not merge yet: a PR that merges closes its issue, so an unverified merge is
a card closed on nothing.

**Project updates.** Post one whenever something happened that a reader could not infer
from the issue list: a milestone slipped, a health change (`onTrack`, `atRisk`,
`offTrack`), a decision taken, a release shipped. `save_status_update` requires
`type: "project"` along with the project, so a call without it fails validation. An
update is three sentences at most: what moved, the health and why, the next visible
thing. An update that only restates the board is noise, but skipping one when the
board alone would mislead a reader is worse.

## What an issue carries

The board is read by two people scanning for "who is doing what, how far", not
studied. Every field has a budget, and the budget is the rule.

- A **title under 80 characters, starting with a verb**: it names the work the
  card does, the same shape as a project name (`Drop the stale orbiters database
  from the CRM host`, `Make the CV optional in the wizard and the member area`).
  One clause, or two when the second only names what makes the first visible; a
  colon means the second half belongs in the body. The observed problem is not
  lost: it is the `**Observed.**` line of the body, one screen down.
- A **body that fits one screen**: at most four bold lead words
  (`**Observed.**`, `**Needed.**`, and only when they have something to say
  `**Done when.**`, `**Not here.**`, `**Adjacent.**`), each two or three sentences.
  Point at the file and line, the spec section, the run id; do not paste them. The
  measurement, the reproduction, the design options and the full neighbour scan
  belong in the
  commit, the PR or the project's document; the card indexes them in one line.
  A body an agent had to write at midnight is a body its reader will not write
  back at noon.
- **Project**, when one fits (a repository-wide issue may have none, § Where things
  are), its **milestone**, one **area:\*** label, one **type** label, a **priority**,
  an **estimate**, and an **assignee**. `save_issue` takes all of this in the same
  call, so an issue that is missing one of them is a mistake, not the accident of a
  skipped second call. The assignee is yourself when you will do the work, the
  person who asked for it when they will, and never empty: an unowned card is one
  the other agent will take.
- What you deliberately did **not** do, in one sentence, when there is such a
  thing. An issue that hides a decision costs a whole round trip later.

## Naming, on the board

Applies to every project, milestone and issue title, and to the labels.

- **A project is named with a verb and the work it does**: `Give every space its
  own team`, `Align the wizard UI with the site`. A name that only states the
  outcome (`A space has a team`) reads as a riddle to anyone who did not write it.
  The initiative field already says which product it belongs to, so the name never
  repeats the initiative and carries no version number: `PigroCRM v3 - ...` said
  `PigroCRM` twice and `v3` meant nothing you could check. It never names a state
  of the work (`deployed`, `done`, `closed`, `complete`, `shipped`): the project's
  own status already says that, and a name that repeats it goes stale the moment
  the status moves.
- **A milestone is named with a verb and the work it lands**, like a project and
  an issue: `Cut both wizards to three screens`, `Invite a colleague by email`.
  Its description is one sentence starting `Closes when`, with the observable
  check: the page, the command, the assertion. Nothing else: not the history, not
  the alternatives, not the file list.
- **A `**Done when.**` line is one of the lead words above, and it is one line.**
  If the check does not fit one line, it is not a done-when, it is the work.
- **No emoji, no status words, no version of the same idea twice.** A title says
  each thing once: `Make the CRM report to PostHog and show the funnel` is
  allowed to name the mechanism because the mechanism *is* the visible outcome;
  `Rewrite X, which means the funnel is now readable in Y` is two sentences in one
  title.

## Rules

- **File what you find.** A defect you noticed and did not fix goes on the board before
  you finish, with the evidence you already have in your hands. This is the single rule
  that decays fastest under time pressure and the one worth most.
- **Do not silently fix somebody else's defect** in an unrelated change. File it, and
  say in your own commit that you left it alone. Choosing the fix is often a design call
  that belongs to whoever owns that code.
- **Do not close what you did not verify**, and do not move a card on somebody's promise
  that it works.
- **Do not take a card that is not yours**, and do not hand your own to somebody else
  without asking them. Assigned to another person, or `In Progress` or `In Review` under
  their name, means hands off: no assignee change, no status change, no branch, no PR,
  and nothing re-parented under it with `parentId` without asking.
  Comment if you have something useful, then pick different work.
- **One area per issue**, because both label families are groups and Linear allows only
  one label from a group. An issue that genuinely spans two areas is usually two issues,
  or belongs to the area that owns the fix. Do not spend a call trying to apply two: the
  second is silently dropped.
- **Never invent an issue id.** If you reference `REB-N` in a commit, a comment or a
  document, it exists and you have read it.
- **The tracker is not documentation.** A design rule goes in `docs/design/DECISIONS.md`,
  a procedure goes in `AGENTS.md` or a project README, and an issue points at them. Work
  that is finished and needs no decision is recorded by its commit, not by a `Done` issue
  filed for the sake of having one.
- **English, first person, no em dashes**, same as every other repo-facing surface. See
  the Conventions section of the root `AGENTS.md`.

## Commits and issues

Linear's GitHub app (`linear-code`) is installed on the GitHub org since 2026-09-09,
on every repository, granted by the org owner (`slavni96`) after Lorenzo was made an
owner of the org the same day. That org was `joinorbiters` then and is `letsrebase`
since 2026-09-15 (REB-204); the installation followed the rename, and so did the links
Linear had already attached to pull requests. Installing it gives Linear the pull
request and issue events, and the diffs show up in Linear for anyone whose personal
GitHub account is connected there.

**Linking works, and it is measured.** PR #33 attached itself to REB-80 within 25
seconds of `gh pr create`, matching on the `orb-80` in the branch name rather than on
the exact branch Linear suggests, so any branch carrying the id is enough.

**The state moves on PR events since 2026-09-16, and that is a different mechanism.**
Status changes are the team's own pull request automation (Linear: Settings, Team,
Workflow), configured per team. It was off until 2026-09-16 (REB-80 stayed `In
Progress` with PR #33 open and linked, and the five PRs merged on 2026-09-10 closed
nothing) and it is on since: REB-247 read `In Review` by hand at 17:30:40, `In Progress`
at 17:30:43 when PR #161 (opened 17:30:32) was seen, and `Done` at 17:46:42 when it
merged. So a PR opening on a branch that carries the id moves the card to `In
Progress`, and the merge moves it to `Done`; nothing sets `In Review` by hand since,
and a card with a PR open reads `In Progress`. What still needs a hand: the `In
Progress` move before the branch exists (with the neighbour scan), every comment
(the closing evidence goes in before or right after the merge, since the state will not
wait for it), `Canceled`, and the `In production` comment when a tag ships. Reference
the issue in the commit body when the commit is the work (`REB-9 covers the real fix`)
and treat that reference as a pointer. The setting was turned on from the team's
workflow settings on 2026-09-16, not through this repository; the `docs/design/DECISIONS.md`
row for it is still to be written by whoever turned it on.

## API details worth knowing before you waste a call

- `save_comment` takes `issueId`, not `issue`.
- `save_issue` accepts `state`, but `get_issue` echoes it back under `status`.
- `milestone` is accepted on write and not echoed: verify through `list_milestones` and
  its `progress`. The read field is `projectMilestone`, and passing *that* name on write
  is silently ignored like `labelIds`: the call answers `success`, the card keeps no
  milestone, and only `list_milestones` progress shows it (measured on REB-338,
  2026-09-22, where it cost two wasted calls).
- Label names are case-insensitive across the whole workspace, and retiring a label
  does not free its name. Creating a lowercase label that collides with an old
  capitalised one (`Bug` versus `bug`, say) silently resolves onto the old, retired
  label instead of creating a new one. If that happens, fix it by reusing the colliding
  label: update it by id with the new name and the right parent, do not try to create
  a second one.
- `save_issue` **overwrites** `assignee` and `state` with whatever you send, silently
  and with no compare-and-set. Nothing in the response says the card had been somebody
  else's a second earlier, and the field keeps no history the MCP surface can read, so
  reading the owner before you write is the only guard there is.
- `gitBranchName` is rendered for **whoever reads the issue**, not for its assignee: the
  same card comes back as `fiorelorenzo/reb-41-...` to one of us and `mariorossi/reb-41-...`
  to the other. A branch prefix therefore proves nothing about who owns the work.
- `list_issues` filters on `assignee: "me"` correctly, and does **not** filter on an
  empty one: `assignee: null` and `assignee: "null"` are both accepted and both silently
  ignored, so the response carries everybody's cards while looking like an answer
  (measured 2026-09-09, against the tool's own description). There is no `createdBy`
  filter either. Ask for `assigneeId` and `createdById` in `fields` and filter the rows
  yourself, and bound the call before you do: it defaults to 50 rows and pages with
  `cursor`, so an unbounded one answers with a slice of a board already past REB-58 that
  reads like the whole of it. Narrow server-side first (`state: "Todo"`, then
  `state: "Backlog"`) and raise `limit`.
- `list_issues` takes one `state` per call, and a state **type** is a valid value:
  `state: "started"` answers `In Progress` and `In Review` together, `"unstarted"` is
  `Todo`, `"backlog"` is `Backlog` (measured 2026-09-10). Three calls cover every open
  card; combined with `label`, they are the neighbour scan of § The loop.
- `list_issues` with `query` ranks, it does not filter: `query: "columns.tsx"` answered
  the three invoice-list cards first and then twenty unrelated ones with a next page
  (measured 2026-09-10). Read the first handful and stop where the titles stop being
  about your surface; a count of what came back means nothing.
- Initiatives cannot be created from the MCP surface at all. `save_project` can attach
  an existing initiative with `addInitiatives`, but there is no `save_initiative`.
  Initiatives are created by hand in the Linear UI; automation only creates projects and
  issues underneath them.
- Project members cannot be set from the MCP surface either. `save_project` takes `lead`
  and has no member field, and `list_projects` with `includeMembers: true` only reads
  them, so the rule that every project carries both members (§ Where things are) is kept
  by hand in the Linear UI, and read back with that call.
