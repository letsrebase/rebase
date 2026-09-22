---
name: pr-creation
description: Use when starting a change that will end in a pull request, and when opening, describing or merging one in this monorepo. Enforces the card and its neighbours before the branch, the branch, the Conventional Commit title, the body in the repository's own template sections, the review and merge loop, and the Linear card kept current from the first file to the merge. Triggers on starting a task, "open a PR", "submit for review", "review and merge", or the Italian «lavora su questo ticket», «apri una PR», «fai review e mergia».
---

# Opening a pull request here

The contract lives in three files this skill points at and restates as little as it
can: the root `AGENTS.md` (Conventional Commits, English, no trailers, release only
through CI), `.github/PULL_REQUEST_TEMPLATE.md` (the body's sections) and
`docs/tracker.md` (what Linear needs at each step). This skill is the order of
operations and the shape of each piece, so two agents open the same kind of PR. Where
it and a document disagree, the document is right and the skill has a bug.

## Before the branch exists

1. **A Linear issue exists and is yours.** Find it with `list_issues` or file it with
   the `linear-ticket` skill. No issue, no branch: the issue is where the reasons live,
   and a PR written first loses them. A Linear tool that fails, or that answers a team
   other than **rebase**, is not a reason to go on without the card: file it from the
   web app as the `linear-ticket` skill § When `linear-rebase` is not in the session
   says, or stop and tell the person. Yours means what `docs/tracker.md` § Who owns a
   card says: assigned to the account this session writes as, or unassigned and filed by
   it, or labelled `parallel` and unclaimed. A card assigned to the other person is not
   made yours by opening a PR for it, nor by their having asked you for it, and a PR on
   their card is worse than no PR: it is their work done twice.
2. **Its neighbours have been read, and the card is `In Progress` with the result on
   it.** Before the first file changes, the open cards next to this one: same `area:*`
   in `started`, `unstarted` and `backlog`, and the same screen, route, table or file by
   name, as the `linear-ticket` skill § The neighbours says. One `In Progress` or
   `In Review` under the other person on the same files is a PR you do not open: narrow
   yours around it, or wait, and write which on the card. One in `Backlog` or `Todo`
   your change would settle or break is linked (`relatedTo`, `blocks`, `blockedBy`,
   `duplicateOf`) and named in the body. Nothing found is written as well, as the body's
   **Adjacent** paragraph. The one `save_issue` that moves the card to `In Progress`
   with `assignee: "me"` carries the links and that paragraph. No scan on the card, no
   branch.
3. **The branch is the milestone's, or Linear's.** If the issue's milestone has an open
   draft PR, you work on that branch: it is named for the milestone, carries no issue
   id, and every hand-driven card in the milestone commits onto it. Otherwise the branch
   is the issue's own `gitBranchName` (`mariorossi/reb-42-...`), which Linear renders
   for whoever reads the issue rather than for its assignee, so it tells you nothing
   about ownership and is only the name to use once step 1 holds. Either way, in a git
   worktree of its own, never on `main` and never in the shared checkout:

   ```bash
   git fetch origin
   git worktree add -b <branch> ../<repo>-<name> origin/main   # the issue's own branch
   cd ../<repo>-<name>
   uv sync --frozen && pnpm install --frozen-lockfile --prefer-offline
   ```

   Other sessions write to the same index; a worktree is what keeps your commit yours.
   A milestone branch is shared, and git refuses the same branch checked out in two
   worktrees (measured: `fatal: '<branch>' is already used by worktree at ...`, which
   is what a second concurrent card hits). So detach from the remote tip and push with
   an explicit refspec:

   ```bash
   git fetch origin
   git worktree add --detach ../<repo>-<name> origin/<milestone-branch>
   cd ../<repo>-<name>
   git push origin HEAD:<milestone-branch>   # a rejection means: git fetch, rebase onto
                                            # origin/<milestone-branch>, push again
   ```

   Never a force-push on the shared branch: it drops another card's commit. When the
   milestone's draft PR does not exist yet, the first card of the milestone opens it:
   branch `<owner>/milestone-<slug>` (no issue id in it), `gh pr create --draft`, body
   with the milestone name and a checklist of the cards it will land.
4. **Read the project's `AGENTS.md`** (`projects/<name>/AGENTS.md`) before its source.

## While the work happens

The card follows the work, per `docs/tracker.md` § The loop, While you work, and the PR
body is written from the card at the end, never the card from the PR. A comment, in the
`linear-content` shapes, when a finding changes the plan, when the scope moves (a
neighbour found late, a file that had to move too, a piece left for its own card), and
when you are waiting on something outside your hands. Not one per commit, and none that
says "working on it".

The loop is concurrent, not serial (DECISIONS.md, 2026-09-22): start `preflight` in the
background as soon as the code is committed, and capture the pair and the video while it
runs. The fixture the screenshots need is arranged while you build the change, not after.
The gates, the capture and the body draft overlap; none waits on another.

## Commits

As the root `AGENTS.md` Conventions say (Conventional Commits, English, first person,
no AI trailer). What that section does not say: the subject states what is true after
the commit (`feat(web): a draft invoice can be deleted from its page`), the body says
why and what was deliberately left alone, the last line is the issue (`REB-42.`), you
commit with a pathspec (`git add <files>`, never `-A`, other sessions share the index),
and a migration or a file move gets a commit of its own.

## Title

The PR title is the subject of the commit that is the work, under 72 characters:

```
type(scope): what is true now
```

`type` is one of `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `ci`, `chore`,
`style`, `revert`. `scope` is the project or the app: `web`, `api`, `core`, `mcp`
(the CRM), `hub`, `website`, `brand`, `ci`, `repo`. After the colon the first word is
lowercase and nothing is in Title Case; a verb in the present: what the reader gets, not
what you did to the files.

## Body

The template's sections, in the first person and in its order: the three that always
appear, then «Screenshots and video», which closes every body. Conditional sections go
after «How I verified it» and before «Anything a reviewer should look at twice». The
Linear line comes last. `gh pr create --body-file -` with a heredoc.

```markdown
## What this changes

<The first sentence names the problem: what was wrong, missing or impossible before.
Then what is true now, in words a product person understands. Bullets or a paragraph,
whichever reads better. Example strings, numbers and states beat descriptions of them.>

## How I verified it

<The commands and what they said: test counts, the gate names that went green, the
URL you opened and what you saw. Never "tests pass". If something could not be
verified, which part and why.>

## Anything a reviewer should look at twice

<The bit you are least sure about, a decision that could have gone the other way, a
path no test covers. Delete this section if there is genuinely nothing.>

## Screenshots and video

<For anything a person could see: one before-and-after pair per change, composed into
a single side-by-side image. For anything a person could do: one video of the feature
in use, recorded with docs/pr-screenshots/record.mjs. Both attached with `gh --attach`
(docs/pr-screenshots/README.md). If nothing visible changed, or a pair or the video
cannot be captured, say so and why. This section is never deleted.>
```

Conditional sections, each only when true (paths from the repository root; the CRM is
`projects/pigrocrm`, the hub `projects/hub`):

- **`## What did not change, on purpose`** when a reviewer could reasonably assume a
  neighbouring thing moved (an MCP tool that deliberately does not follow a new button,
  a list that does not get the new action). Say it, and say where the decision is
  recorded (the Linear issue, `docs/design/DECISIONS.md`).
- **`## Migrations`** when `projects/*/packages/core/migrations/versions/` changed: the
  revision, what it creates or alters in plain words, whether it is safe on a table that
  already exists in production (`IF NOT EXISTS` where the table was adopted), and what
  `compare_metadata` in the migration test says.
- **`## API changes`** when a FastAPI route, request or response shape changed: the
  `METHOD /path`, the fields that changed, the status codes. For the CRM, that
  `pnpm --filter web generate:api` was run with the API up on `localhost:8000`, so
  `projects/pigrocrm/apps/web/src/lib/api-types.ts` matches (the web reads the generated
  types, never hand-written ones). The hub's web has a hand-written client in
  `projects/hub/apps/web/src/lib/api.ts`: say what changed there.
- **`## MCP surface`** when a tool was added, removed or renamed. For the CRM
  (`projects/pigrocrm/apps/mcp`): which service method backs it, and what changed in
  `tests/test_mcp_surface_coverage.py` and `tests/test_mcp_invoice_ban.py`, the record
  of what an agent may and may not do. For the hub (`projects/hub/apps/mcp`): the
  change to `tests/test_tools.py`.

**Screenshots and video**, the section that closes every body, is not conditional: it is
there on every PR, and it carries a picture for anything a person could see. A label, a
pill, a disabled button, a new pane, a reordered menu, a wizard step, a public page. A
**before and after pair** per change, composed into one side-by-side image with a box
around what moved, taken on the same data at the same viewport, the before from a
worktree on `origin/main` and never by swapping files in place.

Then, for anything a person could **do**, a **video of the feature in use**: the clicks,
the typing and what the page does in return. The video is of the **after** only, your
worktree, on the same data and viewport as the after frame, with
`docs/pr-screenshots/record.mjs` (Playwright's own recorder plus ffmpeg, a `.mp4` the PR
body plays inline); there is no before video, the pairs already say what moved. One
video per PR, of the whole flow the PR adds or changes, ten to forty seconds; a second
one only when the PR carries two flows a reader would not follow in one take. A change
nobody interacts with (one label, a colour, a reordered column) gets its pairs and a
line saying why there is no video; a change a person drives (a button that does
something, a wizard step, a dialog, a state that follows an action) is not shown until
the video is there.

Never committed: `gh pr create` takes them with `--attach` (§ Before `gh pr create`),
which uploads every pair and the video in the one command that opens the PR, rewriting
each body reference to its `user-attachments` URL. The video's reference must be the
embed form, `![](./demo.mp4)`, alone in its paragraph: that is what `gh` (2.99.0 or
newer, `gh --version`) rewrites into the bare URL GitHub plays; a plain link degrades to
a link, which is what the old second `sed` edit patched and why it is gone. Verify the
body holds as many `user-attachments` as pairs plus videos. The procedure, per app and
port, is `docs/pr-screenshots/README.md`. When nothing visible changed, or a pair or the
video cannot be captured, the section says so and why. Deleting it reads as forgetting.

The last line of the body: `Linear: REB-N.`, or the milestone form (§ Before `gh pr
create`).

## Before `gh pr create`: the card, the review, the attachments

The PR is not opened until its body ends with its card line: `Linear: REB-N.` for a
single-card PR, or `Linear: milestone <name> (REB-a, REB-b, ...)` for a milestone's
draft PR, listing every card it will land. Either way the ids are ones you read from
the board in this session (`get_issue`, or the card's page), on cards that are yours.
Check the file you are about to send, not your memory of it:

```bash
tail -n 1 pr-body.md | grep -Eq '^Linear: (milestone .+ \(REB-[0-9]+(, REB-[0-9]+)*\)|REB-[0-9]+\.)$' || echo "no card, no PR"
```

The id also goes on the last line of the work commit (§ Commits). There is no
placeholder: «Linear: not filed yet», «TBD», «the id belongs here before this merges» are
each a PR opened without its card, which is what PR #111 did (REB-201 was filed after it,
by hand, REB-202 is this rule). When the card cannot be read or filed at all, the PR
waits and the person hears why; a PR without its card is not the smaller harm.

**The independent review happens before the PR is open for review**, not after: on a
single-card PR, before `gh pr create`; on a milestone's draft PR, before `gh pr ready`,
on the whole diff against `main`. Dispatch a fresh, read-only reviewer (an `Agent` of
type `general-purpose`, told the worktree path, the diff command, the files that give
it context, and to rank findings by severity with a concrete fix each). Do not review
your own diff and call it a review. Apply what you accept in the work commit (amend, or
a fixup squashed into it before the push), so CI runs once, on the sha that will merge:
a review commit pushed after the PR opens pays for a second full cycle. Record the
review on the PR as a comment right after it opens (or turns ready): each finding, what
you did with it, and what you left as is and why, plus one line on each card
(`**Review applied:** ...`, the `linear-content` shape). The card is where the other
agent reads that the PR is not only what its author wrote.

Open it in one command, with the pairs and the video attached (§ Screenshots and video):

```bash
gh pr create --body-file pr-body.md \
  --attach ./pair-1-invoice-detail.png --attach ./demo-1-issue-proforma.mp4
```

## After `gh pr create`

1. Comment the PR URL on the issue. The PR links itself within seconds, and since
   2026-09-16 the team's automation moves the state as well: `In Progress` while the PR
   is open, `Done` when it merges (REB-247, PR #161). The state is not yours from here;
   the comments are.
   On a milestone's draft PR the branch carries no issue id, so none of that fires:
   comment the PR URL on every card it lists, and move each card itself (see 3).
2. **Wait for CI in the background**: `gh pr checks <n> --watch` as a background job,
   never a foreground poll; there is nothing else to wait for, so do not sit on it. The
   `ci` job is the only status that matters; the others may skip by path filter. A red
   run gets a line on the card (`**CI red:** run ..., <job>, <cause>`) when you see it,
   and the sha of the fix on the same comment when you push it.
3. **Merge with a merge commit**, the repository's shape:
   `gh pr merge <n> --merge --delete-branch`. Then, right away, the
   `**Merged:**` comment on the card with the run ids, the commit sha, the test counts
   and what you opened and saw: on a single-card PR the automation sets `Done` at the
   merge without waiting for it, and a card that closes with nothing under it was
   closed by a robot. On a milestone PR nothing moves by itself: set each card it
   listed to `Done` in the same pass, each with its own evidence comment.
4. **Clean up**: `git worktree remove ../<repo>-orb<N>`, `git worktree prune`.
5. **Production is a separate step.** Preview deploys on the green trunk run;
   **production moves only on a tag** (`docs/design/DECISIONS.md`, 2026-09-09) and only
   when asked, and when it does, the card gets its `**In production:**` comment with the
   tag and what answered.

## What never goes in a PR

Secrets, tokens, passwords, personal data of a customer, screenshots of real customer
data, an issue id you did not read, a claim a test did not make, an AI trailer.
