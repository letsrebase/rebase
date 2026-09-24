# The shared self-hosted Actions runner pool

**Live since 2026-09-24.** `62.83.33.29` (`v2202609426308528003.bestsrv.de`), Netcup,
rebase-owned account. Alias `ci-runner` on both devbox and the Mac
(`~/.ssh/config`, `ssh ci-runner`). Runner group `private-clients`
(`allows_public_repositories: false`, `visibility: selected`), holding
`letsrebase/point` today; the Default group, the one `letsrebase/rebase` would fall
back to if a workflow there ever asked for `self-hosted`, has zero runners
registered, so that path fails closed rather than silently reaching this box.
Verified end to end 2026-09-24: a throwaway `push`-triggered workflow on a `point`
branch ran on `ci-runner-1`, confirmed Docker and the `ci` user; a second, 3-way
matrix workflow then confirmed all three runners picking up concurrent jobs at
once (`ci-runner-1`, `ci-runner-2`, `ci-runner-3`, same timestamp). Both branches
were deleted after.

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

Lorenzo provisioned the VPS himself, on a rebase-owned hosting account rather than
his personal one (his call, 2026-09-24). Everything from first SSH onward is
REB-422.

## Sizing: what was ordered versus what this needs

The VPS that actually exists is **4 vCPU, 7.8GB RAM, 125GB disk**, below the 8
vCPU / 16-32GB this document originally called for. Point's CI runs a Postgres
service container plus Node and Python toolchains per job; `rebase`'s own CI (not
on this pool, but the same shape if a future client repo needs it) adds Docker
image builds and a Postgres-backed Python corpus job. None of it needs pre-baking
onto the host: `actions/setup-node` downloads its toolchain at job time, so the
real requirement is outbound internet, not a golden image. `actions/setup-python`
is the one exception, below.

Running **three runner instances** (`ci-runner-1` through `-3`), confirmed picking
up genuinely concurrent jobs (a 3-way matrix workflow landed one job per runner,
same timestamp). Three, not the full four vCPUs, so a runaway job cannot claim
every vCPU the host has: nothing enforces that as a hard reservation today (no
`CPUQuota` or `AllowedCPUs` on the runner units or their parent slice), so this is
headroom by convention, not a guarantee, until one is configured. 7.8GB across
three Postgres-backed jobs plus any Docker
builds is still tight, the swap backstop (zram plus the swapfile) absorbs a spike
rather than an OOM kill, but sustained heavy concurrent load will show as slower
jobs before it shows as failures. Watch the queue once more than one private
repository is on the pool; a Netcup resize (more vCPU/RAM on the same disk) is the
straightforward path if jobs start waiting rather than just running slower.

## Access: public SSH, key-only, every team member, no Tailscale

Decided 2026-09-24: unlike prodbox, this box is reachable by plain public SSH, not
gated behind Tailscale. Two reasons drove it, not one: the pool has to be usable by
every team member administering it, not only whoever is already enrolled on the
`fiorelorenzo.fl` tailnet, and GitHub's runner process itself never needs Tailscale
either way, it only needs outbound internet to poll the API. The tradeoff this takes
on, and the reason the rest of this section exists, is that public SSH means the
standard hardening (key-only auth) is no longer the *only* layer prodbox relies on;
add the rate-limiting a Tailscale gate made unnecessary there.

**Done:**

1. Debian 13 (trixie), matching the rest of the fleet.
2. Docker Engine 29.8.1 + Buildx + compose plugin v5.5.1.
3. SSH: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin prohibit-password` (prodbox's own drop-in,
   `/etc/ssh/sshd_config.d/99-hardening.conf`, unchanged; verified: a
   password-only connection attempt is refused). Non-root admin user `ci`,
   passwordless sudo, key-only login. `authorized_keys` holds the devbox key and
   both of the Mac's keys, one entry per key rather than one shared credential.
   The root account's own password, emailed by Netcup in plaintext, was rotated to
   a value generated and kept entirely on the box itself (never printed anywhere)
   the moment key access was confirmed, since password SSH is off regardless and
   the emailed value should be treated as burned.
4. UFW: default deny incoming, `22/tcp` open publicly, default allow outgoing.
   `fail2ban` on `sshd` (5 attempts / 10 minutes, 1 hour ban), the layer prodbox
   does not need and this box does since the port is internet-facing.
5. `zram` (`ram/2`, lz4, confirmed active via `zramctl`: `/dev/zram0`, priority
   100) plus a 4G static swapfile (priority -2, fallback once zram fills) and
   `vm.swappiness=100`: 7.9GB of swap total against 7.8GB of RAM, the same shape
   as prodbox's layout scaled to this box's smaller memory.
6. SSH aliases on devbox and on the Mac, the same shape as the existing `prodbox`
   alias on both (`~/.ssh/config`): `Host ci-runner`, `HostName 62.83.33.29`,
   `User ci`, `IdentityFile ~/.ssh/id_ed25519`. Verified working from both.
7. Runner group `private-clients`, `visibility: selected`,
   `allows_public_repositories: false` (an extra layer even if `rebase` were ever
   added to it by mistake: GitHub refuses a public repository in a group with this
   set), scoped to `letsrebase/point`. Adding a repository to the group is the
   whole integration step for a future client.
8. Three runners registered and running as systemd services
   (`actions.runner.letsrebase.ci-runner-{1,2,3}.service`, user `ci`, one
   `actions-runner*` directory each), labels `self-hosted, linux, x64`. Verified
   picking up and completing real jobs, including three at once.
9. Ivan's SSH public key (`ivan-sala@macbook runnergitlab-rebase`) added to the
   `ci` user's `authorized_keys`, alongside the devbox and Mac keys.
10. **`TMPDIR` on real disk, not the tmpfs `/tmp`.** Found 2026-09-24 the same day
    the pool went live: `letsrebase/point`'s `api-tests` job failed twice with
    `OSError: [Errno 28] No space left on device` on a `pip install`, while the
    125G root disk sat at 12% used. `/tmp` here is a 3.9G RAM-backed `tmpfs`
    (Debian's default), and a crashed install from an earlier job had left a
    3.2G unpacked package behind in it, the same class of trap
    `devbox-process-hygiene` already documents for the devbox itself. Fixed on
    all three runners: `TMPDIR=/home/ci/.cache/tmp` (created on the root disk,
    owned by `ci`) via a systemd drop-in on each
    `actions.runner.letsrebase.ci-runner-{1,2,3}.service`, plus
    `/etc/cron.daily/ci-runner-disk-hygiene`, which clears anything left in that
    directory after a day and runs `docker system prune -af --filter
    'until=24h'`. That cron also closes the Docker-prune follow-up below: no
    rollback images live on this box the way they do on prodbox, so a plain
    prune is safe here without prodbox's surgical per-app carve-out.

**Not done, deliberate follow-ups rather than gaps in what exists today:**

- **Ephemeral mode.** The runner is a standard persistent systemd service, not
  `--ephemeral`: it cleans its own workspace between jobs (the runner's default
  behaviour) but does not deregister and rotate credentials after each one. Worth
  doing once a second private repository is on the pool and the job mix is less
  predictable; the mechanism is a wrapper that mints a fresh registration token
  and reconfigures after every job, not a flag alone.
- **A fourth runner instance**, if the queue backs up before a resize is worth
  doing; deferred with the sizing note above.

**Known limitation, on purpose, for now: no isolation between clients on the same
pool.** All three runners share one Docker daemon and run as the same `ci` user,
who has passwordless sudo. A workflow file in any enrolled repository runs
arbitrary code as that user: a compromised dependency or a malicious change from
a collaborator with write access can read or interfere with another job's
workspace, containers, images and build cache on the same host, `sudo` aside, and
deregistration between jobs (the ephemeral-mode item above) would not change that,
since the Docker state itself persists across repositories regardless. This is not
a gap to patch quietly: it needs its own compute, or at minimum its own Docker
daemon, per client trust domain, and that is real infrastructure work, not a
documentation fix. Accepted today because exactly one client, `letsrebase/point`,
is on the pool, so there is no second client's job to isolate from; revisit before
a second private repository with a different trust owner joins, not after.

**A CI-published container port must bind to loopback, not every interface.**
UFW's default-deny on `22/tcp` does not stop Docker: Docker inserts its own
`iptables` rules ahead of UFW's chain, so a service container published as
`5432:5432` (all interfaces) reaches the public internet regardless of what UFW
says, the same trap `prodbox-deploy` already documents for a hand-run container.
`letsrebase/point`'s dynamic-port fix above solves the port-collision problem
between concurrent jobs; it does not by itself bind to loopback. A future
workflow's service container should publish `127.0.0.1:<port>:<container-port>`
(or the dynamic-port equivalent bound to loopback), never a bare host port.

**The registration-token wrapper the ephemeral-mode item above still needs**
should use a GitHub App installation token, not a personal access token: a PAT is
tied to whoever created it, so if that person's account or token access changes,
the wrapper stops minting registration tokens and no replacement runner can
register. A GitHub App's installation token is not tied to one person's account.

**`runs-on: [self-hosted, linux, x64]` resolves within the runner groups a
repository can see, not across the whole org.** `private-clients` is the only
group configured for `visibility: selected`, and the Default group has zero
runners registered (confirmed above), so a repository not explicitly added to
`private-clients` has no self-hosted runner to fall back to at all; the generic
labels cannot accidentally route a job to a different pool because there is no
other pool a selected repository can reach.

## Repository side, once a repository joins the pool

`letsrebase/point`'s own `ci.yml` moved to `runs-on: [self-hosted, linux, x64]` on all
four jobs (`letsrebase/point#67`), the worked example for what changes on a shared
host that a hosted runner's isolated VM used to hide: a service container's fixed
host port (`5432:5432` for Postgres) collides the moment two Postgres-backed jobs
land on different runners at once, since all three runners share one Docker daemon;
moved to a dynamic host port, resolved through `job.services.postgres.ports` at
runtime. `actions/setup-python` also stopped working: it only ships prebuilt Pythons
for the OS versions in GitHub's own manifest, which does not include this pool's
Debian 13, so it moved to `astral-sh/setup-uv` (`point`'s own `apps/api` already
carries a `uv.lock`), whose Python builds are portable across host distros.

Scoping a repository into `private-clients` is now automatic: `new-client-repo.mjs`
does it the moment the repository exists on GitHub, in both the `--create-repo` and
the retrofit path, and prints whether it actually ran (it needs `admin:org` on the
`gh` token and the repository to exist first). Writing the workflow file itself
stays a per-repository decision, since the stack varies by contract;
`tooling/client-repo-starter/README.md` § CI: the shared self-hosted runner pool
carries the checklist.
