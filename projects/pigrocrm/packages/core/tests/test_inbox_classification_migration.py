"""REB-972: the revision that lets a proposal read from the inbox, over a proposal planted before
it, both ways. Its own file so the revision's number, which moves when the branch is rebased,
is looked up and never typed."""

from pathlib import Path
from typing import Any

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

CORE_ROOT = Path(__file__).resolve().parents[1]
ENTITY = "00000000-0000-7000-8000-0000000000e1"
ORGANIZATION = "00000000-0000-7000-8000-0000000000c1"
DOCUMENT = "00000000-0000-7000-8000-0000000000d1"
PROPOSAL = "00000000-0000-7000-8000-0000000000f1"
ITEM = "00000000-0000-7000-8000-0000000000a1"


def _config(url: str) -> Config:
    config = Config(str(CORE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _revisions(config: Config) -> tuple[str, str]:
    script = ScriptDirectory.from_config(config)
    (this,) = [
        r
        for r in script.walk_revisions()
        if r.path is not None and r.path.endswith("_inbox_classification.py")
    ]
    assert isinstance(this.down_revision, str)
    return this.revision, this.down_revision


def _scalar(engine: Engine, sql: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql)).scalar_one()


def _constraints(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT conname FROM pg_constraint WHERE conrelid = 'proposals'::regclass")
            ).scalars()
        )


INBOX_PROPOSAL = (
    "INSERT INTO proposals (id, inbox_item_id, target_type, proposed_fields, excerpt, "
    "excerpt_type, confidence, status) VALUES (gen_random_uuid(), '{item}', 'receipt', '{{}}', "
    "'x', 'quoted', 1, 'pending')"
)


def test_the_revision_keeps_a_planted_proposal_widens_the_checks_and_goes_back(
    pigrocrm_postgres: Any,
) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        revision, before = _revisions(config)
        upgrade(config, before)
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{ENTITY}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, country, legal_entity_id, "
                    f"custom_fields) VALUES ('{ORGANIZATION}', 'Acme', 'IT', '{ENTITY}', '{{}}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO documents (id, type, title, customer_id, legal_entity_id, "
                    "current_version, custom_fields) VALUES "
                    f"('{DOCUMENT}', 'contract', 'Contratto', '{ORGANIZATION}', "
                    f"'{ENTITY}', 1, '{{}}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO proposals (id, document_id, target_type, proposed_fields, "
                    "excerpt, excerpt_type, confidence, status) "
                    f"VALUES ('{PROPOSAL}', '{DOCUMENT}', 'contract', '{{}}', 'x', 'quoted', 1, "
                    "'pending')"
                )
            )

        upgrade(config, revision)
        # The planted proposal is untouched, and the new columns are empty on it.
        assert (
            _scalar(engine, f"SELECT document_id::text FROM proposals WHERE id = '{PROPOSAL}'")
            == DOCUMENT
        )
        assert _scalar(
            engine, "SELECT inbox_item_id IS NULL AND owner_user_id IS NULL FROM proposals"
        )
        names = _constraints(engine)
        assert {"ck_proposals_source", "ck_proposals_contract_id_required_for_work_unit"} <= names
        assert "ck_proposals_contract_id_required_unless_contract" not in names

        # An inbox proposal needs an item, and no document; a contract needs a document.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO inbox_items (id, source, original_name, file_type, size_bytes, "
                    "sha256, content_sha256, state) VALUES "
                    f"('{ITEM}', 'upload', 'a.pdf', 'pdf', 1, 'a', 'a', 'proposed')"
                )
            )
            connection.execute(text(INBOX_PROPOSAL.format(item=ITEM)))
        for bad in (
            INBOX_PROPOSAL.replace("'{item}'", "NULL").format(),
            INBOX_PROPOSAL.replace("'receipt'", "'contract'").format(item=ITEM),
            INBOX_PROPOSAL.format(item=ITEM),  # a second one pending for the same item
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(bad))

        downgrade(config, before)
        # The proposal read from the inbox goes (it has no document to keep); the old one stays.
        assert _scalar(engine, "SELECT count(*) FROM proposals") == 1
        # The item it was read for waits to be read again, not stranded as `proposed`.
        assert _scalar(engine, f"SELECT state FROM inbox_items WHERE id = '{ITEM}'") == "new"
        assert (
            _scalar(
                engine,
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'proposals' AND column_name = 'document_id'",
            )
            == "NO"
        )
        assert "ck_proposals_contract_id_required_unless_contract" in _constraints(engine)
        assert not _scalar(engine, "SELECT to_regclass('ai_model_config') IS NOT NULL")
        upgrade(config, revision)  # and the way up again works over what is left
        engine.dispose()
