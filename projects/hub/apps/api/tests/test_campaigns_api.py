"""Campaigns over HTTP: the public unsubscribe (Task 14) and the admin routes (Task 17)."""

import re
from datetime import UTC, datetime
from uuid import UUID

from admins import an_admin
from campaign_api_flow import (  # noqa: F401  (fixture)
    recipient_row,
    recipient_rows,
    tidy,
)
from campaign_fixtures import RESEND_TEST_SECRET
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from rebase_api.deps import get_campaign_sender
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient, Freelancer, User


def test_the_header_url_fetched_by_get_only_redirects_to_the_page(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    answer = client.get("/api/hub/campaigns/unsubscribe?t=good", follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=good"
    assert tidy.query(CampaignOptout).count() == 0


def test_a_mangled_token_stays_one_encoded_parameter_of_the_redirect(
    client: TestClient,
) -> None:
    """The token reaches the route decoded; written back raw, `a%26x%3D1` would become a
    second parameter and `%0D%0A` a line break in the `Location` header."""
    answer = client.get("/api/hub/campaigns/unsubscribe?t=a%26x%3D1", follow_redirects=False)
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=a%26x%3D1"
    broken = client.get("/api/hub/campaigns/unsubscribe?t=a%0D%0Ab", follow_redirects=False)
    assert broken.status_code == 303
    assert broken.headers["location"].endswith("/unsubscribe?t=a%0D%0Ab")


def test_the_one_click_post_opts_out_and_a_wrong_token_answers_the_same(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    recipient_row(tidy, "good")
    wrong = client.post(
        "/api/hub/campaigns/unsubscribe?t=nope", data={"List-Unsubscribe": "One-Click"}
    )
    right = client.post(
        "/api/hub/campaigns/unsubscribe?t=good", data={"List-Unsubscribe": "One-Click"}
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
            f"/api/hub/campaigns/unsubscribe?t={token}", data={"List-Unsubscribe": "One-Click"}
        )
        assert answer.status_code == 200, answer.text
    tidy.expire_all()
    assert {o.email for o in tidy.query(CampaignOptout)} == set(addresses.values())


def login_admin(client: TestClient, sender: RecordingSender, session: Session) -> None:
    an_admin(session, last_name="Sala")
    assert client.post("/api/hub/auth/link", json={"email": "ivan@rebase.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200


def a_card_without_cv(session: Session, email: str) -> None:
    from decimal import Decimal

    user = User(email=email, nome="Ada", last_name="L")
    session.add(user)
    session.flush()
    session.add(
        Freelancer(
            user_id=user.id,
            daily_rate=Decimal("400"),
            posizione="Dev",
            remote="remote",
            links=[],
        )
    )
    session.commit()


def with_webhook(client: TestClient) -> None:
    """This environment has Resend's webhook secret, as production does."""
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,  # type: ignore[call-arg]
        resend_webhook_secret=RESEND_TEST_SECRET,
    )


def a_draft(client: TestClient) -> str:
    created = client.post(
        "/api/hub/campaigns",
        json={
            "nome": "X",
            "source": "status",
            "journey_status": "complete",
            "subject": "o",
            "text": "t",
            "button_text": "b",
            "button_meta": "area",
            "action": "signed_in",
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
    tidy.add(User(email="membro@studio.it", nome="M", last_name="M"))
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
    cv = next(t for t in templates if t["journey_status"] == "missing_cv")
    created = client.post(
        "/api/hub/campaigns",
        json={
            "nome": "CV",
            "source": "status",
            **{
                k: cv[k]
                for k in (
                    "journey_status",
                    "subject",
                    "text",
                    "button_text",
                    "button_meta",
                    "action",
                )
            },
        },
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert client.get(f"/api/hub/campaigns/{campaign_id}/audience").json()["incluse"] == 1
    refused = client.post(f"/api/hub/campaigns/{campaign_id}/schedule", json={"esclusi": []})
    assert refused.status_code == 409 and "test" in refused.json()["detail"]
    tested = client.post(f"/api/hub/campaigns/{campaign_id}/test").json()
    assert tested["ready"] is True and recording.sent[0].mail.to == "ivan@rebase.it"
    scheduled = client.post(
        f"/api/hub/campaigns/{campaign_id}/schedule",
        json={"day": "2030-01-10", "ora": "09:30", "esclusi": []},
    )
    assert (
        scheduled.status_code == 200 and scheduled.json()["scheduled_for"] == "2030-01-10T08:30:00Z"
    )
    detail = client.get(f"/api/hub/campaigns/{campaign_id}").json()
    assert detail["counts"]["in_coda"] == 1 and detail["recipients"][0]["email"] == "ada@studio.it"


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
    assert (campaign.status, campaign.test_sent_at) == ("draft", None)


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
    assert tidy.get(CampaignOptout, "lorenzo@studio.it").source == "admin"  # type: ignore[union-attr]


def test_riscrivi_opens_to_list_draft_and_refuses_to_draft(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    draft_id = a_draft(client)
    refused = client.post(f"/api/hub/campaigns/{draft_id}/follow-up")
    assert refused.status_code == 409 and "già inviata" in refused.json()["detail"]
    tidy.execute(update(Campaign).where(Campaign.id == UUID(draft_id)).values(status="sent"))
    campaign = tidy.get(Campaign, UUID(draft_id))
    assert campaign is not None
    tidy.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            email="ada@studio.it",
            kind="freelancer",
            code="1",
            before={},
            unsubscribe_token="t-riscrivi",
            status="sent",
            sent_at=datetime.now(UTC),
        )
    )
    tidy.commit()
    created = client.post(f"/api/hub/campaigns/{draft_id}/follow-up")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["source"], body["segue_id"], body["status"]) == ("list", draft_id, "draft")
    detail = client.get(f"/api/hub/campaigns/{draft_id}").json()
    assert detail["counts"]["actions"] == 0
    assert detail["recipients"][0]["signed_in_from_mail"] is False


def test_a_null_on_a_not_nullable_field_is_422_not_500(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    """Part 1 (REB-550): `source`, `action` and `button_meta` are `NOT NULL` in
    `models.py`'s `Campaign`. An explicit `null` for one of them used to reach
    `CampaignService.update`'s blanket `setattr` and raise an `IntegrityError`, a 500;
    `CampaignPatch` now refuses it before the body ever reaches the service, and its
    `field_validator` (round 3) puts the field's own name last in `loc`, the way the
    web's `ApiError.fields` reads it (`detail[].loc[-1]`, `apps/web/src/lib/api.ts`)."""
    login_admin(client, sender, tidy)
    campaign_id = a_draft(client)
    for field in ("source", "action", "button_meta"):
        answer = client.patch(f"/api/hub/campaigns/{campaign_id}", json={field: None})
        assert answer.status_code == 422, answer.text
        detail = answer.json()["detail"]
        assert detail[0]["loc"][-1] == field
        assert "il campo non può essere svuotato" in detail[0]["msg"]
    unchanged = client.get(f"/api/hub/campaigns/{campaign_id}").json()
    assert unchanged["campaign"]["source"] == "status"


def test_a_blank_name_is_a_422_sentence_on_create_and_on_update(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    """REB-524: a name of spaces used to be stored empty. It is refused with a sentence
    naming the field, `nome` last in `loc` as the page reads it."""
    login_admin(client, sender, tidy)
    campaign_id = a_draft(client)
    answer = client.patch(f"/api/hub/campaigns/{campaign_id}", json={"nome": "   "})
    assert answer.status_code == 422, answer.text
    (item,) = answer.json()["detail"]
    assert (item["loc"][-1], item["msg"]) == (
        "nome",
        "Il nome della campagna non può essere vuoto.",
    )
    assert client.get(f"/api/hub/campaigns/{campaign_id}").json()["campaign"]["nome"] == "X"
    created = client.post(
        "/api/hub/campaigns",
        json={
            "nome": " \t ",
            "source": "status",
            "journey_status": "complete",
            "button_meta": "area",
            "action": "signed_in",
        },
    )
    assert created.status_code == 422 and created.json()["detail"][0]["loc"][-1] == "nome"


def test_a_draft_is_deleted_and_a_scheduled_campaign_is_not(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    """REB-524: «Elimina» on an abandoned draft. A campaign past its draft answers 409
    with the sentence and stays."""
    login_admin(client, sender, tidy)
    draft_id = a_draft(client)
    kept_id = a_draft(client)
    tidy.execute(update(Campaign).where(Campaign.id == UUID(kept_id)).values(status="cancelled"))
    tidy.commit()
    assert client.delete(f"/api/hub/campaigns/{draft_id}").status_code == 204
    assert client.get(f"/api/hub/campaigns/{draft_id}").status_code == 404
    assert client.delete(f"/api/hub/campaigns/{draft_id}").status_code == 404
    refused = client.delete(f"/api/hub/campaigns/{kept_id}")
    assert refused.status_code == 409 and "Si elimina solo una bozza" in refused.json()["detail"]
    assert client.get(f"/api/hub/campaigns/{kept_id}").status_code == 200


def test_deleting_a_campaign_wants_an_admin(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    assert client.delete(f"/api/hub/campaigns/{UUID(int=1)}").status_code == 401


def test_a_stalled_send_reads_its_reason_on_the_list_and_the_page(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    campaign_id = a_draft(client)
    stopped = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    tidy.execute(
        update(Campaign)
        .where(Campaign.id == UUID(campaign_id))
        .values(
            status="sending",
            stalled_at=stopped,
            stalled_reason="Resend rifiuta l'invio: controlla la chiave e il dominio del mittente",
        )
    )
    tidy.commit()
    page = client.get(f"/api/hub/campaigns/{campaign_id}").json()["campaign"]
    (item,) = client.get("/api/hub/campaigns").json()["items"]
    for read in (page, item):
        assert (
            read["stalled_reason"]
            == "Resend rifiuta l'invio: controlla la chiave e il dominio del mittente"
        )
        assert read["stalled_at"].startswith("2026-09-28T09:00:00")
