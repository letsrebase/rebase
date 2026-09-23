from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.validation import SafeStr

# Mirrors Contract's column widths (models.py). Without these, an over-length value
# sails past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (StringDataRightTruncation) -- not a subclass of IntegrityError, so no handler
# catches it, and it poisons the session. The same gap CustomerCreate's/DealCreate's
# own *_MAX_LENGTH constants close.
TITOLO_MAX_LENGTH = 255
CADENZA_FATTURAZIONE_MAX_LENGTH = 20
DIVISA_MAX_LENGTH = 3

RenewalType = Literal["nessuno", "esplicito", "opzione_controparte", "tacito"]
RateCardTipo = Literal["ricorrente_fisso", "giornaliero", "orario", "una_tantum"]
RateCardUnita = Literal["ora", "giorno", "mese", "anno", "forfait"]
RateCardPeriodo = Literal["mensile", "trimestrale", "annuale", "una_tantum"]

# Mirror RateCard's Numeric(p, s) column widths (models.py): importo is
# Numeric(12, 2), the same precision as every other money column on this schema;
# ore_minime is Numeric(6, 2); each element of frazioni_ammesse is Numeric(4, 2).
IMPORTO_MAX_DIGITS = 12
ORE_MINIME_MAX_DIGITS = 6
FRAZIONE_MAX_DIGITS = 4
DECIMAL_PLACES = 2

Frazione = Annotated[Decimal, Field(max_digits=FRAZIONE_MAX_DIGITS, decimal_places=DECIMAL_PLACES)]


class ContractCreate(BaseModel):
    """`contratto_precedente_id` and `stato` are deliberately absent: the renewal
    chain is left unset by this spike (spec §3's own note), and a new contract
    always starts life as `bozza` -- there is no state-transition endpoint yet for
    either to reach through."""

    customer_id: UUID
    titolo: SafeStr = Field(max_length=TITOLO_MAX_LENGTH)
    inizio: date
    fine: date | None = None
    tipo_rinnovo: RenewalType
    preavviso_rinnovo_giorni: int | None = Field(default=None, ge=0)
    preavviso_disdetta_giorni: int = Field(ge=0)
    # Reuses Customer.giorni_pagamento's own shape (REB-326): null cascades to the
    # customer's own term. See models.py for why pagamento_fine_mese is nullable
    # here, unlike Customer's own copy.
    giorni_pagamento: int | None = Field(default=None, ge=0)
    pagamento_fine_mese: bool | None = None
    cadenza_fatturazione: SafeStr = Field(max_length=CADENZA_FATTURAZIONE_MAX_LENGTH)
    divisa: SafeStr = Field(default="EUR", max_length=DIVISA_MAX_LENGTH)
    requires_prior_approval: bool = False
    applies_social_charge: bool = False
    # mastro's ExpensePolicy (contract.ts:90-99), kept as an open tagged JSONB union
    # -- see models.py's Contract.politica_spese for why this is not deeply
    # validated here.
    politica_spese: dict[str, Any]
    note: SafeStr | None = None
    custom_fields: dict[str, Any] = {}

    @model_validator(mode="after")
    def _fine_not_before_inizio(self) -> "ContractCreate":
        if self.fine is not None and self.fine < self.inizio:
            raise ValueError("fine non può precedere inizio")
        return self


class ContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    titolo: str
    inizio: date
    fine: date | None
    tipo_rinnovo: str
    preavviso_rinnovo_giorni: int | None
    preavviso_disdetta_giorni: int
    giorni_pagamento: int | None
    pagamento_fine_mese: bool | None
    cadenza_fatturazione: str
    divisa: str
    requires_prior_approval: bool
    applies_social_charge: bool
    politica_spese: dict[str, Any]
    stato: str
    contratto_precedente_id: UUID | None
    note: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# Residuo R9. Mirrors CUSTOMER_SORTS (customers/schemas.py) -- see there for why three
# keys and why `created_at` is the default.
CONTRACT_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Contract.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Contract.updated_at, kind="datetime", nullable=False),
        SortSpec(key="titolo", column=Contract.titolo, kind="text", nullable=False),
    ),
    default_key="created_at",
)


class ContractListQuery(BaseModel):
    customer_id: UUID | None = None
    stato: str | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class ContractPage(BaseModel):
    items: list[ContractRead]
    next_cursor: str | None


class RateCardCreate(BaseModel):
    valido_da: date
    valido_a: date | None = None
    tipo: RateCardTipo
    importo: Decimal = Field(max_digits=IMPORTO_MAX_DIGITS, decimal_places=DECIMAL_PLACES)
    unita: RateCardUnita
    frazioni_ammesse: list[Frazione] = Field(default_factory=lambda: [Decimal("1")])
    # Meaningful, and refused outside it, only for tipo == "orario" -- see
    # ck_rate_cards_ore_minime_only_orario.
    ore_minime: Decimal | None = Field(
        default=None, max_digits=ORE_MINIME_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    # Meaningful, and refused outside it, only for tipo == "ricorrente_fisso" -- see
    # ck_rate_cards_periodo_only_ricorrente_fisso.
    periodo_erogazione: RateCardPeriodo | None = None

    @model_validator(mode="after")
    def _valido_a_not_before_valido_da(self) -> "RateCardCreate":
        # Also DB-enforced (ck_rate_cards_validity_ordered), but that CHECK fires
        # inside the same flush the exclusion constraint does, and
        # RateCardService.create's own except IntegrityError cannot tell the two
        # apart -- an inverted range would otherwise be misreported as an overlap
        # conflict. Catching it here, before the row ever reaches the database,
        # is what keeps the two failures distinguishable.
        if self.valido_a is not None and self.valido_a < self.valido_da:
            raise ValueError("valido_a non può precedere valido_da")
        return self


class RateCardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    valido_da: date
    valido_a: date | None
    tipo: str
    importo: Decimal
    unita: str
    frazioni_ammesse: list[Decimal]
    ore_minime: Decimal | None
    periodo_erogazione: str | None
    created_at: datetime
    updated_at: datetime


class ContractConcentrationCap(BaseModel):
    """REB-352 §1.5's contract-anchored concentration cap: one engagement's own
    share of total invoiced revenue over the anniversary year containing `as_of`,
    read the same way `annual_revenue`/`_revenue_filter` already define revenue --
    mastro's `percentage_share` measure, on its `cash_received_contract_year` basis
    (`ceiling.ts:55-75, 197-233`), anchored to `Contract.inizio` instead of a
    ledger row.

    `soglia` is never persisted: no threshold column exists on `contracts` yet (a
    future ceiling entity's own concern, REB-361), so a caller names the share it
    wants checked against, per REB-352 §5 item 4's "would this fit" reading -- a
    live comparison with no persistence required. `superata` mirrors mastro's own
    `crossed` (`ceiling.ts:257`) and stays `None` until a `soglia` is supplied.
    """

    contract_id: UUID
    customer_id: UUID
    periodo_da: date
    periodo_a: date
    ricavi_cliente: Decimal = Field(max_digits=12, decimal_places=2)
    ricavi_totali: Decimal = Field(max_digits=12, decimal_places=2)
    # The client's share of `ricavi_totali`, in [0, 1] -- 0.0 when nothing has been
    # invoiced yet in the period, the same "zero when there is nothing to scale
    # against" reading every other `quota` field on this schema already carries
    # (`dashboard/schemas.py`'s `EsposizioneCliente.quota`, `_quota`).
    quota: float
    soglia: float | None = None
    superata: bool | None = None


# ---- renewal assumptions and the projected figure (REB-375) -----------------------


class RenewalAssumptionUpsert(BaseModel):
    """A contract's own recorded belief about revenue beyond its known term -- mastro's
    `RenewalAssumption` (`certainty.ts:133-147`). `probabilita` mirrors
    `Deal.probabilita`'s own 0-100 integer shape (deals/models.py), not a 0-1 fraction:
    the same percentage concept already has one representation on this schema."""

    probabilita: int = Field(ge=0, le=100)
    volume_atteso: Decimal = Field(
        ge=0, max_digits=IMPORTO_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    orizzonte_al: date


class RenewalAssumptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    probabilita: int
    volume_atteso: Decimal
    orizzonte_al: date
    created_at: datetime
    updated_at: datetime


class ContractProjectionQuery(BaseModel):
    """The window `ContractProjectionService.project` reads: `[da, a)`, half-open --
    an occurrence dated exactly `a` belongs to the next window, matching
    `PeriodPnlQuery`'s own convention (analytics/schemas.py). `come_di` is the date
    a termination notice is assumed served on; `None` reads as "today", resolved by
    the service and never by this schema (packages/core's own single clock,
    db/clock.py)."""

    da: date
    a: date
    come_di: date | None = None

    @model_validator(mode="after")
    def _a_after_da(self) -> "ContractProjectionQuery":
        if self.a <= self.da:
            raise ValueError("a deve essere successiva a da")
        return self


class ContractProjectionRead(BaseModel):
    """Distinct from, and never combined with, `CashOverview.proiettato`
    (analytics/schemas.py): that figure is drafts and proformas already in the
    system, this one is a contract's own recurring-fee schedule plus its own
    recorded renewal assumption -- the Done-when criterion's "genuine 'projected'
    figure"."""

    contract_id: UUID
    come_di: date
    da: date
    a: date
    finestra_irrevocabilita_fino_al: date | None
    programmato: Decimal
    da_rinnovo: Decimal
    totale: Decimal
