"""Customers: create, read, search, archive."""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm.core.customers.schemas import CustomerListQuery
from pigrocrm.core.db import SortDirection
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    customers,
    invoices,
)
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import BoundedLimit, CustomerChanges


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- customers -------------------------------------------------------

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_customer(
        ragione_sociale: str,
        partita_iva: str | None = None,
        codice_fiscale: str | None = None,
        codice_sdi: str | None = None,
        pec: str | None = None,
        indirizzo: str | None = None,
        cap: str | None = None,
        comune: str | None = None,
        provincia: str | None = None,
        email: str | None = None,
        telefono: str | None = None,
        note: str | None = None,
        giorni_pagamento: int | None = None,
        pagamento_fine_mese: bool = False,
        custom_fields: dict[str, Any] | None = None,
        nazione: str | None = None,
        azienda_id: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Crea un cliente.

        `owner_id` è chi risponde del cliente e riceve i promemoria su fatture e contratti (id in
        `list_members`): omesso, è chi crea; un altro lo indica solo un amministratore, come per
        `update_customer`.

        `giorni_pagamento` e `pagamento_fine_mese` sono i termini concordati («30 giorni data
        fattura fine mese» = `30` con `True`); da qui `issue_invoice` calcola la scadenza,
        altrimenti usa il profilo fiscale.

        `nazione`: ISO 3166-1 alpha-2 (`IT` se omessa). `azienda_id`: l'azienda che lo fattura
        (`list_aziende`); omessa, quella di `propose_azienda`.
        """
        return customers.create(
            context,
            {
                "ragione_sociale": ragione_sociale,
                "partita_iva": partita_iva,
                "codice_fiscale": codice_fiscale,
                "codice_sdi": codice_sdi,
                "pec": pec,
                "indirizzo": indirizzo,
                "cap": cap,
                "comune": comune,
                "provincia": provincia,
                "email": email,
                "telefono": telefono,
                "note": note,
                "giorni_pagamento": giorni_pagamento,
                "pagamento_fine_mese": pagamento_fine_mese,
                "custom_fields": custom_fields or {},
                **({"nazione": nazione} if nazione is not None else {}),
                "azienda_id": parse_legal_entity_id(azienda_id),
                "owner_id": owner_id,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def propose_azienda(nazione: str | None = None) -> dict[str, Any]:
        """L'azienda che fatturerebbe un nuovo cliente di `nazione` (ISO 3166-1 alpha-2, `IT` se
        omessa): la stessa che `create_customer` sceglie senza `azienda_id`. Chiedila per
        mostrarla alla persona prima di creare.
        """
        return invoices.propose_azienda(context, nazione)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_customer(customer_id: str, changes: CustomerChanges) -> dict[str, Any]:
        """Aggiorna un cliente. `owner_id` (da `list_members`) lo cambia solo un amministratore;
        `null` toglie il responsabile.
        """
        return customers.update(context, customer_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_customer(customer_id: str) -> dict[str, Any]:
        """Legge un cliente. Per il contesto completo usa la risorsa `customer://<id>`."""
        return customers.get(context, customer_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_customers(
        search: str | None = None,
        stato: str | None = None,
        custom: dict[str, Any] | None = None,
        azienda_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        # `str` and not `Literal["asc", "desc"]`, following the runtime-permissive /
        # schema-strict convention of `tools/params.py`: an out-of-range value then
        # raises `pydantic.ValidationError` inside `_guard`, which renders it as a
        # domain error the agent can read, instead of failing in the SDK's pre-call
        # `validate_arguments` where the message is not ours.
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Cerca clienti per ragione sociale, P.IVA, codice fiscale o email. `sort`: `created_at`,
        `updated_at`, `ragione_sociale`. `azienda_id` filtra per azienda.
        """
        return customers.search(
            context,
            CustomerListQuery(
                search=search,
                stato=stato,
                custom=custom,
                azienda_id=parse_legal_entity_id(azienda_id),
                # cast: limit is `int | str` at runtime for the SDK-bypass reason
                # documented on `BoundedLimit` in `tools/params.py`; the *ListQuery schema this
                # feeds is what actually enforces (and coerces) "must be an int".
                limit=cast(int, limit),
                # Since slice 6 the cursor is an opaque string, not a UUID: it encodes
                # `(sort value, id)`. Passing it through unparsed is the whole
                # contract -- `UUID(cursor)` here would raise a bare `ValueError` on
                # every `next_cursor` this tool itself just handed the agent. The
                # tool's own signature and JSON Schema are unchanged, so nothing an
                # agent sees moves. `list_invoices` and the time-tracking tools keep
                # their `UUID(cursor)`: those pages still key on the id alone.
                cursor=cursor,
                sort=sort,
                dir=cast(SortDirection, dir),
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_customer(customer_id: str) -> dict[str, str]:
        """Archivia un cliente (reversibile con `restore_customer`). Fallisce se ha deal attivi."""
        return customers.archive(context, customer_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_customer(customer_id: str) -> dict[str, Any]:
        """Ripristina un cliente archiviato."""
        return customers.restore(context, customer_id)
