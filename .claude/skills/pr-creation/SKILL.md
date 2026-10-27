---
name: pr-creation
description: Use when starting a change that will end in a pull request, and when opening, describing, reviewing or merging one in this repository. Enforces the card and its neighbours before the branch, the worktree, the Conventional Commit title, the body in the template's sections with the card as its last line, the independent review before the PR opens, the review and merge loop, and the tracker card kept current from the first file to the merge. Triggers on starting a task, "open a PR", "submit for review", "review and merge", or the Italian «lavora su questo ticket», «apri una PR», «fai review e mergia».
---

# Opening a pull request here

This skill is part of the workflow kit (`.github/kit/README.md`) and reads like this in
every repository that uses it. What differs between repositories is data, not prose:

- `.github/workflow.json` holds the tracker (workspace, team, card prefix, labels), the
  commit types and scopes, the PR title limit, the body's sections in order, the merge
  method and the review bots. `python3 .github/kit/bin/kit.py template` prints the body
  those sections make.
- `AGENTS.md` holds the conventions (language, commit rules, what CI covers), and the
  tracker contract is the file `tracker.contract` names in `workflow.json`
  (`docs/tracker.md` when it exists).
- The directory `extras` names in `workflow.json` (usually `docs/agents/`) may hold a
  `pr-creation.md` with this repository's own steps: the review bots' loop, the
  screenshot tooling, milestone branches, release tags. **Read it before you start when
  it exists**; where it is more specific than this skill, it wins.

Where this skill and those files disagree, the files are right and this skill has a bug.

## Before the branch exists

1. **A card exists and is yours.** Find it or file it with the `linear-ticket` skill.
   No card, no branch: the card is where the reasons live, and a PR written first loses
   them. A tracker tool that fails, or that answers a workspace other than
   `tracker.workspace`, is not a reason to go on without the card: file it from the web
   app as `linear-ticket` § No Linear tool in the session says, or stop and say so.
   Yours means assigned to the account this session writes as, or unassigned and filed by
   it, or labelled `parallel` and unclaimed. A card assigned to somebody else is not made
   yours by opening a PR for it, nor by their having asked you for it.
2. **Its neighbours have been read, and the card is `In Progress` with the result on
   it**, per `linear-ticket` § The neighbours. A card `In Progress` or `In Review` under
   somebody else on the same files is a PR you do not open: narrow yours around it, or
   wait, and write which on the card. A card in `Backlog` or `Todo` that your change
   would settle or break is linked and named in the PR body; nothing found is written
   too, as the card's **Adjacent** paragraph. No scan on the card, no branch. When this
   card is the first to start its project, the project moves to `started` and its
   opening update goes out in the same pass.
3. **The branch is the card's own `gitBranchName`**, read from the tracker, never typed
   by hand, unless the extras define a shared branch for the card's milestone.
4. **One worktree per branch**, never `main` and never a checkout another session uses:

   ```bash
   git fetch origin
   git worktree add -b <branch> ../<repo>-<name> origin/main
   cd ../<repo>-<name>
   ```

   Then install the dependencies as `AGENTS.md` says. Other sessions write to the same
   index; a worktree is what keeps your commit yours.
5. **Read the nearest `AGENTS.md`** of the code you are about to touch.

## While the work happens

The card follows the work, the PR body is written from the card at the end, never the
card from the PR. A comment, in the `linear-content` shapes, when a finding changes the
plan, when the scope moves (a neighbour found late, a file that had to move too, a piece
left for its own card), and when you are waiting on something outside your hands. Not
one per commit, and none that says "working on it".

Run the local checks as soon as the code is committed, in the background, and prepare
the captures and the body while they run. When the repository has
`.github/preflight.json`, that is `python3 .github/kit/bin/preflight` (`--list` first
shows what your diff selects).

## Commits

As `AGENTS.md` says. What it may not spell out: the subject states what is true after
the commit (`feat(api): a booking can be cancelled up to 2 hours before start`), the body
says why and what was deliberately left alone, the last line is the card
(`<prefix>-42.`), you commit with a pathspec (`git add <files>`, never `-A`: other
sessions share the index), and a migration or a file move gets a commit of its own. Never
an AI co-author trailer.

## Title

The PR title is the subject of the commit that is the work:

```
type(scope): what is true now
```

`type` comes from `commits.types` and every scope from `commits.scopes` (several are
joined by commas: `feat(core,api,web): ...`). The title stays under `pr.title_max`
characters. After the colon the first word is lowercase, nothing is in Title Case, the
verb is in the present, and there is no period and no em or en dash: what the reader
gets, not what you did to the files.

## Body

The sections of `pr.sections`, in that order, in the first person. The ones marked
`required` are always there; one with a `when` appears only when that is true, between
the leading required sections and the closing one. `gh pr create --body-file
pr-body.md`. What each standard section holds:

- **What this changes.** The first sentence names the problem: what was wrong, missing
  or impossible before. Then what is true now, in words a product person understands.
  Example strings, numbers and states beat descriptions of them.
- **How I verified it.** The commands and what they said: test counts, the checks that
  went green, the URL you opened and what you saw. Never "tests pass". If something
  could not be verified, which part and why.
- **What did not change, on purpose.** When a reviewer could reasonably assume a
  neighbouring thing moved, say it and where the decision is recorded.
- **Anything a reviewer should look at twice.** The bit you are least sure about, a
  decision that could have gone the other way, a path no test covers. Delete it when
  there is genuinely nothing.
- **Screenshots and video.** For anything a person could see, one before-and-after pair
  per change, the before from a worktree on `origin/main`, same data, same viewport. For
  anything a person could do, one video of the after in use. Reference each file by its
  local path (`![](./pair-1.png)`; a video as `![](./demo.mp4)` alone in its
  paragraph) and attach them in the command that opens the PR, which rewrites each path
  to its upload. If nothing visible changed, or a capture is impossible, say so and why.
  The section is never deleted.

The last line, when `workflow.json` has a tracker: `Linear: <prefix>-42.`, or for a PR
that lands several cards of one milestone `Linear: milestone <name> (<prefix>-1,
<prefix>-2)`, with ids you read from the board in this session on cards that are yours.
No placeholder: "TBD" or "not filed yet" is a PR opened without its card; when the card
cannot be read or filed at all, the PR waits and the person hears why.

## Before `gh pr create`

1. **Lint what you are about to send**, the same check `ci` runs:

   ```bash
   python3 .github/kit/bin/pr_lint.py --title "<title>" --body-file pr-body.md
   ```

2. **The independent review happens before the PR is open for review.** Dispatch a
   fresh, read-only reviewer (a subagent told the worktree path, the diff command, the
   files that give it context, and to rank findings by severity with a concrete fix
   each). Do not review your own diff and call it a review. Apply what you accept in the
   work commit (amend, or a fixup squashed into it before the push), so CI runs once, on
   the sha that will merge. Record the review on the PR as a comment right after it
   opens, and one `**Review applied:**` line on the card.
3. **Open it in one command, with the captures attached:**

   ```bash
   gh pr create --title "<title>" --body-file pr-body.md --attach ./pair-1.png --attach ./demo-1.mp4
   ```

   `--attach` needs gh 2.99 or newer. On GitHub, every `./` path in the body must have
   become a `user-attachments` URL; `ci` fails on one that did not.

## After `gh pr create`

1. **Comment the PR URL on the card.** When the tracker's pull-request automation is on
   (the tracker contract says), the PR opening and its merge move the card's state; when
   it is not, move it by hand: `In Progress` while the PR is open, `Done` at the merge.
2. **Wait for CI in the background**, never a foreground poll: `gh pr checks <n>
   --watch`. The only status that matters is `github.required_check`; the others may skip
   by path. A red run gets a line on the card (`**CI red:** run ..., <job>, <cause>`) when
   you see it, and the sha of the fix on the same comment when you push it. A red
   `pr_lint` step is fixed with `gh pr edit` and a re-run of the failed jobs (`gh run
   rerun <run id> --failed`), no push needed.
3. **Every reviewer finding is fixed or answered** before the merge: fixed in a commit
   that names it, or answered on its thread with the reason the code stays. The bots in
   `github.review_bots` and how to read them are in the extras; a finding neither fixed
   nor answered blocks the merge, and two reviewers who disagree are the project lead's
   call, a `**Decision for the lead**` line on the card.
4. **Merge** with the method in `github.merge` (`gh pr merge <n> --merge`, `--squash` or
   `--rebase`, plus `--delete-branch`) once the required check is green, every finding is
   settled and the card carries its closing evidence: a `**Merged:**` comment with the run
   ids, the sha, the test counts and what you opened and saw, written before or right
   after the merge, because automation will not wait for it.
5. **The project update is part of finishing.** A milestone's PR merging, a release
   shipping, and the merge that lands the last open card of a project each owe the
   project an update (`linear-ticket` § Project updates). The last one says what was
   verified and on which surface and moves the project to Completed in the same step, or
   says what keeps it open.
6. **Clean up**: `git worktree remove ../<repo>-<name>`, then `git worktree prune`.
7. **Production is a separate step**, taken only when asked and only the way `AGENTS.md`
   says.

## What never goes in a PR

Secrets, tokens, passwords, personal data of a customer, screenshots of real customer
data, a card id you did not read, a claim a test did not make, an AI trailer, a review
finding neither fixed nor answered.
