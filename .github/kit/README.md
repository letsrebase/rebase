# The workflow kit's copy

Everything under `.github/kit/` and the skills `linear-ticket`, `linear-content` and
`pr-creation` under `.agents/skills/` come from the workflow kit, whose source is
`tooling/workflow-kit/` in [letsrebase/rebase](https://github.com/letsrebase/rebase).
Do not edit them here: `ci` runs `kit.py check`, which fails on a file that differs from
`manifest.json`. Change the kit's source instead, then sync.

What is this repository's own:

- `.github/workflow.json`: the tracker, commit types and scopes, PR sections, merge
  method and review bots the kit's skills and checks read.
- `AGENTS.md` and the tracker contract (`tracker.contract` in `workflow.json`).
- The directory `extras` names (usually `docs/agents/`): this repository's own steps,
  which the kit's skills read when present.
- Any other skill under `.agents/skills/`.

Two generated things are checked too: `.github/PULL_REQUEST_TEMPLATE.md`, rendered from
`workflow.json`, and `.claude/skills/`, a copy of `.agents/skills/` (Codex and other
agents read `.agents/skills/`, Claude Code reads `.claude/skills/`; a copy rather than a
symlink, because Git for Windows checks a symlink out as a text file by default).

```bash
python3 .github/kit/bin/kit.py check                    # what ci runs
python3 .github/kit/bin/kit.py sync --source ../rebase/tooling/workflow-kit
python3 .github/kit/bin/pr_lint.py --title "..." --body-file pr-body.md
python3 .github/kit/bin/preflight --list                # when .github/preflight.json exists
```

On Windows, use `py` where these say `python3`.
