"""The level on the anonymous card (REB-799): the bands the prompt states and the parser
enforces, the seniority the writer stores and marks as a guess, the admin's own level,
and the override that refuses a word the card cannot hold.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from admins import an_admin
from fakes_cards import CARD, card_response
from freelancer_fixtures import (
    SCAN,
    apply_freelancer,
    card_logs,
    card_schema,
    stored_card,
)
from sqlalchemy.orm import Session

from rebase_core.card_level import LEAD_FROM, MID_FROM, SENIOR_FROM
from rebase_core.card_prompt import card_prompt
from rebase_core.card_writer import CardWriter
from rebase_core.cv_text import CvText
from rebase_core.freelancer_schemas import FreelancerOverride
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import (
    RecordingCall,
)
from rebase_core.members import MemberService

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)


@pytest.fixture
def logs(clean_hub: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    return card_logs(caplog)


# ---- the level (REB-799) -------------------------------------------------------------


def test_the_prompt_states_the_bands_the_parser_enforces() -> None:
    """The numbers in the prompt are `card_level`'s, so the rule Claude is told and the
    rule the parser applies cannot drift apart; `anni` is asked from dated roles and may
    be null, and `lead` needs leadership as well as years."""
    cv = CvText(filename="cv.pdf", pagine=1, testo="Backend developer, Python.", troncato=False)
    system = card_prompt(cv, "Backend developer").system

    assert f"junior under {MID_FROM} years" in system
    assert f"mid from {MID_FROM} to {SENIOR_FROM - 1}" in system
    assert f"senior from {SENIOR_FROM} up" in system
    assert f"senior from {LEAD_FROM} years up when the CV states" in system
    assert f"never has fewer than {LEAD_FROM} years" in system
    assert "counted only from roles the CV dates" in system
    assert "gaps between roles left out, not the span from the first start" in system
    assert "null, never a guess, when the CV dates no professional role at all" in system
    assert "When anni is null, give seniority as your reading of the responsibility" in system
    assert system.index("- anni:") < system.index("- seniority:")


def test_the_schema_lets_the_model_say_the_years_are_unknown() -> None:
    """`anni` is required (a key silently missing is a shape failure) and nullable, and
    comes before `seniority`, which is read from it."""
    schema = card_schema()
    properties = schema["properties"]
    assert "anni" in schema["required"]
    assert schema["additionalProperties"] is False
    assert {"type": "null"} in properties["luogo"]["anyOf"]
    assert {"type": "null"} in properties["anni"]["anyOf"]
    assert list(properties).index("anni") < list(properties).index("seniority")


@pytest.mark.parametrize(
    ("answer", "seniority", "anni", "incerta"),
    [
        # The audit's inconsistent cards, as the parser stores them now.
        ({"seniority": "lead", "anni": 3}, "mid", 3, False),
        ({"seniority": "senior", "anni": 3}, "mid", 3, False),
        ({"seniority": "mid", "anni": 7}, "senior", 7, False),
        ({"seniority": "lead", "anni": 12}, "lead", 12, False),
        # A CV that dates nothing: years unknown, the seniority a guess, never lead.
        ({"seniority": "junior", "anni": None}, "junior", None, True),
        ({"seniority": "lead", "anni": None}, "senior", None, True),
    ],
)
def test_the_writer_stores_the_banded_seniority_and_marks_a_guess(
    clean_hub: Session, answer: dict[str, Any], seniority: str, anni: int | None, incerta: bool
) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([card_response({**CARD, **answer})])

    read = CardWriter(clean_hub, llm).write(freelancer_id)

    assert read.error is None and read.card is not None
    assert (read.card.seniority, read.card.anni, read.card.seniority_incerta) == (
        seniority,
        anni,
        incerta,
    )
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.card is not None
    assert (stored.card["seniority"], stored.card["anni"]) == (seniority, anni)


def test_an_admins_level_beats_the_card_and_survives_a_regenerate(clean_hub: Session) -> None:
    """The admin's seniority and years sit on the freelancer, not on the card: a
    regenerate that writes a different card, a card retired by a new CV and one that
    fails all leave them, and taking them off shows Claude's reading again."""
    freelancer_id = apply_freelancer(clean_hub)
    admin = an_admin(clean_hub)
    llm = RecordingCall(
        [
            card_response({**CARD, "seniority": "junior", "anni": 2}),
            card_response({**CARD, "seniority": "mid", "anni": 4}),
        ]
    )
    writer = CardWriter(clean_hub, llm)
    assert writer.write(freelancer_id).card is not None

    FreelancerService(clean_hub).override(
        freelancer_id, FreelancerOverride(seniority_admin="lead", anni_admin=15), admin
    )

    read = writer.read(freelancer_id)
    assert read.card is not None
    assert (read.card.seniority, read.card.anni) == ("lead", 15)
    assert (read.seniority_admin, read.anni_admin) == ("lead", 15)
    assert read.card.seniority_incerta is False
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.card is not None
    assert (stored.card["seniority"], stored.card["anni"]) == ("junior", 2)  # Claude's, untouched

    regenerated = writer.write(freelancer_id, force=True)
    assert regenerated.card is not None
    assert (regenerated.card.seniority, regenerated.card.anni) == ("lead", 15)
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.card is not None
    assert (stored.card["seniority"], stored.card["anni"]) == ("mid", 4)

    # A CV replaced by a scan retires the card, and the correction is still on the row.
    MemberService(clean_hub).replace_cv(freelancer_id, SCAN, "scansione.pdf", "application/pdf")
    retired = CardWriter(clean_hub, RecordingCall([])).write(freelancer_id)
    assert retired.card is None
    assert (retired.seniority_admin, retired.anni_admin) == ("lead", 15)

    FreelancerService(clean_hub).override(
        freelancer_id, FreelancerOverride(seniority_admin=None, anni_admin=None), admin
    )
    assert writer.read(freelancer_id).seniority_admin is None


def test_a_year_alone_from_the_admin_leaves_an_undated_seniority_uncertain(
    clean_hub: Session,
) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    admin = an_admin(clean_hub)
    writer = CardWriter(clean_hub, RecordingCall([card_response({**CARD, "anni": None})]))
    writer.write(freelancer_id)

    FreelancerService(clean_hub).override(freelancer_id, FreelancerOverride(anni_admin=6), admin)
    read = writer.read(freelancer_id)
    assert read.card is not None
    assert (read.card.anni, read.card.seniority_incerta) == (6, True)

    FreelancerService(clean_hub).override(
        freelancer_id, FreelancerOverride(seniority_admin="senior"), admin
    )
    read = writer.read(freelancer_id)
    assert read.card is not None
    assert (read.card.seniority, read.card.seniority_incerta) == ("senior", False)


@pytest.mark.parametrize(
    "bad", [{"seniority_admin": "guru"}, {"anni_admin": -1}, {"anni_admin": 61}], ids=str
)
def test_an_override_refuses_a_level_outside_the_cards_words(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="seniority_admin|anni_admin"):
        FreelancerOverride(**bad)
