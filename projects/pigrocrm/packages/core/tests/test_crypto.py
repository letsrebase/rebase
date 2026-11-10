import base64

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.crypto import decode_aes256_key, seal, unseal
from pigrocrm.core.errors import Conflict
from pigrocrm.core.google.credential import open_refresh_token

KEY = b"k" * 32
OTHER_KEY = b"j" * 32
TOKEN = "1//0gSecretRefreshTokenValue-XYZ"


def test_a_sealed_token_comes_back_identical() -> None:
    ciphertext, nonce = seal(TOKEN, KEY)
    assert unseal(ciphertext, nonce, KEY) == TOKEN


def test_the_ciphertext_never_contains_the_plaintext() -> None:
    ciphertext, _ = seal(TOKEN, KEY)
    assert TOKEN.encode() not in ciphertext


def test_two_seals_of_the_same_token_differ() -> None:
    """A fresh nonce per seal. Reusing a nonce with AES-GCM is a catastrophic
    failure, not a weakness: two messages under one nonce leak their XOR and forge
    the authenticator."""
    first, first_nonce = seal(TOKEN, KEY)
    second, second_nonce = seal(TOKEN, KEY)
    assert first_nonce != second_nonce
    assert first != second


def test_the_wrong_key_is_refused_not_garbled() -> None:
    ciphertext, nonce = seal(TOKEN, KEY)
    with pytest.raises(Conflict):
        unseal(ciphertext, nonce, OTHER_KEY)


def test_a_google_refresh_token_that_cannot_be_opened_names_the_google_key() -> None:
    """`unseal`'s refusal is generic because Slack and the space settings share it; the
    Google callers get the sentence that tells the operator which variable to check."""
    ciphertext, nonce = seal(TOKEN, KEY)
    settings = Settings(
        jwt_secret="x" * 32,
        google_token_key=base64.b64encode(OTHER_KEY).decode(),
        _env_file=None,  # type: ignore[call-arg]
    )
    with pytest.raises(Conflict) as caught:
        open_refresh_token(ciphertext, nonce, settings)
    assert "PIGROCRM_GOOGLE_TOKEN_KEY" in caught.value.message


def test_a_google_refresh_token_opens_with_the_configured_key() -> None:
    ciphertext, nonce = seal(TOKEN, KEY)
    settings = Settings(
        jwt_secret="x" * 32,
        google_token_key=base64.b64encode(KEY).decode(),
        _env_file=None,  # type: ignore[call-arg]
    )
    assert open_refresh_token(ciphertext, nonce, settings) == TOKEN


def test_a_key_variable_must_hold_thirty_two_bytes_of_base64() -> None:
    assert decode_aes256_key(base64.b64encode(KEY).decode(), "SOME_KEY") == KEY
    for bad in ["", "not-base64!!", base64.b64encode(b"short").decode()]:
        with pytest.raises(ValueError, match="SOME_KEY") as caught:
            decode_aes256_key(bad, "SOME_KEY")
        # The message names the variable and never the value, the cause included.
        if bad:
            assert bad not in str(caught.value) + repr(caught.value.__cause__)


def test_a_tampered_ciphertext_is_refused() -> None:
    ciphertext, nonce = seal(TOKEN, KEY)
    tampered = bytes([ciphertext[0] ^ 0x01]) + ciphertext[1:]
    with pytest.raises(Conflict):
        unseal(tampered, nonce, KEY)


def test_a_truncated_nonce_is_refused_rather_than_raising_ValueError() -> None:
    """AESGCM raises `ValueError`, not `InvalidTag`, on a nonce of the wrong length --
    a different exception type for what is, from the caller's side, the same fact: the
    row cannot be decrypted. A row whose nonce column was truncated by a bad backup
    restore must reach the operator as the same `Conflict` as a wrong key, not as an
    unhandled `ValueError` that the API renders as a 500 with a traceback."""
    ciphertext, nonce = seal(TOKEN, KEY)
    with pytest.raises(Conflict):
        unseal(ciphertext, nonce[:4], KEY)


def test_no_failure_path_puts_the_token_or_the_key_in_the_message() -> None:
    """The whole point of encrypting at rest is undone by one traceback that quotes the
    material. `str(exc)` is what `logging.exception` and pytest's own failure dump both
    render, so it is asserted directly and not only through `.message`."""
    ciphertext, nonce = seal(TOKEN, KEY)
    for args in [(ciphertext, nonce, OTHER_KEY), (b"\x00" * len(ciphertext), nonce, KEY)]:
        with pytest.raises(Conflict) as caught:
            unseal(*args)
        rendered = f"{caught.value.message} {caught.value.details} {caught.value} {caught.value!r}"
        rendered += f" {caught.value.args}"
        assert TOKEN not in rendered
        assert KEY.hex() not in rendered
        assert OTHER_KEY.hex() not in rendered
        assert ciphertext.hex() not in rendered
        assert nonce.hex() not in rendered


def test_the_settings_object_hides_both_halves_of_the_credential() -> None:
    """B1-2 declared `google_client_secret` and `google_token_key` as `repr=False`
    precisely so that a `Settings` in a traceback frame does not print the key that
    decrypts every stored refresh token. Asserted here, next to the cipher that key
    feeds, because the two facts are one guarantee: removing the flag would silently
    move the secret from "outside the database" to "in every log line".
    """
    settings = Settings(
        google_client_secret="GOCSPX-super-secret-value",
        google_token_key="a2V5LWJ5dGVzLWdvLWhlcmUtdGhpcnR5LXR3bw==",
    )
    rendered = f"{settings!r} {settings}"
    assert "GOCSPX-super-secret-value" not in rendered
    assert "a2V5LWJ5dGVzLWdvLWhlcmUtdGhpcnR5LXR3bw==" not in rendered
