"""Campaigns over HTTP: the public unsubscribe (Task 14) and the admin routes (Task 17)."""

import re

from campaign_api_flow import (  # noqa: F401  (fixture)
    admin_user,
    recipient_row,
    recipient_rows,
    tidy,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_api.deps import get_campaign_sender
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Campaign, CampaignOptout, Freelancer, User

WEBHOOK_SECRET = "whsec_" + "c2VncmV0bw=="


def test_the_header_url_fetched_by_get_only_redirects_to_the_page(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    answer = client.get("/api/hub/campagne/disiscrizione?t=good", follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=good"
    assert tidy.query(CampaignOptout).count() == 0


def test_a_mangled_token_stays_one_encoded_parameter_of_the_redirect(
    client: TestClient,
) -> None:
    """The token reaches the route decoded; written back raw, `a%26x%3D1` would become a
    second parameter and `%0D%0A` a line break in the `Location` header."""
    answer = client.get("/api/hub/campagne/disiscrizione?t=a%26x%3D1", follow_redirects=False)
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=a%26x%3D1"
    broken = client.get("/api/hub/campagne/disiscrizione?t=a%0D%0Ab", follow_redirects=False)
    assert broken.status_code == 303
    assert broken.headers["location"].endswith("/disiscrizione?t=a%0D%0Ab")


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


def test_seven_one_click_posts_in_a_row_from_one_client_all_opt_out(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    """Gmail and Yahoo send RFC 8058's one-click POST from a handful of server
    addresses: the route must not spend the sign-up limiter's five a minute, or the
    sixth opt-out of a minute would be a 429 and silently lost."""
    addresses = {f"token-{n}": f"persona{n}@studio.it" for n in range(7)}
    recipient_rows(tidy, addresses)
    for token in addresses:
        answer = client.post(
            f"/api/hub/campagne/disiscrizione?t={token}", data={"List-Unsubscribe": "One-Click"}
        )
        assert answer.status_code == 200, answer.text
    tidy.expire_all()
    assert {o.email for o in tidy.query(CampaignOptout)} == set(addresses.values())


def login_admin(client: TestClient, sender: RecordingSender, session: Session) -> None:
    admin_user(session)
    assert client.post("/api/hub/auth/link", json={"email": "ivan@rebase.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200


def a_card_without_cv(session: Session, email: str) -> None:
    from decimal import Decimal

    user = User(email=email, nome="Ada", cognome="L")
    session.add(user)
    session.flush()
    session.add(
        Freelancer(
            user_id=user.id,
            tariffa_giornaliera=Decimal("400"),
            posizione="Dev",
            remoto="remoto",
            links=[],
        )
    )
    session.commit()


def with_webhook(client: TestClient) -> None:
    """This environment has Resend's webhook secret, as production does."""
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,  # type: ignore[call-arg]
        resend_webhook_secret=WEBHOOK_SECRET,
    )


def a_draft(client: TestClient) -> str:
    created = client.post(
        "/api/hub/campaigns",
        json={
            "nome": "X",
            "fonte": "stato",
            "stato_percorso": "completo",
            "oggetto": "o",
            "testo": "t",
            "bottone_testo": "b",
            "bottone_meta": "area",
            "azione": "entrato",
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def test_every_campaign_route_wants_an_admin(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    assert client.get("/api/hub/campaigns").status_code == 401
    assert (
        client.post("/api/hub/campaigns/never-write", json={"email": "a@b.it"}).status_code == 401
    )


def test_a_signed_in_member_hitting_campaigns_is_403_not_401(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    tidy.add(User(email="membro@studio.it", nome="M", cognome="M"))
    tidy.commit()
    assert client.post("/api/hub/auth/link", json={"email": "membro@studio.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200
    assert client.get("/api/hub/campaigns").status_code == 403


def test_the_admin_drafts_tests_and_schedules_a_campaign(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    recording = RecordingCampaignSender()
    client.app.dependency_overrides[get_campaign_sender] = lambda: recording  # type: ignore[attr-defined]
    with_webhook(client)
    a_card_without_cv(tidy, "ada@studio.it")
    templates = client.get("/api/hub/campaigns/templates").json()
    cv = next(t for t in templates if t["stato_percorso"] == "manca_cv")
    created = client.post(
        "/api/hub/campaigns",
        json={
            "nome": "CV",
            "fonte": "stato",
            **{
                k: cv[k]
                for k in (
                    "stato_percorso",
                    "oggetto",
                    "testo",
                    "bottone_testo",
                    "bottone_meta",
                    "azione",
                )
            },
        },
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert client.get(f"/api/hub/campaigns/{campaign_id}/audience").json()["incluse"] == 1
    refused = client.post(f"/api/hub/campaigns/{campaign_id}/schedule", json={"esclusi": []})
    assert refused.status_code == 409 and "prova" in refused.json()["detail"]
    tested = client.post(f"/api/hub/campaigns/{campaign_id}/test").json()
    assert tested["pronta"] is True and recording.sent[0].mail.to == "ivan@rebase.it"
    scheduled = client.post(
        f"/api/hub/campaigns/{campaign_id}/schedule",
        json={"giorno": "2030-01-10", "ora": "09:30", "esclusi": []},
    )
    assert (
        scheduled.status_code == 200
        and scheduled.json()["programmata_per"] == "2030-01-10T08:30:00Z"
    )
    detail = client.get(f"/api/hub/campaigns/{campaign_id}").json()
    assert (
        detail["conteggi"]["in_coda"] == 1 and detail["destinatari"][0]["email"] == "ada@studio.it"
    )


def test_without_a_resend_key_the_test_is_a_503_sentence(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    client.app.dependency_overrides[get_campaign_sender] = lambda: None  # type: ignore[attr-defined]
    campaign_id = a_draft(client)
    for verb, body in (("test", None), ("schedule", {"esclusi": []})):
        answer = client.post(f"/api/hub/campaigns/{campaign_id}/{verb}", json=body)
        assert answer.status_code == 503 and "non è configurato" in answer.json()["detail"]


def test_a_key_without_the_webhook_secret_refuses_test_and_schedule(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    """With a Resend key but no REBASE_RESEND_WEBHOOK_SECRET a campaign would leave
    and nobody would ever read its deliveries, bounces or complaints: both verbs
    refuse with a sentence, before touching the campaign."""
    login_admin(client, sender, tidy)
    recording = RecordingCampaignSender()
    client.app.dependency_overrides[get_campaign_sender] = lambda: recording  # type: ignore[attr-defined]
    campaign_id = a_draft(client)
    for verb, body in (("test", None), ("schedule", {"esclusi": []})):
        answer = client.post(f"/api/hub/campaigns/{campaign_id}/{verb}", json=body)
        assert answer.status_code == 503, answer.text
        assert answer.json()["detail"] == (
            "Manca il webhook di Resend: configuralo prima di inviare, vedi AGENTS.md."
        )
    assert recording.sent == []
    tidy.expire_all()
    campaign = tidy.get(Campaign, campaign_id)
    assert campaign is not None
    assert (campaign.stato, campaign.prova_inviata_at) == ("bozza", None)


def test_never_write_records_the_address(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    assert (
        client.post(
            "/api/hub/campaigns/never-write", json={"email": "Lorenzo@Studio.it"}
        ).status_code
        == 200
    )
    assert tidy.get(CampaignOptout, "lorenzo@studio.it").fonte == "admin"  # type: ignore[union-attr]
