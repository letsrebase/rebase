"""Opening the sealed bot token, and what a dead one does to the row.

The one place the plaintext exists outside a request that is about to use it: `bot_token`
returns it for the length of one call and the caller hands it to the client and keeps no
copy. `unseal` is `gmail/crypto.py`'s, but its refusal names `PIGROCRM_GOOGLE_TOKEN_KEY` and
a mailbox, which is the wrong instruction for Slack, so the refusal is re-worded here and
the cause kept in the traceback.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, decode_slack_token_key
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.crypto import unseal
from pigrocrm.core.slack.errors import SLACK_ENTITY, SlackTokenRevoked
from pigrocrm.core.slack.models import SlackWorkspace


def bot_token(workspace: SlackWorkspace, settings: Settings) -> str:
    """The workspace's bot token. `Conflict` when the row holds none (a disconnected
    workspace), when Slack revoked it (the stored sentence says so and where to fix it), or
    when it cannot be opened with this installation's key."""
    if workspace.status == "revoked":
        raise Conflict(
            SLACK_ENTITY,
            workspace.last_error
            or "Slack ha revocato l'accesso di PigroCRM: ricollega Slack da Impostazioni → Slack",
        )
    if workspace.status != "active" or not workspace.bot_token_ciphertext:
        raise Conflict(SLACK_ENTITY, "Slack non è collegato: collegalo da Impostazioni → Slack")
    try:
        return unseal(
            workspace.bot_token_ciphertext,
            workspace.bot_token_nonce,
            decode_slack_token_key(settings),
        )
    except (Conflict, ValueError) as exc:
        # `ValueError` is `decode_slack_token_key` refusing a key that is not 32 bytes of
        # base64: the same instruction to the operator, and it must not stop the pass.
        raise Conflict(
            SLACK_ENTITY,
            "il token Slack memorizzato non è decifrabile: verifica PIGROCRM_SLACK_TOKEN_KEY, "
            "oppure ricollega Slack",
        ) from exc


def mark_revoked(session: Session, workspace: SlackWorkspace, refusal: SlackTokenRevoked) -> None:
    """Records that Slack told us the token is dead, and commits it, so the next request
    and the next pass see a workspace to reconnect instead of trying again. The row keeps
    its team and its matches (`SlackWorkspace`'s docstring); the sealed token stays, since it
    is worthless now and a reconnect overwrites it."""
    named = SlackTokenRevoked(refusal.details["error"], workspace.team_name)
    workspace.status = "revoked"
    workspace.last_error = named.details["reason"]
    workspace.last_error_at = datetime.now(UTC)
    session.commit()
