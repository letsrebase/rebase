#!/usr/bin/env bash
# Print a compose override that gives every service with a `build:` section a GitHub
# Actions layer cache, so an image build job rebuilds only the layers a change
# touched. Run it in the project directory with the compose file's `:?` variables
# set (compose interpolates the whole file), and pass the result with a second `-f`:
#
#   scripts/ci-build-cache-override.sh hub > "$RUNNER_TEMP/cache.yml"
#   COMPOSE_BAKE=true docker compose -f docker-compose.yml -f "$RUNNER_TEMP/cache.yml" build
#
# The build itself is unchanged: same context, same Dockerfile, same stages. Services
# that share a Dockerfile (the hub builds one image under five names) share a cache
# scope, and only the first of them writes it, so concurrent writers never race.
#
# The cache backend needs the runner's cache credentials in the environment and a
# buildx builder that supports it (docker/setup-buildx-action plus
# crazy-max/ghaction-github-runtime in ci.yml).
set -euo pipefail

scope_prefix="${1:?usage: ci-build-cache-override.sh <scope-prefix> [compose args...]}"
shift

docker compose "$@" config --format json | jq --arg prefix "$scope_prefix" '
  def scope: ($prefix + "-" + (.value.build | (.dockerfile // "Dockerfile") | gsub("[^A-Za-z0-9]+"; "-")));
  [.services | to_entries[] | select(.value.build != null)]
  | group_by(.value.build.context + "|" + (.value.build.dockerfile // "Dockerfile"))
  | map(
      . as $group
      | ($group[0] | scope) as $s
      | $group | to_entries | map({
          key: .value.key,
          value: {
            build: (
              {cache_from: ["type=gha,scope=\($s)"]}
              + (if .key == 0 then {cache_to: ["type=gha,scope=\($s),mode=max"]} else {} end)
            )
          }
        })
    )
  | flatten
  | {services: (map({(.key): .value}) | add)}
'
