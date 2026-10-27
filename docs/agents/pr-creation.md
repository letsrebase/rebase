# Opening a pull request in this repository: what the kit's skill leaves to us

The workflow kit's `pr-creation` skill (`.agents/skills/pr-creation/SKILL.md`) is the
procedure; this file is the part only this repository does. Read the skill first, then
this. Where the two disagree, this file is the more specific one and wins.

## Milestone branches

**The branch is the milestone's, or Linear's.** If the issue's milestone has an open
draft PR, you work on that branch: it is named for the milestone, carries no issue
id, and every hand-driven card in the milestone commits onto it. Otherwise the branch
is the issue's own `gitBranchName` (`mariorossi/reb-42-...`), which Linear renders
for whoever reads the issue rather than for its assignee, so it tells you nothing
about ownership and is only the name to use once the card is yours. Either way, in a git
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

The body of a milestone's draft PR carries the template's sections from its first push,
filled in as the cards land, and ends with `Linear: milestone <name> (REB-a, REB-b, ...)`
listing every card it will land: `ci` lints the body of a draft too. The card ids go on
each work commit's last line as usual.

On a milestone's draft PR the branch carries no issue id, so the Linear automation does
not fire: comment the PR URL on every card it lists, and move each card yourself. The
independent review runs on the whole diff against `main` before `gh pr ready`; the bots'
loop below runs after it, on the sha that will merge, since neither bot reads a draft by
itself (`drafts: false` in `.coderabbit.yaml`, `triggerOnDrafts: false` in
`.greptile/config.json`).

## Working concurrently

The loop is concurrent, not serial (DECISIONS.md, 2026-09-22): start `python3 .github/kit/bin/preflight` in the
background as soon as the code is committed, and capture the pair and the video while it
runs. The fixture the screenshots need is arranged while you build the change, not after.
The gates, the capture and the body draft overlap; none waits on another.

## Conditional sections, here

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

## Screenshots and video, here

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

Never committed: `gh pr create` takes them with `--attach` (the kit's skill, § Before `gh pr create`),
which uploads every pair and the video in the one command that opens the PR, rewriting
each body reference to its `user-attachments` URL. The video's reference must be the
embed form, `![](./demo.mp4)`, alone in its paragraph: that is what `gh` (2.99.0 or
newer, `gh --version`) rewrites into the bare URL GitHub plays; a plain link degrades to
a link, which is what the old second `sed` edit patched and why it is gone. Verify the
body holds as many `user-attachments` as pairs plus videos. The procedure, per app and
port, is `docs/pr-screenshots/README.md`. When nothing visible changed, or a pair or the
video cannot be captured, the section says so and why. Deleting it reads as forgetting.

## The review loop: Greptile, then CodeRabbit, then CodeRabbit against Greptile

**Two reviewers read every push, Greptile from the second one only when asked, and
CodeRabbit then reads Greptile.** Both start by themselves when the PR opens or turns
ready. On a later push CodeRabbit starts again by itself and Greptile does not:
`autoReview` in `.greptile/config.json` is `["open"]`, the dashboard's «On PR
opened», so every push after the first to a PR that is open for review is followed at
once by `gh pr comment <n> --body '@greptileai'` (#529: no run on `0efee8e70` or
`3a397226d` until the comment, then one within fifteen seconds of it). CodeRabbit's
settings are `.coderabbit.yaml`, Greptile's `.greptile/config.json`, which holds
every value app.greptile.com stores that Greptile's reference documents a field for,
as read there on 2026-10-05. The rest stays in the dashboard: the check's threshold,
the comments outside the diff, image badges, the comment header, the «Fix with your
Agent» defaults, TREX, who may create rules, the Linear integration. Each sha goes
through the same round: Greptile's review (a), CodeRabbit's own review (b), then
CodeRabbit against Greptile (c), then every finding fixed or answered (d). Run (a)
and (b) as background jobs next to the CI watch; (c) waits for both. Each block below
reads the PR's own head (`gh pr view <n> --json headRefOid`), because a background job is
a fresh shell and a detached worktree's `HEAD` need not be what the PR shows; the merge
is of that reviewed head (`gh pr merge <n> --merge --match-head-commit <sha>`). On a
milestone's draft PR the loop runs after `gh pr ready`, on the sha that will merge:
neither reviewer reads a draft by itself (`drafts: false` in `.coderabbit.yaml`,
`triggerOnDrafts: false` in `.greptile/config.json`), and an `@greptileai` on a draft
would review a sha that does not merge.

A Dependabot PR is outside this loop. Neither reviewer reads one by itself, by
configuration (`ignore_usernames` in `.coderabbit.yaml`, `excludeAuthors` in
`.greptile/config.json`, both naming `dependabot[bot]`), and nobody comments
`@greptileai` or `@coderabbitai review` on one, though both filters let a request
through: a bump changes pins and locks, and
CodeRabbit refuses the request on a bot PR anyway («Review skipped. This PR was
authored by a bot without an assigned CodeRabbit review seat», #514, 2026-10-05).
A bump PR merges on green `ci` and a merge commit, after the `flake.nix` pnpm hash
commit when `pnpm-lock.yaml` changed (root `AGENTS.md` § Conventions,
`docs/design/DECISIONS.md`, 2026-10-05, REB-639).

**a. Greptile.** It reviews the PRs here (a trial from PR #262; on 2026-10-05 its
Billing page reads «Free plan», this repository marked «Open source», none of the
period's 50 credits used; it reviewed all six PRs from #262 to #267, #265 clean):
inline findings, each with a `P0`, `P1` or `P2` badge, and a summary headed
`Confidence Score: N/5`. Where that summary lands is `shouldUpdateDescription` in
`.greptile/config.json` (before the file existed, 2026-10-05, it was the «Update
pull request description» switch in app.greptile.com, PR Summaries): `true` writes
it into the PR's own description between `<!-- greptile_comment -->` markers,
which is where #266 and #267 read `4/5` and merged anyway; `false`, the value since
the switch went off on 2026-09-22, makes it a conversation comment by the bot,
which says why the score is what it is. The
score of a given sha is read from that sha's own `Greptile Review` check run, never
from the summary, which may still be the last round's: `success` is 5/5 (the
check's threshold, «Required confidence to pass» in app.greptile.com, Status
Checks, read as 5 on 2026-10-05, which has no documented field for the file),
`failure` is under it, with `Confidence N/5` in its title (`Confidence
2/5`, #388's first commit). GitHub lets a
PR merge over that `failure`, since only `ci` is required, but this loop does not: it
ends on `success`. When the run raised findings, it also leaves a review by the bot
with an empty body that owns them. Greptile's replies in its own threads are reviews
as well, with no finding of their own, so the run's review is the one that owns a
top-level comment on that sha. The completed check run is the signal; the review can
land a few seconds before or after it (#268):

```bash
sha=$(gh pr view <n> --json headRefOid -q .headRefOid)
for i in $(seq 20); do   # ten minutes, then the @greptileai nudge below
  run=$(gh api "repos/letsrebase/rebase/commits/$sha/check-runs" \
      --jq '.check_runs[] | select(.name == "Greptile Review" and .status == "completed") | .conclusion + " | " + .output.title + " | " + .output.summary')
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
```

On #367's first commit `rid` is the review that owns the three findings; on its
second it is empty: that run raised nothing (`success`, `0 comments added`), and the
only Greptile review on that sha is a reply in a thread. A `failure` run with an
empty `rid` is either an earlier thread still open, which keeps the score down (§ d;
the open-threads command there shows it), or a review that has not landed: with no
open Greptile thread, read again after a minute, then comment `@greptileai`. Nothing
on the check run until it completes is not a score. A review with a body and no
inline comment is Greptile not reviewing (#259 and #260, `Your trial has ended`; its
reviews here have an empty body): say so on the card and tell the person before you
merge. Ten minutes with no completed run on the head sha, counted on a later push
from the `@greptileai` that followed it: `gh pr comment <n> --body '@greptileai'`
once, which re-triggers it, and run the wait again; still nothing, say so on the
card and go on without the comments command. A later push with no comment gets no
run, which is `autoReview: ["open"]` and not a fault: on #268 only the opening
commit was reviewed unprompted, each of the four later runs started within thirty
seconds of an `@greptileai` and none before it (2026-09-22); #367's third commit
had none after eleven minutes.

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
sha=$(gh pr view <n> --json headRefOid -q .headRefOid)
for i in $(seq 20); do   # ten minutes, then @coderabbitai review
  st=$(gh api "repos/letsrebase/rebase/commits/$sha/statuses" \
      --jq '[.[] | select(.context == "CodeRabbit")][0].description // ""')
  [ "$st" = "Review completed" ] && break; sleep 30
done
echo "$st"
for i in $(seq 12); do   # the summary of this sha, not the last round's
  summary=$(gh api --paginate "repos/letsrebase/rebase/issues/<n>/comments?per_page=100" \
      --jq '.[] | select(.user.login == "coderabbitai[bot]" and (.body | test("summarize by coderabbit.ai"))) | .body')
  echo "$summary" | grep -q "\"coveredCommitId\":\"$sha\"" && break; sleep 10
done
crid=$(gh api --paginate "repos/letsrebase/rebase/pulls/<n>/reviews?per_page=100" \
    --jq ".[] | select(.user.login == \"coderabbitai[bot]\" and .commit_id == \"$sha\" and (.body | test(\"Actionable comments posted\"))) | .id" | tail -n 1)
[ -n "$crid" ] && gh api repos/letsrebase/rebase/pulls/<n>/reviews/$crid --jq .body
[ -n "$crid" ] && gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
    --jq ".[] | select(.pull_request_review_id == $crid and .in_reply_to_id == null) | \"\(.id) \(.path):\(.line // .original_line) \(.body)\""
echo "$summary" | sed -n '/Merge Risk/p;/pre_merge_checks_walkthrough_start/,/Passed checks/p'
```

The summary is edited in place on every round, so it is read only once its
`final_review_risk_coverage` marker names this sha (`"coveredCommitId":"<sha>"`);
then a missing `crid` means the round raised nothing, not that the review is late.
The last command prints the `Merge Risk` line, the checks' header and, when a check
failed, its table; on #363 that is the `Docstring Coverage` warning. The wait keys on
`Review completed` alone: a `Review skipped: ...` status stays on the sha until a
requested review starts. After ten minutes, read `$st`: `author ignored by
configuration` is a Dependabot PR, which this loop does not run on (above); `draft
pull request` means the loop waits for `gh pr ready`; no status, or a summary saying
the reviews are paused (it pauses itself after five reviewed commits on one PR, to
spare the hourly allowance), is `gh pr comment <n> --body '@coderabbitai review'`,
which reviews the head once and leaves the pause in place, and the wait again. When
it answers that it is rate limited instead of reviewing, wait for the window it
names and ask again, and say so on the card.

**c. CodeRabbit against Greptile.** It comes after CodeRabbit's own review on
purpose: its own findings are not shaped by Greptile's, and then it checks Greptile's
against the code. A pass is due when (a) and (b) are both done for the sha and
Greptile raised a finding on it, or you answered a Greptile finding since the last
pass. Until the verdict is in, reply to none of Greptile's threads: CodeRabbit
answers replies in any thread by itself (`auto_reply`), and on #367 it agreed with an
answer on a Greptile thread and resolved it before any pass had been asked for. One PR
comment hands it Greptile's side and asks for a verdict on each item:

```bash
sha=$(gh pr view <n> --json headRefOid -q .headRefOid); short=${sha:0:9}
rid=$(gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
    --jq ".[] | select(.user.login == \"greptile-apps[bot]\" and .original_commit_id == \"$sha\" and .in_reply_to_id == null) | .pull_request_review_id" | tail -n 1)
{ echo "@coderabbitai Adversarial pass on Greptile's review of $short. You have reviewed this commit on your own; now act as Greptile's adversary. For each item below, check the claim against the code at $short yourself and answer **confirmed**, **refuted** or **partly**, with the evidence: file and line, or the script you ran and what it printed. Do not take Greptile's reasoning or mine on trust, and do not agree to be agreeable: a finding that does not hold is refuted, and an answer of mine that does not hold is wrong. Then list any defect in this diff that neither review raised. Start your reply with the line \`Adversarial verdict on $short\`, then one line per item, then the misses."
  echo; echo "Greptile's findings on $short:"
  [ -n "$rid" ] && gh api --paginate "repos/letsrebase/rebase/pulls/<n>/comments?per_page=100" \
      --jq ".[] | select(.pull_request_review_id == $rid and .in_reply_to_id == null) | \"- \(.html_url) \(.path):\(.line // .original_line) \(.body | split(\"<details>\")[0] | gsub(\"<[^>]*>\"; \"\") | gsub(\"\\\\s+\"; \" \"))\""
} > adversary.md
```

Each line carries the finding's link, its `path:line` and the whole finding up to
Greptile's own fix prompt (on #367's first commit, three lines). Then add by hand, under `Greptile
findings I answered since the last pass:`, one line per answer: the thread's link and
the answer in a sentence. When Greptile raised nothing new and there is no new answer,
there is no pass. Post it and wait for the reply, a new comment by the bot that
opens with the line it was asked for:

```bash
short=$(gh pr view <n> --json headRefOid -q .headRefOid | cut -c1-9); since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh pr comment <n> --body-file adversary.md
for i in $(seq 40); do   # twenty minutes; #388's verdict took two and a half
  verdict=$(gh api --paginate "repos/letsrebase/rebase/issues/<n>/comments?per_page=100" \
      --jq ".[] | select(.user.login == \"coderabbitai[bot]\" and .created_at >= \"$since\" and any(.body | split(\"\n\")[]; test(\"^\\\\s*Adversarial verdict on $short\\\\s*$\"))) | .body")
  [ -n "$verdict" ] && break; sleep 30
done
printf '%s\n' "$verdict"
```

The verdict line is matched as a line of its own, wherever it sits: on #388 the reply
opened with a tip and CodeRabbit's analysis before `Adversarial verdict on
bd6747fe9`, and a quote of the request (`> ...`) does not match. Read it through:
every item listed has a verdict; one without is asked for again in a reply to the
verdict. Twenty minutes with no verdict: post the same comment once more and wait
again; still nothing, say so on the card and tell the person before you merge, as
with a Greptile that does not review.

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
gh api graphql --paginate -f query='query($endCursor: String) { repository(owner: "letsrebase", name: "rebase") { pullRequest(number: <n>) { reviewThreads(first: 100, after: $endCursor) { pageInfo { hasNextPage endCursor } nodes { isResolved comments(first: 1) { nodes { author { login } url } } } } } } }' \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved | not) | "\(.comments.nodes[0].author.login) \(.comments.nodes[0].url)"' \
  || echo "thread query failed: the gate is not met"
```

**The loop ends** on the sha that will merge when: Greptile's run on it raised nothing
new and its check run is `success`; CodeRabbit's status on it reads `Review completed`, its
review of it raised nothing new and none of its pre-merge checks failed without an
answer; the command above prints nothing; every verdict of the last adversarial pass
was acted on; and no `**Decision for the lead**` is open. What the loop did goes on the
card in the same `**Review applied:**` comment as the independent review (the
`linear-content` shape): each reviewer's findings, which changed the code (sha),
which were answered and why, the adversarial verdicts (confirmed, refuted, partly,
misses), Greptile's final score and CodeRabbit's `Merge Risk` line.

## Merging

**Merge with a merge commit**, the repository's shape:
`gh pr merge <n> --merge --delete-branch`. Then, right away, the
`**Merged:**` comment on the card with the run ids, the commit sha, the test counts
and what you opened and saw: on a single-card PR the automation sets `Done` at the
merge without waiting for it, and a card that closes with nothing under it was
closed by a robot. On a milestone PR nothing moves by itself: set each card it
listed to `Done` in the same pass, each with its own evidence comment.

## Releases

**Production is a separate step.** Preview deploys on the green trunk run;
**production moves only on a tag** (`docs/design/DECISIONS.md`, 2026-09-09) and only
when asked. The tag is the project's own, `<project>-v<semver>`, one past the
project's last (`git tag -l '<project>-v*' --sort=-v:refname | head -1`; on
2026-09-28 those were `website-v0.21.0`, `hub-v0.43.0` and
`pigrocrm-v0.28.0`), pushed from the merge commit on `main`:

```bash
git fetch origin && git tag <project>-v<semver> origin/main && git push origin <project>-v<semver>
```

The tag gets a CI run of its own, and since 2026-09-28 (REB-563) that run is
**every job of the project the prefix names plus the shared packages it uses**
(`shared/ui`, `shared/analytics`), and nothing else: a website tag runs the
website's gate and image and `shared/analytics`, not the CRM's suite. A prefix
`changes` does not know keeps the full run. `deploy-<project>.yml` waits for that
run and refuses a red one, or one that has not completed within thirty minutes;
it never reads the trunk's run for the same commit, which may have skipped the
project. Read three things before you
write anything: the tag run's job list is the project's and nothing of another
project ran; `ci` is green on it; the deploy's own run answered its health check.
Then the card gets its `**In production:**` comment with the tag, the tag run's id,
the deploy run's id and what answered (the URL you opened and what you saw), in
the `linear-content` shape. The mechanism and the five secrets are
`docs/adding-a-project.md` § 7; a tag is immutable once pushed (ruleset, 2026-09-10),
so a wrong one is followed by the next version, never moved.

The project update for the release follows the `**In production:**` comment, and its
claim is the tag, the run or the URL you read, never the card's title. When that
release is what kept the project open, the same step moves it to Completed.
