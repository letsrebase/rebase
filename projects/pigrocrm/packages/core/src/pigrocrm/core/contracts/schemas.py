from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.renewal import RenewalUnit
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
ContractState = Literal["bozza", "attivo", "terminato", "scaduto"]
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

# `tetto_quota_cliente` mirrors `Contract.tetto_quota_cliente`'s Numeric(5, 2): a
# percentage strictly between 0 and 100 (the CHECK says why), two decimals as the rates on
# `FiscalProfile` have.
TETTO_MAX_DIGITS = 5

EXPENSE_POLICY_KINDS: tuple[str, ...] = (
    "non_rimborsabile",
    "rimborsabile",
    "rimborsabile_con_tetto",
)


def check_expense_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """The tagged shape `contract_expenses/triggers.py` reads: `kind` is one of three,
    `importo_tetto` rides on the capped one only and is a non-negative decimal there,
    `richiede_preautorizzazione` is a boolean when present. A document the trigger
    cannot read would make every later expense write on the contract fail with a
    `check_violation`, so a write that would store one is refused here."""
    kind = policy.get("kind")
    if kind not in EXPENSE_POLICY_KINDS:
        raise ValueError(
            f"politica_spese.kind deve essere uno fra {', '.join(EXPENSE_POLICY_KINDS)}"
        )
    cap = policy.get("importo_tetto")
    if kind == "rimborsabile_con_tetto":
        try:
            valid = cap is not None and Decimal(str(cap)) >= 0
        except InvalidOperation:
            valid = False
        if not valid:
            raise ValueError(
                "politica_spese.importo_tetto è obbligatorio per rimborsabile_con_tetto "
                "e va scritto come importo, per esempio 500.00"
            )
    elif cap is not None:
        raise ValueError("politica_spese.importo_tetto vale solo per rimborsabile_con_tetto")
    pre = policy.get("richiede_preautorizzazione")
    if pre is not None and not isinstance(pre, bool):
        raise ValueError("politica_spese.richiede_preautorizzazione deve essere vero o falso")
    return policy


Frazione = Annotated[Decimal, Field(max_digits=FRAZIONE_MAX_DIGITS, decimal_places=DECIMAL_PLACES)]


class ContractCreate(BaseModel):
    """`contratto_precedente_id` and `stato` are deliberately absent: the renewal
    chain is written only by `ContractService.renew`, and a new contract always starts
    life as `bozza` -- `ContractService.set_state` is what moves it on."""

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
    # REB-761: the share of the azienda's invoiced revenue this customer must not reach
    # over the contract's own anniversary year, as a percentage (`50.00`). Absent, no cap.
    tetto_quota_cliente: Decimal | None = Field(
        default=None, max_digits=TETTO_MAX_DIGITS, decimal_places=DECIMAL_PLACES, gt=0, lt=100
    )
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


class ContractUpdate(BaseModel):
    """A partial patch of everything `ContractCreate` writes except the customer, which
    a contract never changes. `stato` is absent: it moves through `ContractStateChange`
    only, one named act (`set_state`) instead of a field any edit could flip. The checks
    that span fields (payment terms together, a renewal notice for every renewal type,
    `fine` not before `inizio`) run against the *merged* row in
    `ContractService.update`, since one call may touch either field of a pair alone."""

    model_config = ConfigDict(extra="forbid")

    titolo: SafeStr | None = Field(default=None, max_length=TITOLO_MAX_LENGTH)
    inizio: date | None = None
    fine: date | None = None
    tipo_rinnovo: RenewalType | None = None
    preavviso_rinnovo_giorni: int | None = Field(default=None, ge=0)
    preavviso_disdetta_giorni: int | None = Field(default=None, ge=0)
    giorni_pagamento: int | None = Field(default=None, ge=0)
    pagamento_fine_mese: bool | None = None
    cadenza_fatturazione: SafeStr | None = Field(
        default=None, max_length=CADENZA_FATTURAZIONE_MAX_LENGTH
    )
    divisa: SafeStr | None = Field(default=None, max_length=DIVISA_MAX_LENGTH)
    requires_prior_approval: bool | None = None
    applies_social_charge: bool | None = None
    tetto_quota_cliente: Decimal | None = Field(
        default=None, max_digits=TETTO_MAX_DIGITS, decimal_places=DECIMAL_PLACES, gt=0, lt=100
    )
    politica_spese: dict[str, Any] | None = None
    note: SafeStr | None = None
    custom_fields: dict[str, Any] | None = None

    @field_validator("politica_spese")
    @classmethod
    def _expense_policy_is_readable(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else check_expense_policy(value)


class RenewalDuration(BaseModel):
    """How long the renewed term lasts, counted from its first day: `{"conteggio": 12,
    "unita": "mesi"}` from 1 January ends on 31 December."""

    model_config = ConfigDict(extra="forbid")

    conteggio: int = Field(ge=1, le=1200)
    unita: RenewalUnit


class ContractRenew(BaseModel):
    """The renewal of a contract (REB-763): its terms stay and its dates move. All three
    fields are optional, and with none the successor starts the day after the contract
    ends and lasts as long as it did. `fine` and `durata` are two ways to say the end,
    so naming both is refused instead of one silently winning."""

    model_config = ConfigDict(extra="forbid")

    inizio: date | None = None
    fine: date | None = None
    durata: RenewalDuration | None = None
    titolo: SafeStr | None = Field(default=None, min_length=1, max_length=TITOLO_MAX_LENGTH)

    @model_validator(mode="after")
    def _one_way_to_say_the_end(self) -> "ContractRenew":
        if self.fine is not None and self.durata is not None:
            raise ValueError("fine e durata sono due modi di dire la stessa cosa: indicane uno")
        return self


class ContractStateChange(BaseModel):
    """The one way a contract's `stato` changes. Any state to any other, never to the
    state it already has: mastro's own editor offers the whole select, and a contract
    whose term passed stays `attivo` until somebody says otherwise (nothing here runs
    on a timer)."""

    model_config = ConfigDict(extra="forbid")

    stato: ContractState


class ContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    # The customer's azienda when the contract was created (REB-623); read-only.
    azienda_id: UUID
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
    tetto_quota_cliente: Decimal | None
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
    azienda_id: UUID | None = None
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


class RateCardUpdate(BaseModel):
    """A partial patch of a rate card. The conditional shape (`ore_minime` only for
    `orario`, `periodo_erogazione` only for `ricorrente_fisso`) and the validity order
    are checked against the *merged* row by `RateCardService.update`; the overlap is
    the database's, as on create."""

    model_config = ConfigDict(extra="forbid")

    valido_da: date | None = None
    valido_a: date | None = None
    tipo: RateCardTipo | None = None
    importo: Decimal | None = Field(
        default=None, max_digits=IMPORTO_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    unita: RateCardUnita | None = None
    frazioni_ammesse: list[Frazione] | None = None
    ore_minime: Decimal | None = Field(
        default=None, max_digits=ORE_MINIME_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    periodo_erogazione: RateCardPeriodo | None = None


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


class ConcentrationFigures(BaseModel):
    """One currency's side of a concentration cap: the customer's invoiced revenue and
    the azienda's total, both in `divisa` and never converted, with the share one is of
    the other. A customer billed in euros on a USD contract is a `EUR` row, not a figure
    labelled with the contract's own currency (REB-776)."""

    divisa: str
    ricavi_cliente: Decimal = Field(max_digits=12, decimal_places=2)
    ricavi_totali: Decimal = Field(max_digits=12, decimal_places=2)
    # The client's share of `ricavi_totali`, in [0, 1] -- 0.0 when nothing has been
    # invoiced yet in the period, the same "zero when there is nothing to scale
    # against" reading every other `quota` field on this schema already carries
    # (`dashboard/schemas.py`'s `EsposizioneCliente.quota`, `_quota`).
    quota: float
    superata: bool | None = None


class ContractConcentrationCap(BaseModel):
    """REB-352 §1.5's contract-anchored concentration cap: one engagement's own
    share of total invoiced revenue over the anniversary year containing `as_of`,
    read the same way `annual_revenue`/`_revenue_filter` already define revenue --
    mastro's `percentage_share` measure, on its `cash_received_contract_year` basis
    (`ceiling.ts:55-75, 197-233`), anchored to `Contract.inizio` instead of a
    ledger row.

    The figures are per currency (`valute`), since an invoice's `divisa` is its own and
    nothing here converts: a share of euros of dollars is not a number. A window with no
    invoice at all answers one row in the contract's currency, all zero, so a reader
    never gets an empty list for a quiet year.

    `soglia` is the share each row is compared with, in [0, 1]. A caller may name one,
    per REB-352 §5 item 4's "would this fit" reading: a live comparison, never stored.
    Without one it is the contract's own cap (`Contract.tetto_quota_cliente`, REB-761,
    divided by 100), and `soglia_da_contratto` says so, so a reader can tell the clause
    from a figure somebody tried. Each row's `superata` mirrors mastro's own `crossed`
    (`ceiling.ts:257`: the share reaching the cap) and stays `None` when neither a
    `soglia` nor a cap exists.
    """

    contract_id: UUID
    customer_id: UUID
    periodo_da: date
    periodo_a: date
    soglia: float | None = None
    soglia_da_contratto: bool = False
    valute: list[ConcentrationFigures]


class ContractCapSimulation(BaseModel):
    """REB-761: the «ci sta?» simulator's answer for one contract's cap. The customer's
    share of the azienda's invoiced revenue over the contract's own anniversary year
    containing `data_prevista`, before and after a synthetic addition that goes entirely
    to this customer: both the customer's revenue and the total grow by it, which is why
    the share moves less than the addition. In the contract's own currency, since a cap
    compares one currency with itself (REB-776). `rientra` is the share staying under the
    cap, which it must not reach: the same reading `ConcentrationFigures.superata` has.
    """

    contract_id: UUID
    titolo: str
    customer_id: UUID
    divisa: str
    periodo_da: date
    periodo_a: date
    # The contract's cap as a share in [0, 1] (`tetto_quota_cliente` / 100).
    tetto: float
    ricavi_cliente_attuali: Decimal = Field(max_digits=12, decimal_places=2)
    ricavi_totali_attuali: Decimal = Field(max_digits=12, decimal_places=2)
    quota_attuale: float
    ricavi_cliente_simulati: Decimal = Field(max_digits=12, decimal_places=2)
    ricavi_totali_simulati: Decimal = Field(max_digits=12, decimal_places=2)
    quota_simulata: float
    rientra: bool


# ---- clause notes (REB-762) ---------------------------------------------------------

RIFERIMENTO_CLAUSOLA_MAX_LENGTH = 200
CLAUSE_TEXT_MAX_LENGTH = 20_000


def _not_blank(value: str | None) -> str | None:
    """A reading made of spaces is no reading: `min_length` counts them."""
    if value is not None and not value.strip():
        raise ValueError("non può essere vuoto")
    return value


class ClauseNoteCreate(BaseModel):
    """A reading of one clause: where it is (`riferimento_clausola`, «Art. 8.3 Rinnovo»),
    what it says word for word (`testo_letterale`) and what the person decided it means
    (`interpretazione_adottata`). `note` is anything else worth keeping beside them."""

    model_config = ConfigDict(extra="forbid")

    riferimento_clausola: SafeStr = Field(min_length=1, max_length=RIFERIMENTO_CLAUSOLA_MAX_LENGTH)
    testo_letterale: SafeStr = Field(min_length=1, max_length=CLAUSE_TEXT_MAX_LENGTH)
    interpretazione_adottata: SafeStr = Field(min_length=1, max_length=CLAUSE_TEXT_MAX_LENGTH)
    note: SafeStr | None = Field(default=None, max_length=CLAUSE_TEXT_MAX_LENGTH)

    _required_text_is_not_blank = field_validator(
        "riferimento_clausola", "testo_letterale", "interpretazione_adottata"
    )(_not_blank)


class ClauseNoteUpdate(BaseModel):
    """A partial patch: the three required fields may be changed and never emptied, `note`
    is cleared with an explicit `null`."""

    model_config = ConfigDict(extra="forbid")

    riferimento_clausola: SafeStr | None = Field(
        default=None, min_length=1, max_length=RIFERIMENTO_CLAUSOLA_MAX_LENGTH
    )
    testo_letterale: SafeStr | None = Field(
        default=None, min_length=1, max_length=CLAUSE_TEXT_MAX_LENGTH
    )
    interpretazione_adottata: SafeStr | None = Field(
        default=None, min_length=1, max_length=CLAUSE_TEXT_MAX_LENGTH
    )
    note: SafeStr | None = Field(default=None, max_length=CLAUSE_TEXT_MAX_LENGTH)

    _required_text_is_not_blank = field_validator(
        "riferimento_clausola", "testo_letterale", "interpretazione_adottata"
    )(_not_blank)


class ClauseNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    riferimento_clausola: str
    testo_letterale: str
    interpretazione_adottata: str
    note: str | None
    created_at: datetime
    updated_at: datetime


class ContractDetail(ContractRead):
    """A contract as its page and an agent read it: the row, the clause notes written
    against it (REB-762) and the contract that renewed it, if one did (REB-763). Only
    `ContractService.detail` builds one, so a list of contracts stays one query: the
    notes and the successor are asked for the one contract being opened."""

    contratto_successivo_id: UUID | None
    # The latest 50, oldest first; the notes' own list answers for all of them.
    note_clausole: list[ClauseNoteRead]


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
