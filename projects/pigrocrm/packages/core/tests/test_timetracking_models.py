"""The four tables, their constraints and their indexes, asserted against real
Postgres. `Base.metadata.create_all` has already run in the `db_engine` fixture, so
every assertion here is about the schema that actually exists."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.timetracking.models import Cost, CostCategory, PeriodLock, TimeEntry


def test_the_four_tables_exist_with_the_expected_columns(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    assert {"time_entries", "costs", "cost_categories", "period_locks"} <= set(
        inspector.get_table_names()
    )
    entry_columns = {c["name"] for c in inspector.get_columns("time_entries")}
    assert entry_columns == {
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "deal_id",
        "user_id",
        "data",
        "hours",
        "description",
        "billable",
        "applied_rate",
        "applied_cost",
        "rate_source",
        "cost_source",
        "invoice_line_id",
        "internal_notes",
        "custom_fields",
    }


def test_numeric_precision_is_the_three_scales_and_never_float(db_engine: Engine) -> None:
    """Numeric(8,2) hours, Numeric(12,2) money, Numeric(12,6) factors -- read off the
    live catalogue, not off the model, because the model is what is being checked."""
    with db_engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT table_name || '.' || column_name, "
                    "       numeric_precision || ',' || numeric_scale "
                    "FROM information_schema.columns "
                    "WHERE table_name IN ('time_entries','costs','deals','users') "
                    "  AND data_type = 'numeric'"
                )
            ).all()
        )
    assert rows["time_entries.hours"] == "8,2"
    assert rows["time_entries.applied_rate"] == "12,6"
    assert rows["time_entries.applied_cost"] == "12,6"
    assert rows["costs.amount"] == "12,2"
    assert rows["deals.hourly_rate"] == "12,6"
    assert rows["users.default_hourly_rate"] == "12,6"
    assert rows["users.default_hourly_cost"] == "12,6"
    # And nothing in these tables is a float.
    with db_engine.connect() as conn:
        floats = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name IN ('time_entries','costs','cost_categories') "
                "  AND data_type IN ('double precision','real')"
            )
        ).scalar_one()
    assert floats == 0


def test_the_partial_indexes_this_slice_queries_by_exist(db_engine: Engine) -> None:
    """(deal_id, data) and (user_id, data), both partial on `deleted_at IS NULL` --
    the shape every query in this slice has, and the cure residual R7 asks for in
    general. Asserted through `pg_indexes.indexdef` so a non-partial index of the
    same name still fails."""
    with db_engine.connect() as conn:
        definitions = {
            name: definition
            for name, definition in conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'time_entries'")
            ).all()
        }
    assert "deleted_at IS NULL" in definitions["ix_time_entries_deal_data"]
    assert "deleted_at IS NULL" in definitions["ix_time_entries_user_data"]
    with db_engine.connect() as conn:
        cost_definitions = {
            name: definition
            for name, definition in conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'costs'")
            ).all()
        }
    assert "deleted_at IS NULL" in cost_definitions["ix_costs_deal_data"]
    assert "deleted_at IS NULL" in cost_definitions["ix_costs_data"]


def test_a_billed_entry_cannot_be_soft_deleted_even_in_raw_sql(
    db_session: Session, seeded_entry_id, draft_invoice_line_id
) -> None:
    """The CHECK, not the service. §4.3: the disappearance of an hour that belongs to
    a fiscal document is covered at the level no write path can go around. The
    constraint is deliberately WIDER than the service rule -- it forbids deleting any
    entry bound to a line, draft included -- because distinguishing invoice state
    would need to read another table, i.e. a trigger, and this project keeps that kind
    of invisible logic out of the database.

    Bound to a *draft*, which since 4B-3 is the sharpest form of the point: the service
    would happily edit this entry, and the CHECK still refuses to let it disappear."""
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": draft_invoice_line_id, "id": seeded_entry_id},
    )
    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(
            text("UPDATE time_entries SET deleted_at = now() WHERE id = :id"),
            {"id": seeded_entry_id},
        )
        db_session.flush()
    assert "ck_time_entries_billed_not_deleted" in str(excinfo.value)
    db_session.rollback()


def test_period_locks_primary_key_is_the_month(db_session: Session) -> None:
    db_session.add(PeriodLock(year=2026, month=3, closed_at=datetime.now(UTC), closed_by=None))
    db_session.flush()
    db_session.add(PeriodLock(year=2026, month=3, closed_at=datetime.now(UTC), closed_by=None))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_cost_category_code_is_unique_but_null_repeats(db_session: Session) -> None:
    """Copies `pipeline_stages.code` exactly (residual R11's fix): `code` is the stable
    identity of a seeded category, distinct from `nome`, which the user may rename.
    Postgres treats every NULL as distinct under a unique index, so any number of
    user-created code-less categories coexist."""
    db_session.add_all(
        [
            CostCategory(nome="Prima", code=None),
            CostCategory(nome="Seconda", code=None),
            CostCategory(nome="Terza", code="viaggi"),
        ]
    )
    db_session.flush()
    db_session.add(CostCategory(nome="Quarta", code="viaggi"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_cost_amount_may_be_negative_but_never_zero(
    db_session: Session, seeded_category_id, seeded_deal_id
) -> None:
    """§4.4: a negative amount is a refund or a credit note received -- the same choice
    slice 3 §6.1 rule 6 makes for a discount ("a discount is a line"), instead of a
    `type` column that multiplies the cases in every sum. Zero is refused, because it
    is neither a cost nor a correction."""
    db_session.add(
        Cost(
            deal_id=seeded_deal_id,
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            amount=Decimal("-120.00"),
            description="Rimborso hotel",
        )
    )
    db_session.flush()
    db_session.add(
        Cost(
            deal_id=None,
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            amount=Decimal("0.00"),
            description="Niente",
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    assert "ck_costs_amount_not_zero" in str(excinfo.value)
    db_session.rollback()


def test_hours_are_bounded_by_the_database_too(
    db_session: Session, seeded_deal_id, seeded_user_id
) -> None:
    """`ore > 0 AND ore <= 24` as a CHECK as well as a schema bound. 24 is not a
    productivity limit, it is a shape check: a value above it is almost always the
    comma slip that writes 80 for 8,0, which without the bound would enter the margin
    as ten thousand euros of work never done."""
    for bad in (Decimal("0.00"), Decimal("-1.00"), Decimal("24.01")):
        db_session.add(
            TimeEntry(
                deal_id=seeded_deal_id,
                user_id=seeded_user_id,
                data=date(2026, 3, 1),
                hours=bad,
                description="x",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()
