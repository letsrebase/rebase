# Adding a project

The runbook. Follow it in order; each step exists because skipping it fails somewhere
that does not name the cause.

Throughout, `<name>` is the project's directory name: lowercase, no spaces, the name
people already use for the product.

## 1. The directory

```
projects/<name>/
  apps/            deployables
  packages/        libraries this project owns and nobody else uses
  docs/            this project's own documentation, specs and notes
  README.md        what it is, how to run it, how to deploy it
  AGENTS.md        the facts an agent needs before touching it
```

A library only this project uses stays in `projects/<name>/packages/`. It moves to
`shared/` the day a second project imports it, and not before.

## 2. Wire it into the workspaces

**Node**, nothing to do for the workspace itself: `pnpm-workspace.yaml` already
globs `projects/*/apps/*` and `projects/*/packages/*`. Do point every
build-and-test dependency at the catalog:

```json
"devDependencies": { "typescript": "catalog:", "vitest": "catalog:" }
```

If a version you need is not in the catalog yet, add it there rather than pinning it
in the package. If you need a *different* version from the one in the catalog, say
why in the package.json, in a comment on the line above: a second TypeScript major
in this repository is a decision, not a detail.

**Python**: two lists in the root `pyproject.toml`, and both are required:

```toml
[tool.uv.workspace]
members = [..., "projects/<name>/apps/api"]

[project]
dependencies = [..., "<name>-api"]

[tool.uv.sources]
<name>-api = { workspace = true }
```

Then `uv lock` and commit the lockfile. Members are listed one by one on purpose:
globbing `projects/*/apps/*` also matches any Vite app, and uv refuses to start on a
member with no `pyproject.toml`.

**Every existing Dockerfile that runs `uv sync` now fails**, with "Workspace member
... is missing a `pyproject.toml`" naming your new package, because that member is
not in their build context. Add one `COPY` line to each. This is the loud half of the
tradeoff described in `architecture.md`.

## 3. Lint config

Add `projects/<name>/ruff.toml` extending the root one if the project is Python:

```toml
extend = "../../ruff.toml"

[lint.isort]
known-first-party = ["<name>"]
```

Node projects inherit nothing automatically: put the eslint config where the app
expects it, and if two projects end up with the same config, move it to
`tooling/eslint-config/` and depend on it as `workspace:*`.

## 4. Tests and types

Append the project's paths to the two lists in the root `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = [..., "projects/<name>/apps/api/tests"]

[tool.mypy]
mypy_path = [..., "projects/<name>/apps/api/src"]
files     = [..., "projects/<name>/apps/api/src"]
```

Both are root-relative, and everything runs from the root. Do not create a
`pyproject.toml` inside `projects/<name>/` for this: it is not a workspace member, a
bare `uv run` inside that directory would quietly build a second virtualenv there,
and the paths would only resolve when you happened to be standing in the right place.

## 5. CI

Two edits to `.github/workflows/ci.yml`, and no new workflow file:

```yaml
  # in the `changes` job
  outputs:
    <name>_py: ${{ steps.filter.outputs.<name>_py }}
  # ... and the matching filter block. Underscores, never dashes: a dash in an output
  # name is invalid expression syntax and the run dies with no job started.

  <name>-py:
    needs: changes
    if: ${{ !cancelled() && (needs.changes.outputs.all == 'true' || needs.changes.outputs.<name>_py == 'true') }}
    uses: ./.github/workflows/_python-gate.yml
    with:
      lint-path: projects/<name>
      sources: projects/<name>/apps/api/src
      tests: projects/<name>/apps/api/tests
```

Then add the new job to `ci`'s `needs:` list. Forgetting that is the failure that
does not look like one: the job runs, it can go red, and `ci` stays green.

Copy that `if:` exactly; both halves earn their place. `!cancelled()` keeps the job
alive when a job it needs was skipped, because a skipped dependency skips the
dependent whatever its own `if` says, and skipped counts as passing. `outputs.all`
is the push whose base commit could not be reached, where the honest answer to
"what changed" is everything.

**A project that ships an image needs its own image filter**, listing what the build
context actually copies in: the project's own tree, `shared/**` if it uses it, and
the root manifests (`package.json`, `pnpm-lock.yaml`, `pyproject.toml`, `uv.lock`,
`.dockerignore`). The trunk tier is scoped like the PR tier since 2026-09-09, so a
filter that is missing means an image that stops being built rather than one that is
built too often, and the deploy will happily ship the last one that was.

**A project that deploys needs a `<name>_deploy` filter too**, which is that image
filter plus the two workflow files that decide the deploy. Write it with the YAML
anchor the existing three use (`&<name>_image` on the image filter, `*<name>_image`
in the deploy one) rather than a second copy of the paths: `changes` publishes these
three as the `changed-paths` artifact and each deploy reads its own key from it, so a
drifted copy is a project that stops deploying. It happened the other way round
before 2026-09-09, when each deploy carried its own grep and PigroCRM's had lost
`shared/brand`.

## 6. Preflight

Add the project's expensive checks to `.github/preflight.json`, with `when` globs
scoped to `projects/<name>/**`. Mark `serial: true` anything that binds a fixed host
port or a shared database (this box runs several agents at once and a port
collision reads exactly like a failing test). Then run `preflight --list` and read
which checks your diff actually selects, rather than assuming the globs are right.

**Anything you took off the PR path has to appear here.** A heavy check in neither
tier is a hole, not a saving.

## 6b. The flake

Three edits to `flake.nix`, all in the pattern the existing projects set, and no
version anywhere: a Python deployable is `pythonApp { name; members; core; }` (the
workspace members its Dockerfile `--package`s, and the `packages/core` directory
whose `alembic.ini` and `migrations/` ship beside the venv), a Vite deployable is
`viteApp { name; dir; shared; }` (the pnpm package name, its directory, and the
`shared/*` libraries its Dockerfile copies). Then a NixOS module under
`flake.nixosModules` that restates the project's compose file and its `deploy/` nginx
configuration, one systemd unit per compose service (the API, its MCP server), the
`nginx-headers` module imported for the origin's security headers, and a
`checks.<name>` VM test that boots it and probes what the host's nginx would: the
health path, the SPA on a deep link, the API behind its prefix, the MCP server refusing
a call with no token. A `pnpm-lock.yaml` change moves the pnpm store hash in
`flake.nix`; the `nix-packages` preflight check fails naming the new one, and that is
where it goes.

A project that ships nothing (a library, a shared asset) adds nothing here.

## 7. Deploy

Two environments, two triggers, and no deploy logic of your own:

- **preview** when CI concludes green on `main` for a commit that touched the project;
- **production** on a version tag, `<name>-v<semver>`, never on a branch.

A bare `v1.2.0` cannot work here: it does not say which project it releases. The tag
is project-scoped for the same reason the directory is.

The tag is also a push `ci.yml` listens to (`tags: ['*-v*']`), so it earns a full CI
run of its own, every job, and `_deploy-compose.yml` gates the production deploy on
that run rather than on the trunk's run for the same commit. The trunk tier is
path-scoped: a docs-only commit after a red change to your project gets a trunk run
where every one of your jobs is skipped and `ci` concludes success, and a tag on that
commit would otherwise ship the red code. The price is one full run per release.

The mechanism lives in `.github/workflows/_deploy-compose.yml` and is shared. What a
project writes is a caller, `deploy-<name>.yml`, with one job per environment, each
naming four things: the GitHub environment, the compose directory, the compose project
name, and a health URL. A stack with more than one process worth probing (REB-246)
also names an `extra-health-url`, curled the same way alongside the first; optional,
and empty by default for every project that has only the one. Copy
`deploy-pigrocrm.yml`; it is deliberately short.

The preview trigger is `workflow_run` on CI, not `push`, because the deploy refuses an
unverified commit and waiting for CI on a billed runner cost more than the deploy
itself (measured 2026-09-09: 458 of 514 seconds). Two things follow, and the copied
file already does both. The preview job passes `ref: ${{ github.event.workflow_run.head_sha }}`,
since `github.sha` on that event is the branch tip at event time and can already be a
commit CI never saw. And a `workflow_run` workflow always runs in its default-branch
version, so a change to one of these files cannot be exercised from a branch: land it
and watch the next trunk push.

Three rules that are easy to get wrong and expensive to debug:

1. **`secrets: inherit` on both jobs.** A reusable workflow reads an environment's
   secrets only when the caller inherits. Without it every `DEPLOY_*` secret is the
   empty string, silently. `_deploy-compose.yml` fails loudly on that, by design.
2. **Never let compose derive its project name.** Pass `-p`. Otherwise the name comes
   from the directory, and moving the project in the repository orphans the running
   stack and starts a second one beside it.
3. **A health URL that touches the database.** An endpoint answering from
   configuration alone reports a healthy deploy with Postgres on the floor.

### Where the per-environment configuration lives

In **GitHub Environments**, named `<name>-preview` and `<name>-production`, each
holding the same four secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`,
`DEPLOY_SSH_KEY`. Always those four names. Ten projects with two environments each are
forty secrets and four names, instead of forty names to remember. Environments also
give the Deployments tab a real per-environment history, and they are where a required
reviewer on production goes the day the account is on a paid plan.

```
gh api -X PUT repos/letsrebase/rebase/environments/<name>-preview
gh secret set DEPLOY_HOST --env <name>-preview --body '...'
```

The deploy stays off until its arming variable exists:
`vars.<NAME>_PREVIEW_ENABLED` and `vars.<NAME>_DEPLOY_ENABLED`, both `'true'` to run.
A fresh repository has neither, so nothing deploys by accident.

### What the host needs

One directory per environment, `DEPLOY_PATH`, each with its own `.env`, its own data
directory, its own ports, and its own compose project name. The `.env` is never in the
repository and never rsynced: the deploy excludes it.

**The `.env` is `${DEPLOY_PATH}/.env`**, at the root of the environment's checkout and
two levels above the compose file. The deploy passes `--env-file "${DEPLOY_PATH}/.env"`
explicitly, which replaces compose's default lookup beside the compose file: a `.env`
in `projects/<name>/` on the server is not read. Write that same location in the
project's `.env.example` and `AGENTS.md`, and put every variable the compose file
requires there, the data directory included. Give the data directory no default in the
compose file (`${<NAME>_DATA_DIR:?}`): a `.env` that forgets it then fails the stack,
where a relative default brings Postgres up on an empty directory with the health check
answering 200 and the real rows unmounted. A build interpolates the whole file, so the
image jobs in `ci.yml` and `preflight.json` pass a throwaway value for it.

Two environments on one host must share nothing but the host, which for PigroCRM means
separate databases, separate secrets, and no production Google credentials in preview.

### Taking something over from another project

When a project starts serving what another one served, the order is not a preference.
**The new deployable goes up and takes the name first; only then does the old one stop
building it.** Landing them the other way round leaves a window where the name points
at a container that no longer has the pages, and on this repository that window was not
theoretical: while production was still deployed from `main` by hand, a merge was
effectively a release whatever the tag policy said. It cost twenty minutes of a
redirecting joinorbiters.com on 2026-09-09 (REB-16). Since that afternoon nothing is
deployed by hand (`docs/design/DECISIONS.md`, 2026-09-09): production moves only on a
tag, so the order above is what makes the tag safe to push.

The reverse direction is free: a new container that nobody points at yet can be
deployed, curled and left running for as long as you like.

### If the project answers on a public name

The host's nginx vhost belongs to the project, in `projects/<name>/deploy/`, and it
decides only what is not the project's container: everything else proxies to it and the
container owns its own path map. Every stack publishes on the loopback and never on
`0.0.0.0`: the host's nginx is what faces the internet, and publishing wider walks past
the firewall. One host carries every environment of every project, so the ports are
allocated here and nowhere else; a new project takes the next free ones and adds its
rows.

| Project | Production | Preview |
|---|---|---|
| PigroCRM | web 8080, Postgres 55432 | web 8081, Postgres 55434 |
| website | web 8082 | web 8083 |
| hub (`rebase`, `rebase-preview`) | api 8084, web 8085, mcp 8088, Postgres 55435 | api 8086, web 8087, mcp 8089, Postgres 55436 |

Since 2026-09-10 preview has public names too: `preview.letsrebase.com` mirrors the
website plus hub map, `preview.pigro.letsrebase.com` mirrors the CRM's. So a new
project's preview gets a vhost as well as a production one, the two files stay the same
shape, and only the ports differ.

**A preview name is open, and nothing on it may be a secret.** It was briefly behind
HTTP basic auth on 2026-09-10; Lorenzo's call the same day removed it, because a
password nobody asked for is one more credential to hand around for a surface whose
whole purpose is being easy to look at. What keeps it safe is narrower and does not
depend on anyone remembering a password: `X-Robots-Tag: noindex, nofollow, noarchive`
on every answer, so the copy of a public site does not become duplicate content in
search; a preview name only ever proxies preview containers, so a click inside preview
cannot walk out into production data; and preview's own database and secrets are its
own. The consequence for a new project: if its preview would expose something that must
not be read by whoever finds the URL, that is a reason to keep the surface off preview,
not a reason to put a password back. `projects/website/deploy/check-unindexable.sh`
(preflight `check-unindexable`) is what proves this stays true against the live
names rather than only against the vhost files that promise it (REB-108).

TLS on the preview names is a **certificate of their own**, `preview.letsrebase.com`,
covering both of them, rather than two more names on the production certificate. The
reason is blast radius: `certbot --nginx --expand` reinstalls the certificate into every
vhost whose `server_name` it matches, which means it rewrites the two production files
to add a preview name, and those are the files that are edited in place. A separate
certificate touches only the two preview vhosts and renews on its own.

The copy in the repository is plain HTTP and is the source of truth for what the rules
are. The copy in `/etc/nginx/sites-available/` has certbot's port-443 block on top of
it: **edit that one in place**, with `nginx -t` before the reload. Overwriting it from
the repository takes TLS away on the spot.

### What PostHog's warehouse reads

Since 2026-09-12 (REB-187) PostHog's data warehouse reads the hub's production database
(`rebase` on 55435: `signups`, `freelancers`, `companies`, `guide_downloads`, `logins`,
`comments`) and the CRM's production registry (`pigrocrm_tenants` on 55432: `tenants`),
so the events the surfaces send can be joined to the rows behind them. `logins` was
`member_logins` until the identity merge renamed it (REB-281, migration 0012); the grant
followed the table, the sync did not, and PostHog paused it with «something this sync
depends on no longer exists». The preview databases (55434, 55436) are not connected.
The design is `docs/design/2026-09-12-posthog-analytics-design.md`. Both Postgres answer on the
loopback only, so PostHog reaches them through an SSH tunnel, and the arrangement on the
server is:

- a user `posthog`, shell `/usr/sbin/nologin`, whose one `authorized_keys` line is the
  public half of a key pair generated for this purpose (the private half is stored in
  PostHog's source configuration and nowhere else), restricted to the two forwards:

  ```
  restrict,port-forwarding,permitopen="127.0.0.1:55435",permitopen="127.0.0.1:55432" ssh-ed25519 ...
  ```

- `/etc/ssh/sshd_config.d/60-posthog-tunnel.conf`, a `Match User posthog` block with
  `AllowTcpForwarding yes`, `PermitOpen` on the same two addresses, `PermitTTY no`,
  `X11Forwarding no`, `AllowAgentForwarding no`, `PasswordAuthentication no`,
  `ForceCommand /bin/false`. A `Match` block runs to the end of its file, so nothing
  else goes in that drop-in. A shell attempt answers nologin's «This account is
  currently not available»; a forward to either port works and to anything else does
  not. `sshd -t` before the reload, and `sshd -T -C user=posthog` prints the effective
  `permitopen` for that user;
- a role `posthog_ro` in each database, `LOGIN` with its own password, `CONNECT` on the
  database, `USAGE` on `public` and `SELECT` on the tables above and nothing else. The
  sessions, the admin users and the magic-link tokens are not granted.

In PostHog the two sources are Postgres sources with prefixes `hub` and `pigro`, host
`127.0.0.1` and the container's host port, the SSH tunnel enabled towards the server's
address with key-pair auth as `posthog`, and **«Require TLS through tunnel» off**: the
tunnel is the encryption, the containers speak plain Postgres, and with the switch on
every attempt answers «Your database doesn't support the encrypted connection PostHog
requires». Through the API the switch is `ssh_tunnel.require_tls: {"enabled": false}`
in the source payload, which the wizard shows and the API reference does not name.

Keeping it working:

- **A new table** PostHog should see needs its own `GRANT SELECT` (the grants are per
  table, a migration does not extend them) and then a schema refresh on the source.
- **A renamed table or column breaks a sync**, and this is the one that has already
  happened twice: the `orbiters` → `rebase` database rename on 2026-09-15, and
  `member_logins` → `logins` on 2026-09-21. `test_warehouse_contract.py` in the hub's
  core now fails on the second kind, on the pull request, instead of leaving it to an
  email nine days later. A rename carries the grant with it, so nothing here refuses PostHog; the
  sync simply names an object that no longer exists and is paused until somebody
  re-points it, which is a schema refresh on the source plus the sync enabled on the new
  name. The old sync's rows stay in PostHog under the old name: delete them there, or
  two tables claim to be the same thing. Same for a column dropped from a synced table.
- **A column moved out of a synced table takes its data out of the warehouse**, without
  failing anything. The identity merge moved `nome`, `cognome`, `email` and
  `linkedin_url` from `freelancers` and `companies` into `users`, which is deliberately
  not granted: those syncs keep running and arrive without the identity they used to
  carry, so a dashboard that joins a person to their card is empty rather than broken.
  Granting `users` is a decision about what a third party holds, not a repair.
- **A source syncs every column of a table unless somebody says otherwise**, which for
  `freelancers` means `cv_bytes`: the CV itself, up to five megabytes of PDF per person.
  The column picker is behind «Columns» beside the table in the source's table list. The
  grant is per table here, so the database does not stop it; on 2026-09-21
  `pg_statio_user_tables` showed 384,866 TOAST block reads against 6,972 heap reads on a
  table of 14 MB, which is what a repeated full-table read of the binaries looks like.
  Unchecking the column is the fix. **Not** a column-level grant: a sync reads whole
  rows, so revoking one column fails the whole table. What keeps it that way in the
  repository is `projects/hub/packages/core/tests/test_warehouse_contract.py`, which
  fails when a synced table grows another binary column; what keeps it that way in
  production, if the answer is ever «not even once», is a view without the column, as
  the credential rule above already prescribes.
- **A new project's database**: a `permitopen` for its port on the key line and in the
  `Match` block, `sshd -t`, `systemctl reload ssh`; a `posthog_ro` role with `SELECT` on
  the tables that matter; a source with the prefix the project is called.
- **Rotating the key**: generate a new pair, replace the one `authorized_keys` line,
  put the new private half in each source's SSH settings, and test both forwards.
- **Rotating a role password**: `ALTER ROLE posthog_ro PASSWORD '...'` in that
  database, then the new password on the source; no restart anywhere.

Per-space CRM databases are deliberately not connected: one source per space does not
scale, and the activation funnel is read from events.

## 8. Documentation

`projects/<name>/README.md` and `projects/<name>/AGENTS.md`, plus a row in the
project table in the root `README.md`.

## 9. Its place in Linear

Linear is the tracker and `docs/tracker.md` is the contract: initiatives, projects,
milestones, statuses, the two label groups and the loop an issue goes through are all
defined there, checked against the board, and this page does not carry a copy of them
because a copy drifts, which is exactly what an earlier version of this section did
(REB-45). What a new monorepo project needs is an initiative of its own, named as the
directory reads to people (`PigroCRM`, not `pigrocrm`) and created by hand in the Linear
UI since the MCP surface cannot create one, and a first project under it with a scope
that can actually close, a lead and both members (the members in the UI too, since
`save_project` has no field for them), plus its row in the project table of
`docs/tracker.md` § Where things are in the same change: the initiative is the permanent
container, the project is the release. Repository-wide work that belongs to no product
(CI cost, the licence, this documentation) goes under the `Monorepo` initiative; it went
into `Monorepo hygiene v1`, which sat under no `projects/<name>/` and is closed, and
where such an issue goes now is that same section of the tracker page. The `Area`
group is shared by everyone: if the new project needs an `area:*` child the board does
not have, add it under the group rather than inventing a parallel scheme, and record it
in `docs/tracker.md` in the same change.
