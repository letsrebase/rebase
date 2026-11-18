"""Signing in with GitHub or Google, the parts that need no database (REB-1137): the return
path guard, the cookie's seal, the authorize URL, and what each provider's answers come to."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from rebase_core.config import Settings
from rebase_core.http import NETWORK_ERROR_STATUS, HttpCall
from rebase_core.oauth import (
    Attempt,
    MissedAccount,
    OAuthService,
    OAuthUnavailable,
    open_attempt,
    open_miss,
    safe_next,
    seal_attempt,
    seal_miss,
    utm_of,
)
from rebase_core.signup_schemas import SignupUtm


def _settings(**values: str) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _both() -> Settings:
    return _settings(
        github_oauth_client_id="gh-id",
        github_oauth_client_secret="gh-secret",
        google_oauth_client_id="g-id",
        google_oauth_client_secret="g-secret",
        hub_url="https://preview.letsrebase.com/hub",
    )


def _never(*_: object) -> tuple[int, bytes]:
    raise AssertionError("no call expected")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "me",
        "//evil.example",
        "///evil.example",
        "https://evil.example/",
        "http://evil.example",
        "javascript:alert(1)",
        "/\\evil.example",
        "/me/../admin",
        "/..",
        "/me?x=1",
        "/me#frag",
        "/me%2f%2fevil",
        "/cli?code=0000-0000",
        "/cli?code=ABCD-EFGH&x=1",
        "/cli?code=abcd-efgh",
        "/cli?next=https://evil.example",
        "/cli?code=ABCD_EFGH",
        "/" + "a" * 80,
        "/me\r\nSet-Cookie: x=1",
    ],
)
def test_a_return_path_that_is_not_a_page_of_the_hub_is_dropped(raw: str | None) -> None:
    assert safe_next(raw) is None


@pytest.mark.parametrize(
    "raw", ["/me", "/admin", "/me/cloud", "/admin/freelance", "/cli?code=ABCD-EFGH", "/cli"]
)
def test_a_page_of_the_hub_is_kept(raw: str) -> None:
    assert safe_next(raw) == raw


def test_the_campaign_is_cut_to_what_is_stored_and_never_refused() -> None:
    utm = utm_of(
        {
            "utm_source": "email",
            "utm_campaign": "x" * 500,
            "origin": "home",
            "next": "/me",
            "other": "ignored",
        }
    )
    assert utm.utm_source == "email"
    assert utm.utm_campaign is not None and len(utm.utm_campaign) == 200
    assert utm.origin == "home"
    assert utm_of({"origin": "Not A Slug!"}).is_empty()


def test_the_cookie_round_trips_and_a_forged_one_opens_nothing() -> None:
    attempt = Attempt(
        "github", "s" * 43, "v" * 64, "/cli?code=ABCD-EFGH", SignupUtm(utm_source="m")
    )
    assert open_attempt(seal_attempt(attempt)) == attempt
    for bad in (None, "", "%%%", "e30", base64.urlsafe_b64encode(b"[1]").decode()):
        assert open_attempt(bad) is None
    other = base64.urlsafe_b64encode(
        json.dumps({"p": "apple", "s": "a", "v": "b", "n": None, "u": {}}).encode()
    ).decode()
    assert open_attempt(other) is None


def test_a_return_path_forged_into_the_cookie_is_dropped_on_the_way_out() -> None:
    forged = base64.urlsafe_b64encode(
        json.dumps(
            {"p": "google", "s": "a", "v": "b", "n": "https://evil.example/", "u": {}}
        ).encode()
    ).decode()
    opened = open_attempt(forged)
    assert opened is not None and opened.next_path is None


def test_the_missed_address_round_trips() -> None:
    assert open_miss(seal_miss(_miss())) == _miss()
    assert open_miss("nonsense") is None


def _miss() -> MissedAccount:
    return MissedAccount(provider="google", email="ada@studio.it")


def test_a_provider_is_on_only_with_its_id_and_its_secret() -> None:
    half = OAuthService(_settings(github_oauth_client_id="only-an-id"), _never)
    assert half.providers_on().model_dump() == {"github": False, "google": False}
    both = OAuthService(_both(), _never)
    assert both.providers_on().model_dump() == {"github": True, "google": True}


def test_the_redirect_uri_comes_from_the_configured_hub_not_from_anything_a_request_says() -> None:
    assert (
        OAuthService(_both(), _never).redirect_uri("github")
        == "https://preview.letsrebase.com/api/hub/auth/oauth/github/callback"
    )
    assert (
        OAuthService(_settings(), _never).redirect_uri("google")
        == "https://letsrebase.com/api/hub/auth/oauth/google/callback"
    )


@pytest.mark.parametrize(
    ("provider", "host"), [("github", "github.com"), ("google", "accounts.google.com")]
)
def test_the_authorize_url_carries_state_and_an_s256_challenge_of_the_verifier(
    provider: str, host: str
) -> None:
    begun = OAuthService(_both(), _never).begin(provider, None, SignupUtm())
    url = urlsplit(begun.url)
    query = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert url.netloc == host
    assert query["state"] == begun.attempt.state
    assert query["code_challenge_method"] == "S256"
    digest = hashlib.sha256(begun.attempt.verifier.encode()).digest()
    assert query["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert query["redirect_uri"].startswith("https://preview.letsrebase.com/api/hub/")
    assert "client_secret" not in query
    assert query["scope"] == ("user:email" if provider == "github" else "openid email profile")
    assert len(begun.attempt.state) >= 43 and len(begun.attempt.verifier) >= 43


def _answers(*replies: tuple[int, object]) -> HttpCall:
    queue = list(replies)

    def call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        status, payload = queue.pop(0)
        return status, json.dumps(payload).encode()

    return call


def _attempt(provider: str) -> Attempt:
    return Attempt(provider, "state", "verifier", None, SignupUtm())


def test_github_keeps_every_verified_address_the_primary_first_and_drops_the_rest() -> None:
    service = OAuthService(
        _both(),
        _answers(
            (200, {"access_token": "t"}),
            (
                200,
                [
                    {"email": "Old@Studio.it", "verified": True, "primary": False},
                    {"email": "nope@studio.it", "verified": False, "primary": False},
                    {"email": "ada@studio.it", "verified": True, "primary": True},
                    {"email": "1+ada@users.noreply.github.com", "verified": True, "primary": False},
                    {"email": "old@studio.it", "verified": True, "primary": False},
                    {"verified": True},
                    "junk",
                ],
            ),
        ),
    )
    assert service.verified_addresses(_attempt("github"), "code") == [
        "ada@studio.it",
        "old@studio.it",
    ]


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"email": "Ada@Studio.it", "email_verified": True}, ["ada@studio.it"]),
        ({"email": "ada@studio.it", "email_verified": "true"}, ["ada@studio.it"]),
        ({"email": "ada@studio.it", "email_verified": False}, []),
        ({"email": "ada@studio.it", "email_verified": "false"}, []),
        ({"email": "ada@studio.it"}, []),
        ({"email_verified": True}, []),
    ],
)
def test_google_needs_a_verified_address(info: dict[str, object], expected: list[str]) -> None:
    service = OAuthService(_both(), _answers((200, {"access_token": "t"}), (200, info)))
    assert service.verified_addresses(_attempt("google"), "code") == expected


@pytest.mark.parametrize(
    "replies",
    [
        [(400, {"error": "invalid_grant"})],
        [(200, {"error": "bad_verification_code"})],
        [(200, {"access_token": ""})],
        [(200, ["not", "an", "object"])],
        [(200, {"access_token": "t"}), (401, {})],
        [(200, {"access_token": "t"}), (200, {"not": "a list"})],
    ],
)
def test_a_provider_that_does_not_answer_usably_is_a_failure_that_names_a_step(
    replies: list[tuple[int, object]],
) -> None:
    service = OAuthService(_both(), _answers(*replies))
    with pytest.raises(OAuthUnavailable):
        service.verified_addresses(_attempt("github"), "code")


def test_a_network_failure_is_a_failure_too() -> None:
    def down(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        return NETWORK_ERROR_STATUS, b""

    with pytest.raises(OAuthUnavailable):
        OAuthService(_both(), down).verified_addresses(_attempt("google"), "code")
