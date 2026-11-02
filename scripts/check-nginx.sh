#!/usr/bin/env bash
# `nginx -t` over every nginx config the repository carries, in the pinned nginx image:
# the container configs that the Dockerfiles copy in, the host vhosts under
# projects/*/deploy and the origin's shared files under infra/origin/nginx.
#
# Each file is tested on its own, the way the host or the container loads it. A vhost
# `include`s snippets that live on the host (/etc/nginx/snippets); the repository holds
# their source, so those are mounted from it, and a snippet that is only referenced
# (no source in the tree) gets an empty stub made here, at run time. TLS is not
# present in the committed vhosts (certbot adds it on the host), so no certificate is
# needed.
set -euo pipefail

IMAGE='nginx:1.31-alpine@sha256:785835ea65db4bba6f021acc0f3be91b0fef76a53fe41f8588c6698d2aed0606'
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Snippet name on the host -> the file in this repository that is its source.
declare -A SNIPPET_SOURCE=(
  [website-security-headers.conf]=projects/website/deploy/security-headers.conf
  [pigrocrm-security-headers.conf]=projects/pigrocrm/deploy/nginx/security-headers.conf
  [rebase-proxy.conf]=projects/pigrocrm/deploy/nginx/rebase-proxy.conf
)

# Fragments that are not a vhost on their own: the snippets themselves.
is_snippet() {
  case "$1" in
    */security-headers.conf | */rebase-proxy.conf) return 0 ;;
  esac
  return 1
}

prepare_snippets() {
  local dest="$1" name
  mkdir -p "$dest"
  for name in "${!SNIPPET_SOURCE[@]}"; do
    cp "${SNIPPET_SOURCE[$name]}" "$dest/$name"
  done
  # Any other snippet a conf names that has no source here: an empty stub.
  while IFS= read -r name; do
    [ -e "$dest/$name" ] || : > "$dest/$name"
  done < <(grep -hoE 'include /etc/nginx/snippets/[A-Za-z0-9._-]+' "${@:2}" 2>/dev/null | sed 's|.*/||' | sort -u)
}

fail=0
check() {
  # check LABEL FILE...  (files land together in conf.d/)
  local label="$1"
  shift
  local dir="$work/$label"
  mkdir -p "$dir/conf.d"
  local f
  for f in "$@"; do
    cp "$f" "$dir/conf.d/$(basename "$f")"
  done
  prepare_snippets "$dir/snippets" "$@"
  if out=$(docker run --rm \
    -v "$dir/conf.d:/etc/nginx/conf.d:ro" \
    -v "$dir/snippets:/etc/nginx/snippets:ro" \
    "$IMAGE" nginx -t 2>&1); then
    echo "ok   $label"
  else
    echo "FAIL $label"
    printf '%s\n' "$out"
    fail=1
  fi
}

# The origin's shared files form one set: the log format the default vhost uses.
check origin-default infra/origin/nginx/*.conf

shopt -s nullglob
for conf in projects/*/deploy/*.conf projects/*/deploy/nginx/*.conf; do
  is_snippet "$conf" && continue
  check "$(printf '%s' "$conf" | tr '/' '_')" "$conf"
done

exit "$fail"
