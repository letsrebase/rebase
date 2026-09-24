@AGENTS.md

## Claude Code specifics

Everything above applies. These are the few things that are about this harness rather
than about the repository.

**Hard rules, on top of AGENTS.md:**

- Never claim a deploy or a page works on the strength of a 200. Exercise the changed
  path and read what came back.
- Never widen a `preflight.json` glob to make a check stop selecting your diff. If a
  check is wrong, fix the check.
- Never finish a run that found a defect without filing it in Linear, and never move
  an issue to `Done` on a check you did not read. `docs/tracker.md` is the whole
  contract; it takes two minutes and it is the difference between a board that is worth
  opening and a list of stale cards.
- Linear is the constant source of truth, for you and for every human working here.
  `linear-rebase` is the only Linear surface for this repository.
- Never edit `projects/<name>/` and the root workspace files in the same commit when
  the root change is a move: a commit that both moves and edits a file loses git's
  rename detection, and this repository is merged against a busy `main`.

**Context discipline.** Read `projects/<name>/AGENTS.md` for the project you are in
before its source. The design record for PigroCRM is
`projects/pigrocrm/docs/superpowers/specs/`, in Italian, and it is the reason a piece
of code looks the way it does: read the spec before rewriting something that seems
odd.
