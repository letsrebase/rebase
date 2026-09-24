# The shared self-hosted Actions runner pool

A private, rebase-managed client repository bills hosted-runner minutes on the org's
GitHub plan; `letsrebase/point` already hit a payment failure that blocked every job
instantly (2026-09-24). This is the shared pool that replaces hosted runners for
every private client repository, on its own rebase-owned Netcup VPS, never on the
devbox: the devbox already runs agent sessions on the same 8 vCPU / 16GB the pool
would compete with (measured 2026-09-24, 767MB free out of 15GB at the time this was
written), and it holds credentials for every client and personal repo — a runner
executes whatever a workflow file in the target repository says, and a shared box is
the wrong place to put that next to everything else here.

**`letsrebase/rebase` itself stays on hosted `ubuntu-latest`.** It is public, hosted
minutes are free for a public repository, and a self-hosted runner shared with a
public repo that accepts fork PRs is a known remote-code-execution vector: a
malicious fork PR can edit the workflow file itself and run arbitrary code on
whatever runner picks the job up. Mixing that into a pool that also holds private
clients' secrets is the failure mode to avoid, not a theoretical one worth writing
down and then ignoring.

## Who does what

Lorenzo provisions the VPS himself, on a rebase-owned hosting account rather than his
personal one (his call, 2026-09-24) — that step is not in this document. Everything
from first SSH onward is REB-422.

## Sizing

- **8 vCPU**, 16-32GB RAM, 100-200GB NVMe/SSD. Point's CI alone runs a Postgres
  service container plus Node and Python toolchains per job; `rebase`'s own CI adds
  Docker image builds (pigrocrm/website/hub) and a Postgres-backed Python corpus job.
  None of it needs pre-baking onto the host: `actions/setup-node` and
  `actions/setup-python` download toolchains at job time, so the real requirement is
  outbound internet (npm, PyPI, Docker Hub/GHCR, github.com), not a golden image.
- Concurrency is **runner count, not machine size**: one `actions/runner` process
  handles exactly one job at a time. Start with 2-4 registered instances; watch the
  queue and add more if jobs wait.

## Setup, mirroring prodbox's own hardening (`prodbox-deploy` skill)

1. Debian 13 (trixie), matching the rest of the fleet.
2. Docker Engine + Buildx + compose plugin.
3. Tailscale, joined to the same tailnet as devbox and prodbox. SSH reachable only
   over `tailscale0`; UFW default-deny incoming, no public `:22`. A non-root admin
   user (`sudoers.d` entry, no password, key-only login) — the same shape as
   prodbox's `prod` user, not a shared credential with it.
4. `zram` + a static swapfile, sized the way prodbox's is: heavy image builds spike
   memory past what 16-32GB alone comfortably absorbs.
5. Register the runners as an **org-level runner group** (`letsrebase` organization
   settings -> Actions -> Runner groups), **scoped to selected repositories**: add
   `letsrebase/point` and every future private client repo, and explicitly leave
   `letsrebase/rebase` off the list. This is the actual mechanism that turns the
   RCE concern above into a closed door rather than a documented risk.
6. Register each runner with `--ephemeral`: a runner that does one job and
   deregisters leaves nothing on disk or in memory for the next job to inherit,
   whichever repository it belonged to. A small systemd unit or wrapper script
   re-registers a fresh ephemeral runner after each job completes, using a
   short-lived registration token minted from an org-scoped credential (a GitHub
   App or a fine-grained PAT with `organization_self_hosted_runners: write`), never
   a personal access token tied to one person's account.
7. A cron-driven prune, the same shape as prodbox's own disk-hygiene rule
   (`prodbox-deploy` § Disk is the shared resource): Docker images and build cache
   grow with every job across every repository in the pool, and nothing here
   prunes itself.

## Repository side, once the pool exists

Every generated client repository (`tooling/client-repo-starter/`) keeps
`runs-on: ubuntu-latest` in its own committed workflow until this pool exists and is
proven; switching a repository over is `runs-on: [self-hosted, linux, x64]` (or
whatever label the registration used) in that repository's own `.github/workflows/`,
done per repository once the pool is live, not part of the starter kit's own
scaffolding step.
