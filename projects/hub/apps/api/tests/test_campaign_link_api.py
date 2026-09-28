"""«Un link» over HTTP (REB-530): the address a form can point at when it is refused,
the campaign that carries it, and the test mail that leads there untouched."""

import re

from campaign_api_flow import admin_user, tidy  # noqa: F401  (fixture)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_api.deps import get_campaign_sender
from rebase_core.campaigns.links import LINK_URL_MISSING, LINK_URL_NOT_HTTPS
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender

LUMA = "https://lu.ma/rebase-house"
BODY = {
    "nome": "Casa",
    "fonte": "stato",
    "stato_percorso": "completo",
    "oggetto": "Vieni alla rebase house",
    "testo": "Ciao {nome},\n\nti aspettiamo.",
    "bottone_testo": "Iscriviti su Luma",
    "bottone_meta": "link",
    "bottone_url": LUMA,
    "azione": "clic",
}


def signed_in(client: TestClient, sender: RecordingSender, session: Session) -> None:
    admin_user(session)
    assert client.post("/api/hub/auth/link", json={"email": "ivan@rebase.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200


def test_a_refused_address_names_its_field_in_a_sentence(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    signed_in(client, sender, tidy)
    for url, sentence in ((None, LINK_URL_MISSING), ("http://lu.ma/x", LINK_URL_NOT_HTTPS)):
        answer = client.post("/api/hub/campaigns", json={**BODY, "bottone_url": url})
        assert answer.status_code == 422
        assert answer.json()["detail"] == [
            {"loc": ["body", "bottone_url"], "msg": sentence, "type": "value_error"}
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
        resend_webhook_secret="whsec_c2VncmV0bw==",
    )
    created = client.post("/api/hub/campaigns", json=BODY)
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert (created.json()["bottone_meta"], created.json()["bottone_url"]) == ("link", LUMA)
    detail = client.get(f"/api/hub/campaigns/{campaign_id}").json()["campagna"]
    assert (detail["bottone_url"], detail["azione"]) == (LUMA, "clic")
    tested = client.post(f"/api/hub/campaigns/{campaign_id}/test")
    assert tested.status_code == 200, tested.text
    mail = recording.sent[0].mail
    assert f"Iscriviti su Luma: {LUMA}\n" in mail.text and "utm_" not in mail.text
    # Back to the hub: the address goes with «Un link», and the action must follow.
    back = client.patch(
        f"/api/hub/campaigns/{campaign_id}", json={"bottone_meta": "area", "azione": "entrato"}
    )
    assert back.status_code == 200, back.text
    assert (back.json()["bottone_url"], back.json()["pronta"]) == (None, False)
