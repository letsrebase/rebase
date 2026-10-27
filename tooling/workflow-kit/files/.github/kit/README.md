# The workflow kit's copy

Everything under `.github/kit/`, the skills `linear-ticket`, `linear-content` and
`pr-creation` under `.agents/skills/`, and `.omp/hooks/pre/linear-gate.ts` come from the
workflow kit, whose source is `tooling/workflow-kit/` in
[letsrebase/rebase](https://github.com/letsrebase/rebase). Do not edit them here: `ci`
runs `kit.py check`, which fails on a file that differs from `manifest.json`. Change the
kit's source instead, then sync.

What is this repository's own:

- `.github/workflow.json`: the tracker, commit types and scopes, PR sections, merge
  method and review bots the kit's skills and checks read.
- `AGENTS.md` and the tracker contract (`tracker.contract` in `workflow.json`).
- The directory `extras` names (usually `docs/agents/`): this repository's own steps,
  which the kit's skills read when present.
- Any other skill under `.agents/skills/`.

Three generated things are checked too: `.github/PULL_REQUEST_TEMPLATE.md`, rendered
from `workflow.json`; `.claude/skills/`, a copy of `.agents/skills/` (Codex and other
agents read `.agents/skills/`, Claude Code reads `.claude/skills/`; a copy rather than a
symlink, because Git for Windows checks a symlink out as a text file by default); and the
Linear gate's entry in `.claude/settings.json`, the rest of which is this repository's.

The gate (`bin/linear_gate.py`) refuses a Linear write that breaks the `linear-content`
rules before it is sent, in Claude Code and in omp, and says why: rewrite and retry.
`bin/linear_audit.py` is the read-only audit of the team in `workflow.json`, for a
scheduled job with a `LINEAR_API_KEY` secret.

```bash
python3 .github/kit/bin/kit.py check                    # what ci runs
python3 .github/kit/bin/kit.py sync --source ../rebase/tooling/workflow-kit
python3 .github/kit/bin/pr_lint.py --title "..." --body-file pr-body.md
python3 .github/kit/bin/preflight --list                # when .github/preflight.json exists
```

On Windows, use `py` where these say `python3`.

`preflight --install-hook` writes a pre-push hook into the repository's own git
directory, never into a `core.hooksPath` directory, which it refuses with the line to add
there instead. The hook tests the checked-out commit, so it refuses a push that sends a
different commit (another branch, say) and a push from a tree with uncommitted changes.
`PREFLIGHT_SKIP=1` bypasses it.
