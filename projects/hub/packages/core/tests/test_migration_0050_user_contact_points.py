"""0050 (REB-943): `users.phone` becomes a primary `work` phone point, the column goes, and the
downgrade gives each person's primary phone back."""

from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from rebase_core.migrate import INI_PATH

REVISION = "0050"
PARENT = "0049"


def _config(url: str) -> Config:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _user(connection: Any, email: str, phone: str | None) -> Any:
    return connection.execute(
        text(
            "INSERT INTO users (id, email, first_name, last_name, role, active, phone) "
            "VALUES (gen_random_uuid(), :email, 'A', 'B', 'member', true, :phone) RETURNING id"
        ),
        {"email": email, "phone": phone},
    ).scalar_one()


def test_revision_follows_the_english_names() -> None:
    parent = ScriptDirectory.from_config(Config(str(INI_PATH))).get_revision(REVISION).down_revision
    assert parent == PARENT


def test_the_phone_moves_into_a_primary_work_point_and_the_column_is_dropped(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), PARENT)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            with_phone = _user(connection, "uno@studio.it", "  +39 333 1234567 ")
            _user(connection, "due@studio.it", None)
            _user(connection, "tre@studio.it", "   ")
        command.upgrade(_config(url), REVISION)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT user_id, kind, value, label, note, is_primary, position "
                    "FROM user_contact_points"
                )
            ).all()
            columns = {
                name
                for (name,) in connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'users'"
                    )
                )
            }
        assert rows == [(with_phone, "phone", "+39 333 1234567", "work", None, True, 0)]
        assert "phone" not in columns
        engine.dispose()


def test_the_database_holds_the_rules_of_the_list(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), REVISION)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            owner = _user_at_head(connection, "uno@studio.it")
            other = _user_at_head(connection, "due@studio.it")
            _point(connection, owner, "phone", "+39 333 123 4567", primary=True)

        def refused(
            user_id: Any, kind: str, value: str, *, primary: bool = False, **kw: Any
        ) -> None:
            with pytest.raises(IntegrityError), engine.begin() as connection:
                _point(connection, user_id, kind, value, primary=primary, **kw)

        refused(owner, "phone", "0039 333 111", primary=True)  # a second primary
        refused(owner, "phone", "39 (333) 123-4567")  # the same digits
        with engine.begin() as connection:
            _point(connection, owner, "email", "Extra@Y.it")
        refused(owner, "email", "extra@y.IT")  # the same address, same person
        refused(other, "email", "EXTRA@y.it")  # the same address, another person
        refused(owner, "fax", "123")  # outside the kinds
        refused(owner, "phone", "123456", label="casa")  # outside the labels
        with engine.begin() as connection:
            _point(connection, other, "phone", "+39 333 123 4567", primary=True)  # not shared
            connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner})
            left = connection.execute(
                text("SELECT count(*) FROM user_contact_points WHERE user_id = :id"),
                {"id": owner},
            ).scalar_one()
        assert left == 0
        engine.dispose()


def _user_at_head(connection: Any, email: str) -> Any:
    return connection.execute(
        text(
            "INSERT INTO users (id, email, first_name, last_name, role, active) "
            "VALUES (gen_random_uuid(), :email, 'A', 'B', 'member', true) RETURNING id"
        ),
        {"email": email},
    ).scalar_one()


def _point(
    connection: Any,
    user_id: Any,
    kind: str,
    value: str,
    *,
    primary: bool = False,
    label: str = "work",
) -> None:
    connection.execute(
        text(
            "INSERT INTO user_contact_points (id, user_id, kind, value, label, is_primary) "
            "VALUES (gen_random_uuid(), :user_id, :kind, :value, :label, :primary)"
        ),
        {"user_id": user_id, "kind": kind, "value": value, "label": label, "primary": primary},
    )


def test_the_downgrade_gives_each_persons_primary_phone_back(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), REVISION)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            both = _user_at_head(connection, "uno@studio.it")
            _point(connection, both, "phone", "02 5550199", primary=False, label="billing")
            _point(connection, both, "phone", "+39 333 123 4567", primary=True)
            _point(connection, both, "email", "extra@y.it")
            none = _user_at_head(connection, "due@studio.it")
            _point(connection, none, "email", "altra@y.it")
        command.downgrade(_config(url), PARENT)
        with engine.connect() as connection:
            phones = dict(connection.execute(text("SELECT id, phone FROM users")).all())
            has_table = connection.execute(
                text("SELECT to_regclass('user_contact_points')")
            ).scalar_one()
        assert phones == {both: "+39 333 123 4567", none: None}
        assert has_table is None
        engine.dispose()
