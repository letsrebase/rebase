"""Asking a client for prior approval of named days, from the contract (REB-749).

Mastro composed, previewed and sent a request for approval of named dates, in the form the
contract's clause asks for. PigroCRM sends mail from the user's own Gmail only on a person's
action (`gmail/send.py`), so this module stops short of sending, on purpose: it **composes**
the request and **prepares** it, which records the named days as `proposto` and files the
message as a draft of the customer in one transaction; the person reads it and presses Invia
on the draft, which is the one existing send path. Nothing here calls Gmail.

The days are recorded before the mail leaves because that is the part a person can undo (a
proposed day is revoked or refused) and the mail is not: a request that went out for days the
CRM does not hold is the worse failure. A draft that never leaves leaves proposed days
behind, which the contract's days list shows and the person can withdraw.

A contract here has one clause flag, `requires_prior_approval`, where mastro's had four, so
the text is one: it names the contract, lists the dates with the part of the day and what the
work is, and asks for a written reply before the first date.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.schemas import (
    EMAIL_ADDRESS_MAX_LENGTH,
    SUBJECT_MAX_LENGTH,
    EmailDraftCreate,
    EmailDraftRead,
)
from pigrocrm.core.validation import SafeStr
from pigrocrm.core.work_units.repository import WorkUnitRepository
from pigrocrm.core.work_units.schemas import (
    LIST_LIMIT_MAX,
    WorkUnitCreate,
    WorkUnitListQuery,
    WorkUnitRead,
)
from pigrocrm.core.work_units.service import WorkUnitService

DAYS_MAX = 31
WEEKDAYS = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MONTHS = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)
FULL = Decimal("1")
HALF = Decimal("0.5")


class ApprovalRequestDay(BaseModel):
    """One date the request names: how much of it and what the work is. The part of the
    day is the two a day names individually can be (`1` or `0.5`), as in mastro."""

    model_config = ConfigDict(extra="forbid")

    data: date
    quantita: Decimal = Field(max_digits=4, decimal_places=2)
    descrizione: SafeStr = Field(min_length=1)

    @field_validator("quantita")
    @classmethod
    def _full_or_half(cls, value: Decimal) -> Decimal:
        if value not in (FULL, HALF):
            raise ValueError("una giornata chiesta per data e' intera (1) o mezza (0.5)")
        return value


class ApprovalRequestCompose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[ApprovalRequestDay] = Field(min_length=1, max_length=DAYS_MAX)


class ApprovalRequestMessage(BaseModel):
    """The subject and body the request would carry: what the person previews and edits."""

    oggetto: str
    corpo: str


class ApprovalRequestPrepare(ApprovalRequestCompose):
    to_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]] = Field(
        min_length=1
    )
    cc_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]] = []
    oggetto: SafeStr = Field(min_length=1, max_length=SUBJECT_MAX_LENGTH)
    corpo: SafeStr = Field(min_length=1)


class ApprovalRequestPrepared(BaseModel):
    """The days now proposed and the draft that asks for them, ready to send."""

    draft: EmailDraftRead
    work_units: list[WorkUnitRead]


def italian_date(giorno: date) -> str:
    """`lunedì 2 novembre 2026`, spelled here rather than by a locale lookup: an ICU or libc
    default that changed would reword a request already in a client's inbox."""
    return f"{WEEKDAYS[giorno.weekday()]} {giorno.day} {MONTHS[giorno.month - 1]} {giorno.year}"


def compose(titolo: str, days: list[ApprovalRequestDay]) -> ApprovalRequestMessage:
    """The request for `days` of the contract `titolo`, oldest date first (the order a
    reader confirming a week expects), asking for a written reply before the first."""
    ordered = sorted(days, key=lambda day: day.data)
    rows = [
        f"- {italian_date(day.data)}: "
        f"{'giornata intera' if day.quantita == FULL else 'mezza giornata'}, {day.descrizione}"
        for day in ordered
    ]
    corpo = (
        "Buongiorno,\n\n"
        f"come previsto dal contratto «{titolo}», le chiedo l'approvazione preventiva delle "
        "seguenti giornate:\n\n"
        + "\n".join(rows)
        + "\n\nLe chiedo di confermarmi per iscritto, rispondendo a questa email, entro "
        f"{italian_date(ordered[0].data)}, indicando le date approvate.\n\n"
        "Grazie."
    )
    return ApprovalRequestMessage(
        oggetto=f"Richiesta di approvazione delle giornate: {titolo}", corpo=corpo
    )


class ApprovalRequestService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _contract(self, contract_id: UUID) -> tuple[str, UUID]:
        contract = ContractRepository(self.session).get(contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)
        return contract.titolo, contract.customer_id

    def compose(
        self, contract_id: UUID, data: ApprovalRequestCompose, actor: Actor
    ) -> ApprovalRequestMessage:
        """The text a request would carry, written and not stored: a read."""
        titolo, _ = self._contract(contract_id)
        return compose(titolo, list(data.days))

    def prepare(
        self, contract_id: UUID, data: ApprovalRequestPrepare, actor: Actor
    ) -> ApprovalRequestPrepared:
        """Records the named days as proposed and files the message as a draft of the
        customer, in one transaction (the draft's own commit is the one that lands both).
        A date the contract already holds a live day for refuses the whole request, with the
        dates named: asking for a day that is already recorded is a mistake nobody wants
        in a client's inbox.
        """
        actor.require_write("request_approval")
        _, customer_id = self._contract(contract_id)
        taken = self._taken(contract_id, data)
        if taken:
            raise Conflict(
                "approval_request",
                "il contratto ha gia' una giornata in queste date: " + ", ".join(taken),
                date=taken,
            )
        days = WorkUnitService(self.session)
        proposed = [
            days.create(
                WorkUnitCreate(
                    contract_id=contract_id,
                    data=day.data,
                    quantita=day.quantita,
                    descrizione=day.descrizione,
                    stato="proposto",
                ),
                actor,
                f"Richiesta di approvazione a {', '.join(data.to_addresses)}",
                commit=False,
            )
            for day in sorted(data.days, key=lambda day: day.data)
        ]
        draft = EmailDraftService(self.session, settings=self.settings).create(
            EmailDraftCreate(
                entity_type="customer",
                entity_id=customer_id,
                to_addresses=list(data.to_addresses),
                cc_addresses=list(data.cc_addresses),
                subject=data.oggetto,
                body_markdown=data.corpo,
            ),
            actor,
        )
        return ApprovalRequestPrepared(draft=draft, work_units=proposed)

    def _taken(self, contract_id: UUID, data: ApprovalRequestPrepare) -> list[str]:
        asked = {day.data for day in data.days}
        live = WorkUnitRepository(self.session).list_labelled(
            WorkUnitListQuery(
                contract_id=contract_id,
                da=min(asked),
                a=max(asked),
                limit=LIST_LIMIT_MAX,
            )
        )
        return sorted(
            work_unit.data.isoformat()
            for work_unit, _, _ in live
            if work_unit.data in asked and work_unit.stato not in ("revocato", "rifiutato")
        )
