"""Campaigns over HTTP: the public unsubscribe (Task 14) and the admin routes (Task 17)."""

from campaign_api_flow import recipient_row, tidy  # noqa: F401  (fixture)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_core.models import CampaignOptout


def test_the_header_url_fetched_by_get_only_redirects_to_the_page(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    answer = client.get("/api/hub/campagne/disiscrizione?t=good", follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=good"
    assert tidy.query(CampaignOptout).count() == 0


def test_the_one_click_post_opts_out_and_a_wrong_token_answers_the_same(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    wrong = client.post(
        "/api/hub/campagne/disiscrizione?t=nope", data={"List-Unsubscribe": "One-Click"}
    )
    right = client.post(
        "/api/hub/campagne/disiscrizione?t=good", data={"List-Unsubscribe": "One-Click"}
    )
    assert wrong.status_code == right.status_code == 200
    assert wrong.json() == right.json() == {"ok": True}
    assert tidy.get(CampaignOptout, "ada@studio.it") is not None
