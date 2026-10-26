"""What a contract's page reads to bill its days, and what it sends to do it (REB-751).

Kept apart from `schemas.py`, which is the day ledger's own: this is the invoicing side of
it, and the two grow for different reasons.
"""

from datetime import date
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class BillableLine(BaseModel):
    """One line the draft would carry: the days of one rate card in one month, priced at
    that card, exactly as `unbilled_work_unit_lines` builds it."""

    mese: str = Field(description="AAAA-MM")
    descrizione: str
    giornate: int = Field(description="Quante giornate copre la riga")
    quantita: Decimal
    unita_misura: str
    prezzo_unitario: Decimal
    importo: Decimal


class BillableDays(BaseModel):
    """The days of a contract that are ready to invoice, as the lines they would make.

    `senza_tariffa` lists the dates no rate card covers: the draft is refused while there
    is one (inventing a price is not this step's decision), and the page says which days to
    fix. `senza_approvazione` counts the days worked without the approval the contract
    requires, which are never gathered: they are the reason a month can look short.
    """

    contract_id: UUID
    divisa: str
    righe: list[BillableLine]
    mesi: list[str] = Field(description="I mesi AAAA-MM che hanno giornate pronte")
    giornate: int
    quantita: Decimal
    imponibile: Decimal
    senza_tariffa: list[date]
    senza_approvazione: int
    fatturabile: bool = Field(description="C'è almeno una riga e ogni giornata ha la sua tariffa")


class InvoiceDaysRequest(BaseModel):
    """Which days to put on the draft: every ready day, or those in `[da, a]`, both
    inclusive. A month is `da` the first and `a` the last."""

    da: date | None = None
    a: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.da is not None and self.a is not None and self.a < self.da:
            raise ValueError("a non può precedere da")
        return self
