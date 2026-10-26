"""The Slack credential's own refusals, in the sentence the admin reads.

On the model of `drive/errors.py`: the *type* survives from the socket to the caller so that
a reaction can match on it (a revoked token is marked on the row and ends the pass for the
space, an outage is waited out), and the *sentence* names Slack and the place to fix it,
never a token, never Slack's body. `details` carries the Slack error code, which is a fixed
word (`invalid_auth`), and the method, nothing else.
"""

from pigrocrm.core.errors import Conflict

SLACK_ENTITY = "slack_workspace"
_RECONNECT = "ricollega Slack da Impostazioni → Slack"

# What Slack answers when the token itself is the problem: the app was uninstalled, the token
# revoked, the workspace deactivated. Terminal, as `invalid_grant` is for Google: retrying
# repeats the refusal, and only a new installation cures it.
DEAD_TOKEN_CODES = frozenset(
    {
        "invalid_auth",
        "token_revoked",
        "account_inactive",
        "not_authed",
        "token_expired",
        "team_access_not_granted",
        "access_denied",
    }
)


class SlackTokenRevoked(Conflict):
    """The bot token no longer works. Do not retry and do not skip silently."""

    def __init__(self, code: str, team_name: str | None = None) -> None:
        name = f" per {team_name}" if team_name else ""
        super().__init__(
            SLACK_ENTITY,
            f"Slack ha revocato l'accesso di PigroCRM{name}: {_RECONNECT}",
            error=code,
        )


class SlackRefused(Conflict):
    """Slack answered `ok: false` with a code that is not about the token: a channel that
    does not exist, a bot that is not in it, a missing scope. `code` is Slack's fixed word,
    which is what the caller matches on and what the settings page may show."""

    def __init__(self, method: str, code: str) -> None:
        super().__init__(
            SLACK_ENTITY,
            f"Slack ha rifiutato la richiesta ({code})",
            error=code,
            method=method,
        )
        self.code = code
        self.method = method


def slack_unavailable(status: int) -> Conflict:
    """Transient, after every retry was spent: wait and try again, as opposed to
    re-installing. A plain `Conflict`, since nothing matches on it beyond reporting it."""
    return Conflict(
        SLACK_ENTITY,
        f"Slack non ha risposto correttamente (codice {status}). Riprova più tardi",
        status=status,
    )
