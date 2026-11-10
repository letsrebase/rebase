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

TAX_DATA_SAVED = {"tax_data": "saved"}


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def set_freelancer_tax_data(
        freelancer_id: str,
        tax_code: str,
        domicile: str,
        vat_number: str | None = None,
        certified_email: str | None = None,
    ) -> dict[str, Any]:
        """Saves a freelancer's tax data, the ones the contracts print: tax code (16 characters,
        or 11 digits for a sole proprietorship), domicile on one line, the VAT number (11
        digits; omitted or empty: none, for those who work with occasional services and sign
        only the occasional engagement letter) and, if there is one, the PEC (omitted or
        empty: none). Replaces what was there. The answer only says they are saved, never the
        values; the change log names the changed fields, never the values."""
        key = to_uuid(freelancer_id, "freelancer_id")
        try:
            data = FiscalData(
                tax_code=tax_code,
                vat_number=vat_number or None,
                domicile=domicile,
                certified_email=certified_email or None,
            )
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None
        deps.call(lambda session: FiscalService(session).save(key, data, deps.admin().id))
        return dict(TAX_DATA_SAVED)
