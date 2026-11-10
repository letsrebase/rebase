"""Engagements with freelancers (REB-976, collaborators design § 1 and § 3): the freelancer's
identity on the organization, the engagement's rules and states, the overlap warning and the
availability by week, and who may do what.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.schemas import (
    EngagementCreate,
    EngagementListQuery,
    EngagementRead,
    EngagementUpdate,
    FreelancerTaxData,
)
from pigrocrm.core.collaborators.service import EngagementService, FreelancerService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationCreate, OrganizationUpdate
from pigrocrm.core.organizations.service import OrganizationService

D = dt.date
# No user id: an organization an actor creates is owned by it, and these actors are not rows.
ADMIN = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]
COLLABORATOR = Actor(id=None, type="user", role="collaborator")  # type: ignore[arg-type]
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
MONDAY = D(2026, 11, 2)


def freelancer(session: Session, **overrides: object) -> Organization:
    values: dict[str, object] = {"roles": ["freelancer"], "hub_freelancer_id": uuid4()}
    values.update(overrides)
    return make_customer(session, **values)


def make(
    service: EngagementService, who: Organization, actor: Actor = ADMIN, **overrides: object
) -> EngagementRead:
    values: dict[str, object] = {
        "freelancer_id": who.id,
        "role": "Backend developer",
        "starts_on": MONDAY,
        "ends_on": MONDAY + dt.timedelta(days=27),
        "days_per_week": Decimal("3"),
        "fee": Decimal("450.00"),
        "fee_unit": "day",
        "payment_rule": "fixed_term",
        "payment_days": 30,
    }
    values.update(overrides)
    return service.create(EngagementCreate(**values), actor)  # type: ignore[arg-type]


def activate(service: EngagementService, engagement_id: UUID) -> EngagementRead:
    service.transition(engagement_id, "awaiting_signature", ADMIN)
    return service.transition(engagement_id, "active", ADMIN)


@pytest.fixture
def service(db_session: Session) -> EngagementService:
    return EngagementService(db_session)


# ---- the freelancer on the organization -----------------------------------------------------


def test_the_hub_freelancer_id_is_one_organization_and_needs_the_role(db_session: Session) -> None:
    organizations = OrganizationService(db_session)
    hub_id = uuid4()
    created = organizations.create(
        OrganizationCreate(
            legal_name="Ada Lovelace", roles=["freelancer"], hub_freelancer_id=hub_id
        ),
        ADMIN,
    )
    assert created.hub_freelancer_id == hub_id

    with pytest.raises(Conflict) as twice:
        organizations.create(
            OrganizationCreate(
                legal_name="Ada again", roles=["freelancer"], hub_freelancer_id=hub_id
            ),
            ADMIN,
        )
    assert twice.value.details["field"] == "hub_freelancer_id"
    with pytest.raises(ValidationFailed, match="ruolo di freelance"):
        organizations.create(
            OrganizationCreate(legal_name="Not one", hub_freelancer_id=uuid4()), ADMIN
        )


def test_the_hub_freelancer_id_is_an_admins_and_does_not_change(db_session: Session) -> None:
    organizations = OrganizationService(db_session)
    made = organizations.create(
        OrganizationCreate(legal_name="Grace Hopper", roles=["freelancer"]), COLLABORATOR
    )
    with pytest.raises(PermissionDenied):
        organizations.update(made.id, OrganizationUpdate(hub_freelancer_id=uuid4()), COLLABORATOR)
    with pytest.raises(AgentForbidden):
        organizations.update(made.id, OrganizationUpdate(hub_freelancer_id=uuid4()), AGENT_ADMIN)

    hub_id = uuid4()
    organizations.update(made.id, OrganizationUpdate(hub_freelancer_id=hub_id), ADMIN)
    with pytest.raises(ValidationFailed, match="non cambia"):
        organizations.update(made.id, OrganizationUpdate(hub_freelancer_id=uuid4()), ADMIN)
    with pytest.raises(ValidationFailed, match="non si toglie"):
        organizations.update(made.id, OrganizationUpdate(hub_freelancer_id=None), ADMIN)
    with pytest.raises(ValidationFailed, match="ruolo di freelance"):
        organizations.update(made.id, OrganizationUpdate(roles=["customer"]), ADMIN)
    # Said again unchanged is no change, and nobody's permission to ask for.
    again = organizations.update(
        made.id, OrganizationUpdate(hub_freelancer_id=hub_id, legal_name="Grace B. Hopper"), ADMIN
    )
    assert again.legal_name == "Grace B. Hopper"


def test_a_freelancer_is_a_professional_a_flat_rate_or_an_occasional_one(
    db_session: Session,
) -> None:
    organizations = OrganizationService(db_session)
    for kind in ("professional", "flat_rate", "occasional"):
        organizations.create(
            OrganizationCreate(legal_name=f"F {kind}", roles=["freelancer"], supplier_kind=kind),
            ADMIN,
        )
    for kind in ("company", "foreign"):
        with pytest.raises(ValidationFailed, match="professionista"):
            organizations.create(
                OrganizationCreate(legal_name="F", roles=["freelancer"], supplier_kind=kind), ADMIN
            )
    # A supplier that is not a freelancer keeps every kind.
    organizations.create(
        OrganizationCreate(legal_name="S", roles=["supplier"], supplier_kind="company"), ADMIN
    )


def test_the_database_refuses_a_hub_id_without_the_role(db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        make_customer(db_session, hub_freelancer_id=uuid4())


# ---- an engagement is created and validated -------------------------------------------------


def test_an_engagement_starts_as_a_draft_with_the_letter_its_freelancer_asks_for(
    db_session: Session, service: EngagementService
) -> None:
    ordinary = make(service, freelancer(db_session, supplier_kind="professional"))
    occasional = make(service, freelancer(db_session, supplier_kind="occasional"))
    unclassified = make(service, freelancer(db_session))

    assert ordinary.state == "draft"
    assert (ordinary.letter_type, occasional.letter_type, unclassified.letter_type) == (
        "ordinary",
        "occasional",
        "ordinary",
    )
    assert ordinary.missing == ["client"]
    with pytest.raises(ValidationFailed, match="lettera"):
        make(service, freelancer(db_session, supplier_kind="occasional"), letter_type="ordinary")


def test_the_freelancer_must_exist_and_have_the_role(
    db_session: Session, service: EngagementService
) -> None:
    with pytest.raises(NotFound):
        make(service, Organization(id=uuid4(), legal_name="Nobody"))
    with pytest.raises(ValidationFailed, match="ruolo di freelance"):
        make(service, make_customer(db_session))


def test_days_per_week_is_required_and_a_half_day_step_from_half_a_day_to_five(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    with pytest.raises(ValidationFailed, match="giorni a settimana") as missing:
        make(service, who, days_per_week=None)
    assert missing.value.details["field"] == "days_per_week"
    for bad in ("0", "0.4", "0.7", "5.5", "6"):
        with pytest.raises(ValidationFailed, match="da 0,5 a 5"):
            make(service, who, days_per_week=Decimal(bad))
    for good in ("0.5", "2.5", "5"):
        assert make(service, who, days_per_week=Decimal(good)).days_per_week == Decimal(good)


def test_the_fee_and_the_dates_are_checked(db_session: Session, service: EngagementService) -> None:
    who = freelancer(db_session)
    with pytest.raises(ValidationFailed, match="maggiore di zero"):
        make(service, who, fee=Decimal("0"))
    with pytest.raises(ValidationFailed, match="due decimali"):
        make(service, who, fee=Decimal("10.001"))
    with pytest.raises(ValidationFailed, match="valore non valido"):
        make(service, who, fee_unit="week")
    with pytest.raises(ValidationFailed, match="prima dell'inizio"):
        make(service, who, ends_on=MONDAY - dt.timedelta(days=1))
    with pytest.raises(ValidationFailed, match="giorni previsti"):
        make(service, who, expected_days=Decimal("0"))
    assert make(service, who, ends_on=None, fee_unit="flat").ends_on is None


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"payment_days": 61}, "da 1 a 60"),
        ({"payment_days": 0}, "da 1 a 60"),
        ({"payment_days": 31, "payment_end_of_month": True}, "da 1 a 30"),
        ({"payment_days": None}, "serve il termine"),
        ({"collection_days": 20}, "valgono solo se il pagamento segue il cliente"),
        (
            {"payment_rule": "on_collection", "payment_days": None, "collection_days": 61},
            "da 1 a 60",
        ),
        (
            {"payment_rule": "on_collection", "payment_days": None, "collection_days": 0},
            "da 1 a 60",
        ),
        ({"payment_rule": "on_collection", "payment_days": None}, "servono i giorni"),
        ({"payment_rule": "sometime"}, "valore non valido"),
    ],
)
def test_a_payment_rule_the_law_or_the_letter_does_not_allow_is_refused(
    db_session: Session, service: EngagementService, values: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        make(service, freelancer(db_session), **values)


def test_paid_after_the_client_has_no_fixed_term_floor(
    db_session: Session, service: EngagementService
) -> None:
    """The printed clause of the letter (REB-979) names no floor: a stored term next to
    `collection_days` would be a rule the letter does not say."""
    who = freelancer(db_session)
    with pytest.raises(ValidationFailed, match="non c'e' un termine fisso"):
        make(service, who, payment_rule="on_collection", collection_days=15, payment_days=30)
    with pytest.raises(ValidationFailed, match="non c'e' un termine fisso"):
        make(
            service,
            who,
            payment_rule="on_collection",
            collection_days=15,
            payment_days=None,
            payment_end_of_month=True,
        )
    for days in (1, 60):
        made = make(
            service, who, payment_rule="on_collection", collection_days=days, payment_days=None
        )
        assert (made.payment_rule, made.collection_days, made.payment_days) == (
            "on_collection",
            days,
            None,
        )
    month_end = make(service, who, payment_days=30, payment_end_of_month=True)
    assert month_end.payment_end_of_month is True


def test_the_schema_holds_the_payment_rule_the_service_does(db_session: Session) -> None:
    from pigrocrm.core.collaborators.models import Engagement

    who = freelancer(db_session)
    with pytest.raises(IntegrityError):
        db_session.add(
            Engagement(
                freelancer_id=who.id,
                role="x",
                starts_on=MONDAY,
                fee=Decimal("1"),
                fee_unit="day",
                payment_rule="on_collection",
                payment_days=30,
                collection_days=10,
            )
        )
        db_session.flush()


def test_the_client_follows_the_deal_or_contract_and_is_never_the_freelancer(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    client = make_customer(db_session)
    other = make_customer(db_session)
    made = make(service, who, client_organization_id=client.id)
    assert made.client_organization_id == client.id
    with pytest.raises(ValidationFailed, match="freelance stesso"):
        make(service, who, client_organization_id=who.id)
    with pytest.raises(NotFound):
        make(service, who, client_organization_id=uuid4())
    with pytest.raises(NotFound):
        make(service, who, deal_id=uuid4())
    with pytest.raises(NotFound):
        make(service, who, contract_id=uuid4())
    assert other.id != client.id


def test_a_hub_match_is_imported_once(db_session: Session, service: EngagementService) -> None:
    who = freelancer(db_session)
    match = uuid4()
    first = make(service, who, hub_match_id=match)
    with pytest.raises(Conflict) as again:
        make(service, who, hub_match_id=match)
    assert again.value.details["field"] == "hub_match_id"
    assert first.hub_match_id == match


# ---- states ---------------------------------------------------------------------------------


def test_the_states_follow_the_table_and_nothing_goes_back(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    made = make(service, who)
    assert {state: rules.TRANSITIONS[state] for state in rules.TRANSITIONS} == {
        "draft": ("awaiting_signature", "cancelled"),
        "awaiting_signature": ("active", "cancelled"),
        "active": ("concluded", "cancelled"),
        "concluded": (),
        "cancelled": (),
    }
    for target in ("active", "concluded", "draft"):
        with pytest.raises(ValidationFailed, match="non si passa"):
            service.transition(made.id, target, ADMIN)
    with pytest.raises(ValidationFailed, match="valore non valido"):
        service.transition(made.id, "paid", ADMIN)

    assert service.transition(made.id, "awaiting_signature", ADMIN).state == "awaiting_signature"
    assert service.transition(made.id, "active", ADMIN).state == "active"
    with pytest.raises(ValidationFailed):
        service.cancel(made.id, ADMIN)
    closed = service.close(made.id, ADMIN, D(2026, 11, 13))
    assert (closed.state, closed.ends_on) == ("concluded", D(2026, 11, 13))
    with pytest.raises(ValidationFailed):
        service.close(made.id, ADMIN)


def test_cancelling_works_from_a_draft_and_from_out_for_signature(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    draft = make(service, who)
    sent = make(service, who)
    service.transition(sent.id, "awaiting_signature", ADMIN)

    assert service.cancel(draft.id, ADMIN).state == "cancelled"
    assert service.cancel(sent.id, ADMIN).state == "cancelled"
    with pytest.raises(ValidationFailed):
        service.cancel(sent.id, ADMIN)


def test_closing_ends_an_open_ended_engagement_and_never_extends_one(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    open_ended = make(service, who, ends_on=None)
    short = make(service, who, ends_on=D(2026, 11, 6))
    for made in (open_ended, short):
        activate(service, made.id)

    assert service.close(open_ended.id, ADMIN, D(2026, 12, 4)).ends_on == D(2026, 12, 4)
    assert service.close(short.id, ADMIN, D(2026, 12, 4)).ends_on == D(2026, 11, 6)
    third = make(service, who)
    activate(service, third.id)
    with pytest.raises(ValidationFailed, match="prima di essere iniziato"):
        service.close(third.id, ADMIN, MONDAY - dt.timedelta(days=1))
    with pytest.raises(ValidationFailed, match="solo chiudendo"):
        service.transition(make(service, who).id, "awaiting_signature", ADMIN, ended_on=MONDAY)


# ---- updating -------------------------------------------------------------------------------


def test_a_draft_changes_freely_and_a_restated_rule_clears_what_it_does_not_use(
    db_session: Session, service: EngagementService
) -> None:
    made = make(service, freelancer(db_session))

    updated = service.update(
        made.id,
        EngagementUpdate(role="Tech lead", fee=Decimal("500"), days_per_week=Decimal("2.5")),
        ADMIN,
    )
    assert (updated.role, updated.fee, updated.days_per_week) == (
        "Tech lead",
        Decimal("500.00"),
        Decimal("2.5"),
    )
    switched = service.update(
        made.id, EngagementUpdate(payment_rule="on_collection", collection_days=21), ADMIN
    )
    assert (switched.payment_days, switched.collection_days) == (None, 21)
    back = service.update(
        made.id, EngagementUpdate(payment_rule="fixed_term", payment_days=45), ADMIN
    )
    assert (back.payment_days, back.collection_days) == (45, None)
    with pytest.raises(ValidationFailed, match="valgono solo se il pagamento segue il cliente"):
        service.update(made.id, EngagementUpdate(payment_days=30, collection_days=10), ADMIN)
    with pytest.raises(ValidationFailed, match="due decimali"):
        service.update(made.id, EngagementUpdate(fee=Decimal("1.234")), ADMIN)


def test_once_sent_only_the_days_and_the_client_change(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    made = make(service, who)
    activate(service, made.id)

    with pytest.raises(Conflict, match="la lettera e' gia' partita") as frozen:
        service.update(
            made.id, EngagementUpdate(fee=Decimal("1"), days_per_week=Decimal("1")), ADMIN
        )
    assert frozen.value.details["fields"] == ["fee"]
    client = make_customer(db_session)
    kept = service.update(
        made.id,
        EngagementUpdate(days_per_week=Decimal("1.5"), client_organization_id=client.id),
        ADMIN,
    )
    assert (kept.days_per_week, kept.client_organization_id, kept.fee) == (
        Decimal("1.5"),
        client.id,
        Decimal("450.00"),
    )
    service.close(made.id, ADMIN, D(2026, 11, 13))
    with pytest.raises(Conflict, match="chiuso"):
        service.update(made.id, EngagementUpdate(days_per_week=Decimal("2")), ADMIN)


def test_filling_in_what_an_import_lacked_never_trips_over_the_rest_of_the_letter(
    db_session: Session, service: EngagementService
) -> None:
    """An engagement without `days_per_week` is active; the admin fills it, and the letter type
    or the term it came with is not re-judged against a freelancer classified since."""
    who = freelancer(db_session)
    made = make(service, who)
    service.update(made.id, EngagementUpdate(days_per_week=None), ADMIN)
    activate(service, made.id)
    assert service.get(made.id, ADMIN).missing == ["days_per_week", "client"]
    # The freelancer is classified occasional afterwards: the ordinary letter stays as it is.
    who.supplier_kind = "occasional"
    db_session.flush()

    filled = service.update(made.id, EngagementUpdate(days_per_week=Decimal("4")), ADMIN)
    assert filled.days_per_week == Decimal("4")
    assert filled.missing == ["client"]


# ---- the overlap warning --------------------------------------------------------------------


def test_two_overlapping_engagements_of_one_freelancer_raise_the_warning(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    first = make(service, who, days_per_week=Decimal("3"))
    assert first.warnings == []

    second = make(
        service,
        who,
        starts_on=MONDAY + dt.timedelta(days=14),
        ends_on=MONDAY + dt.timedelta(days=41),
        days_per_week=Decimal("1.5"),
    )

    assert [w.code for w in second.warnings] == ["overlap"]
    warning = second.warnings[0]
    assert warning.engagement_ids == [first.id]
    assert warning.peak_days_per_week == Decimal("4.5")
    assert warning.message == (
        "Si sovrappone a un altro incarico dello stesso freelance: nelle settimane in comune "
        "e' impegnato fino a 4,5 giorni a settimana."
    )
    # The first one reads the same overlap from the other side, and the list leaves it out.
    assert [w.code for w in service.get(first.id, ADMIN).warnings] == ["overlap"]
    assert all(item.warnings == [] for item in service.list(EngagementListQuery(), ADMIN).items)


def test_engagements_that_do_not_share_a_day_or_belong_to_two_freelancers_warn_of_nothing(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    make(service, who, ends_on=D(2026, 11, 13))
    next_day = make(service, who, starts_on=D(2026, 11, 14), ends_on=None)
    elsewhere = make(service, freelancer(db_session), ends_on=None)

    assert next_day.warnings == [] and elsewhere.warnings == []
    # A cancelled engagement is not a commitment to warn about.
    other = freelancer(db_session)
    done = make(service, other, starts_on=D(2027, 2, 1), ends_on=D(2027, 2, 12))
    service.cancel(done.id, ADMIN)
    assert make(service, other, starts_on=D(2027, 2, 1), ends_on=D(2027, 2, 12)).warnings == []


def test_more_than_five_days_a_week_is_a_second_warning(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    make(service, who, days_per_week=Decimal("3"))
    over = make(service, who, days_per_week=Decimal("3"))

    assert [w.code for w in over.warnings] == ["overlap", "over_capacity"]
    assert over.warnings[1].peak_days_per_week == Decimal("6")
    exactly = make(
        freelancer_service := service, freelancer(db_session), days_per_week=Decimal("5")
    )
    assert exactly.warnings == [] and freelancer_service is service


def test_an_overlap_with_an_engagement_of_unknown_days_says_so(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    made = make(service, who)
    service.update(made.id, EngagementUpdate(days_per_week=None), ADMIN)

    second = make(service, who)

    assert [w.code for w in second.warnings] == ["overlap"]
    assert second.warnings[0].peak_days_per_week is None
    assert "sconosciuta" in second.warnings[0].message


def test_the_overlap_is_read_across_open_ended_engagements(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    make(service, who, ends_on=None, days_per_week=Decimal("4"))
    later = make(service, who, starts_on=D(2027, 3, 1), ends_on=None, days_per_week=Decimal("2"))
    assert later.warnings[0].peak_days_per_week == Decimal("6")


# ---- availability ---------------------------------------------------------------------------


def test_availability_shows_the_committed_days_per_week_from_active_engagements(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    first = make(
        service, who, ends_on=D(2026, 11, 29), days_per_week=Decimal("3")
    )  # weeks 45 to 48
    second = make(
        service,
        who,
        starts_on=D(2026, 11, 16),
        ends_on=D(2026, 12, 13),
        days_per_week=Decimal("2"),
    )  # weeks 47 to 50
    draft = make(service, who, days_per_week=Decimal("1"))
    for made in (first, second):
        activate(service, made.id)
    freelancers = FreelancerService(db_session)

    answer = freelancers.availability(
        ADMIN, freelancer_id=who.id, date_from=D(2026, 11, 4), date_to=D(2026, 12, 20)
    )

    assert [(w.week, w.committed_days, w.status) for w in answer.weeks] == [
        ("2026-W45", Decimal("3"), "committed"),
        ("2026-W46", Decimal("3"), "committed"),
        ("2026-W47", Decimal("5"), "committed"),
        ("2026-W48", Decimal("5"), "committed"),
        ("2026-W49", Decimal("2"), "committed"),
        ("2026-W50", Decimal("2"), "committed"),
        ("2026-W51", Decimal("0"), "free"),
    ]
    assert answer.weeks[0].starts_on == MONDAY and answer.weeks[0].ends_on == D(2026, 11, 8)
    assert answer.weeks[2].engagement_ids == [first.id, second.id]
    assert answer.weeks[2].free_days == Decimal("0")
    assert answer.weeks[0].free_days == Decimal("2")
    assert answer.weeks[6].free_days == Decimal("5") and answer.weeks[6].label == "libera"
    assert draft.id not in {i for week in answer.weeks for i in week.engagement_ids}
    assert (answer.date_from, answer.hub_freelancer_id) == (MONDAY, who.hub_freelancer_id)


def test_an_engagement_without_days_per_week_makes_its_weeks_sconosciuta_never_libera(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    known = make(service, who, ends_on=D(2026, 11, 8), days_per_week=Decimal("2"))
    unknown = make(service, who, starts_on=D(2026, 11, 9), ends_on=D(2026, 11, 15))
    service.update(unknown.id, EngagementUpdate(days_per_week=None), ADMIN)
    for made in (known, unknown):
        activate(service, made.id)

    answer = FreelancerService(db_session).availability(
        ADMIN, freelancer_id=who.id, date_from=MONDAY, date_to=D(2026, 11, 22)
    )

    statuses = [(w.status, w.label, w.free_days) for w in answer.weeks]
    assert statuses == [
        ("committed", "impegnata", Decimal("3")),
        ("unknown", "sconosciuta", None),
        ("free", "libera", Decimal("5")),
    ]
    assert answer.weeks[1].committed_days == Decimal("0")


def test_availability_is_found_by_the_hub_id_and_defaults_to_twelve_weeks(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    freelancers = FreelancerService(db_session)

    by_hub = freelancers.availability(ADMIN, hub_freelancer_id=who.hub_freelancer_id)

    assert by_hub.freelancer_id == who.id
    assert len(by_hub.weeks) == 12
    assert {w.status for w in by_hub.weeks} == {"free"}
    assert by_hub.weeks[0].starts_on.weekday() == 0


def test_availability_refuses_what_it_cannot_answer(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    freelancers = FreelancerService(db_session)
    with pytest.raises(ValidationFailed, match="uno dei due"):
        freelancers.availability(ADMIN)
    with pytest.raises(ValidationFailed, match="uno dei due"):
        freelancers.availability(ADMIN, freelancer_id=who.id, hub_freelancer_id=uuid4())
    with pytest.raises(NotFound):
        freelancers.availability(ADMIN, hub_freelancer_id=uuid4())
    with pytest.raises(NotFound):
        freelancers.availability(ADMIN, freelancer_id=make_customer(db_session).id)
    with pytest.raises(ValidationFailed, match="prima dell'inizio"):
        freelancers.availability(
            ADMIN, freelancer_id=who.id, date_from=MONDAY, date_to=D(2026, 1, 1)
        )
    with pytest.raises(ValidationFailed, match="104 settimane"):
        freelancers.availability(
            ADMIN, freelancer_id=who.id, date_from=MONDAY, date_to=MONDAY + dt.timedelta(weeks=105)
        )


def test_the_week_arithmetic_is_iso() -> None:
    assert rules.iso_week(D(2026, 12, 31)) == "2026-W53"
    assert rules.iso_week(D(2027, 1, 3)) == "2026-W53"
    assert rules.iso_week(D(2027, 1, 4)) == "2027-W01"
    assert rules.monday(D(2026, 11, 8)) == MONDAY
    assert len(rules.weeks(D(2026, 11, 8), D(2026, 11, 9))) == 2


# ---- who may do what ------------------------------------------------------------------------


def test_every_engagement_read_and_write_is_an_admins(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    made = make(service, who)
    freelancers = FreelancerService(db_session)
    calls = [
        lambda: make(service, who, COLLABORATOR),
        lambda: service.get(made.id, COLLABORATOR),
        lambda: service.list(EngagementListQuery(), COLLABORATOR),
        lambda: service.update(made.id, EngagementUpdate(role="x"), COLLABORATOR),
        lambda: service.transition(made.id, "awaiting_signature", COLLABORATOR),
        lambda: service.cancel(made.id, COLLABORATOR),
        lambda: freelancers.availability(COLLABORATOR, freelancer_id=who.id),
        lambda: freelancers.set_tax_data(who.id, FreelancerTaxData(tax_code="X"), COLLABORATOR),
    ]
    for call in calls:
        with pytest.raises(PermissionDenied):
            call()


def test_an_agent_reads_and_updates_a_draft_but_needs_full_access_for_the_rest(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    made = make(service, who)
    freelancers = FreelancerService(db_session)

    assert service.get(made.id, AGENT_ADMIN).id == made.id
    assert service.list(EngagementListQuery(), AGENT_ADMIN).items
    assert freelancers.availability(AGENT_ADMIN, freelancer_id=who.id).weeks
    assert (
        service.update(made.id, EngagementUpdate(role="Tech lead"), AGENT_ADMIN).role == "Tech lead"
    )

    for call in (
        lambda: make(service, who, AGENT_ADMIN),
        lambda: service.cancel(made.id, AGENT_ADMIN),
        lambda: service.close(made.id, AGENT_ADMIN),
        lambda: service.transition(made.id, "awaiting_signature", AGENT_ADMIN),
        lambda: freelancers.set_tax_data(who.id, FreelancerTaxData(tax_code="X"), AGENT_ADMIN),
    ):
        with pytest.raises(AgentForbidden):
            call()

    created = make(service, who, AGENT_ADMIN_FULL)
    assert service.cancel(created.id, AGENT_ADMIN_FULL).state == "cancelled"


def test_the_signature_that_makes_an_engagement_active_is_a_persons_act(
    db_session: Session, service: EngagementService
) -> None:
    made = make(service, freelancer(db_session))
    service.transition(made.id, "awaiting_signature", ADMIN)

    with pytest.raises(AgentForbidden):
        service.transition(made.id, "active", AGENT_ADMIN_FULL)
    assert service.transition(made.id, "active", ADMIN).state == "active"


def test_an_engagement_that_does_not_exist_is_not_found(service: EngagementService) -> None:
    with pytest.raises(NotFound):
        service.get(uuid4(), ADMIN)
    with pytest.raises(NotFound):
        service.update(uuid4(), EngagementUpdate(role="x"), ADMIN)


def test_the_list_filters_and_pages_newest_first(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    other = freelancer(db_session)
    made = [make(service, who) for _ in range(3)]
    make(service, other)
    service.cancel(made[0].id, ADMIN)

    mine = service.list(EngagementListQuery(freelancer_id=who.id), ADMIN)
    assert [item.id for item in mine.items] == [e.id for e in reversed(made)]
    first = service.list(EngagementListQuery(freelancer_id=who.id, limit=2), ADMIN)
    assert len(first.items) == 2 and first.next_cursor == first.items[-1].id
    rest = service.list(
        EngagementListQuery(freelancer_id=who.id, limit=2, cursor=first.next_cursor), ADMIN
    )
    assert [item.id for item in rest.items] == [made[0].id] and rest.next_cursor is None
    cancelled = service.list(EngagementListQuery(state="cancelled"), ADMIN)
    assert [item.id for item in cancelled.items] == [made[0].id]
    with pytest.raises(ValidationFailed):
        service.list(EngagementListQuery(state="paid"), ADMIN)


# ---- the tax data ---------------------------------------------------------------------------


def test_the_tax_data_lands_on_the_organization_and_the_kind_is_checked(
    db_session: Session,
) -> None:
    who = freelancer(db_session)
    freelancers = FreelancerService(db_session)

    saved = freelancers.set_tax_data(
        who.id,
        FreelancerTaxData(
            tax_code="RSSMRA80A01H501U",
            vat_number="01234567897",
            domicile="Via Roma 1",
            certified_email="mario@pec.it",
            fiscal_kind="flat_rate",
        ),
        ADMIN,
    )

    assert (saved.tax_code, saved.vat_number, saved.address, saved.supplier_kind) == (
        "RSSMRA80A01H501U",
        "01234567897",
        "Via Roma 1",
        "flat_rate",
    )
    only_kind = freelancers.set_tax_data(who.id, FreelancerTaxData(fiscal_kind="occasional"), ADMIN)
    assert (only_kind.supplier_kind, only_kind.tax_code) == ("occasional", "RSSMRA80A01H501U")
    with pytest.raises(ValidationFailed, match="professionista"):
        freelancers.set_tax_data(who.id, FreelancerTaxData(fiscal_kind="company"), ADMIN)
    with pytest.raises(ValidationFailed, match="valore non valido"):
        freelancers.set_tax_data(who.id, FreelancerTaxData(fiscal_kind="astronaut"), ADMIN)
    with pytest.raises(ValidationFailed, match="nessun dato fiscale"):
        freelancers.set_tax_data(who.id, FreelancerTaxData(), ADMIN)
    with pytest.raises(ValidationFailed, match="ruolo di freelance"):
        freelancers.set_tax_data(
            make_customer(db_session).id, FreelancerTaxData(tax_code="X"), ADMIN
        )
    with pytest.raises(NotFound):
        freelancers.set_tax_data(uuid4(), FreelancerTaxData(tax_code="X"), ADMIN)


# ---- review findings ----------------------------------------------------------------------------


def test_an_engagement_starting_late_in_the_last_week_still_commits_that_week(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    # Starts on the Friday of the week the answer ends on (date_to is the Wednesday of it).
    made = make(service, who, starts_on=D(2026, 11, 13), ends_on=None, days_per_week=Decimal("2"))
    activate(service, made.id)

    answer = FreelancerService(db_session).availability(
        ADMIN, freelancer_id=who.id, date_from=MONDAY, date_to=D(2026, 11, 11)
    )

    assert [(w.week, w.status) for w in answer.weeks] == [
        ("2026-W45", "free"),
        ("2026-W46", "committed"),
    ]


def test_a_fee_too_long_for_its_column_is_refused_before_the_database() -> None:
    from pydantic import ValidationError

    for bad in ("1" + "0" * 12, "1E+20"):
        with pytest.raises(ValidationError):
            EngagementCreate(
                freelancer_id=uuid4(),
                role="x",
                starts_on=MONDAY,
                fee=Decimal(bad),
                fee_unit="day",
                payment_rule="fixed_term",
                payment_days=30,
            )
    with pytest.raises(ValidationError):
        EngagementUpdate(expected_days=Decimal("1" + "0" * 8))


def test_an_archived_link_does_not_stop_filling_in_the_days(
    db_session: Session, service: EngagementService
) -> None:
    from datetime import UTC, datetime

    who = freelancer(db_session)
    client = make_customer(db_session)
    made = make(service, who, client_organization_id=client.id)
    service.update(made.id, EngagementUpdate(days_per_week=None), ADMIN)
    activate(service, made.id)
    client.deleted_at = datetime.now(UTC)
    who.deleted_at = datetime.now(UTC)
    db_session.flush()

    filled = service.update(made.id, EngagementUpdate(days_per_week=Decimal("2")), ADMIN)

    assert filled.days_per_week == Decimal("2")
    # Naming the archived client in a change is still refused.
    with pytest.raises(NotFound):
        service.update(made.id, EngagementUpdate(client_organization_id=client.id), ADMIN)


def test_an_active_engagement_can_be_called_off_only_before_it_starts(
    db_session: Session, service: EngagementService
) -> None:
    who = freelancer(db_session)
    future = make(service, who, starts_on=D(2099, 1, 4), ends_on=D(2099, 2, 1))
    started = make(service, who)
    for made in (future, started):
        activate(service, made.id)

    assert service.cancel(future.id, ADMIN).state == "cancelled"
    with pytest.raises(ValidationFailed, match="si chiude, non si annulla"):
        service.cancel(started.id, ADMIN)


def test_what_is_the_clients_is_left_out_for_an_admin_who_cannot_see_the_client(
    db_session: Session, service: EngagementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row policy answers «not visible» for the client of a scoped admin; the stand-in here
    is that answer, the policy itself being `test_legal_entity_scope`'s."""
    who = freelancer(db_session)
    client = make_customer(db_session)
    made = make(service, who, client_organization_id=client.id)
    activate(service, made.id)
    from pigrocrm.core.collaborators.repository import EngagementRepository

    monkeypatch.setattr(EngagementRepository, "visible_organization", lambda self, i: None)
    read = service.get(made.id, ADMIN)

    assert read.redacted is True
    assert (read.role, read.fee, read.client_organization_id) == (None, None, None)
    assert read.days_per_week == Decimal("3") and read.state == "active"
