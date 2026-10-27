"""REB-829: the AES-GCM helper for the model providers' keys."""

import base64
import os

import pytest

from rebase_core.config import Settings
from rebase_core.errors import DomainError, SecretsKeyInvalid, SecretsKeyMissing
from rebase_core.secrets import last_four, seal, secrets_key_bytes, unseal

KEY = os.urandom(32)


def test_seal_unseal_round_trip() -> None:
    assert unseal(*seal("sk-abc", KEY), KEY) == "sk-abc"


def test_nonce_is_fresh_per_call() -> None:
    first_ciphertext, first_nonce = seal("sk-abc", KEY)
    second_ciphertext, second_nonce = seal("sk-abc", KEY)
    assert first_nonce != second_nonce
    assert first_ciphertext != second_ciphertext


def test_wrong_key_raises_without_leaking() -> None:
    ciphertext, nonce = seal("sk-abc", KEY)
    with pytest.raises(SecretsKeyInvalid) as raised:
        unseal(ciphertext, nonce, os.urandom(32))
    assert "sk-abc" not in str(raised.value)
    assert "sk-abc" not in repr(raised.value)
    assert "REBASE_SECRETS_KEY" in str(raised.value)
    assert isinstance(raised.value, DomainError)


def test_altered_ciphertext_and_short_key_raise_the_same_error() -> None:
    ciphertext, nonce = seal("sk-abc", KEY)
    with pytest.raises(SecretsKeyInvalid):
        unseal(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), nonce, KEY)
    with pytest.raises(SecretsKeyInvalid):
        unseal(ciphertext, nonce, b"short")


def test_missing_key_refused() -> None:
    with pytest.raises(SecretsKeyMissing) as raised:
        secrets_key_bytes(Settings(secrets_key=""))
    assert isinstance(raised.value, DomainError)


@pytest.mark.parametrize(
    "value",
    [
        "not base64 at all!",
        base64.b64encode(bytes(16)).decode(),
        base64.b64encode(bytes(33)).decode(),
    ],
)
def test_key_that_is_not_32_bytes_of_base64_refused_without_echo(value: str) -> None:
    with pytest.raises(SecretsKeyMissing) as raised:
        secrets_key_bytes(Settings(secrets_key=value))
    assert value not in str(raised.value)


def test_valid_key_is_decoded() -> None:
    assert secrets_key_bytes(Settings(secrets_key=base64.b64encode(KEY).decode())) == KEY


def test_secrets_key_and_github_token_stay_out_of_repr() -> None:
    settings = Settings(secrets_key="c2VjcmV0", github_token="ghp_abcdef")
    assert "c2VjcmV0" not in repr(settings)
    assert "ghp_abcdef" not in repr(settings)


def test_last_four() -> None:
    assert last_four("sk-ant-xyz1234") == "1234"
