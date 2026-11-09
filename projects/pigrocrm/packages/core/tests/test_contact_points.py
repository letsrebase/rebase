"""Contact points (REB-939, REB-940): the rules of a list, how a person and a customer take
one, and the lookups that read it.

The lookups that live in other files, because their fixtures do (the Gmail link by a second
address in `test_gmail_links.py`, the leads door in `test_leads.py`, the payment reminder's
recipient in `test_payment_reminders_create.py`), are the same scenario in three places:
a person with two addresses, found by either and written to at the primary.
"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.schemas import MAX_CONTACT_POINTS, ContactPointIn
from pigrocrm.core.contact_points.service import phone_key, prepare
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail.roster import AddressRoster, EntityRef
from pigrocrm.core.organizations.schemas import (
    OrganizationCreate,
    OrganizationListQuery,
    OrganizationUpdate,
)
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PersonCreate, PersonListQuery, PersonUpdate
from pigrocrm.core.people.service import PersonService

ADMIN = Actor(id=None, type="system", role="admin")


def _point(kind: str, value: str, **fields: Any) -> ContactPointIn:
    return ContactPointIn.model_validate({"kind": kind, "value": value, **fields})


def _ada(session: Session) -> Person:
    """Ada, with two addresses (the second flagged primary) and a labelled phone."""
    read = PersonService(session).create(
        PersonCreate(
            first_name="Ada",
            last_name="Byron",
            contact_points=[
                _point("email", "ada@acme.it", label="work"),
                _point("email", "Ada.Byron@gmail.com", label="personal", is_primary=True),
                _point("phone", "+39 02 1234567", label="billing", note="centralino"),
            ],
        ),
        ADMIN,
    )
    person = session.get(Person, read.id)
    assert person is not None
    return person


# --- the rules of a list --------------------------------------------------------------


def test_the_first_point_of_a_kind_is_primary_when_none_is_flagged() -> None:
    prepared = prepare(
        "person",
        [_point("email", "a@x.it"), _point("email", "b@x.it"), _point("phone", "0212345")],
    )

    assert [(p.kind, p.is_primary) for p in prepared] == [
        ("email", True),
        ("email", False),
        ("phone", True),
    ]


def test_a_flagged_point_is_the_primary_of_its_kind() -> None:
    prepared = prepare(
        "person", [_point("email", "a@x.it"), _point("email", "b@x.it", is_primary=True)]
    )

    assert [p.is_primary for p in prepared] == [False, True]


def test_two_primaries_of_one_kind_are_refused() -> None:
    with pytest.raises(ValidationFailed) as exc:
        prepare(
            "person",
            [
                _point("email", "a@x.it", is_primary=True),
                _point("email", "b@x.it", is_primary=True),
            ],
        )
    assert exc.value.details["field"] == "contact_points[1]"


def test_a_primary_of_each_kind_is_allowed() -> None:
    prepared = prepare(
        "person",
        [_point("email", "a@x.it", is_primary=True), _point("phone", "0212345", is_primary=True)],
    )

    assert [p.is_primary for p in prepared] == [True, True]


@pytest.mark.parametrize(
    ("kind", "first", "second"),
    [
        ("email", "Ada@Acme.IT", " ada@acme.it "),
        ("phone", "+39 02 1234567", "+39-02-123 4567"),
        ("linkedin", "https://www.linkedin.com/in/Ada", "linkedin.com/in/ada/?utm_source=share"),
    ],
)
def test_the_same_value_twice_on_one_owner_is_refused(kind: str, first: str, second: str) -> None:
    """Compared the way a lookup compares: an address in lowercase, a number on its digits, a
    profile as the hub canonicalises it."""
    with pytest.raises(ValidationFailed) as exc:
        prepare("person", [_point(kind, first), _point(kind, second)])
    assert exc.value.details["field"] == "contact_points[1]"


def test_the_same_digits_with_and_without_the_plus_are_two_numbers() -> None:
    assert [p.normalized for p in prepare("person", [_point("phone", "+39021234567")])] == [
        "+39021234567"
    ]
    assert phone_key("0039 02 1234567") == "0039021234567"
    assert (
        len(prepare("person", [_point("phone", "+39021234567"), _point("phone", "39021234567")]))
        == 2
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("email", "non-una-email"),
        ("email", "  "),
        ("email", "<a@b.it>"),
        ("email", "Name<a@b.it>"),
        ("email", "Name <a@b.it>"),
        ("email", "a@b.it, c@d.it"),
        ("email", "a@b.it;c@d.it"),
        ("email", '"a"@b.it'),
        ("phone", "12345"),
        ("phone", "abc"),
        ("phone", "1234567 " + "x" * 40),
        ("linkedin", "https://example.com/in/ada"),
    ],
)
def test_a_value_that_is_not_what_its_kind_says_is_refused(kind: str, value: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        prepare("person", [_point(kind, value)])
    assert exc.value.details["field"] == "contact_points[0]"


@pytest.mark.parametrize(
    "value", ["+39 345 1234567 (ufficio)", "345-1234567 int. 2", "02 1234567", "123456"]
)
def test_a_phone_is_whatever_is_written_with_enough_digits(value: str) -> None:
    [stored] = prepare("person", [_point("phone", value)])

    assert stored.value == value
    assert stored.normalized == phone_key(value)


def test_a_bare_linkedin_handle_is_the_profile() -> None:
    [stored] = prepare("person", [_point("linkedin", "Ada-Lovelace")])

    assert stored.value == "https://www.linkedin.com/in/ada-lovelace"


def test_a_stored_value_is_accepted_as_it_is_where_the_rules_would_refuse_it() -> None:
    """What was stored before the rules, or by a looser version of them, saves again
    unchanged and beside something new; the same text as a new value is refused."""
    stored = [
        _row("email", "ada(at)acme", "ada(at)acme"),
        _row("phone", "int. 2", "2"),
        _row("linkedin", "Ada on LinkedIn", "ada on linkedin"),
    ]
    sent = [
        _point("email", "ada(at)acme"),
        _point("phone", "int. 2"),
        _point("linkedin", "Ada on LinkedIn"),
        _point("email", "new@acme.it"),
    ]

    prepared = prepare("person", sent, stored)

    assert [p.value for p in prepared] == [
        "ada(at)acme",
        "int. 2",
        "Ada on LinkedIn",
        "new@acme.it",
    ]
    with pytest.raises(ValidationFailed):
        prepare("person", sent[:3])
    # A different broken value is still refused: only what the owner already had passes.
    with pytest.raises(ValidationFailed):
        prepare("person", [_point("email", "ada(at)other")], stored)


def _row(kind: str, value: str, normalized: str) -> ContactPoint:
    return ContactPoint(
        kind=kind,
        value=value,
        normalized=normalized,
        label="work",
        is_primary=True,
        position=0,
    )


def test_a_linkedin_profile_is_stored_the_way_the_hub_stores_it() -> None:
    [stored] = prepare("person", [_point("linkedin", "it.linkedin.com/in/Ada-Byron/details/")])

    assert stored.value == "https://www.linkedin.com/in/ada-byron"
    assert stored.normalized == "https://www.linkedin.com/in/ada-byron"


def test_an_unknown_kind_or_label_is_a_schema_error() -> None:
    with pytest.raises(ValidationError):
        _point("fax", "0212345")
    with pytest.raises(ValidationError):
        _point("email", "a@x.it", label="boss")


def test_a_note_longer_than_the_column_is_a_schema_error() -> None:
    with pytest.raises(ValidationError):
        _point("email", "a@x.it", note="x" * 201)


def test_a_list_beyond_the_cap_is_refused() -> None:
    with pytest.raises(ValidationError):
        PersonCreate(
            first_name="Ada",
            contact_points=[_point("email", f"p{i}@x.it") for i in range(MAX_CONTACT_POINTS + 1)],
        )


# --- a person and a customer take the list --------------------------------------------


def test_the_derived_fields_are_the_primary_of_each_kind(db_session: Session) -> None:
    ada = _ada(db_session)

    read = PersonService(db_session).get(ada.id, ADMIN)

    assert read.email == "Ada.Byron@gmail.com"
    assert read.phone == "+39 02 1234567"
    assert read.linkedin is None
    assert [
        (p.kind, p.value, p.label, p.note, p.is_primary, p.position) for p in read.contact_points
    ] == [
        ("email", "ada@acme.it", "work", None, False, 0),
        ("email", "Ada.Byron@gmail.com", "personal", None, True, 1),
        ("phone", "+39 02 1234567", "billing", "centralino", True, 2),
    ]


def test_an_update_replaces_the_whole_list(db_session: Session) -> None:
    ada = _ada(db_session)
    service = PersonService(db_session)

    read = service.update(
        ada.id, PersonUpdate(contact_points=[_point("email", "new@acme.it")]), ADMIN
    )

    assert [p.value for p in read.contact_points] == ["new@acme.it"]
    assert (read.email, read.phone) == ("new@acme.it", None)


def test_an_empty_list_clears_and_an_omitted_one_leaves(db_session: Session) -> None:
    ada = _ada(db_session)
    service = PersonService(db_session)

    left = service.update(ada.id, PersonUpdate(role="Ingegnera"), ADMIN)
    cleared = service.update(ada.id, PersonUpdate(contact_points=[]), ADMIN)

    assert len(left.contact_points) == 3
    assert cleared.contact_points == [] and cleared.email is None


def test_sending_the_list_back_as_it_was_changes_nothing(db_session: Session) -> None:
    from pigrocrm.core.activities.service import ActivityService

    ada = _ada(db_session)
    service = PersonService(db_session)
    same = [
        _point("email", "ada@acme.it", label="work"),
        _point("email", "Ada.Byron@gmail.com", label="personal", is_primary=True),
        _point("phone", "+39 02 1234567", label="billing", note="centralino"),
    ]

    service.update(ada.id, PersonUpdate(contact_points=same, role="Ingegnera"), ADMIN)

    [update] = [
        e for e in ActivityService(db_session).timeline("person", ada.id) if e.kind == "updated"
    ]
    assert update.payload["changed"] == ["role"]


def test_a_refused_list_leaves_the_stored_one(db_session: Session) -> None:
    ada = _ada(db_session)
    service = PersonService(db_session)

    with pytest.raises(ValidationFailed):
        service.update(
            ada.id,
            PersonUpdate(contact_points=[_point("email", "x@x.it"), _point("email", "X@x.it")]),
            ADMIN,
        )

    assert len(service.get(ada.id, ADMIN).contact_points) == 3


def test_the_old_flat_fields_are_refused_on_write() -> None:
    with pytest.raises(ValidationError):
        PersonCreate.model_validate({"first_name": "Ada", "email": "ada@acme.it"})
    with pytest.raises(ValidationError):
        PersonUpdate.model_validate({"phone": "0212345"})
    with pytest.raises(ValidationError):
        OrganizationCreate.model_validate({"legal_name": "Acme", "email": "info@acme.it"})
    with pytest.raises(ValidationError):
        OrganizationUpdate.model_validate({"phone": "0212345"})


def test_a_customer_takes_a_list_like_a_person(db_session: Session) -> None:
    service = OrganizationService(db_session)

    read = service.create(
        OrganizationCreate(
            legal_name="Acme",
            certified_email="acme@pec.it",
            contact_points=[
                _point("phone", "+39 02 7654321", label="other", note="centralino"),
                _point("email", "info@acme.it", label="billing"),
            ],
        ),
        ADMIN,
    )

    assert (read.email, read.phone, read.certified_email) == (
        "info@acme.it",
        "+39 02 7654321",
        "acme@pec.it",
    )
    updated = service.update(read.id, OrganizationUpdate(contact_points=[]), ADMIN)
    assert (updated.email, updated.phone, updated.certified_email) == (None, None, "acme@pec.it")


def test_the_database_refuses_what_the_service_would(db_session: Session) -> None:
    """The rules are the database's too: a writer that skips the service meets the indexes and
    the CHECK."""
    ada = _ada(db_session)

    def point(**fields: Any) -> ContactPoint:
        return ContactPoint(
            **{
                "person_id": ada.id,
                "kind": "email",
                "value": "x@x.it",
                "normalized": "x@x.it",
                "label": "work",
                "is_primary": False,
                "position": 9,
                **fields,
            }
        )

    for broken in (
        point(normalized="ada@acme.it"),
        point(normalized="y@x.it", is_primary=True),
        point(person_id=None),
        point(customer_id=uuid4()),
        point(label="boss"),
    ):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(broken)
            db_session.flush()
        db_session.expire_all()


# --- the lookups ---------------------------------------------------------------------


def test_people_are_found_by_any_of_their_points(db_session: Session) -> None:
    _ada(db_session)
    PersonService(db_session).create(PersonCreate(first_name="Luigi", last_name="Verdi"), ADMIN)
    service = PersonService(db_session)

    def found(term: str) -> list[str]:
        return [p.first_name for p in service.list(PersonListQuery(search=term), ADMIN).items]

    assert found("ada@acme.it") == ["Ada"]  # the non-primary address
    assert found("byron@GMAIL") == ["Ada"]  # the primary one, in another case
    assert found("0212") == ["Ada"]  # a phone, on its digits
    assert found("+39 02-1234 567") == ["Ada"]  # typed with other separators
    assert found("nessuno@x.it") == []


def test_a_search_with_digits_in_a_word_is_not_a_phone_search(db_session: Session) -> None:
    _ada(db_session)

    page = PersonService(db_session).list(PersonListQuery(search="acme1234"), ADMIN)

    assert page.items == []


def test_organizations_are_found_by_any_of_their_points(db_session: Session) -> None:
    service = OrganizationService(db_session)
    service.create(
        OrganizationCreate(
            legal_name="Acme",
            contact_points=[_point("email", "info@acme.it"), _point("phone", "+39 02 7654321")],
        ),
        ADMIN,
    )
    service.create(OrganizationCreate(legal_name="Beta"), ADMIN)

    def found(term: str) -> list[str]:
        return [c.legal_name for c in service.list(OrganizationListQuery(search=term), ADMIN).items]

    assert found("info@acme") == ["Acme"]
    assert found("02 76543") == ["Acme"]


def test_the_roster_knows_every_address_of_a_person_and_resolves_by_any(
    db_session: Session,
) -> None:
    ada = _ada(db_session)
    roster = AddressRoster(db_session)

    assert roster.known_addresses() == ("ada.byron@gmail.com", "ada@acme.it")
    assert roster.known_among(["ADA@acme.it", "nobody@x.it"]) == {"ada@acme.it"}
    assert roster.resolve_many(["ada@acme.it"])["ada@acme.it"] == (EntityRef("person", ada.id),)
    assert roster.resolve_many(["Ada.Byron@Gmail.com"])["ada.byron@gmail.com"] == (
        EntityRef("person", ada.id),
    )


def test_a_domain_is_taken_by_any_address_of_a_organizations_person(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="Acme"), ADMIN)
    PersonService(db_session).create(
        PersonCreate(
            first_name="Ada",
            customer_id=customer.id,
            contact_points=[_point("email", "ada@gmail.com"), _point("email", "ada@acme.it")],
        ),
        ADMIN,
    )

    assert AddressRoster(db_session).domains_taken(["acme.it", "other.it"]) == {"acme.it"}


def test_deleting_the_owner_row_takes_its_points(db_session: Session) -> None:
    ada = _ada(db_session)
    db_session.commit()

    db_session.execute(text("DELETE FROM people WHERE id = :id"), {"id": ada.id})

    assert (
        db_session.scalars(select(ContactPoint).where(ContactPoint.person_id == ada.id)).all() == []
    )
