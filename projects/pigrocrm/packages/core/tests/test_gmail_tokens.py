import base64
import json
from uuid import uuid4

import pytest
from fakes.fake_gmail import FakeGmail

from pigrocrm.core.gmail.errors import CredentialRevoked, GmailUnavailable
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES
from pigrocrm.core.gmail.tokens import GoogleTokenClient, TokenGrant
from pigrocrm.core.gmail.transport import GmailTransport

ACCOUNT = uuid4()
REFRESH = "1//0gRefreshTokenValue"
SECRET = "the-client-secret"


def _client(fake: FakeGmail, clock: object | None = None) -> GoogleTokenClient:
    return GoogleTokenClient(
        client_id="cid.apps.googleusercontent.com",
        client_secret=SECRET,
        transport=GmailTransport(http=fake, sleep=lambda _: None),
        clock=clock or (lambda: 1000.0),  # type: ignore[arg-type]
    )


def _id_token(sub: str, email: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"sub": sub, "email": email}).encode())
    return f"header.{claims.decode().rstrip('=')}.signature"


def test_one_exchange_serves_every_call_within_the_token_lifetime() -> None:
    """The previous system performed two OAuth exchanges to send a single invoice email, because it
    threw `expires_in` away. The cache is not an optimisation: it is why a flaky
    network stops presenting itself twice per operation."""
    fake = FakeGmail(expires_in=3599)
    client = _client(fake)
    tokens = {
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
        for _ in range(5)
    }
    assert tokens == {fake.access_token}
    assert fake.token_requests == 1


def test_the_cache_expires_on_the_servers_own_expires_in() -> None:
    now = [1000.0]
    fake = FakeGmail(expires_in=3599)
    client = _client(fake, clock=lambda: now[0])
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    now[0] += 3000  # still inside the window, minus the safety margin
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.token_requests == 1
    now[0] += 1000  # past it
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.token_requests == 2


def test_a_short_lived_token_is_refreshed_before_it_actually_expires() -> None:
    """The margin, tested as behaviour rather than as a constant restated. Google's
    own `expires_in` is 60 seconds here, so *every* second of that life is inside the
    margin: a client that cached on the raw `expires_in` would serve the same token
    twice, and the second call would carry a token that expires mid-request."""
    now = [1000.0]
    fake = FakeGmail(expires_in=60)
    client = _client(fake, clock=lambda: now[0])
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    now[0] += 1
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.token_requests == 2


def test_two_accounts_do_not_share_a_cache_entry() -> None:
    fake = FakeGmail()
    client = _client(fake)
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    client.access_token(account_id=uuid4(), email_address="c@d.it", refresh_token="1//other")
    assert fake.token_requests == 2


def test_forget_drops_the_cached_token_for_that_account_only() -> None:
    """Re-authorisation and disconnection both invalidate a cached token: after either,
    the cached value belongs to a grant that no longer applies."""
    other = uuid4()
    fake = FakeGmail()
    client = _client(fake)
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    client.access_token(account_id=other, email_address="c@d.it", refresh_token="1//other")
    client.forget(ACCOUNT)
    client.access_token(account_id=other, email_address="c@d.it", refresh_token="1//other")
    assert fake.token_requests == 2
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.token_requests == 3


def test_invalid_grant_is_terminal_and_is_never_retried() -> None:
    fake = FakeGmail(revoked=True)
    client = _client(fake)
    with pytest.raises(CredentialRevoked) as caught:
        client.access_token(account_id=ACCOUNT, email_address="ada@acme.it", refresh_token=REFRESH)
    assert "ada@acme.it" in caught.value.message
    assert "revocato" in caught.value.message
    # Exactly one attempt. Retrying an invalid_grant is a bug: it will never succeed,
    # and retrying only delays telling the user something they must act on.
    assert fake.token_requests == 1


def test_a_transient_failure_is_not_a_revocation() -> None:
    """The previous system's defect, from the other side: a 503 and an `invalid_grant` produced the
    same screen and therefore the same wrong reaction. `GmailUnavailable` means wait,
    `CredentialRevoked` means re-consent, and the transport's retries are spent first."""
    fake = FakeGmail()
    fake.fail_with = [(503, b"{}", {})] * 4
    client = _client(fake)
    with pytest.raises(GmailUnavailable) as caught:
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert not isinstance(caught.value, CredentialRevoked)
    assert len(fake.requests) == 4


def test_a_failed_refresh_leaves_no_entry_behind_in_the_cache() -> None:
    """A cached empty string would be handed out as a bearer token for the rest of the
    hour, turning one transient failure into an hour of 401s."""
    fake = FakeGmail()
    fake.fail_with = [(503, b"{}", {})] * 4
    client = _client(fake)
    with pytest.raises(GmailUnavailable):
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert client._cache == {}
    fake.fail_with = []
    assert (
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
        == fake.access_token
    )


def test_a_200_without_an_access_token_is_a_failure_and_not_an_empty_bearer() -> None:
    """`{}` with a 200 is what a captive portal or a misrouted proxy answers. Treating
    it as a token caches the empty string and sends `Authorization: Bearer ` for an
    hour, which fails far away from here."""
    fake = FakeGmail()
    fake.fail_with = [(200, b"{}", {})]
    client = _client(fake)
    with pytest.raises(GmailUnavailable):
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert client._cache == {}


def test_a_revoked_grant_is_not_cached_as_a_failure_either() -> None:
    """A second call must ask again rather than replay a cached exception: the user may
    have re-consented between the two."""
    fake = FakeGmail(revoked=True)
    client = _client(fake)
    for _ in range(2):
        with pytest.raises(CredentialRevoked):
            client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.token_requests == 2


def test_no_error_path_leaks_the_refresh_token_or_the_client_secret() -> None:
    """Every rendering of the exception, not only `.message`: `str`, `repr` and `.args`
    are what a logger, a traceback and a pytest failure dump actually print."""
    fake = FakeGmail(revoked=True)
    client = _client(fake)
    with pytest.raises(CredentialRevoked) as caught:
        client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    error = caught.value
    rendered = " ".join(
        [error.message, repr(error.details), str(error), repr(error), repr(error.args)]
    )
    assert REFRESH not in rendered
    assert SECRET not in rendered
    # The cause travels in the traceback, so it is rendered too.
    assert REFRESH not in repr(error.__cause__)
    assert SECRET not in repr(error.__cause__)


def test_a_token_grant_never_prints_either_of_its_two_tokens() -> None:
    """`TokenGrant` is a dataclass, so it has a generated `repr`, so it prints itself
    into every traceback that has one in a frame -- and it carries both a refresh token
    (a long-lived credential to a whole mailbox) and an access token. Both are
    `repr=False`; the scopes, the subject and the address stay visible, because a
    connection that fails is unreadable without them."""
    grant = TokenGrant(
        access_token="ya29.the-access-token",
        refresh_token=REFRESH,
        scopes=REQUESTED_SCOPES,
        subject="104729",
        email_address="ada@acme.it",
        expires_in=3599,
    )
    printed = repr(grant)
    assert REFRESH not in printed
    assert "ya29.the-access-token" not in printed
    assert "ada@acme.it" in printed


def test_the_cache_never_prints_the_access_token_it_holds() -> None:
    """A cache is one more place a secret sits. The entry is a dataclass too, and
    `repr(self._cache)` is one `logger.debug` away from an hour-long bearer token in a
    log file."""
    fake = FakeGmail()
    client = _client(fake)
    client.access_token(account_id=ACCOUNT, email_address="a@b.it", refresh_token=REFRESH)
    assert fake.access_token not in repr(client._cache)


def test_exchange_code_reads_the_granted_scopes_and_the_subject() -> None:
    """Google may grant a subset of what was asked. What was *granted* is what gets
    recorded, because every feature checks the granted set, never the requested one."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = _id_token("104729", "ada@acme.it")
    grant = _client(fake).exchange_code(
        code="4/0A-code",
        code_verifier="v" * 43,
        redirect_uri="https://crm.example.it/api/gmail/oauth/callback",
    )
    assert grant.subject == "104729"
    assert grant.email_address == "ada@acme.it"
    assert grant.scopes == REQUESTED_SCOPES
    assert grant.refresh_token == fake.refresh_token
    assert grant.access_token == fake.access_token
    assert grant.expires_in == fake.expires_in


def test_exchange_code_sends_the_verifier_and_never_the_challenge() -> None:
    """PKCE is only proof of possession if the *verifier* reaches the token endpoint.
    Asserted on the request the fake received, not on the code that built it."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = _id_token("104729", "ada@acme.it")
    _client(fake).exchange_code(
        code="4/0A-code",
        code_verifier="v" * 43,
        redirect_uri="https://crm.example.it/api/gmail/oauth/callback",
    )
    sent = fake.requests[-1]
    assert sent.method == "POST"
    body = (sent.body or b"").decode()
    assert "code_verifier=" + "v" * 43 in body
    assert "grant_type=authorization_code" in body
    assert "redirect_uri=https%3A%2F%2Fcrm.example.it%2Fapi%2Fgmail%2Foauth%2Fcallback" in body


def test_an_exchange_does_not_seed_the_refresh_cache() -> None:
    """`exchange_code` has no account id: the row does not exist yet. Keying its access
    token on anything invented here would either collide or never be read again."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = _id_token("104729", "ada@acme.it")
    client = _client(fake)
    client.exchange_code(code="c", code_verifier="v" * 43, redirect_uri="https://x/y")
    assert client._cache == {}


def test_an_unreadable_id_token_yields_no_identity_rather_than_a_crash() -> None:
    """A malformed segment must not become a 500 on the callback: `subject` and
    `email_address` come back empty and B1-6 refuses on the empty subject instead."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = "not.a-valid-base64-!!!.token"
    grant = _client(fake).exchange_code(
        code="c", code_verifier="v" * 43, redirect_uri="https://x/y"
    )
    assert grant.subject == ""
    assert grant.email_address == ""


def test_a_grant_without_a_refresh_token_is_rejected_loudly() -> None:
    """Google omits the refresh token when `prompt=consent` was not sent and the user
    had already consented. Storing the row anyway would produce an account that can
    never refresh, failing only on the second day."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = _id_token("104729", "ada@acme.it")
    fake.omit_refresh_token = True
    with pytest.raises(CredentialRevoked):
        _client(fake).exchange_code(
            code="4/0A-code", code_verifier="v" * 43, redirect_uri="https://crm.example.it/x"
        )
