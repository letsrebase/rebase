"""Invitations over HTTP (spec 2026-09-17, REB-290).

The routes, their statuses, their problem documents, and the mail. The service
guarantees (single spend, the three dead states, the index) are tested in
`packages/core/tests/test_invitations.py`; what this file holds to is what a browser
sees: the admin surface refuses the wrong role with a problem document, the raw token
never crosses the wire outside a mail, the accept sets the same cookie pair `login`
sets, and the anonymous half is throttled per client.

One fixture discipline worth knowing before reading: `client` is one TestClient per
test and the `logged_in` / `collaborator_client` / `readonly_client` fixtures all
log into *that same jar*, so a test gets exactly one signed-in identity, whichever
of them it asks for. `sender` deliberately overrides the mail dependencies on
`client` alone (not on any signed-in fixture): the routes that mail check the
installation's ability to send *before* the role guard, so a 403 test needs a
sender configured on a box whose jar belongs to the collaborator, not the admin.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.invitation_models import Invitation
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.mail import RecordingSender
from pigrocrm_api.ratelimit import REQUESTS_PER_MINUTE
from pigrocrm_api.routers.auth import get_sender

PUBLIC_URL = "https://pigro.test"
INVITED = "nuova@pigro.it"


@pytest.fixture
def sender(client: TestClient) -> RecordingSender:
    """A recording sender, and the public origin an invitation needs: without either
    the routes that mail answer 503 by design (the same override shape
    `test_auth_api.py` builds for the link by mail, anchored on `client` so it
    composes with any one signed-in identity)."""
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        public_url=PUBLIC_URL,
        _env_file=None,  # type: ignore[call-arg]
    )
    return recording


def _invite(client: TestClient, email: str = INVITED, nome: str | None = "Luca") -> dict:
    response = client.post("/api/users/invites", json={"email": email, "nome": nome})
    assert response.status_code == 201, response.text
    return response.json()


def _token_from(mail_text: str) -> str:
    return mail_text.split("?t=", 1)[1].split()[0]


# --- the admin surface ---------------------------------------------------------------


def test_an_invitation_is_created_mailed_and_listed(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    invite = _invite(logged_in)
    assert invite["email"] == INVITED
    assert invite["ruolo"] == "collaboratore" and invite["nome"] == "Luca"
    # The raw token is never in the response: the only copy of it leaves by mail.
    assert "token" not in str(invite).lower()
    assert len(sender.sent) == 1
    mail = sender.sent[0]
    assert mail.to == INVITED
    assert mail.subject == "Sei stato invitato in PigroCRM su PigroCRM"
    assert "ti ha invitato a entrare" in mail.text and "Admin" in mail.text
    assert "senza scegliere una password" in mail.text and "7 giorni" in mail.text
    # The link wears the configured public origin and the English page REB-291 builds.
    assert f"{PUBLIC_URL}/app/invite?t=" in mail.text and "testserver" not in mail.text

    listed = logged_in.get("/api/users/invites")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [invite["id"]]


def test_without_a_sender_the_endpoint_refuses_without_writing(logged_in: TestClient) -> None:
    """An installation that cannot mail does not half-create an invitation: no row,
    so nothing to find in the list and no token with no mail carrying it."""
    response = logged_in.post("/api/users/invites", json={"email": INVITED, "nome": None})
    assert response.status_code == 503
    assert "RESEND_API_KEY" in response.json()["detail"]
    assert logged_in.get("/api/users/invites").json() == []


def test_a_collaboratore_cannot_invite(
    collaborator_client: TestClient, sender: RecordingSender
) -> None:
    """403 through the problem document, on every one of the four admin routes. The
    `sender` fixture is what lets the role be the *first* refusal: with no sender
    the POST would answer 503, which is the installation's answer, not the caller's."""
    client = collaborator_client
    refused = client.post("/api/users/invites", json={"email": INVITED, "nome": None})
    assert refused.status_code == 403, refused.text
    problem = refused.json()
    assert problem["type"] == "https://pigrocrm.dev/errors/permission_denied"
    assert "invite_user" in problem["detail"]
    assert client.get("/api/users/invites").status_code == 403
    unknown = "00000000-0000-0000-0000-000000000000"
    assert client.delete(f"/api/users/invites/{unknown}").status_code == 403
    assert client.post(f"/api/users/invites/{unknown}/resend").status_code == 403


def test_a_readonly_cannot_invite(readonly_client: TestClient, sender: RecordingSender) -> None:
    del sender
    assert readonly_client.post("/api/users/invites", json={"email": INVITED}).status_code == 403


def test_a_pending_duplicate_and_an_active_user_are_both_409(
    logged_in: TestClient, sender: RecordingSender, api_session: Session
) -> None:
    _invite(logged_in)
    twice = logged_in.post("/api/users/invites", json={"email": INVITED, "nome": "Luca"})
    assert twice.status_code == 409
    assert twice.json()["code"] == "conflict"
    assert "invito in attesa" in twice.json()["detail"]
    assert len(sender.sent) == 1  # the refusal sent nothing

    UserService(api_session).create(
        UserCreate(email="gia@pigro.it", password="lunghissima1", nome="Già", ruolo="readonly"),
        Actor.system(),
    )
    member = logged_in.post("/api/users/invites", json={"email": "gia@pigro.it", "nome": None})
    assert member.status_code == 409
    assert "persona attiva" in member.json()["detail"]


def test_resend_sends_a_new_link_and_kills_the_old_one(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    invite = _invite(logged_in)
    old_raw = _token_from(sender.sent[0].text)
    resent = logged_in.post(f"/api/users/invites/{invite['id']}/resend")
    assert resent.status_code == 200, resent.text
    assert resent.json()["id"] == invite["id"]
    assert len(sender.sent) == 2
    new_raw = _token_from(sender.sent[1].text)
    assert new_raw != old_raw
    # The old link is dead: no row carries its hash any more.
    stale_peek = logged_in.get("/api/auth/invite", params={"t": old_raw})
    assert stale_peek.status_code == 404
    assert stale_peek.json()["code"] == "invitation_unknown"


def test_revoke_stops_the_link_and_a_second_revoke_is_404(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    invite = _invite(logged_in)
    raw = _token_from(sender.sent[0].text)
    assert logged_in.delete(f"/api/users/invites/{invite['id']}").status_code == 204
    gone = logged_in.get("/api/auth/invite", params={"t": raw})
    assert gone.status_code == 410
    assert gone.json()["code"] == "invitation_revoked"
    assert logged_in.delete(f"/api/users/invites/{invite['id']}").status_code == 404
    assert logged_in.post(f"/api/users/invites/{invite['id']}/resend").status_code == 404
    assert logged_in.get("/api/users/invites").json() == []


def test_an_unknown_invitation_id_is_404_for_both_actions(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    """With a sender configured, so the answer is the row's absence and not the
    installation's inability to mail (the mail guard runs before the write: a resend
    that cannot send must not spend the old token)."""
    unknown = "00000000-0000-0000-0000-0000000000ff"
    assert logged_in.delete(f"/api/users/invites/{unknown}").status_code == 404
    assert logged_in.post(f"/api/users/invites/{unknown}/resend").status_code == 404


# --- the invitee's half (anonymous) ----------------------------------------------------


def test_the_peek_shows_the_invitation_and_does_not_spend_it(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    _invite(logged_in)
    raw = _token_from(sender.sent[0].text)
    peek = logged_in.get("/api/auth/invite", params={"t": raw})
    assert peek.status_code == 200, peek.text
    assert peek.json() == {"spazio": "PigroCRM", "invitato_da": "Admin", "nome": "Luca"}
    # Reading is not spending: two peeks and the accept still work.
    assert logged_in.get("/api/auth/invite", params={"t": raw}).status_code == 200
    assert logged_in.post("/api/auth/invite", json={"t": raw}).status_code == 200


def test_peek_without_a_token_is_422_and_a_forged_one_is_404(logged_in: TestClient) -> None:
    assert logged_in.get("/api/auth/invite").status_code == 422
    forged = logged_in.get("/api/auth/invite", params={"t": "x" * 43})
    assert forged.status_code == 404
    assert forged.json()["type"] == "https://pigrocrm.dev/errors/invitation_unknown"


def test_accept_creates_the_user_and_opens_the_session(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    _invite(logged_in, email=INVITED.upper())
    raw = _token_from(sender.sent[0].text)
    accepted = logged_in.post("/api/auth/invite", json={"t": raw})
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["email"] == INVITED and body["ruolo"] == "collaboratore"
    assert body["attivo"] is True
    # Same cookie pair `login` sets.
    cookies = accepted.headers.get_list("set-cookie")
    assert any("pigrocrm_access=" in c for c in cookies)
    assert any("pigrocrm_refresh=" in c for c in cookies)
    me = logged_in.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == INVITED
    # Spent once: the same token is now a 410 with its own name.
    again = logged_in.post("/api/auth/invite", json={"t": raw})
    assert again.status_code == 410
    assert again.json()["code"] == "invitation_used"


def test_accept_without_a_name_when_the_invitation_carried_none(
    logged_in: TestClient, sender: RecordingSender
) -> None:
    _invite(logged_in, nome=None)
    raw = _token_from(sender.sent[0].text)
    refused = logged_in.post("/api/auth/invite", json={"t": raw})
    assert refused.status_code == 422
    assert "nome" in refused.json()["detail"]
    accepted = logged_in.post("/api/auth/invite", json={"t": raw, "nome": "Nominato"})
    assert accepted.status_code == 200
    assert accepted.json()["nome"] == "Nominato"


def test_an_expired_invitation_answers_410(
    logged_in: TestClient, sender: RecordingSender, api_session: Session
) -> None:
    invite = _invite(logged_in)
    raw = _token_from(sender.sent[0].text)
    row = api_session.scalar(select(Invitation).where(Invitation.id == invite["id"]))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    api_session.commit()
    peek = logged_in.get("/api/auth/invite", params={"t": raw})
    assert peek.status_code == 410
    assert peek.json()["code"] == "invitation_expired"
    assert "scaduto" in peek.json()["detail"]
    accept = logged_in.post("/api/auth/invite", json={"t": raw})
    assert accept.status_code == 410 and accept.json()["code"] == "invitation_expired"


def test_the_accept_route_is_throttled_per_client(logged_in: TestClient) -> None:
    """The anonymous half of the flow is the new public surface; the bucket is what
    stops a script hammering it (REB-228's discipline, its own scope so guessing at
    invitations cannot burn the login link's budget and vice versa)."""
    for _ in range(REQUESTS_PER_MINUTE):
        assert logged_in.post("/api/auth/invite", json={"t": "inesistente"}).status_code == 404
    refused = logged_in.post("/api/auth/invite", json={"t": "inesistente"})
    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "60"
    # Another client has its own bucket.
    other = logged_in.post(
        "/api/auth/invite", json={"t": "inesistente"}, headers={"X-Real-IP": "10.0.0.7"}
    )
    assert other.status_code == 404, other.text


def test_a_nul_byte_in_the_token_is_a_clean_422_not_a_500(logged_in: TestClient) -> None:
    """The token travels as a query parameter and a body field, and psycopg refuses
    to bind a NUL byte at all; `SafeStr` is what turns that into a 422."""
    assert logged_in.get("/api/auth/invite", params={"t": "ab\x00cd"}).status_code == 422
    assert logged_in.post("/api/auth/invite", json={"t": "ab\x00cd"}).status_code == 422
