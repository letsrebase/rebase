---
name: linear-ticket
description: Use when filing, finding, moving or closing a Linear issue for this repository, or posting a project update, and at the start of any change here, before the first file is touched, to find the card and the cards next to it. The MCP workflow with every field in one call, the workspace check, the neighbour scan, the state changes at each step, and the API quirks that otherwise cost a wasted call. Triggers on "file this", "move to Done", "update the project", on starting a task or a PR, or the Italian «crea ticket», «apri un'issue», «parti da questo ticket».
---

# Working the Linear board

Part of the workflow kit (`.github/kit/README.md`). The board this repository uses is in
`.github/workflow.json` under `tracker`: the workspace's URL key, the team, the card
prefix and the label vocabulary. The contract (what an issue carries, when it moves,
what closes it, who owns a card) is the file `tracker.contract` names, read once per
session. This skill is the sequence of calls and the traps. How to write the body, a
comment or an update is the `linear-content` skill.

## First calls of the session

Prove the workspace before any write. Call `get_workspace` on the Linear server you are
about to use: its `url` must be `https://linear.app/<tracker.workspace>`. One machine
often has several Linear workspaces signed in, the server's name proves nothing, and
filing a client's work in another company's board is the failure mode. Any other
answer: that server is not this board, never write through it.

Then `get_user` with `query: "me"`, and keep the `id` it returns, not the display name:
it is the account this session writes as, what `assignee: "me"` will mean, and what every
ownership test below compares against. It is not necessarily the person talking to you.

An empty list from the first `list_issues` with `team: <tracker.team>` is a wrong
workspace until proven otherwise, not an empty board: `get_issue` on a card id from `git
log origin/main` settles it.

## No Linear tool in the session

The board is still reachable in a logged-in browser at
`https://linear.app/<tracker.workspace>`. The lists read as page text
(`/team/<prefix>/active`, `/team/<prefix>/backlog`) for the neighbour scan. A new card is
one URL with every field of § Filing except the relations:

```
https://linear.app/<workspace>/team/<prefix>/new?title=..&description=..&status=In%20Progress&priority=Medium&assignee=me&labels=<type>,<area>&project=<project name>
```

Build it with `URLSearchParams` and turn `+` into `%20`, check the modal shows each
field, create it, then open `/issue/<prefix>-N` and read the fields back. Relations and
comments are added on the card by hand; never type multi-line text there, Enter submits.
If neither surface is available, the card is not filed and the work waits: say so.

## Finding before filing, and whether it is yours to take

`list_issues` with `team` and `query: "<two or three words of the problem>"`, then with
the area label. Ask for `assigneeId`, `createdById`, `statusType` and `labels` in
`fields`, because an issue that exists is not an issue that is available. Compare ids
against the `id` you kept, never display names:

- `assigneeId` is you: yours, use it, move it, comment on it.
- `assigneeId` is somebody else, or `statusType` is `started` under their name: theirs.
  Leave every field alone, add a comment only if you have something useful, and pick
  other work, even when you are about to fix exactly that and even when the person
  talking to you asked for it by id: if it is theirs, they reassign it.
- no `assigneeId`: it belongs to whoever filed it (`createdById`).
- labelled `parallel`, with no `assigneeId` and not `started`: whoever is free may take
  it. The label opens an unclaimed card; it never reopens a claimed one.

Looking for something to work on: `list_issues` with `assignee: "me"`, which filters, and
`label: "parallel"`, which does not filter on ownership, so drop every row carrying
somebody else's `assigneeId` or a `statusType` of `started`. `assignee: null` is accepted
and silently ignored: narrow by `state` instead, raise `limit` past the default 50, and
filter the rows yourself.

A new issue is filed only when none exists and the work will outlive this run, and then
**before** the work starts, never after.

## The neighbours, before the first file changes

Finding the same card is one search. Finding the cards your change can collide with or
settle is another, every time, before the `save_issue` that files or moves your card.
Each read with `fields: ["id", "title", "status", "statusType", "labels", "project",
"assigneeId", "createdById"]`:

1. **The area, open states only.** `list_issues` with the area label and `state:
   "started"` (In Progress and In Review together), then `"unstarted"`, then `"backlog"`:
   one `state` per call.
2. **The surface, by name.** `list_issues` with `query:` one noun of what you are about to
   touch, one call per noun: the screen, the route, the table, the file's stem. `query`
   ranks, it does not filter: read the first handful and stop where the titles stop being
   about your surface.
3. **The project**, when the area is the wrong lens: `list_issues` with `project:` and the
   three open states, paging with `cursor` until `hasNextPage` is false. An audit of a
   whole project adds `includeArchived: true`.

What comes back is read, not counted:

- **`started` under somebody else, on the same surface.** Narrow your card to what theirs
  leaves alone, or wait, and write which on your card.
- **`Backlog` or `Todo` that your change would close.** Yours to take: it is your card,
  file nothing new. Not yours: `duplicateOf` from yours to theirs, or a comment on theirs.
- **A card your change would break, make easier, or has to land after.** `blocks`,
  `blockedBy`, or `relatedTo`, in the same call that files or moves your card.
- **Nothing.** Written anyway, as the **Adjacent** paragraph of the body, with the area
  calls that answered empty and the Done cards `query` ranked first for the surface: a
  scan that leaves no trace cannot be told from one that did not happen.

## Filing: one `save_issue` call

Every field at once; a second call to add the missing ones is the sign the first was wrong.

| Field | Value |
|---|---|
| `team` | `tracker.team` |
| `project` | the project id from `list_projects`; project names get renamed and a lookup by an old name fails |
| `milestone` | the milestone id from `list_milestones(project)`. Accepted and not echoed back. Never `projectMilestone` (the read's name), which is silently ignored on write |
| `title` | a verb and the work, one clause, under 80 characters (`linear-content`) |
| `description` | per `linear-content`, real newlines, never `\n` escapes |
| `addLabels` | one from `tracker.labels.type` and one from `tracker.labels.area`. Both are groups: a second label from one group is silently dropped. Never `labels` (it replaces the set) and never the id fields (`labelIds`, `addLabelIds`), which answer success and apply nothing |
| `priority` | 1 Urgent, 2 High, 3 Medium, 4 Low; a field, never a label |
| `estimate` | the team's points |
| `assignee` | never omitted: `"me"` when you will do the work, the person who asked when they will. An empty assignee reads as free to every other agent |
| `state` | `"In Progress"` when you start now, otherwise the default |
| `relatedTo`, `blocks`, `blockedBy`, `duplicateOf` | what the neighbour scan found, ids you have read. The arrays are append-only (`removeRelatedTo` and friends undo); `duplicateOf: null` clears |

Label names resolve case-insensitively and a retired label keeps its name: use the exact
names in `workflow.json`; `list_issue_labels` with `includeGroups: true` is the source
when in doubt.

**A write whose result you did not see is inspected before it is retried.** A timeout or
a dropped connection can still have created the card: `list_issues` with the title's words
first, then retry only if it is not there. A success is the response's `id`, read back
with `get_issue`, never the absence of an error.

## Moving it

- `In Progress` goes with `assignee: "me"` in the same call, and only on a card already
  yours. On a card you found rather than filed, the **Adjacent** paragraph and the
  relations go in that same call, the paragraph as `patch: [{ "op": "append", "text":
  "\n\n**Adjacent.** ..." }]`, because `description` replaces the whole body.
  `description` and `patch` cannot travel together.
- When the PR opens, comment its URL. Whether the tracker's automation then moves the state
  is in the tracker contract; where it does, never set `In Review` by hand.
- The closing comment (`linear-content`, `Evidence:` with run ids, sha, what you exercised)
  is written before or right after the merge, because `Done` will not wait for it.
- Won't-do is `Canceled` (one `l`), with the reason.

`save_issue` accepts `state`; `get_issue` echoes it as `status`. `save_issue` overwrites
`assignee` with no compare-and-set: reading the owner first is the only guard.

## Commenting

`save_comment` with `issueId` (not `issue`) and `body`, in the `linear-content` shapes,
when something changed the issue, when the work changed shape, when you are waiting on
something outside your hands, when the PR opened, its review, a red run, and the closing
evidence. Not "working on it". Replies take `parentId`.

## Project updates

`save_status_update` with `type: "project"`, the project id in a key called **`project`**,
a `health` (`onTrack`, `atRisk`, `offTrack`) and a body per `linear-content`. Omitting
`type` fails on `type: is required`; passing the id as `projectId` fails on `project is
required`. Three moments always get one: the project moves to `started`, a milestone's PR
merges or a release ships, and the last card is `Done` (with the project moved to
Completed in the same step, or the update saying what keeps it open). Between those, one
when something changed that the issue list would not show.

## Milestones

The name is a verb and the work it lands; the description is one sentence starting
`Closes when`, with the observable check. `save_milestone` wants `project` on every call,
an update included. `sortOrder` is not honoured as sent: order milestones in the UI.

## References in code and commits

A card id in a commit or a comment is a pointer to an issue you have read; never invent
one. The branch is the card's `gitBranchName`, rendered for whoever asks, not for the
assignee: it is never evidence that the card is yours.

## The plan cap

A Free workspace is capped at 250 issues that are not archived, Done ones included, and
past the cap `save_issue` can refuse to file. Before filing several cards at once, count
first; never delete an issue to make room (an archived one keeps its URL and comments).
