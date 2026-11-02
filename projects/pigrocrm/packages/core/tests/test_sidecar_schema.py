"""A side database has no migrations: a column added to its model must be reported at
boot, not discovered by the first query that names it (B-31)."""

import pytest
from sqlalchemy import Column, Engine, Integer, MetaData, String, Table, text

from pigrocrm.core.db.sidecar import SidecarSchemaOutdated, _assert_columns


def test_a_side_table_that_lacks_a_declared_column_is_reported_with_its_name(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        connection.execute(text("CREATE TABLE sidecar_probe (id integer primary key)"))
    try:
        current = MetaData()
        Table("sidecar_probe", current, Column("id", Integer, primary_key=True))
        _assert_columns(db_engine, current)

        newer = MetaData()
        Table(
            "sidecar_probe",
            newer,
            Column("id", Integer, primary_key=True),
            Column("added_later", String),
        )
        with pytest.raises(SidecarSchemaOutdated, match="sidecar_probe lacks added_later"):
            _assert_columns(db_engine, newer)
    finally:
        with db_engine.begin() as connection:
            connection.execute(text("DROP TABLE sidecar_probe"))
