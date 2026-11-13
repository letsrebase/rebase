"""0052 (REB-1106): `referral_rewards.document_id` becomes `engagement_id`, a foreign key to the
hub's read model of the CRM's engagements. Production has no reward row; a preview with test rows
keeps each one linked: the migration writes the engagement of the match its letter belonged to,
keyed by the match's id, before it drops the old column. It can run again, and it rolls back only
when no reward would be lost."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text

from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def _scalar(connection: Connection, sql: str, **params: object) -> str:
    return str(connection.execute(text(sql), params).scalar_one())


def test_migration_0052_keeps_a_test_reward_linked_to_the_engagement_of_its_match(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0051")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE "
                        "table_name = 'referral_rewards' AND column_name = 'document_id'"
                    )
                ).scalar_one()
                == 1
            )
            admin = _scalar(
                connection,
                "INSERT INTO users (id, email, first_name, last_name, role, active, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'ivan@rebase.it', 'I', 'V', 'admin', "
                "true, now(), now()) RETURNING id",
            )
            card = _scalar(
                connection,
                "INSERT INTO freelancers (id, user_id, status, links, filled_by, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :u, 'new', '[]', 'person', now(), now()) "
                "RETURNING id",
                u=admin,
            )
            company = _scalar(
                connection,
                "INSERT INTO companies (id, user_id, company_name, requested_role, project, "
                "period_from, duration, daily_budget, remote, headcount, status, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :u, 'Acme', 'Dev', 'P', '2026-10-01', "
                "'3 mesi', 800, 'remote', 1, 'new', now(), now()) RETURNING id",
                u=admin,
            )
            match = _scalar(
                connection,
                "INSERT INTO matches (id, freelancer_id, company_id, customer_legal_name, "
                "customer_vat_number, customer_registered_office, created_by, status, "
                "letter_start_date, letter_end_date, letter_fee, expected_days, "
                "company_daily_budget, created_at, updated_at) VALUES (gen_random_uuid(), :f, "
                ":c, 'Acme S.r.l.', '01234567890', 'Milano', :a, 'active', '2026-10-01', "
                "'2026-12-31', 450, 20, 800, now(), now()) RETURNING id",
                f=card,
                c=company,
                a=admin,
            )
            letter = _scalar(
                connection,
                "INSERT INTO contract_documents (id, match_id, kind, freelancer_id, "
                "text_version, draft_text, data, number, pdf, status, signed_at, created_by, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :m, 'letter', :f, 'v1', "
                "false, '{\"modalita\": \"a giornata\"}', '2026-001', '\\x00', 'signed', "
                "'2026-10-02 10:00+00', :a, now(), now()) RETURNING id",
                m=match,
                f=card,
                a=admin,
            )
            referral = _scalar(
                connection,
                "INSERT INTO referrals (id, referrer_user_id, kind, entity_id, code, status, "
                "verified_at, verified_via, created_at, updated_at) VALUES (gen_random_uuid(), "
                ":r, 'freelancer', :e, 'ABCDEF2345', 'verified', now(), 'letter', now(), now()) "
                "RETURNING id",
                r=admin,
                e=card,
            )
            connection.execute(
                text(
                    "INSERT INTO referral_rewards (id, referral_id, document_id, rate, "
                    "base_amount, reward_amount, status, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), :r, :d, 0.1, 7000, 700, 'to_confirm', now(), now())"
                ),
                {"r": referral, "d": letter},
            )

        for _ in range(2):  # a retried deploy runs the revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                reward = connection.execute(
                    text("SELECT engagement_id, reward_amount FROM referral_rewards")
                ).one()
                assert str(reward.engagement_id) == match
                engagement = connection.execute(
                    text("SELECT * FROM hub_engagements WHERE id = :m"),
                    {"m": match},
                ).one()
                assert (
                    str(engagement.freelancer_id),
                    str(engagement.company_id),
                    str(engagement.letter_id),
                    engagement.letter_number,
                    engagement.mode,
                    float(engagement.fee),
                    float(engagement.expected_days),
                    float(engagement.company_daily_budget),
                    engagement.state,
                    engagement.sequence,
                    str(engagement.ends_on),
                    str(engagement.planned_ends_on),
                ) == (
                    card,
                    company,
                    letter,
                    "2026-001",
                    "day",
                    450.0,
                    20.0,
                    800.0,
                    "active",
                    0,
                    "2026-12-31",
                    "2026-12-31",
                )
                assert engagement.signed_at is not None
                assert engagement.crm_engagement_id is None
                diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
                assert diff == [], diff
        # A rollback never deletes a reward: it refuses while one exists, and changes nothing.
        with pytest.raises(RuntimeError, match="referral_rewards holds 1 row"):
            command.downgrade(config, "0051")
        with engine.begin() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM referral_rewards")).scalar_one() == 1
            )
            assert _scalar(connection, "SELECT version_num FROM alembic_version") == head_revision()
            connection.execute(text("DELETE FROM referral_rewards"))
        command.downgrade(config, "0051")
        with engine.begin() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns WHERE "
                        "table_name = 'referral_rewards' AND column_name = 'document_id'"
                    )
                ).scalar_one()
                == 1
            )
        command.upgrade(config, "head")
        with engine.begin() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
