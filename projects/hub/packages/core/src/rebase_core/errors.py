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
