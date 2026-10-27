"""People (contacts) of a customer."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.db import SortDirection
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    people,
)
from pigrocrm_mcp.tools.params import BoundedLimit, PersonChanges


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- people ----------------------------------------------------------

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_person(
        nome: str,
        cognome: str | None = None,
        email: str | None = None,
        telefono: str | None = None,
        ruolo: str | None = None,
        linkedin: str | None = None,
        note: str | None = None,
        customer_id: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Crea una persona. `customer_id` è opzionale: un contatto può non avere ancora un
        cliente.
        """
        return people.create(
            context,
            {
                "nome": nome,
                "cognome": cognome,
                "email": email,
                "telefono": telefono,
                "ruolo": ruolo,
                "linkedin": linkedin,
                "note": note,
                "customer_id": UUID(customer_id) if customer_id else None,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_person(person_id: str, changes: PersonChanges) -> dict[str, Any]:
        """Aggiorna una persona. Per staccarla dal cliente senza assegnarne uno nuovo passa
        `changes.detach = true` invece di `customer_id`.
        """
        return people.update(context, person_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_person(person_id: str) -> dict[str, Any]:
        """Legge una persona."""
        return people.get(context, person_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_people(
        search: str | None = None,
        customer_id: str | None = None,
        custom: dict[str, Any] | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        # `str`, not a Literal -- see `search_customers`.
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Cerca persone per nome, cognome o email, anche entro un cliente. `sort`: `created_at`,
        `updated_at`, `cognome`.
        """
        return people.search(
            context,
            PersonListQuery(
                search=search,
                customer_id=UUID(customer_id) if customer_id else None,
                custom=custom,
                # cast: limit is `int | str` at runtime for the SDK-bypass reason
                # documented on `BoundedLimit` in `tools/params.py`; the *ListQuery schema this
                # feeds is what actually enforces (and coerces) "must be an int".
                limit=cast(int, limit),
                # An opaque string since slice 6 -- see `search_customers`.
                cursor=cursor,
                sort=sort,
                dir=cast(SortDirection, dir),
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_person(person_id: str) -> dict[str, str]:
        """Archivia una persona (reversibile con `restore_person`)."""
        return people.archive(context, person_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_person(person_id: str) -> dict[str, Any]:
        """Ripristina una persona archiviata."""
        return people.restore(context, person_id)
