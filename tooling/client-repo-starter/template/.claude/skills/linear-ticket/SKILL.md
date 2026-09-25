---
name: linear-ticket
description: Use when filing, finding, moving or closing a Linear issue for this repository (team {{LINEAR_TEAM}}, {{LINEAR_PREFIX}}-N), or posting a project update, and at the start of any change here, before the first file is touched, to find the card and the cards next to it. The MCP workflow with every field in one call, the neighbour scan, the state changes at each step, and the API quirks that otherwise cost a wasted call.
---

# Working the Linear board

`AGENTS.md` is the contract: what an issue carries, when it moves, what closes it.
Read it once per session. This skill is the sequence of calls and the traps. Content
(how to write the body, a comment, an update) is the `linear-content` skill.

## First call of the session

Read with whatever Linear MCP server your session has, or fall back to the web app
(`AGENTS.md` § Tracker: Linear). Before any write: `list_projects` or `list_issues`
with `team: "{{LINEAR_TEAM}}"`, and check the team that comes back really is
**{{LINEAR_TEAM}}** (`{{LINEAR_PREFIX}}-`): more than one Linear workspace can be
enrolled in a session, and filing this repository's work on the wrong board is the
failure mode an empty or surprising result usually means, not an empty tracker. Then
`get_user` with `query: "me"`, and keep the `id` it returns, not the display name: it
is the account this session writes as, it is what `assignee: "me"` means, and it is
what every ownership check below compares against.

## When no Linear tool is in the session

What reaches the board then is the web app, logged in to `linear.app/letsrebase`. The
lists are readable as page text (`/team/{{LINEAR_PREFIX}}/active`,
`/team/{{LINEAR_PREFIX}}/backlog`) for the neighbour scan below. A new card is one
URL with every field except the relations:

```
https://linear.app/letsrebase/team/{{LINEAR_PREFIX}}/new?title=..&description=..&status=In%20Progress&priority=Medium&assignee=me&labels=feature,area:api&project=<project name>
```

Build it with `URLSearchParams`, turning `+` into `%20`; check the modal shows each
field before clicking "Create issue"; open the issue afterwards and read the fields
back. The branch is the slug of that page's URL after `{{LINEAR_PREFIX}}-N/`,
prefixed `<you>/`. Relations and comments go on by hand; never type multi-line text
in the quick-add box, Enter submits and the rest runs as keyboard shortcuts on the
issue. If neither surface is reachable, the card is not filed and the work waits:
say so to whoever you are working with, do not start without one.

## Finding before filing, and whether it is yours to take

`list_issues` with `team: "{{LINEAR_TEAM}}"` and `query: "<two or three words of the
problem>"`, then again with the relevant `area:*` label. Ask for `assigneeId`,
`createdById`, `statusType` and `labels` in `fields`: an issue that exists is not
automatically an issue that is available.

- `assigneeId` is you: yours. Use it, move it, comment on it.
- `assigneeId` is somebody else, or `statusType` is `started` under their name:
  theirs. Leave every field alone; comment only if you have something useful, and
  pick other work. Being asked for it by name does not make it yours: if it should
  be, its owner reassigns it and you proceed from there.
- No `assigneeId`: it belongs to whoever filed it (`createdById`).
- Labelled `parallel`, with no `assigneeId` and not `started`: whoever is free may
  take it. The label opens an unclaimed card; it never reopens a claimed one.

A new issue is filed only when none exists and the work will outlive this run, and
then **before** the work starts, never written afterwards as a summary.

## The neighbours, before the first file changes

Finding the same card is one search; finding the cards your change can collide with
or settle is another, and it happens every time, found or filed. Three reads, each
with `fields: ["id", "title", "status", "statusType", "labels", "project",
"assigneeId", "createdById"]`:

1. **The area, open states only.** `list_issues` with `team: "{{LINEAR_TEAM}}"`,
   `label: "<the area:* your change lands in>"` and `state: "started"` (answers
   `In Progress` and `In Review` together), then `state: "unstarted"` (`Todo`), then
   `state: "backlog"`. One `state` per call, `limit` raised past the default 50.
2. **The surface, by name.** `list_issues` with `query:` one noun of what you are
   about to touch (the screen, the route, the table, the file's stem), one call per
   noun. `query` ranks, it does not filter: read the first handful and stop where the
   titles stop being about your surface.
3. **The project**, when the area is the wrong lens (a shared package, a change that
   spans two apps): `list_issues` with `project:` the id and the three open states,
   paging each with `cursor` until `hasNextPage` is false. Auditing whether the
   whole project is done, not just its open work, needs a fourth call with
   `includeArchived: true`, paged the same way: an issue archives on its own
   schedule independent of the project's own state, so a project whose issues
   already archived reads as empty without it (`AGENTS.md` § Tracker: Linear).

What comes back is read, not counted:

- **`started` under somebody else, on the same surface.** You do not overlap it:
  narrow yours to what theirs leaves alone, or wait for theirs, and write which on
  your card.
- **`Backlog` or `Todo` that your change would close.** If it is yours to take, move
  it and file nothing new. If not, `duplicateOf` from yours to theirs, or comment on
  theirs, and leave the rest alone.
- **A card your change would break, make easier, or has to land after.** `blocks`,
  `blockedBy`, or `relatedTo`, whichever a reader of either card would want.
- **Nothing.** Written anyway, as the body's **Adjacent** paragraph
  (`linear-content` skill), naming the calls that answered empty. A scan that leaves
  no trace cannot be told from one that did not happen.

The links and the paragraph go in the same `save_issue` call that files the card or
moves it to `In Progress`: never a second call after.

## Filing: one `save_issue` call

Every field at once; a second call to add what is missing is the sign the first was
wrong.

| Field | Value |
|---|---|
| `team` | `"{{LINEAR_TEAM}}"` |
| `project` | the project id, from `list_projects`. Project names get renamed; a lookup by an old name fails with "Could not find project". |
| `milestone` | the milestone id from `list_milestones(project)`, when the contract has milestones yet. Accepted and not echoed back: trust `list_milestones` for progress, not the response. `projectMilestone` (the name the read side uses) is silently ignored on write, like `labelIds`. |
| `title` | starts with a verb, names the work, under 80 characters, per `linear-content`. |
| `description` | per `linear-content`. Real newlines, never `\n` escapes. |
| `addLabels` | exactly two: one `type` label, one `area:*` label, both by name. `addLabels`, never `labels` (which replaces the whole set). The id-based fields (`labelIds`, `addLabelIds`) answer success and apply nothing: names, not ids. A second label from the same group is silently dropped; `list_issue_labels` with `includeGroups: true` is the source when unsure of the exact names on this board. |
| `priority` | 1 Urgent, 2 High, 3 Medium, 4 Low. A field, never a label. |
| `estimate` | the team's points, when the team uses them. |
| `assignee` | never omitted. `"me"` when you do the work now, the person who will otherwise. An empty assignee reads as free. |
| `state` | `"In Progress"` when you start now; otherwise the default. |
| `relatedTo`, `blocks`, `blockedBy`, `duplicateOf` | what the neighbour scan found: three arrays of ids you have read, or one id for `duplicateOf`. Arrays are append-only: undo with `removeRelatedTo`, `removeBlocks`, `removeBlockedBy`; `duplicateOf: null` clears it. |

Label names are case-insensitive workspace-wide and a retired label keeps its name.

## Moving it

- `In Progress` goes with `assignee: "me"` in the same call, only on a card that is
  already yours. On a card you found rather than filed, the **Adjacent** paragraph
  and the relations go in that same call, appended with
  `patch: [{ "op": "append", "text": "\n\n**Adjacent.** ..." }]`: `save_issue`
  rejects `description` and `patch` together, so a full rewrite that also needs to
  append is one `description` string with the addition already folded in.
- When the PR opens, comment the PR URL. If this Linear team has the GitHub
  integration and its pull-request automation on, the PR opening and merging move
  the state for you; if not, move it by hand at each step. From the PR to the merge
  the card keeps following it: the review's findings, a red CI run and its cause, a
  push that reshaped the PR.
- The closing comment (`Evidence:` with run ids, sha, what you exercised and what came
  back) is written before or right after the merge. A card closed with no evidence
  under it is a card closed by a robot, not by you.
- Won't-do is `Canceled` (one `l`), with the reason.

`save_issue` accepts `state`; `get_issue` echoes it as `status`, same field.
`save_issue` overwrites `assignee` with whatever you send, with no compare-and-set:
reading the owner first is the only guard against clobbering somebody else's claim.

## Commenting

`save_comment` with `issueId` (not `issue`) and `body`, in the `linear-content`
shapes, whenever: something changed the issue (a reproduction, a cause different
from the title, a decision made); the work changed shape (scope grown or narrowed, a
neighbour found late); you are waiting on something outside your hands; the PR
opened, its review's findings, a red CI run; the closing evidence. Not "working on
it", and not a step a reader could infer from the previous comment.

## Project updates and milestones

`save_status_update` with `type: "project"`, the project id, a `health` (`onTrack`,
`atRisk`, `offTrack`) and a body per `linear-content`. Post one when the board alone
would mislead a reader (a milestone slipped, a decision taken, a release shipped),
never one that only restates the issue list.

`save_milestone` wants `project` on every call, an update included: sending only
`id` and `description` fails on `project: is required`, which reads like the
milestone was not found. Name it with a verb and the outcome it lands, description
one sentence starting `Closes when`.

## References in code and commits

`{{LINEAR_PREFIX}}-N` in a commit body or a comment is a pointer to an issue you have
read: never invent one. The branch is the issue's `gitBranchName`, read from
`get_issue`, not typed by hand.
