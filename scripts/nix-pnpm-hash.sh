#!/usr/bin/env bash
# Recompute the pnpm store hash `flake.nix` needs after a `pnpm-lock.yaml`
# change (REB-452).
#
# `fetchPnpmDeps` is a fixed-output derivation: when the hash already
# declared in flake.nix has an output in the local Nix store, `nix build`
# does not rebuild it and a stale hash "succeeds" silently instead of naming
# the new one. Setting the hash to a fake value forces a rebuild, and the
# failure names the real one, on the "got:" line.
#
# There is no Linux builder for this repository any more (qasimodo.com is
# gone, Ivan, 2026-09-25). This script builds inside a `nixos/nix` container
# on the local machine instead. The hash does not depend on the CPU
# architecture: aarch64-linux and x86_64-linux reproduce the same value, so
# it runs on whichever one the container gives it (aarch64-linux on Apple
# Silicon, x86_64-linux on an Intel Mac or a Linux box), unless overridden.
#
# Usage:
#   scripts/nix-pnpm-hash.sh                       # print the hash
#   scripts/nix-pnpm-hash.sh --write                # also write it into flake.nix
#   scripts/nix-pnpm-hash.sh --ref origin/main      # build a ref other than HEAD
#   scripts/nix-pnpm-hash.sh --system x86_64-linux  # force a system
#
# It reads the given ref's committed content (default: HEAD), never the
# working tree's uncommitted changes, and always builds a throwaway copy: a
# failed run leaves flake.nix untouched.
#
# Docker's /nix is the named volume "rebase-nix-store", so a second run
# reuses the store instead of paying the several-hundred-MB pull and build
# again. Delete it with: docker volume rm rebase-nix-store
#
# Docker Desktop's credential helper can hang the image pull; this script
# always points its own docker calls at an empty, throwaway DOCKER_CONFIG to
# sidestep that, without touching your shell's own docker config.

set -euo pipefail

FAKE_HASH='sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
DOCKER_VOLUME='rebase-nix-store'
MIN_FREE_GB=5
IMAGE='nixos/nix:latest'

write=0
ref='HEAD'
system=''

while [ $# -gt 0 ]; do
  case "$1" in
    --write)
      write=1
      shift
      ;;
    --ref)
      ref="$2"
      shift 2
      ;;
    --system)
      system="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "nix-pnpm-hash: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root=$(cd "$(dirname "$0")/.." && git rev-parse --show-toplevel)

# The Mac this runs on is near full.
data_mount='/'
[ -d /System/Volumes/Data ] && data_mount='/System/Volumes/Data'
avail_kb=$(df -Pk "$data_mount" | awk 'NR==2 {print $4}')
avail_gb=$((avail_kb / 1024 / 1024))
if [ "$avail_gb" -lt "$MIN_FREE_GB" ]; then
  echo "nix-pnpm-hash: only ${avail_gb} GB free on ${data_mount}, refusing (need at least ${MIN_FREE_GB} GB)" >&2
  exit 1
fi

if [ -z "$system" ]; then
  case "$(uname -m)" in
    arm64 | aarch64) system='aarch64-linux' ;;
    x86_64 | amd64) system='x86_64-linux' ;;
    *)
      echo "nix-pnpm-hash: unrecognised host architecture $(uname -m), pass --system explicitly" >&2
      exit 2
      ;;
  esac
fi
case "$system" in
  aarch64-linux) platform='linux/arm64' ;;
  x86_64-linux) platform='linux/amd64' ;;
  *)
    echo "nix-pnpm-hash: unsupported --system $system (want aarch64-linux or x86_64-linux)" >&2
    exit 2
    ;;
esac

copy_dir=$(mktemp -d "${TMPDIR:-/tmp}/nix-pnpm-hash.XXXXXX")
docker_config_dir=$(mktemp -d "${TMPDIR:-/tmp}/nix-pnpm-hash-docker.XXXXXX")
cleanup() {
  rm -rf "$copy_dir" "$docker_config_dir"
}
trap cleanup EXIT

# A throwaway copy of the ref's tracked content, never the working tree's
# own flake.nix. Nix only evaluates git-tracked files, so the copy becomes
# its own tiny, self-contained repo (no commit needed on the real one).
git -C "$repo_root" archive "$ref" | tar -x -C "$copy_dir"
git -C "$copy_dir" init -q
git -C "$copy_dir" add -A
git -C "$copy_dir" \
  -c user.email='nix-pnpm-hash@localhost' -c user.name='nix-pnpm-hash' \
  commit -q -m 'throwaway copy for nix-pnpm-hash.sh'

if [ ! -f "$copy_dir/flake.nix" ]; then
  echo "nix-pnpm-hash: $ref has no flake.nix, aborting" >&2
  exit 1
fi

perl -pi -e "s/^(\\s*hash = )\"sha256-[^\"]*\"(;)/\${1}\"$FAKE_HASH\"\${2}/" "$copy_dir/flake.nix"
if [ "$(grep -c "$FAKE_HASH" "$copy_dir/flake.nix")" != 1 ]; then
  echo "nix-pnpm-hash: did not find exactly one pnpmDeps hash in flake.nix, aborting" >&2
  exit 1
fi
git -C "$copy_dir" -c user.email='nix-pnpm-hash@localhost' -c user.name='nix-pnpm-hash' \
  commit -q -am 'set fake pnpmDeps hash'

echo "nix-pnpm-hash: building packages.$system.pigrocrm-web.pnpmDeps in a $IMAGE container ($platform)..." >&2
echo '{}' >"$docker_config_dir/config.json"

set +e
build_log=$(DOCKER_CONFIG="$docker_config_dir" docker run --rm \
  --platform "$platform" \
  -v "${DOCKER_VOLUME}:/nix" \
  -v "${copy_dir}:/workspace:ro" \
  -w /workspace \
  "$IMAGE" \
  sh -c "nix build --extra-experimental-features 'nix-command flakes' --no-link '.#packages.${system}.pigrocrm-web.pnpmDeps'" 2>&1)
docker_status=$?
set -e

got_hash=$(printf '%s\n' "$build_log" | grep -oE 'got:[[:space:]]*sha256-[A-Za-z0-9+/=]+' | tail -n1 | grep -oE 'sha256-[A-Za-z0-9+/=]+' || true)

if [ -z "$got_hash" ]; then
  echo "$build_log" >&2
  echo "nix-pnpm-hash: build did not fail with a 'got: sha256-...' line (docker exit $docker_status); see the log above" >&2
  exit 1
fi

echo "$got_hash"

if [ "$write" = 1 ]; then
  perl -pi -e "s/^(\\s*hash = )\"sha256-[^\"]*\"(;)/\${1}\"$got_hash\"\${2}/" "$repo_root/flake.nix"
  if [ "$(grep -c "$got_hash" "$repo_root/flake.nix")" != 1 ]; then
    echo "nix-pnpm-hash: --write did not find exactly one pnpmDeps hash in $repo_root/flake.nix, left it untouched" >&2
    exit 1
  fi
  echo "nix-pnpm-hash: wrote $got_hash into $repo_root/flake.nix" >&2
fi

volume_size=$(docker system df -v 2>/dev/null | grep -F "$DOCKER_VOLUME" || true)
echo "nix-pnpm-hash: docker volume $DOCKER_VOLUME: ${volume_size:-not reported (run 'docker system df -v')}" >&2
