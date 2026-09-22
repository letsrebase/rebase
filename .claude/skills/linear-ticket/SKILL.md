---
name: linear-ticket
description: Use when filing, finding, moving or closing a Linear issue for this repository (team rebase, REB-N), or posting a project update, and at the start of any change here, before the first file is touched, to find the card and the cards next to it. The MCP workflow with every field in one call, the neighbour scan, the state changes at each step, and the API quirks that otherwise cost a wasted call. Triggers on "file this", "move to Done", "update the project", on starting a task or a PR, or the Italian «crea ticket», «apri un'issue», «parti da questo ticket».
---

# Working the Linear board

`docs/tracker.md` is the contract: what an issue carries, when it moves, what closes it.
Read it once per session. This skill is the sequence of calls and the traps. Content
(how to write the body, a comment, an update) is the `linear-content` skill.

## First call of the session

The MCP server is `linear-rebase`, the only Linear surface for this board. Two reads
before any write. `list_projects` or `list_issues` with `team: "rebase"`, and check the
team that comes back is **rebase** (`REB-`): two workspaces are enrolled on this
machine, and filing a client's work in the wrong company's board is the failure mode.
Then `get_user` with `query: "me"`, and keep the `id` it returns, not the display name:
it is the account this session writes as, it is what `assignee: "me"` will mean, it is
what every ownership test below compares against, and it is not necessarily the person
talking to you. Both people on this team run agents against the same board.

## When `linear-rebase` is not in the session

A session can have no `linear-rebase` and still have a Linear tool: a server named
`linear`, logged in to another company's workspace. On 2026-09-15 `get_user "me"` on it
answered an `@paid.ai` account in team `PAID`, and `list_issues` with `team: "rebase"`
answered an empty list rather than an error. An empty list from the first read is a
wrong workspace until proven otherwise, not an empty board: `get_issue` on a `REB-` id
you know exists (one from `git log origin/main`) settles it; the trunk's own history
still carries the old `ORB-` prefix on the same numbers, from before the 2026-09-15
rename, and that is a valid id to check with too. Never write through that
server.

What reaches this board then is the web app in the Chrome session, logged in to
`linear.app/letsrebase`. The lists are readable as page text
(`/team/REB/active`, `/team/REB/backlog`) for the neighbour scan. A new card is one URL,
with every field of § Filing except the relations:

```
https://linear.app/letsrebase/team/REB/new?title=..&description=..&status=In%20Progress&priority=Medium&assignee=me&labels=feature,area:hub&project=<project name>
```

Build it with `URLSearchParams` and turn `+` into `%20`, check the modal shows each field,
click «Create issue» by its ref, then open `/issue/REB-N` and read the fields back. The
branch is the slug of that page's URL after `REB-N/`, prefixed `<you>/reb-N-`. Relations
and comments are added by hand on the card; never `type` multi-line text there, Enter
submits and the rest runs as shortcuts on the issue. If neither surface is available,
the card is not filed and the work waits: say so to the person.

## Finding before filing, and whether it is yours to take

`list_issues` with `team: "rebase"` and `query: "<two or three words of the problem>"`,
then with the `area:*` label. Ask for `assigneeId`, `createdById`, `statusType` and
`labels` in `fields`, because an issue that exists is not an issue that is available
(`docs/tracker.md` § Who owns a card). Compare ids against the `id` you kept from
`get_user`, never display names:

- `assigneeId` is you: yours, use it, move it, comment on it.
- `assigneeId` is somebody else, or `statusType` is `started` under their name: theirs.
  Leave every field alone, add a comment only if you have something useful, and pick
  other work. Never `assignee: "me"` on it, not even when you are about to fix exactly
  that, and not because the person talking to you asked for it by id: if it is theirs,
  they reassign it and you proceed.
- no `assigneeId`: it belongs to whoever filed it (`createdById`).
- labelled `parallel`, with no `assigneeId` and not `started`: whoever is free may take
  it, whoever filed it. The label opens an unclaimed card; it never reopens a claimed
  one.

Looking for something to work on rather than checking one card: `list_issues` with
`assignee: "me"`, which does filter, and `label: "parallel"`, which does not filter on
ownership at all, so drop from it every row carrying an `assigneeId` that is not yours or
a `statusType` of `started`. The unassigned list is not filterable either:
`assignee: null` and `assignee: "null"` are both accepted and both silently ignored, so
the response still carries everybody's cards (measured 2026-09-09, against the tool's own
description). Narrow it server-side (`state: "Todo"`, then `state: "Backlog"`), raise
`limit` past the default 50, and filter the rows yourself. Those lists together are the
work you may start.

A new issue is filed only when none exists and the work will outlive this run, and then
**before** the work starts, never after (`docs/tracker.md`, The loop).

## The neighbours, before the first file changes

Finding the same card is one search. Finding the cards your change can collide with or
settle is another, and it happens every time, whether the card is found or filed, and
before the `save_issue` that files or moves it (`docs/tracker.md` § The loop). Three
reads, each with `fields: ["id", "title", "status", "statusType", "labels", "project",
"assigneeId", "createdById"]`:

1. **The area, open states only.** `list_issues` with `team: "rebase"`,
   `label: "<the area:* your change lands in>"` and `state: "started"`, which answers
   `In Progress` and `In Review` together; then `state: "unstarted"` (`Todo`), then
   `state: "backlog"`. One `state` per call, so three calls, with `limit` raised past
   50.
2. **The surface, by name.** `list_issues` with `query:` one noun of what you are about
   to touch, one call per noun: the screen («Fatture»), the route (`/api/invoices`), the
   table (`invoices`), the file's stem (`columns.tsx`). `query` ranks, it does not
   filter (measured 2026-09-10: `columns.tsx` gave the three invoice-list cards first,
   then twenty unrelated ones). Read the first handful and stop where the titles stop
   being about your surface.
3. **The project**, when the area is the wrong lens (a shared package, `docs/`, a
   change that spans two apps): `list_issues` with `project:` the id and the three open
   states.

What comes back is read, not counted:

- **`started` under the other person, on the same surface.** You do not overlap it.
  Narrow your card to what theirs leaves alone, or wait for theirs, and write which on
  your card. Never a second PR on the same files, and never a touch on their card beyond
  a comment (§ Finding before filing).
- **`Backlog` or `Todo` that your change would close.** If it is yours to take, it is
  your card: move it, and file nothing new. If it is not, `duplicateOf` from yours to
  theirs, or a comment on theirs saying yours will close it, and leave the rest alone.
- **A card your change would break, make easier, or has to land after.** `blocks`,
  `blockedBy`, or `relatedTo` when a reader of either card would want the other.
  `blocks`, `blockedBy` and `relatedTo` are arrays on `save_issue`, append-only;
  `duplicateOf` is one id, and `null` clears it. All four go in the same call that files
  or moves your card, never a second one.
- **Nothing.** Written anyway, as the **Adjacent** paragraph of the body per the
  `linear-content` skill, with the area calls that answered empty and the Done cards
  `query` ranked first for the surface. A scan that leaves no trace cannot be told from
  one that did not happen.

## Filing: one `save_issue` call

Every field at once; a second call to add the missing ones is the sign the first was
wrong.

| Field | Value |
|---|---|
| `team` | `"rebase"` |
| `project` | **the project id**, from `list_projects`, or omitted when the issue is repository-wide and fits no open project (`docs/tracker.md` § Where things are). Project names are a verb and the work it does, and they do get renamed: a lookup by the old name fails with "Could not find project". |
| `milestone` | the milestone id from `list_milestones(project)`. It is accepted and not echoed back: trust `list_milestones` progress, not the response. |
| `title` | starts with a verb and names the work, one clause (or two when the second names what makes the first visible), under 80 characters, per the `linear-content` skill. Not the body restated. |
| `description` | per the `linear-content` skill. Real newlines, never `\n` escapes. |
| `addLabels` | exactly two: one from the `type` group (`fix`, `feature`, `refactor`, `chore`, `docs`, `test`, `ci`, `design`, `security`, `spike`) and one from `Area` (`area:web`, `area:api`, `area:core`, `area:mcp`, `area:hub`, `area:infra`, `area:ci`, `area:website`, `area:brand`, `area:repo`). Both are groups: a second label from the same group is silently dropped. Use `addLabels`, never `labels`: `labels` replaces the whole set. |
| `priority` | 1 Urgent, 2 High, 3 Medium, 4 Low. A field, never a label. |
| `estimate` | the team's points. |
| `assignee` | never omitted. `"me"` when you will do the work, the person who asked for it when they will. A card filed for later still gets one: an empty assignee reads as free to the other agent. |
| `state` | `"In Progress"` when you start now, otherwise leave the default. |
| `relatedTo`, `blocks`, `blockedBy`, `duplicateOf` | what the neighbour scan found (§ The neighbours): the three arrays of `REB-N` you have read, or the one id for `duplicateOf`, in this same call. The arrays are append-only, so one added by mistake is undone only with `removeRelatedTo`, `removeBlocks` or `removeBlockedBy`; `duplicateOf: null` clears the one id. Read the id before you write it. |

Label names are case-insensitive workspace-wide and a retired label keeps its name:
`Chore` resolves to whatever old label owned that name. Use the exact lowercase names
above; `list_issue_labels` with `includeGroups: true` is the source when in doubt.

## Moving it

When each state applies, and what closes an issue, is `docs/tracker.md` § The loop; do
not learn it from here. What that section leaves to the caller:

- `In Progress` goes with `assignee: "me"` in the same call, and only on a card that is
  already yours (§ Finding before filing). On somebody else's card both fields stay as
  they are. On a card you found rather than filed, the **Adjacent** paragraph and the
  relations go in that same call; the paragraph as
  `patch: [{ "op": "append", "text": "\n\n**Adjacent.** ..." }]`, because `description`
  on an update replaces the whole body.
- When the PR opens, comment the PR URL. Since 2026-09-16 the team's automation moves
  the state on PR events: the PR opening puts the card in `In Progress` (an `In Review`
  set before that fires is overwritten: REB-247, PR #161) and the merge sets `Done`.
  Nothing sets `In Review` by hand since. From the PR to the merge the card keeps
  following the PR (§ Commenting): the review, a red run, a reshaped PR.
- The closing comment shaped as the `linear-content` skill says (`Evidence:` with run
  ids, sha, what you exercised and what came back) is written before or right after the
  merge, because `Done` will not wait for it. A card that closes with no evidence under
  it is a card closed by a robot, not by you.
- Won't-do is `Canceled` (one `l`), with the reason.

`save_issue` accepts `state`; `get_issue` echoes it as `status`. Same field.

`save_issue` overwrites `assignee` with whatever you send, with no compare-and-set and
nothing in the response to say whose card it was a second earlier, and the field keeps no
history you can read back. Reading the owner first is the only guard.

## Commenting

`save_comment` with `issueId` (not `issue`) and `body`. The card follows the work while
it happens (`docs/tracker.md` § The loop, While you work), so a comment at each of
these, in the shape the `linear-content` skill gives it:

- something changed the issue: a reproduction, a measurement, a cause different from the
  title, a decision that is now the project lead's;
- the work changed shape: a scope dropped or added, a file or a surface you had not
  planned to touch, a neighbour found late;
- you are waiting on something outside your hands: what, and from whom;
- the PR opened; the review's findings and what you did with them; a CI run that went
  red and why; a push that changed what the PR is;
- the closing evidence.

Not: "working on it", and not a step a reader could infer from the previous comment.
Replies in a thread take `parentId`.

## Project updates

`save_status_update` with `type: "project"`, the project id in a key called **`project`**,
a `health` (`onTrack`, `atRisk`, `offTrack`) and a body per the `linear-content` skill.
Both of those are easy to get wrong once: omitting `type` fails on `type: is required`,
and passing the id as `projectId` fails on `project is required when creating a project
status update` while the id is right there in the payload. Post one when the board alone
would mislead a reader: a release shipped, a milestone slipped, a decision taken. Not one
that restates the issue list.

## Milestones

The name is a verb and the work it lands, like a project and an issue (`Cut both
wizards to three screens`, `Invite a colleague by email`), never a status word. The
description is one sentence starting `Closes when`, with the observable check.

`save_milestone` wants `project` on **every** call, an update included: sending only
`id` and `description` fails on `project: is required`, which reads like the milestone
was not found. `sortOrder` is accepted on create and the response echoes a different
number than the one you sent, so order the milestones in the UI rather than by guessing
at it.

## References in code and commits

`REB-N` in a commit body or a comment is a pointer to an issue you have read. Never
invent one. The branch is the issue's `gitBranchName`, read from `get_issue` (or
`list_issues` with `fields: ["gitBranchName"]`), not typed by hand.

That name is rendered for **whoever asked for it**, not for the assignee: the same card
answers `fiorelorenzo/reb-41-...` to one of us and `mariorossi/reb-41-...` to the other. So
it is the branch to use once the card is yours, and it is never evidence that it is.

## What the tracker is not

Documentation: `docs/tracker.md` § Rules says where a rule, a procedure and finished
work go instead. An issue points at those places.
