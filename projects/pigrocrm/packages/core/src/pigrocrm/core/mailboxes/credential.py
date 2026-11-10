"""Sealing and opening the password of an IMAP mailbox (REB-1015).

The plaintext exists for the length of one call: `open_password` hands it to the connection and
the caller keeps no copy. `unseal` is `core/crypto.py`'s and its refusal is generic, so it is
re-worded here to name `PIGROCRM_MAILBOX_TOKEN_KEY`, the instruction that fixes it.
"""

from pigrocrm.core.config import Settings, decode_mailbox_token_key
from pigrocrm.core.crypto import seal, unseal
from pigrocrm.core.errors import Conflict
from pigrocrm.core.mailboxes.models import Mailbox

MAILBOX_ENTITY = "mailbox"
NOT_CONFIGURED = (
    "questa installazione non ha una chiave per le password delle caselle: "
    "imposta PIGROCRM_MAILBOX_TOKEN_KEY"
)


def seal_password(password: str, settings: Settings) -> tuple[bytes, bytes]:
    """`(ciphertext, nonce)`, or `Conflict` when the installation has no valid key."""
    try:
        return seal(password, decode_mailbox_token_key(settings))
    except ValueError as exc:
        raise Conflict(MAILBOX_ENTITY, NOT_CONFIGURED) from exc


def open_password(mailbox: Mailbox, settings: Settings) -> str:
    """The mailbox's password. `Conflict` when the row holds none (a disconnected mailbox) or
    when it cannot be opened with this installation's key (a rotated one): in both the person
    types the password again."""
    if mailbox.password_ciphertext is None or mailbox.password_nonce is None:
        raise Conflict(MAILBOX_ENTITY, "la casella non è collegata")
    try:
        return unseal(
            mailbox.password_ciphertext,
            mailbox.password_nonce,
            decode_mailbox_token_key(settings),
        )
    except (Conflict, ValueError) as exc:
        raise Conflict(
            MAILBOX_ENTITY,
            "la password memorizzata non è decifrabile: verifica PIGROCRM_MAILBOX_TOKEN_KEY, "
            "oppure inserisci di nuovo la password",
        ) from exc
