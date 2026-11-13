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


class NoCv(NotFound):
    """A talent with no CV on file, where a CV is what is asked for (the anonymous CV,
    REB-862): a `NotFound`, so a 404 wherever one is, whose `message` is the sentence the
    admin reads rather than «cv <id> non trovato»."""

    def __init__(self, freelancer_id: UUID) -> None:
        super().__init__("cv", freelancer_id)
        self.message = "Nessun CV caricato"
        self.args = (self.message,)


class ValidationFailed(DomainError):
    code = "validation_failed"

    def __init__(self, entity: str, field: str, reason: str) -> None:
        super().__init__(f"{entity}.{field}: {reason}", entity=entity, field=field, reason=reason)


class InvalidState(DomainError):
    """An action the row's current state does not allow: a link retried that the CRM
    already refused, a merge undone twice. The API answers it with a 409, the
    status every `DomainError` without a mapping of its own already gets."""

    code = "invalid_state"


class LlmUnavailable(DomainError):
    """Claude did not answer: a refusal fallback exhausted, a rate limit, an outage
    (`llm.py`, REB-508). `message` is the one sentence a page can show as it stands. A
    502 in the API; the MCP's `_call` renders it as the tool's own sentence rather than
    a traceback."""

    code = "llm_unavailable"


class CvTextUnavailable(LlmUnavailable):
    """The CV could not be read *now*, for a reason that is the host's and not the file's:
    the parser process did not start, or was killed by a signal (`cv_text.extract_text`).
    The same `code` as its parent, so a caller that already treats an outage as «try again
    later» does: the card writer stores no hash for the CV, so the next pass tries it again,
    where an unreadable file is never tried twice."""


class LlmCapReached(LlmUnavailable):
    """The task's daily euro cap is spent (`ai_config.py`): the call was refused before
    any network. `message` is the sentence «Modelli AI» shows for the task. The same `code`
    as its parent, so the places that key on it (`TEAM_PROPOSAL_ERRORS`, the API's
    handler) treat it as the outage it is for a visitor."""


class LlmNotConfigured(LlmUnavailable):
    """The task cannot be called as configured: off, its provider off or without a key,
    no price for its model, or a saved key that does not open. Refused before any
    network; `message` is the sentence that says which, never key text. The `code` of its
    parent, as `LlmCapReached`."""


class AnonymousCvFailed(DomainError):
    """The anonymous CV could not be written from the CV on file (REB-862): no readable
    text, the model refused or was cut at the task's ceiling, the answer was not the shape,
    it still named the person after its one rewrite, or the CV changed during the call.
    `message` is one sentence of ours, never the model's words. Nothing was stored, so the
    next press asks again. A 422 in the API."""

    code = "anonymous_cv_failed"


class CvPrintFailed(DomainError):
    """The anonymous CV could not be typeset (REB-862): Typst missing, slow or failing, the
    brand's files gone, the working directory not made or written. The server's failure,
    not the request's: a 503 in the API, with this sentence and never `detail`, which is
    for the log (Typst's diagnostic quotes `cv.typ`, never the CV)."""

    code = "cv_print_failed"

    def __init__(self, detail: str) -> None:
        super().__init__("Non riesco a stampare il CV anonimo.", detail=detail)
        self.detail = detail


class TeamBuilderOff(DomainError):
    """The team builder cannot run in this environment: `REBASE_TEAM_BUILDER_ENABLED` is
    false, or no model is configured for the `team_proposal` task in «Modelli AI» (spec
    § 3.2, § 5).
    `message` is «Il team builder è spento.», the same sentence for either reason -- a
    visitor gets nothing useful from knowing which. A 503 with the sentence, like the member
    area without a mail key."""

    code = "team_builder_off"


class TeamBuilderBusy(DomainError):
    """The team builder is on but full: every slot of the API process is taken
    (`REBASE_TEAM_BUILDER_CONCURRENCY`), or today's proposals reached
    `REBASE_TEAM_BUILDER_DAILY_CAP` (spec § 5, `team_caps.py`). `message` is the same
    «too many requests, try again in a minute» sentence for both, since a visitor does
    the same thing either way. A 503 with `Retry-After`, like the limiter's 429."""

    code = "team_builder_busy"


class SecretsKeyMissing(DomainError):
    """`REBASE_SECRETS_KEY` is empty or is not 32 bytes in base64 (`secrets.py`, REB-829):
    a provider key is neither saved nor read, and nothing falls back to plain text. A 409
    in the API with the sentence, which «Modelli AI» shows as its banner."""

    code = "secrets_key_missing"

    def __init__(self) -> None:
        super().__init__(
            "REBASE_SECRETS_KEY manca o non è valida: servono 32 byte in base64. "
            "Senza, le chiavi dei modelli non si salvano né si leggono."
        )


class SecretsKeyInvalid(DomainError):
    """A stored secret does not open with the current `REBASE_SECRETS_KEY`. `message` names
    the variable and nothing else: never the ciphertext, the key or a partial text, and
    never which of «wrong key» or «row altered» it was (`secrets.py`, REB-829)."""

    code = "secrets_key_invalid"

    def __init__(self) -> None:
        super().__init__(
            "Una chiave salvata non si decifra: verifica REBASE_SECRETS_KEY, "
            "oppure salva di nuovo la chiave del fornitore."
        )
