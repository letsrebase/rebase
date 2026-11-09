"""The thin half. Everything that matters happened in `SearchService`.

Shaped exactly like `tools/customers.py`: resolve the service on the context's session, call
it, `model_dump(mode="json")`.

What keeps the score out of an agent's context as `0.6000000238418579` is
`SearchHit.score` being a `Decimal` and not a `float` -- measured, not assumed:
swapping `mode="json"` for the default here leaves every assertion in
`apps/mcp/tests/test_mcp_search.py` green, because the SDK's own encoder renders a
`Decimal` and a `UUID` as strings anyway. `mode="json"` stays because every other tool
module in this package spells it, and one module that dumps in a different mode is a
difference a reader has to stop and account for; it is consistency, not the safeguard.
The safeguard is the schema type, and the test that would catch its loss says so.
"""

from typing import Any

from pigrocrm.core.search.schemas import SearchQuery
from pigrocrm.core.search.service import SearchService
from pigrocrm_mcp.context import McpContext


def search_everything(context: McpContext, query: SearchQuery) -> dict[str, Any]:
    return (
        SearchService(context.session)
        .search_everything(query, context.actor, modules=context.modules)
        .model_dump(mode="json")
    )
