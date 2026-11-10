"""The compose file's own defaults must pass the settings' budget checks.

On 2026-11-10 hub-v0.92.0 failed to start in production and on the preview: the compose
file defaulted the public mail budget to 15 and the reserve to 20, which the new rule
(public + confirmation + room for test mails within the reserve, REB-911) refuses, and
neither host set those variables. A deploy that only uses defaults has to boot.
"""

import re
from pathlib import Path

import pytest

from rebase_core.config import Settings

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
BUDGET_KEYS = (
    "REBASE_RESEND_DAILY_QUOTA",
    "REBASE_CAMPAIGN_DAILY_RESERVE",
    "REBASE_PUBLIC_MAIL_DAILY_BUDGET",
    "REBASE_CONFIRMATION_MAIL_DAILY_BUDGET",
)


def _compose_defaults() -> dict[str, str]:
    text = COMPOSE.read_text()
    found = dict(re.findall(r"\$\{(REBASE_[A-Z_]+):-([0-9]+)\}", text))
    return {key: value for key, value in found.items() if key in BUDGET_KEYS}


def test_the_compose_file_sets_the_reserve_and_the_public_budget() -> None:
    defaults = _compose_defaults()
    assert "REBASE_CAMPAIGN_DAILY_RESERVE" in defaults
    assert "REBASE_PUBLIC_MAIL_DAILY_BUDGET" in defaults


def test_the_compose_defaults_pass_the_budget_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in BUDGET_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in _compose_defaults().items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (
        settings.public_mail_daily_budget + settings.confirmation_mail_daily_budget
        < settings.campaign_daily_reserve
    )
