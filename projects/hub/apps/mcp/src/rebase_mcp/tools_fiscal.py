"""The freelancer's tax data, the one tool whose answer never carries the values it saved."""

from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError as PydanticValidationError

from rebase_core.contract_schemas import FiscalData
from rebase_core.fiscal import FiscalService
from rebase_mcp.tools_common import (
    Deps,
    field_refusal,
    to_uuid,
)

TAX_DATA_SAVED = {"dati_fiscali": "salvati"}


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def set_freelancer_tax_data(
        freelancer_id: str,
        codice_fiscale: str,
        domicilio: str,
        partita_iva: str | None = None,
        pec: str | None = None,
    ) -> dict[str, Any]:
        """Salva i dati fiscali di un freelance, quelli che i contratti stampano: codice
        fiscale (16 caratteri, o 11 cifre per una ditta), domicilio su una riga, la partita
        IVA (11 cifre; omessa o vuota: nessuna, per chi lavora con prestazioni occasionali
        e firma solo la lettera di incarico occasionale) e, se c'è, la PEC (omessa o vuota:
        nessuna). Sostituisce quelli che c'erano. Risponde solo che sono salvati, mai i
        valori; il registro delle modifiche nomina i campi cambiati, mai i valori."""
        key = to_uuid(freelancer_id, "freelancer_id")
        try:
            data = FiscalData(
                codice_fiscale=codice_fiscale,
                partita_iva=partita_iva or None,
                domicilio=domicilio,
                pec=pec or None,
            )
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None
        deps.call(lambda session: FiscalService(session).save(key, data, deps.admin().id))
        return dict(TAX_DATA_SAVED)
