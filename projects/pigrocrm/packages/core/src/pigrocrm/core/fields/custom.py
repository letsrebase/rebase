"""The partial update of a record's custom fields, shared by every entity that carries
them (organizations, people, deals, and the money half's records).

The complete-set check of a `create` is `fields.validator.validate_custom_fields`, called
with every active spec; this is its partial-update twin.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.types import FieldSpec
from pigrocrm.core.fields.validator import validate_custom_fields


def merge_custom(
    entity: EntityType,
    specs: Sequence[FieldSpec],
    stored: Mapping[str, Any],
    provided: dict[str, Any],
) -> dict[str, Any]:
    """`stored` with the keys of `provided` applied, validated against the *active* `specs`
    of `entity`, and only the keys the caller touches: never the union with what is already
    stored. Validating the union would break the contract for archiving a field ("hide it,
    keep the data readable"): an archived key still in storage would fail the «campo non
    definito» check on every later update, even one that never mentions it, and there would
    be no way to clear it.

    A key supplied with `None` removes its entry, the one way left to clear a value once its
    definition is archived, unless the key belongs to an active, required definition, which
    raises the same «campo obbligatorio» that `validate_custom_fields` raises for `""`:
    `None` and `""` both mean «no value», and on a required field they must be refused
    alike, or a caller strips the value by choosing the other spelling. Clearing an archived
    or undefined key stays possible even if its definition was once required.

    Any other value must belong to an active definition: archiving closes a field to new
    input, it does not make it gone. Keys stored and not mentioned, archived or not, required
    or not, are carried over untouched.
    """
    active_by_key = {spec.key: spec for spec in specs}

    to_remove: set[str] = set()
    for key, value in provided.items():
        if value is not None:
            continue
        spec = active_by_key.get(key)
        if spec is not None and spec.required:
            raise ValidationFailed(
                entity, key, "campo obbligatorio", expected="un valore non vuoto"
            )
        to_remove.add(key)

    to_set = {key: value for key, value in provided.items() if value is not None}
    # Only the touched specs: `validate_custom_fields` fails a blank required one, which is
    # right for a complete set and wrong for an active required field this update never
    # mentions.
    touched = [spec for spec in active_by_key.values() if spec.key in to_set]
    validated = validate_custom_fields(entity, touched, to_set)

    merged = {key: value for key, value in stored.items() if key not in to_remove}
    merged.update(validated)
    return merged
