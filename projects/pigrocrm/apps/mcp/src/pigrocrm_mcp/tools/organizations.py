from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from pigrocrm.core.engagements.registry import EngagementRegistry
from pigrocrm.core.organizations.schemas import (
    OrganizationCreate,
    OrganizationListQuery,
    OrganizationUpdate,
)
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.errors import refuse_removed_changes
from pigrocrm_mcp.tools import notes
from pigrocrm_mcp.tools.changes import refuse_empty, report


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    service = OrganizationService(context.session)
    return service.create(OrganizationCreate(**data), context.actor).model_dump(mode="json")


def update(context: McpContext, organization_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = OrganizationService(context.session)
    identifier = UUID(organization_id)
    notes.refuse_note_in_changes(data, "add_note")
    refuse_removed_changes("update_organization", data)
    changes = OrganizationUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def get(context: McpContext, organization_id: str) -> dict[str, Any]:
    identifier = UUID(organization_id)
    organization = OrganizationService(context.session).get(identifier, context.actor)
    return {
        **organization.model_dump(mode="json"),
        **notes.thread(context, "organization", identifier),
    }


def search(context: McpContext, query: OrganizationListQuery) -> dict[str, Any]:
    page = OrganizationService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        # Already a string since slice 6 (an opaque `(sort value, id)` cursor), so no
        # `str()`: keeping one here would suggest the value is still a UUID being
        # rendered, which is exactly the assumption this change removes.
        "next_cursor": page.next_cursor,
    }


def archive(context: McpContext, organization_id: str) -> dict[str, str]:
    OrganizationService(context.session).soft_delete(UUID(organization_id), context.actor)
    return {"status": "archiviato", "organization_id": organization_id}


def restore(context: McpContext, organization_id: str) -> dict[str, Any]:
    return (
        OrganizationService(context.session)
        .restore(UUID(organization_id), context.actor)
        .model_dump(mode="json")
    )


def duplicates(context: McpContext) -> dict[str, Any]:
    groups = OrganizationService(context.session).duplicates(context.actor)
    return {"groups": [group.model_dump(mode="json") for group in groups]}


@contextmanager
def _registry_of_space(context: McpContext) -> Iterator[EngagementRegistry | None]:
    """The registry's rows of the space this server serves, on a session of the registry's own
    (REB-1031). None for the root installation, which has none. A server built for a space
    without a way to the registry is a wiring bug, refused rather than left to point nothing."""
    if context.space is None:
        yield None
        return
    if context.registry_session is None:
        raise RuntimeError("the MCP server of a space was built without a registry session")
    with context.registry_session() as registry:
        yield EngagementRegistry(registry, context.space)


def merge(context: McpContext, survivor_id: str, loser_id: str, *, dry_run: bool) -> dict[str, Any]:
    with _registry_of_space(context) as registry:
        return (
            OrganizationService(context.session)
            .merge(
                UUID(survivor_id), UUID(loser_id), context.actor, dry_run=dry_run, registry=registry
            )
            .model_dump(mode="json")
        )
