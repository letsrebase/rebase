# Contributing

`AGENTS.md` is the longer version of this file and applies to people and to coding
agents alike. This is the short workflow.

## Before you start

```
uv sync --frozen
pnpm install --frozen-lockfile
```

Python 3.13, Node 22 and Docker. PigroCRM's document rendering also needs Pandoc and
Typst on `PATH`; without them about thirty tests fail with
`strumento di composizione non installato`. On Nix, `nix develop` provides everything
but Docker (see the README).

## The loop

1. Branch from `main`. Short-lived, one subject.
2. Work inside one project where you can. A change that touches
   `projects/<name>/` and the root workspace files at once is fine, but split the
   *move* of a file from the *edit* of it into two commits: a commit that does both
   loses git's rename detection, and this repository is merged against a busy trunk.
3. Run the checks your diff can break, before pushing:
   ```
   preflight --list      # what would run, and which changed paths selected it
   preflight             # run them
   ```
   `preflight --install-hook` wires it into `git push`. Expect that push to take
   minutes rather than seconds: that is the expensive tier doing its job before the
   pull request exists, rather than after it.
4. Open a pull request. CI runs a cheap, change-scoped gate on it; the full suite runs
   on the trunk after the merge.

## Tracker

Every change starts from an issue in Linear, team `rebase`. Linear is the source of
truth: work that is not on the board did not happen. Four levels: an **initiative** is
a product and is permanent; a **project** is a release with an end, closed when it
ships; a **project milestone** is an outcome inside a release; an **issue** is one
agent run, one PR. Every issue carries exactly one `type` label and exactly one `area:*`
label, both from enforced groups, and priority and effort as Linear's own fields, never
labels. Before the first file changes, read the open cards next to yours (same area,
same screen or table) and link or narrow around them; while you work, keep your card
current with a comment at every turn a reader could not infer, and leave a project
status update whenever something changed that the issue list alone does not show.
Linear's GitHub app links a PR to its card, and since 2026-09-16 the team's automation
moves the state too: `In Progress` while the PR is open, `Done` when it merges. What
stays in your hands is the `In Progress` move before the branch exists, every comment,
and the closing evidence written on the card before or right after the merge. Full
conventions are in `docs/tracker.md`.

## Commits

Conventional Commits, English, first person, written the way a person writes:

```
feat(spazi): the root installation has a space name of its own
fix(web): the sidebar is as tall as the window, not the page
```

No em dashes, no emoji, no "not just X but Y", and **never an AI co-author trailer**.
The subject says what changed and, where it is not obvious, the body says why: the
reason is the part nobody can reconstruct later.

## What review looks for

- The change does what the commit says and nothing else.
- A bug fix comes with the reproduction that used to fail.
- A new dependency is pinned, and a build-or-test dependency went into the catalog
  rather than into a package.
- No absolute paths, and no credential of any kind, anywhere.
- Anything taken off the PR path appears in `.github/preflight.json`.

## Decisions that are not a contributor's to take

The licence, the repository's visibility and name, domains, production deploys, and
anything a client will read. Ask first.
