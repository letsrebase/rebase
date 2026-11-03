"""`cap` becomes `postal_code`: the two columns and the JSON that carries them

Revision ID: 0069
Revises: 0068

REB-898. Revision 0068 left one Italian abbreviation, `cap` (the postal code), because the
vocabulary lint reads it as English. It is renamed everywhere it means the postal code:

- the columns `customers.cap` and `legal_entities.cap`;
- the `cap` key of the customer and legal entity parties of `invoices.snapshot`;
- the `cap` name in the timeline payloads of the `customer` and `legal_entity` entities
  (`changed`, `before`, `after`).

`downgrade` reverses each step. No policy, trigger, function, CHECK or index names the
column, so nothing else is rebuilt. The template database `create_all`
builds already has the new column names, so the test `conftest.py` applies nothing of this
revision.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | Sequence[str] | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("customers", "legal_entities")
ENTITIES = ("customer", "legal_entity")
PARTIES = ("legal_entity", "customer")


def _swap(up: bool) -> tuple[str, str]:
    return ("cap", "postal_code") if up else ("postal_code", "cap")


def _rewrite_snapshot(snapshot: Any, old: str, new: str) -> Any:
    if not isinstance(snapshot, dict):
        return snapshot
    out = dict(snapshot)
    for party in PARTIES:
        value = out.get(party)
        if isinstance(value, dict) and old in value:
            out[party] = {(new if key == old else key): v for key, v in value.items()}
    return out


def _rewrite_payload(payload: Any, old: str, new: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    changed = out.get("changed")
    if isinstance(changed, list):
        out["changed"] = [new if name == old else name for name in changed]
    for side in ("before", "after"):
        value = out.get(side)
        if isinstance(value, dict) and old in value:
            out[side] = {(new if key == old else key): v for key, v in value.items()}
    return out


def _update_json(
    connection: Any, sql_select: str, sql_update: str, rewrite: Any, params: Any
) -> None:
    rows = connection.execute(sa.text(sql_select), params).fetchall()
    changed = []
    for row_id, value in rows:
        new = rewrite(value)
        if new != value:
            changed.append({"id": row_id, "value": new})
    if changed:
        connection.execute(
            sa.text(sql_update).bindparams(sa.bindparam("value", type_=sa.JSON(none_as_null=True))),
            changed,
        )


def _run(up: bool) -> None:
    old, new = _swap(up)
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} RENAME COLUMN "{old}" TO "{new}"')
    connection = op.get_bind()
    _update_json(
        connection,
        "SELECT id, snapshot FROM invoices WHERE snapshot IS NOT NULL",
        "UPDATE invoices SET snapshot = :value WHERE id = :id",
        lambda value: _rewrite_snapshot(value, old, new),
        {},
    )
    _update_json(
        connection,
        "SELECT id, payload FROM activities WHERE entity_type = ANY(:entities)",
        "UPDATE activities SET payload = :value WHERE id = :id",
        lambda value: _rewrite_payload(value, old, new),
        {"entities": list(ENTITIES)},
    )


def upgrade() -> None:
    _run(True)


def downgrade() -> None:
    _run(False)
