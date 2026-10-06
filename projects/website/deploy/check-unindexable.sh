#!/usr/bin/env sh
# Proves every host that must stay out of search still does, against the live
# names rather than the vhost files that only promise it (REB-108). Nothing ran
# this before: the day an edit to preview.letsrebase.conf,
# preview.pigro.letsrebase.conf or pigro.letsrebase.conf drops the header or the
# robots.txt location, the first sign would otherwise be a preview URL in a
# search result.
#
# Both preview names are open since REB-103 (docs/adding-a-project.md §7, "A
# preview name is open"): there is no basic auth to authenticate past any more,
# so this checks only what is left -- the header and robots.txt -- and never
# reads a credential. `-q` is the first curl argument on every call so a stray
# ~/.curlrc cannot reintroduce one, and cannot silently turn `-L` on either: with
# a redirect followed, `-D -` dumps every hop's headers into one stream, and two
# of these three hosts answer `/` with a 302, so a follow would let a noindex
# header anywhere in the chain pass a host that itself sends none.
#
# `pigro.letsrebase.com` is the CRM's production host and only carries the
# header once REB-107's vhost is installed there (same wave, PR #130); until
# then this check fails on that one host on purpose, which is the point of
# running it rather than assuming the install happened. That means a push
# touching one of the four vhost files this selects on is red until REB-107
# deploys -- known and accepted for that short window, not a reason to make the
# assertion advisory: a check that never fails is not proof of anything.
#
# Reaches live hosts over HTTPS, so it runs from a developer machine or from
# `.github/preflight.json` -- a GitHub-hosted runner has nothing to prove here --
# never as a `pull_request` or `push` job. `CHECK_UNINDEXABLE_HOSTS` and
# `CHECK_UNINDEXABLE_SCHEME` override the real names for exactly one reason:
# proving this fails on a responder that omits the header, without touching a
# live host to do it. Refused under preflight (`PREFLIGHT=1`) so a variable left
# set in a shell cannot silently turn a real run into a check of localhost that
# still reports green, e.g.
#
#   python3 -m http.server 8000 &
#   CHECK_UNINDEXABLE_SCHEME=http CHECK_UNINDEXABLE_HOSTS=localhost:8000 \
#     sh projects/website/deploy/check-unindexable.sh
#
#   sh projects/website/deploy/check-unindexable.sh
set -euf

if [ "${PREFLIGHT:-}" = "1" ] && { [ -n "${CHECK_UNINDEXABLE_HOSTS:-}" ] || [ -n "${CHECK_UNINDEXABLE_SCHEME:-}" ]; }; then
  echo "check-unindexable: CHECK_UNINDEXABLE_* is a self-test override and must not be set under preflight" >&2
  exit 2
fi

SCHEME="${CHECK_UNINDEXABLE_SCHEME:-https}"
HOSTS="${CHECK_UNINDEXABLE_HOSTS:-preview.letsrebase.com preview.pigro.letsrebase.com pigro.letsrebase.com}"
STATUS=0
CHECKED=0

check_host() {
  host="$1"
  CHECKED=$((CHECKED + 1))

  headers="$(curl -q -sS -o /dev/null -D - --max-time 10 "${SCHEME}://${host}/")" || {
    echo "FAIL ${host}: could not reach ${SCHEME}://${host}/"
    STATUS=1
    return
  }
  # `[^:]*` between the field name and the value rejects a directive scoped to one
  # crawler (`X-Robots-Tag: googlebot: noindex`), which binds that bot only.
  if printf '%s\n' "$headers" | grep -qi '^x-robots-tag:[^:]*noindex'; then
    echo "PASS ${host}: X-Robots-Tag carries noindex on /"
  else
    echo "FAIL ${host}: no unscoped X-Robots-Tag: noindex on ${SCHEME}://${host}/"
    STATUS=1
  fi

  robots_body="$(mktemp)"
  code="$(curl -q -sS -o "$robots_body" -w '%{http_code}' --max-time 10 "${SCHEME}://${host}/robots.txt")" || {
    echo "FAIL ${host}: could not reach ${SCHEME}://${host}/robots.txt"
    STATUS=1
    rm -f "$robots_body"
    return
  }
  # Both directive names are case-insensitive (RFC 9309) and asserted as a pair:
  # `Disallow: /` alone can sit under a `User-agent: googlebot` group while a
  # `User-agent: *` group two lines down allows everything.
  if [ "$code" = "200" ] \
    && grep -qiE '^user-agent:[[:space:]]*\*[[:space:]]*$' "$robots_body" \
    && grep -qiE '^disallow:[[:space:]]*/[[:space:]]*$' "$robots_body"; then
    echo "PASS ${host}: robots.txt disallows everything for every crawler"
  else
    echo "FAIL ${host}: robots.txt at ${SCHEME}://${host}/robots.txt answered ${code} or did not disallow / for *"
    STATUS=1
  fi
  rm -f "$robots_body"
}

echo "check-unindexable: ${SCHEME} -> ${HOSTS}"
for host in $HOSTS; do
  check_host "$host"
done

if [ "$CHECKED" -eq 0 ]; then
  echo "FAIL: no hosts to check (CHECK_UNINDEXABLE_HOSTS is empty or blank)"
  exit 1
fi

exit "$STATUS"
