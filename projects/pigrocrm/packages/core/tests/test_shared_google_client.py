"""The root's Google client lent to the spaces (REB-394), without a database.

What a space sees (`space_base_settings`), what a space that brings its own client sees
(`apply_overrides`), how the root reads a slug off a returning `state` (`relay_slug`)
and the key each space's refresh tokens are sealed with (`space_token_key`).
"""

import base64

import pytest

from pigrocrm.core.config import Settings, decode_google_token_key, gmail_configured
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.crypto import seal, unseal
from pigrocrm.core.space_settings import apply_overrides
from pigrocrm.core.tenants import space_base_settings
from pigrocrm.core.tenants.google import oauth_state_prefix, relay_slug, space_token_key

ROOT_KEY = base64.b64encode(b"r" * 32).decode()


def _root(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "public_url": "https://pigro.example",
        "google_client_id": "root-client.apps",
        "google_client_secret": "root-secret",
        "google_token_key": ROOT_KEY,
        "google_app_unverified": True,
        "google_shared_client": True,
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_space_borrows_the_root_client_with_a_key_and_a_callback_of_its_own() -> None:
    space = space_base_settings(_root(), "studio")

    assert gmail_configured(space)
    assert space.google_client_id == "root-client.apps"
    assert space.google_client_secret == "root-secret"
    # The root declared its client unverified: the space's consents lapse in seven days
    # too, and the page has to say so.
    assert space.google_app_unverified is True
    # Google sends it back to the root's own address, and the state names the space.
    assert space.google_callback_base_url == "https://pigro.example"
    assert space.google_oauth_state_prefix == "studio."
    assert space.public_url == "https://pigro.example/studio"
    assert space.storage_backend == "local"

    key = space.google_token_key
    assert key and key != ROOT_KEY
    assert len(decode_google_token_key(space)) == 32
    # Derived, not random: the next request of the same space reads the same key.
    assert space_base_settings(_root(), "studio").google_token_key == key
    assert space_base_settings(_root(), "altro-studio").google_token_key != key


def test_the_root_is_untouched_and_relays_nothing_of_its_own() -> None:
    root = _root()
    assert space_base_settings(root, None) is root
    assert root.google_oauth_state_prefix == ""
    assert root.google_callback_base_url == ""


def test_without_the_setting_a_space_still_has_no_google() -> None:
    for root in (_root(google_shared_client=False), _root(google_client_id="")):
        space = space_base_settings(root, "studio")
        assert not gmail_configured(space)
        assert space.google_client_id == ""
        assert space.google_client_secret == ""
        assert space.google_token_key == ""
        assert space.google_app_unverified is False
        assert space.google_oauth_state_prefix == ""
        assert space.google_callback_base_url == ""


def test_a_space_with_its_own_client_borrows_nothing_from_the_root() -> None:
    """Impostazioni → Spazio still lets a space bring its own client. Then the root's
    secret must not authenticate somebody else's client id, and the root's callback is
    not an address their client registered."""
    base = space_base_settings(_root(), "studio")
    own_key = base64.b64encode(b"s" * 32).decode()

    half = apply_overrides(base, {"google_client_id": "own.apps"})
    assert half.google_client_id == "own.apps"
    assert half.google_client_secret == ""
    assert half.google_token_key == ""
    assert half.google_app_unverified is False
    assert half.google_oauth_state_prefix == ""
    assert half.google_callback_base_url == ""
    assert not gmail_configured(half)

    whole = apply_overrides(
        base,
        {
            "google_client_id": "own.apps",
            "google_client_secret": "own-secret",
            "google_token_key": own_key,
        },
    )
    assert gmail_configured(whole)
    assert whole.google_client_secret == "own-secret"
    assert whole.google_token_key == own_key
    assert whole.google_oauth_state_prefix == ""


def test_rows_left_from_a_client_the_space_no_longer_has_are_not_read() -> None:
    """A secret saved before its client id, or left behind when the client id was
    cleared, would sit on top of the root's client id and fail every exchange. Without
    a client id of its own, the space's Google rows are not read at all."""
    base = space_base_settings(_root(), "studio")
    effective = apply_overrides(
        base,
        {
            "google_client_secret": "stale",
            "google_token_key": base64.b64encode(b"s" * 32).decode(),
            "google_app_unverified": "false",
        },
    )
    assert effective.google_client_secret == "root-secret"
    assert effective.google_token_key == base.google_token_key
    assert effective.google_app_unverified is True
    assert effective.google_oauth_state_prefix == "studio."


def test_overriding_something_else_keeps_the_borrowed_client() -> None:
    base = space_base_settings(_root(), "studio")
    effective = apply_overrides(base, {"mcp_full_access": "true"})
    assert effective.google_client_id == "root-client.apps"
    assert effective.google_oauth_state_prefix == "studio."
    assert effective.google_token_key == base.google_token_key


def test_a_ciphertext_sealed_for_one_space_does_not_open_in_another() -> None:
    """What deriving buys over sharing the root's key: a row restored into the wrong
    space's database, or written there by mistake, fails closed."""
    studio = decode_google_token_key(space_base_settings(_root(), "studio"))
    other = decode_google_token_key(space_base_settings(_root(), "altro-studio"))
    ciphertext, nonce = seal("1//refresh", studio)
    assert unseal(ciphertext, nonce, studio) == "1//refresh"
    with pytest.raises(Conflict):
        unseal(ciphertext, nonce, other)
    with pytest.raises(Conflict):
        unseal(ciphertext, nonce, base64.b64decode(ROOT_KEY))


def test_a_root_key_that_is_not_one_derives_nothing() -> None:
    assert space_token_key("", "studio") == ""
    assert space_token_key("non-base64!", "studio") == ""
    assert space_token_key(base64.b64encode(b"short").decode(), "studio") == ""
    space = space_base_settings(_root(google_token_key="non-base64!"), "studio")
    assert not gmail_configured(space)


@pytest.mark.parametrize(
    ("state", "slug"),
    [
        ("studio.Zm9vYmFyLWp0aS1hYmNkZWY", "studio"),
        ("studio-rossi.abc_DEF-123", "studio-rossi"),
        # The root's own consent: a bare jti, never relayed.
        ("Zm9vYmFyLWp0aS1hYmNkZWY", None),
        (None, None),
        ("", None),
        ("studio.", None),
        (".jti", None),
        # A reserved word or a malformed name never becomes a path segment.
        ("app.jti", None),
        ("api.jti", None),
        ("Studio.jti", None),
        ("st.jti", None),
        ("../evil.jti", None),
        ("evil.com/x.jti", None),
        ("studio\n.jti", None),
    ],
)
def test_the_root_reads_a_slug_only_off_a_well_formed_state(
    state: str | None, slug: str | None
) -> None:
    assert relay_slug(state) == slug


def test_the_prefix_a_space_writes_is_the_one_the_root_reads() -> None:
    assert relay_slug(f"{oauth_state_prefix('studio-rossi')}jti") == "studio-rossi"
