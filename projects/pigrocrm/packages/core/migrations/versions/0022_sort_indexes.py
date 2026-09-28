"""B-tree (column, id) indexes for the sort whitelist

Revision ID: 0022
Revises: 0021

Residuo R9's other half. `db/sort.py` declares the ordering contract
`ORDER BY <col> <dir>, id <dir>`, with `NULLS LAST` on the one nullable column and on no
other; without a matching index that is an in-memory sort of the whole table on every page
of every ordered scan, and the ordering feature ends up slower than the absence of it.

Twelve ascending indexes, one per admitted (entity, column) pair, plus one descending
index for the single nullable column in the whitelist. The tie-break carrying the same
direction as the column is what lets one ascending `(col, id)` index serve `desc` as a
backward scan -- but a backward scan yields `NULLS FIRST`, so `people.cognome` is the one
column that costs two.

**And that backward scan only happens if the ordering does not spell `NULLS LAST`.**
Postgres matches ordering pathkeys including nulls placement and will not use a `NOT NULL`
constraint to reconcile `DESC NULLS LAST` with what a backward scan of an ascending index
produces. An earlier version of this note claimed the tie-break direction was sufficient on
its own; it is not, and for as long as `order_by` emitted `NULLS LAST` unconditionally
eleven of these twelve indexes were dead for `dir=desc` -- a sequential scan and a sort,
with the index sitting there unused. `db/sort.py::order_by` now emits `NULLS LAST` only for
a nullable column. `tests/test_sort_plan.py` asserted the plan rather than the order until
REB-580 removed it with the planner corpus (a space's tables never reach the size where the
planner wants these), so the rule now lives in `db/sort.py`'s docstring and nothing fails
when it goes stale; a tenant that outgrows a space is the day to bring a plan test back.

None of the thirteen is partial on `deleted_at IS NULL`, unlike the trigram indexes of
0021. Those serve a predicate that always carries the clause; an ordering has to remain
usable for any listing, including one that asks for the deleted rows.

`CONCURRENTLY` is not used, for the reason 0021 records: Alembic runs each migration in a
transaction and `CREATE INDEX CONCURRENTLY` cannot run in one. Every statement carries
`IF NOT EXISTS` so an operator who built them out-of-band is not blocked.

Written as raw SQL rather than `op.create_index` because the thirteenth is an expression
index over `DESC NULLS LAST`, which has no `op.create_index` spelling; the twelve
ascending ones follow the same shape so the pair reads as one list.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index name, table, column). Mirrors the `__table_args__` declarations on the four
# models exactly -- `test_migrations_produce_exactly_the_models_schema` keeps the two
# honest, and `Base.metadata.create_all` is what makes them visible under test.
_ASCENDING: tuple[tuple[str, str, str], ...] = (
    ("ix_customers_created_at_id", "customers", "created_at"),
    ("ix_customers_updated_at_id", "customers", "updated_at"),
    ("ix_customers_ragione_sociale_id", "customers", "ragione_sociale"),
    ("ix_people_created_at_id", "people", "created_at"),
    ("ix_people_updated_at_id", "people", "updated_at"),
    ("ix_people_cognome_id", "people", "cognome"),
    ("ix_deals_created_at_id", "deals", "created_at"),
    ("ix_deals_updated_at_id", "deals", "updated_at"),
    ("ix_deals_nome_id", "deals", "nome"),
    ("ix_documents_created_at_id", "documents", "created_at"),
    ("ix_documents_updated_at_id", "documents", "updated_at"),
    ("ix_documents_titolo_id", "documents", "titolo"),
)

_DESCENDING_NULLABLE: tuple[tuple[str, str, str], ...] = (
    ("ix_people_cognome_desc_id", "people", "cognome"),
)


def upgrade() -> None:
    for name, table, column in _ASCENDING:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column}, id)")
    for name, table, column in _DESCENDING_NULLABLE:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column} DESC NULLS LAST, id DESC)"
        )


def downgrade() -> None:
    for name, _table, _column in reversed(_ASCENDING + _DESCENDING_NULLABLE):
        op.execute(f"DROP INDEX IF EXISTS {name}")
