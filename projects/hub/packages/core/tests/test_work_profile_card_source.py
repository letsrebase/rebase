"""The approved work profile as a source of the anonymous card (REB-1134, spec 2026-11-17 § 6):
read like the hub's facts, with its own key and hash, only while its switch is on and only for an
approved profile that has not expired; an approval, a replacement, a deletion and the 12 months'
end all make the card stale; the anonymity guard keeps the person out of it; and the live checks
count it as a text a skill may be found in."""

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, card_response
from freelancer_fixtures import card_writer, enable_sources, written_cards
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from test_hub_facts import CLIENT, _match, _person
from work_profile_helpers import START, sample

from rebase_core.card_draft import WORK_PROFILE
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.card_live_checks import card_flags
from rebase_core.card_prompt import card_prompt, source_blocks, source_keys
from rebase_core.card_provenance import hash_inputs
from rebase_core.card_writer import CardWriter
from rebase_core.cv_text import CvText
from rebase_core.hub_facts import HubFacts, hub_facts, hub_facts_many
from rebase_core.llm import RecordingCall
from rebase_core.models import Freelancer, WorkProfile
from rebase_core.sources import SourceSet
from rebase_core.team_schemas import Card
from rebase_core.work_profiles.source import source_text

NOW = START


@pytest.fixture
def clean(clean_hub: Session) -> Iterator[Session]:
    yield clean_hub


def _profile(
    session: Session,
    person: UUID,
    *,
    state: str = "approved",
    expires: timedelta = timedelta(days=300),
    summary: str | None = None,
) -> None:
    payload = {key: value for key, value in sample().items() if key != "contact"}
    if summary is not None:
        payload["summary"] = summary
    session.add(
        WorkProfile(
            freelancer_id=person,
            payload=payload,
            groundedness=89,
            unsupported_anchors=[],
            state=state,
            submitted_at=NOW,
            expires_at=NOW + expires,
        )
    )
    session.commit()


def _hashes(session: Session, person: UUID) -> dict[str, str]:
    row = session.get_one(Freelancer, person)
    return hash_inputs(row.job_title, SourceSet([]), hub_facts(session, person))


def test_it_is_off_until_the_switch_is_on_and_then_only_an_approved_profile_counts(
    clean: Session,
) -> None:
    [person] = written_cards(clean, 1)
    _profile(clean, person)

    assert hub_facts(clean, person).work_profile is None  # approved, but the switch is off
    assert WORK_PROFILE not in _hashes(clean, person)

    enable_sources(clean, "work_profile")
    facts = hub_facts(clean, person)
    assert facts.work_profile is not None and WORK_PROFILE in _hashes(clean, person)
    assert hub_facts_many(clean, [person])[person].work_profile == facts.work_profile

    for state in ("draft", "rejected"):
        clean.execute(text("UPDATE work_profiles SET state = :s"), {"s": state})
        clean.commit()
        assert hub_facts(clean, person).work_profile is None, state


def test_it_reads_the_summary_domains_stack_and_agentic_narrative_and_no_date(
    clean: Session,
) -> None:
    [person] = written_cards(clean, 1)
    _profile(clean, person)
    enable_sources(clean, "work_profile")

    body = hub_facts(clean, person).work_profile
    assert body is not None

    payload = sample()
    assert body.startswith("FONTE: profilo di lavoro")
    assert payload["summary"] in body
    assert payload["projects"][0]["domain"] in body and "Supabase/Postgres" in body
    assert payload["agenticLiteracy"]["narrative"] in body
    for private in ("2026-04", "2026-05", "2701", "cognitive", "orchestrator,"):
        assert private not in body, private
    assert source_text({"unknown": 1}, KnownIdentifiers.empty("Rossi")) is None


def test_the_prompt_carries_it_as_a_source_of_its_own_beside_the_hubs_facts(
    clean: Session,
) -> None:
    [person] = written_cards(clean, 1)
    _profile(clean, person)
    enable_sources(clean, "work_profile")
    facts = hub_facts(clean, person)

    assert source_keys(SourceSet([]), facts) == ["cv", WORK_PROFILE]
    [block] = source_blocks(SourceSet([]), facts)
    assert block.startswith(f'<source key="{WORK_PROFILE}">')
    request = card_prompt(
        CvText(filename="cv.pdf", pages=1, text="Un CV.", truncated=False),
        "Backend",
        SourceSet([]),
        facts,
    )
    assert "Sources available: cv, work_profile" in request.messages[0].text
    # The hub's own facts still read as before: the profile is not part of their hash.
    assert hub_facts(clean, person).hash_material() == ""
    assert HubFacts(None, None, work_profile="x").empty


def test_approving_replacing_deleting_and_expiring_each_make_the_card_stale(
    clean: Session,
) -> None:
    [person] = written_cards(clean, 1)
    enable_sources(clean, "work_profile")
    writer = CardWriter(clean, None)
    assert writer.stale_of(person) is None  # no profile: nothing changed for the card

    _profile(clean, person, state="draft")
    assert writer.stale_of(person) is None  # a draft is never read

    clean.execute(text("UPDATE work_profiles SET state = 'approved'"))
    clean.commit()
    assert writer.stale_of(person) == "source"  # approval

    # The card is written again from it: now it matches.
    from fakes_cards import (
        Scripted,  # noqa: F401
    )

    card_writer(clean, RecordingCall([card_response()]), now=NOW).write(person, force=True)
    assert writer.stale_of(person) is None

    clean.execute(
        text("UPDATE work_profiles SET payload = jsonb_set(payload, '{summary}', '\"Altro.\"')")
    )
    clean.commit()
    assert writer.stale_of(person) == "source"  # replacement

    card_writer(clean, RecordingCall([card_response()]), now=NOW).write(person, force=True)
    clean.execute(text("DELETE FROM work_profiles"))
    clean.commit()
    assert writer.stale_of(person) == "source"  # deletion

    _profile(clean, person)
    card_writer(clean, RecordingCall([card_response()]), now=NOW).write(person, force=True)
    assert writer.stale_of(person) is None
    clean.execute(text("UPDATE work_profiles SET expires_at = now() - interval '1 day'"))
    clean.commit()
    assert writer.stale_of(person) == "source"  # expiry


def test_the_guard_keeps_the_person_and_their_clients_out_of_the_source(clean: Session) -> None:
    row = _person(clean)
    row.links = ["https://github.com/ada-l/x", "https://ada.studio"]
    clean.commit()
    person = row.id
    _profile(clean, person, summary="Works with Lovelace on payment systems, research-first.")
    enable_sources(clean, "work_profile")

    assert hub_facts(clean, person).work_profile is None  # the surname, as a whole word

    clean.execute(text("DELETE FROM work_profiles"))
    clean.commit()
    _profile(clean, person, summary="Builds sites for ada.studio and orchestrates agents well.")
    assert hub_facts(clean, person).work_profile is None  # the host of one of their links

    clean.execute(text("DELETE FROM work_profiles"))
    clean.commit()
    _match(clean, row)
    _profile(clean, person, summary=f"Shipped a billing rewrite for {CLIENT} with agents.")
    assert hub_facts(clean, person).work_profile is None  # a client the hub's engagements name

    clean.execute(text("DELETE FROM work_profiles"))
    clean.commit()
    _profile(clean, person)
    assert hub_facts(clean, person).work_profile is not None  # the ordinary profile passes


def test_a_skill_only_the_work_profile_names_is_not_flagged(clean: Session) -> None:
    card = Card.model_validate({**CARD, "skills": ["Supabase", "Cobol"]})
    plain = HubFacts(None, None)
    with_profile = HubFacts(
        None, None, work_profile="FONTE: profilo di lavoro\nStack: Supabase, Postgres"
    )
    known = KnownIdentifiers.empty("Rossi")

    def skills_flag(facts: HubFacts) -> str | None:
        flags = [
            flag
            for flag in card_flags(card, "Backend developer.", facts, known, date(2026, 11, 17))
            if flag.field == "skills"
        ]
        return flags[0].reason if flags else None

    assert "Supabase" in (skills_flag(plain) or "")
    reason = skills_flag(with_profile) or ""
    assert "Supabase" not in reason and "Cobol" in reason


def test_a_read_of_the_facts_costs_the_same_statements_with_the_switch_on_or_off(
    clean: Session,
) -> None:
    people = written_cards(clean, 3)
    for person in people:
        _profile(clean, person)

    def statements() -> int:
        seen: list[str] = []

        def record(*args: Any) -> None:
            seen.append(args[2])

        engine = clean.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            hub_facts_many(clean, people)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    off = statements()
    enable_sources(clean, "work_profile")
    assert statements() == off


def test_a_practices_block_does_not_change_the_cards_source_text() -> None:
    plain = sample()
    with_block = plain | {
        "practices": {
            "rubric": "rebase-practices/v1",
            "timezone": "UTC",
            "items": [
                {
                    "id": "checks_before_commit",
                    "method": "code",
                    "seen": 18,
                    "of": 25,
                    "products": 3,
                    "months": 4,
                }
            ],
        }
    }
    known = KnownIdentifiers.empty("Rossi")
    assert source_text(with_block, known) == source_text(plain, known)
