"""0061 (REB-1164): a freelancer card is `new` or `discarded`, nothing else."""

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0061"


def _config(url: str) -> Config:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _card(connection: Any, status: str) -> Any:
    user_id, card_id = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, first_name, last_name, active, role) "
            "VALUES (:id, :e, 'A', 'B', true, 'member')"
        ),
        {"id": user_id, "e": f"{user_id}@acme.it"},
    )
    connection.execute(
        text(
            "INSERT INTO freelancers (id, user_id, status, filled_by, links) "
            "VALUES (:id, :u, :s, 'person', '[]')"
        ),
        {"id": card_id, "u": user_id, "s": status},
    )
    return card_id, user_id


def _campaign(connection: Any, user_id: Any, filters: str) -> Any:
    campaign_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO campaigns (id, created_by, name, slug, source, filters, subject, text, "
            "button_text, button_meta, action, status, content_at) "
            "VALUES (:id, :u, 'n', :slug, 'filters', CAST(:f AS jsonb), 's', 't', 'b', "
            "'area', 'signed_in', 'draft', now())"
        ),
        {"id": campaign_id, "u": user_id, "slug": str(campaign_id), "f": filters},
    )
    return campaign_id


def test_revision_follows_the_unknown_practices_count() -> None:
    assert _parent(Config(str(INI_PATH))) == "0060"


def test_old_states_fold_into_new_and_stored_filters_follow(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        command.upgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as c:
            ids = {s: _card(c, s) for s in ("new", "contacted", "active", "discarded")}
            owner = ids["new"][1]
            stale = _campaign(c, owner, '{"list": "talents", "status": "contacted"}')
            kept = _campaign(c, owner, '{"list": "talents", "status": "discarded"}')
            company = _campaign(c, owner, '{"list": "companies", "status": "contacted"}')
        command.upgrade(config, REVISION)
        with engine.connect() as c:
            states = {
                s: c.execute(
                    text("SELECT status FROM freelancers WHERE id = :i"), {"i": ids[s][0]}
                ).scalar()
                for s in ids
            }
            assert states == {
                "new": "new",
                "contacted": "new",
                "active": "new",
                "discarded": "discarded",
            }

            def stored(campaign_id: Any) -> Any:
                return c.execute(
                    text("SELECT filters ->> 'status' FROM campaigns WHERE id = :i"),
                    {"i": campaign_id},
                ).scalar()

            assert stored(stale) == "new"
            assert stored(kept) == "discarded"
            assert stored(company) == "contacted"
        with pytest.raises(IntegrityError, match="ck_freelancers_status"), engine.begin() as c:
            _card(c, "contacted")
        command.downgrade(config, _parent(config))
        with engine.begin() as c:
            _card(c, "contacted")
        engine.dispose()


def test_metadata_matches_the_schema(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
