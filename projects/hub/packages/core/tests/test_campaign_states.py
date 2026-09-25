"""Journey states (spec § 2) and the settings campaigns read."""

from pathlib import Path

from rebase_core.config import Settings

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
