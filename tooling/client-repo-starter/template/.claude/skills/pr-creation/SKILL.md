---
name: pr-creation
description: Use when starting a change that will end in a pull request, and when opening, describing or merging one in this repository. Enforces the card before the branch, the branch, the Conventional Commit title, the body in the repository's own template sections, and the Linear card kept current from the first file to the merge.
---

# Opening a pull request here

The contract lives in `AGENTS.md` (Conventional Commits, English, no trailers) and
`.github/PULL_REQUEST_TEMPLATE.md` (the body's sections). This skill is the order of
operations, so every PR here has the same shape. Where it and those files disagree,
the files are right and this skill has a bug.

## Before the branch exists

1. **A Linear card exists and is yours.** Find it with `list_issues` or file it with
   the `linear-ticket` skill. No card, no branch. A Linear tool that fails, or
   answers a team other than **{{LINEAR_TEAM}}**, is not a reason to go on without
   one: file it from the web app (`linear-ticket` § When no Linear tool is in the
   session), or stop and say so. Yours means what `AGENTS.md` § Tracker: Linear
   says — assigned to you, unassigned and filed by you, or labelled `parallel` and
   unclaimed.
2. **Its neighbours have been read, and the card is `In Progress` with the result on
   it.** Before the first file changes, per `linear-ticket` § The neighbours. One
   card `In Progress` or `In Review` under somebody else on the same files is a PR
   you do not open: narrow yours around it, or wait, and write which on the card.
3. **The branch is the issue's own `gitBranchName`**, read from Linear, never typed
   by hand.
{{WORKTREE_BLOCK}}

## While the work happens

The card follows the work (`AGENTS.md` § Tracker: Linear), a comment at every turn a
reader could not infer from the diff. The PR body is written from the card at the
end, never the card from the PR.

## Commits

As `AGENTS.md` § Commits says. What that section does not spell out: the subject
states what is true after the commit (`feat(api): a booking can be cancelled up to 2
hours before start`), the body says why and what was deliberately left alone, the
last line is the issue (`{{LINEAR_PREFIX}}-N.`), and you commit with a pathspec
(`git add <files>`, never `-A`). A migration or a file move gets a commit of its own.

## Title

The PR title is the subject of the commit that is the work, under 72 characters:

```
type(scope): what is true now
```

`type` is one of `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `ci`, `chore`,
`style`, `revert`. After the colon the first word is lowercase, nothing is in Title
Case, and the verb is present tense — what the reader gets, not what you did to the
files.

## Body

The template's sections, in the first person and in its order. `gh pr create
--body-file -` with a heredoc, or `--body-file pr-body.md`.

```markdown
## What this changes

<The first sentence names the problem: what was wrong, missing or impossible before.
Then what is true now, in words a non-engineer understands.>

## How I verified it

<The commands and what they said: test counts, the URL you opened and what you saw.
Never "tests pass". If something could not be verified, which part and why.>

## Anything a reviewer should look at twice

<The bit you are least sure about, a decision that could have gone the other way, a
path no test covers. Delete this section if there is genuinely nothing.>

## Screenshots and video

<For anything a person could see: a before-and-after pair. For anything a person
could do: a short video of the feature in use. If nothing visible changed, or a pair
or the video cannot be captured, say so and why. This section is never deleted.>
```

The last line of the body, after everything else: `Linear: {{LINEAR_PREFIX}}-N.`
Read the id from the board in this session, on a card that is yours. No placeholder —
"not filed yet", "TBD", "the id belongs here before this merges" are each a PR opened
without its card; when the card cannot be read or filed at all, the PR waits and the
person hears why.

## Before `gh pr create`: the card, the review, the attachments

Check the file you are about to send before you send it:

```bash
tail -n 1 pr-body.md | grep -Eq '^Linear: {{LINEAR_PREFIX}}-[0-9]+\.$' || echo "no card, no PR"
```

**Read your own diff against `main` before opening the PR for review**, not after —
the point where a reviewer, human or a fresh dispatched agent, catches something is
before CI runs on the sha that will merge rather than after, so a fix does not cost a
second full cycle. Apply what you accept in the work commit (amend it, or squash a
fixup in before the push).

```bash
gh pr create --body-file pr-body.md
```
{{GREPTILE_BLOCK}}

## After `gh pr create`

1. Comment the PR URL on the Linear card. If this team's GitHub integration and its
   pull-request automation are on, the PR opening and merging move the card's state
   for you; if not, move it by hand — `In Progress` while the PR is open, `Done` on
   the merge, with the evidence in a comment.
2. **Wait for CI in the background**, never a foreground poll. A red run gets a line
   on the card when you see it, and the sha of the fix on the same comment when you
   push it.
3. Merge only once CI is green{{GREPTILE_MERGE_CLAUSE}} and the card carries its
   closing evidence.
