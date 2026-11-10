"""The data the pages of «Incarichi» read (REB-1035, brief `2026-11-10-engagement-pages-brief.md`
«Needs code before the page can be built»): the names on the list, what waits for a person and its
count, a `source_id` filter on due items, what an engagement pays month by month, availability
counting the states the overlap warning counts, and the freelancer's own card read through the
door.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import test_engagements as door_tests
from fakes.customer_fixtures import make_customer
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_engagement_forecasts import World

import pigrocrm.core.db.clock as db_clock
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.monthly_reports import MonthlyReportService
from pigrocrm.core.collaborators.payment_read import EngagementPaymentService
from pigrocrm.core.collaborators.schemas import (
    EngagementCreate,
    EngagementListQuery,
    EngagementRead,
    MonthlyReportApproval,
    MonthlyReportRequest,
)
from pigrocrm.core.collaborators.service import EngagementService, FreelancerService
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.models import Document
from pigrocrm.core.due_items.schemas import DueItemListQuery
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.own import OwnEngagementService
from pigrocrm.core.errors import AgentForbidden, NotFound, PermissionDenied
from pigrocrm.core.organizations.models import Organization

door = door_tests.door
D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
# After October, with the seven working days of a report received on the 3rd still running.
NOW = dt.datetime(2026, 11, 10, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_clock, "_now", lambda: NOW)


@pytest.fixture
def world(db_session: Session, seeded_deal_id: UUID) -> World:
    return World(db_session, seeded_deal_id)


def _settings(**overrides: Any) -> Settings:
    base = {"leads_space": "rebase", "root_slug": "rebase", "public_url": "https://pigro.test"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _item(service: EngagementService, engagement_id: UUID) -> EngagementRead:
    return service.get(engagement_id, ADMIN)


def _listed(service: EngagementService, **query: Any) -> list[EngagementRead]:
    return service.list(EngagementListQuery(**query), ADMIN).items


def _codes(read: EngagementRead) -> list[str]:
    return [waiting.code for waiting in read.waiting]


# ---- names on the list ----------------------------------------------------------------------


def test_the_list_names_the_freelancer_and_the_client(world: World) -> None:
    created = world.engagement(active=False)
    freelancer = world.session.get(Organization, created.freelancer_id)
    client = world.session.get(Organization, world.client_id)
    assert freelancer is not None and client is not None

    (row,) = _listed(world.engagements, freelancer_id=created.freelancer_id)

    assert (row.freelancer_name, row.client_name) == (freelancer.legal_name, client.legal_name)
    assert row.redacted is False
    no_client = world.engagement(active=False, deal_id=None)
    assert _item(world.engagements, no_client.id).client_name is None


# ---- what waits for a person ----------------------------------------------------------------


def test_a_draft_waits_for_its_letter_and_then_for_sending(world: World) -> None:
    draft = world.engagement(active=False)
    assert _codes(_item(world.engagements, draft.id)) == ["letter_not_created"]
    assert _item(world.engagements, draft.id).letter_document_id is None

    letter = Document(
        customer_id=draft.freelancer_id,
        engagement_id=draft.id,
        type="engagement_letter",
        title="Lettera di incarico",
    )
    world.session.add(letter)
    world.session.flush()

    read = _item(world.engagements, draft.id)
    assert _codes(read) == ["letter_not_sent"]
    assert read.letter_document_id == letter.id
    assert [row.id for row in _listed(world.engagements, bucket="todo")] == [draft.id]


def test_a_live_engagement_waits_for_the_days_or_the_client_an_import_left_out(
    world: World,
) -> None:
    complete = world.engagement()
    assert _codes(_item(world.engagements, complete.id)) == []

    bare = world.engagement(deal_id=None)
    assert _codes(_item(world.engagements, bare.id)) == ["missing_client"]
    assert {row.id for row in _listed(world.engagements, bucket="todo")} == {bare.id}


def test_a_month_received_and_not_decided_waits_until_a_person_decides_it(world: World) -> None:
    created = world.engagement()
    reports = MonthlyReportService(world.session, _settings())
    # World's engagement runs the month before the frozen today: October 2026.
    reports.request(
        created.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )

    waiting = _item(world.engagements, created.id).waiting
    assert [(w.code, w.month, w.tacit_on) for w in waiting] == [
        ("report_undecided", D(2026, 10, 1), D(2026, 11, 12))
    ]
    assert [row.id for row in _listed(world.engagements, bucket="todo")] == [created.id]

    reports.approve(created.id, "2026-10", MonthlyReportApproval(), ADMIN)
    assert _item(world.engagements, created.id).waiting == []
    assert [row.id for row in _listed(world.engagements, bucket="ongoing")] == [created.id]


def test_a_month_nobody_decided_stops_waiting_the_day_after_the_tacit_date(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = world.engagement()
    MonthlyReportService(world.session, _settings()).request(
        created.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )
    assert _codes(_item(world.engagements, created.id)) == ["report_undecided"]

    monkeypatch.setattr(db_clock, "_now", lambda: NOW + dt.timedelta(days=3))  # the 13th
    assert _codes(_item(world.engagements, created.id)) == []
    assert world.engagements.counts(ADMIN).todo == 0


def test_a_concluded_engagement_still_waits_for_its_last_month(world: World) -> None:
    created = world.engagement()
    world.engagements.transition(created.id, "concluded", ADMIN, ended_on=world.period_end)
    MonthlyReportService(world.session, _settings()).request(
        created.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 3)), ADMIN
    )

    assert _codes(_item(world.engagements, created.id)) == ["report_undecided"]
    assert [row.id for row in _listed(world.engagements, bucket="todo")] == [created.id]
    assert _listed(world.engagements, bucket="closed") == []


def test_the_tabs_never_overlap_and_the_counts_add_up(world: World) -> None:
    waiting_draft = world.engagement(active=False)
    ongoing = world.engagement()
    signing = world.engagement(active=False)
    world.engagements.transition(signing.id, "awaiting_signature", ADMIN)
    closed = world.engagement()
    world.engagements.transition(closed.id, "concluded", ADMIN, ended_on=world.period_end)
    cancelled = world.engagement(active=False)
    world.engagements.cancel(cancelled.id, ADMIN)
    bare = world.engagement(deal_id=None)

    tabs = {
        bucket: {row.id for row in _listed(world.engagements, bucket=bucket)}
        for bucket in ("todo", "ongoing", "closed")
    }

    assert tabs["todo"] == {waiting_draft.id, bare.id}
    assert tabs["ongoing"] == {ongoing.id, signing.id}
    assert tabs["closed"] == {closed.id, cancelled.id}
    assert not (tabs["todo"] & tabs["ongoing"] or tabs["todo"] & tabs["closed"])
    everything = {row.id for row in _listed(world.engagements)}
    assert everything == tabs["todo"] | tabs["ongoing"] | tabs["closed"]
    counts = world.engagements.counts(ADMIN)
    assert (counts.todo, counts.ongoing, counts.closed) == (2, 2, 2)
    # A tab filters together with the others.
    assert {row.id for row in _listed(world.engagements, bucket="todo", state="draft")} == {
        waiting_draft.id
    }


def test_only_an_administrator_reads_the_counts_and_the_tabs(world: World) -> None:
    world.engagement(active=False)
    with pytest.raises(PermissionDenied):
        world.engagements.counts(COLLABORATOR)
    with pytest.raises(PermissionDenied):
        world.engagements.list(EngagementListQuery(bucket="todo"), COLLABORATOR)
    assert world.engagements.counts(AGENT).todo == 1


# ---- due items by source --------------------------------------------------------------------


def test_due_items_are_listed_by_the_record_that_wrote_them(world: World) -> None:
    first = world.engagement()
    second = world.engagement()
    items = DueItemService(world.session)

    page = items.list(
        DueItemListQuery(source_kind="engagement", source_id=first.id, state="forecast"), ADMIN
    )

    assert [item.source_id for item in page.items] == [first.id]
    only_second = items.list(DueItemListQuery(source_id=second.id), ADMIN)
    assert [item.source_id for item in only_second.items] == [second.id]
    # Engagement items are an administrator's: a collaborator filtering by the record sees none.
    assert items.list(DueItemListQuery(source_id=first.id), COLLABORATOR).items == []
    with pytest.raises(PermissionDenied):
        items.list(DueItemListQuery(source_kind="engagement", source_id=first.id), COLLABORATOR)


# ---- what an engagement pays, month by month ------------------------------------------------


def test_the_payments_name_the_month_and_the_client_invoice_a_date_follows(world: World) -> None:
    created = world.engagement()
    invoice = world.client_invoice()
    world.forecasts.pair_invoice(invoice)
    world.session.expire_all()

    page = EngagementPaymentService(world.session).for_engagement(created.id, ADMIN)

    (payment,) = page.items
    assert payment.month == world.period_start
    assert (payment.state, payment.due_date_estimated) == ("forecast", True)
    assert payment.waiting_for_invoice == f"{invoice.year}/{invoice.number}"
    assert payment.amount is not None and payment.amount > 0
    assert payment.remaining == payment.amount

    world.pay(world.client_item(invoice), "10000.00", world.today)
    again = EngagementPaymentService(world.session).for_engagement(created.id, ADMIN)
    assert again.items[0].waiting_for_invoice is None
    assert again.items[0].due_date_estimated is False


def test_a_forecast_something_replaced_is_not_a_payment(world: World) -> None:
    created = world.engagement()
    (forecast,) = world.forecast(created.id)
    DueItemService(world.session).drop(forecast.id, "Sostituita dalla fattura")

    assert EngagementPaymentService(world.session).for_engagement(created.id, ADMIN).items == []


def test_reading_the_payments_is_an_administrators_and_a_full_access_agents(world: World) -> None:
    created = world.engagement()
    service = EngagementPaymentService(world.session)
    with pytest.raises(PermissionDenied):
        service.for_engagement(created.id, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.for_engagement(created.id, AGENT)
    assert len(service.for_engagement(created.id, AGENT_FULL).items) == 1
    with pytest.raises(NotFound):
        service.for_engagement(uuid4(), ADMIN)


# ---- availability counts what the warning counts --------------------------------------------


def _make(service: EngagementService, who: Organization, **overrides: Any) -> EngagementRead:
    values: dict[str, Any] = {
        "freelancer_id": who.id,
        "role": "Backend developer",
        "starts_on": D(2026, 11, 9),
        "ends_on": D(2026, 11, 22),
        "days_per_week": Decimal("3"),
        "fee": Decimal("450.00"),
        "fee_unit": "day",
        "payment_rule": "fixed_term",
        "payment_days": 30,
    }
    values.update(overrides)
    return service.create(EngagementCreate(**values), ADMIN)


def test_availability_counts_a_draft_and_one_out_for_signature_as_the_warning_does(
    db_session: Session,
) -> None:
    service = EngagementService(db_session)
    who = make_customer(db_session, roles=["freelancer"], hub_freelancer_id=uuid4())
    draft = _make(service, who)
    signing = _make(service, who, days_per_week=Decimal("1"))
    service.transition(signing.id, "awaiting_signature", ADMIN)
    # The band says 4 days a week against the second one; the strip must say the same.
    assert [w.peak_days_per_week for w in service.get(signing.id, ADMIN).warnings] == [Decimal("4")]
    freelancers = FreelancerService(db_session)

    answer = freelancers.availability(
        ADMIN, freelancer_id=who.id, date_from=D(2026, 11, 9), date_to=D(2026, 11, 22)
    )

    assert [(w.committed_days, w.status) for w in answer.weeks] == [
        (Decimal("4"), "committed"),
        (Decimal("4"), "committed"),
    ]
    assert set(answer.weeks[0].engagement_ids) == {draft.id, signing.id}

    # An engagement's own page leaves it out, so a draft is not warned against itself.
    without = freelancers.availability(
        ADMIN,
        freelancer_id=who.id,
        date_from=D(2026, 11, 9),
        date_to=D(2026, 11, 22),
        excluding_engagement_id=signing.id,
    )
    assert [w.committed_days for w in without.weeks] == [Decimal("3"), Decimal("3")]
    # A cancelled engagement frees its weeks.
    service.cancel(draft.id, ADMIN)
    after = freelancers.availability(
        ADMIN, freelancer_id=who.id, date_from=D(2026, 11, 9), date_to=D(2026, 11, 15)
    )
    assert after.weeks[0].committed_days == Decimal("1")


def test_a_draft_without_days_a_week_makes_its_weeks_sconosciuta(db_session: Session) -> None:
    service = EngagementService(db_session)
    who = make_customer(db_session, roles=["freelancer"], hub_freelancer_id=uuid4())
    made = _make(service, who)
    db_session.execute(
        text("UPDATE engagements SET days_per_week = NULL WHERE id = :id"), {"id": made.id}
    )
    db_session.expire_all()

    answer = FreelancerService(db_session).availability(
        ADMIN, freelancer_id=who.id, date_from=D(2026, 11, 9), date_to=D(2026, 11, 15)
    )

    assert (answer.weeks[0].status, answer.weeks[0].label) == ("unknown", "sconosciuta")


# ---- the freelancer's own card, read through the door ---------------------------------------

OWNER = "owner@studio-rebase.it"
FREELANCER = "ada@studio.it"


def _two_spaces(door: "door_tests.Door") -> tuple[UUID, Settings, str, str]:
    """rebase's own space (the leads space) holding an engagement with two months reported, and
    the freelancer's space with the contract the door wrote for it. Returns the contract and the
    settings that name the leads space, and the two slugs (unique per call: a space is a database,
    and one left by another run must never be mistaken for this one)."""
    leads, slug = f"leads-{uuid4().hex[:8]}", f"ada-{uuid4().hex[:8]}"
    door_tests._provision(door, leads, OWNER)
    ada = door_tests._provision(door, slug, FREELANCER)
    match_id, contract_id = uuid4(), uuid4()
    settings = door.settings.model_copy(update={"leads_space": leads, "root_slug": "rebase"})
    with door_tests._space(door, leads) as space:
        who = make_customer(
            space,
            roles=["freelancer"],
            hub_freelancer_id=uuid4(),
            legal_name="Ada Lovelace",
            email=FREELANCER,
        )
        client = make_customer(space, legal_name="Cliente Segreto S.r.l.")
        service = EngagementService(space)
        made = service.create(
            EngagementCreate(
                freelancer_id=who.id,
                hub_match_id=match_id,
                client_organization_id=client.id,
                role="Backend developer",
                starts_on=D(2026, 9, 1),
                days_per_week=Decimal("3"),
                fee=Decimal("400.00"),
                fee_unit="day",
                payment_rule="on_collection",
                collection_days=15,
            ),
            ADMIN,
        )
        service.transition(made.id, "awaiting_signature", ADMIN)
        service.transition(made.id, "active", ADMIN)
        reports = MonthlyReportService(space, settings, space_slug=leads)
        reports.request(
            made.id, MonthlyReportRequest(month="2026-09", received_on=D(2026, 11, 3)), ADMIN
        )
        reports.approve(
            made.id,
            "2026-09",
            MonthlyReportApproval(
                held_dates=[D(2026, 9, 7), D(2026, 9, 8)], objection="Assente per il cliente"
            ),
            ADMIN,
        )
        reports.request(
            made.id, MonthlyReportRequest(month="2026-10", received_on=D(2026, 11, 9)), ADMIN
        )
        space.commit()
    with session_factory(door.registry)() as registry:
        registry.add(RebaseEngagement(match_id=match_id, tenant_id=ada, contract_id=contract_id))
        registry.commit()
    return contract_id, settings, leads, slug


def test_a_freelancer_reads_their_own_engagement_and_nothing_of_the_client(
    door: "door_tests.Door",
) -> None:
    contract_id, settings, leads, slug = _two_spaces(door)

    card = OwnEngagementService(door.registry, settings).for_contract(slug, contract_id)

    assert (card.fee, card.fee_unit, card.state) == (Decimal("400.00"), "day", "active")
    assert (card.payment_rule, card.collection_days) == ("on_collection", 15)
    months = {month.label: month for month in card.months}
    assert months["2026-09"].state == "approved"
    assert months["2026-09"].held_dates == [D(2026, 9, 7), D(2026, 9, 8)]
    assert months["2026-10"].state == "received"
    assert (months["2026-10"].received_on, months["2026-10"].answer_by) == (
        D(2026, 11, 9),
        D(2026, 11, 18),
    )
    assert months["2026-11"].state == "in_progress"
    dumped = card.model_dump_json().lower()
    for secret in ("cliente segreto", "assente per il cliente", "client", "objection", "budget"):
        assert secret not in dumped


def test_a_space_reads_only_the_engagement_of_its_own_contract(door: "door_tests.Door") -> None:
    contract_id, settings, leads, slug = _two_spaces(door)
    service = OwnEngagementService(door.registry, settings)

    with pytest.raises(NotFound):
        service.for_contract(leads, contract_id)  # another space, the same contract id
    with pytest.raises(NotFound):
        service.for_contract(slug, uuid4())  # a contract the door did not write
    with pytest.raises(NotFound):
        service.for_contract(None, contract_id)  # the root installation, no space
    no_leads = settings.model_copy(update={"leads_space": ""})
    with pytest.raises(NotFound):
        OwnEngagementService(door.registry, no_leads).for_contract(slug, contract_id)


def test_a_cancelled_engagement_has_no_card(door: "door_tests.Door") -> None:
    contract_id, settings, leads, slug = _two_spaces(door)
    with door_tests._space(door, leads) as space:
        space.execute(text("UPDATE engagements SET state = 'cancelled'"))
        space.commit()

    with pytest.raises(NotFound):
        OwnEngagementService(door.registry, settings).for_contract(slug, contract_id)


def test_a_match_that_belongs_to_another_freelancer_is_not_read(door: "door_tests.Door") -> None:
    contract_id, settings, leads, slug = _two_spaces(door)
    with door_tests._space(door, leads) as space:
        other = make_customer(
            space, roles=["freelancer"], hub_freelancer_id=uuid4(), email="grace@studio.it"
        )
        space.execute(text("UPDATE engagements SET freelancer_id = :id"), {"id": other.id})
        space.commit()

    with pytest.raises(NotFound):
        OwnEngagementService(door.registry, settings).for_contract(slug, contract_id)
