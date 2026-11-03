"""Opening the Google refresh token a mailbox or a Drive account stored.

Gmail's sync, send and attachment readers and Drive's transport all start the same way:
unseal the stored refresh token with the installation's key, then exchange it for an
access token. The refusal of `unseal` is generic (it serves Slack and the space settings
too), so it is re-worded here for the one instruction that fixes a Google credential.
"""

from pigrocrm.core.config import Settings, decode_google_token_key
from pigrocrm.core.crypto import unseal
from pigrocrm.core.errors import Conflict


def open_refresh_token(ciphertext: bytes, nonce: bytes, settings: Settings) -> str:
    """The plaintext refresh token. `Conflict` when it cannot be opened with
    `PIGROCRM_GOOGLE_TOKEN_KEY`; `ValueError` (from the key decoder) when that key is
    missing or malformed, which is a deployment fault and not a user's."""
    try:
        return unseal(ciphertext, nonce, decode_google_token_key(settings))
    except Conflict as exc:
        raise Conflict(
            "google_account",
            "il refresh token memorizzato non è decifrabile: verifica "
            "PIGROCRM_GOOGLE_TOKEN_KEY, oppure ricollega la casella",
        ) from exc
