{
  description = "rebase monorepo: development shell, packages, NixOS modules and their tests";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    flake-parts.inputs.nixpkgs-lib.follows = "nixpkgs";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{
      self,
      flake-parts,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      # The two renderer binaries below are the upstream Linux release tarballs, one
      # per architecture in `rendererReleases`, so these are the systems the shell,
      # the packages and the modules exist for. A Mac is not among them: from one,
      # name the system and let a Linux builder in nix.conf do the work
      # (`nix build .#packages.x86_64-linux.pigrocrm-api`, as preflight.json does).
      # Only x86_64-linux is exercised by the VM tests today.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      perSystem =
        {
          pkgs,
          lib,
          ...
        }:
        let
          # One capture group out of a file, with the file named in the error when the
          # pin moved to a shape this regex does not read.
          readPin =
            file: regex:
            let
              m = builtins.match regex (builtins.readFile file);
            in
            if m == null then throw "flake.nix: no version found in ${toString file}" else lib.head m;

          # `.python-version` holds "3.13"; the attribute is `python313`.
          pythonVersion = readPin ./.python-version "([0-9]+\\.[0-9]+)[[:space:]]*";
          python = pkgs."python${lib.replaceStrings [ "." ] [ "" ] pythonVersion}";

          # `engines.node` is ">=22.13"; the attribute is `nodejs_22`.
          nodeMajor = lib.head (
            builtins.match ">=([0-9]+)(\\.[0-9]+)*" (builtins.fromJSON (builtins.readFile ./package.json)).engines.node
          );
          nodejs = pkgs."nodejs_${nodeMajor}";

          # PigroCRM's document renderer is verified against exactly the pair the
          # Dockerfile installs (and `_python-gate.yml` repeats): Typst's diagnostic
          # format and the auto-typography workaround were both confirmed against
          # 0.14.2 specifically. nixpkgs carries 0.15.1 and pandoc 3.7.0.2 today, so
          # `pkgs.typst` and `pkgs.pandoc` would hand a developer a renderer that
          # proves less than nothing. These are the same release tarballs the
          # Dockerfile downloads, and both binaries are statically linked, so they run
          # unpatched.
          dockerfileArg = name: readPin ./projects/pigrocrm/Dockerfile.api ".*\nARG ${name}=([^\n]+)\n.*";
          pandocVersion = dockerfileArg "PANDOC_VERSION";
          typstVersion = dockerfileArg "TYPST_VERSION";

          # The release asset per system, and its hash: the one place a new system
          # is added. A version bump in the Dockerfile moves every hash here.
          rendererReleases = {
            x86_64-linux = {
              pandocAsset = "linux-amd64.tar.gz";
              pandocHash = "sha256-s2KBXiHYrTYpwSSqkrr1RVjaCGrXI3S09v3Ze58ydbA=";
              typstTriple = "x86_64-unknown-linux-musl";
              typstHash = "sha256-pgRMutKpVN65IRZ+JX4SCsChayAznsARIRlP+dOUmW0=";
            };
            aarch64-linux = {
              pandocAsset = "linux-arm64.tar.gz";
              pandocHash = "sha256-hS6JjCSQ+oQK51qLavimydbWO3fvFwwy7DoXlYRk2Sk=";
              typstTriple = "aarch64-unknown-linux-musl";
              typstHash = "sha256-SRsQGqQKOn6oKj+KYjLKu05qfiM4EAguWsgS1D/c1Ho=";
            };
          };
          release = rendererReleases.${pkgs.stdenv.hostPlatform.system};

          pandoc = pkgs.stdenvNoCC.mkDerivation {
            pname = "pandoc-bin";
            version = pandocVersion;
            src = pkgs.fetchurl {
              url = "https://github.com/jgm/pandoc/releases/download/${pandocVersion}/pandoc-${pandocVersion}-${release.pandocAsset}";
              hash = release.pandocHash;
            };
            dontBuild = true;
            installPhase = ''
              install -Dm755 bin/pandoc "$out/bin/pandoc"
            '';
            doInstallCheck = true;
            installCheckPhase = ''
              "$out/bin/pandoc" --version | head -1 | grep -Fx "pandoc ${pandocVersion}"
            '';
          };

          typst = pkgs.stdenvNoCC.mkDerivation {
            pname = "typst-bin";
            version = typstVersion;
            src = pkgs.fetchurl {
              url = "https://github.com/typst/typst/releases/download/v${typstVersion}/typst-${release.typstTriple}.tar.xz";
              hash = release.typstHash;
            };
            dontBuild = true;
            installPhase = ''
              install -Dm755 typst "$out/bin/typst"
            '';
            doInstallCheck = true;
            installCheckPhase = ''
              "$out/bin/typst" --version | grep -F "typst ${typstVersion} "
            '';
          };

          # ---- Python: the uv workspace, built from uv.lock -------------------------
          #
          # `sourcePreference = "wheel"`: the same artefacts uv installs into the
          # Docker images, patched for the Nix store rather than rebuilt from sdists.
          # A member of the workspace is a package of this set like any other, so a
          # virtualenv is asked for by the member names an image would `--package`.
          workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
          pythonSet = (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
            ]
          );

          # A Python deployable: the virtualenv of the named workspace members, plus
          # `share/<name>/` holding the Alembic configuration and migrations, which
          # live beside the package in the checkout and inside the images
          # (`packages/core/alembic.ini`, `%(here)s/migrations`) and are not part of
          # any wheel. A module migrates by running `alembic upgrade head` from that
          # directory, which is what both Dockerfiles do on start-up.
          pythonApp =
            {
              name,
              members,
              core,
            }:
            pkgs.symlinkJoin {
              inherit name;
              # The migrations first, the venv second: `symlinkJoin` runs `lndir` once
              # per path, in order, and a member whose closure ships a `share` of its
              # own (`hub-api-env`'s does, by way of fontTools) hands `mkVirtualEnv` a
              # `$out/share` that is a plain symlink, not a directory. `lndir` cannot
              # descend into a symlink to add `share/${name}` beside it and silently
              # drops the whole thing, no warning short of `-silent`'s own "is a link
              # instead of a directory" line, which nobody reads on a green build; this
              # order lets the migrations claim `$out/share` as a real directory first,
              # so the venv's vestigial symlink is what gets skipped instead (REB-403,
              # found while making `hub-api` also carry pandoc, Typst and the brand).
              paths = [
                (pkgs.runCommand "${name}-migrations" { } ''
                  mkdir -p "$out/share/${name}"
                  cp -r ${core}/alembic.ini ${core}/migrations "$out/share/${name}/"
                '')
                (pythonSet.mkVirtualEnv "${name}-env" (lib.genAttrs members (_: [ ])))
              ];
            };

          # ---- Node: the pnpm workspace, built from pnpm-lock.yaml -----------------
          #
          # One dependency store for the whole workspace, fetched once and shared by
          # every Vite build below; its input is exactly what `pnpm install` reads
          # (the three workspace files and every package.json), so a source change
          # does not refetch it. `hash` is the one value in this file that has to be
          # updated by hand: every change to pnpm-lock.yaml changes it, the build
          # fails naming the hash it got, and that hash goes here.
          pnpmDeps = pkgs.fetchPnpmDeps {
            pname = "rebase-pnpm-deps";
            version = "0";
            fetcherVersion = 4;
            src = lib.fileset.toSource {
              root = ./.;
              fileset = lib.fileset.unions (
                [
                  ./package.json
                  ./pnpm-workspace.yaml
                  ./pnpm-lock.yaml
                  (lib.fileset.fileFilter (f: f.name == "package.json") ./projects)
                  (lib.fileset.fileFilter (f: f.name == "package.json") ./shared)
                ]
                # pnpm-workspace.yaml names `tooling/*` too; empty today, and a
                # fileset of a directory that does not exist is an error.
                ++ lib.optional (builtins.pathExists ./tooling) (
                  lib.fileset.fileFilter (f: f.name == "package.json") ./tooling
                )
              );
            };
            hash = "sha256-zJRUfQgklA+dikh6JQ0M+hbd/EpLlIFxr0Tj5NaZO1Q=";
          };

          # A Vite deployable: the `dist/` of one workspace package, built the way its
          # Dockerfile builds it (`pnpm install --filter <name>...`, then `pnpm --filter
          # <name> build`) from the same files that Dockerfile copies: the package's
          # own directory and the `shared/` libraries its Dockerfile lists, no more, so
          # a change to a library one app does not use does not rebuild it.
          viteApp =
            {
              name,
              dir,
              shared,
            }:
            let
              manifest = builtins.fromJSON (builtins.readFile (dir + "/package.json"));
            in
            pkgs.stdenv.mkDerivation {
              pname = manifest.name;
              inherit (manifest) version;
              src = lib.fileset.toSource {
                root = ./.;
                fileset = lib.fileset.unions (
                  [
                    ./package.json
                    ./pnpm-workspace.yaml
                    ./pnpm-lock.yaml
                    dir
                  ]
                  ++ shared
                );
              };
              nativeBuildInputs = [
                nodejs
                pkgs.pnpm
                pkgs.pnpmConfigHook
              ];
              inherit pnpmDeps;
              pnpmWorkspaces = [ "${name}..." ];
              buildPhase = ''
                runHook preBuild
                pnpm --filter ${name} build
                runHook postBuild
              '';
              installPhase = ''
                runHook preInstall
                cp -r ${lib.path.removePrefix ./. dir}/dist "$out"
                runHook postInstall
              '';
            };
          # What the two applications' Dockerfile.web copy beside the app itself.
          appShared = [
            ./shared/brand
            ./shared/analytics
            ./shared/ui
          ];
        in
        {
          packages = {
            pigrocrm-api = pythonApp {
              name = "pigrocrm-api";
              # The MCP server ships inside the API's environment, as it does in the
              # image: the compose `mcp` service runs `pigrocrm_mcp.http:app` from the
              # same build.
              members = [
                "pigrocrm-api"
                "pigrocrm-mcp"
              ];
              core = ./projects/pigrocrm/packages/core;
            };
            # `rebase_core.contracts.render` shells out to bare `pandoc` and `typst`,
            # and `.brand` reads the palette and the typeface at the repository's own
            # paths (`REPO = parents[7]`), which a store venv does not have. Both
            # binaries and a copy of the two files go into this derivation, the files
            # at `share/hub-api/brand`; `postBuild` then rewraps `bin/rebase` (the CLI:
            # `contracts-check`, `contracts-sweep`) and `bin/uvicorn` (both
            # `rebase_api.main:app` and `rebase_mcp.http:app` run from it, one venv for
            # both) with `makeWrapper`, so every entry point that can render a contract
            # finds `pandoc`, `typst` and the brand on its own, with nothing set by a
            # caller (REB-403).
            hub-api = pkgs.symlinkJoin {
              name = "hub-api";
              paths = [
                (pythonApp {
                  name = "hub-api";
                  members = [
                    "rebase-api"
                    "rebase-mcp"
                  ];
                  core = ./projects/hub/packages/core;
                })
                pandoc
                typst
                (pkgs.runCommand "hub-api-brand" { } ''
                  install -Dm444 ${./shared/brand/palette.css} \
                    "$out/share/hub-api/brand/palette.css"
                  install -Dm444 ${./shared/brand/fonts/outfit-variable-latin.woff2} \
                    "$out/share/hub-api/brand/fonts/outfit-variable-latin.woff2"
                '')
              ];
              nativeBuildInputs = [ pkgs.makeWrapper ];
              postBuild = ''
                for exe in rebase uvicorn; do
                  wrapProgram "$out/bin/$exe" \
                    --set-default REBASE_CONTRACTS_BRAND_DIR "$out/share/hub-api/brand" \
                    --prefix PATH : "$out/bin"
                done
              '';
            };
            pigrocrm-web = viteApp {
              name = "web";
              dir = ./projects/pigrocrm/apps/web;
              shared = appShared;
            };
            hub-web = viteApp {
              name = "hub";
              dir = ./projects/hub/apps/web;
              shared = appShared;
            };
            website = viteApp {
              name = "website";
              dir = ./projects/website;
              shared = [ ./shared/brand ];
            };
            # The renderer pair, exported so the PigroCRM module can hand the API the
            # same binaries the image and the shell carry.
            inherit pandoc typst;
          };

          # ---- The modules, booted -------------------------------------------------
          #
          # One VM per module, asserting what a browser or a probe would see through
          # nginx: the SPA shell on a deep link, the API behind its prefix, the health
          # probe, and, where there is a database, that the migration ran. `nix flake
          # check` runs all three; `.github/preflight.json` runs them on a diff that
          # can break them. They need KVM.
          checks = {
            pigrocrm = pkgs.testers.runNixOSTest {
              name = "pigrocrm";
              nodes.machine = {
                imports = [ self.nixosModules.pigrocrm ];
                services.pigrocrm = {
                  enable = true;
                  domain = "pigrocrm.test";
                  # One of each type, so a bool that rendered as `False` (which pydantic
                  # refuses) would fail the boot here rather than on somebody's host.
                  settings = {
                    timezone = "Europe/Rome";
                    mcp_full_access = false;
                    solleciti_grace_days = 7;
                  };
                  # A throwaway secret for a throwaway VM; in the store on purpose,
                  # where a real one must never be.
                  environmentFile = pkgs.writeText "pigrocrm-test.env" ''
                    PIGROCRM_JWT_SECRET=test-only-secret-long-enough-for-the-validator
                  '';
                };
              };
              testScript = ''
                machine.wait_for_unit("pigrocrm-api.service")
                machine.wait_for_open_port(8000)
                machine.wait_for_unit("pigrocrm-mcp.service")
                machine.wait_for_open_port(8001)
                machine.wait_for_unit("nginx.service")

                # The probe, through nginx, exact path (spa.conf: not under /api/).
                machine.succeed("curl -fsS http://localhost/health | grep -F '\"status\":\"ok\"'")
                # The SPA shell, on its prefix and on a deep link a refresh would hit.
                machine.succeed("curl -fsS http://localhost/app/ | grep -F '/app/assets/'")
                machine.succeed("curl -fsS http://localhost/app/clienti/some-uuid | grep -F '/app/assets/'")
                machine.succeed("curl -fsS http://localhost/app/mark.svg >/dev/null")
                # The magic link: the shell on /app/verify, the old /app/entra sent
                # there with its query string, on the root and under a space, and the
                # token in none of nginx's log lines.
                machine.succeed("curl -fsS 'http://localhost/app/verify?t=secret-token' | grep -F '/app/assets/'")
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/app/entra?t=secret-token' | grep -Fx '301 http://localhost/app/verify?t=secret-token'")
                machine.succeed("curl -fsS 'http://localhost/studiorossi/app/verify?t=secret-token' | grep -F '/app/assets/'")
                machine.succeed("curl -fsS 'http://localhost/app/invite?t=secret-token' | grep -F '/app/assets/'")
                machine.succeed("curl -fsS 'http://localhost/studiorossi/app/invite?t=secret-token' | grep -F '/app/assets/'")
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/studiorossi/app/entra?t=secret-token' | grep -Fx '301 http://localhost/studiorossi/app/verify?t=secret-token'")
                machine.succeed("curl -fsS http://localhost/app/ >/dev/null && grep -c 'GET /app/ ' /var/log/nginx/access.log")
                machine.fail("grep -r 'secret-token' /var/log/nginx/")
                # The bare root goes into the application, with a relative Location.
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost/ | grep -Fx '302 http://localhost/app/'")
                # A space's own prefix reaches the same shell and the same API.
                machine.succeed("curl -fsS http://localhost/studiorossi/app/ | grep -F '/app/assets/'")
                machine.succeed("curl -fsS http://localhost/studiorossi/health | grep -F '\"status\":\"ok\"'")
                # The API answers behind /api/: an unauthenticated request is refused
                # by the application, not by nginx.
                machine.succeed("curl -sS -o /dev/null -w '%{http_code}' http://localhost/api/auth/me | grep -Ex '401'")
                # The MCP server is its own process on its own port, reached at /mcp
                # and under a space's prefix (spa.conf, ORB-170), and it is the one
                # answering: a call with no bearer is refused with the challenge, and
                # a space nobody registered is «spazio non trovato», the API's own
                # wording, never nginx's HTML.
                machine.succeed("curl -sS -o /dev/null -D - -X POST http://localhost/mcp | grep -i '^www-authenticate: Bearer'")
                machine.succeed("curl -sS -X POST http://localhost/studiorossi/mcp | grep -F 'spazio non trovato'")
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost/mcp/ | grep -Fx '308 http://localhost/mcp'")
                # A document upload may weigh 105 MB (spa.conf); anything else 1 MB.
                machine.succeed("head -c 2000000 /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST --data-binary @- http://localhost/api/documents/from-template | grep -Ex '401'")
                machine.succeed("head -c 2000000 /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST --data-binary @- http://localhost/api/auth/me | grep -Ex '413'")
                # Nothing else is served: no SPA fallback outside /app/.
                machine.succeed("curl -sS -o /dev/null -w '%{http_code}' http://localhost/nothing/here | grep -Ex '404'")
                # The headers the host's vhost carries in production (security-headers.conf).
                machine.succeed("curl -sSI http://localhost/app/ | grep -ic '^referrer-policy: strict-origin' | grep -Fx 1")
                machine.succeed("curl -sSI http://localhost/app/ | grep -i '^x-content-type-options: nosniff'")
                # The migration ran against the local database before the API started.
                machine.succeed("su postgres -s /bin/sh -c \"psql -d pigrocrm -tAc 'select version_num from alembic_version'\" | grep -E '.'")
                # A space is a database the API creates: the role may.
                machine.succeed("su postgres -s /bin/sh -c \"psql -tAc \\\"select rolcreatedb from pg_roles where rolname = 'pigrocrm'\\\"\" | grep -Fx 't'")
                # The renderer the unit was pointed at is the pinned pair, and runs as
                # the unit's own user under the same sandbox flags.
                for var, expected in (("PIGROCRM_TYPST_BINARY", "typst ${typstVersion} "), ("PIGROCRM_PANDOC_BINARY", "pandoc ${pandocVersion}")):
                    binary = machine.succeed(f"systemctl show -p Environment pigrocrm-api | grep -oE '{var}=[^ ]+' | cut -d= -f2").strip()
                    out = machine.succeed(f"systemd-run --wait --pipe --uid=pigrocrm -p ProtectSystem=strict -p PrivateDevices=true {binary} --version")
                    assert expected in out, f"{var}: {out!r}"
              '';
            };

            # The hub beside the website on one name, which is letsrebase.com's own
            # layout: two modules adding locations to the same virtual host.
            hub =
              let
                # A minimal document the sweep has work on (REB-403, Greptile's review
                # of the first cut): one user, one freelancer, one framework agreement
                # `inviato` with an envelope, every NOT NULL column filled, every check
                # constraint satisfied (`ck_contract_documents_envelope_item` wants
                # `documenso_item_id` set alongside `documenso_id`; `kind = 'quadro'`
                # wants `match_id`/`numero` both null). Verified by hand first against a
                # throwaway Postgres with the same migrations (see the report).
                sweepFixtureSql = pkgs.writeText "rebase-hub-sweep-fixture.sql" ''
                  WITH u AS (
                    INSERT INTO users (id, email, nome, cognome, role, attivo)
                    VALUES (gen_random_uuid(), 'sweep-test@example.com', 'Test', 'Sweep', 'member', true)
                    RETURNING id
                  ), f AS (
                    INSERT INTO freelancers (id, user_id, links, stato, compilata_da)
                    SELECT gen_random_uuid(), u.id, '[]'::jsonb, 'nuovo', 'persona' FROM u
                    RETURNING id
                  )
                  INSERT INTO contract_documents
                    (id, kind, freelancer_id, text_version, testo_bozza, data, pdf, stato,
                     documenso_id, documenso_item_id, created_by)
                  SELECT gen_random_uuid(), 'quadro', f.id, '1.0', false, '{}'::jsonb,
                         '\x255044462d312e34'::bytea, 'inviato',
                         'env_test_sweep', 'item_test_sweep', u.id
                  FROM f, u;
                '';
              in
              pkgs.testers.runNixOSTest {
                name = "hub";
                nodes.machine = {
                  imports = [
                    self.nixosModules.rebase-hub
                    self.nixosModules.rebase-website
                  ];
                  services.rebase-hub = {
                    enable = true;
                    domain = "hub.test";
                    # An address the VM's own loopback refuses outright, so a document
                    # awaiting confirmation fails fast with `DocumensoFailed` rather than
                    # timing out: enough to make the sweep's `unconfirmed` count move,
                    # never a real Documenso.
                    settings = {
                      documenso_url = "http://127.0.0.1:1";
                      documenso_api_token = "unreachable-on-purpose";
                    };
                  };
                  services.rebase-website = {
                    enable = true;
                    domain = "hub.test";
                  };
                };
                testScript = ''
                  machine.wait_for_unit("rebase-hub-api.service")
                  machine.wait_for_open_port(8084)
                  machine.wait_for_unit("rebase-hub-mcp.service")
                  machine.wait_for_open_port(8088)
                  machine.wait_for_unit("nginx.service")

                  # The hub's probe touches the database on purpose, so a 200 here is
                  # the migration and Postgres both.
                  machine.succeed("curl -fsS http://localhost/health | grep -F '\"status\":\"ok\"'")
                  machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost/hub | grep -Fx '302 http://localhost/hub/'")
                  machine.succeed("curl -fsS http://localhost/hub/ | grep -F '/hub/assets/'")
                  machine.succeed("curl -fsS http://localhost/hub/admin/anything | grep -F '/hub/assets/'")
                  machine.succeed("curl -fsS http://localhost/hub/mark.svg >/dev/null")
                  # The two API prefixes the host routes to the hub reach FastAPI: an
                  # empty POST is a 422 with FastAPI's JSON body, which nginx's own
                  # errors never are. (`Server:` is no discriminator, nginx rewrites it.)
                  machine.succeed("curl -sS -X POST http://localhost/api/hub/companies | grep -F '\"detail\"'")
                  machine.succeed("curl -sS -X POST http://localhost/api/community/signups | grep -F '\"detail\"'")
                  # The admin's MCP server behind /api/hub/mcp (REB-213): the process
                  # itself refuses a call with no token.
                  machine.succeed("curl -sS -o /dev/null -D - -X POST http://localhost/api/hub/mcp | grep -i '^www-authenticate: Bearer'")
                  # The contracts sweep (REB-403) needs a document to find: one user,
                  # one freelancer, one framework agreement `inviato` with an envelope
                  # (`sweepFixtureSql`), as the superuser the same way the CRM's test
                  # reaches its own database.
                  machine.succeed(
                      "su postgres -s /bin/sh -c 'psql -d rebase -v ON_ERROR_STOP=1 -f ${sweepFixtureSql}'"
                  )
                  # A oneshot a timer fires every ten minutes; starting it once here is
                  # what the timer does. `settings.documenso_url` above answers nothing
                  # on this machine, so confirming the envelope fails fast, and the
                  # printed line proves the unit found the row and worked on it with its
                  # own environment and permissions, not that an empty database left it
                  # nothing to do.
                  machine.succeed("systemctl start rebase-hub-contracts-sweep.service")
                  out = machine.succeed(
                      "journalctl -u rebase-hub-contracts-sweep --no-pager -o cat"
                  )
                  assert "0 documenti ripresi, 1 non confermati" in out, out
                  # The renderer itself: pandoc, Typst and the brand are wrapped onto
                  # `bin/rebase` inside the package (REB-403), so this runs under the
                  # unit's own sandbox with nothing set by the caller, the way the CRM's
                  # test runs its own renderer pair as the service user.
                  out = machine.succeed(
                      "systemd-run --wait --pipe --uid=rebase -p ProtectSystem=strict "
                      "-p PrivateDevices=true -p PrivateTmp=true "
                      "${self.packages.${pkgs.stdenv.hostPlatform.system}.hub-api}/bin/rebase contracts-check"
                  )
                  lines = {line.split(": ", 1)[0]: line for line in out.splitlines()}
                  for document in ("contratto-quadro", "lettera-di-incarico"):
                      assert "versione 1.0" in lines.get(document, ""), f"{document}: {out!r}"
                  # The magic link's token travels in the SPA's URL and in no Referer,
                  # and the header is set once although two modules share the name.
                  machine.succeed("curl -sSI http://localhost/hub/ | grep -ic '^referrer-policy: strict-origin' | grep -Fx 1")
                  machine.succeed("curl -sSI http://localhost/ | grep -ic '^x-content-type-options: nosniff' | grep -Fx 1")
                  # The website's front door on the same name, untouched by the hub.
                  machine.succeed("curl -fsS http://localhost/ | grep -Fi 'rebase'")
                  machine.succeed("curl -sS -o /dev/null -w '%{http_code}' http://localhost/nothing | grep -Ex '404'")
                '';
              };

            website = pkgs.testers.runNixOSTest {
              name = "website";
              nodes.machine = {
                imports = [ self.nixosModules.rebase-website ];
                services.rebase-website = {
                  enable = true;
                  domain = "website.test";
                };
              };
              testScript = ''
                machine.wait_for_unit("nginx.service")
                # The path map of projects/website/deploy/nginx.conf, page by page,
                # which is `PAGES` and `REDIRECTS` in src/path-map-plugin.ts.
                machine.succeed("curl -fsS http://localhost/ | grep -Fi 'rebase'")
                machine.succeed("curl -fsS http://localhost/pigrocrm | grep -Fi 'pigrocrm'")
                machine.succeed("curl -fsS http://localhost/community | grep -Fi 'rebase'")
                machine.succeed("curl -fsS http://localhost/privacy >/dev/null")
                machine.succeed("curl -fsS http://localhost/terms >/dev/null")
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' http://localhost/termini | grep -Fx '301 http://localhost/terms'")
                machine.succeed("curl -fsS http://localhost/pitch >/dev/null")
                # The community page's old name, with its query string kept.
                machine.succeed("curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' 'http://localhost/orbiters?x=1' | grep -Fx '301 http://localhost/community?x=1'")
                # The two files the build generates from the same map.
                machine.succeed("curl -fsS http://localhost/robots.txt | grep -F 'Sitemap:'")
                machine.succeed("curl -fsS http://localhost/sitemap.xml | grep -F '<urlset'")
                # The hashed assets the pages link to are served under /assets/.
                machine.succeed("curl -fsS http://localhost/ | grep -oE '/assets/[^\"]+' | head -1 | xargs -I{} curl -fsS http://localhost{} >/dev/null")
                # No fallback: a file nobody linked is a 404, as is the bare html name.
                machine.succeed("curl -sS -o /dev/null -w '%{http_code}' http://localhost/nothing | grep -Ex '404'")
                machine.succeed("curl -sS -o /dev/null -w '%{http_code}' http://localhost/privacy.html | grep -Ex '404'")
                machine.succeed("curl -sSI http://localhost/ | grep -i '^x-content-type-options: nosniff'")
              '';
            };
          };

          devShells.default = pkgs.mkShell {
            packages = [
              # The two package managers. uv's own version is not load-bearing: every
              # command in this repository runs against uv.lock as written (`--frozen`,
              # and `--locked` in the CI gate, which only checks it). pnpm's is
              # not either, since pnpm 10+ reads `packageManager` from package.json
              # and switches itself to the pinned version on first use.
              pkgs.uv
              pkgs.pnpm
              nodejs
              python

              pandoc
              typst
              # `pdftotext`: how the suite reads a rendered PDF back. Test equipment,
              # never part of the product, so the distribution version is fine. Same
              # for `strings`, which one template test runs over a PDF: mkShell's own
              # stdenv happens to put it on PATH, and this line is so nobody depends
              # on that happening.
              pkgs.poppler-utils
              pkgs.binutils
            ];

            # uv would otherwise download a python-build-standalone interpreter, which
            # expects /lib64/ld-linux-x86-64.so.2 and does not start on NixOS. Point it
            # at the interpreter above and refuse the download outright, so the failure
            # mode on a bad `.python-version` is an error rather than a silent fetch.
            UV_PYTHON = lib.getExe python;
            UV_PYTHON_DOWNLOADS = "never";

            # The manylinux wheels in uv.lock (lxml, psycopg's binary build, pydantic-core)
            # bundle their own libraries but leave libstdc++ and zlib to the system, as
            # the manylinux policy allows. NixOS has no system libraries on a global
            # path, so the two are put on the loader's path for this shell only.
            LD_LIBRARY_PATH = lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ];
          };
        };

      # ---- Self-hosting: one NixOS module per deployable ---------------------------
      #
      # These are for whoever self-hosts a product under the AGPL, on NixOS. They are
      # not how rebase's own environments are deployed, which stays the compose stack
      # and `_deploy-compose.yml` (docs/design/DECISIONS.md, 2026-09-09), so a
      # preview or a production of ours is never brought up from here.
      #
      # Each module reads its compose file and its nginx configuration as the contract
      # and restates them in NixOS terms: the same environment variables, the same
      # locations, the same start-up order (migrate, then serve). Where a location is
      # written twice, here and in `deploy/`, the VM test above is what keeps the two
      # saying the same thing.
      flake.nixosModules =
        let
          # `settings.timezone = "Europe/Rome"` becomes `PIGROCRM_TIMEZONE=Europe/Rome`:
          # the keys are the field names of the project's `Settings` class, which is
          # the one place their meaning is documented.
          settingsToEnv =
            prefix: settings:
            inputs.nixpkgs.lib.mapAttrs' (
              name: value:
              inputs.nixpkgs.lib.nameValuePair "${prefix}${inputs.nixpkgs.lib.toUpper name}" (
                if builtins.isBool value then inputs.nixpkgs.lib.boolToString value else toString value
              )
            ) settings;
          settingsType =
            lib:
            lib.types.attrsOf (
              lib.types.oneOf [
                lib.types.str
                lib.types.bool
                lib.types.int
              ]
            );
          # The systemd sandbox the API and MCP units share: a static user for the
          # state that must outlive the unit, a read-only view of everything else.
          hardening = {
            NoNewPrivileges = true;
            PrivateTmp = true;
            PrivateDevices = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            RestrictNamespaces = true;
            LockPersonality = true;
            RestrictRealtime = true;
            SystemCallArchitectures = "native";
          };
          # A Postgres of the machine's own, reached over the socket as the unit's
          # user, which is what the compose stack's `db` service is here.
          localPostgres = user: {
            services.postgresql = {
              enable = true;
              ensureDatabases = [ user ];
              ensureUsers = [
                {
                  name = user;
                  ensureDBOwnership = true;
                }
              ];
            };
          };
          socketUrl = user: "postgresql+psycopg://${user}@/${user}?host=/run/postgresql";
          # Where uvicorn listens is where nginx proxies: the `address` option moves
          # both, and an IPv6 literal is bracketed for nginx.
          upstream =
            address: port:
            "http://${
              if inputs.nixpkgs.lib.hasInfix ":" address then "[${address}]" else address
            }:${toString port}";
          proxyLocation = address: port: {
            proxyPass = upstream address port;
          };
          # The MCP transport is Streamable HTTP: one request may stream for as long
          # as a tool runs, so no buffering and a long read timeout (spa.conf, the
          # `/mcp` locations).
          mcpLocation = address: port: {
            proxyPass = upstream address port;
            extraConfig = ''
              proxy_http_version 1.1;
              proxy_buffering off;
              proxy_read_timeout 300s;
            '';
          };
        in
        {
          # The response headers the host's nginx adds for the whole origin in
          # production (projects/*/deploy/**/security-headers.conf, REB-275), minus
          # HSTS, which is the host's TLS decision, and minus the full CSP (enforcing
          # since REB-306), which names the analytics and pixel hosts of our own
          # deployment and not a self-hoster's; its `frame-ancestors 'none'` is the
          # one directive kept, as a CSP of its own. Here the module is the host. A
          # module names its domain and the headers are added once per name, however
          # many modules share it: the `key` is what deduplicates this module when
          # the hub and the website import it on the same machine, and the hub's VM
          # test counts the header to prove it.
          nginx-headers = {
            key = "rebase/nginx-headers";
            imports = [
              (
                { config, lib, ... }:
                {
                  options.services.rebase-nginx-headers.domains = lib.mkOption {
                    type = lib.types.listOf lib.types.str;
                    default = [ ];
                    internal = true;
                    description = "The virtual hosts that carry the origin-wide security headers.";
                  };
                  config.services.nginx.virtualHosts =
                    lib.genAttrs (lib.unique config.services.rebase-nginx-headers.domains)
                      (_: {
                        extraConfig = ''
                          add_header X-Content-Type-Options nosniff always;
                          # `strict-origin`, not `strict-origin-when-cross-origin`: the
                          # magic link's token is in the SPA's URL and must not travel in a
                          # Referer to anything.
                          add_header Referrer-Policy strict-origin always;
                          add_header X-Frame-Options DENY always;
                          add_header Content-Security-Policy "frame-ancestors 'none';" always;
                          add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
                        '';
                      });
                }
              )
            ];
          };

          pigrocrm =
            {
              config,
              lib,
              pkgs,
              ...
            }:
            let
              cfg = config.services.pigrocrm;
              own = self.packages.${pkgs.stdenv.hostPlatform.system};
              # Dockerfile.web copies `dist/` to `html/app`: the SPA's assets are
              # absolute under `/app/`, so the document root holds it under that name.
              webRoot = pkgs.runCommand "pigrocrm-web-root" { } ''
                mkdir -p "$out"
                ln -s ${cfg.web} "$out/app"
              '';
              migrate = pkgs.writeShellScript "pigrocrm-migrate" ''
                cd ${cfg.package}/share/pigrocrm-api
                exec ${cfg.package}/bin/alembic upgrade head
              '';
              # The SPA reads a space's prefix from the URL (spa.conf, "Spaces"):
              # `/<slug>/app/...` is the same shell, `/<slug>/api/...`, `/<slug>/health`
              # and `/<slug>/mcp` reach the API or the MCP server with the full path,
              # whose middleware strips the prefix.
              slug = "[a-z0-9][a-z0-9-]{1,30}[a-z0-9]";
              # The compose file's `x-api-environment`: one block, both services.
              environment = {
                PIGROCRM_STORAGE_LOCAL_ROOT = cfg.documentsDir;
                # `tenants.service.default_alembic_ini` walks up from the package
                # to a checkout layout that a virtualenv in the store does not
                # have; this is the setting that exists for exactly that.
                PIGROCRM_TENANTS_ALEMBIC_INI = "${cfg.package}/share/pigrocrm-api/alembic.ini";
                PIGROCRM_PANDOC_BINARY = lib.getExe' cfg.renderer.pandoc "pandoc";
                PIGROCRM_TYPST_BINARY = lib.getExe' cfg.renderer.typst "typst";
              }
              // lib.optionalAttrs cfg.database.createLocally {
                PIGROCRM_DATABASE_URL = socketUrl "pigrocrm";
              }
              // settingsToEnv "PIGROCRM_" cfg.settings;
              serviceConfig = hardening // {
                User = "pigrocrm";
                Group = "pigrocrm";
                EnvironmentFile = cfg.environmentFile;
                StateDirectory = "pigrocrm";
                ReadWritePaths = [ cfg.documentsDir ];
                # The migration and every space's upgrade run before uvicorn, and
                # systemd's 90 s would kill a long one halfway (the image's CMD has
                # no such limit). Retries the way `restart: unless-stopped` does:
                # without giving up after five in ten seconds.
                TimeoutStartSec = "30min";
                Restart = "on-failure";
                RestartSec = 5;
              };
              unitConfig.StartLimitIntervalSec = 0;
            in
            {
              imports = [ self.nixosModules.nginx-headers ];

              options.services.pigrocrm = {
                enable = lib.mkEnableOption "PigroCRM, the API and its MCP server behind nginx with the SPA";
                package = lib.mkOption {
                  type = lib.types.package;
                  default = own.pigrocrm-api;
                  defaultText = "rebase.packages.<system>.pigrocrm-api";
                  description = "The API's environment, with the MCP server, Alembic and the migrations under share/.";
                };
                web = lib.mkOption {
                  type = lib.types.package;
                  default = own.pigrocrm-web;
                  defaultText = "rebase.packages.<system>.pigrocrm-web";
                  description = "The SPA's `dist/`.";
                };
                domain = lib.mkOption {
                  type = lib.types.str;
                  example = "pigro.example.com";
                  description = "The nginx virtual host the CRM answers on. TLS is the host's to add (`enableACME`, `forceSSL`).";
                };
                address = lib.mkOption {
                  type = lib.types.str;
                  default = "127.0.0.1";
                  description = "Where uvicorn listens; nginx is what faces the network.";
                };
                port = lib.mkOption {
                  type = lib.types.port;
                  default = 8000;
                };
                mcp = {
                  enable = lib.mkOption {
                    type = lib.types.bool;
                    default = true;
                    description = "Run the MCP server (the compose stack's `mcp` service) at `/mcp` and `/<slug>/mcp`, for the «Connect an agent» button. It shares the API's environment and reads the same database.";
                  };
                  port = lib.mkOption {
                    type = lib.types.port;
                    default = 8001;
                  };
                };
                environmentFile = lib.mkOption {
                  type = lib.types.path;
                  example = "/run/secrets/pigrocrm.env";
                  description = ''
                    The secrets, as `PIGROCRM_*=value` lines, never in the store. It must
                    define `PIGROCRM_JWT_SECRET` (32 characters or more); the Google and
                    Resend variables and a non-local `PIGROCRM_DATABASE_URL` go here too.
                  '';
                };
                settings = lib.mkOption {
                  type = settingsType lib;
                  default = { };
                  example = {
                    timezone = "Europe/Rome";
                    root_slug = "studiorossi";
                    public_url = "https://pigro.example.com";
                    mcp_full_access = false;
                  };
                  description = "Non-secret settings by their field name in `pigrocrm.core.config.Settings`, exported as `PIGROCRM_<NAME>`.";
                };
                database.createLocally = lib.mkOption {
                  type = lib.types.bool;
                  default = true;
                  description = "Run PostgreSQL on this machine and point the API at it over the socket. Off, `PIGROCRM_DATABASE_URL` must be in the environment file.";
                };
                documentsDir = lib.mkOption {
                  type = lib.types.path;
                  default = "/var/lib/pigrocrm/documents";
                  description = "Where `storage_backend = local` writes the PDFs (the compose stack's `PIGROCRM_DOCUMENTS_DIR`).";
                };
                renderer = {
                  pandoc = lib.mkOption {
                    type = lib.types.package;
                    default = own.pandoc;
                    defaultText = "rebase.packages.<system>.pandoc";
                    description = "Pinned with the API image; see projects/pigrocrm/Dockerfile.api.";
                  };
                  typst = lib.mkOption {
                    type = lib.types.package;
                    default = own.typst;
                    defaultText = "rebase.packages.<system>.typst";
                  };
                };
              };

              config = lib.mkIf cfg.enable (
                lib.mkMerge [
                  (lib.mkIf cfg.database.createLocally (localPostgres "pigrocrm"))
                  {
                    services.rebase-nginx-headers.domains = [ cfg.domain ];

                    # A space is a database of its own, created by the API beside the
                    # root's (`tenants.service.TenantService.provision`), and so is the
                    # registry `pigrocrm_tenants`. The compose stack's user is Postgres'
                    # superuser; here it is a role that may create databases and no more.
                    # `postgresql-setup.service` is where nixpkgs creates the role and
                    # the database (`ensureUsers`); the grant goes on the end of it and
                    # the API waits for it, not merely for the server. A bare `psql`,
                    # exactly as the statements nixpkgs writes above it: the unit's
                    # environment carries the port.
                    systemd.services.postgresql-setup.script = lib.mkIf cfg.database.createLocally (
                      lib.mkAfter ''
                        psql -tAc 'ALTER ROLE pigrocrm CREATEDB'
                      ''
                    );

                    users.users.pigrocrm = {
                      isSystemUser = true;
                      group = "pigrocrm";
                      home = "/var/lib/pigrocrm";
                    };
                    users.groups.pigrocrm = { };

                    systemd.services.pigrocrm-api = {
                      description = "PigroCRM API";
                      inherit unitConfig;
                      wantedBy = [ "multi-user.target" ];
                      after = [
                        "network.target"
                      ]
                      ++ lib.optional cfg.database.createLocally "postgresql-setup.service";
                      requires = lib.optional cfg.database.createLocally "postgresql-setup.service";
                      inherit environment;
                      serviceConfig = serviceConfig // {
                        # The image's CMD, in order: the root's migration, then every
                        # registered space brought to the same schema and given its
                        # defaults (ORB-189), then uvicorn. One instance, as in the image.
                        ExecStartPre = [
                          "${migrate}"
                          "${cfg.package}/bin/pigrocrm ensure-space-defaults"
                        ];
                        ExecStart = "${cfg.package}/bin/uvicorn pigrocrm_api.main:app --host ${cfg.address} --port ${toString cfg.port}";
                      };
                    };
                    # The compose `mcp` service: the same build and environment, a
                    # second process, started after the API the way `depends_on` says.
                    systemd.services.pigrocrm-mcp = lib.mkIf cfg.mcp.enable {
                      description = "PigroCRM MCP server";
                      inherit unitConfig;
                      wantedBy = [ "multi-user.target" ];
                      after = [ "pigrocrm-api.service" ];
                      wants = [ "pigrocrm-api.service" ];
                      inherit environment;
                      serviceConfig = serviceConfig // {
                        ExecStart = "${cfg.package}/bin/uvicorn pigrocrm_mcp.http:app --host ${cfg.address} --port ${toString cfg.mcp.port}";
                      };
                    };
                    systemd.tmpfiles.rules = [ "d ${cfg.documentsDir} 0750 pigrocrm pigrocrm -" ];

                    # projects/pigrocrm/deploy/nginx/spa.conf, minus what only exists
                    # because that nginx lives in a container beside a moving `api`.
                    services.nginx = {
                      enable = true;
                      recommendedProxySettings = true;
                      virtualHosts.${cfg.domain} = {
                        root = webRoot;
                        # A relative Location on every redirect: an absolute one drops
                        # a non-default port (measured against the image, spa.conf).
                        extraConfig = ''
                          absolute_redirect off;
                          client_max_body_size 1m;
                        '';
                        locations = {
                          "= /".return = "302 /app/";
                          "= /app".return = "302 /app/";
                          "= /login".return = "302 /app/login";
                          "^~ /app/".tryFiles = "$uri /app/index.html";
                          # The magic link lands on /app/verify?t=<token> (spa.conf):
                          # the shell, served from inside this location so `access_log
                          # off` holds and the token never reaches the log; the old
                          # name /app/entra follows it, query string and all.
                          "= /app/verify" = {
                            tryFiles = "/app/index.html =404";
                            extraConfig = "access_log off;";
                          };
                          "= /app/entra" = {
                            return = "301 /app/verify$is_args$args";
                            extraConfig = "access_log off;";
                          };
                          # The invitation link is the same `?t=` shape (REB-291).
                          "= /app/invite" = {
                            tryFiles = "/app/index.html =404";
                            extraConfig = "access_log off;";
                          };
                          "~ \"^/${slug}/app/invite$\"" = {
                            priority = 900;
                            tryFiles = "/app/index.html =404";
                            extraConfig = "access_log off;";
                          };
                          "~ \"^/${slug}/app/verify$\"" = {
                            priority = 900;
                            tryFiles = "/app/index.html =404";
                            extraConfig = "access_log off;";
                          };
                          "~ \"^(/${slug})/app/entra$\"" = {
                            priority = 900;
                            return = "301 $1/app/verify$is_args$args";
                            extraConfig = "access_log off;";
                          };
                          "~ \"^/${slug}/app(/|$)\"".tryFiles = "/app/index.html =404";
                          # nginx takes the first regex that matches in file order, and
                          # the module writes them alphabetically: the documents one
                          # must come before `(api|health)`, which would swallow it.
                          "~ \"^/${slug}/api/documents(/|$)\"" = proxyLocation cfg.address cfg.port // {
                            priority = 900;
                            extraConfig = "client_max_body_size 105m;";
                          };
                          "~ \"^/${slug}/(api|health)(/|$)\"" = proxyLocation cfg.address cfg.port;
                          "~ \"^/(${slug})$\"".return = "302 /$1/app/";
                          "^~ /api/documents/" = proxyLocation cfg.address cfg.port // {
                            extraConfig = "client_max_body_size 105m;";
                          };
                          "/api/" = proxyLocation cfg.address cfg.port;
                          "= /health" = proxyLocation cfg.address cfg.port;
                          # Anything else: a 404, never the SPA shell.
                          "/".tryFiles = "$uri =404";
                        }
                        // lib.optionalAttrs cfg.mcp.enable {
                          "= /mcp/".return = "308 /mcp";
                          "= /mcp" = mcpLocation cfg.address cfg.mcp.port;
                          "~ \"^/${slug}/mcp(/|$)\"" = mcpLocation cfg.address cfg.mcp.port;
                        };
                      };
                    };
                  }
                ]
              );
            };

          rebase-hub =
            {
              config,
              lib,
              pkgs,
              ...
            }:
            let
              cfg = config.services.rebase-hub;
              own = self.packages.${pkgs.stdenv.hostPlatform.system};
              webRoot = pkgs.runCommand "rebase-hub-web-root" { } ''
                mkdir -p "$out"
                ln -s ${cfg.web} "$out/hub"
              '';
              migrate = pkgs.writeShellScript "rebase-hub-migrate" ''
                cd ${cfg.package}/share/hub-api
                exec ${cfg.package}/bin/alembic upgrade head
              '';
              environment =
                lib.optionalAttrs cfg.database.createLocally {
                  REBASE_DATABASE_URL = socketUrl "rebase";
                }
                // settingsToEnv "REBASE_" cfg.settings;
              serviceConfig = hardening // {
                User = "rebase";
                Group = "rebase";
                EnvironmentFile = lib.optional (cfg.environmentFile != null) cfg.environmentFile;
                # As in the CRM module: the migration may outlast 90 s, and a failing
                # unit keeps retrying the way the compose stack does.
                TimeoutStartSec = "30min";
                Restart = "on-failure";
                RestartSec = 5;
              };
              # The oneshot sweep is not this: on failure it waits for the timer's next
              # tick rather than restarting fast, the way the compose loop's own
              # `while :; do sleep 600; ...; done` does.
              sweepServiceConfig = hardening // {
                Type = "oneshot";
                User = "rebase";
                Group = "rebase";
                EnvironmentFile = lib.optional (cfg.environmentFile != null) cfg.environmentFile;
              };
              unitConfig.StartLimitIntervalSec = 0;
            in
            {
              imports = [ self.nixosModules.nginx-headers ];

              options.services.rebase-hub = {
                enable = lib.mkEnableOption "the rebase hub: the signup wizards, the member and admin areas, their API and the admin's MCP server";
                package = lib.mkOption {
                  type = lib.types.package;
                  default = own.hub-api;
                  defaultText = "rebase.packages.<system>.hub-api";
                };
                web = lib.mkOption {
                  type = lib.types.package;
                  default = own.hub-web;
                  defaultText = "rebase.packages.<system>.hub-web";
                };
                domain = lib.mkOption {
                  type = lib.types.str;
                  example = "example.com";
                  description = "The virtual host: the SPA under /hub/, the API under /api/hub/ and /api/community/signups, the MCP server under /api/hub/mcp, beside the website when both share a name.";
                };
                address = lib.mkOption {
                  type = lib.types.str;
                  default = "127.0.0.1";
                };
                port = lib.mkOption {
                  type = lib.types.port;
                  # The compose stack's host-side ports, and not the CRM's 8000 and
                  # 8001: the two products run on one machine without a line of
                  # configuration between them.
                  default = 8084;
                };
                mcp = {
                  enable = lib.mkOption {
                    type = lib.types.bool;
                    default = true;
                    description = "Run the admin's MCP server (the compose stack's `mcp` service) behind /api/hub/mcp (REB-213).";
                  };
                  port = lib.mkOption {
                    type = lib.types.port;
                    default = 8088;
                  };
                };
                environmentFile = lib.mkOption {
                  type = lib.types.nullOr lib.types.path;
                  default = null;
                  description = "`REBASE_*=value` lines for the secrets: the Resend key, the conversions API key, the CRM's registry token, a non-local database URL.";
                };
                settings = lib.mkOption {
                  type = settingsType lib;
                  default = { };
                  example = {
                    signup_url = "https://example.com/";
                    hub_url = "https://example.com/hub";
                  };
                  description = "Non-secret settings by their field name in `rebase_core.config.Settings`, exported as `REBASE_<NAME>`.";
                };
                database.createLocally = lib.mkOption {
                  type = lib.types.bool;
                  default = true;
                };
              };

              config = lib.mkIf cfg.enable (
                lib.mkMerge [
                  (lib.mkIf cfg.database.createLocally (localPostgres "rebase"))
                  {
                    assertions = [
                      {
                        assertion = cfg.database.createLocally || cfg.environmentFile != null;
                        message = "services.rebase-hub: with database.createLocally off, environmentFile must carry REBASE_DATABASE_URL, or the API boots against the Settings default.";
                      }
                    ];
                    services.rebase-nginx-headers.domains = [ cfg.domain ];

                    users.users.rebase = {
                      isSystemUser = true;
                      group = "rebase";
                    };
                    users.groups.rebase = { };

                    systemd.services.rebase-hub-api = {
                      description = "rebase hub API";
                      inherit unitConfig;
                      wantedBy = [ "multi-user.target" ];
                      after = [
                        "network.target"
                      ]
                      ++ lib.optional cfg.database.createLocally "postgresql-setup.service";
                      requires = lib.optional cfg.database.createLocally "postgresql-setup.service";
                      inherit environment;
                      serviceConfig = serviceConfig // {
                        ExecStartPre = migrate;
                        ExecStart = "${cfg.package}/bin/uvicorn rebase_api.main:app --host ${cfg.address} --port ${toString cfg.port}";
                      };
                    };
                    systemd.services.rebase-hub-mcp = lib.mkIf cfg.mcp.enable {
                      description = "rebase hub MCP server";
                      inherit unitConfig;
                      wantedBy = [ "multi-user.target" ];
                      after = [ "rebase-hub-api.service" ];
                      wants = [ "rebase-hub-api.service" ];
                      inherit environment;
                      serviceConfig = serviceConfig // {
                        ExecStart = "${cfg.package}/bin/uvicorn rebase_mcp.http:app --host ${cfg.address} --port ${toString cfg.mcp.port}";
                      };
                    };
                    # The compose stack's `sweep` service (docker-compose.yml):
                    # `rebase contracts-sweep` redoes what a lost background task or a
                    # restart left unfinished. Compose runs it as a loop that sleeps
                    # ten minutes between calls; here it is a oneshot a timer fires on
                    # the same cadence (REB-403).
                    systemd.services.rebase-hub-contracts-sweep = {
                      description = "rebase hub contracts sweep";
                      inherit unitConfig;
                      after = [ "rebase-hub-api.service" ];
                      wants = [ "rebase-hub-api.service" ];
                      inherit environment;
                      serviceConfig = sweepServiceConfig // {
                        ExecStart = "${cfg.package}/bin/rebase contracts-sweep";
                      };
                    };
                    systemd.timers.rebase-hub-contracts-sweep = {
                      description = "rebase hub contracts sweep, every ten minutes";
                      wantedBy = [ "timers.target" ];
                      timerConfig = {
                        OnBootSec = "10min";
                        OnUnitActiveSec = "10min";
                      };
                    };

                    # projects/hub/deploy/nginx.conf for the SPA, and the API locations
                    # the host's letsrebase.conf routes to the hub.
                    services.nginx = {
                      enable = true;
                      recommendedProxySettings = true;
                      virtualHosts.${cfg.domain} = {
                        # No vhost-level `root` and no server-level directive of any
                        # kind: the website's module may share this name, and a
                        # directive set twice on one server (`absolute_redirect`, say)
                        # is a duplicate nginx refuses to start on. Every location
                        # names its own root, and the one redirect carries its own
                        # `absolute_redirect`.
                        locations = {
                          "= /hub" = {
                            return = "302 /hub/";
                            extraConfig = "absolute_redirect off;";
                          };
                          "^~ /hub/assets/" = {
                            root = webRoot;
                            tryFiles = "$uri =404";
                          };
                          "= /hub/mark.svg" = {
                            root = webRoot;
                            tryFiles = "$uri =404";
                          };
                          "^~ /hub/" = {
                            root = webRoot;
                            tryFiles = "$uri /hub/index.html";
                          };
                          # letsrebase.conf also keeps `/api/orbiters/signups`, the
                          # name before REB-204, for pages cached with it; a fresh
                          # self-host has no such pages, so the alias is not carried.
                          "= /api/community/signups" = proxyLocation cfg.address cfg.port;
                          "^~ /api/hub/" = proxyLocation cfg.address cfg.port // {
                            extraConfig = "client_max_body_size 6M;";
                          };
                          "= /health" = proxyLocation cfg.address cfg.port;
                          "/".return = lib.mkDefault "404";
                        }
                        // lib.optionalAttrs cfg.mcp.enable {
                          # `/api/hub/mcp` is `/mcp` to the process, prefix and all
                          # (letsrebase.conf). `^~` so it wins over `^~ /api/hub/`.
                          "^~ /api/hub/mcp" = mcpLocation cfg.address cfg.mcp.port // {
                            proxyPass = "${upstream cfg.address cfg.mcp.port}/mcp";
                          };
                        };
                      };
                    };
                  }
                ]
              );
            };

          rebase-website =
            {
              config,
              lib,
              pkgs,
              ...
            }:
            let
              cfg = config.services.rebase-website;
              own = self.packages.${pkgs.stdenv.hostPlatform.system};
              page = file: {
                root = cfg.package;
                tryFiles = "/${file} =404";
              };
            in
            {
              imports = [ self.nixosModules.nginx-headers ];

              options.services.rebase-website = {
                enable = lib.mkEnableOption "the rebase website: the landing, the product and community pages, the policies and the pitch";
                package = lib.mkOption {
                  type = lib.types.package;
                  default = own.website;
                  defaultText = "rebase.packages.<system>.website";
                };
                domain = lib.mkOption {
                  type = lib.types.str;
                  example = "example.com";
                };
              };

              # projects/website/deploy/nginx.conf: which extensionless path is which
              # page, and nothing invented. The website's own unit test parses that
              # file's `location =` lines against src/path-map-plugin.ts; the VM test
              # walks these.
              config = lib.mkIf cfg.enable {
                services.rebase-nginx-headers.domains = [ cfg.domain ];
                services.nginx = {
                  enable = true;
                  virtualHosts.${cfg.domain} = {
                    locations = {
                      "= /" = page "index.html";
                      "= /pigrocrm" = page "pigrocrm.html";
                      "= /community" = page "community.html";
                      # The community page's old name (ORB-194). A relative Location,
                      # on the location itself rather than the server: the hub's
                      # module may share this name (see there).
                      "= /orbiters" = {
                        return = "301 /community$is_args$args";
                        extraConfig = "absolute_redirect off;";
                      };
                      # The pitch deck, a page shared by link (ORB-153).
                      "= /pitch" = page "pitch.html";
                      "= /privacy" = page "privacy.html";
                      "= /terms" = page "terms.html";
                      "= /termini" = {
                        return = "301 /terms";
                        extraConfig = "absolute_redirect off;";
                      };
                      # Generated by the build from the same map as the pages.
                      "= /robots.txt" = page "robots.txt";
                      "= /sitemap.xml" = page "sitemap.xml";
                      "/assets/" = {
                        root = cfg.package;
                        tryFiles = "$uri =404";
                      };
                      "/".return = lib.mkDefault "404";
                    };
                  };
                };
              };
            };
        };
    };
}
