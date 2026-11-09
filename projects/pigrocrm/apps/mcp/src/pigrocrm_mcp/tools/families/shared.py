"""Pipeline stages, the timeline of any entity and the global search."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.search.schemas import PER_CLASS_LIMIT, SearchQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import search as search_tools
from pigrocrm_mcp.tools.params import BoundedLimit, SearchLimit, parse_legal_entity_id


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- shared ------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_pipeline_stages() -> dict[str, Any]:
        """List the pipeline stages, with the type (open/won/lost)."""
        stages = PipelineService(context.session).list()
        return {"stages": [stage.model_dump(mode="json") for stage in stages]}

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_timeline(
        entity_type: EntityType, entity_id: str, limit: BoundedLimit = 50
    ) -> dict[str, Any]:
        """Timeline of an entity: for each entry who, when and from where. `actor_type`: `user` from
        the web app, `mcp` from an agent using the member's token, `rebase`, `system` from an
        automation (which names whoever triggered it in `triggered_by_name`).
        """
        entries = ActivityService(context.session).timeline(
            entity_type, UUID(entity_id), limit=cast(int, limit)
        )
        return {"entries": [entry.model_dump(mode="json") for entry in entries]}

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_everything(
        term: str, limit: SearchLimit = PER_CLASS_LIMIT, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Search the whole CRM in one call: organizations (company name, VAT number, tax code,
        contact points), people (first name, last name, contact points), deals (name),
        documents (title), invoices (description, number) and tasks (title and notes, closed
        ones too). It also accepts a fragment in the middle of a word («34567» finds the VAT
        number 01234567890). An invoice number is written «2026/7», «7/2026» or «007»: it finds
        that invoice; without a year, in every year.

        At least 3 characters are required. It returns up to `limit` results per class and the real
        count; if `total_is_minimum` is true the count is a minimum and the rest is in the
        entity's own list. `legal_entity_id` restricts organizations, people (via the customer), deals,
        documents and invoices to that company, but not tasks, which have none; if omitted, it
        searches the whole space. It does not search contracts, costs, proposals or mail.
        """
        # `term` is a bare `str` with no `Annotated` bound, following the
        # runtime-permissive / schema-only-strict convention of `tools/params.py`: the
        # length check happens inside `SearchQuery`, inside the guarded call, so a
        # two-character term produces a domain error the agent can act on instead of an SDK
        # rejection whose wording is not ours.
        return search_tools.search_everything(
            context,
            SearchQuery(
                term=term,
                limit=cast(int, limit),
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
            ),
        )
