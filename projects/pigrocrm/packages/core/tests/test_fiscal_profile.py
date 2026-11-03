"""One row, an admin-only writer, and an activity on every change.

R5 (no audit trail for configuration) stays open in general and is closed here:
changing fiscal regime without a trace is a different order of severity from renaming
a pipeline stage, and the timeline is also what reconstructs the history of regimes
without a `valid_from`/`valid_to` column -- which spec 7.1 considered and rejected
because spec 6.2 forbids back-dating past the current year, so no emission ever needs
a previous period's parameters.
"""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import (
    DEFAULT_LEGAL_REFERENCE,
    DEFAULT_STAMP_DUTY_AMOUNT,
    DEFAULT_STAMP_DUTY_THRESHOLD,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaborator")


@pytest.fixture(autouse=True)
def _default_legal_entity(db_session: Session) -> None:
    """Since REB-615 a fiscal profile belongs to an azienda, and every read and write
    here that names none resolves to the default one, so the space needs it first,
    exactly as a real space has it from provisioning."""
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi"), ADMIN
    )


def _payload(**overrides: object) -> FiscalProfileUpsert:
    base: dict[str, object] = {
        "regime_code": "RF19",
        "default_vat_rate": Decimal("0.00"),
        "default_vat_nature": "N2.2",
        "legal_reference": DEFAULT_LEGAL_REFERENCE,
        "apply_stamp_duty": True,
        "stamp_duty_threshold": DEFAULT_STAMP_DUTY_THRESHOLD,
        "stamp_duty_amount": DEFAULT_STAMP_DUTY_AMOUNT,
        "payment_terms": "TP02",
        "payment_method": "MP05",
        "due_days": 30,
        "iban": "IT60X0542811101000000123456",
    }
    base.update(overrides)
    return FiscalProfileUpsert(**base)  # type: ignore[arg-type]


def test_reading_a_profile_that_does_not_exist_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        FiscalProfileService(db_session).get(ADMIN)


def test_the_defaults_are_the_values_of_law(db_session: Session) -> None:
    profile = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(regime_code="RF19"), ADMIN
    )
    assert profile.stamp_duty_threshold == Decimal("77.47")
    assert profile.stamp_duty_amount == Decimal("2.00")
    assert profile.apply_stamp_duty is True
    assert profile.payment_terms == "TP02"
    assert profile.payment_method == "MP05"
    assert profile.default_vat_rate == Decimal("0.00")


def test_a_second_upsert_updates_the_same_row(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    first = service.upsert(_payload(), ADMIN)
    second = service.upsert(_payload(due_days=60), ADMIN)
    assert first.id == second.id
    assert second.due_days == 60


def test_only_an_admin_may_write(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        FiscalProfileService(db_session).upsert(_payload(), COLLABORATOR)


def test_every_upsert_records_an_activity(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    profile = service.upsert(_payload(), ADMIN)
    service.upsert(
        _payload(
            pack_id="it-standard",
            regime_code="RF01",
            default_vat_nature=None,
            default_vat_rate=Decimal("22.00"),
            legal_reference=None,
            apply_stamp_duty=False,
            profitability_coefficient=None,
            substitute_tax_rate=None,
            social_security_rate=None,
        ),
        ADMIN,
    )
    entries = ActivityService(db_session).timeline("fiscal_profile", profile.id)
    assert [entry.kind for entry in entries] == ["updated", "updated"]
    assert "regime_code" in entries[0].payload["changed"]


def test_an_unimplemented_regime_is_refused_at_the_service_boundary(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(_payload(regime_code="RF07"), ADMIN)
    assert caught.value.details["field"] == "regime_code"


def test_a_regime_code_with_a_trailing_newline_never_reaches_the_column(
    db_session: Session,
) -> None:
    """String(4) plus `.fullmatch`: `re.match` with `$` would accept "RF19\\n" and
    hand five characters to a four-character column as a raw DataError.

    Here the five-character value is actually intercepted one layer earlier, by
    `FiscalProfileUpsert.regime_code`'s own `max_length=4` -- a `pydantic.
    ValidationError` at construction time, before `FiscalProfileService.upsert` ever
    runs. That is not a weaker guarantee: the point of `max_length=n` mirroring every
    `String(n)` column (see `schemas.py`'s own module docstring) is exactly to stop
    this class of value before it reaches a raw, session-poisoning `DataError`, and a
    schema `ValidationError` is just as clean a rejection as the service's own
    `ValidationFailed`. `resolve_regime`'s `.fullmatch` still matters as defense in
    depth for any caller that does not go through this schema.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FiscalProfileService(db_session).upsert(_payload(regime_code="RF19\n"), ADMIN)


def test_an_iban_longer_than_the_column_is_refused_by_pydantic(db_session: Session) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _payload(iban="IT" + "0" * 40)


def test_to_flat_rate_profile_must_declare_to_vat_nature(db_session: Session) -> None:
    """Under a zero-rate regime the summary group needs a Natura, and a profile with
    neither a natura nor a non-zero default rate can only produce invoices the SdI
    rejects. Caught at configuration time, not at emission time."""
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(_payload(default_vat_nature=None), ADMIN)
    assert caught.value.details["field"] == "default_vat_nature"


def test_the_snapshot_is_the_profile_without_its_identity(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    service.upsert(_payload(), ADMIN)
    snapshot = service.snapshot()
    assert snapshot.regime_code == "RF19"
    assert snapshot.due_days == 30
    assert not hasattr(snapshot, "id")


def test_upsert_turns_a_true_insert_race_into_a_clean_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `repo.get()` pre-check cannot cover two concurrent first-time saves: both
    see no row, both insert, and only the unique key on `legal_entity_id` stops the
    second. Forced deterministically -- a savepoint-backed test session cannot produce
    real thread concurrency -- the same technique `test_emitter.py` already uses."""
    service = FiscalProfileService(db_session)
    service.upsert(_payload(), ADMIN)
    monkeypatch.setattr(FiscalProfileRepository, "get", lambda self, legal_entity_id: None)
    with pytest.raises(Conflict):
        service.upsert(_payload(), ADMIN)


def test_one_profile_by_legal_entity_is_the_database_guarantee(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    from pigrocrm.core.legal_entities.repository import LegalEntityRepository

    legal_entity = LegalEntityRepository(db_session).default()
    assert legal_entity is not None
    db_session.add(FiscalProfile(regime_code="RF19", legal_entity_id=legal_entity.id))
    db_session.flush()
    db_session.add(FiscalProfile(regime_code="RF19", legal_entity_id=legal_entity.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_foreign_profile_leaves_the_italian_fields_empty_unless_told_otherwise(
    db_session: Session,
) -> None:
    """REB-619: on the `non-it` pack the schema's defaults are not the forfettario's,
    so the minimal body an API or MCP caller sends saves a profile with no natura, no
    bollo and no income parameters, and the service then accepts it."""
    minimal = FiscalProfileUpsert(pack_id="non-it", default_vat_rate=Decimal("20.00"))
    assert minimal.regime_code is None
    assert (minimal.default_vat_nature, minimal.legal_reference) == (None, None)
    assert minimal.apply_stamp_duty is False
    assert (
        minimal.profitability_coefficient,
        minimal.substitute_tax_rate,
        minimal.social_security_rate,
    ) == (None, None, None)
    saved = FiscalProfileService(db_session).upsert(minimal, ADMIN)
    assert (saved.pack_id, saved.regime_code, saved.apply_stamp_duty) == ("non-it", None, False)
    assert saved.profitability_coefficient is None
    # The Italian pack keeps the forfettario's defaults, untouched by the rule above.
    assert FiscalProfileUpsert(regime_code="RF19").default_vat_nature == "N2.2"


def test_to_foreign_profile_refuses_the_stamp_duty_and_the_forfettarios_parameters(
    db_session: Session,
) -> None:
    service = FiscalProfileService(db_session)
    with pytest.raises(ValidationFailed) as stamp_duty:
        service.upsert(
            FiscalProfileUpsert(
                pack_id="non-it", default_vat_rate=Decimal("20.00"), apply_stamp_duty=True
            ),
            ADMIN,
        )
    assert stamp_duty.value.details["field"] == "apply_stamp_duty"
    with pytest.raises(ValidationFailed) as coefficient:
        service.upsert(
            FiscalProfileUpsert(
                pack_id="non-it",
                default_vat_rate=Decimal("20.00"),
                profitability_coefficient=Decimal("67.00"),
            ),
            ADMIN,
        )
    assert coefficient.value.details["field"] == "profitability_coefficient"


def test_to_foreign_zero_rate_needs_to_vat_nature_and_says_so_in_its_own_words(
    db_session: Session,
) -> None:
    """`invoice_lines` requires a natura beside a zero rate whoever issues; the foreign
    refusal names the reason a foreign company understands, not the SdI."""
    with pytest.raises(ValidationFailed) as refused:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="non-it"), ADMIN)
    assert refused.value.details["field"] == "default_vat_nature"
    assert "estera" in refused.value.message
    saved = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="non-it", default_vat_nature="N2.1", legal_reference="Art. 7"),
        ADMIN,
    )
    assert (saved.default_vat_nature, saved.legal_reference) == ("N2.1", "Art. 7")


# ---- the version check (REB-622, spec 2026-10-03 §11) ----------------------------------


def test_a_save_built_on_the_current_profile_is_accepted_and_a_stale_one_refused(
    db_session: Session,
) -> None:
    from pigrocrm.core.errors import StaleRow

    service = FiscalProfileService(db_session)
    first = service.upsert(_payload(), ADMIN)
    second = service.upsert(_payload(due_days=60, updated_at=first.updated_at), ADMIN)
    assert second.due_days == 60
    assert second.updated_at > first.updated_at
    with pytest.raises(StaleRow) as excinfo:
        service.upsert(_payload(due_days=90, updated_at=first.updated_at), ADMIN)
    assert excinfo.value.code == "stale_row"
    assert excinfo.value.details == {"entity": "fiscal_profile", "updated_at": second.updated_at}
    assert service.get(ADMIN).due_days == 60
    # No version, no check: the MCP tool's whole-row replace as before.
    assert service.upsert(_payload(due_days=90), ADMIN).due_days == 90


def test_null_is_the_version_of_a_profile_not_saved_yet(db_session: Session) -> None:
    """A first save built on «nothing there» sends `null` and is accepted; the same
    `null` once somebody else created the row is a draft built on a state that is gone,
    and a timestamp sent for a profile that does not exist was built on nothing either.
    A body without the key (the MCP tool) checks nothing on a first save."""
    from datetime import UTC, datetime

    from pigrocrm.core.errors import StaleRow

    service = FiscalProfileService(db_session)
    with pytest.raises(StaleRow) as excinfo:
        service.upsert(_payload(updated_at=datetime(2026, 1, 1, tzinfo=UTC)), ADMIN)
    assert excinfo.value.details["updated_at"] is None
    first = service.upsert(_payload(updated_at=None), ADMIN)
    assert first.due_days == 30
    with pytest.raises(StaleRow) as again:
        service.upsert(_payload(due_days=60, updated_at=None), ADMIN)
    assert again.value.details["updated_at"] == first.updated_at
    assert service.upsert(_payload(due_days=60), ADMIN).due_days == 60
