"""A world for the cash schedule, the payables mirror and the phone summary: due items written
the way a source's service writes them, and bank accounts with a known balance. Every date is
relative to today, since the schedule is read from today forward."""

import datetime as dt
import itertools
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from fakes.customer_fixtures import make_customer
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.models import BankAccount
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)

_IBANS = itertools.count(1)
LONG_AGO = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)


class CashWorld:
    def __init__(self, session: Session) -> None:
        self.session = session
        default = LegalEntityRepository(session).default()
        assert default is not None
        self.entity: UUID = default.id
        self.customer = make_customer(session, legal_entity_id=self.entity, legal_name="Acme srl")
        self.service = DueItemService(session)
        self.today = today_local()
        self.monday = self.today - dt.timedelta(days=self.today.weekday())

    def week(self, index: int, day: int = 0) -> dt.date:
        """The `day`th day (0 is Monday) of the `index`th week from the current one."""
        return self.monday + dt.timedelta(days=7 * index + day)

    def item(
        self,
        amount: str,
        due: dt.date | None,
        *,
        direction: str = "in",
        state: str = "expected",
        source_kind: str | None = None,
        currency: str = "EUR",
        entity: UUID | None = None,
        estimated: bool = False,
        tax: str | None = None,
    ) -> UUID:
        kind = source_kind or (
            "sales_invoice" if direction == "in" and state == "expected" else "purchase"
        )
        values: dict[str, object] = {
            "legal_entity_id": entity or self.entity,
            "direction": direction,
            "amount": Decimal(amount),
            "currency": currency,
            "source_kind": kind,
            "source_id": uuid4(),
            "own_date": due,
        }
        if tax is not None:
            values.update(counterparty_type="tax_office", tax=tax)
        else:
            values.update(organization_id=self.customer.id)
        if state == "forecast":
            values.update(
                state="forecast", source_rule="contract_recurring_fee", forecast_key=uuid4()
            )
            values["source_kind"] = source_kind or "contract_occurrence"
        read = self.service.create(DueItemCreate(**values), check_organization=False)  # type: ignore[arg-type]
        if estimated:
            row = self.session.get(DueItem, read.id)
            assert row is not None
            row.due_date_estimated = True
            self.session.flush()
        return read.id

    def account(
        self,
        balance: str | None,
        *,
        name: str = "Conto operativo",
        currency: str = "EUR",
        as_of: dt.date | None = None,
        entity: UUID | None = None,
        cadence: str = "monthly",
    ) -> BankAccount:
        """An account whose balance is `balance` as of `as_of` (today by default): the opening
        balance, with no statement or movement after it."""
        account = BankAccount(
            legal_entity_id=entity or self.entity,
            name=name,
            bank="Banco BPM",
            iban=f"IT60X05428111{next(_IBANS):013d}",
            currency=currency,
            cadence=cadence,
            opening_balance=None if balance is None else Decimal(balance),
            opening_balance_date=None if balance is None else (as_of or self.today),
            created_at=LONG_AGO,
        )
        self.session.add(account)
        self.session.flush()
        return account

    def second_entity(self) -> UUID:
        other = LegalEntity(name="Seconda", legal_name="Seconda srl", country="IT")
        self.session.add(other)
        self.session.flush()
        return other.id
