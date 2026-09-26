"""The hub's domain errors: few, named, and rendered by each adapter in its own way."""

from typing import Any
from uuid import UUID


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    code = "not_found"

    def __init__(self, entity: str, identifier: str | UUID) -> None:
        super().__init__(
            f"{entity} {identifier} non trovato", entity=entity, identifier=str(identifier)
        )


class ValidationFailed(DomainError):
    code = "validation_failed"

    def __init__(self, entity: str, field: str, reason: str) -> None:
        super().__init__(f"{entity}.{field}: {reason}", entity=entity, field=field, reason=reason)


class InvalidState(DomainError):
    """An action the row's current state does not allow: cancelling a match that is no
    longer a draft, closing one that is not active. The API answers it with a 409, the
    status every `DomainError` without a mapping of its own already gets."""

    code = "invalid_state"


class DocumensoFailed(DomainError):
    """Documenso refused a call or did not answer (REB-387). `message` is a sentence for
    the admin, carrying Documenso's own `message` at most and never the stack trace its
    error bodies include; `detail` is for the log. A 502 in the API."""

    code = "documenso_failed"

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.detail = detail


class SigningUnavailable(DomainError):
    """This environment cannot send for signature: no Documenso, no mail, or no data for
    whoever signs for rebase. A 503 with the sentence, like the member area without a
    mail key."""

    code = "signing_unavailable"


class LlmUnavailable(DomainError):
    """Claude did not answer: a refusal fallback exhausted, a rate limit, an outage
    (`llm.py`, REB-508). `message` is the one sentence a page can show as it stands. A
    502 in the API; the MCP's `_call` renders it as the tool's own sentence rather than
    a traceback."""

    code = "llm_unavailable"


class TeamBuilderOff(DomainError):
    """The team builder cannot run in this environment: `REBASE_TEAM_BUILDER_ENABLED` is
    false, or no `REBASE_ANTHROPIC_API_KEY` is configured at all (spec § 3.2, § 5).
    `message` is «Il team builder è spento.», the same sentence for either reason -- a
    visitor gets nothing useful from knowing which. A 503, the same shape
    `SigningUnavailable` already gives an environment missing Documenso or a mail
    sender."""

    code = "team_builder_off"


class TeamBuilderBusy(DomainError):
    """The team builder is on but full: every slot of the API process is taken
    (`REBASE_TEAM_BUILDER_CONCURRENCY`), or today's proposals reached
    `REBASE_TEAM_BUILDER_DAILY_CAP` (spec § 5, `team_caps.py`). `message` is «Troppe
    richieste in questo momento: riprova tra un minuto.» for both, since a visitor does
    the same thing either way. A 503 with `Retry-After`, like the limiter's 429."""

    code = "team_builder_busy"
