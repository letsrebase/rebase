"""The update contract, in one place.

Residual A14: every service used `data.model_dump(exclude_none=True)`, which collapses
"the caller supplied `null`" into "the caller omitted the key" -- so no `Update` schema
could clear a numeric or date column. On `deals.ore_preventivate` that is a correctness
defect and not a convenience: `NULL` means "nobody estimated" and `0` means "estimated
zero hours", the budget report of slice 4 §9.2 treats them as opposite, and only `0` was
writable.

`model_fields_set` is the distinction `exclude_none` cannot make: it records which keys
the caller actually provided, before defaults are filled in, so an explicit `null`
survives as a key present with value `None`.

Three outcomes, all now reachable:
  * key omitted   -> absent from the result -> the column is not touched;
  * key = `null`  -> present with `None`    -> the column is set to `NULL`;
  * key = `""`    -> present with `""`      -> a text column is set to the empty string.

The third is unchanged and must stay so: plan 1B's form contract is built on it, and
every shipped form sends `""` for a cleared native text field.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect

from pigrocrm.core.errors import ValidationFailed


def supplied_changes(data: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Only the fields the caller actually supplied, `None` included.

    `exclude` is for fields a service handles separately -- `custom_fields` in every
    domain, whose `None`-inside-the-dict semantics are different and must not be routed
    through here.
    """
    return data.model_dump(exclude_unset=True, exclude=exclude or set())


def reject_cleared_columns(entity: str, model: type[Any], changes: dict[str, Any]) -> None:
    """Refuse a `null` aimed at a column the database declares `NOT NULL`.

    The consequence `exclude_none` was accidentally covering up. Every `Update` schema
    in this project spells *every* field `T | None = None`, because that is what makes a
    field optional in a partial update -- including the fields that back a `NOT NULL`
    column, such as `deals.nome` or `costs.importo`. While `exclude_none` was the
    contract those nulls were silently dropped; under `exclude_unset` they reach
    `setattr` and then `flush()`, and a `NOT NULL` violation does not merely fail the
    call -- it poisons the caller's `Session` for every statement after it, which is the
    failure mode this codebase already paid for on `customers.partita_iva` and guards
    against by hand in `AziendaService._check_fiscal`.

    Nullability is read from the mapper rather than from a hand-written list per
    service: a list is a second source of truth that drifts the moment somebody adds a
    column, and the whole point of this module is that the rule lives in one place.
    Keys that are not mapped columns at all (`custom_fields` is normally excluded before
    this runs, `detach` in `PersonUpdate` is not a column) are left alone -- this
    function answers one question only, and the service answers the rest.
    """
    columns = sa_inspect(model).columns
    for key, value in changes.items():
        if value is not None or key not in columns:
            continue
        if not columns[key].nullable:
            raise ValidationFailed(
                entity, key, "il campo non può essere svuotato", expected="un valore"
            )
