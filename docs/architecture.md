# How this monorepo is laid out, and what that costs

Written when PigroCRM's repository became the monorepo, so that the next person does
not have to reverse-engineer the reasoning from the directory names. The repository's
own name on GitHub changed twice. It left `pigrocrm` on 2026-09-09, for the same
reason: it holds more than one project now. It became `rebase` under the `letsrebase`
org on 2026-09-15, when the rename of the brand reached GitHub (REB-204).

## The shape

```
projects/<name>/     one project owns everything under here
shared/<name>/       code or assets used by more than one project (today: brand)
tooling/<name>/      configuration shared by every project
docs/                this directory: about the monorepo, never about a project
```

## Why a directory per project, rather than one flat apps/ and packages/

With several projects on different stacks, a directory per project buys three things
that prefixed names in a flat tree do not:

- **CI path filters are trivial**: `projects/pigrocrm/**` is the whole answer, and a
  filter that is trivial stays correct.
- **A clear ownership boundary** when two people work in parallel. A conflict is then
  a real disagreement rather than an accident of layout.
- **A project can be extracted later** into a repository of its own without
  archaeology.

The cost is one more level of nesting, and a build context that is bigger than the
project (see below).

## Why a project's own docs live inside the project

`projects/pigrocrm/packages/core/tests/test_gmail_message_id_contract.py` reads
`docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md` through
`Path(__file__).parents[3]` and asserts on its Status line. That is not an accident
to work around: it is a test holding a written verification to its word. Keeping the
project's documentation inside the project is what makes that reference (and every
other path inside the project) keep working when the project moves.

This was the property that made the migration safe. Almost every relative path in
PigroCRM points *within* the project: `parents[3]` in the tests, `REPO_ROOT` in the
e2e scripts, `extend = "../../ruff.toml"`, the alembic.ini resolved from
`pigrocrm.core.__file__`. Moving the project as one block left all of them valid, and
the entire move landed as 861 renames with not one line of application code changed.

## One lockfile per language, at the root

This is the reason the projects share a repository at all. `uv.lock` and
`pnpm-lock.yaml` live at the top; `pnpm-workspace.yaml` carries a `catalog:` that
pins the build-and-test toolchain once for everybody.

**What it costs, stated plainly:**

- **One version of a library for every project.** That is the deduplication, and it
  is also the constraint. A project that needs an incompatible pin has to leave the
  workspace with a lock of its own: an exception with a written reason, not a
  default.
- **A Docker build context is the whole repository**, because the lockfile an image
  is pinned by lives at the root. `.dockerignore` at the root is what keeps that
  cheap, and it is load-bearing rather than a nicety.
- **A per-project image has to name the manifests it copies.** Adding a Python
  package to the workspace makes every existing image build fail with "Workspace
  member ... is missing a `pyproject.toml`", naming it. That is deliberate: the
  alternative is copying the whole tree before resolving, which silently gives up the
  dependency layer cache. Loud beats slow.
- **uv cannot glob the members.** `projects/*/apps/*` matches `apps/web`, a Vite app
  with no `pyproject.toml`, and uv refuses to start on a member without one. The
  members are therefore listed one by one. pnpm has no such problem and globs.

## Where tool configuration lives, and why it is not per project

`pytest`'s `testpaths` and `mypy`'s `files` are at the root and name every project's
paths in full. Both resolve relative to the working directory, not to the file they
are written in, so a per-project config file only does what it looks like it does
when you happen to have `cd`'d into that project, and silently checks nothing when
you have not. Everything runs from the root; CI narrows to one project by passing
that project's paths as arguments, which override both lists.

`ruff` is genuinely hierarchical (it resolves the nearest config for each file), so
it is the one tool that does get a per-project file, and the two nested
`apps/*/ruff.toml` that enforce PigroCRM's core-must-not-import-an-adapter rule keep
working untouched.

## Where it runs: two servers

rebase has exactly two servers (Lorenzo, 2026-10-06, `docs/design/DECISIONS.md`). Every
other page in this repository that says "the host" or "the server" means the first one.

**The origin, on Hetzner.** One machine runs the website, the hub, PigroCRM and
Documenso. The website, the hub and PigroCRM each have a production stack and a preview
stack beside it; Documenso has a production instance only, which the hub's preview signs
on. Vaultwarden is to join them behind the same nginx (REB-644, REB-648, not live yet).
Every environment is a Docker Compose project in its own `DEPLOY_PATH` directory with its
own `.env` (Documenso runs inside the hub's production project), and publishes on the
loopback only (the port table is in `docs/adding-a-project.md` § 7). The machine's
own nginx is the only thing that faces the internet: it terminates TLS with Let's Encrypt
certificates that certbot renews, and proxies by name and by path to those containers,
using the vhosts committed under each project's `deploy/` directory. The `letsrebase.com`
names (`letsrebase.com`, `pigro`, `preview`, `preview.pigro`, `firma`) are DNS-only
records pointing at it (`infra/cloudflare/rebase.tf`); the old zone's records point at
it too, to answer with redirects (`docs/migrations/2026-09-14-letsrebase.md`). A deploy
reaches it over SSH with its host key pinned, and `DEPLOY_HOST` names it in every GitHub
Environment, since one host carries every environment of every project
(`docs/adding-a-project.md` § 7).

**The CI/CD server.** A second machine, separate from the origin, runs the self-hosted
GitHub Actions runner pool: three runners, `ci-runner-1` to `ci-runner-3`, in the org's
`private-clients` runner group (`docs/ci-runner-pool.md`). Today that group holds the
private client repositories, and this repository is not in it: `letsrebase/rebase` is
public, so every job of its own workflows runs on GitHub-hosted `ubuntu-latest`, and its
deploy jobs reach the origin over SSH from there.

Everything else the platform touches is a hosted service rather than a server of ours:
GitHub, Cloudflare (DNS only), Resend (mail) and PostHog (analytics).
