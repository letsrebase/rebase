"""REB-769: the space-wide group of the catalogue, raised to admins (spec 2026-10-26 §3.5).

Every test evaluates on a date it chooses (`TODAY`, `NOON`), which is the point of a pure
detector. The card's proof is the first test: a forfettario space at 90% of its ceiling
raises the reminder to its admins and not to its members, and an ordinary-regime space
raises none.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.scope import apply_scope
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_NON_IT
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.schemas import SCOPE_READONLY, SCOPE_SEND
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.reminders.catalogue import LADDER_MAX, REMINDER_TYPES, Severity
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.reminders.detectors.space import (
    BROKEN,
    MAX_PROPOSALS,
    REMINDER_PASS_JOB,
    job_subject,
    period_subject,
    subject_period,
)
from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
from pigrocrm.core.reminders.ledger import ReminderLedger
from pigrocrm.core.reminders.models import ReminderJobRun
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import ReminderPreferenceWrite
from pigrocrm.core.timetracking.models import PeriodLock

TODAY = date(2026, 10, 26)
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
RIVALSA = Decimal("85000.00")
_NUMERO = count(1)


@dataclass
class Space:
    session: Session
    owner: User  # an admin, and the space's owner
    other_admin: User
    member: User
    azienda: UUID

    def evaluate(self, today: date = TODAY, now: datetime = NOON) -> Evaluation:
        self.session.flush()
        return ReminderEngine(self.session, SETTINGS).evaluate(today=today, now=now)

    def told(self, type_key: str, evaluation: Evaluation | None = None) -> dict[UUID, Severity]:
        """Who is told of `type_key`, and at which severity (one subject at a time: the
        tests that raise several read `reminders` themselves)."""
        evaluation = evaluation or self.evaluate()
        return {r.user_id: r.severity for r in evaluation.reminders if r.type == type_key}

    def user(self, ruolo: str = "admin") -> User:
        user = User(
            email=f"u-{uuid4()}@example.test", password_hash="x", nome="Persona", ruolo=ruolo
        )
        self.session.add(user)
        self.session.flush()
        return user

    def revenue(
        self,
        amount: str,
        *,
        anno: int = 2026,
        azienda: UUID | None = None,
        paid: bool = True,
        deleted: bool = False,
    ) -> Invoice:
        """An issued fattura of `anno`: paid in June, or unpaid and due in December."""
        customer = Customer(ragione_sociale=f"Acme {uuid4().hex[:6]}")
        self.session.add(customer)
        self.session.flush()
        imponibile = Decimal(amount)
        invoice = Invoice(
            customer_id=customer.id,
            azienda_id=azienda or self.azienda,
            tipo="fattura",
            stato="emessa",
            anno=None if deleted else anno,
            numero=None if deleted else next(_NUMERO),
            data_emissione=date(anno, 6, 15),
            imponibile=imponibile,
            imposta=Decimal("0.00"),
            bollo=Decimal("0.00"),
            totale=imponibile,
            stato_pagamento="incassato" if paid else "da_incassare",
            data_incasso=date(anno, 6, 20) if paid else None,
            data_scadenza=None if paid else date(anno, 12, 15),
            deleted_at=NOON if deleted else None,
        )
        self.session.add(invoice)
        self.session.flush()
        return invoice

    def profile(self) -> FiscalProfile:
        return self.session.scalars(
            select(FiscalProfile).where(FiscalProfile.azienda_id == self.azienda)
        ).one()


def _admin() -> Actor:
    return Actor(id=None, type="system", role="admin")


@pytest.fixture
def space(db_session: Session) -> Space:
    azienda = db_session.scalars(
        select(LegalEntity.id).where(LegalEntity.predefinita.is_(True))
    ).one()
    users = []
    for ruolo in ("admin", "admin", "collaboratore"):
        user = User(
            email=f"u-{uuid4()}@example.test", password_hash="x", nome="Persona", ruolo=ruolo
        )
        db_session.add(user)
        users.append(user)
    db_session.flush()
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), _admin())
    return Space(db_session, users[0], users[1], users[2], azienda)


def _ordinary(space: Space) -> None:
    FiscalProfileService(space.session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF01",
            aliquota_iva_default=Decimal("22.00"),
            natura_default=None,
            riferimento_normativo=None,
        ),
        _admin(),
    )


# -- ceiling_approaching, year_end_overrun_projected: the card's proof ----------------------


def test_a_forfettario_space_at_90_percent_tells_its_admins_not_members_and_an_ordinary_one_none(
    space: Space,
) -> None:
    space.revenue("76500.00")  # 90% of 85,000

    evaluation = space.evaluate()

    assert space.told("ceiling_approaching", evaluation) == {
        space.owner.id: "serious",
        space.other_admin.id: "serious",
    }, "both admins, at serious, and the collaborator is not told"
    assert space.member.id not in {r.user_id for r in evaluation.reminders}
    reminder = next(r for r in evaluation.reminders if r.type == "ceiling_approaching")
    assert reminder.measure == 90.0
    assert reminder.entity == "fiscal" and reminder.entity_id == space.azienda
    assert reminder.azienda_id == space.azienda
    assert "76.500,00 €" in reminder.detail and "85.000,00 €" in reminder.detail
    assert "ceiling_approaching" in evaluation.ran

    _ordinary(space)
    ordinary = space.evaluate()

    assert [r for r in ordinary.reminders if r.type.startswith(("ceiling", "year_end"))] == []
    assert "ceiling_approaching" not in ordinary.ran, "skipped, not run and discarded"
    assert "year_end_overrun_projected" not in ordinary.ran


@pytest.mark.parametrize(
    ("revenue", "expected"),
    [
        ("67999.99", None),  # 79.99%, rounded down to 79
        ("68000.00", "warning"),  # 80%
        ("76499.99", "warning"),  # 89.99% is 89
        ("76500.00", "serious"),  # 90%
        ("84999.99", "serious"),  # 99.99% is 99
        ("85000.00", "critical"),  # 100%
        ("120000.00", "critical"),
    ],
)
def test_the_ceiling_ladder_climbs_at_80_90_and_100_percent(
    space: Space, revenue: str, expected: Severity | None
) -> None:
    space.revenue(revenue)

    told = space.told("ceiling_approaching")

    assert told == (
        {} if expected is None else {space.owner.id: expected, space.other_admin.id: expected}
    )


def test_only_revenue_paid_this_year_counts_towards_the_ceiling(space: Space) -> None:
    space.revenue("90000.00", anno=2025)  # last year's money
    space.revenue("90000.00", paid=False)  # issued, not collected: not the ceiling's basis
    space.revenue("90000.00", deleted=True)  # archived

    assert space.told("ceiling_approaching") == {}


def test_the_text_names_the_immediate_exit_ceiling_when_it_is_near(space: Space) -> None:
    space.revenue("70000.00")
    far = next(r for r in space.evaluate().reminders if r.type == "ceiling_approaching")
    assert "100.000,00 €" not in far.detail

    space.revenue("22000.00")  # 92,000: past 85,000 and 92% of 100,000
    near = next(r for r in space.evaluate().reminders if r.type == "ceiling_approaching")

    assert "100.000,00 €" in near.detail and "subito" in near.detail
    assert "non vale più" in near.detail, "the 85,000 is crossed: the regime ends next year"


def test_the_ceiling_is_one_reminder_per_azienda_and_belongs_to_a_forfettario_one(
    space: Space,
) -> None:
    other = LegalEntity(nome="Seconda", ragione_sociale="Seconda Srl", attiva=True)
    space.session.add(other)
    space.session.flush()
    FiscalProfileService(space.session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), _admin(), other.id
    )
    space.revenue("76500.00")
    space.revenue("68000.00", azienda=other.id)

    raised = {
        r.entity_id: r.severity
        for r in space.evaluate().reminders
        if r.type == "ceiling_approaching" and r.user_id == space.owner.id
    }

    assert raised == {space.azienda: "serious", other.id: "warning"}, (
        "each azienda is measured against its own receipts"
    )


def test_a_foreign_a_missing_or_an_inactive_azienda_has_no_ceiling(space: Space) -> None:
    space.revenue("90000.00")
    profile = space.profile()
    profile.pack_id = PACK_NON_IT
    profile.codice_regime = None
    space.session.flush()
    assert space.told("ceiling_approaching") == {}, "the foreign pack has no ceiling"

    profile.pack_id = "it-flat-rate"
    profile.codice_regime = "RF19"
    space.session.flush()
    assert space.told("ceiling_approaching") != {}

    inactive = LegalEntity(nome="Chiusa", ragione_sociale="Chiusa Srl", attiva=False)
    space.session.add(inactive)
    space.session.flush()
    FiscalProfileService(space.session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), _admin(), inactive.id
    )
    space.revenue("90000.00", azienda=inactive.id)
    reminders = space.evaluate().reminders
    assert {r.entity_id for r in reminders if r.type == "ceiling_approaching"} == {space.azienda}, (
        "an inactive azienda is not watched"
    )

    space.session.delete(profile)
    space.session.flush()
    evaluation = space.evaluate()
    assert space.told("ceiling_approaching", evaluation) == {}
    assert "ceiling_approaching" not in evaluation.ran


def test_a_scoped_admin_is_told_only_of_the_aziende_their_scope_covers(space: Space) -> None:
    other = LegalEntity(nome="Seconda", ragione_sociale="Seconda Srl", attiva=True)
    space.session.add(other)
    space.session.flush()
    apply_scope(space.session, space.other_admin, [other.id])
    space.revenue("76500.00")

    assert space.told("ceiling_approaching") == {space.owner.id: "serious"}

    apply_scope(space.session, space.other_admin, [space.azienda])

    assert space.told("ceiling_approaching") == {
        space.owner.id: "serious",
        space.other_admin.id: "serious",
    }


def test_a_ceiling_whose_cause_is_fixed_is_gone_on_the_next_evaluation(space: Space) -> None:
    invoice = space.revenue("76500.00")
    assert space.told("ceiling_approaching")

    invoice.stato_pagamento = "da_incassare"  # the payment is undone
    invoice.data_incasso = None

    assert space.told("ceiling_approaching") == {}


@pytest.mark.parametrize(
    ("paid", "unpaid", "expected"),
    [
        ("0.00", "89249.99", None),  # 104.99%, 104
        ("0.00", "89250.00", "serious"),  # 105%
        ("0.00", "101999.99", "serious"),  # 119.99%
        ("0.00", "102000.00", "critical"),  # 120%
        ("40000.00", "49250.00", "serious"),  # paid and unpaid add up: 89,250
    ],
)
def test_the_year_end_projection_climbs_at_105_and_120_percent(
    space: Space, paid: str, unpaid: str, expected: Severity | None
) -> None:
    if Decimal(paid):
        space.revenue(paid)
    space.revenue(unpaid, paid=False)

    told = space.told("year_end_overrun_projected")

    assert told == (
        {} if expected is None else {space.owner.id: expected, space.other_admin.id: expected}
    )


def test_the_projection_is_the_cash_overviews_and_counts_drafts_and_proformas(
    space: Space,
) -> None:
    customer = Customer(ragione_sociale="Acme Bozze")
    space.session.add(customer)
    space.session.flush()
    for tipo, stato in (("fattura", "bozza"), ("proforma", "confermata")):
        space.session.add(
            Invoice(
                customer_id=customer.id,
                azienda_id=space.azienda,
                tipo=tipo,
                stato=stato,
                data_emissione=date(2026, 11, 10),
                imponibile=Decimal("45000.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("45000.00"),
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
    space.session.flush()

    evaluation = space.evaluate()

    reminder = next(r for r in evaluation.reminders if r.type == "year_end_overrun_projected")
    assert reminder.measure == 105.0, "90,000 of 85,000, the dashboard's `proiettato`"
    assert reminder.entity == "fiscal" and reminder.entity_id == space.azienda
    assert "90.000,00 €" in reminder.detail
    assert space.told("ceiling_approaching", evaluation) == {}, "nothing is collected yet"


def test_the_projection_names_the_immediate_exit_ceiling_when_it_reaches_it(
    space: Space,
) -> None:
    space.revenue("103000.00", paid=False)

    reminder = next(r for r in space.evaluate().reminders if r.type == "year_end_overrun_projected")

    assert reminder.severity == "critical" and "100.000,00 €" in reminder.detail


# -- month_not_closed -----------------------------------------------------------------------


def _lock(space: Space, anno: int, mese: int) -> None:
    space.session.add(PeriodLock(anno=anno, mese=mese, chiuso_il=NOON))
    space.session.flush()


def _months(space: Space, today: date) -> dict[tuple[int, int], Severity]:
    evaluation = space.evaluate(today=today)
    return {
        subject_period(UUID(r.key.partition(":")[2])): r.severity
        for r in evaluation.reminders
        if r.type == "month_not_closed" and r.user_id == space.owner.id
    }


def test_a_space_that_never_closed_a_period_gets_no_month_reminder(space: Space) -> None:
    evaluation = space.evaluate()

    assert "month_not_closed" not in evaluation.ran
    assert [r for r in evaluation.reminders if r.type == "month_not_closed"] == []


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 9), {}),  # September ended 9 days ago
        (date(2026, 10, 10), {(2026, 9): "warning"}),  # 10 days
        (date(2026, 10, 19), {(2026, 9): "warning"}),
        (date(2026, 10, 20), {(2026, 9): "serious"}),  # 20 days
    ],
)
def test_a_month_ends_a_warning_at_10_days_and_a_serious_one_at_20(
    space: Space, today: date, expected: dict[tuple[int, int], Severity]
) -> None:
    _lock(space, 2026, 8)

    assert _months(space, today) == expected


def test_months_before_the_first_lock_are_not_owed_and_a_gap_between_locks_is(
    space: Space,
) -> None:
    _lock(space, 2026, 5)
    _lock(space, 2026, 7)

    assert _months(space, TODAY) == {
        (2026, 6): "serious",  # skipped between two closed months
        (2026, 8): "serious",
        (2026, 9): "serious",
    }, "April and before were never part of the ritual, and October has not ended"


def test_closing_the_month_clears_its_reminder_and_the_year_wraps(space: Space) -> None:
    _lock(space, 2025, 11)
    assert set(_months(space, date(2026, 2, 20))) == {(2025, 12), (2026, 1)}

    _lock(space, 2025, 12)
    _lock(space, 2026, 1)

    assert _months(space, date(2026, 2, 20)) == {}


def test_the_month_subject_carries_the_month_and_goes_to_unscoped_admins_only(
    space: Space,
) -> None:
    assert subject_period(period_subject(2026, 9)) == (2026, 9)
    assert subject_period(period_subject(2027, 12)) == (2027, 12)
    _lock(space, 2026, 8)
    apply_scope(space.session, space.other_admin, [space.azienda])

    evaluation = space.evaluate()

    reminder = next(r for r in evaluation.reminders if r.type == "month_not_closed")
    assert reminder.entity == "period" and reminder.entity_id == period_subject(2026, 9)
    assert space.told("month_not_closed", evaluation) == {space.owner.id: "serious"}, (
        "closing a period takes an unscoped admin, so a scoped one is not asked to"
    )


# -- proposal_pending -----------------------------------------------------------------------


def _proposal(
    space: Space, *, age_days: int, contract: Contract | None = None, stato: str = "in_attesa"
) -> Proposal:
    customer = Customer(ragione_sociale=f"Acme {uuid4().hex[:6]}")
    space.session.add(customer)
    space.session.flush()
    document = Document(tipo="contratto", titolo="Documento archiviato", customer_id=customer.id)
    space.session.add(document)
    space.session.flush()
    proposal = Proposal(
        document_id=document.id,
        contract_id=contract.id if contract is not None else None,
        target_type="giornata" if contract is not None else "contratto",
        campi_proposti={},
        estratto="Confermiamo la giornata.",
        confidenza=Decimal("0.90"),
        stato=stato,
        created_at=NOON - timedelta(days=age_days),
    )
    space.session.add(proposal)
    space.session.flush()
    return proposal


def _contract(space: Space, azienda: UUID | None = None) -> Contract:
    customer = Customer(ragione_sociale="Acme Contratti")
    space.session.add(customer)
    space.session.flush()
    contract = Contract(
        customer_id=customer.id,
        azienda_id=azienda or space.azienda,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
    )
    space.session.add(contract)
    space.session.flush()
    return contract


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (2, None),
        (3, "warning"),
        (6, "warning"),
        (7, "serious"),
        (13, "serious"),
        (14, "critical"),
    ],
)
def test_a_proposal_waits_a_warning_at_3_days_a_serious_at_7_and_a_critical_at_14(
    space: Space, age: int, expected: Severity | None
) -> None:
    proposal = _proposal(space, age_days=age)

    told = space.told("proposal_pending")

    assert told == (
        {} if expected is None else {space.owner.id: expected, space.other_admin.id: expected}
    )
    if expected is not None:
        reminder = next(r for r in space.evaluate().reminders if r.type == "proposal_pending")
        assert reminder.entity == "proposal" and reminder.entity_id == proposal.id
        assert reminder.measure == float(age)


def test_only_proposals_still_waiting_count_and_each_is_its_own_reminder(space: Space) -> None:
    _proposal(space, age_days=20, stato="accettata")
    _proposal(space, age_days=20, stato="rifiutata")
    waiting = [_proposal(space, age_days=age) for age in (4, 9)]

    keys = {r.key for r in space.evaluate().reminders if r.type == "proposal_pending"}

    assert keys == {f"proposal_pending:{proposal.id}" for proposal in waiting}
    assert space.member.id not in space.told("proposal_pending")


def test_a_proposal_counts_its_days_in_the_installations_zone_not_in_utc(space: Space) -> None:
    """Created at 23:30 UTC on 23 October is already the 24th in Rome: two days before
    the 26th, not three, so it is not yet a reminder."""
    proposal = _proposal(space, age_days=0)
    proposal.created_at = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)
    space.session.flush()

    assert space.told("proposal_pending") == {}

    proposal.created_at = datetime(2026, 10, 23, 21, 30, tzinfo=UTC)  # the 23rd, 23:30 in Rome
    space.session.flush()

    assert space.told("proposal_pending") != {}


def test_a_proposal_is_told_to_the_admins_who_see_its_contracts_azienda(space: Space) -> None:
    other = LegalEntity(nome="Seconda", ragione_sociale="Seconda Srl", attiva=True)
    space.session.add(other)
    space.session.flush()
    apply_scope(space.session, space.other_admin, [other.id])
    _proposal(space, age_days=8, contract=_contract(space))

    assert space.told("proposal_pending") == {space.owner.id: "serious"}

    _proposal(space, age_days=8, contract=_contract(space, other.id))

    scoped = [r for r in space.evaluate().reminders if r.user_id == space.other_admin.id]
    assert [r.azienda_id for r in scoped if r.type == "proposal_pending"] == [other.id]


def test_the_queue_is_listed_oldest_first_and_bounded(space: Space) -> None:
    for age in range(MAX_PROPOSALS + 5):
        _proposal(space, age_days=3 + age)

    reminders = [
        r
        for r in space.evaluate().reminders
        if r.type == "proposal_pending" and r.user_id == space.owner.id
    ]

    assert len(reminders) == MAX_PROPOSALS
    assert min(r.measure for r in reminders) == 8.0, "the five youngest are the ones left out"


# -- connection_failing ---------------------------------------------------------------------


def _gmail(
    space: Space,
    *,
    status: str = "active",
    synced_hours_ago: float | None = 0.5,
    scopes: tuple[str, ...] = (SCOPE_READONLY, SCOPE_SEND),
) -> GoogleAccount:
    account = GoogleAccount(
        user_id=space.user("collaboratore").id,
        google_sub=f"sub-{uuid4()}",
        email_address=f"box-{uuid4().hex[:6]}@example.test",
        refresh_token_ciphertext=b"x",
        refresh_token_nonce=b"x",
        scopes_granted=list(scopes),
        status=status,
        connected_at=NOON - timedelta(days=30),
        last_sync_at=None if synced_hours_ago is None else NOON - timedelta(hours=synced_hours_ago),
    )
    space.session.add(account)
    space.session.flush()
    return account


def _drive(space: Space, *, status: str = "active") -> GoogleDriveAccount:
    account = GoogleDriveAccount(
        user_id=space.user("collaboratore").id,
        google_sub=f"sub-{uuid4()}",
        email_address=f"drive-{uuid4().hex[:6]}@example.test",
        refresh_token_ciphertext=b"x",
        refresh_token_nonce=b"x",
        status=status,
        connected_at=NOON - timedelta(days=30),
    )
    space.session.add(account)
    space.session.flush()
    return account


def test_a_space_that_connected_nothing_runs_no_connection_check(space: Space) -> None:
    _gmail(space, status="disconnected")

    assert "connection_failing" not in space.evaluate().ran


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(2.99, None), (3.0, "serious"), (72.0, "serious"), (700.0, "serious")],
)
def test_a_mailbox_that_stopped_syncing_is_serious_at_3_hours(
    space: Space, hours: float, expected: Severity | None
) -> None:
    account = _gmail(space, synced_hours_ago=hours)

    told = space.told("connection_failing")

    assert told == (
        {} if expected is None else {space.owner.id: expected, space.other_admin.id: expected}
    )
    if expected is not None:
        reminder = next(r for r in space.evaluate().reminders if r.type == "connection_failing")
        assert reminder.entity == "google_connection" and reminder.entity_id == account.id
        assert reminder.measure == float(int(hours))


@pytest.mark.parametrize("status", ["revoked", "expired"])
def test_a_broken_credential_is_critical_at_once_whatever_the_last_sync(
    space: Space, status: str
) -> None:
    _gmail(space, status=status, synced_hours_ago=0.1)

    reminder = next(r for r in space.evaluate().reminders if r.type == "connection_failing")

    assert reminder.severity == "critical" and reminder.measure == BROKEN == float(LADDER_MAX)


def test_a_broken_credential_stays_critical_under_an_edited_ladder(space: Space) -> None:
    ReminderPreferenceService(space.session).write(
        Actor(id=space.owner.id, type="user", role="admin"),
        "space",
        "connection_failing",
        ReminderPreferenceWrite(ladder={"serious": 1, "critical": 500}),
    )
    _gmail(space, status="revoked")

    assert set(space.told("connection_failing").values()) == {"critical"}


def test_a_disconnected_mailbox_and_one_without_the_read_scope_are_not_failures(
    space: Space,
) -> None:
    _gmail(space, status="disconnected", synced_hours_ago=500)
    _gmail(space, synced_hours_ago=500, scopes=(SCOPE_SEND,))
    _gmail(space)

    assert space.told("connection_failing") == {}


def test_a_mailbox_that_never_synced_is_as_stale_as_it_is_old(space: Space) -> None:
    account = _gmail(space, synced_hours_ago=None)
    account.connected_at = NOON - timedelta(hours=5)
    space.session.flush()

    assert set(space.told("connection_failing").values()) == {"serious"}


def test_a_broken_drive_is_critical_and_a_healthy_one_says_nothing(space: Space) -> None:
    _drive(space)
    assert space.told("connection_failing") == {}

    drive = _drive(space, status="revoked")

    reminder = next(r for r in space.evaluate().reminders if r.type == "connection_failing")
    assert reminder.entity_id == drive.id and reminder.severity == "critical"
    assert "Drive" in reminder.title


def test_connection_reminders_go_to_unscoped_admins_and_a_recovered_one_is_gone(
    space: Space,
) -> None:
    account = _gmail(space, status="revoked")
    apply_scope(space.session, space.other_admin, [space.azienda])

    assert set(space.told("connection_failing")) == {space.owner.id}

    account.status = "active"
    account.last_sync_at = NOON

    assert space.told("connection_failing") == {}


# -- job_late -------------------------------------------------------------------------------


def _touch(space: Space, job: str, minutes_ago: int) -> None:
    ReminderLedger(space.session).touch_job(job, NOON - timedelta(minutes=minutes_ago))
    space.session.flush()


def test_a_job_with_no_clock_is_never_late(space: Space) -> None:
    evaluation = space.evaluate()

    assert "job_late" in evaluation.ran
    assert space.told("job_late", evaluation) == {}
    assert space.session.scalars(select(ReminderJobRun)).all() == [], "no row is invented"


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(29, None), (30, "serious"), (179, "serious"), (180, "critical"), (1000, "critical")],
)
def test_the_reminder_pass_is_serious_at_twice_its_interval_and_critical_after_3_hours(
    space: Space, minutes: int, expected: Severity | None
) -> None:
    _touch(space, REMINDER_PASS_JOB, minutes)

    told = space.told("job_late")

    assert told == (
        {} if expected is None else {space.owner.id: expected, space.other_admin.id: expected}
    )
    if expected is not None:
        reminder = next(r for r in space.evaluate().reminders if r.type == "job_late")
        assert reminder.entity == "job" and reminder.entity_id == job_subject(REMINDER_PASS_JOB)
        assert reminder.measure == float(minutes)


def test_a_stale_mailbox_is_connection_failing_and_not_a_second_late_job(space: Space) -> None:
    """One cause, one reminder: the mailbox sync's clock is `last_sync_at`, which
    `connection_failing` reads with the account's name, so `job_late` does not."""
    account = _gmail(space, synced_hours_ago=48)

    reminders = space.evaluate().reminders

    assert {r.entity_id for r in reminders if r.type == "connection_failing"} == {account.id}
    assert [r for r in reminders if r.type == "job_late"] == []


def test_only_the_reminder_pass_is_a_job_and_a_job_nothing_names_is_ignored(
    space: Space,
) -> None:
    _touch(space, REMINDER_PASS_JOB, 200)
    _touch(space, "digest", 99999)
    _gmail(space, synced_hours_ago=3)

    subjects = {r.entity_id for r in space.evaluate().reminders if r.type == "job_late"}

    assert subjects == {job_subject(REMINDER_PASS_JOB)}


def test_an_edited_ladder_changes_the_pass_severity_and_nothing_else_can_reach_it(
    space: Space,
) -> None:
    ReminderPreferenceService(space.session).write(
        Actor(id=space.owner.id, type="user", role="admin"),
        "space",
        "job_late",
        ReminderPreferenceWrite(ladder={"serious": 10, "critical": 40}),
    )
    _touch(space, REMINDER_PASS_JOB, 45)
    _gmail(space, synced_hours_ago=9)  # long past 40 minutes, and not a `job_late` subject

    reminders = [r for r in space.evaluate().reminders if r.type == "job_late"]

    assert {(r.entity_id, r.severity) for r in reminders} == {
        (job_subject(REMINDER_PASS_JOB), "critical")
    }


def test_a_detail_says_one_day_one_hour_one_minute_and_not_one_days(space: Space) -> None:
    admin = Actor(id=space.owner.id, type="user", role="admin")
    prefs = ReminderPreferenceService(space.session)
    prefs.write(admin, "space", "proposal_pending", ReminderPreferenceWrite(ladder={"warning": 1}))
    prefs.write(
        admin, "space", "connection_failing", ReminderPreferenceWrite(ladder={"serious": 1})
    )
    prefs.write(admin, "space", "job_late", ReminderPreferenceWrite(ladder={"serious": 1}))
    _proposal(space, age_days=1)
    _gmail(space, synced_hours_ago=1)
    _touch(space, REMINDER_PASS_JOB, 1)

    details = {r.type: r.detail for r in space.evaluate().reminders}

    assert "da 1 giorno:" in details["proposal_pending"]
    assert "è di 1 ora fa" in details["connection_failing"]
    assert "da 1 minuto:" in details["job_late"]


def test_the_aziende_are_read_once_per_evaluation(space: Space) -> None:
    seen: list[str] = []

    def record(_c: object, _cur: object, statement: str, *_a: object) -> None:
        if "FROM emitter_profile JOIN fiscal_profile" in statement:
            seen.append(statement)

    engine = space.session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        evaluation = space.evaluate()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert {"ceiling_approaching", "year_end_overrun_projected"} <= evaluation.ran
    assert len(seen) == 1


def test_a_job_late_goes_to_unscoped_admins_and_recovers_when_it_runs(space: Space) -> None:
    _touch(space, REMINDER_PASS_JOB, 200)
    apply_scope(space.session, space.other_admin, [space.azienda])

    assert set(space.told("job_late")) == {space.owner.id}

    _touch(space, REMINDER_PASS_JOB, 0)

    assert space.told("job_late") == {}


def test_a_pass_sees_the_previous_runs_clock_not_its_own(space: Space) -> None:
    """The pass writes its job time after the detectors read (`record_pass`), so the
    clock a pass reads is the last one's."""
    _touch(space, REMINDER_PASS_JOB, 200)
    engine = ReminderEngine(space.session, SETTINGS)

    first = engine.evaluate(today=TODAY, now=NOON)
    engine.record_pass(first, today=TODAY, job=REMINDER_PASS_JOB)
    second = engine.evaluate(today=TODAY, now=NOON)

    assert any(r.type == "job_late" for r in first.reminders)
    assert not any(r.type == "job_late" for r in second.reminders)


# -- the group as a whole -------------------------------------------------------------------


def test_every_type_of_the_group_is_for_admins_and_a_space_with_one_admin_keeps_them(
    space: Space,
) -> None:
    group = (
        "ceiling_approaching",
        "year_end_overrun_projected",
        "month_not_closed",
        "proposal_pending",
        "connection_failing",
        "job_late",
    )
    assert all(REMINDER_TYPES[key].audience == "admins" for key in group)
    assert "tax_payment_due" not in REMINDER_TYPES, (
        "the fiscal pack knows no payment date, so the type applies nowhere and is not added"
    )
    assert [d.kind.key for d in DETECTORS][-len(group) :] == list(group)
