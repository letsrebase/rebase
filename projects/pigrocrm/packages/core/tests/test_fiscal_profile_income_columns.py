"""§4.5. Three columns on the **existing** single row, not a second table.

Slice 1 §3 described a `FiscalProfile` holding "regime, ATECO coefficient, substitute
tax rate, INPS"; the table slice 3 delivered holds the parameters FatturaPA needed and
not the ones income calculation needs, which nobody used yet. They are the same concept,
so they go on the same single row: a second fiscal profile would create two answers to
"which regime am I in".

The three constants migrated out of the previous system's `App.jsx` --
`FORFETTARIO_PROFITABILITY_RATE = 0.67`, `FORFETTARIO_SUBSTITUTE_TAX_RATE = 0.05`,
`FORFETTARIO_INPS_RATE = 0.2607` -- become the defaults, as percentages. That migration is the final
payment on the debt slice 1 §2.2 cited as the empirical justification for this whole architecture.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")


def _seed(session: Session) -> FiscalProfileService:
    """A profile written the way the settings screen writes one: `regime_code` and
    nothing else, so what lands in the three new columns is whatever the schema
    defaults say and not something the test itself supplied."""
    service = FiscalProfileService(session)
    service.upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    return service


def test_the_three_columns_exist_at_the_right_precision(db_engine: Engine) -> None:
    columns = {c["name"]: c for c in inspect(db_engine).get_columns("fiscal_profile")}
    for name in (
        "profitability_coefficient",
        "substitute_tax_rate",
        "social_security_rate",
    ):
        assert name in columns, name
        assert columns[name]["nullable"] is True
    with db_engine.connect() as conn:
        precisions = dict(
            conn.execute(
                text(
                    "SELECT column_name, numeric_precision || ',' || numeric_scale "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'fiscal_profile' AND data_type = 'numeric'"
                )
            ).all()
        )
    assert precisions["profitability_coefficient"] == "5,2"
    assert precisions["substitute_tax_rate"] == "5,2"
    assert precisions["social_security_rate"] == "5,2"


def test_there_is_exactly_one_fiscal_profile_table(db_engine: Engine) -> None:
    """Asserted so nobody later adds the second profile §4.5 rules out."""
    tables = set(inspect(db_engine).get_table_names())
    assert "fiscal_profile" in tables
    assert not {t for t in tables if t != "fiscal_profile" and "fiscal" in t}


def test_the_previous_systems_values_are_the_defaults(db_session: Session) -> None:
    profile = _seed(db_session).get(ADMIN)
    assert profile.profitability_coefficient == Decimal("67.00")
    assert profile.substitute_tax_rate == Decimal("5.00")
    assert profile.social_security_rate == Decimal("26.07")


def test_a_supplied_rate_is_stored_as_a_percentage(db_session: Session) -> None:
    """`26.07`, not `0.2607`. The conversion happens once, where the tax is computed;
    the column holds the number the way its owner reads and types it."""
    service = _seed(db_session)
    profile = service.upsert(
        FiscalProfileUpsert(regime_code="RF19", social_security_rate=Decimal("25.72")), ADMIN
    )
    assert profile.social_security_rate == Decimal("25.72")
    stored = db_session.execute(
        text("SELECT social_security_rate FROM fiscal_profile WHERE id = :id"), {"id": profile.id}
    ).scalar_one()
    assert stored == Decimal("25.72")


def test_changing_one_writes_an_activity(db_session: Session) -> None:
    """R5 closes for this table, and not for hygiene: the timeline is what reconstructs
    when a fiscal parameter changed, which is the same reason slice 3 §7.1 refused to
    historicise the profile at all."""
    service = _seed(db_session)
    before = service.get(ADMIN)
    service.upsert(
        FiscalProfileUpsert(regime_code="RF19", social_security_rate=Decimal("25.00")), ADMIN
    )
    entries = ActivityService(db_session).timeline("fiscal_profile", before.id)
    assert entries[0].kind == "updated"
    assert "social_security_rate" in str(entries[0].payload)


def test_an_out_of_range_rate_is_refused_before_the_database(db_session: Session) -> None:
    """`ge`/`le` because a rate is a percentage and `Numeric(5, 2)` would happily accept
    `999.99`; `decimal_places` because `1.005` would otherwise be rounded by Postgres
    into a value the caller never asked for."""
    for bad in (Decimal("-1.00"), Decimal("100.01"), Decimal("1.005")):
        with pytest.raises(ValidationError):
            FiscalProfileUpsert(regime_code="RF19", social_security_rate=bad)
