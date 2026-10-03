from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.schemas import FIRMA_EMAIL_MAX_LENGTH, AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture(autouse=True)
def _bare(db_session: Session) -> None:
    """This file tests the first save too, so it starts from a space with no azienda,
    undoing the default row the session fixture seeds for everyone else."""
    db_session.execute(delete(Azienda))
    db_session.flush()


def _upsert(**overrides: object) -> AziendaUpsert:
    payload: dict[str, object] = {
        "ragione_sociale": "Studio Rossi",
        "partita_iva": "01234567890",
        "pec": "studiorossi@pec.it",
        "indirizzo": "Via Roma 1",
        "comune": "Milano",
        "cap": "20053",
        "provincia": "MI",
        "telefono": "+39 02 1234567",
        "email": "mario@example.com",
        "regime_fiscale": "Regime forfettario, L. 190/2014 art. 1 commi 54-89",
    }
    payload.update(overrides)
    return AziendaUpsert(**payload)  # type: ignore[arg-type]


def test_get_before_any_save_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        AziendaService(db_session).get(ADMIN)


def test_upsert_creates_the_single_row(db_session: Session) -> None:
    profile = AziendaService(db_session).upsert_default(_upsert(), ADMIN)
    assert profile.ragione_sociale == "Studio Rossi"
    assert profile.partita_iva == "01234567890"


def test_a_second_upsert_updates_rather_than_creating_a_second_row(db_session: Session) -> None:
    service = AziendaService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    second = service.upsert_default(_upsert(ragione_sociale="Nuovo Nome"), ADMIN)
    assert second.id == first.id
    assert second.ragione_sociale == "Nuovo Nome"


def test_a_readonly_actor_cannot_write(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        AziendaService(db_session).upsert_default(_upsert(), READONLY)


def test_a_malformed_partita_iva_is_refused(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        AziendaService(db_session).upsert_default(_upsert(partita_iva="1234567890"), ADMIN)
    assert excinfo.value.details["field"] == "partita_iva"


def test_a_partita_iva_with_a_trailing_newline_is_stored_as_its_eleven_digits(
    db_session: Session,
) -> None:
    # The twelve-character value used to be refused so it could not reach the
    # String(11) column as a raw DataError. Since REB-615 both fiscal ids go through
    # `normalise_fiscal_id` on every write (spec 2026-10-03 §2 step 1), which strips
    # the newline the way it strips punctuation and an `IT` prefix: what is stored is
    # the code the classifier compares, never the keystroke that came with it.
    read = AziendaService(db_session).upsert_default(_upsert(partita_iva="IT 01234567890\n"), ADMIN)
    assert read.partita_iva == "01234567890"


def test_a_value_the_normaliser_empties_is_refused_not_saved_as_null(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        AziendaService(db_session).upsert_default(_upsert(codice_fiscale="non-un-codice"), ADMIN)
    assert excinfo.value.details["field"] == "codice_fiscale"


def test_a_foreign_azienda_keeps_a_vat_number_that_is_not_italian_in_shape(
    db_session: Session,
) -> None:
    # A nine-digit British VAT number: `normalise_fiscal_id` would return None for it,
    # which is exactly why a foreign azienda goes through `normalise_foreign_fiscal_id`.
    read = AziendaService(db_session).upsert_default(
        _upsert(nazione="GB", partita_iva="GB 123 4567 89"), ADMIN
    )
    assert read.partita_iva == "GB123456789"


def test_as_template_values_exposes_the_profile_under_emittente(db_session: Session) -> None:
    service = AziendaService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    values = service.as_template_values(ADMIN)
    assert values["emittente"]["ragione_sociale"] == "Studio Rossi"
    assert values["emittente"]["partita_iva"] == "01234567890"
    assert "singleton" not in values["emittente"]


def test_as_template_values_before_any_save_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        AziendaService(db_session).as_template_values(ADMIN)


def test_upsert_converts_a_true_insert_race_into_a_clean_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates two concurrent first-time saves: `repo.default()` reports no row for
    both, so both take the insert branch, and only the partial unique index on
    `predefinita` stops the second.

    This is the case an earlier draft of `upsert` got wrong: it called
    `repo.add(...)` -- which flushes -- *before* the `try/except IntegrityError`,
    so the constraint violation from this exact scenario escaped as a raw,
    session-poisoning `IntegrityError` instead of a clean `Conflict`. Forcing
    `repo.get()` to always report "no row" (rather than relying on real thread
    concurrency, which a single savepoint-backed test session cannot produce) is
    what reproduces that race deterministically.
    """
    service = AziendaService(db_session)
    monkeypatch.setattr(AziendaRepository, "default", lambda self: None)
    service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(Conflict):
        service.upsert_default(_upsert(ragione_sociale="Secondo"), ADMIN)

    # The session must still be usable after the rollback, not poisoned.
    monkeypatch.undo()
    profile = AziendaService(db_session).get(ADMIN)
    assert profile.ragione_sociale == "Studio Rossi"


def test_firma_email_holds_a_text_block_and_firma_key_still_holds_an_image(
    db_session: Session,
) -> None:
    """Spec 10: the shipped `firma_key` is the storage key of a signature *image*, and
    an email does not attach one -- it wants a text block. The two coexist; neither is
    overloaded."""
    read = AziendaService(db_session).upsert_default(
        _upsert(firma_key="firme/rossi.png", firma_email="Mario Rossi\nConsulente"), ADMIN
    )
    assert read.firma_email == "Mario Rossi\nConsulente"
    assert read.firma_key == "firme/rossi.png"


def test_firma_email_reaches_a_template_scope_under_emittente(db_session: Session) -> None:
    """`as_template_values` derives from `AziendaRead`, so a column added to the
    model but forgotten on the read schema would be silently absent from every rendered
    document and every rendered email instead of failing anywhere."""
    service = AziendaService(db_session)
    service.upsert_default(_upsert(firma_email="Mario Rossi\nConsulente"), ADMIN)
    scope = service.as_template_values(ADMIN)
    assert scope["emittente"]["firma_email"] == "Mario Rossi\nConsulente"


def test_firma_email_is_bounded_and_rejects_a_nul_byte() -> None:
    with pytest.raises(PydanticValidationError):
        _upsert(firma_email="a" * (FIRMA_EMAIL_MAX_LENGTH + 1))
    with pytest.raises(PydanticValidationError):
        _upsert(firma_email="Mario\x00Rossi")


# -- several aziende (REB-615) -------------------------------------------------
#
# No `create` exists before milestone 5 (spec 2026-10-03 §9), so a second azienda is
# written by row here, which is what the migration of milestone 3 will also do. The
# point of these tests is that two rows already behave as two aziende: two fiscal
# profiles apart, one default, a deactivation that refuses the default.


def _second_azienda(db_session: Session, **overrides: object) -> Azienda:
    values: dict[str, object] = {
        "nome": "rebase",
        "ragione_sociale": "Rebase S.r.l.",
        "partita_iva": "09876543210",
        "nazione": "IT",
    }
    values.update(overrides)
    row = Azienda(**values)  # type: ignore[arg-type]
    db_session.add(row)
    db_session.flush()
    return row


def test_nome_is_derived_from_the_ragione_sociale_when_missing(db_session: Session) -> None:
    read = AziendaService(db_session).upsert_default(_upsert(ragione_sociale="S" * 120), ADMIN)
    assert read.nome == "S" * 80
    assert read.predefinita is True
    assert read.attiva is True


def test_the_list_puts_the_default_first_and_hides_an_inactive_one(db_session: Session) -> None:
    service = AziendaService(db_session)
    service.upsert_default(_upsert(ragione_sociale="Zeta"), ADMIN)
    second = _second_azienda(db_session, nome="alfa")
    assert [a.nome for a in service.list(ADMIN)] == ["Zeta", "alfa"]
    service.deactivate(second.id, ADMIN)
    assert [a.nome for a in service.list(ADMIN)] == ["Zeta"]
    assert [a.nome for a in service.list(ADMIN, only_active=False)] == ["Zeta", "alfa"]


def test_two_aziende_hold_two_fiscal_profiles_apart(db_session: Session) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    aziende = AziendaService(db_session)
    first = aziende.upsert_default(_upsert(), ADMIN)
    second = _second_azienda(db_session)
    fiscal = FiscalProfileService(db_session)
    fiscal.upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    fiscal.upsert(
        FiscalProfileUpsert(
            codice_regime="RF01", aliquota_iva_default=Decimal("22.00"), natura_default=None
        ),
        ADMIN,
        azienda_id=second.id,
    )
    assert fiscal.get(ADMIN).codice_regime == "RF19"
    assert fiscal.get(ADMIN, first.id).codice_regime == "RF19"
    assert fiscal.get(ADMIN, second.id).codice_regime == "RF01"
    assert fiscal.snapshot(second.id).aliquota_iva_default == Decimal("22.00")
    assert fiscal.snapshot().aliquota_iva_default == Decimal("0.00")


def test_the_default_moves_in_one_step_and_never_onto_an_inactive_azienda(
    db_session: Session,
) -> None:
    service = AziendaService(db_session)
    first = service.upsert_default(_upsert(), ADMIN)
    second = _second_azienda(db_session)
    moved = service.set_default(second.id, ADMIN)
    assert moved.predefinita is True
    assert service.get(ADMIN, first.id).predefinita is False
    assert service.get(ADMIN).id == second.id
    service.deactivate(first.id, ADMIN)
    with pytest.raises(ValidationFailed):
        service.set_default(first.id, ADMIN)


def test_the_default_cannot_be_deactivated(db_session: Session) -> None:
    service = AziendaService(db_session)
    default = service.upsert_default(_upsert(), ADMIN)
    with pytest.raises(ValidationFailed) as excinfo:
        service.deactivate(default.id, ADMIN)
    assert excinfo.value.details["field"] == "attiva"


def test_update_addresses_one_azienda_and_leaves_the_other_alone(db_session: Session) -> None:
    service = AziendaService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    second = _second_azienda(db_session)
    read = service.update(
        second.id,
        _upsert(ragione_sociale="Rebase S.r.l.", partita_iva="09876543210", nome="rb"),
        ADMIN,
    )
    assert read.nome == "rb"
    assert service.get(ADMIN).ragione_sociale == "Studio Rossi"


def test_a_partita_iva_already_held_by_another_azienda_is_a_conflict(db_session: Session) -> None:
    service = AziendaService(db_session)
    service.upsert_default(_upsert(), ADMIN)
    second = _second_azienda(db_session)
    with pytest.raises(Conflict):
        service.update(
            second.id, _upsert(ragione_sociale="Doppia", partita_iva="01234567890"), ADMIN
        )


def test_a_missing_azienda_id_is_not_found_under_the_table_label(db_session: Session) -> None:
    from uuid import uuid4

    with pytest.raises(NotFound) as excinfo:
        AziendaService(db_session).get(ADMIN, uuid4())
    assert excinfo.value.details["entity"] == "emitter_profile"
