"""What an update tool owes the agent that called it (REB-807): a refusal when there is
nothing to apply, and, when there is, proof of what was.

Two failures looked like success. `update_deal(changes={})` was accepted, wrote an
`updated` timeline row with `changed: []` and answered with the deal as it was. And a
client that drops the `null`s of a nested object before sending (the symptom a field
that would not clear points to) turned «clear this field» into «change nothing», which
the same code answered with the unchanged record. Neither tells the agent that nothing
was applied.

`refuse_empty` is the first half: an update with nothing to say is a mistake of the
caller's, so it is answered as one, in Italian, instead of as a record. `report` is the
second: the record the service returned, next to the two keys an agent reads to check
its own call,

  * `changed`: the fields whose value is not what it was, by name, in the shape the
    timeline's `updated` row carries (`{"changed": [...]}`), and
  * `cleared`: of those, the ones now empty, each with the value it held, because
    «cleared» on its own cannot be told from «was already empty».

The difference is taken between a read before the call and the record the service
answers with, not from the keys the caller sent: a key the service ignored, or set to the
value it already had, is not a change, and a field the service derived (`rimborsabile`
recalculated from a new amount) is one.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel

from pigrocrm.core.errors import NotFound, ValidationFailed

# Not the caller's doing and not something an agent can set: the row's own bookkeeping,
# which moves on every write, a no-op included.
_BOOKKEEPING: Final[frozenset[str]] = frozenset({"updated_at"})

# The one nested object whose members are fields in their own right: an agent clears
# `custom_fields.sede` and not «custom_fields».
_NESTED: Final[str] = "custom_fields"

_REPORT_KEYS: Final[frozenset[str]] = frozenset({"changed", "cleared"})


def refuse_empty(changes: Mapping[str, Any] | None) -> None:
    """Refuses an update whose `changes` names no field.

    Raised as the product's own `ValidationFailed`, which the guard renders like every
    other refusal: what failed, what was expected, what to do.
    """
    # `custom_fields: {}` names nothing either: it is what `{"custom_fields": {"sede": null}}`
    # becomes in a client that drops the nulls of a nested object.
    if any(not (key == _NESTED and value == {}) for key, value in (changes or {}).items()):
        return
    raise ValidationFailed(
        "input",
        "changes",
        "nessun campo da modificare",
        expected=(
            "almeno un campo in `changes` con il suo nuovo valore; `null` svuota un campo, "
            "e un campo che non nomini resta com'è"
        ),
    )


def pick[T: BaseModel](rows: Iterable[T], identifier: UUID, entity: str) -> T:
    """The row of a contract's list that an update is about to change, for the tools whose
    service has no read of one row. Absent is `NotFound`, the answer the update itself
    would have given."""
    for row in rows:
        if getattr(row, "id", None) == identifier:
            return row
    raise NotFound(entity, identifier)


def refuse_nothing_given(**given: object) -> None:
    """The same refusal for a tool whose fields are parameters rather than a `changes`
    object (`update_proforma`, `update_timer`), where `None` means «not given»."""
    if any(value is not None for value in given.values()):
        return
    names = ", ".join(f"`{name}`" for name in given)
    raise ValidationFailed(
        "input",
        "campi",
        "nessun campo da modificare",
        expected=f"almeno uno fra {names}",
    )


def _flat(record: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if key in _BOOKKEEPING or key in _REPORT_KEYS:
            continue
        if key == _NESTED and isinstance(value, Mapping):
            flat.update({f"{key}.{inner}": inner_value for inner, inner_value in value.items()})
        else:
            flat[key] = value
    return flat


def report(before: BaseModel | None, after: BaseModel) -> dict[str, Any]:
    """`after` as the tool answers, with `changed` and `cleared` added: what differs from
    `before`, the record read ahead of the update (`None` when there was none to read).

    A field is compared when both records carry it: a read and an update of the same row
    can differ in the extras one of them computes (`scadenza_prevista` on an invoice's
    detail), and an extra is not a change. The exception is a custom field, which exists
    on one side only when it was set or removed: not set is empty, so `None` stands for
    the missing side.

    The values are compared as Python values and shown as JSON: the database answers an
    amount as `Decimal('1000.00')` and the update just set `Decimal('1000')`, which are
    the same amount and would read as two different strings.
    """
    answer = after.model_dump(mode="json")
    old = _flat(before.model_dump()) if before is not None else {}
    old_shown = _flat(before.model_dump(mode="json")) if before is not None else {}
    new = _flat(after.model_dump())
    compared = {
        key
        for key in old.keys() | new.keys()
        if key.startswith(f"{_NESTED}.") or (key in old and key in new)
    }
    changed = sorted(key for key in compared if old.get(key) != new.get(key))
    cleared = {
        key: old_shown[key] for key in changed if old.get(key) is not None and new.get(key) is None
    }
    return {**answer, "changed": changed, "cleared": cleared}
