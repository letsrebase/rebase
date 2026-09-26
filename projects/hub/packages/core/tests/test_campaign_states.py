"""Journey states (spec § 2) and the settings campaigns read."""

from decimal import Decimal
from pathlib import Path

import pytest
from campaign_fixtures import clean, company, emails, lead, person  # noqa: F401  (fixture)
from sqlalchemy.orm import Session

from rebase_core.campaigns.states import candidates_for_state, card_state, display_name
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed

HUB = Path(__file__).resolve().parents[3]


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
        ((4, Decimal("1"), "Dev", "remoto"), "completo"),
        ((None, Decimal("1"), "Dev", "remoto"), "manca_cv"),
        ((4, Decimal("1"), None, "remoto"), "scheda"),  # position missing: not «manca_cv»
        ((None, None, "Dev", "remoto"), "scheda"),
        ((None, None, None, None), "scheda"),
    ],
)
def test_card_state_reads_the_four_fields_is_complete_reads(
    fields: tuple[object, object, object, object], expected: str
) -> None:
    assert card_state(*fields) == expected


def test_each_card_lands_in_exactly_one_state(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "nopos@studio.it", posizione=False)
    person(clean, "empty-in@studio.it", cv=False, tariffa=False, logins=1)
    person(clean, "empty-new@studio.it", cv=False, tariffa=False)
    person(clean, "gone@studio.it", cv=False, deleted=True)
    assert emails(candidates_for_state(clean, "completo")) == ["done@studio.it"]
    assert emails(candidates_for_state(clean, "manca_cv")) == ["nocv@studio.it"]
    assert sorted(emails(candidates_for_state(clean, "scheda_vuota_nuovi"))) == [
        "empty-new@studio.it",
        "nopos@studio.it",
    ]
    assert emails(candidates_for_state(clean, "scheda_vuota_entrati")) == ["empty-in@studio.it"]


def test_a_lead_whose_address_has_a_card_is_not_a_lead_in_any_case(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "ada@studio.it")
    lead(clean, "ADA@Studio.it")
    lead(clean, "giulia@studio.it")
    found = candidates_for_state(clean, "lead")
    assert emails(found) == ["giulia@studio.it"]
    assert found[0].tipo == "lead" and found[0].nome == "Giulia"


def test_a_referente_with_two_open_requests_is_one_row(clean: Session) -> None:  # noqa: F811  (fixture)
    first = company(clean, "info@block-buy.it")
    second = company(clean, "info@block-buy.it", stato="in_corso")
    company(clean, "closed@acme.it", stato="chiuso")
    company(clean, "deleted@acme.it", deleted=True)
    found = candidates_for_state(clean, "azienda_aperta")
    assert emails(found) == ["info@block-buy.it"]
    assert set(found[0].company_ids) == {first.id, second.id}
    assert found[0].tipo == "azienda"


def test_the_pigro_state_waits_for_phase_three(clean: Session) -> None:  # noqa: F811  (fixture)
    with pytest.raises(ValidationFailed, match="fase Pigro"):
        candidates_for_state(clean, "pigro_vuoto")


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
