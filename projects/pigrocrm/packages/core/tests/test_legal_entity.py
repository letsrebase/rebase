from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.legal_entities.assets import LegalEntityAssets
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.schemas import (
    EMAIL_SIGNATURE_MAX_LENGTH,
    LegalEntityCreate,
    LegalEntityUpsert,
)
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.render.pdf import BLANK_PNG as PNG_PIXEL
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture(autouse=True)
def _bare(db_session: Session) -> None:
    """This file tests the first save too, so it starts from a space with no azienda,
    undoing the default row the session fixture seeds for everyone else."""
    db_session.execute(delete(LegalEntity))
    db_session.flush()


def _upsert(**overrides: object) -> LegalEntityUpsert:
    payload: dict[str, object] = {
        "legal_name": "Studio Rossi",
        "vat_number": "01234567890",
        "certified_email": "studiorossi@pec.it",
        "address": "Via Roma 1",
        "city": "Milano",
        "cap": "20053",
        "province": "MI",
        "phone": "+39 02 1234567",
        "email": "mario@example.com",
        "tax_regime": "Regime forfettario, L. 190/2014 art. 1 commi 54-89",
    }
    payload.update(overrides)
    return LegalEntityUpsert(**payload)  # type: ignore[arg-type]


def test_get_before_any_save_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        LegalEntityService(db_session).get(ADMIN)


def test_upsert_creates_the_single_row(db_session: Session) -> None:
    profile = LegalEntityService(db_session).upsert_default(_upsert(), ADMIN)
    assert profile.legal_name == "Studio Rossi"
    assert profile.vat_number == "01234567890"


def test_a_second_upsert_updates_rather_than_creating_a_second_row(db_session: Session) -> None:
    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    second = service.upsert_default(_upsert(legal_name="Nuovo Nome"), ADMIN)
    assert second.id == first.id
    assert second.legal_name == "Nuovo Nome"


def test_a_readonly_actor_cannot_write(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        LegalEntityService(db_session).upsert_default(_upsert(), READONLY)


def test_to_malformed_vat_number_is_refused(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        LegalEntityService(db_session).upsert_default(_upsert(vat_number="1234567890"), ADMIN)
    assert excinfo.value.details["field"] == "vat_number"


def test_to_vat_number_with_to_trailing_newline_is_stored_as_its_eleven_digits(
    db_session: Session,
) -> None:
    # The twelve-character value used to be refused so it could not reach the
    # String(11) column as a raw DataError. Since REB-615 both fiscal ids go through
    # `normalise_fiscal_id` on every write (spec 2026-10-03 §2 step 1), which strips
    # the newline the way it strips punctuation and an `IT` prefix: what is stored is
    # the code the classifier compares, never the keystroke that came with it.
    read = LegalEntityService(db_session).upsert_default(
        _upsert(vat_number="IT 01234567890\n"), ADMIN
    )
    assert read.vat_number == "01234567890"


def test_a_value_the_normaliser_empties_is_refused_not_saved_as_null(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        LegalEntityService(db_session).upsert_default(_upsert(tax_code="non-un-codice"), ADMIN)
    assert excinfo.value.details["field"] == "tax_code"


def test_to_foreign_legal_entity_keeps_to_vat_number_that_is_not_italian_in_shape(
    db_session: Session,
) -> None:
    # A nine-digit British VAT number: `normalise_fiscal_id` would return None for it,
    # which is exactly why a foreign azienda goes through `normalise_foreign_fiscal_id`.
    read = LegalEntityService(db_session).upsert_default(
        _upsert(country="GB", vat_number="GB 123 4567 89"), ADMIN
    )
    assert read.vat_number == "GB123456789"


def test_as_template_values_exposes_the_profile_under_legal_entity(db_session: Session) -> None:
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    values = service.as_template_values(ADMIN)
    assert values["emittente"]["ragione_sociale"] == "Studio Rossi"
    assert values["emittente"]["partita_iva"] == "01234567890"
    assert "singleton" not in values["emittente"]


def test_as_template_values_before_any_save_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        LegalEntityService(db_session).as_template_values(ADMIN)


def test_upsert_converts_a_true_insert_race_into_a_clean_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates two concurrent first-time saves: `repo.default()` reports no row for
    both, so both take the insert branch, and only the partial unique index on
    `is_default` stops the second.

    This is the case an earlier draft of `upsert` got wrong: it called
    `repo.add(...)` -- which flushes -- *before* the `try/except IntegrityError`,
    so the constraint violation from this exact scenario escaped as a raw,
    session-poisoning `IntegrityError` instead of a clean `Conflict`. Forcing
    `repo.get()` to always report "no row" (rather than relying on real thread
    concurrency, which a single savepoint-backed test session cannot produce) is
    what reproduces that race deterministically.
    """
    service = LegalEntityService(db_session)
    monkeypatch.setattr(LegalEntityRepository, "default", lambda self: None)
    service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(Conflict):
        service.upsert_default(_upsert(legal_name="Secondo"), ADMIN)

    # The session must still be usable after the rollback, not poisoned.
    monkeypatch.undo()
    profile = LegalEntityService(db_session).get(ADMIN)
    assert profile.legal_name == "Studio Rossi"


def test_email_signature_holds_to_text_block_and_signature_key_still_holds_an_image(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Spec 10: the shipped `signature_key` is the storage key of a signature *image*, and
    an email does not attach one -- it wants a text block. The two coexist; neither is
    overloaded. Since REB-627 the key is written by the upload, never by the row's
    `PUT`, which leaves it as it is."""
    LegalEntityService(db_session).upsert_default(
        _upsert(email_signature="Mario Rossi\nConsulente"), ADMIN
    )
    LegalEntityAssets(db_session, local_storage).set_signature(PNG_PIXEL, ADMIN)
    # A second whole-row save names no key and leaves the image where it is.
    read = LegalEntityService(db_session).upsert_default(
        _upsert(email_signature="Mario Rossi\nConsulente"), ADMIN
    )
    assert read.email_signature == "Mario Rossi\nConsulente"
    assert read.signature_key is not None and read.signature_key.endswith(".png")


def test_email_signature_reaches_to_template_scope_under_legal_entity(db_session: Session) -> None:
    """`as_template_values` derives from `LegalEntityRead`, so a column added to the
    model but forgotten on the read schema would be silently absent from every rendered
    document and every rendered email instead of failing anywhere."""
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(email_signature="Mario Rossi\nConsulente"), ADMIN)
    scope = service.as_template_values(ADMIN)
    assert scope["emittente"]["firma_email"] == "Mario Rossi\nConsulente"


def test_email_signature_is_bounded_and_rejects_to_nul_byte() -> None:
    with pytest.raises(PydanticValidationError):
        _upsert(email_signature="a" * (EMAIL_SIGNATURE_MAX_LENGTH + 1))
    with pytest.raises(PydanticValidationError):
        _upsert(email_signature="Mario\x00Rossi")


# -- several aziende (REB-615) -------------------------------------------------
#
# No `create` exists before milestone 5 (spec 2026-10-03 §9), so a second azienda is
# written by row here, which is what the migration of milestone 3 will also do. The
# point of these tests is that two rows already behave as two aziende: two fiscal
# profiles apart, one default, a deactivation that refuses the default.


def _second_legal_entity(db_session: Session, **overrides: object) -> LegalEntity:
    values: dict[str, object] = {
        "name": "rebase",
        "legal_name": "Rebase S.r.l.",
        "vat_number": "09876543210",
        "country": "IT",
    }
    values.update(overrides)
    row = LegalEntity(**values)  # type: ignore[arg-type]
    db_session.add(row)
    db_session.flush()
    return row


def test_nome_is_derived_from_the_legal_name_when_missing(db_session: Session) -> None:
    read = LegalEntityService(db_session).upsert_default(_upsert(legal_name="S" * 120), ADMIN)
    assert read.name == "S" * 80
    assert read.is_default is True
    assert read.active is True


def test_the_list_puts_the_default_first_and_hides_an_inactive_one(db_session: Session) -> None:
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(legal_name="Zeta"), ADMIN)
    second = _second_legal_entity(db_session, name="alfa")
    assert [a.name for a in service.list(ADMIN)] == ["Zeta", "alfa"]
    service.deactivate(second.id, ADMIN)
    assert [a.name for a in service.list(ADMIN)] == ["Zeta"]
    assert [a.name for a in service.list(ADMIN, only_active=False)] == ["Zeta", "alfa"]


def test_two_legal_entities_hold_two_fiscal_profiles_apart(db_session: Session) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    legal_entities = LegalEntityService(db_session)
    first = legal_entities.upsert_default(_upsert(), ADMIN)
    second = _second_legal_entity(db_session)
    fiscal = FiscalProfileService(db_session)
    fiscal.upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    fiscal.upsert(
        FiscalProfileUpsert(pack_id="it-standard"),
        ADMIN,
        legal_entity_id=second.id,
    )
    assert fiscal.get(ADMIN).regime_code == "RF19"
    assert fiscal.get(ADMIN, first.id).regime_code == "RF19"
    assert fiscal.get(ADMIN, second.id).regime_code == "RF01"
    assert fiscal.get(ADMIN, second.id).pack_id == "it-standard"
    assert fiscal.snapshot(second.id).default_vat_rate == Decimal("22.00")
    assert fiscal.snapshot().default_vat_rate == Decimal("0.00")


def test_the_default_moves_in_one_step_and_never_onto_an_inactive_legal_entity(
    db_session: Session,
) -> None:
    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    second = _second_legal_entity(db_session)
    moved = service.set_default(second.id, ADMIN)
    assert moved.is_default is True
    assert service.get(ADMIN, first.id).is_default is False
    assert service.get(ADMIN).id == second.id
    service.deactivate(first.id, ADMIN)
    with pytest.raises(ValidationFailed):
        service.set_default(first.id, ADMIN)


def test_the_default_cannot_be_deactivated(db_session: Session) -> None:
    service = LegalEntityService(db_session)
    default = service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(ValidationFailed) as excinfo:
        service.deactivate(default.id, ADMIN)
    assert excinfo.value.details["field"] == "active"


def test_update_addresses_one_legal_entity_and_leaves_the_other_alone(db_session: Session) -> None:
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    second = _second_legal_entity(db_session)
    read = service.update(
        second.id,
        _upsert(legal_name="Rebase S.r.l.", vat_number="09876543210", name="rb"),
        ADMIN,
    )
    assert read.name == "rb"
    assert service.get(ADMIN).legal_name == "Studio Rossi"


def test_to_vat_number_already_held_by_another_legal_entity_is_to_conflict(
    db_session: Session,
) -> None:
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    second = _second_legal_entity(db_session)
    with pytest.raises(Conflict):
        service.update(second.id, _upsert(legal_name="Doppia", vat_number="01234567890"), ADMIN)


def test_to_missing_legal_entity_id_is_not_found_under_the_table_label(db_session: Session) -> None:
    from uuid import uuid4

    with pytest.raises(NotFound) as excinfo:
        LegalEntityService(db_session).get(ADMIN, uuid4())
    assert excinfo.value.details["entity"] == "legal_entity"


def test_a_foreign_vat_number_fits_the_column_and_one_beyond_it_is_refused_not_a_500(
    db_session: Session,
) -> None:
    # A French VAT number is 13 characters once normalised and fits since REB-619
    # widened the column; one longer than the column is still refused in words, never
    # as a DataError.
    read = LegalEntityService(db_session).upsert_default(
        _upsert(country="FR", vat_number="FR 12 345678901"), ADMIN
    )
    assert read.vat_number == "FR12345678901"
    with pytest.raises(ValidationFailed) as excinfo:
        LegalEntityService(db_session).upsert_default(
            _upsert(country="FR", vat_number="FR" + "1" * 25), ADMIN
        )
    assert excinfo.value.details["field"] == "vat_number"
    # The session is still usable: a refusal before the flush poisons nothing.
    assert [a.vat_number for a in LegalEntityService(db_session).list(ADMIN)] == ["FR12345678901"]


def test_the_database_refuses_an_inactive_default(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    LegalEntityService(db_session).upsert_default(_upsert(), ADMIN)
    row = LegalEntityRepository(db_session).default()
    assert row is not None
    row.active = False
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_lower_case_country_code_does_not_make_an_italian_p_iva_foreign(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        LegalEntityService(db_session).upsert_default(
            _upsert(country="it", vat_number="123"), ADMIN
        )
    assert excinfo.value.details["field"] == "vat_number"
    read = LegalEntityService(db_session).upsert_default(_upsert(country="it"), ADMIN)
    assert read.country == "IT"


# --- creation, the single-azienda resolution and the deactivation count (REB-630) -----


def _create(**overrides: object) -> LegalEntityCreate:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert

    payload: dict[str, object] = {
        # A create carries no version (REB-622): the upsert's fields without it.
        **_upsert().model_dump(exclude={"updated_at"}),
        "name": "rebase ltd",
        "legal_name": "Rebase Ltd",
        "vat_number": "GB123456789",
        "country": "GB",
        "province": None,
        "certified_email": None,
        "fiscal_profile": FiscalProfileUpsert(pack_id="non-it", default_vat_rate=Decimal("20.00")),
    }
    payload.update(overrides)
    return LegalEntityCreate(**payload)  # type: ignore[arg-type]


def test_create_gives_to_second_legal_entity_its_profile_in_one_call(db_session: Session) -> None:
    from pigrocrm.core.fiscal.service import FiscalProfileService

    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    created = service.create(_create(), ADMIN)
    assert (created.name, created.is_default, created.active) == ("rebase ltd", False, True)
    assert created.vat_number == "GB123456789"
    profile = FiscalProfileService(db_session).get(ADMIN, created.id)
    assert (profile.pack_id, profile.default_vat_rate) == ("non-it", Decimal("20.00"))
    assert [a.id for a in service.list(ADMIN)] == [first.id, created.id]


def test_the_first_legal_entity_to_space_creates_is_its_default(db_session: Session) -> None:
    created = LegalEntityService(db_session).create(_create(), ADMIN)
    assert created.is_default is True
    assert LegalEntityService(db_session).get(ADMIN).id == created.id


def test_to_profile_the_fiscal_service_refuses_leaves_no_legal_entity_behind(
    db_session: Session,
) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert

    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(ValidationFailed) as refused:
        service.create(
            _create(
                fiscal_profile=FiscalProfileUpsert(
                    pack_id="non-it", default_vat_rate=Decimal("20.00"), apply_stamp_duty=True
                )
            ),
            ADMIN,
        )
    assert refused.value.details["field"] == "apply_stamp_duty"
    assert LegalEntityRepository(db_session).count() == 1


def test_create_is_admin_only_and_refuses_to_fiscal_id_another_legal_entity_holds(
    db_session: Session,
) -> None:
    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(PermissionDenied):
        service.create(_create(), READONLY)
    with pytest.raises(Conflict):
        service.create(_create(country="IT", vat_number="01234567890"), ADMIN)
    assert LegalEntityRepository(db_session).count() == 1


def test_single_resolves_the_only_legal_entity_and_never_picks_between_two(
    db_session: Session,
) -> None:
    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    assert service.single(None) is not None
    assert service.require_single(None, entity="analytics").id == first.id
    second = service.create(_create(), ADMIN)
    assert service.single(None) is None
    with pytest.raises(ValidationFailed) as refused:
        service.require_single(None, entity="analytics")
    assert refused.value.details["entity"] == "analytics"
    assert refused.value.details["field"] == "legal_entity_id"
    assert service.require_single(second.id, entity="analytics").id == second.id
    # A deactivated azienda no longer makes the choice ambiguous: the sidebar does not
    # offer it, and the one active azienda is again the one every read means.
    service.deactivate(second.id, ADMIN)
    assert service.require_single(None, entity="analytics").id == first.id


def test_deactivate_answers_how_many_customers_still_point_at_the_row(
    db_session: Session,
) -> None:
    from pigrocrm.core.customers.models import Customer

    service = LegalEntityService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    second = service.create(_create(), ADMIN)
    db_session.add_all(
        [
            Customer(legal_name="Cliente uno", country="GB", legal_entity_id=second.id),
            Customer(legal_name="Cliente due", country="GB", legal_entity_id=second.id),
        ]
    )
    db_session.flush()
    deactivated = service.deactivate(second.id, ADMIN)
    assert deactivated.active is False
    assert deactivated.linked_customers == 2


# ---- the version check (REB-622, spec 2026-10-03 §11) ----------------------------------


def test_an_update_built_on_the_current_row_is_accepted_and_moves_the_version(
    db_session: Session,
) -> None:
    from pigrocrm.core.errors import StaleRow

    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    second = service.update(first.id, _upsert(city="Torino", updated_at=first.updated_at), ADMIN)
    assert second.city == "Torino"
    assert second.updated_at > first.updated_at
    # The draft built on `first` is stale now: refused before anything is assigned, and
    # the refusal names the row's current version so a client can tell a reload brought it.
    with pytest.raises(StaleRow) as excinfo:
        service.update(first.id, _upsert(city="Genova", updated_at=first.updated_at), ADMIN)
    assert excinfo.value.code == "stale_row"
    assert excinfo.value.details == {"entity": "legal_entity", "updated_at": second.updated_at}
    assert service.get(ADMIN).city == "Torino"


def test_an_update_with_no_version_keeps_the_old_behaviour(db_session: Session) -> None:
    """The MCP tools read right before they write and an older client sends nothing:
    a body without `updated_at` is a whole-row replace as before."""
    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    service.update(first.id, _upsert(city="Torino", updated_at=first.updated_at), ADMIN)
    third = service.update(first.id, _upsert(city="Genova"), ADMIN)
    assert third.city == "Genova"


def test_the_version_is_compared_as_an_instant_and_never_written(db_session: Session) -> None:
    """A naive copy of the row's own timestamp is the same instant and is accepted; the
    value is never assigned to the row, whose `updated_at` moves on its own."""
    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    naive = first.updated_at.astimezone(UTC).replace(tzinfo=None)
    second = service.update(first.id, _upsert(city="Torino", updated_at=naive), ADMIN)
    assert second.updated_at != first.updated_at


def test_to_null_version_on_an_legal_entity_is_to_draft_built_on_nothing(
    db_session: Session,
) -> None:
    """The row always exists here, so `null` is never its version: sent, it is checked
    like any other value and refused; only an absent key checks nothing."""
    from pigrocrm.core.errors import StaleRow

    service = LegalEntityService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(StaleRow):
        service.update(first.id, _upsert(city="Torino", updated_at=None), ADMIN)
    assert service.get(ADMIN).city == "Milano"


def test_a_creation_takes_no_version(db_session: Session) -> None:
    """A row being created was built on nothing: `LegalEntityCreate` refuses the key
    rather than carrying a field `create` would have to ignore."""
    with pytest.raises(PydanticValidationError):
        LegalEntityCreate(
            name="x",
            legal_name="X",
            fiscal_profile=FiscalProfileUpsert(),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),  # type: ignore[call-arg]
        )
