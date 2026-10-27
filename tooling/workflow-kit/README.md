# Workflow kit

The procedure an agent or a person follows here for Linear cards, pull requests and CI,
written once and copied into every repository that uses it: this monorepo, the client
repositories built from `tooling/client-repo-starter/`, and Lorenzo's personal
repositories. Decided 2026-10-27 (`docs/design/DECISIONS.md`, project P-REB-67).

## What is in it

`files/` is copied verbatim into a repository, path for path:

| Path | What |
|---|---|
| `.agents/skills/linear-ticket/` | the board workflow: workspace check, neighbour scan, filing in one call, the API traps |
| `.agents/skills/linear-content/` | how a card, a comment and a project update are written |
| `.agents/skills/pr-creation/` | from the card to the merge |
| `.github/kit/bin/kit.py` | `sync` and the offline `check` |
| `.github/kit/bin/pr_lint.py` | the PR title and body check `ci` runs |
| `.github/kit/bin/preflight` | the local, diff-scoped run of `.github/preflight.json` |

Nothing in `files/` names a team, a prefix or a scope. Those are data, in each
repository's `.github/workflow.json` (`workflow.example.json` is the starting point), and
the skills and scripts read them from there. Steps that only one repository takes (this
one's Greptile and CodeRabbit loop, its release tags) live in that repository's
`docs/agents/`, which the skills read when present.

`kit.py sync` also writes three things that are not copies: the pull request template,
rendered from `workflow.json`; `.claude/skills/`, a copy of `.agents/skills/` for Claude
Code (a copy rather than a symlink, so a Windows clone works); and
`.github/kit/manifest.json`, the hash of every managed file. `kit.py check` compares all
three, needs no network and no dependency beyond Python 3.10, and is a step of `ci`.

## Using it

A repository that adopts the kit, from a rebase checkout next to it:

```bash
cp ../rebase/tooling/workflow-kit/workflow.example.json .github/workflow.json   # then fill it in
python3 ../rebase/tooling/workflow-kit/files/.github/kit/bin/kit.py sync --source ../rebase/tooling/workflow-kit
```

Then one CI step that runs `python3 .github/kit/bin/kit.py check`, and on pull requests
`python3 .github/kit/bin/pr_lint.py --github` with `GITHUB_TOKEN` in its environment,
inside the repository's required check.

Changing the kit: edit `files/`, bump `VERSION`, run the tests, then `sync` this
repository in the same PR (its `ci` fails otherwise). Other repositories pick the change
up with their own `sync`, in their own PR.

```bash
python3 -m unittest discover -s tooling/workflow-kit/tests
```
