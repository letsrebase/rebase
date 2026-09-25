"""Who no campaign may reach (spec § 7)."""

import pytest
from campaign_fixtures import campaign_row, clean  # noqa: F401
from sqlalchemy.orm import Session

from rebase_core.campaigns.optouts import TOKEN_MAX_LENGTH, OptoutService
from rebase_core.models import CampaignOptout, CampaignRecipient


def test_a_good_token_opts_out_its_address_and_an_unknown_one_does_nothing(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(clean)
    clean.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            email="ada@studio.it",
            tipo="freelancer",
            codice="1",
            prima={},
            disiscrizione_token="good",
        )
    )
    clean.commit()
    OptoutService(clean).unsubscribe("nobody")
    assert clean.query(CampaignOptout).count() == 0
    OptoutService(clean).unsubscribe("good")
    OptoutService(clean).unsubscribe("good")  # twice: still one row
    row = clean.get(CampaignOptout, "ada@studio.it")
    assert row is not None and row.fonte == "link" and row.campaign_id == campaign.id


def test_never_write_is_lowercase_and_the_first_source_stays(clean: Session) -> None:  # noqa: F811  (fixture)
    OptoutService(clean).never_write(" Lorenzo@Studio.it ")
    OptoutService(clean).record("lorenzo@studio.it", "reclamo", None)
    assert clean.get(CampaignOptout, "lorenzo@studio.it").fonte == "admin"  # type: ignore[union-attr]


@pytest.mark.parametrize("invalid_token", ["", "x" * (TOKEN_MAX_LENGTH + 1)])
def test_invalid_tokens_are_rejected_silently(clean: Session, invalid_token: str) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(clean)
    clean.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            email="test@studio.it",
            tipo="freelancer",
            codice="1",
            prima={},
            disiscrizione_token="valid_token",
        )
    )
    clean.commit()
    OptoutService(clean).unsubscribe(invalid_token)
    assert clean.query(CampaignOptout).count() == 0
