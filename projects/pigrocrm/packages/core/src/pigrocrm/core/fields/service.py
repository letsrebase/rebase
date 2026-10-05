from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.diff import field_changes
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.models import FieldDefinition
from pigrocrm.core.fields.repository import FieldDefinitionRepository
from pigrocrm.core.fields.schemas import (
    EntityType,
    FieldDefinitionCreate,
    FieldDefinitionRead,
    FieldDefinitionUpdate,
)
from pigrocrm.core.fields.types import OPTION_TYPES, FieldSpec
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# The timeline `entity_type` for a field definition. Its own value, not the
# `entity_type` *column* on the row (which says which entity the custom field belongs
# to, "customer"/"person"/"deal") -- the two are different questions and both end up
# in the audit entry, the first as `Activity.entity_type` and the second inside the
# payload.
ENTITY = "field_definition"

# Recorded on create/archive/unarchive. Deliberately not the whole row: `position` is
# cosmetic and `id` is already `Activity.entity_id`, while these five are what an
# administrator reading the timeline needs to recognise the definition without
# resolving a UUID -- and `required` is the flag whose flip is the "who turned this
# off" question this audit exists to answer.
_IDENTITY_FIELDS = ("entity_type", "key", "label", "field_type", "required")

# The attributes `update` may touch, mirroring `FieldDefinitionUpdate`'s own fields.
# Listed explicitly rather than derived from the incoming patch so that the "before"
# snapshot is taken over a fixed, reviewable set: a future field added to the update
# schema and forgotten here shows up as an unaudited change, which is a visible gap,
# rather than as a silently mis-shaped payload.
_AUDITED_FIELDS = ("label", "options", "required", "position")


def _identity(field: FieldDefinition) -> dict[str, object]:
    return {name: getattr(field, name) for name in _IDENTITY_FIELDS}


def _snapshot(field: FieldDefinition) -> dict[str, object]:
    """`options` is copied into a plain list: it is a mutable JSON column, so keeping
    the live object here would make the "before" snapshot follow the "after" value as
    soon as the attribute is reassigned, and every options change would audit as
    unchanged."""
    values = {name: getattr(field, name) for name in _AUDITED_FIELDS}
    values["options"] = list(field.options)
    return values


def _check_options(field_type: str, options: list[str]) -> None:
    if field_type in OPTION_TYPES and not options:
        raise ValidationFailed(
            "field_definition",
            "options",
            f"un campo di tipo {field_type} richiede almeno un'opzione",
            expected="una lista non vuota",
        )
    if field_type not in OPTION_TYPES and options:
        raise ValidationFailed(
            "field_definition",
            "options",
            f"un campo di tipo {field_type} non ammette opzioni",
            expected="una lista vuota",
        )


class FieldDefinitionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = FieldDefinitionRepository(session)
        self.activities = ActivityService(session)

    def create(self, data: FieldDefinitionCreate, actor: Actor) -> FieldDefinitionRead:
        actor.require_unscoped_admin("create_field_definition")
        if not data.key:
            raise ValidationFailed(
                "field_definition", "key", "chiave vuota dopo la normalizzazione"
            )
        _check_options(data.field_type, data.options)

        # A13. `native_fields()` is derived from the entity's own Create model
        # (schema_registry.py), and includes `EXTRA_NATIVE_FIELDS` -- columns a
        # Create schema cannot declare because they are set only later (invoice's
        # fiscal columns at emission, time_entry's/cost's billing-state columns
        # when a line is applied) -- so it stays correct as models change without
        # needing this guard to hand-list anything. Imported here rather than at
        # module scope: `schema_registry` imports this class
        # (`schema_registry.py:19`), so a top-level import is a real cycle -- the
        # same call-time resolution `server.py` uses for `register_entity_tools`.
        from pigrocrm.core.schema_registry import native_fields

        native = native_fields(data.entity_type)
        if data.key in native:
            raise ValidationFailed(
                "field_definition",
                "key",
                "collide con una colonna nativa dell'entità",
                expected=(
                    f"una chiave diversa da {data.key!r}: è già una colonna nativa di "
                    f"{data.entity_type}. Colonne native: {', '.join(sorted(native))}"
                ),
            )

        if self.repo.get_by_key(data.entity_type, data.key):
            raise Conflict(
                "field_definition",
                "esiste già un campo con questa chiave",
                entity_type=data.entity_type,
                key=data.key,
            )

        field = FieldDefinition(**data.model_dump())
        try:
            self.repo.add(field)
            # Inside the try, and after the add: `repo.add` flushes, so `field.id`
            # exists by now, and a collision that only the unique constraint can catch
            # rolls the audit entry back together with the row it would have claimed
            # was created.
            self.activities.record(ENTITY, field.id, "created", actor, _identity(field))
            self.session.commit()
        except IntegrityError as exc:
            # The pre-check above cannot cover a race between two concurrent requests:
            # there the database constraint is the only authority. The rollback is
            # mandatory — without it the session is unusable for the caller.
            self.session.rollback()
            raise Conflict(
                "field_definition",
                "esiste già un campo con questa chiave",
                entity_type=data.entity_type,
                key=data.key,
            ) from exc
        return FieldDefinitionRead.model_validate(field)

    def update(
        self, field_id: UUID, data: FieldDefinitionUpdate, actor: Actor
    ) -> FieldDefinitionRead:
        actor.require_unscoped_admin("update_field_definition")
        field = self.repo.get(field_id)
        if field is None:
            raise NotFound("field_definition", field_id)

        changes = supplied_changes(data)
        reject_cleared_columns("field_definition", FieldDefinition, changes)
        if "options" in changes:
            _check_options(field.field_type, changes["options"])
        before = _snapshot(field)
        for key, value in changes.items():
            setattr(field, key, value)

        # A rename and an archive must not look alike in the timeline: they are
        # different kinds, and this one carries the old and new label explicitly.
        # Nothing is recorded when the patch changed nothing -- see `field_changes`.
        delta = field_changes(before, _snapshot(field))
        if delta:
            self.activities.record(ENTITY, field.id, "updated", actor, {"key": field.key, **delta})
        self.session.commit()
        return FieldDefinitionRead.model_validate(field)

    def archive(self, field_id: UUID, actor: Actor) -> FieldDefinitionRead:
        """Archive rather than delete: deleting a definition while rows still hold the
        value in JSONB produces orphan data nobody can see.

        The key stays reserved on purpose, even while archived: `get_by_key` does not
        filter on `archived`, so `create` still raises `Conflict` for it. This is a
        choice, not an oversight -- freeing the key would let someone recreate it with
        a different `field_type`, and there is no correct way to reinterpret the
        existing JSONB values under a new type. That is exactly the operation
        `field_type` immutability exists to rule out at every layer; leaving the key
        reserved is what keeps archiving from reopening it through the back door. Use
        `unarchive` to bring the field back -- the stored data was never touched.
        """
        actor.require_unscoped_admin("archive_field_definition")
        field = self.repo.get(field_id)
        if field is None:
            raise NotFound("field_definition", field_id)
        was_archived = field.archived
        field.archived = True
        # Only when it really changed state: archiving an already-archived definition
        # is a no-op, and an entry for it would claim a decision nobody took. Same
        # reasoning as `CustomerService.restore`.
        if not was_archived:
            self.activities.record(ENTITY, field.id, "archived", actor, _identity(field))
        self.session.commit()
        return FieldDefinitionRead.model_validate(field)

    def unarchive(self, field_id: UUID, actor: Actor) -> FieldDefinitionRead:
        """Symmetric to `archive`. Only visibility changes -- archiving never touched
        the stored JSONB values, so there is nothing to restore in the data itself."""
        actor.require_unscoped_admin("unarchive_field_definition")
        field = self.repo.get(field_id)
        if field is None:
            raise NotFound("field_definition", field_id)
        was_archived = field.archived
        field.archived = False
        if was_archived:
            self.activities.record(ENTITY, field.id, "unarchived", actor, _identity(field))
        self.session.commit()
        return FieldDefinitionRead.model_validate(field)

    def specs_for(self, entity_type: EntityType) -> list[FieldSpec]:
        """The bridge to the validator and to the runtime model factory.

        Defined before `list` below: inside a class body, annotations are evaluated
        eagerly by looking the name up in the class namespace first. Once `def list`
        below has executed, the name `list` is rebound there from the builtin to that
        method, and this method's bare `list[FieldSpec]` return annotation would
        resolve to it instead — `TypeError: 'function' object is not subscriptable`.
        Defining this method first sidesteps that; do not reorder.
        """
        return [
            FieldSpec(
                key=f.key,
                label=f.label,
                field_type=f.field_type,  # type: ignore[arg-type]
                options=list(f.options),
                required=f.required,
            )
            for f in self.repo.list(entity_type)
        ]

    # `list` must stay the last method defined in this class. Giving a method the same
    # name as a builtin rebinds that name in the *class* namespace; any method defined
    # below this one whose own return annotation is a bare `list[...]` (as `specs_for`
    # above is, which is why it had to move ahead of this one) would resolve `list` to
    # this method instead of the builtin and fail at import time with `TypeError:
    # 'function' object is not subscriptable`. This is exactly the bug this class
    # shipped with once already. `test_module_imports.py` is the real guard against a
    # repeat -- it imports every module under `pigrocrm.core` and fails with the
    # module name and exception if any of them raises, in this class or any other,
    # present or future. Treat that test as the actual protection; this comment is
    # only here for whoever is looking at the diff that adds the next method.
    def list(
        self, entity_type: EntityType, *, include_archived: bool = False
    ) -> list[FieldDefinitionRead]:
        return [
            FieldDefinitionRead.model_validate(f)
            for f in self.repo.list(entity_type, include_archived=include_archived)
        ]
