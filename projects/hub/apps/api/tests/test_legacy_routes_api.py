"""Mails sent before the English rename (REB-902) still carry the Italian unsubscribe path."""

from campaign_api_flow import recipient_row, tidy  # noqa: F401  (fixture)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_core.models import CampaignOptout


def test_the_old_one_click_post_path_still_unsubscribes(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    answer = client.post(
        "/api/hub/campagne/disiscrizione?t=good", data={"List-Unsubscribe": "One-Click"}
    )
    assert answer.status_code == 200
    assert answer.json() == {"ok": True}
    assert tidy.get(CampaignOptout, "ada@studio.it") is not None


def test_the_old_unsubscribe_page_link_still_redirects(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    answer = client.get("/api/hub/campagne/disiscrizione?t=good", follow_redirects=False)
    assert answer.status_code in (302, 303, 307)
    assert answer.headers["location"] == "https://letsrebase.com/hub/unsubscribe?t=good"
