"""One row, an admin-only writer, and an activity on every change.

R5 (no audit trail for configuration) stays open in general and is closed here:
changing fiscal regime without a trace is a different order of severity from renaming
a pipeline stage, and the timeline is also what reconstructs the history of regimes
without a `valido_da`/`valido_a` column -- which spec 7.1 considered and rejected
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
    DEFAULT_IMPORTO_BOLLO,
    DEFAULT_RIFERIMENTO_NORMATIVO,
    DEFAULT_SOGLIA_BOLLO,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATORE = Actor(id=None, type="user", role="collaboratore")


@pytest.fixture(autouse=True)
def _default_azienda(db_session: Session) -> None:
    """Since REB-615 a fiscal profile belongs to an azienda, and every read and write
    here that names none resolves to the default one, so the space needs it first,
    exactly as a real space has it from provisioning."""
    from pigrocrm.core.emitter.schemas import AziendaUpsert
    from pigrocrm.core.emitter.service import AziendaService

    AziendaService(db_session).upsert_default(AziendaUpsert(ragione_sociale="Studio Rossi"), ADMIN)


def _payload(**overrides: object) -> FiscalProfileUpsert:
    base: dict[str, object] = {
        "codice_regime": "RF19",
        "aliquota_iva_default": Decimal("0.00"),
        "natura_default": "N2.2",
        "riferimento_normativo": DEFAULT_RIFERIMENTO_NORMATIVO,
        "applica_bollo": True,
        "soglia_bollo": DEFAULT_SOGLIA_BOLLO,
        "importo_bollo": DEFAULT_IMPORTO_BOLLO,
        "condizioni_pagamento": "TP02",
        "modalita_pagamento": "MP05",
        "giorni_scadenza": 30,
        "iban": "IT60X0542811101000000123456",
    }
    base.update(overrides)
    return FiscalProfileUpsert(**base)  # type: ignore[arg-type]


def test_reading_a_profile_that_does_not_exist_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        FiscalProfileService(db_session).get(ADMIN)


def test_the_defaults_are_the_values_of_law(db_session: Session) -> None:
    profile = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN
    )
    assert profile.soglia_bollo == Decimal("77.47")
    assert profile.importo_bollo == Decimal("2.00")
    assert profile.applica_bollo is True
    assert profile.condizioni_pagamento == "TP02"
    assert profile.modalita_pagamento == "MP05"
    assert profile.aliquota_iva_default == Decimal("0.00")


def test_a_second_upsert_updates_the_same_row(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    first = service.upsert(_payload(), ADMIN)
    second = service.upsert(_payload(giorni_scadenza=60), ADMIN)
    assert first.id == second.id
    assert second.giorni_scadenza == 60


def test_only_an_admin_may_write(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        FiscalProfileService(db_session).upsert(_payload(), COLLABORATORE)


def test_every_upsert_records_an_activity(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    profile = service.upsert(_payload(), ADMIN)
    service.upsert(
        _payload(
            codice_regime="RF01",
            natura_default=None,
            aliquota_iva_default=Decimal("22.00"),
            riferimento_normativo=None,
        ),
        ADMIN,
    )
    entries = ActivityService(db_session).timeline("fiscal_profile", profile.id)
    assert [entry.kind for entry in entries] == ["updated", "updated"]
    assert "codice_regime" in entries[0].payload["changed"]


def test_an_unimplemented_regime_is_refused_at_the_service_boundary(
    db_session: Session,
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(_payload(codice_regime="RF07"), ADMIN)
    assert caught.value.details["field"] == "codice_regime"


def test_a_regime_code_with_a_trailing_newline_never_reaches_the_column(
    db_session: Session,
) -> None:
    """String(4) plus `.fullmatch`: `re.match` with `$` would accept "RF19\\n" and
    hand five characters to a four-character column as a raw DataError.

    Here the five-character value is actually intercepted one layer earlier, by
    `FiscalProfileUpsert.codice_regime`'s own `max_length=4` -- a `pydantic.
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
        FiscalProfileService(db_session).upsert(_payload(codice_regime="RF19\n"), ADMIN)


def test_an_iban_longer_than_the_column_is_refused_by_pydantic(db_session: Session) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _payload(iban="IT" + "0" * 40)


def test_a_forfettario_profile_must_declare_a_natura(db_session: Session) -> None:
    """Under a zero-rate regime the summary group needs a Natura, and a profile with
    neither a natura nor a non-zero default rate can only produce invoices the SdI
    rejects. Caught at configuration time, not at emission time."""
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(_payload(natura_default=None), ADMIN)
    assert caught.value.details["field"] == "natura_default"


def test_the_snapshot_is_the_profile_without_its_identity(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    service.upsert(_payload(), ADMIN)
    snapshot = service.snapshot()
    assert snapshot.codice_regime == "RF19"
    assert snapshot.giorni_scadenza == 30
    assert not hasattr(snapshot, "id")


def test_upsert_turns_a_true_insert_race_into_a_clean_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `repo.get()` pre-check cannot cover two concurrent first-time saves: both
    see no row, both insert, and only the unique key on `azienda_id` stops the
    second. Forced deterministically -- a savepoint-backed test session cannot produce
    real thread concurrency -- the same technique `test_emitter.py` already uses."""
    service = FiscalProfileService(db_session)
    service.upsert(_payload(), ADMIN)
    monkeypatch.setattr(FiscalProfileRepository, "get", lambda self, azienda_id: None)
    with pytest.raises(Conflict):
        service.upsert(_payload(), ADMIN)


def test_one_profile_per_azienda_is_the_database_guarantee(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    from pigrocrm.core.emitter.repository import AziendaRepository

    azienda = AziendaRepository(db_session).default()
    assert azienda is not None
    db_session.add(FiscalProfile(codice_regime="RF19", azienda_id=azienda.id))
    db_session.flush()
    db_session.add(FiscalProfile(codice_regime="RF01", azienda_id=azienda.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_foreign_profile_leaves_the_italian_fields_empty_unless_told_otherwise(
    db_session: Session,
) -> None:
    """REB-619: on the `non-it` pack the schema's defaults are not the forfettario's,
    so the minimal body an API or MCP caller sends saves a profile with no natura, no
    bollo and no income parameters, and the service then accepts it."""
    minimal = FiscalProfileUpsert(pack_id="non-it", aliquota_iva_default=Decimal("20.00"))
    assert minimal.codice_regime is None
    assert (minimal.natura_default, minimal.riferimento_normativo) == (None, None)
    assert minimal.applica_bollo is False
    assert (
        minimal.coefficiente_redditivita,
        minimal.aliquota_imposta_sostitutiva,
        minimal.aliquota_inps,
    ) == (None, None, None)
    saved = FiscalProfileService(db_session).upsert(minimal, ADMIN)
    assert (saved.pack_id, saved.codice_regime, saved.applica_bollo) == ("non-it", None, False)
    assert saved.coefficiente_redditivita is None
    # The Italian pack keeps the forfettario's defaults, untouched by the rule above.
    assert FiscalProfileUpsert(codice_regime="RF19").natura_default == "N2.2"


def test_a_foreign_profile_refuses_the_bollo_and_the_forfettarios_parameters(
    db_session: Session,
) -> None:
    service = FiscalProfileService(db_session)
    with pytest.raises(ValidationFailed) as bollo:
        service.upsert(
            FiscalProfileUpsert(
                pack_id="non-it", aliquota_iva_default=Decimal("20.00"), applica_bollo=True
            ),
            ADMIN,
        )
    assert bollo.value.details["field"] == "applica_bollo"
    with pytest.raises(ValidationFailed) as coefficient:
        service.upsert(
            FiscalProfileUpsert(
                pack_id="non-it",
                aliquota_iva_default=Decimal("20.00"),
                coefficiente_redditivita=Decimal("67.00"),
            ),
            ADMIN,
        )
    assert coefficient.value.details["field"] == "coefficiente_redditivita"


def test_a_foreign_zero_rate_needs_a_natura_and_says_so_in_its_own_words(
    db_session: Session,
) -> None:
    """`invoice_lines` requires a natura beside a zero rate whoever issues; the foreign
    refusal names the reason a foreign company understands, not the SdI."""
    with pytest.raises(ValidationFailed) as refused:
        FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="non-it"), ADMIN)
    assert refused.value.details["field"] == "natura_default"
    assert "estera" in refused.value.message
    saved = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            pack_id="non-it", natura_default="N2.1", riferimento_normativo="Art. 7"
        ),
        ADMIN,
    )
    assert (saved.natura_default, saved.riferimento_normativo) == ("N2.1", "Art. 7")
