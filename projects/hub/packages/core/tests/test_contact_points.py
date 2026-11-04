"""Contact points (REB-943): an extra address, phone or profile on a person, the rules that keep
an address from being two people's, and the doors that must still mean the login address."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from admins import an_admin
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    clean,
    draft,
    person,
)
from freelancer_fixtures import apply_freelancer
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate, CompanyOverride
from rebase_core.contact_points import ContactPointInput, ContactPointService
from rebase_core.errors import ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerOverride
from rebase_core.freelancers import FreelancerService
from rebase_core.merge import PersonMergeService
from rebase_core.models import AdminAction, CampaignRecipient, Freelancer, User, UserContactPoint
from rebase_core.users import UserService


def _point(kind: str, value: str, label: str = "work", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "value": value, "label": label, **extra}


def _override(session: Session, freelancer_id: UUID, *points: dict[str, Any]) -> Any:
    return FreelancerService(session).override(
        freelancer_id,
        FreelancerOverride(contact_points=list(points)),  # type: ignore[arg-type]
        an_admin(session),
    )


def _user(session: Session, freelancer_id: UUID) -> User:
    card = session.get(Freelancer, freelancer_id)
    assert card is not None
    user = session.get(User, card.user_id)
    assert user is not None
    return user


def test_an_admin_adds_an_extra_email_and_the_read_puts_the_login_first(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    read = _override(
        clean,
        ada,
        _point("email", "Ada.Personal@Gmail.com", "personal", note="solo per le fatture"),
        _point("phone", "+39 333 123 4567"),
    )
    assert [(p.kind, p.value, p.label, p.is_primary, p.position) for p in read.contact_points] == [
        ("email", "ada@studio.it", "work", True, 0),
        ("email", "Ada.Personal@Gmail.com", "personal", False, 1),
        ("phone", "+39 333 123 4567", "work", True, 2),
    ]
    assert read.contact_points[0].id is None and read.contact_points[1].id is not None
    assert read.contact_points[1].note == "solo per le fatture"
    assert read.phone == "+39 333 123 4567"
    # The login address is untouched: it is the identity.
    assert _user(clean, ada).email == "ada@studio.it"


def test_the_first_phone_is_primary_and_a_second_one_is_not(clean: Session) -> None:  # noqa: F811
    ada = apply_freelancer(clean, "ada@studio.it")
    read = _override(
        clean,
        ada,
        _point("phone", "+39 333 111 2222", "personal"),
        _point("phone", "02 5550199", "billing"),
    )
    assert [(p.value, p.is_primary) for p in read.contact_points if p.kind == "phone"] == [
        ("+39 333 111 2222", True),
        ("02 5550199", False),
    ]
    flipped = _override(
        clean,
        ada,
        _point("phone", "+39 333 111 2222", "personal"),
        _point("phone", "02 5550199", "billing", is_primary=True),
    )
    assert flipped.phone == "02 5550199"


def test_two_primaries_of_one_kind_and_a_primary_extra_email_are_refused(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    with pytest.raises(ValidationFailed, match="più di un recapito principale"):
        _override(
            clean,
            ada,
            _point("phone", "+39 333 111 2222", is_primary=True),
            _point("phone", "02 5550199", is_primary=True),
        )
    with pytest.raises(ValidationFailed, match="quello con cui la persona accede"):
        _override(clean, ada, _point("email", "altra@studio.it", is_primary=True))


def test_the_same_value_twice_on_one_person_is_refused_by_what_it_means(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    with pytest.raises(ValidationFailed, match="scritto due volte"):
        _override(
            clean, ada, _point("email", "Altra@studio.it"), _point("email", "altra@STUDIO.it")
        )
    with pytest.raises(ValidationFailed, match="scritto due volte"):
        _override(clean, ada, _point("phone", "+39 333 1234567"), _point("phone", "39-333-1234567"))


def test_the_login_address_is_not_an_extra_one_but_a_list_read_can_be_sent_back(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    with pytest.raises(ValidationFailed, match="già mostrato come principale"):
        _override(clean, ada, _point("email", "ADA@studio.it"))
    first = _override(clean, ada, _point("email", "altra@studio.it", "other"))
    again = FreelancerOverride(
        contact_points=[
            ContactPointInput.model_validate(p.model_dump()) for p in first.contact_points
        ]
    )
    sent_back = FreelancerService(clean).override(ada, again, an_admin(clean))
    assert [p.value for p in sent_back.contact_points] == ["ada@studio.it", "altra@studio.it"]


def test_an_extra_email_that_is_another_persons_login_or_extra_address_is_refused_and_named(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    grace = apply_freelancer(clean, "grace@studio.it", first_name="Grace", last_name="Hopper")
    _override(clean, ada, _point("email", "ada.casa@gmail.com", "personal"))

    with pytest.raises(ValidationFailed, match=r"«grace@studio.it».*con cui un'altra persona"):
        _override(clean, ada, _point("email", "Grace@Studio.it"))
    with pytest.raises(ValidationFailed, match=r"«ada.casa@gmail.com».*tra i recapiti"):
        _override(clean, grace, _point("email", "ADA.casa@gmail.com"))
    # Nothing was written for Grace.
    assert _user(clean, grace).contact_points == []


def test_the_database_holds_an_address_to_one_person_even_without_the_service(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    from sqlalchemy.exc import IntegrityError

    ada = _user(clean, apply_freelancer(clean, "ada@studio.it"))
    grace = _user(clean, apply_freelancer(clean, "grace@studio.it", first_name="Grace"))
    clean.add(UserContactPoint(user_id=ada.id, kind="email", value="Shared@x.it", label="work"))
    clean.commit()
    clean.add(UserContactPoint(user_id=grace.id, kind="email", value="shared@X.it", label="work"))
    with pytest.raises(IntegrityError):
        clean.commit()
    clean.rollback()


def test_a_signup_with_somebodys_extra_address_is_that_person_not_a_new_one(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it")
    _override(clean, ada, _point("email", "ada.casa@gmail.com", "personal"))
    users_before = clean.scalar(select(User.id).where(User.email == "ada.casa@gmail.com"))
    assert users_before is None

    service = FreelancerService(clean)
    read, created = service.apply(
        FreelancerCreate(
            first_name="Ada",
            last_name="Lovelace",
            email="Ada.Casa@gmail.com",
            daily_rate="450",
            job_title="Backend developer",
            remote="remote",
        )  # type: ignore[arg-type]
    )
    assert created is False and read.id == ada
    assert clean.scalar(select(User.id).where(User.email == "ada.casa@gmail.com")) is None
    assert len(clean.scalars(select(Freelancer.id)).all()) == 1

    found = UserService(clean).find_person("ada.casa@gmail.com")
    assert found is not None and found.email == "ada@studio.it"


def test_an_extra_address_never_logs_in(clean: Session) -> None:  # noqa: F811  (fixture)
    ada = apply_freelancer(clean, "ada@studio.it")
    _override(clean, ada, _point("email", "ada.casa@gmail.com", "personal"))
    users = UserService(clean, SETTINGS)
    assert users.request_link("ada.casa@gmail.com") is None
    mail = users.request_link("ada@studio.it")
    assert mail is not None and mail.to == "ada@studio.it"


def test_a_lead_with_a_cards_extra_address_is_no_lead(clean: Session) -> None:  # noqa: F811  (fixture)
    from campaign_fixtures import lead

    ada = apply_freelancer(clean, "ada@studio.it")
    _override(clean, ada, _point("email", "ada.casa@gmail.com", "personal"))
    lead(clean, "ada.casa@gmail.com")
    lead(clean, "giulia@studio.it")
    _leads, total = FreelancerService(clean)._leads(50)
    assert [item.email for item in _leads] == ["giulia@studio.it"] and total == 1


def test_a_campaign_still_writes_to_the_login_address(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    card = person(clean, "nocv@studio.it", cv=False)
    _override(clean, card.id, _point("email", "nocv.casa@gmail.com", "personal"))
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    rows = clean.scalars(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == created.id)
    ).all()
    assert [r.email for r in rows] == ["nocv@studio.it"]


def test_a_company_contact_gets_a_labelled_phone_from_an_admin(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    company = CompanyService(clean)
    read, _ = company.request(
        CompanyCreate(
            company_name="Immobilsistem",
            contact_first_name="Marco",
            contact_last_name="Crippa",
            email="marco@immobilsistem.it",
            phone="+39 345 1234567",
            requested_role="CTO",
            project="Portale",
            period_from="2026-12-01",
            duration="3 mesi",
            daily_budget="500",
            remote="remote",
            headcount=1,
        )  # type: ignore[arg-type]
    )
    assert read.phone == "+39 345 1234567"
    assert [p.value for p in read.contact_points if p.kind == "phone"] == ["+39 345 1234567"]
    changed = company.override(
        read.id,
        CompanyOverride(
            contact_points=[
                ContactPointInput(
                    kind="phone",
                    value="+39 02 5550199",
                    label="work",
                    note="centralino, chiedere di Veronica",
                )
            ]
        ),
        an_admin(clean),
    )
    assert changed.phone == "+39 02 5550199"
    trail = clean.scalars(select(AdminAction).where(AdminAction.entity_id == read.id)).all()
    entry = next(a for a in trail if a.kind == "overridden")
    assert entry.payload["changed"] == ["contact_points"]
    assert entry.payload["before"]["contact_points"][0]["value"] == "+39 345 1234567"
    assert entry.payload["after"]["contact_points"][0]["note"] == "centralino, chiedere di Veronica"


def test_a_merge_carries_the_points_and_the_login_address_and_the_undo_puts_them_back(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    admin_id = an_admin(clean)
    ada = apply_freelancer(clean, "ada@studio.it")
    old = apply_freelancer(clean, "ada.vecchia@studio.it", first_name="Ada")
    _override(clean, ada, _point("phone", "+39 333 111 1111", "work"))
    _override(
        clean,
        old,
        _point("email", "ada.casa@gmail.com", "personal"),
        _point("phone", "+39 333 222 2222", "personal", note="solo WhatsApp"),
        _point("phone", "+39 333 111 1111", "other"),
    )

    done = PersonMergeService(clean).merge(ada, old, admin_id)

    survivor, absorbed = _user(clean, ada), _user(clean, old)
    clean.refresh(survivor)
    assert [(p.kind, p.value, p.is_primary) for p in survivor.contact_points] == [
        ("phone", "+39 333 111 1111", True),
        ("email", "ada.casa@gmail.com", False),
        ("phone", "+39 333 222 2222", False),
        ("email", "ada.vecchia@studio.it", False),
    ]
    assert survivor.contact_points[2].note == "solo WhatsApp"
    assert absorbed.contact_points == []
    # The merged-away address still answers for the survivor, and so do the points that came.
    users = UserService(clean)
    assert users.person_at("ada.vecchia@studio.it") == survivor
    assert users.person_at("ADA.casa@gmail.com") == survivor
    assert ContactPointService(clean).present(survivor)[0].value == "ada@studio.it"

    back = PersonMergeService(clean).undo(done.action_id, admin_id)

    assert back.left_behind == {}
    clean.expire_all()
    survivor, absorbed = _user(clean, ada), _user(clean, old)
    assert [(p.kind, p.value) for p in survivor.contact_points] == [("phone", "+39 333 111 1111")]
    assert [(p.kind, p.value, p.is_primary) for p in absorbed.contact_points] == [
        ("email", "ada.casa@gmail.com", False),
        ("phone", "+39 333 222 2222", True),
        ("phone", "+39 333 111 1111", False),
    ]


def test_an_override_cannot_clear_the_list_with_null_and_an_empty_list_clears_it(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    ada = apply_freelancer(clean, "ada@studio.it", phone="+39 333 1234567")
    service = ContactPointService(clean)
    user = _user(clean, ada)
    with pytest.raises(ValidationFailed, match="elenco vuoto"):
        service.write_override(user, None)
    cleared = _override(clean, ada)
    assert cleared.phone is None and [p.value for p in cleared.contact_points] == ["ada@studio.it"]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("email", "not-an-address"),
        ("phone", "12"),
        ("phone", "chiamami"),
        ("linkedin", "https://example.com/in/ada"),
    ],
)
def test_a_value_that_is_not_what_its_kind_says_is_refused(kind: str, value: str) -> None:
    with pytest.raises(ValueError):
        ContactPointInput(kind=kind, value=value, label="work")  # type: ignore[arg-type]


def test_a_note_is_bounded_and_a_linkedin_profile_is_stored_canonical() -> None:
    with pytest.raises(ValueError):
        ContactPointInput(kind="phone", value="+39 333 1234567", label="work", note="x" * 201)
    point = ContactPointInput(
        kind="linkedin", value="linkedin.com/in/Ada-Lovelace/?utm=1", label="other"
    )
    assert point.value == "https://www.linkedin.com/in/ada-lovelace"
