import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate, FieldDefinitionUpdate
from pigrocrm.core.fields.service import FieldDefinitionService

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaborator")


def _create(service: FieldDefinitionService, key: str = "settore", **kw):
    payload = {
        "entity_type": "organization",
        "key": key,
        "label": key.title(),
        "field_type": "text",
    }
    payload.update(kw)
    return service.create(FieldDefinitionCreate(**payload), ADMIN)


def test_create_returns_the_definition(db_session: Session) -> None:
    field = _create(FieldDefinitionService(db_session))
    assert field.key == "settore"
    assert field.archived is False


def test_key_is_slugified(db_session: Session) -> None:
    field = _create(FieldDefinitionService(db_session), key="  Settore Merceologico  ")
    assert field.key == "settore_merceologico"


def test_duplicate_key_on_the_same_entity_is_a_conflict(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    _create(service)
    with pytest.raises(Conflict):
        _create(service)


def test_the_same_key_on_a_different_entity_is_allowed(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    _create(service)
    other = service.create(
        FieldDefinitionCreate(
            entity_type="deal", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    assert other.entity_type == "deal"


def test_select_without_options_is_rejected(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        _create(service, key="status", field_type="select", options=[])
    assert exc.value.details["field"] == "options"


def test_non_select_with_options_is_rejected(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    with pytest.raises(ValidationFailed):
        _create(service, key="name", field_type="text", options=["a"])


def test_field_type_cannot_be_changed(db_session: Session) -> None:
    """There is no correct answer for text -> number with existing values, so the
    operation does not exist. Archive the old field and create a new one."""
    service = FieldDefinitionService(db_session)
    field = _create(service)

    assert "field_type" not in FieldDefinitionUpdate.model_fields
    # extra="forbid" turns the attempt into a pydantic ValidationError, so the
    # request never reaches the service at all.
    with pytest.raises(ValidationError):
        FieldDefinitionUpdate(field_type="number")  # type: ignore[call-arg]

    updated = service.update(field.id, FieldDefinitionUpdate(label="Nuovo"), ADMIN)
    assert updated.field_type == "text"


def test_archive_hides_the_field_but_keeps_it_readable(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    field = _create(service)
    service.archive(field.id, ADMIN)

    assert service.list("organization") == []
    archived = service.list("organization", include_archived=True)
    assert len(archived) == 1
    assert archived[0].archived is True


def test_archived_fields_are_excluded_from_specs(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    keep = _create(service, key="tenuto")
    drop = _create(service, key="archiviato")
    service.archive(drop.id, ADMIN)

    keys = [spec.key for spec in service.specs_for("organization")]
    assert keys == [keep.key]


def test_list_is_ordered_by_position_then_label(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    _create(service, key="terzo", position=2)
    _create(service, key="primo", position=0)
    _create(service, key="secondo", position=1)
    assert [f.key for f in service.list("organization")] == ["primo", "secondo", "terzo"]


def test_only_admins_change_the_schema(db_session: Session) -> None:
    service = FieldDefinitionService(db_session)
    with pytest.raises(PermissionDenied) as exc:
        service.create(
            FieldDefinitionCreate(
                entity_type="organization", key="x", label="X", field_type="text"
            ),
            COLLAB,
        )
    assert exc.value.details["required_roles"] == ["admin"]


def test_specs_for_returns_field_specs_usable_by_the_validator(db_session: Session) -> None:
    """`key="stato_operativo"`, not `"status"`: `customer` already has a native
    `status` column (organizations/schemas.py), so a custom field with that key would
    now be refused by the A13 guard (fields/service.py) -- exactly the collision
    this test would otherwise have silently exercised without ever noticing it
    was shadowing a real column."""
    from pigrocrm.core.fields.validator import validate_custom_fields

    service = FieldDefinitionService(db_session)
    _create(service, key="stato_operativo", field_type="select", options=["active", "sospeso"])

    specs = service.specs_for("organization")
    assert validate_custom_fields("organization", specs, {"stato_operativo": "active"}) == {
        "stato_operativo": "active"
    }


def test_key_length_is_bounded_to_the_column_width_after_slugification() -> None:
    """`key` is `String(60)` in the database. Without a matching Pydantic bound, a key
    that is still too long after slugification reaches `flush()` and comes back as a
    raw `sqlalchemy.exc.DataError` (StringDataRightTruncation) instead of a domain
    error -- and `DataError` is not a subclass of `IntegrityError`, so the service's
    existing `except IntegrityError` around the commit does not catch it either."""
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization", key="a" * 70, label="Lunga", field_type="text"
        )


def test_label_length_is_bounded_to_the_column_width() -> None:
    """`label` is `String(120)` in the database; same reasoning as the key bound above."""
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization", key="ok", label="a" * 121, field_type="text"
        )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("città", "citta"),
        ("perché", "perche"),
        ("così", "cosi"),
    ],
)
def test_key_slugification_transliterates_accents_instead_of_dropping_them(
    db_session: Session, raw: str, expected: str
) -> None:
    """`slugify_key` strips every character the regex does not recognise, including
    an accented letter's own base character -- `"città"` must not silently become
    `"citt"`. Normalising to NFKD and discarding only the combining marks keeps the
    base letter, which is what an Italian product needs."""
    field = _create(FieldDefinitionService(db_session), key=raw)
    assert field.key == expected


@pytest.mark.parametrize("raw", ["!!!", "   ...   ", "日本語"])
def test_key_that_slugifies_to_nothing_is_rejected(db_session: Session, raw: str) -> None:
    """Punctuation-only input, and input with no ASCII-transliterable letters at all,
    both slugify to an empty string. The service must reject that rather than create
    a field with an empty key."""
    service = FieldDefinitionService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        _create(service, key=raw)
    assert exc.value.details["field"] == "key"


def test_unarchive_restores_the_field_definition(db_session: Session) -> None:
    from pigrocrm.core.fields.validator import validate_custom_fields

    service = FieldDefinitionService(db_session)
    field = _create(service)
    value = {field.key: "prova"}
    specs_before = service.specs_for("organization")
    assert validate_custom_fields("organization", specs_before, value) == value

    service.archive(field.id, ADMIN)
    assert service.specs_for("organization") == []
    # The key stays reserved while archived, on purpose: recreating it would allow a
    # different, incompatible field_type under the same key.
    with pytest.raises(Conflict):
        _create(service)

    restored = service.unarchive(field.id, ADMIN)
    assert restored.archived is False

    specs_after = service.specs_for("organization")
    assert [spec.key for spec in specs_after] == [field.key]
    # Archiving/unarchiving never touched the stored value -- only its visibility.
    assert validate_custom_fields("organization", specs_after, value) == value


def test_duplicate_key_race_past_the_precheck_still_becomes_a_domain_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SELECT-then-INSERT precheck cannot see a row another request commits between
    its own SELECT and its own INSERT -- that gap is exactly what makes it a race. This
    simulates that race deterministically (no threads, no flakiness): force the precheck
    to report "not found" while a real duplicate already exists, so the INSERT hits the
    database's own unique constraint. `create()` must convert that into a domain
    `Conflict`, never let a raw `IntegrityError` escape, and must leave the session
    usable for whatever the caller does next."""
    service = FieldDefinitionService(db_session)
    _create(service)

    monkeypatch.setattr(service.repo, "get_by_key", lambda entity_type, key: None)

    with pytest.raises(Conflict) as exc:
        _create(service)
    assert exc.value.details["entity_type"] == "organization"

    # The session must still be usable right after -- a leftover PendingRollbackError
    # would blow up on the very next statement issued on it.
    assert len(service.list("organization")) == 1


# --- Final review item 2 (CRITICAL): position is a bare Integer, unbounded -------
#
# `field_definitions.position` is `Integer`. Nothing anywhere checked its range --
# unlike `probability`/`default_probability`, this column has no equivalent
# service-level guard, so `2**40` reached `flush()` raw as
# `psycopg.errors.NumericValueOutOfRange` before `Field(ge=..., le=...)` existed on
# `FieldDefinitionCreate.position`/`FieldDefinitionUpdate.position`.


def test_position_at_the_bound_is_accepted_on_create(db_session: Session) -> None:
    from pigrocrm.core.fields.schemas import POSITION_MAX, POSITION_MIN

    at_min = _create(FieldDefinitionService(db_session), key="a", position=POSITION_MIN)
    at_max = _create(FieldDefinitionService(db_session), key="b", position=POSITION_MAX)
    assert at_min.position == POSITION_MIN
    assert at_max.position == POSITION_MAX


def test_position_far_beyond_the_bound_is_rejected_on_create() -> None:
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization", key="x", label="X", field_type="text", position=2**40
        )


def test_position_far_beyond_the_bound_is_rejected_on_update() -> None:
    with pytest.raises(ValidationError):
        FieldDefinitionUpdate(position=2**40)


def test_a_negative_position_is_rejected() -> None:
    """Unlike `pipeline_stages.position` (which legitimately goes negative -- see
    test_pipeline.py), nothing in this project ever creates a custom field with a
    negative display order, so `position` is bounded to `>= 0`."""
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization", key="x", label="X", field_type="text", position=-1
        )


# --- Final review item 1 (CRITICAL): a NUL byte in label or inside options -------
#
# `apps/api/tests/test_input_bounds_sweep.py` sweeps both over real HTTP; these
# exercise the same gap directly at the schema layer.


def test_a_nul_byte_in_label_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization", key="x", label="X\x00Y", field_type="text"
        )


def test_a_nul_byte_inside_an_options_entry_is_rejected() -> None:
    """`options` is `list[SafeStr]`, not a single string -- the guard has to reach
    into every element, not just a top-level scalar field."""
    with pytest.raises(ValidationError):
        FieldDefinitionCreate(
            entity_type="organization",
            key="settore",
            label="Settore",
            field_type="select",
            options=["IT", "Retail\x00"],
        )


def test_a_nul_byte_in_key_is_silently_removed_by_slugify_not_rejected() -> None:
    """Documents a deliberate exception to "reject, don't strip": `key` does not get
    `SafeStr` (see the comment on `FieldDefinitionCreate.key`) because `_slugify`
    already runs first and replaces any character outside [a-z0-9] -- including a
    NUL byte -- with "_". This is pre-existing, reviewed behaviour this fix wave
    does not change; the test exists so a future reader does not "fix" the
    inconsistency by adding a SafeStr that would never fire."""
    field = FieldDefinitionCreate(
        entity_type="organization", key="a\x00b", label="X", field_type="text"
    )
    assert field.key == "a_b"
