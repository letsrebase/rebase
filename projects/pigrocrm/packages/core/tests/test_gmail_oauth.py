"""The authorisation-code flow, end to end, against the recording fake.

Three properties are load-bearing here and each is tested as a mechanism rather than
as a convention:

  * **PKCE is real.** The `code_verifier` never leaves the server, and the verifier
    that reaches Google's token endpoint is the pre-image of the challenge that went
    out in the redirect. `FakeGmail.expected_code_challenge` makes the fake verify it
    the way Google does, so a flow that sent the wrong verifier -- or none -- fails
    here instead of failing in production.
  * **A state is single-use, whatever the outcome.** It is burned and committed before
    anything that can fail, so a refusal never hands the state back for a second try.
  * **Nothing leaks.** Not a refresh token, not an access token, not the verifier, and
    not -- to a caller who has not already proved they own the account -- the address
    the flow was started for.
"""

import base64
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fakes.fake_gmail import FakeGmail
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings, decode_google_token_key
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.errors import Conflict, DomainError, PermissionDenied
from pigrocrm.core.gmail.crypto import seal, unseal
from pigrocrm.core.gmail.models import (
    GmailMessage,
    GmailMessageLink,
    GoogleAccount,
    GoogleOAuthState,
)
from pigrocrm.core.gmail.oauth import GmailOAuthService
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES, SCOPE_READONLY, SCOPE_SEND
from pigrocrm.core.gmail.tokens import GOOGLE_AUTH_URL, GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.tenants import space_base_settings

KEY = b"k" * 32
KEY_B64 = base64.b64encode(KEY).decode()
REFRESH = "1//0gTheRealRefresh"
ACCESS = "ya29.the-real-access-token"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "jwt_secret": "x" * 32,
        "google_client_id": "cid.apps.googleusercontent.com",
        "google_client_secret": "the-client-secret",
        "google_token_key": KEY_B64,
        "public_url": "https://crm.example.it",
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def _id_token(sub: str, email: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"sub": sub, "email": email}).encode())
    return f"header.{claims.decode().rstrip('=')}.signature"


def _fake(sub: str = "104729", email: str = "ada@acme.it", **overrides: object) -> FakeGmail:
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES, **overrides)  # type: ignore[arg-type]
    fake.id_token = _id_token(sub, email)
    fake.refresh_token = REFRESH
    fake.access_token = ACCESS
    return fake


def _service(
    session: Session, fake: FakeGmail, settings: Settings | None = None
) -> GmailOAuthService:
    resolved = settings or _settings()
    return GmailOAuthService(
        session,
        settings=resolved,
        tokens=GoogleTokenClient(
            client_id=resolved.google_client_id,
            client_secret=resolved.google_client_secret,
            transport=GmailTransport(http=fake, sleep=lambda _: None),
        ),
    )


def _user(session: Session, email: str = "owner@example.it", ruolo: str = "admin") -> User:
    user = User(email=email, nome="Owner", password_hash="x", ruolo=ruolo, attivo=True)
    session.add(user)
    session.flush()
    return user


def _actor(user: User, ruolo: str = "admin") -> Actor:
    return Actor(id=user.id, type="user", role=ruolo)  # type: ignore[arg-type]


def _start(service: GmailOAuthService, actor: Actor) -> tuple[str, dict[str, list[str]]]:
    url = service.start(actor)
    return url, parse_qs(urlparse(url).query)


def _state_of(service: GmailOAuthService, actor: Actor) -> str:
    return _start(service, actor)[1]["state"][0]


def _accounts(session: Session) -> list[GoogleAccount]:
    return list(session.execute(select(GoogleAccount)).scalars().all())


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _token_bodies(fake: FakeGmail) -> Iterator[dict[str, list[str]]]:
    for request in fake.requests:
        if request.host == "oauth2.googleapis.com":
            yield parse_qs((request.body or b"").decode())


# --- the redirect --------------------------------------------------------------------


def test_start_asks_for_offline_access_and_forces_the_consent_screen(db_session: Session) -> None:
    user = _user(db_session)
    url, query = _start(_service(db_session, _fake()), _actor(user))
    assert url.startswith(f"{GOOGLE_AUTH_URL}?")
    assert query["access_type"] == ["offline"]
    # Without prompt=consent a repeat authorisation returns no refresh token at all.
    assert query["prompt"] == ["consent"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == set(REQUESTED_SCOPES)
    assert query["redirect_uri"] == ["https://crm.example.it/api/gmail/oauth/callback"]


def test_start_keeps_the_code_verifier_server_side(db_session: Session) -> None:
    """PKCE is worth nothing if the verifier travels through the browser. It lives in
    google_oauth_states; the redirect carries only the challenge and the jti."""
    user = _user(db_session)
    url, query = _start(_service(db_session, _fake()), _actor(user))
    row = db_session.execute(select(GoogleOAuthState)).scalars().one()
    assert row.code_verifier not in url
    assert row.code_verifier != query["code_challenge"][0]
    assert len(row.code_verifier) >= 43
    assert _s256(row.code_verifier) == query["code_challenge"][0]


def test_the_redirect_never_carries_the_client_secret(db_session: Session) -> None:
    """The authorisation request is a browser redirect: everything in it is public.
    The client *secret* belongs only in the back-channel token POST."""
    user = _user(db_session)
    url, _ = _start(_service(db_session, _fake()), _actor(user))
    assert "the-client-secret" not in url


def test_each_start_mints_a_fresh_state_and_a_fresh_verifier(db_session: Session) -> None:
    """Reusing either across two authorisations would make the second replayable with
    material captured from the first."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    first = _start(service, _actor(user))[1]
    second = _start(service, _actor(user))[1]
    assert first["state"] != second["state"]
    assert first["code_challenge"] != second["code_challenge"]
    verifiers = {
        row.code_verifier for row in db_session.execute(select(GoogleOAuthState)).scalars().all()
    }
    assert len(verifiers) == 2


# --- the callback --------------------------------------------------------------------


def test_complete_stores_the_refresh_token_encrypted_and_records_the_connection(
    db_session: Session,
) -> None:
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)

    read = service.complete(
        code="4/0A-code", state=_state_of(service, _actor(user)), actor=_actor(user)
    )

    assert read.email_address == "ada@acme.it"
    assert read.status == "active"
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    assert account.google_sub == "104729"
    assert REFRESH.encode() not in account.refresh_token_ciphertext
    assert unseal(account.refresh_token_ciphertext, account.refresh_token_nonce, KEY) == REFRESH
    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "gmail.account_collegato" in kinds


def test_the_verifier_that_reaches_google_is_the_pre_image_of_the_published_challenge(
    db_session: Session,
) -> None:
    """The end-to-end PKCE claim. Asserted twice on purpose: the fake refuses the
    exchange unless the verifier hashes to the challenge, *and* the recorded request
    body is checked directly here -- so removing either guard leaves the other."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    _, query = _start(service, _actor(user))
    fake.expected_code_challenge = query["code_challenge"][0]

    service.complete(code="4/0A-code", state=query["state"][0], actor=_actor(user))

    sent = [
        body for body in _token_bodies(fake) if body.get("grant_type") == ["authorization_code"]
    ]
    assert len(sent) == 1
    verifier = sent[0]["code_verifier"][0]
    assert _s256(verifier) == query["code_challenge"][0]
    assert verifier != query["code_challenge"][0]


def test_an_exchange_carrying_the_wrong_verifier_is_refused_and_stores_nothing(
    db_session: Session,
) -> None:
    """What PKCE is for: an attacker holding a stolen authorisation code but not the
    verifier cannot redeem it. Google answers `invalid_grant`, and no row is written."""
    user = _user(db_session)
    fake = _fake()
    fake.expected_code_challenge = _s256("a-verifier-this-flow-never-generated")
    service = _service(db_session, fake)

    with pytest.raises(Conflict):
        service.complete(
            code="4/0A-code", state=_state_of(service, _actor(user)), actor=_actor(user)
        )
    assert _accounts(db_session) == []


def test_a_state_can_only_be_used_once(db_session: Session) -> None:
    user = _user(db_session)
    service = _service(db_session, _fake())
    state = _state_of(service, _actor(user))
    service.complete(code="4/0A-code", state=state, actor=_actor(user))

    with pytest.raises(Conflict) as caught:
        service.complete(code="4/0A-code", state=state, actor=_actor(user))
    # Deliberately does not say which check failed: unknown, expired, replayed and
    # belonging to another session all answer the same way.
    assert "autorizzazione" in caught.value.message
    assert len(_accounts(db_session)) == 1


def test_a_state_belonging_to_another_user_is_refused(db_session: Session) -> None:
    owner = _user(db_session, "owner@example.it")
    other = _user(db_session, "other@example.it")
    service = _service(db_session, _fake())
    state = _state_of(service, _actor(owner))

    with pytest.raises(Conflict):
        service.complete(code="4/0A-code", state=state, actor=_actor(other))
    assert _accounts(db_session) == []


def test_a_state_refused_for_the_wrong_user_is_burned_and_not_returned_to_its_owner(
    db_session: Session,
) -> None:
    """The state is consumed and committed *before* anything that can fail, so a
    refusal never hands it back. Rolling the consumption back on the failure path --
    the obvious-looking alternative -- would let an attacker probe with a stolen state
    repeatedly, and would leave a still-live state after every transient error."""
    owner = _user(db_session, "owner@example.it")
    other = _user(db_session, "other@example.it")
    service = _service(db_session, _fake())
    state = _state_of(service, _actor(owner))

    with pytest.raises(Conflict):
        service.complete(code="4/0A-code", state=state, actor=_actor(other))
    with pytest.raises(Conflict):
        service.complete(code="4/0A-code", state=state, actor=_actor(owner))
    assert _accounts(db_session) == []


def test_an_expired_state_is_refused(db_session: Session) -> None:
    """The TTL is a wall-clock interval, not a calendar date, so there is no year to
    hard-code and nothing for `oggi_in_italia` to project."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    state = _state_of(service, _actor(user))
    row = db_session.execute(select(GoogleOAuthState)).scalars().one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(Conflict):
        service.complete(code="4/0A-code", state=state, actor=_actor(user))
    assert _accounts(db_session) == []


def test_an_unknown_state_is_refused(db_session: Session) -> None:
    user = _user(db_session)
    service = _service(db_session, _fake())
    _start(service, _actor(user))

    with pytest.raises(Conflict):
        service.complete(code="4/0A-code", state="a-jti-nobody-ever-issued", actor=_actor(user))
    assert _accounts(db_session) == []


def test_replaying_the_authorization_code_under_a_fresh_state_creates_no_second_grant(
    db_session: Session,
) -> None:
    """Google's authorisation codes are single-use too. A replay of a captured code --
    with a state the attacker legitimately holds -- must surface as a refusal, not as
    a silently re-created account."""
    user = _user(db_session)
    fake = _fake(single_use_codes=True)
    service = _service(db_session, fake)
    service.complete(code="4/0A-code", state=_state_of(service, _actor(user)), actor=_actor(user))
    connected_at = db_session.execute(select(GoogleAccount)).scalars().one().connected_at

    with pytest.raises(Conflict):
        service.complete(
            code="4/0A-code", state=_state_of(service, _actor(user)), actor=_actor(user)
        )
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    assert account.connected_at == connected_at


# --- the mailbox identity check ------------------------------------------------------


def test_reconnecting_a_different_mailbox_is_refused_and_names_both(db_session: Session) -> None:
    """Silently relabelling the whole stored history because someone picked the wrong
    Google account in the chooser is the failure this refusal exists to prevent."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    service.complete(code="c1", state=_state_of(service, _actor(user)), actor=_actor(user))

    fake.id_token = _id_token("999999", "someone.else@gmail.com")
    with pytest.raises(Conflict) as caught:
        service.complete(code="c2", state=_state_of(service, _actor(user)), actor=_actor(user))

    assert "ada@acme.it" in caught.value.message
    assert "someone.else@gmail.com" in caught.value.message
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    assert account.email_address == "ada@acme.it"


def test_reconnecting_when_the_same_crm_user_has_a_different_drive_identity_is_refused(
    db_session: Session,
) -> None:
    """The mirror of `GoogleDriveOAuthService.complete`'s own cross-identity check
    (spec 9 §5.2): the mailbox and the Drive of one CRM *user* must name the same
    Google identity, or neither flow's grant may be stored over the other.

    Per user, not per installation: both tables are keyed by `user_id`, two users of
    one installation are meant to hold two different Google identities, and the row
    this refusal reads is `account_for_user(actor.id)`. The Drive row below therefore
    belongs to the very user starting the Gmail flow -- that is what makes it a
    conflict rather than somebody else's account."""
    user = _user(db_session)
    ciphertext, nonce = seal("1//0gDriveRefresh", KEY)
    db_session.add(
        GoogleDriveAccount(
            user_id=user.id,
            google_sub="sub-drive",
            email_address="drive@acme.it",
            refresh_token_ciphertext=ciphertext,
            refresh_token_nonce=nonce,
            scopes_granted=[],
            status="active",
        )
    )
    db_session.flush()
    fake = _fake(sub="sub-other")
    service = _service(db_session, fake)

    with pytest.raises(Conflict) as caught:
        service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    assert "drive@acme.it" in caught.value.message
    assert _accounts(db_session) == []


def test_the_same_google_identity_under_a_different_address_is_refused_too(
    db_session: Session,
) -> None:
    """The address is compared, not only the `sub`. Everything downstream is matched by
    address -- the roster, the stored messages, the send path -- so an account whose
    address moved under a stable `sub` would relabel exactly the same history."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    service.complete(code="c1", state=_state_of(service, _actor(user)), actor=_actor(user))

    fake.id_token = _id_token("104729", "ada@nuovo-dominio.it")
    with pytest.raises(Conflict):
        service.complete(code="c2", state=_state_of(service, _actor(user)), actor=_actor(user))


def test_reconnecting_the_same_mailbox_replaces_the_stored_credential(
    db_session: Session,
) -> None:
    """Re-authorising is the documented cure for a revoked or expired consent, so the
    same mailbox must be allowed through -- and must overwrite, never accumulate."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    service.complete(code="c1", state=_state_of(service, _actor(user)), actor=_actor(user))
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    account.status = "revoked"
    account.last_error = "il consenso è stato revocato"
    account.last_error_at = datetime.now(UTC)
    db_session.flush()

    fake.refresh_token = "1//0gARenewedRefresh"
    read = service.complete(code="c2", state=_state_of(service, _actor(user)), actor=_actor(user))

    assert read.status == "active"
    assert read.last_error is None
    stored = db_session.execute(select(GoogleAccount)).scalars().one()
    assert stored.id == account.id
    assert unseal(stored.refresh_token_ciphertext, stored.refresh_token_nonce, KEY) == (
        "1//0gARenewedRefresh"
    )


def test_after_disconnecting_a_different_mailbox_can_be_connected(db_session: Session) -> None:
    """The refusal above tells the user to disconnect first, so disconnecting has to
    actually work -- otherwise the instruction is a dead end and the row is a
    permanent lock on the wrong address."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    service.complete(code="c1", state=_state_of(service, _actor(user)), actor=_actor(user))
    service.disconnect(delete_messages=False, actor=_actor(user))

    fake.id_token = _id_token("999999", "someone.else@gmail.com")
    read = service.complete(code="c2", state=_state_of(service, _actor(user)), actor=_actor(user))

    assert read.email_address == "someone.else@gmail.com"
    assert read.status == "active"
    assert read.disconnected_at is None
    assert len(_accounts(db_session)) == 1


def test_a_grant_without_an_identity_is_refused(db_session: Session) -> None:
    """`openid`+`email` are requested precisely so the mailbox can be named. A token
    response with no usable ID token leaves nothing to compare on a later reconnection,
    so it is refused rather than stored with an empty address."""
    user = _user(db_session)
    fake = _fake()
    fake.id_token = ""
    service = _service(db_session, fake)

    with pytest.raises(Conflict):
        service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    assert _accounts(db_session) == []


# --- what the refusals may say -------------------------------------------------------


def test_the_refusal_of_an_invalid_state_names_no_mailbox_at_all(db_session: Session) -> None:
    """The mismatch refusal above names both addresses, and may: by then the caller has
    redeemed a state bound to their own user id, so they own the account and can read
    the address on their own settings page anyway. Everything *before* that proof --
    an unknown, expired, replayed or foreign state -- must give away nothing, or the
    callback becomes an oracle for "which mailbox is connected here"."""
    owner = _user(db_session, "owner@example.it")
    other = _user(db_session, "other@example.it")
    fake = _fake()
    service = _service(db_session, fake)
    service.complete(code="c1", state=_state_of(service, _actor(owner)), actor=_actor(owner))

    probes: list[Conflict] = []
    for state in ("a-jti-nobody-ever-issued", _state_of(service, _actor(owner))):
        with pytest.raises(Conflict) as caught:
            service.complete(code="c2", state=state, actor=_actor(other))
        probes.append(caught.value)

    for refusal in probes:
        rendered = f"{refusal.message} {refusal.details} {refusal.args}"
        assert "ada@acme.it" not in rendered
        assert "owner@example.it" not in rendered
        assert "104729" not in rendered


@pytest.mark.parametrize("wrong_verifier", [True, False])
def test_no_refusal_carries_a_token_or_the_code_verifier(
    db_session: Session, wrong_verifier: bool
) -> None:
    """Every string a caller might print, on both of the failure paths that run after
    the token endpoint has been reached at all."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake)
    if wrong_verifier:
        fake.expected_code_challenge = _s256("not-the-verifier")
    else:
        service.complete(code="c1", state=_state_of(service, _actor(user)), actor=_actor(user))
        fake.id_token = _id_token("999999", "someone.else@gmail.com")

    verifiers = [
        row.code_verifier for row in db_session.execute(select(GoogleOAuthState)).scalars().all()
    ]
    with pytest.raises(DomainError) as caught:
        service.complete(code="c2", state=_state_of(service, _actor(user)), actor=_actor(user))

    error = caught.value
    rendered = " ".join(
        [error.message, str(error), repr(error), str(error.args), str(error.details)]
    )
    assert REFRESH not in rendered
    assert ACCESS not in rendered
    assert "the-client-secret" not in rendered
    for verifier in verifiers:
        assert verifier not in rendered


def test_a_stored_state_row_never_prints_its_verifier(db_session: Session) -> None:
    """`repr` of a mapped row lands in tracebacks and pytest dumps. The verifier is
    half of the PKCE proof, so it gets the same treatment as the tokens."""
    user = _user(db_session)
    _start(_service(db_session, _fake()), _actor(user))
    row = db_session.execute(select(GoogleOAuthState)).scalars().one()
    assert row.code_verifier not in repr(row)


# --- scopes, consent expiry, configuration and roles ---------------------------------


def test_a_partial_grant_is_stored_as_granted_and_stays_active(db_session: Session) -> None:
    """Spec 5.1: status describes the credential, capability is derived from the
    granted scopes at the point of use. Only gmail.send was granted here, so the
    account is healthy and it is *sync* that will refuse."""
    user = _user(db_session)
    fake = _fake()
    fake.granted_scopes = ("openid", "email", SCOPE_SEND)
    service = _service(db_session, fake)

    read = service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))

    assert read.status == "active"
    assert SCOPE_SEND in read.scopes_granted
    assert SCOPE_READONLY not in read.scopes_granted


def test_consent_expiry_is_set_only_while_the_client_is_unverified(db_session: Session) -> None:
    user = _user(db_session)
    service = _service(db_session, _fake(), _settings(google_app_unverified=True))
    read = service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))

    assert read.consent_expires_at is not None
    # Testing mode: Google expires a consumer refresh token seven days after consent.
    # Compared as a duration and not with `.days`, which floors: seven days minus the
    # microseconds this test itself takes reports six.
    remaining = read.consent_expires_at - datetime.now(UTC)
    assert timedelta(days=7) - timedelta(minutes=1) < remaining <= timedelta(days=7)


def test_a_verified_client_records_no_consent_expiry(db_session: Session) -> None:
    """The other half of the switch: a warning shown on a verified installation would
    be a false alarm every seven days for as long as the account lives."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    read = service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    assert read.consent_expires_at is None


def test_an_unconfigured_installation_refuses_to_start(db_session: Session) -> None:
    user = _user(db_session)
    bare = _settings(google_client_id="")
    with pytest.raises(Conflict, match="non è configurato"):
        _service(db_session, _fake(), bare).start(_actor(user))
    assert db_session.execute(select(GoogleOAuthState)).scalars().all() == []


def test_an_unconfigured_installation_refuses_the_callback_too(db_session: Session) -> None:
    """The redirect URI is a fixed, public URL. Turning Gmail off must close the
    callback as well, or the half of the flow an attacker can reach stays open."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    state = _state_of(service, _actor(user))
    bare = _service(db_session, _fake(), _settings(google_client_secret=""))
    with pytest.raises(Conflict, match="non è configurato"):
        bare.complete(code="c", state=state, actor=_actor(user))


def test_a_readonly_actor_can_neither_start_nor_disconnect(db_session: Session) -> None:
    user = _user(db_session, "reader@example.it", ruolo="readonly")
    service = _service(db_session, _fake())
    with pytest.raises(PermissionDenied):
        service.start(_actor(user, "readonly"))
    with pytest.raises(PermissionDenied):
        service.disconnect(delete_messages=False, actor=_actor(user, "readonly"))


# --- disconnect ----------------------------------------------------------------------


def _stored_message(session: Session, account: GoogleAccount, gmail_id: str) -> GmailMessage:
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id=gmail_id,
        gmail_thread_id="t1",
        direction="inbound",
        from_address="info@acme.it",
        to_addresses=[account.email_address],
        cc_addresses=[],
        subject="Oggetto",
        snippet="anteprima",
        internal_date=datetime.now(UTC),
        body_text="corpo",
        attachments=[],
    )
    session.add(message)
    session.flush()
    return message


def test_disconnect_overwrites_the_credential_rather_than_dereferencing_it(
    db_session: Session,
) -> None:
    """Leaving the ciphertext behind means the credential is still in every backup
    taken after the disconnect."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))

    service.disconnect(delete_messages=False, actor=_actor(user))

    account = db_session.execute(select(GoogleAccount)).scalars().one()
    assert account.refresh_token_ciphertext == b""
    assert account.refresh_token_nonce == b""
    assert account.status == "disconnected"
    assert account.disconnected_at is not None
    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "gmail.account_scollegato" in kinds


def test_disconnect_records_the_choice_about_the_stored_messages(db_session: Session) -> None:
    """The choice is audited because it is destructive and irreversible, and the count
    goes into the same entry: it is the only place the size of what was destroyed
    survives the destruction."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    _stored_message(db_session, account, "m1")

    service.disconnect(delete_messages=True, actor=_actor(user))

    payload = (
        db_session.execute(
            select(Activity.payload).where(Activity.kind == "gmail.account_scollegato")
        )
        .scalars()
        .one()
    )
    assert payload["messaggi_cancellati"] is True
    assert payload["messaggi_cancellati_conteggio"] == 1


def test_disconnecting_with_deletion_removes_the_messages_and_their_links(
    db_session: Session,
) -> None:
    """The other half of the choice above, which until this task did nothing at all.
    The links go with the messages by ON DELETE CASCADE rather than by a second
    statement: a link to a message that no longer exists is a row nothing can render
    and nothing would ever clean up."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    message = _stored_message(db_session, account, "m1")
    db_session.add(
        GmailMessageLink(gmail_message_id=message.id, entity_type="customer", entity_id=uuid4())
    )
    db_session.flush()

    service.disconnect(delete_messages=True, actor=_actor(user))

    assert db_session.execute(select(GmailMessage)).scalars().all() == []
    assert db_session.execute(select(GmailMessageLink)).scalars().all() == []


def test_disconnecting_without_deletion_keeps_the_correspondence(db_session: Session) -> None:
    """Deleting a customer's correspondence because a token expired would be a
    disaster. The default is to keep it, and the credential is destroyed either way."""
    user = _user(db_session)
    service = _service(db_session, _fake())
    service.complete(code="c", state=_state_of(service, _actor(user)), actor=_actor(user))
    account = db_session.execute(select(GoogleAccount)).scalars().one()
    _stored_message(db_session, account, "m1")

    service.disconnect(delete_messages=False, actor=_actor(user))

    kept = db_session.execute(select(GmailMessage.gmail_message_id)).scalars().all()
    assert kept == ["m1"]


def test_disconnecting_nothing_is_a_refusal_and_not_a_silent_success(
    db_session: Session,
) -> None:
    user = _user(db_session)
    with pytest.raises(Conflict):
        _service(db_session, _fake()).disconnect(delete_messages=False, actor=_actor(user))


# --- the state table's own housekeeping ----------------------------------------------


def test_prune_states_removes_the_expired_rows_and_only_those(db_session: Session) -> None:
    """`refresh_tokens` has this same problem and, per residuo R8, no pruning at all --
    this table does not repeat it."""
    user = _user(db_session)
    now = datetime.now(UTC)
    repo = GmailRepository(db_session)
    for offset in (-timedelta(minutes=10), -timedelta(seconds=1), timedelta(minutes=5)):
        repo.add_state(
            GoogleOAuthState(
                jti=f"jti-{offset.total_seconds()}",
                code_verifier="v" * 43,
                user_id=user.id,
                expires_at=now + offset,
            )
        )

    assert repo.prune_states(now) == 2
    survivors = db_session.execute(select(GoogleOAuthState.jti)).scalars().all()
    assert survivors == ["jti-300.0"]


# --- a space on the root's client (REB-394) -------------------------------------------


def _space_settings() -> Settings:
    """What a space called `studio` sees while the root lends its client."""
    root = _settings(google_shared_client=True, public_url="https://pigro.example")
    return space_base_settings(root, "studio")


def test_a_space_on_the_root_client_goes_to_google_with_the_root_callback_and_its_slug(
    db_session: Session,
) -> None:
    """The redirect URI is the root's own, which is the one address the root's client
    has registered; the state carries the space's name for the root to relay it, and
    the jti behind it is the row this space's database holds."""
    user = _user(db_session)
    fake = _fake()
    service = _service(db_session, fake, _space_settings())
    _, query = _start(service, _actor(user))

    assert query["redirect_uri"] == ["https://pigro.example/api/gmail/oauth/callback"]
    assert query["client_id"] == ["cid.apps.googleusercontent.com"]
    state = query["state"][0]
    slug, _, jti = state.partition(".")
    assert slug == "studio"
    stored = db_session.execute(select(GoogleOAuthState.jti)).scalars().all()
    assert stored == [jti]

    read = service.complete(code="4/0A-code", state=state, actor=_actor(user))
    assert read.status == "active"
    # Google refuses an exchange whose redirect URI differs from the consent's.
    exchanges = [
        body for body in _token_bodies(fake) if body.get("grant_type") == ["authorization_code"]
    ]
    assert exchanges[0]["redirect_uri"] == ["https://pigro.example/api/gmail/oauth/callback"]
    # Sealed with the space's derived key, which the root's key does not open.
    account = _accounts(db_session)[0]
    space_key = decode_google_token_key(_space_settings())
    ciphertext, nonce = account.refresh_token_ciphertext, account.refresh_token_nonce
    assert unseal(ciphertext, nonce, space_key) == REFRESH
    with pytest.raises(Conflict):
        unseal(ciphertext, nonce, KEY)


def test_the_root_lending_its_client_keeps_its_own_consent_as_it_was(
    db_session: Session,
) -> None:
    """The setting changes what the spaces see, never the root: a bare jti, its own
    callback, its own key."""
    root = _settings(google_shared_client=True, public_url="https://pigro.example")
    assert space_base_settings(root, None) is root
    user = _user(db_session)
    service = _service(db_session, _fake(), root)
    _, query = _start(service, _actor(user))
    assert query["redirect_uri"] == ["https://pigro.example/api/gmail/oauth/callback"]
    assert "." not in query["state"][0]
    service.complete(code="4/0A-code", state=query["state"][0], actor=_actor(user))
    account = _accounts(db_session)[0]
    assert unseal(account.refresh_token_ciphertext, account.refresh_token_nonce, KEY) == REFRESH


def test_a_space_refuses_a_state_that_does_not_carry_its_own_prefix(
    db_session: Session,
) -> None:
    """A state minted here and handed back without the prefix, or under another space's
    name, is refused with the sentence an unknown jti gets, before any row is touched:
    the real state is still there for the consent that carries it."""
    user = _user(db_session)
    service = _service(db_session, _fake(), _space_settings())
    state = _state_of(service, _actor(user))
    jti = state.partition(".")[2]

    for forged in (jti, f"altro-studio.{jti}", "studio."):
        with pytest.raises(Conflict) as caught:
            service.complete(code="4/0A-code", state=forged, actor=_actor(user))
        assert "non è più valida" in caught.value.message
    assert _accounts(db_session) == []

    assert service.complete(code="4/0A-code", state=state, actor=_actor(user)).status == "active"
