from typing import Any
from uuid import UUID

from pigrocrm.core.customers.schemas import CustomerCreate, CustomerListQuery, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_empty, report


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    service = CustomerService(context.session)
    return service.create(CustomerCreate(**data), context.actor).model_dump(mode="json")


def update(context: McpContext, customer_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = CustomerService(context.session)
    identifier = UUID(customer_id)
    changes = CustomerUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def get(context: McpContext, customer_id: str) -> dict[str, Any]:
    return (
        CustomerService(context.session)
        .get(UUID(customer_id), context.actor)
        .model_dump(mode="json")
    )


def search(context: McpContext, query: CustomerListQuery) -> dict[str, Any]:
    page = CustomerService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        # Already a string since slice 6 (an opaque `(sort value, id)` cursor), so no
        # `str()`: keeping one here would suggest the value is still a UUID being
        # rendered, which is exactly the assumption this change removes.
        "next_cursor": page.next_cursor,
    }


def archive(context: McpContext, customer_id: str) -> dict[str, str]:
    CustomerService(context.session).soft_delete(UUID(customer_id), context.actor)
    return {"status": "archiviato", "customer_id": customer_id}


def restore(context: McpContext, customer_id: str) -> dict[str, Any]:
    return (
        CustomerService(context.session)
        .restore(UUID(customer_id), context.actor)
        .model_dump(mode="json")
    )
