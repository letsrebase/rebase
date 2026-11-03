"""Which space a request belongs to, read from the first segment of its path.

`/<slug>/api/...` and `/<slug>/health` are a space's requests to the API;
`/<slug>/mcp` is a space's request to the MCP server. Both adapters strip the prefix so
that their routes know nothing about tenants, and both apply the same three rules: a
reserved word is not a slug, a malformed segment is not a slug, and the root
installation's own name (`PIGROCRM_ROOT_SLUG`) is stripped like a space's but stays the
root. One function, parametrised on the segments an adapter serves, so the two cannot
drift -- this used to live in `apps/api/tenancy.py` alone.
"""

import re
from functools import lru_cache

from pigrocrm.core.tenants.schemas import RESERVED_SLUGS, SLUG_PATTERN

API_SEGMENTS: tuple[str, ...] = ("api", "health")
MCP_SEGMENTS: tuple[str, ...] = ("mcp",)


@lru_cache(maxsize=8)
def _pattern(segments: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(segment) for segment in segments)
    return re.compile(rf"^/([a-z0-9][a-z0-9-]{{1,30}}[a-z0-9])(/(?:{alternatives})(?:/.*)?)$")


def split_tenant_prefix(
    path: str, root_slug: str = "", segments: tuple[str, ...] = API_SEGMENTS
) -> tuple[str | None, str]:
    """`("studio", "/api/customers")` for `/studio/api/customers`; `(None, path)` when
    there is no prefix, or when the would-be slug is a reserved word -- `/api/...` is
    never anybody's space, and `/app/...` never reaches either adapter.

    `root_slug` is the root installation's own name: its prefix is stripped like a
    space's, but the request stays the root's -- `(None, rest)` -- so it opens the root
    database with the root's settings, Gmail and Drive included."""
    match = _pattern(segments).match(path)
    if not match:
        return None, path
    slug, rest = match.group(1), match.group(2)
    if root_slug and slug == root_slug:
        return None, rest
    if slug in RESERVED_SLUGS or not SLUG_PATTERN.fullmatch(slug):
        return None, path
    return slug, rest
