"""Journey states (spec § 2) and the settings campaigns read."""

from decimal import Decimal
from itertools import product
from pathlib import Path

import pytest
from campaign_fixtures import clean, company, emails, lead, person  # noqa: F401  (fixture)
from sqlalchemy.orm import Session

from rebase_core.campaigns.states import candidates_for_state, card_state, display_name
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed

HUB = Path(__file__).resolve().parents[3]
CARD_STATES = ("complete", "missing_cv", "card_empty_new", "card_empty_signed_in")


def test_the_campaign_settings_have_their_defaults_and_reach_the_container() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.campaign_from == "Ivan di rebase <ciao@letsrebase.com>"
    assert settings.campaign_gap_days == 3
    assert settings.resend_webhook_secret == ""
    compose = (HUB / "docker-compose.yml").read_text(encoding="utf-8")
    example = (HUB / ".env.example").read_text(encoding="utf-8")
    names = (
        "REBASE_CAMPAIGN_FROM",
        "REBASE_CAMPAIGN_GAP_DAYS",
        "REBASE_RESEND_WEBHOOK_SECRET",
    )
    for name in names:
        assert f"{name}: ${{{name}" in compose, name
        assert name in example, name


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((4, Decimal("1"), "Dev", "remote"), "complete"),
        ((None, Decimal("1"), "Dev", "remote"), "missing_cv"),
        ((4, Decimal("1"), None, "remote"), "card"),  # position missing: not «manca_cv»
        ((None, None, "Dev", "remote"), "card"),
        ((None, None, None, None), "card"),
    ],
)
def test_card_state_reads_the_four_fields_is_complete_reads(
    fields: tuple[object, object, object, object], expected: str
) -> None:
    assert card_state(*fields) == expected


def test_each_card_lands_in_exactly_one_state(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "nopos@studio.it", job_title=False)
    person(clean, "empty-in@studio.it", cv=False, rate=False, logins=1)
    person(clean, "empty-new@studio.it", cv=False, rate=False)
    person(clean, "gone@studio.it", cv=False, deleted=True)
    assert emails(candidates_for_state(clean, "complete")) == ["done@studio.it"]
    assert emails(candidates_for_state(clean, "missing_cv")) == ["nocv@studio.it"]
    assert sorted(emails(candidates_for_state(clean, "card_empty_new"))) == [
        "empty-new@studio.it",
        "nopos@studio.it",
    ]
    assert emails(candidates_for_state(clean, "card_empty_signed_in")) == ["empty-in@studio.it"]


def test_a_lead_whose_address_has_a_card_is_not_a_lead_in_any_case(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "ada@studio.it")
    lead(clean, "ADA@Studio.it")
    lead(clean, "giulia@studio.it")
    found = candidates_for_state(clean, "lead")
    assert emails(found) == ["giulia@studio.it"]
    assert found[0].kind == "lead" and found[0].name == "Giulia"


def test_to_contact_person_with_two_open_requests_is_one_row(clean: Session) -> None:  # noqa: F811  (fixture)
    first = company(clean, "info@block-buy.it")
    second = company(clean, "info@block-buy.it", status="in_progress")
    company(clean, "closed@acme.it", status="closed")
    company(clean, "deleted@acme.it", deleted=True)
    found = candidates_for_state(clean, "company_open")
    assert emails(found) == ["info@block-buy.it"]
    assert set(found[0].company_ids) == {first.id, second.id}
    assert found[0].kind == "company"


def test_the_pigro_state_waits_for_phase_three(clean: Session) -> None:  # noqa: F811  (fixture)
    with pytest.raises(ValidationFailed, match="fase Pigro"):
        candidates_for_state(clean, "pigro_empty")


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("giulia", "Giulia"),
        ("STEFANIA", "Stefania"),
        ("Mauro Leonardo", "Mauro Leonardo"),
        ("  ", None),
        (None, None),
    ],
)
def test_display_name_fixes_only_all_lower_or_all_upper(raw: str | None, shown: str | None) -> None:
    assert display_name(raw) == shown


def test_the_sql_states_agree_with_card_state_for_every_combination(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """`_cards` filters in SQL what `card_state` decides in Python: the same state for
    every card whether the CV, the rate, the position and the work mode are there or not,
    and whether the person has logged in."""
    expected: dict[str, list[str]] = {state: [] for state in CARD_STATES}
    for n, (cv, rate, job_title, remote, entered) in enumerate(product((True, False), repeat=5)):
        email = f"c{n}@studio.it"
        person(
            clean,
            email,
            cv=cv,
            rate=rate,
            job_title=job_title,
            remote=remote,
            logins=1 if entered else 0,
        )
        state = card_state(
            4 if cv else None,
            Decimal("450") if rate else None,
            "Dev" if job_title else None,
            "remote" if remote else None,
        )
        key = (
            state if state != "card" else ("card_empty_signed_in" if entered else "card_empty_new")
        )
        expected[key].append(email)
    for state, wanted in expected.items():
        assert sorted(emails(candidates_for_state(clean, state))) == sorted(wanted), state
