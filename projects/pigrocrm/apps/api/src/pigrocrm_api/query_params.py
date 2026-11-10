"""The query parameters the list routes share.

A list route declares one `*ListParams` model as `Annotated[..., Query()]`: FastAPI expands
it into the individual parameters of the OpenAPI document, so the wire contract is the same
as a hand-spelled signature, and the rules that were repeated per route (the `limit`
ceiling, the cursor bound, the NUL guard, the custom-field filter) live here once. FastAPI
accepts a query model only as the route's single query parameter, so each route's own
filters are fields of its subclass.

`ListParams.to_query` builds the service's `*ListQuery` from it. Validation happens here,
at the request boundary, where a failure is a 422; building the service model afterwards
cannot fail on anything this model already accepted.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.validation import SafeStr

# The most rows any list route answers in one page.
PAGE_LIMIT_MAX = 200
PAGE_LIMIT_DEFAULT = 50

LEGAL_ENTITY_QUERY_DESCRIPTION = "Only this legal entity's rows; omitted, all of them"

CUSTOM_QUERY_DESCRIPTION = "Filter on custom fields (JSONB), repeatable for several keys: "
"?custom=settore:IT&custom=priorita:alta returns only the rows that match both (same JSONB "
"containment semantics the service uses). Each value is compared as an exact string; a value "
"without the ':' separator counts as a key with an empty value and does not raise an error. Call "
"GET /api/schema/{entity_type} to learn the available keys."


def parse_custom_filter(raw: list[str] | None) -> dict[str, str] | None:
    """Turns a repeated `?custom=key:value` query parameter into the dict
    `OrganizationListQuery.custom`/`PersonListQuery.custom`/`DealListQuery.custom`
    already accept and pass straight through to the service's JSONB containment
    filter (`custom_fields.contains(...)`).

    `str.partition` never raises: an entry with no `:` becomes `(entry, "", "")`,
    so this returns `{entry: ""}` rather than a 500 from a malformed query
    string -- there is no single "correct" interpretation of a value with no
    key/value separator, so degrading to an empty-string value is the one
    behaviour that cannot itself become a new bug. The last occurrence of a
    repeated key wins, the same convention most `key=value`-shaped query
    parameters already follow.

    Deliberately shared across the three list routers (organizations, people,
    deals) rather than duplicated per domain, unlike the domain-specific
    services/repositories those routers call: this is adapter-only, purely
    mechanical string parsing with no business rule in it, the same class of
    thing `pigrocrm.core.db.search.escape_like` already is for its own layer.
    """
    if not raw:
        return None
    return dict(item.partition(":")[::2] for item in raw)


def sort_field(*keys: str) -> Any:
    """The `sort` field of a list that orders by the given keys. The admissible values go
    in the description and not in a `Literal`: the whitelist lives in the entity's
    `*_SORTS` and answers with this project's own `ValidationFailed`, whose `field` key the
    web client's `fieldErrorFrom` and an MCP agent both read, where a `Literal` would answer
    with FastAPI's `HTTPValidationError` shape, which neither parses."""
    return Field(default=None, description=" | ".join(keys))


class ListParams(BaseModel):
    limit: int = Field(default=PAGE_LIMIT_DEFAULT, ge=1, le=PAGE_LIMIT_MAX)

    def to_query[Q: BaseModel](self, query_type: type[Q], **extra: Any) -> Q:
        """The service's list query with the same field values, plus `extra` for what the
        route takes from its path. A `custom` filter, if the route has one, goes through
        `parse_custom_filter` on the way."""
        values = self.model_dump()
        if "custom" in values:
            values["custom"] = parse_custom_filter(values["custom"])
        return query_type(**values, **extra)


class KeysetParams(ListParams):
    """A list that pages by id alone, so its cursor is the last row's id and nothing a
    client could not forge: `UUID`, which the services take as such."""

    cursor: UUID | None = None


class SortedParams(ListParams):
    """A list with an ordering. The cursor encodes `(sort value, id)`, so that a scan
    ordered by a non-unique or nullable column can resume: opaque by design, clients echo
    `next_cursor` back and never parse it. `max_length` is here as well as on the schema,
    so an oversized value is refused before the decoder sees it."""

    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class SearchParams(BaseModel):
    # SafeStr here and not only on the schemas: a NUL byte in a query parameter is caught
    # as a clean 422 by the request validation, before the service query is built.
    search: SafeStr | None = None


class CustomParams(BaseModel):
    custom: list[SafeStr] | None = Field(default=None, description=CUSTOM_QUERY_DESCRIPTION)
