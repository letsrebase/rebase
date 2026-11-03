"""«Un link» over HTTP (REB-530): the address a form can point at when it is refused,
the campaign that carries it, and the test mail that leads there untouched."""

import re

from admins import an_admin
from campaign_api_flow import tidy  # noqa: F401  (fixture)
from campaign_fixtures import RESEND_TEST_SECRET
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_api.deps import get_campaign_sender
from rebase_core.campaigns.links import LINK_URL_INVALID, LINK_URL_MISSING, LINK_URL_NOT_HTTPS
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender

LUMA = "https://lu.ma/rebase-house"
BODY = {
    "nome": "Casa",
    "source": "status",
    "journey_status": "complete",
    "subject": "Vieni alla rebase house",
    "text": "Ciao {nome},\n\nti aspettiamo.",
    "button_text": "Iscriviti su Luma",
    "button_meta": "link",
    "button_url": LUMA,
    "action": "click",
}


def signed_in(client: TestClient, sender: RecordingSender, session: Session) -> None:
    an_admin(session, last_name="Sala")
    assert client.post("/api/hub/auth/link", json={"email": "ivan@rebase.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200


def test_a_refused_address_names_its_field_in_a_sentence(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    signed_in(client, sender, tidy)
    for url, sentence in (
        (None, LINK_URL_MISSING),
        ("http://lu.ma/x", LINK_URL_NOT_HTTPS),
        # A 422, never the 500 `urlsplit`'s own `ValueError` would be (Greptile on #476).
        ("https://[invalid", LINK_URL_INVALID),
        ("https://lu.ma:99999/rebase-house", LINK_URL_INVALID),
    ):
        answer = client.post("/api/hub/campaigns", json={**BODY, "button_url": url})
        assert answer.status_code == 422
        assert answer.json()["detail"] == [
            {"loc": ["body", "button_url"], "msg": sentence, "type": "value_error"}
        ]


def test_a_link_campaign_is_saved_read_back_and_tested_without_our_tracking(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    signed_in(client, sender, tidy)
    recording = RecordingCampaignSender()
    client.app.dependency_overrides[get_campaign_sender] = lambda: recording  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,  # type: ignore[call-arg]
        resend_webhook_secret=RESEND_TEST_SECRET,
    )
    created = client.post("/api/hub/campaigns", json=BODY)
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert (created.json()["button_meta"], created.json()["button_url"]) == ("link", LUMA)
    detail = client.get(f"/api/hub/campaigns/{campaign_id}").json()["campaign"]
    assert (detail["button_url"], detail["action"]) == (LUMA, "click")
    tested = client.post(f"/api/hub/campaigns/{campaign_id}/test")
    assert tested.status_code == 200, tested.text
    mail = recording.sent[0].mail
    assert f"Iscriviti su Luma: {LUMA}\n" in mail.text and "utm_" not in mail.text
    # Back to the hub: the address goes with «Un link», and the action must follow.
    back = client.patch(
        f"/api/hub/campaigns/{campaign_id}", json={"button_meta": "area", "action": "signed_in"}
    )
    assert back.status_code == 200, back.text
    assert (back.json()["button_url"], back.json()["ready"]) == (None, False)
