"""REB-970 (design 2026-11-09 suppliers § 1): the roles of an organization, the one-row-per-VAT
rule, and the reads that stay blind to everything but customers."""

from datetime import UTC, datetime

import pytest
from fakes.contacts import points, points_in
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.gmail.roster import AddressRoster, address_key
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.duplicates import duplicate_vat_groups, ensure_vat_index
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.organizations.roles import VAT_UNIQUE_INDEX
from pigrocrm.core.organizations.schemas import (
    OrganizationCreate,
    OrganizationListQuery,
    OrganizationUpdate,
)
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.models import Person

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaborator")


def _create(session: Session, name: str, **fields: object) -> Organization:
    created = OrganizationService(session).create(
        OrganizationCreate(legal_name=name, **fields),  # type: ignore[arg-type]
        ADMIN,
    )
    return session.get_one(Organization, created.id)


def _names(session: Session, **query: object) -> set[str]:
    page = OrganizationService(session).list(OrganizationListQuery(**query), ADMIN)  # type: ignore[arg-type]
    return {item.legal_name for item in page.items}


# -- roles ---------------------------------------------------------------------------------


def test_an_organization_is_a_customer_unless_it_says_otherwise(db_session: Session) -> None:
    assert _create(db_session, "Acme").roles == ["customer"]
    both = _create(db_session, "Both", roles=["supplier", "customer", "supplier"])
    assert both.roles == ["supplier", "customer"], "no repeats, the order given"


@pytest.mark.parametrize("roles", [[], ["collaborator"], ["customer", "vendor"]])
def test_roles_are_one_or_more_of_the_three_names(db_session: Session, roles: list[str]) -> None:
    with pytest.raises(ValidationFailed) as exc:
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="X", roles=roles), ADMIN
        )
    assert exc.value.details["field"] == "roles"


def test_roles_can_be_changed_but_not_emptied(db_session: Session) -> None:
    acme = _create(db_session, "Acme")
    service = OrganizationService(db_session)
    updated = service.update(acme.id, OrganizationUpdate(roles=["customer", "supplier"]), ADMIN)
    assert updated.roles == ["customer", "supplier"]
    with pytest.raises(ValidationFailed):
        service.update(acme.id, OrganizationUpdate(roles=[]), ADMIN)
    with pytest.raises(ValidationFailed):
        service.update(acme.id, OrganizationUpdate(roles=None), ADMIN)


def test_a_list_shows_customers_unless_it_asks_for_a_role_or_for_all(db_session: Session) -> None:
    _create(db_session, "Only customer")
    _create(db_session, "Only supplier", roles=["supplier"])
    _create(db_session, "Both", roles=["customer", "supplier"])
    _create(db_session, "Freelancer", roles=["freelancer"])

    assert _names(db_session) == {"Only customer", "Both"}
    assert _names(db_session, role="supplier") == {"Only supplier", "Both", "Freelancer"}
    assert _names(db_session, role="freelancer") == {"Freelancer"}
    assert _names(db_session, role="any") == {
        "Only customer",
        "Only supplier",
        "Both",
        "Freelancer",
    }


def test_every_organization_is_on_the_customers_list_or_the_suppliers_list(
    db_session: Session,
) -> None:
    """«Clienti» asks `customer` and «Fornitori» `supplier` (REB-1061): a freelancer is always
    a supplier, so no organization is on neither."""
    _create(db_session, "Only customer")
    _create(db_session, "Only supplier", roles=["supplier"])
    _create(db_session, "Both", roles=["customer", "supplier"])
    _create(db_session, "Freelancer", roles=["freelancer"])
    _create(db_session, "Freelancer supplier", roles=["freelancer", "supplier"])
    _create(db_session, "Freelancer customer", roles=["freelancer", "customer"])

    assert _names(db_session, role="customer") == {"Only customer", "Both", "Freelancer customer"}
    assert _names(db_session, role="supplier") == {
        "Only supplier",
        "Both",
        "Freelancer",
        "Freelancer supplier",
        "Freelancer customer",
    }
    assert _names(db_session, role="any") == (
        _names(db_session, role="customer") | _names(db_session, role="supplier")
    )


def test_the_list_query_refuses_the_retired_customer_or_neither_role() -> None:
    with pytest.raises(PydanticValidationError):
        OrganizationListQuery(role="customer_or_neither")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        (["freelancer"], ["freelancer", "supplier"]),
        (["freelancer", "customer"], ["freelancer", "customer", "supplier"]),
        (["freelancer", "supplier"], ["freelancer", "supplier"]),
        (["supplier", "freelancer"], ["supplier", "freelancer"]),
        (["customer"], ["customer"]),
    ],
)
def test_a_freelancer_is_always_a_supplier_on_create(
    db_session: Session, given: list[str], stored: list[str]
) -> None:
    assert _create(db_session, "Ada", roles=given).roles == stored


def test_a_freelancer_gets_the_supplier_role_when_an_update_gives_it_the_freelancer_one(
    db_session: Session,
) -> None:
    service = OrganizationService(db_session)
    acme = _create(db_session, "Acme")
    updated = service.update(acme.id, OrganizationUpdate(roles=["customer", "freelancer"]), ADMIN)
    assert updated.roles == ["customer", "freelancer", "supplier"]
    # A legacy freelancer-only row (stored before the rule) gets it on its next roles write.
    legacy = _create(db_session, "Legacy")
    db_session.execute(
        text("UPDATE organizations SET roles = ARRAY['freelancer']::varchar[] WHERE id = :id"),
        {"id": legacy.id},
    )
    db_session.commit()
    db_session.expire_all()
    updated = service.update(legacy.id, OrganizationUpdate(roles=["freelancer"]), ADMIN)
    assert updated.roles == ["freelancer", "supplier"]


@pytest.mark.parametrize("roles", [["freelancer"], ["freelancer", "customer"]])
def test_the_supplier_role_of_a_freelancer_cannot_be_taken_away(
    db_session: Session, roles: list[str]
) -> None:
    service = OrganizationService(db_session)
    ada = _create(db_session, "Ada", roles=["freelancer"])
    with pytest.raises(ValidationFailed) as exc:
        service.update(ada.id, OrganizationUpdate(roles=roles), ADMIN)
    assert exc.value.details["field"] == "roles"
    assert "fornitore" in exc.value.message
    assert db_session.get_one(Organization, ada.id).roles == ["freelancer", "supplier"]


def test_the_freelancer_role_can_go_while_the_supplier_one_stays(db_session: Session) -> None:
    service = OrganizationService(db_session)
    ada = _create(db_session, "Ada", roles=["freelancer", "customer"])
    updated = service.update(ada.id, OrganizationUpdate(roles=["customer", "supplier"]), ADMIN)
    assert updated.roles == ["customer", "supplier"]


def test_a_freelancer_still_holds_only_a_freelancer_fiscal_kind(db_session: Session) -> None:
    ok = _create(db_session, "Ada", roles=["freelancer"], supplier_kind="professional")
    assert ok.roles == ["freelancer", "supplier"]
    assert ok.supplier_kind == "professional"
    for kind in ("company", "foreign"):
        with pytest.raises(ValidationFailed):
            OrganizationService(db_session).create(
                OrganizationCreate(
                    legal_name=f"F {kind}", roles=["freelancer"], supplier_kind=kind
                ),
                ADMIN,
            )


def test_fiscal_matching_and_name_lookup_see_customers_unless_told_otherwise(
    db_session: Session,
) -> None:
    _create(db_session, "Vendor Srl", vat_number="01234567897", roles=["supplier"])
    repo = OrganizationRepository(db_session)

    assert repo.match_by_fiscal_id(vat_number="01234567897", tax_code=None) is None
    assert repo.match_by_fiscal_id(vat_number="01234567897", tax_code=None, role="any") is not None
    assert repo.match_by_fiscal_id(vat_number="01234567897", tax_code=None, role="supplier")
    assert repo.find_by_name("Vendor Srl") is None
    assert repo.find_by_name("Vendor Srl", role="supplier") is not None


def test_the_role_filter_is_served_by_an_index(db_session: Session) -> None:
    indexes = db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_organizations_roles'")
    ).scalar_one()
    assert "USING gin (roles)" in indexes


# -- one row per VAT number ------------------------------------------------------------------


def test_a_second_organization_with_the_same_italian_vat_number_is_refused(
    db_session: Session,
) -> None:
    first = _create(db_session, "Acme", vat_number="12345678901")
    with pytest.raises(Conflict) as exc:
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="Acme again", vat_number="12345678901"), ADMIN
        )
    assert "esiste gia'" in exc.value.message
    assert exc.value.details["existing_id"] == str(first.id)
    # The refusal leaves the session usable and nothing behind.
    assert _names(db_session, role="any") == {"Acme"}


def test_the_refusal_holds_across_roles(db_session: Session) -> None:
    _create(db_session, "Vendor", vat_number="12345678901", roles=["supplier"])
    with pytest.raises(Conflict):
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="Same company", vat_number="12345678901"), ADMIN
        )


def test_foreign_numbers_are_compared_without_punctuation_or_case(db_session: Session) -> None:
    _create(db_session, "Dutch BV", vat_number="NL123456789B01", country="NL", roles=["supplier"])
    with pytest.raises(Conflict):
        OrganizationService(db_session).create(
            OrganizationCreate(
                legal_name="Dutch BV again", vat_number="nl 1234.56.789 b01", country="NL"
            ),
            ADMIN,
        )


def test_the_same_number_in_another_country_is_another_company(db_session: Session) -> None:
    _create(db_session, "Belgian", vat_number="123456789", country="BE")
    _create(db_session, "Luxembourgish", vat_number="123456789", country="LU")


def test_a_number_longer_than_twenty_characters_or_without_any_is_refused_for_a_foreign_country(
    db_session: Session,
) -> None:
    service = OrganizationService(db_session)
    for number in ("X" * 21, "---"):
        with pytest.raises(ValidationFailed) as exc:
            service.create(
                OrganizationCreate(legal_name="Foreign", vat_number=number, country="NL"), ADMIN
            )
        assert exc.value.details["field"] == "vat_number"
    assert _create(db_session, "Dutch", vat_number="NL123456789B01", country="NL").vat_number == (
        "NL123456789B01"
    )


def test_changing_a_number_or_a_country_into_a_collision_is_refused(db_session: Session) -> None:
    _create(db_session, "Acme", vat_number="12345678901")
    other = _create(db_session, "Other", vat_number="10987654321")
    service = OrganizationService(db_session)
    with pytest.raises(Conflict):
        service.update(other.id, OrganizationUpdate(vat_number="12345678901"), ADMIN)
    # Same row, same number: not a collision with itself.
    assert service.update(other.id, OrganizationUpdate(vat_number="10987654321"), ADMIN)
    # A move to the country of an identical number collides too.
    abroad = _create(db_session, "Abroad", vat_number="12345678901", country="SM")
    with pytest.raises(Conflict):
        service.update(abroad.id, OrganizationUpdate(country="IT"), ADMIN)


def test_an_archived_organization_does_not_hold_its_number_but_cannot_come_back_over_another(
    db_session: Session,
) -> None:
    service = OrganizationService(db_session)
    old = _create(db_session, "Old", vat_number="12345678901")
    service.soft_delete(old.id, ADMIN)
    _create(db_session, "New", vat_number="12345678901")
    with pytest.raises(Conflict):
        service.restore(old.id, ADMIN)


def test_the_unique_index_holds_where_the_service_is_not_asked(db_session: Session) -> None:
    db_session.add(Organization(legal_name="One", vat_number="12345678901"))
    db_session.flush()
    db_session.add(Organization(legal_name="Two", vat_number="12345678901"))
    with pytest.raises(IntegrityError) as exc:
        db_session.flush()
    assert VAT_UNIQUE_INDEX in str(exc.value.orig)
    db_session.rollback()


# -- the duplicate report ---------------------------------------------------------------------


def _without_the_index(session: Session) -> None:
    session.execute(text(f"DROP INDEX {VAT_UNIQUE_INDEX}"))


def test_the_report_groups_the_rows_that_share_a_number_and_the_index_waits_for_them(
    db_session: Session,
) -> None:
    _without_the_index(db_session)
    older = Organization(legal_name="Older", vat_number="12345678901")
    newer = Organization(legal_name="Newer", vat_number="12345678901", roles=["supplier"])
    foreign = [
        Organization(legal_name="NL one", vat_number="NL123456789B01", country="NL"),
        Organization(legal_name="NL two", vat_number="nl-123456789-b01", country="NL"),
    ]
    alone = Organization(legal_name="Alone", vat_number="10987654321")
    archived = Organization(
        legal_name="Archived", vat_number="10987654321", deleted_at=datetime.now(UTC)
    )
    db_session.add_all([older, newer, *foreign, alone, archived])
    db_session.flush()

    groups = duplicate_vat_groups(db_session)
    assert [(g.country, [row.legal_name for row in g.organizations]) for g in groups] == [
        ("IT", ["Older", "Newer"]),
        ("NL", ["NL one", "NL two"]),
    ]
    assert groups[0].organizations[1].roles == ("supplier",)
    assert ensure_vat_index(db_session) == "blocked"

    # A person resolves them: one archived, one number corrected.
    newer.deleted_at = datetime.now(UTC)
    foreign[1].vat_number = "NL999999999B01"
    db_session.flush()
    assert duplicate_vat_groups(db_session) == []
    assert ensure_vat_index(db_session) == "created"
    assert ensure_vat_index(db_session) == "present"


def test_the_report_is_an_unscoped_administrators(db_session: Session) -> None:
    assert OrganizationService(db_session).duplicates(ADMIN) == []
    with pytest.raises(PermissionDenied):
        OrganizationService(db_session).duplicates(COLLAB)


def test_the_service_refuses_a_duplicate_even_while_the_index_is_missing(
    db_session: Session,
) -> None:
    _without_the_index(db_session)
    _create(db_session, "Acme", vat_number="12345678901")
    with pytest.raises(Conflict):
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="Acme again", vat_number="12345678901"), ADMIN
        )


# -- terms and fiscal kinds -------------------------------------------------------------------


def test_the_two_directions_keep_their_own_payment_terms(db_session: Session) -> None:
    both = _create(
        db_session,
        "Both",
        roles=["customer", "supplier"],
        payment_days=30,
        payment_end_of_month=True,
        supplier_payment_days=60,
    )
    assert (both.payment_days, both.payment_end_of_month) == (30, True)
    assert (both.supplier_payment_days, both.supplier_payment_end_of_month) == (60, False)


@pytest.mark.parametrize(
    "fields",
    [
        {"supplier_kind": "freelance"},
        {"customer_kind": "company"},
        {"supplier_kind": "foreign", "supplier_area": "mars"},
        {"supplier_kind": "company", "supplier_area": "eu"},
        {"supplier_supplies": "goods"},
    ],
)
def test_a_kind_outside_its_list_or_a_foreign_detail_on_a_domestic_supplier_is_refused(
    db_session: Session, fields: dict[str, str]
) -> None:
    with pytest.raises(ValidationFailed):
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="X", roles=["supplier"], **fields), ADMIN
        )


def test_a_foreign_supplier_says_where_it_is_and_what_it_supplies(db_session: Session) -> None:
    supplier = _create(
        db_session,
        "Vendor",
        roles=["supplier"],
        supplier_kind="foreign",
        supplier_area="eu",
        supplier_supplies="services",
        country="DE",
    )
    assert (supplier.supplier_area, supplier.supplier_supplies) == ("eu", "services")
    service = OrganizationService(db_session)
    with pytest.raises(ValidationFailed):
        service.update(supplier.id, OrganizationUpdate(supplier_kind="company"), ADMIN)


def test_a_public_body_recipient_code_is_six_characters_and_everyone_elses_seven(
    db_session: Session,
) -> None:
    service = OrganizationService(db_session)
    body = _create(db_session, "Comune", customer_kind="public_body", sdi_code="UF1234")
    assert body.sdi_code == "UF1234"
    with pytest.raises(ValidationFailed):
        service.create(
            OrganizationCreate(legal_name="Ente", customer_kind="public_body", sdi_code="ABCDEFG"),
            ADMIN,
        )
    with pytest.raises(ValidationFailed):
        service.create(
            OrganizationCreate(legal_name="Srl", customer_kind="business", sdi_code="UF1234"),
            ADMIN,
        )
    # Changing the kind alone re-checks the code already stored.
    private = _create(db_session, "Srl", sdi_code="ABCDEFG")
    with pytest.raises(ValidationFailed):
        service.update(private.id, OrganizationUpdate(customer_kind="public_body"), ADMIN)


def test_the_withholding_agent_flag_defaults_to_no_and_cannot_be_cleared_to_null(
    db_session: Session,
) -> None:
    acme = _create(db_session, "Acme")
    assert acme.is_withholding_agent is False
    service = OrganizationService(db_session)
    assert service.update(acme.id, OrganizationUpdate(is_withholding_agent=True), ADMIN)
    with pytest.raises(ValidationFailed):
        service.update(acme.id, OrganizationUpdate(is_withholding_agent=None), ADMIN)


# -- the Gmail roster syncs customers only -----------------------------------------------------


def test_the_gmail_roster_holds_customers_only(db_session: Session) -> None:
    customer = _create(
        db_session,
        "Customer",
        website="customer.example",
        contact_points=points_in(email="a@customer.example"),
    )
    supplier = _create(
        db_session,
        "Supplier",
        roles=["supplier"],
        contact_points=points_in(email="billing@supplier.example"),
    )
    both = _create(
        db_session,
        "Both",
        roles=["customer", "supplier"],
        contact_points=points_in(email="hello@both.example"),
    )
    db_session.add_all(
        [
            Person(
                first_name="Sam",
                contact_points=points(email="sam@supplier.example"),
                customer_id=supplier.id,
            ),
            Person(
                first_name="Cy",
                contact_points=points(email="cy@customer.example"),
                customer_id=customer.id,
            ),
            Person(first_name="Free", contact_points=points(email="free@nowhere.example")),
        ]
    )
    db_session.flush()
    roster = AddressRoster(db_session)

    assert roster.known_addresses() == (
        "a@customer.example",
        "cy@customer.example",
        "free@nowhere.example",
        "hello@both.example",
    )
    resolved = roster.resolve_many(
        ["billing@supplier.example", "sam@supplier.example", "hello@both.example"]
    )
    assert address_key("billing@supplier.example") not in resolved
    assert address_key("sam@supplier.example") not in resolved
    assert {(ref.entity_type, ref.entity_id) for ref in resolved["hello@both.example"]} == {
        ("organization", both.id)
    }


# -- the unchanged key is not asked again, a race is named, placeholders are nobody's number ----


def test_saving_a_row_of_a_legacy_pair_unchanged_is_not_refused(db_session: Session) -> None:
    """A form resends the number and the country it loaded. On a space whose rows already
    collide neither row of the pair may become uneditable: only a change of the key is checked."""
    _without_the_index(db_session)
    first = Organization(legal_name="Beta", vat_number="12345678901", country="IT")
    second = Organization(legal_name="Beta twin", vat_number="12345678901", country="IT")
    db_session.add_all([first, second])
    db_session.flush()
    service = OrganizationService(db_session)

    renamed = service.update(
        second.id,
        OrganizationUpdate(legal_name="Beta twin srl", vat_number="12345678901", country="it"),
        ADMIN,
    )
    assert renamed.legal_name == "Beta twin srl"
    # Writing the same number in another form is the same key too.
    assert service.update(first.id, OrganizationUpdate(vat_number="12345678901"), ADMIN)
    # A change of the key into a taken one is still refused.
    third = _create(db_session, "Third", vat_number="10987654321")
    with pytest.raises(Conflict):
        service.update(third.id, OrganizationUpdate(vat_number="12345678901"), ADMIN)


def test_a_race_on_an_update_is_a_conflict_that_names_the_row_and_keeps_the_transaction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index refuses what the service's own look missed. The answer is the Italian
    conflict even when the row had no number before, and the rest of the transaction stands."""
    holder = _create(db_session, "Holder", vat_number="12345678901")
    earlier = _create(db_session, "Earlier write")
    mine = _create(db_session, "Mine")
    monkeypatch.setattr(OrganizationService, "_check_vat_free", lambda *args, **kwargs: None)

    with pytest.raises(Conflict) as exc:
        OrganizationService(db_session).update(
            mine.id, OrganizationUpdate(vat_number="12345678901"), ADMIN
        )

    assert exc.value.details["existing_id"] == str(holder.id)
    assert db_session.get(Organization, earlier.id) is not None, "the transaction was not undone"
    db_session.expire_all()
    assert db_session.get_one(Organization, mine.id).vat_number is None


@pytest.mark.parametrize("placeholder", ["", "-", "/", " . "])
def test_a_number_that_is_only_punctuation_is_nobodys_and_never_blocks_the_index(
    db_session: Session, placeholder: str
) -> None:
    _without_the_index(db_session)
    db_session.add_all(
        [
            Organization(legal_name="One", vat_number=placeholder),
            Organization(legal_name="Two", vat_number=placeholder),
        ]
    )
    db_session.flush()

    assert duplicate_vat_groups(db_session) == []
    assert ensure_vat_index(db_session) == "created"
    # And the index it built takes the placeholder twice, as it takes no number twice.
    db_session.add(Organization(legal_name="Three", vat_number=placeholder))
    db_session.flush()


def test_a_vat_number_holds_ascii_only(db_session: Session) -> None:
    """Python and Postgres fold the case of a letter like ß differently, so the comparison key
    would depend on which of them computed it."""
    with pytest.raises(ValidationFailed) as exc:
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="Straße", vat_number="DE12ß456789", country="DE"), ADMIN
        )
    assert exc.value.details["field"] == "vat_number"
    assert "ASCII" in exc.value.message


# -- shared across the legal entities -----------------------------------------------------------


def _second_legal_entity(session: Session) -> LegalEntity:
    other = LegalEntity(legal_name="Second Ltd", country="IE", is_default=False)
    session.add(other)
    session.flush()
    return other


def test_a_shared_organization_belongs_to_every_legal_entitys_list(db_session: Session) -> None:
    other = _second_legal_entity(db_session)
    own = _create(db_session, "Own")
    shared = _create(db_session, "Shared")
    elsewhere = Organization(legal_name="Elsewhere", legal_entity_id=other.id)
    db_session.add(elsewhere)
    db_session.flush()

    service = OrganizationService(db_session)
    assert service.share(shared.id, ADMIN).legal_entity_id is None
    assert _names(db_session, legal_entity_id=own.legal_entity_id) == {"Own", "Shared"}
    assert _names(db_session, legal_entity_id=other.id) == {"Elsewhere", "Shared"}
    # Sharing twice is the same row, and says it once on the timeline.
    service.share(shared.id, ADMIN)
    entries = db_session.execute(
        text("SELECT count(*) FROM activities WHERE entity_id = :id AND kind = 'updated'"),
        {"id": shared.id},
    ).scalar_one()
    assert entries == 1


def test_sharing_is_an_administrators_and_an_update_with_a_null_legal_entity_is_sharing(
    db_session: Session,
) -> None:
    acme = _create(db_session, "Acme")
    service = OrganizationService(db_session)
    with pytest.raises(PermissionDenied):
        service.share(acme.id, COLLAB)
    with pytest.raises(PermissionDenied):
        service.update(acme.id, OrganizationUpdate(legal_entity_id=None), COLLAB)
    assert (
        service.update(acme.id, OrganizationUpdate(legal_entity_id=None), ADMIN).legal_entity_id
        is None
    )
    # Said again on a shared row, nothing is written.
    assert (
        service.update(acme.id, OrganizationUpdate(legal_entity_id=None), ADMIN).legal_entity_id
        is None
    )


def test_what_is_born_under_a_shared_organization_takes_the_default_legal_entity(
    db_session: Session,
) -> None:
    from pigrocrm.core.legal_entities.service import LegalEntityService

    service = LegalEntityService(db_session)
    assert service.inherited(None, "deal") == service.resolve(None).id
