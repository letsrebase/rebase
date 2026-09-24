# The shared self-hosted Actions runner pool

A private, rebase-managed client repository bills hosted-runner minutes on the org's
GitHub plan; `letsrebase/point` already hit a payment failure that blocked every job
instantly (2026-09-24). This is the shared pool that replaces hosted runners for
every private client repository, on its own rebase-owned Netcup VPS, never on the
devbox: the devbox already runs agent sessions on the same 8 vCPU / 16GB the pool
would compete with (measured 2026-09-24, 767MB free out of 15GB at the time this was
written), and it holds credentials for every client and personal repo: a runner
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
personal one (his call, 2026-09-24), that step is not in this document. Everything
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

## Access: public SSH, key-only, every team member, no Tailscale

Decided 2026-09-24: unlike prodbox, this box is reachable by plain public SSH, not
gated behind Tailscale. Two reasons drove it, not one: the pool has to be usable by
every team member administering it, not only whoever is already enrolled on the
`fiorelorenzo.fl` tailnet, and GitHub's runner process itself never needs Tailscale
either way, it only needs outbound internet to poll the API. The tradeoff this takes
on, and the reason the rest of this section exists, is that public SSH means the
standard hardening (key-only auth) is no longer the *only* layer prodbox relies on;
add the rate-limiting a Tailscale gate made unnecessary there.

1. Debian 13 (trixie), matching the rest of the fleet.
2. Docker Engine + Buildx + compose plugin.
3. SSH: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin prohibit-password` (prodbox's own drop-in,
   `/etc/ssh/sshd_config.d/99-hardening.conf`, unchanged). A non-root admin user
   with passwordless sudo, key-only login, one `authorized_keys` entry per person
   rather than one shared key, so access is revocable per person: the devbox key
   (`devbox-git`), the Mac's two keys (`id_ed25519`, `macbook`, same as prodbox's own
   list), and Ivan's own key once he sends its public half. Needed from Lorenzo
   before this step is complete: Ivan's SSH public key.
4. UFW: default deny incoming, `22/tcp` open publicly (no Tailscale hop to gate it
   this time), `fail2ban` on `sshd` since the port is now internet-facing rather
   than tailnet-only, a layer prodbox does not need and this box does.
5. `zram` + a static swapfile, sized the way prodbox's is: heavy image builds spike
   memory past what 16-32GB alone comfortably absorbs.
6. SSH aliases on devbox and on the Mac, the same shape as the existing `prodbox`
   alias on both (`~/.ssh/config`): `Host ci-runner`, `HostName <public IP>`,
   `User <admin>`, `IdentityFile ~/.ssh/id_ed25519`. Written once the VPS exists and
   its IP is known; there is no Tailscale hostname to use instead here, so the
   public IP is the `HostName` directly.
7. Register the runners as an **org-level runner group** (`letsrebase` organization
   settings -> Actions -> Runner groups), **scoped to selected repositories**: every
   private client repository, `letsrebase/point` first, and explicitly never
   `letsrebase/rebase`. This is the actual mechanism that turns the fork-PR RCE
   concern above into a closed door rather than a documented risk, and it is also
   what makes the pool "usable by every private repo" true without hand-wiring each
   one: adding a repository to the group is the whole integration step.
8. Register each runner with `--ephemeral`: a runner that does one job and
   deregisters leaves nothing on disk or in memory for the next job to inherit,
   whichever repository it belonged to. A small systemd unit or wrapper script
   re-registers a fresh ephemeral runner after each job completes, using a
   short-lived registration token minted from an org-scoped credential (a GitHub
   App or a fine-grained PAT with `organization_self_hosted_runners: write`), never
   a personal access token tied to one person's account.
9. A cron-driven prune, the same shape as prodbox's own disk-hygiene rule
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
