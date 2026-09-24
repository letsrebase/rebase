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
(`**Review applied:** ...`, the `linear-content` shape). Greptile and CodeRabbit are
the second and third reviewers: both read the PR once it is open, or once a milestone's
draft PR turns ready, and CodeRabbit then checks Greptile's findings against the code
(§ After `gh pr create`, step 3). The card is where the other agent reads that the PR
is not only what its author wrote.

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
   comment the PR URL on every card it lists, and move each card itself (see 4).
2. **Wait for CI in the background**: `gh pr checks <n> --watch` as a background job,
   never a foreground poll; there is nothing else to wait for, so do not sit on it. The
   `ci` job is the only status that matters; the others may skip by path filter. A red
   run gets a line on the card (`**CI red:** run ..., <job>, <cause>`) when you see it,
   and the sha of the fix on the same comment when you push it.
3. **Two reviewers read every push, and CodeRabbit then reads Greptile.** Greptile and
   CodeRabbit both start by themselves when the PR opens and again on every push
   (CodeRabbit's settings are `.coderabbit.yaml`). Each sha goes through the same
   round: Greptile's review (a), CodeRabbit's own review (b), then CodeRabbit against
   Greptile (c), then every finding fixed or answered (d). Run (a) and (b) as
   background jobs next to the CI watch; (c) waits for both. Each block below starts
   from `git rev-parse HEAD`, because a background job is a fresh shell. On a
   milestone's draft PR the loop runs after `gh pr ready`, on the sha that will merge:
   neither reviewer reads a draft (`drafts: false` in `.coderabbit.yaml`).

   A Dependabot PR gets no CodeRabbit review by itself: `.coderabbit.yaml` skips the
   bot, because Dependabot opens its PRs a dozen at a time (#369 to #380) and CodeRabbit
   allows ten reviews an hour. Whoever picks one up to merge it comments
   `@coderabbitai review` on that PR, one PR at a time, and runs the loop from (b).

   **a. Greptile.** It reviews the PRs here (a trial since PR #262, ending 2026-10-06
   unless the plan changes; it reviewed all six PRs from #262 to #267, #265 clean):
   inline findings, each with a `P0`, `P1` or `P2` badge, and a summary headed
   `Confidence Score: N/5`. Where that summary lands is the «Update pull request
   description» switch in app.greptile.com, PR Summaries: on, it is written into the
   PR's own description between `<!-- greptile_comment -->` markers, which is where
   #266 and #267 read `4/5` and merged anyway; off, since 2026-09-22, it is a
   conversation comment by the bot. The read below covers both. A run shows on the
   commit as the `Greptile Review` check run: `success` with a count (`1 files
   reviewed, 0 comments added.`) at 5/5, `failure` under 5/5 (`The review scored 4/5,
   below the 5/5 this repository requires`, #367), which does not block the merge. When
   it raised findings, it also leaves a review by the bot with an empty body that owns
   them. Greptile's replies in its own threads are reviews as well, with no finding of
   their own, so the run's review is the one that owns a top-level comment on that sha.
   The completed check run is the signal; the review can land a few seconds before or
   after it (#268):

   ```bash
   sha=$(git rev-parse HEAD)
   for i in $(seq 20); do   # ten minutes, then the @greptileai nudge below
     run=$(gh api "repos/letsrebase/rebase/commits/$sha/check-runs" \
         --jq '.check_runs[] | select(.name == "Greptile Review" and .status == "completed") | .conclusion + ": " + .output.summary')
     [ -n "$run" ] && break; sleep 30
   done
   echo "$run"
   for i in $(seq 12); do   # the run's own review, if it raised anything
     rid=$(gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
         --jq ".[] | select(.user.login == \"greptile-apps[bot]\" and .original_commit_id == \"$sha\" and .in_reply_to_id == null) | .pull_request_review_id" | tail -n 1)
     { [ -n "$rid" ] || echo "$run" | grep -q ' 0 comments added'; } && break; sleep 10
   done
   [ -n "$rid" ] && gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
       --jq ".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \"\(.id) \(.path):\(.line // .original_line) \(.body)\""
   { gh pr view <n> --json body -q .body
     gh api repos/letsrebase/rebase/issues/<n>/comments \
         --jq '.[] | select(.user.login == "greptile-apps[bot]") | .body'; } \
       | grep -oE 'Confidence Score: [0-9]/5' | tail -n 1
   ```

   On #367's first commit `rid` is the review that owns the three findings; on its
   second it is empty: that run raised nothing, and the only Greptile review on that sha
   is a reply in a thread. The last command reads the score from the PR description and
   from the bot's comments, whichever the switch fills; on #267 it prints `Confidence
   Score: 4/5`. Nothing printed after a completed run means the summary is not there
   yet, not a score of zero: read again before you go on. A score under 5/5 with no new
   finding means an earlier thread is still open (§ d). A review with a body and no
   inline comment is Greptile not reviewing (#259 and #260, `Your trial has ended`; its
   reviews here have an empty body): say so on the card and tell the person before you
   merge. Ten minutes with no completed run on the head sha: `gh pr comment <n> --body
   '@greptileai'` once, which re-triggers it, and run the wait again; still nothing, say
   so on the card and go on without the comments command. A later push may get no run on
   its own: three of #268's five commits got none in ten minutes and one within thirty
   seconds of the comment, the other two were reviewed unprompted (2026-09-22); #367's
   third commit had none after eleven minutes.

   **b. CodeRabbit, its own review.** It shows on the commit as a `CodeRabbit` commit
   status: `Review in progress`, then `Review completed`, or `Review skipped: ...` with
   the reason (`draft pull request` on a draft, `author ignored by configuration` on
   Dependabot's). When it found something, it also leaves a review by
   `coderabbitai[bot]` on that commit whose body opens with `Actionable comments posted:
   N`; that body can carry `Outside diff range` and `Nitpick` findings that are not
   inline comments, so read it whole. Either way it keeps one summary comment on the PR,
   edited in place, with a `Merge Risk` line and the pre-merge checks table; a check
   listed under `Failed checks` (a `Warning` or an `Error` row) is a finding too. A clean
   run leaves only the status and the summary (#367's first commit). Its replies in
   threads are reviews as well, without the `Actionable comments` line, which is why the
   read below filters on it:

   ```bash
   sha=$(git rev-parse HEAD)
   for i in $(seq 20); do   # ten minutes, then @coderabbitai review
     st=$(gh api "repos/letsrebase/rebase/commits/$sha/statuses" \
         --jq '[.[] | select(.context == "CodeRabbit")][0].description // ""')
     case "$st" in "Review completed"|"Review skipped"*) break;; esac; sleep 30
   done
   echo "$st"
   crid=$(gh api repos/letsrebase/rebase/pulls/<n>/reviews \
       --jq ".[] | select(.user.login == \"coderabbitai[bot]\" and .commit_id == \"$sha\" and (.body | test(\"Actionable comments posted\"))) | .id" | tail -n 1)
   [ -n "$crid" ] && gh api repos/letsrebase/rebase/pulls/<n>/reviews/$crid --jq .body
   [ -n "$crid" ] && gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
       --jq ".[] | select(.pull_request_review_id == $crid and .in_reply_to_id == null) | \"\(.id) \(.path):\(.line // .original_line) \(.body)\""
   gh api repos/letsrebase/rebase/issues/<n>/comments \
       --jq '.[] | select(.user.login == "coderabbitai[bot]" and (.body | test("summarize by coderabbit.ai"))) | .body' \
     | sed -n '/Merge Risk/p;/pre_merge_checks_walkthrough_start/,/Passed checks/p'
   ```

   The last command prints the `Merge Risk` line, the checks' header and, when a check
   failed, its table; on #363 that is the `Docstring Coverage` warning. `Review
   skipped` on a Dependabot PR is the `@coderabbitai review` above; on a draft, the loop
   waits for `gh pr ready`. Ten minutes with no status on the head sha, or a summary
   saying the reviews are paused (it pauses itself after five reviewed commits on one
   PR, to spare the hourly allowance): `gh pr comment <n> --body '@coderabbitai review'`,
   which reviews the head once and leaves the pause in place, and wait again. When it
   answers that it is rate limited instead of reviewing, wait for the window it names
   and ask again, and say so on the card.

   **c. CodeRabbit against Greptile.** It comes after CodeRabbit's own review on
   purpose: its own findings are not shaped by Greptile's, and then it checks Greptile's
   against the code. A pass is due when (a) and (b) are both done for the sha and
   Greptile raised a finding on it, or you answered a Greptile finding since the last
   pass. Until the verdict is in, reply to none of Greptile's threads: CodeRabbit
   answers replies in any thread by itself (`auto_reply`), and on #367 it agreed with an
   answer on a Greptile thread and resolved it before any pass had been asked for. One PR
   comment hands it Greptile's side and asks for a verdict on each item:

   ```bash
   sha=$(git rev-parse HEAD); short=$(git rev-parse --short=9 HEAD)
   rid=$(gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
       --jq ".[] | select(.user.login == \"greptile-apps[bot]\" and .original_commit_id == \"$sha\" and .in_reply_to_id == null) | .pull_request_review_id" | tail -n 1)
   { echo "@coderabbitai Adversarial pass on Greptile's review of $short. You have reviewed this commit on your own; now act as Greptile's adversary. For each item below, check the claim against the code at $short yourself and answer **confirmed**, **refuted** or **partly**, with the evidence: file and line, or the script you ran and what it printed. Do not take Greptile's reasoning or mine on trust, and do not agree to be agreeable: a finding that does not hold is refuted, and an answer of mine that does not hold is wrong. Then list any defect in this diff that neither review raised. Start your reply with the line \`Adversarial verdict on $short\`, then one line per item, then the misses."
     echo; echo "Greptile's findings on $short:"
     [ -n "$rid" ] && gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
         --jq ".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \"- \(.html_url) \(.path):\(.line // .original_line) \(.body | gsub(\"<[^>]*>\"; \"\") | split(\"\n\n\") | map(gsub(\"^\\\\s+|\\\\s+$\"; \"\")) | map(select(length > 0)) | .[0:2] | join(\" \"))\""
   } > adversary.md
   ```

   Each line carries the finding's link, its `path:line`, its title and the claim
   itself (on #367's first commit, three lines). Then add by hand, under `Greptile
   findings I answered since the last pass:`, one line per answer: the thread's link and
   the answer in a sentence. When Greptile raised nothing new and there is no new answer,
   there is no pass. Post it and wait for the reply, a new comment by the bot that
   opens with the line it was asked for:

   ```bash
   since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
   gh pr comment <n> --body-file adversary.md
   for i in $(seq 40); do   # twenty minutes: #367's one chat reply took nine and a half
     verdict=$(gh api --paginate "repos/letsrebase/rebase/issues/<n>/comments?per_page=100" \
         --jq ".[] | select(.user.login == \"coderabbitai[bot]\" and .created_at >= \"$since\" and (.body | contains(\"Adversarial verdict on $short\"))) | .body")
     [ -n "$verdict" ] && break; sleep 30
   done
   printf '%s\n' "$verdict"
   ```

   Twenty minutes with no verdict: post the same comment once more and wait again;
   still nothing, say so on the card and tell the person before you merge, as with a
   Greptile that does not review.

   **d. What each finding and verdict does.** Every finding, from either reviewer, is
   either **fixed**, in a commit that names it, or **answered** with the reason the code
   stays as it is. An inline finding is answered on its thread (`gh api
   repos/letsrebase/rebase/pulls/<n>/comments/<id>/replies -f body=...`). One with no
   thread (an `Outside diff range` or `Nitpick` item, a failed pre-merge check, a miss
   the adversarial pass named) is answered in one PR comment addressed to
   `@coderabbitai`, one line per item. The adversarial verdict decides which way each
   Greptile finding goes:

   - **Confirmed**: fix it. Read the code first all the same: a confirmation is a
     second opinion, not proof.
   - **Refuted**, and the evidence holds when you check it yourself: answer on
     Greptile's thread with that evidence and a link to CodeRabbit's verdict.
   - **Partly**: fix the part that holds, answer the rest.
   - **An answer of yours called wrong**: it is a finding again; fix it, or answer with
     what the verdict missed.
   - **A miss** CodeRabbit names: a finding like any other.
   - **A refutation you do not accept**, or one Greptile answers by raising the finding
     again: the two reviewers disagree, and that is the lead's call, a
     `**Decision for the lead**` line on the card. The PR does not merge over it.

   An answered Greptile finding still counts against the score until Greptile reads the
   thread as closed: resolve it (`gh api graphql -f query='mutation {
   resolveReviewThread(input:{threadId:"<id>"}) { thread { isResolved } } }'`, the id
   from the PR's `reviewThreads`) and comment `@greptileai`; on #269 that took the score
   from 4/5 to 5/5 in ninety seconds with no push, and on #367 Greptile withdrew a
   finding in its own thread after reading the answer. A Greptile thread CodeRabbit
   resolved is neither a verdict nor Greptile closing it: the `@greptileai` still goes
   out. CodeRabbit resolves its own thread when it accepts an answer (`Review thread
   resolved`, #367); one it keeps open after your answer is a disagreement for the
   card, like a finding raised again after an answer, which is not closed by repeating
   the answer. After a fix, push, and the round starts again on the new sha. Before the
   merge, list the threads still open (the author is `greptile-apps` or `coderabbitai`
   here, without `[bot]`):

   ```bash
   gh api graphql -F o=letsrebase -F r=rebase -F n=<n> -f query='query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ pullRequest(number:$n){ reviewThreads(first:100){ nodes{ isResolved comments(first:1){ nodes{ author{login} url } } } } } } }' \
     --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved | not) | "\(.comments.nodes[0].author.login) \(.comments.nodes[0].url)"'
   ```

   **The loop ends** on the sha that will merge when: Greptile's run on it raised nothing
   new and the score reads 5/5; CodeRabbit's status on it reads `Review completed`, its
   review of it raised nothing new and none of its pre-merge checks failed without an
   answer; the command above prints nothing; every verdict of the last adversarial pass
   was acted on; and no `**Decision for the lead**` is open. What the loop did goes on the
   card in the same `**Review applied:**` comment as the independent review (the
   `linear-content` shape): each reviewer's findings, which changed the code (sha),
   which were answered and why, the adversarial verdicts (confirmed, refuted, partly,
   misses), Greptile's final score and CodeRabbit's `Merge Risk` line.
4. **Merge with a merge commit**, the repository's shape:
   `gh pr merge <n> --merge --delete-branch`. Then, right away, the
   `**Merged:**` comment on the card with the run ids, the commit sha, the test counts
   and what you opened and saw: on a single-card PR the automation sets `Done` at the
   merge without waiting for it, and a card that closes with nothing under it was
   closed by a robot. On a milestone PR nothing moves by itself: set each card it
   listed to `Done` in the same pass, each with its own evidence comment.
5. **Clean up**: `git worktree remove ../<repo>-<name>`, `git worktree prune`.
6. **Production is a separate step.** Preview deploys on the green trunk run;
   **production moves only on a tag** (`docs/design/DECISIONS.md`, 2026-09-09) and only
   when asked, and when it does, the card gets its `**In production:**` comment with the
   tag and what answered.

## What never goes in a PR

Secrets, tokens, passwords, personal data of a customer, screenshots of real customer
data, an issue id you did not read, a claim a test did not make, an AI trailer, a
Greptile or CodeRabbit finding neither fixed nor answered.
