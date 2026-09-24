# Onboarding a freelancer onto a rebase-managed contract

For whoever is inviting them (Lorenzo or Ivan), once the repository and its Linear
structures already exist (`tooling/client-repo-starter/README.md` § Doing the
setup). One pass, before their first card.

## Access

1. **GitHub**: add them as a collaborator on the repository (`Write`, not `Admin`),
   under the `letsrebase` org.
2. **Linear**: invite them to `linear.app/letsrebase` as a member. There is no
   narrower option today (§ Tell them, below) — the Free plan has no guest role.
3. **Whatever secrets the codebase's own `AGENTS.md` § This codebase says a
   contributor needs** (an `.env.example` to fill in, a local database, API keys
   that are the contract's own and not rebase's) — handed over by whatever channel
   the contract already uses, never through Linear or GitHub.

## Tell them, in the invite message

- This is a **Linear-only tracker**: no card, no branch, and `AGENTS.md` §
  Tracker: Linear is the whole contract if their harness does not read skills.
- **They will see the whole `letsrebase` workspace**, not just this repository's
  team and project — every team, every other client's work, because Linear's Free
  plan has no guest role. Say it plainly: stay inside this repository's own Linear
  team and project, and nothing seen outside them gets discussed, repeated or
  acted on. This is not a technical wall, it is an ask, and it is worth being
  explicit about exactly because it is not enforced.
- **Conventional Commits, the repository's PR template, no AI co-author trailer**:
  already in `AGENTS.md`, worth saying once out loud too.
- Where the repository's own `AGENTS.md` § This codebase is, and that it is read
  before the source, every time.

## What is not part of this kit

Anything that only makes sense on rebase's own devbox — the unattended pickup loop,
Hindsight memory, `chrome-profiles`, the orchestration skills — is infrastructure
for how *we* run agents, not a convention a freelancer's own machine or harness has
to reproduce. What travels with the repository is exactly what is in
`template/`: the tracker contract, the commit and PR conventions, nothing about how
any particular person runs their agent.
