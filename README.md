# rebase

The monorepo for rebase's products.

## Projects

| Project | What it is | Stack |
|---|---|---|
| [`projects/pigrocrm`](projects/pigrocrm) | PigroCRM, an AI-first CRM for Italian freelancers, consultants and small startups. Everything the web UI can do is also reachable over REST and over MCP. | Python · FastAPI · SQLAlchemy · PostgreSQL · React · Vite |
| [`projects/website`](projects/website) | letsrebase.com, the public site: the rebase community page, the pages the product signs itself with, and the signup form. Static pages, deliberately no framework. | HTML · CSS · Vite |
| [`projects/hub`](projects/hub/README.md) | The rebase hub, at letsrebase.com/hub: the freelancer and company signup wizards, and the admin area that reads them. | Python · FastAPI · SQLAlchemy · PostgreSQL · React · Vite |
| [`shared/brand`](shared/brand) | The palette, the typeface and the four-tile mark, read by both surfaces so there is one source and no copy. | CSS |

## Getting started

Requires Python 3.13, Node 22 and Docker. [uv](https://docs.astral.sh/uv/) and
[pnpm](https://pnpm.io/) manage the two toolchains; pnpm's version is pinned in
`package.json` and corepack will honour it.

```
uv sync --frozen            # every Python package, one virtualenv
pnpm install --frozen-lockfile
```

On Nix, `nix develop` (or `direnv allow`, the `.envrc` is committed) gives a shell
with all of that except Docker, which stays the host's: Python 3.13 and Node 22 as
the repository declares them, and Pandoc and Typst at the exact versions
`projects/pigrocrm/Dockerfile.api` pins, since the document renderer is verified
against those and no other. Playwright's browsers are not in the shell, so the two
e2e checks in `preflight.json` need the host's own browsers or a `nix-ld` setup.

Then follow the project you want to work on: for PigroCRM, its
[README](projects/pigrocrm/README.md) covers running it and deploying it.

## How it is laid out

```
projects/<name>/    one project: its apps, packages, docs, Dockerfiles, deploy
shared/<name>/      code and assets used by more than one project
tooling/            configuration shared by every project
docs/               documentation about the monorepo itself
```

There is exactly one `uv.lock` and exactly one `pnpm-lock.yaml`, both at the root, so
that two projects cannot resolve the same library at two versions.
[`docs/architecture.md`](docs/architecture.md) explains the layout and the tradeoffs
it makes; [`docs/adding-a-project.md`](docs/adding-a-project.md) is the runbook for
adding the next one.

## Self-hosting on NixOS

`flake.nix` also builds every deployable as a package from the same two locks
(`pigrocrm-api`, `pigrocrm-web`, `hub-api`, `hub-web`, `website`) and ships one NixOS
module per product: `services.pigrocrm`, `services.rebase-hub`,
`services.rebase-website`. Each restates its compose file and its `deploy/` nginx
configuration in NixOS terms: a local PostgreSQL reached over the socket, the
migration (and, for the CRM, `pigrocrm ensure-space-defaults`) before the API starts,
the MCP server as a second unit behind its `/mcp` locations, nginx serving the SPA and
proxying the API with the security headers the production vhost carries. Secrets never
go in the store: `services.pigrocrm.environmentFile` is a file of `PIGROCRM_*=value`
lines and must define `PIGROCRM_JWT_SECRET`.

```nix
{
  inputs.rebase.url = "github:letsrebase/rebase";
  # ...
  imports = [ rebase.nixosModules.pigrocrm ];
  services.pigrocrm = {
    enable = true;
    domain = "crm.example.com";
    environmentFile = "/run/secrets/pigrocrm.env";
    settings.timezone = "Europe/Rome";
  };
}
```

Each module is booted in a VM by `nix flake check` and probed through nginx, which is
the evidence the copy has not drifted from the compose stack. The flake declares
`x86_64-linux` and `aarch64-linux` (the renderer binaries are the upstream Linux
release tarballs), and only the former is exercised by the VM tests; from a Mac with
a Linux builder in `nix.conf`, name the system
(`nix build .#packages.x86_64-linux.pigrocrm-api`). These modules are how a third
party runs the software; rebase's own environments are deployed by CI from the
compose files and never from here (`docs/design/DECISIONS.md`, 2026-09-09).

## Contributing

[`CONTRIBUTING.md`](CONTRIBUTING.md) for the workflow and
[`AGENTS.md`](AGENTS.md) for the conventions, which apply to people and to coding
agents alike.

## Licence

[GNU AGPL v3](LICENSE), chosen on 2026-09-10 when this repository went public.
Self-hosting is free; anyone who runs a modified version as a network service has to
offer that version's source to its users. Copyright stays with the authors.
