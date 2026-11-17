"""Review follow-ups (REB-1127, REB-1134): the years backstop with a profile-only card, the cloud
summary's cap and guard, and migrations 0055 (an on switch survives) and 0056 (guard_hits)."""

from typing import Any

from alembic import command
from alembic.config import Config
from fakes_cards import CARD
from sqlalchemy import create_engine, text

from rebase_core.card_draft import CardDraft
from rebase_core.card_provenance import provenance
from rebase_core.hub_facts import HubFacts
from rebase_core.migrate import INI_PATH, upgrade_to_head
from rebase_core.sources import SourceSet
from rebase_core.work_profiles.view import SUMMARY_CLOUD_MAX, cloud_work_profile


def test_years_are_dropped_when_only_the_work_profile_stood_beside_the_cv() -> None:
    fields = dict.fromkeys(
        (
            "role",
            "experience_years",
            "seniority",
            "skills",
            "industries",
            "languages",
            "location",
            "profile_summary",
        ),
        ["work_profile"],
    )
    person = dict.fromkeys(
        ("first_name", "last_name", "email", "phone", "city", "linkedin_url"), None
    )
    draft = CardDraft.model_validate(
        {
            "card": {**CARD, "experience_years": 7},
            "sources": fields,
            "employers": [],
            "person": {**person, "other_links": []},
        }
    )
    facts = HubFacts(None, None, work_profile="FONTE: profilo di lavoro\nx")

    card, field_sources, found = provenance(draft, SourceSet([]), facts)

    assert card.experience_years is None and field_sources["experience_years"] == []
    assert found[0].kind == "discarded_experience_years"


def test_the_cloud_summary_is_cut_at_a_word_and_a_flagged_field_is_left_out() -> None:
    long = " ".join(["parola"] * 300)
    shown = cloud_work_profile({"summary": long}).summary
    assert shown is not None and len(shown) <= SUMMARY_CLOUD_MAX + 1 and shown.endswith("…")
    assert "parol…" not in shown
    payload = {"summary": "Initech", "domains": [{"label": "Fintech"}], "projects": []}
    assert cloud_work_profile(payload, {"summary"}).summary is None
    assert [d.label for d in cloud_work_profile(payload, {"summary"}).domains] == ["Fintech"]
    assert cloud_work_profile(payload, {"domains[0].label"}).domains == []


def test_an_on_switch_survives_migration_0055_and_0056_adds_guard_hits(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0054")
        with engine.begin() as connection:
            connection.execute(text("UPDATE ai_sources SET enabled = true WHERE kind = 'github'"))
        command.upgrade(config, "0055")
        with engine.connect() as connection:
            rows = dict(connection.execute(text("SELECT kind, enabled FROM ai_sources")).all())
        assert rows == {"github": True, "links": False, "work_profile": False}
        columns = text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'work_profiles' AND column_name = 'guard_hits'"
        )
        command.downgrade(config, "0055")
        with engine.connect() as connection:
            assert connection.execute(columns).all() == []
        command.upgrade(config, "0056")
        with engine.connect() as connection:
            assert connection.execute(columns).all() == [("guard_hits",)]
        engine.dispose()
