from typing import Any
from uuid import uuid4

import pytest
from fakes.contacts import points_in
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contact_points.schemas import ContactPointIn
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import (
    LAST_NAME_MAX_LENGTH,
    NAME_MAX_LENGTH,
    ROLE_MAX_LENGTH,
    PersonCreate,
    PersonListQuery,
    PersonUpdate,
)
from pigrocrm.core.people.service import PersonService

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaborator")
READONLY = Actor(id=None, type="user", role="readonly")


def test_a_person_can_exist_without_a_customer(db_session: Session) -> None:
    """Forcing the association produces phantom organizations called 'Freelance vari'."""
    person = PersonService(db_session).create(PersonCreate(first_name="Mario"), ADMIN)
    assert person.customer_id is None


def test_a_person_can_be_attached_to_a_customer(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    person = PersonService(db_session).create(
        PersonCreate(first_name="Mario", last_name="Rossi", customer_id=customer.id), ADMIN
    )
    assert person.customer_id == customer.id


def test_attaching_to_a_missing_customer_is_rejected(db_session: Session) -> None:
    with pytest.raises(NotFound) as exc:
        PersonService(db_session).create(
            PersonCreate(first_name="Mario", customer_id=uuid4()), ADMIN
        )
    assert exc.value.details["entity"] == "organization"


def test_reattaching_to_a_missing_customer_is_rejected(db_session: Session) -> None:
    """The brief's own test only exercises this rule at create time. `update()` runs
    the identical `_check_customer` whenever `customer_id` is among the supplied
    changes, so re-pointing an existing person at a customer that does not resolve
    to a live row must be rejected exactly the same way."""
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)

    with pytest.raises(NotFound) as exc:
        service.update(person.id, PersonUpdate(customer_id=uuid4()), ADMIN)
    assert exc.value.details["entity"] == "organization"


def test_invalid_email_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as exc:
        PersonService(db_session).create(
            PersonCreate(first_name="Mario", contact_points=points_in(email="non-una-email")), ADMIN
        )
    assert exc.value.details["field"] == "contact_points[0]"


def test_an_email_with_something_after_a_newline_is_rejected(db_session: Session) -> None:
    r"""`^[^@\s]+@[^@\s]+\.[^@\s]+$` checked with `.match()` accepts a trailing "\n": `$`
    matches just before a final newline, so a value could pass the check and reach `flush()`
    as a raw, session-poisoning `DataError` once long enough to exceed the column. The
    service strips the value first and checks it with `.fullmatch()`: a newline inside it is
    refused, and the trailing one of a pasted address is simply trimmed."""
    service = PersonService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        service.create(
            PersonCreate(first_name="Mario", contact_points=points_in(email="a@b.it\nBcc: x@y.it")),
            ADMIN,
        )
    assert exc.value.details["field"] == "contact_points[0]"
    person = service.create(
        PersonCreate(first_name="Mario", contact_points=points_in(email="a@b.it\n")), ADMIN
    )
    assert person.email == "a@b.it"


def test_an_email_is_stored_as_typed_and_compared_in_lowercase(db_session: Session) -> None:
    person = PersonService(db_session).create(
        PersonCreate(first_name="Mario", contact_points=points_in(email="  Mario@ACME.IT ")), ADMIN
    )
    assert person.email == "Mario@ACME.IT"
    row = db_session.get(Person, person.id)
    assert row is not None
    assert [point.normalized for point in row.contact_points] == ["mario@acme.it"]


def test_a_blank_contact_point_is_refused_not_stored_as_an_empty_string(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as exc:
        PersonService(db_session).create(
            PersonCreate(
                first_name="Mario", contact_points=[ContactPointIn(kind="email", value="  ")]
            ),
            ADMIN,
        )
    assert exc.value.details["field"] == "contact_points[0]"


@pytest.mark.parametrize(
    "field,limit",
    [
        ("first_name", NAME_MAX_LENGTH),
        ("last_name", LAST_NAME_MAX_LENGTH),
        ("role", ROLE_MAX_LENGTH),
    ],
)
def test_string_fields_are_bounded_to_their_column_width_on_create(field: str, limit: int) -> None:
    """Mirrors `OrganizationCreate`'s own `*_MAX_LENGTH` bounds (organizations/schemas.py):
    without a matching Pydantic bound, an over-length value sails past validation,
    reaches `flush()`, and comes back as a raw `sqlalchemy.exc.DataError`
    (`StringDataRightTruncation`) -- not a subclass of `IntegrityError`, so nothing
    in this codebase catches it, and it poisons the session."""
    kwargs: dict[str, Any] = {"first_name": "Mario"}
    kwargs[field] = "x" * (limit + 1)
    with pytest.raises(ValidationError):
        PersonCreate(**kwargs)


@pytest.mark.parametrize(
    "field,limit",
    [
        ("first_name", NAME_MAX_LENGTH),
        ("last_name", LAST_NAME_MAX_LENGTH),
        ("role", ROLE_MAX_LENGTH),
    ],
)
def test_string_fields_are_bounded_to_their_column_width_on_update(field: str, limit: int) -> None:
    with pytest.raises(ValidationError):
        PersonUpdate(**{field: "x" * (limit + 1)})


def test_custom_fields_are_validated(db_session: Session) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="person", key="seniority", label="Seniority", field_type="text"
        ),
        ADMIN,
    )
    person = PersonService(db_session).create(
        PersonCreate(first_name="Mario", custom_fields={"seniority": "Senior"}), ADMIN
    )
    assert person.custom_fields == {"seniority": "Senior"}

    with pytest.raises(ValidationFailed):
        PersonService(db_session).create(
            PersonCreate(first_name="Luigi", custom_fields={"inventato": "x"}), ADMIN
        )


def test_archived_custom_field_value_survives_unrelated_updates_and_can_still_be_cleared(
    db_session: Session,
) -> None:
    """Task 7's contract for archiving a field definition is 'hide it, keep the data
    readable'. Validating the *union* of a row's stored custom_fields and the
    caller's incoming values against only the active definitions breaks that
    contract: an archived key still sitting in `custom_fields` would make every
    future update -- even one that never mentions that key -- fail with "campo non
    definito". `update()` must validate only the keys the caller actually supplies,
    never the union with what is already stored. Mirrors
    `test_archived_custom_field_value_survives_unrelated_updates_and_can_still_be_cleared`
    in test_organizations.py exactly."""
    fields = FieldDefinitionService(db_session)
    industry = fields.create(
        FieldDefinitionCreate(
            entity_type="person", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="person", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    person = service.create(
        PersonCreate(first_name="Mario", custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(industry.id, ADMIN)

    updated = service.update(person.id, PersonUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}

    cleared = service.update(person.id, PersonUpdate(custom_fields={"settore": None}), ADMIN)
    assert cleared.custom_fields == {"priorita": "alta"}

    fields.create(
        FieldDefinitionCreate(
            entity_type="person",
            key="stato_persona",
            label="Stato",
            field_type="select",
            options=["active", "sospeso"],
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed):
        service.update(person.id, PersonUpdate(custom_fields={"stato_persona": "closed"}), ADMIN)


def test_required_active_custom_field_set_to_none_is_rejected_like_empty_string(
    db_session: Session,
) -> None:
    """`None` and `""` are two spellings of the same intent -- "this field has no
    value" -- and must be rejected identically on a currently active, required
    field. A version that only sent `None` straight to removal, bypassing any
    required check, would silently strip a required value with no error."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="person",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    person = service.create(
        PersonCreate(first_name="Mario", custom_fields={"settore": "IT"}), ADMIN
    )

    with pytest.raises(ValidationFailed) as via_none:
        service.update(person.id, PersonUpdate(custom_fields={"settore": None}), ADMIN)
    assert via_none.value.details["field"] == "settore"
    assert via_none.value.details["reason"] == "campo obbligatorio"

    with pytest.raises(ValidationFailed) as via_empty:
        service.update(person.id, PersonUpdate(custom_fields={"settore": ""}), ADMIN)
    assert via_empty.value.details["reason"] == via_none.value.details["reason"]


def test_non_required_active_custom_field_set_to_none_is_removed(db_session: Session) -> None:
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="person", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    person = service.create(
        PersonCreate(first_name="Mario", custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(person.id, PersonUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_archived_custom_field_set_to_none_is_removed_even_if_it_was_required(
    db_session: Session,
) -> None:
    """Requiredness only ever blocks removal for a *currently active* definition:
    clearing an archived field's stored value must stay possible even if the
    definition was required back when it was active."""
    fields = FieldDefinitionService(db_session)
    industry = fields.create(
        FieldDefinitionCreate(
            entity_type="person",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    person = service.create(
        PersonCreate(first_name="Mario", custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(industry.id, ADMIN)

    updated = service.update(person.id, PersonUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_required_active_custom_field_not_mentioned_in_a_partial_update_is_unaffected(
    db_session: Session,
) -> None:
    """A required, active field the caller never mentions in this update is neither
    an omission (create-time semantics) nor a removal (a `None` was never sent) --
    it must be left exactly as stored, with no error."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="person",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="person", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    person = service.create(
        PersonCreate(first_name="Mario", custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(person.id, PersonUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}


def test_create_records_a_timeline_entry(db_session: Session) -> None:
    from pigrocrm.core.activities.service import ActivityService

    person = PersonService(db_session).create(PersonCreate(first_name="Mario"), ADMIN)
    assert [e.kind for e in ActivityService(db_session).timeline("person", person.id)] == [
        "created"
    ]


def test_readonly_cannot_create(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        PersonService(db_session).create(PersonCreate(first_name="Mario"), READONLY)


def test_readonly_cannot_write_but_can_read(db_session: Session) -> None:
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)

    assert service.get(person.id, READONLY).id == person.id
    with pytest.raises(PermissionDenied):
        service.create(PersonCreate(first_name="Luigi"), READONLY)


def test_collaborator_can_create(db_session: Session) -> None:
    assert PersonService(db_session).create(PersonCreate(first_name="Mario"), COLLAB)


def test_update_can_detach_a_person_from_a_customer(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)

    detached = service.update(person.id, PersonUpdate(customer_id=None, detach=True), ADMIN)
    assert detached.customer_id is None


def test_detach_wins_over_a_valid_customer_id_supplied_in_the_same_update(
    db_session: Session,
) -> None:
    """`detach=True` is an explicit, self-contained intention: an incidental
    `customer_id` sent in the same payload (a client re-submitting stale form
    state alongside an "unlink" action is not exotic) must not be silently
    honoured instead, nor interact with the detach at all -- the person ends up
    detached regardless of what `customer_id` said."""
    original = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    other = OrganizationService(db_session).create(OrganizationCreate(legal_name="Beta"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=original.id), ADMIN)

    detached = service.update(person.id, PersonUpdate(customer_id=other.id, detach=True), ADMIN)
    assert detached.customer_id is None


def test_detach_succeeds_even_with_a_nonexistent_customer_id_in_the_same_update(
    db_session: Session,
) -> None:
    """The detach's own success must not depend on `customer_id` being valid, or
    even resolvable at all: `_check_customer` must never run when `detach=True`,
    since the caller's intent to unlink does not depend on that field."""
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)

    detached = service.update(person.id, PersonUpdate(customer_id=uuid4(), detach=True), ADMIN)
    assert detached.customer_id is None


def test_detach_on_an_already_detached_person_records_an_honest_empty_changed_list(
    db_session: Session,
) -> None:
    """`detach=True` on a person with no customer to begin with must not force a
    spurious `customer_id=None` assignment into the timeline's "changed" list --
    that would be an audit entry claiming a change that never happened, the same
    class of defect `restore()`'s own `was_deleted` guard exists to prevent."""
    from pigrocrm.core.activities.service import ActivityService

    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)

    detached = service.update(person.id, PersonUpdate(detach=True), ADMIN)
    assert detached.customer_id is None

    updates = [
        e for e in ActivityService(db_session).timeline("person", person.id) if e.kind == "updated"
    ]
    assert updates[0].payload["changed"] == []


def test_soft_delete_then_restore(db_session: Session) -> None:
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)
    service.soft_delete(person.id, ADMIN)

    with pytest.raises(NotFound):
        service.get(person.id, ADMIN)
    assert service.restore(person.id, ADMIN).first_name == "Mario"


def test_restore_on_a_person_that_was_never_deleted_does_not_log_a_restored_entry(
    db_session: Session,
) -> None:
    """Mirrors the identical guard on `OrganizationService.restore` (organizations/
    service.py): unconditionally logging "restored" -- even for a person who was
    never soft-deleted -- would write a timeline entry claiming a recovery that
    never happened."""
    from pigrocrm.core.activities.service import ActivityService

    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)

    service.restore(person.id, ADMIN)

    kinds = [e.kind for e in ActivityService(db_session).timeline("person", person.id)]
    assert "restored" not in kinds


def test_get_missing_person_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        PersonService(db_session).get(uuid4(), ADMIN)


def test_list_can_be_filtered_by_customer(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="Dentro", customer_id=customer.id), ADMIN)
    service.create(PersonCreate(first_name="Fuori"), ADMIN)

    page = service.list(PersonListQuery(customer_id=customer.id), ADMIN)
    assert [p.first_name for p in page.items] == ["Dentro"]


def test_search_matches_name_surname_and_email(db_session: Session) -> None:
    service = PersonService(db_session)
    service.create(
        PersonCreate(
            first_name="Mario", last_name="Rossi", contact_points=points_in(email="mr@acme.it")
        ),
        ADMIN,
    )
    service.create(PersonCreate(first_name="Luigi", last_name="Verdi"), ADMIN)

    assert len(service.list(PersonListQuery(search="rossi"), ADMIN).items) == 1
    assert len(service.list(PersonListQuery(search="mr@acme.it"), ADMIN).items) == 1
    assert len(service.list(PersonListQuery(search="mario"), ADMIN).items) == 1


def test_search_treats_underscore_as_a_literal_character_not_a_wildcard(
    db_session: Session,
) -> None:
    """In LIKE/ILIKE, "_" means "any one character". An unescaped search term makes
    a literal underscore in the query match every row with any character in that
    position -- here, searching "a_b" would also match "axb"."""
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="A_B"), ADMIN)
    service.create(PersonCreate(first_name="AXB"), ADMIN)

    result = service.list(PersonListQuery(search="a_b"), ADMIN)
    assert [p.first_name for p in result.items] == ["A_B"]


def test_search_treats_percent_as_a_literal_character_not_a_wildcard(db_session: Session) -> None:
    """Same bug, "%" instead of "_": unescaped, it means "any run of characters", so
    searching "50%off" would also match "50XXXoff"."""
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="50%off"), ADMIN)
    service.create(PersonCreate(first_name="50XXXoff"), ADMIN)

    result = service.list(PersonListQuery(search="50%off"), ADMIN)
    assert [p.first_name for p in result.items] == ["50%off"]


def test_search_term_with_a_trailing_backslash_still_matches(db_session: Session) -> None:
    """Before escaping, a trailing backslash in the search term combines with the
    "%" this method appends to build the pattern, forming an accidental escape
    sequence that swallows the trailing wildcard -- the match disappears entirely,
    even though the target genuinely contains that backslash."""
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="ACME\\"), ADMIN)

    result = service.list(PersonListQuery(search="acme\\"), ADMIN)
    assert [p.first_name for p in result.items] == ["ACME\\"]


def test_list_query_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        PersonListQuery(limit=0)
    with pytest.raises(ValidationError):
        PersonListQuery(limit=201)


def test_filter_by_custom_field_uses_jsonb_containment(db_session: Session) -> None:
    """Mirrors `test_filter_by_custom_field_uses_jsonb_containment` in
    test_organizations.py -- coverage Deals will also need once it copies this shape,
    even though nothing here is currently broken."""
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="person", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="A", custom_fields={"settore": "IT"}), ADMIN)
    service.create(PersonCreate(first_name="B", custom_fields={"settore": "Retail"}), ADMIN)

    page = service.list(PersonListQuery(custom={"settore": "IT"}), ADMIN)
    assert [p.first_name for p in page.items] == ["A"]


def test_pagination_returns_a_cursor_and_does_not_repeat_rows(db_session: Session) -> None:
    """Mirrors `test_pagination_returns_a_cursor_and_does_not_repeat_rows` in
    test_organizations.py."""
    service = PersonService(db_session)
    for index in range(5):
        service.create(PersonCreate(first_name=f"Persona {index:02d}"), ADMIN)

    first = service.list(PersonListQuery(limit=2), ADMIN)
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = service.list(PersonListQuery(limit=2, cursor=first.next_cursor), ADMIN)
    assert {p.id for p in first.items}.isdisjoint({p.id for p in second.items})


def test_last_page_has_no_cursor(db_session: Session) -> None:
    """Mirrors `test_last_page_has_no_cursor` in test_organizations.py."""
    service = PersonService(db_session)
    service.create(PersonCreate(first_name="Solo"), ADMIN)
    assert service.list(PersonListQuery(limit=10), ADMIN).next_cursor is None


# --- Final review item 1 (CRITICAL): a NUL byte in a native column ---------------
#
# `apps/api/tests/test_input_bounds_sweep.py` sweeps every string field on this
# schema over real HTTP; this exercises the same gap directly at the schema layer.


def test_a_nul_byte_in_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PersonCreate(first_name="Mario\x00Rossi")


def test_a_nul_byte_in_a_contact_point_is_rejected() -> None:
    """The value reaches `flush()` as a query parameter, and a NUL byte there is a raw
    `ValueError` out of the driver: `SafeStr` stops it at the schema."""
    with pytest.raises(ValidationError):
        PersonCreate(
            first_name="Mario", contact_points=points_in(email="  Mario\x00@Example.COM  ")
        )


# --- Final review item 8 (IMPORTANT): restore() must not undo the invariant -----
# --- soft_delete() protects -- mirrors the identical fix on DealService.       --


def test_restoring_a_person_whose_customer_is_now_archived_is_refused(
    db_session: Session,
) -> None:
    """Unlike deals, a customer can already be archived while an active person
    still points at it (soft_delete on a customer only ever counted active deals,
    never people) -- but restoring a person onto an archived customer is still the
    operation this fix closes, mirroring DealService.restore exactly."""
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)
    service.soft_delete(person.id, ADMIN)
    OrganizationService(db_session).soft_delete(customer.id, ADMIN)

    with pytest.raises(Conflict) as exc:
        service.restore(person.id, ADMIN)
    assert exc.value.details["entity"] == "person"
    assert "cliente" in exc.value.message


def test_restoring_the_customer_first_then_the_person_works(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(OrganizationCreate(legal_name="ACME"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)
    service.soft_delete(person.id, ADMIN)
    OrganizationService(db_session).soft_delete(customer.id, ADMIN)

    OrganizationService(db_session).restore(customer.id, ADMIN)
    service.restore(person.id, ADMIN)

    assert service.get(person.id, ADMIN).id == person.id


def test_restoring_a_person_with_no_customer_at_all_is_unaffected(db_session: Session) -> None:
    """The common case -- most people have no customer_id -- must not be blocked
    by a check that only ever applies when one is actually set."""
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)
    service.soft_delete(person.id, ADMIN)

    service.restore(person.id, ADMIN)
    assert service.get(person.id, ADMIN).id == person.id


def test_every_read_path_names_the_person_s_company(db_session: Session) -> None:
    """L'"azienda di riferimento" is the linked customer, and the list is the screen the
    owner actually reads: `customer_id` alone forces every caller to look the name up
    again, one request per row. Asserted on all four read paths at once because they
    must agree -- a person's company cannot depend on which endpoint asked."""
    customer = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl"), ADMIN
    )
    service = PersonService(db_session)

    created = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)
    assert created.customer_legal_name == "ACME Srl"
    assert service.get(created.id, ADMIN).customer_legal_name == "ACME Srl"
    updated = service.update(created.id, PersonUpdate(role="CTO"), ADMIN)
    assert updated.customer_legal_name == "ACME Srl"

    page = service.list(PersonListQuery(customer_id=customer.id), ADMIN)
    assert [p.customer_legal_name for p in page.items] == ["ACME Srl"]


def test_a_person_with_no_customer_has_no_company_name(db_session: Session) -> None:
    """The common case (see `Person`'s own docstring): `None`, not "" and not a dash --
    how an absent company is *rendered* is the reading surface's decision, not this
    schema's."""
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario"), ADMIN)
    assert person.customer_legal_name is None
    assert service.get(person.id, ADMIN).customer_legal_name is None
    assert service.list(PersonListQuery(), ADMIN).items[0].customer_legal_name is None


def test_detaching_a_person_clears_the_company_name_too(db_session: Session) -> None:
    customer = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl"), ADMIN
    )
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)

    detached = service.update(person.id, PersonUpdate(detach=True), ADMIN)
    assert detached.customer_id is None
    assert detached.customer_legal_name is None


def test_an_archived_customer_still_names_the_company(db_session: Session) -> None:
    """A soft-deleted customer is still the company this person belongs to: the
    association is not what was archived. Blanking the name instead would make an
    archived customer read as "no company at all" -- the same claim a person who never
    had one makes -- and it is `PersonService.restore`'s conflict ("il cliente e'
    archiviato") that tells the owner the customer is gone, not an empty cell in the
    Persone list. Mirrors `SearchRepository._with_customer_names`, whose own join
    carries no `deleted_at` filter for the identical reason."""
    organizations = OrganizationService(db_session)
    customer = organizations.create(OrganizationCreate(legal_name="ACME Srl"), ADMIN)
    service = PersonService(db_session)
    person = service.create(PersonCreate(first_name="Mario", customer_id=customer.id), ADMIN)

    organizations.soft_delete(customer.id, ADMIN)

    assert service.get(person.id, ADMIN).customer_legal_name == "ACME Srl"
    page = service.list(PersonListQuery(customer_id=customer.id), ADMIN)
    assert [p.customer_legal_name for p in page.items] == ["ACME Srl"]


def test_the_company_name_costs_one_query_for_the_whole_page(db_session: Session) -> None:
    """The reason the name is resolved in one batched lookup and not per row: a page of
    50 people from 50 different companies must not become 51 queries. Counted, not
    reasoned about -- an N+1 reintroduced by a later refactor is invisible to every
    other assertion in this file."""
    organizations = OrganizationService(db_session)
    service = PersonService(db_session)
    for index in range(3):
        customer = organizations.create(OrganizationCreate(legal_name=f"ACME {index}"), ADMIN)
        service.create(PersonCreate(first_name=f"Mario {index}", customer_id=customer.id), ADMIN)

    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "after_cursor_execute", record)
    try:
        page = service.list(PersonListQuery(), ADMIN)
    finally:
        event.remove(connection, "after_cursor_execute", record)

    assert len(page.items) == 3
    assert sorted(p.customer_legal_name or "" for p in page.items) == [
        "ACME 0",
        "ACME 1",
        "ACME 2",
    ]
    assert sum(1 for statement in statements if "FROM organizations" in statement) == 1
