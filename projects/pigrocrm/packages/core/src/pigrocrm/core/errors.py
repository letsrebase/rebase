from datetime import datetime
from typing import Any
from uuid import UUID

from pigrocrm.core.agent_actions import action_label
from pigrocrm.core.labels import entity_not_found, field_path


class DomainError(Exception):
    """Base for every business-rule failure.

    Carries structured `details`, never a pre-formatted sentence: the API renders
    them as RFC 9457 problem details, the MCP adapter renders them as guidance an
    LLM can act on. Those are two different renderings of the same fact.
    """

    code: str = "domain_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details


class NotFound(DomainError):
    """The record is not there. The message is Italian, like every other a person can read,
    and the `entity` and `identifier` in the details stay the machine names. `message` is for
    the one case where the sentence can say what to do about it, not only what is missing."""

    code = "not_found"

    def __init__(self, entity: str, identifier: str | UUID, *, message: str | None = None) -> None:
        super().__init__(
            message if message is not None else entity_not_found(entity, str(identifier)),
            entity=entity,
            identifier=str(identifier),
        )


class ValidationFailed(DomainError):
    """A value the rules refuse. `entity`, `field` and `reason` in the details are the machine
    names; `in_words` names the field in the message as a person says it («azienda emittente,
    indirizzo») where `labels.FIELD_PATH` has the words, and as `entity.field` where it has none."""

    code = "validation_failed"

    def __init__(
        self,
        entity: str,
        field: str,
        reason: str,
        *,
        expected: str | None = None,
        in_words: bool = False,
    ) -> None:
        details: dict[str, Any] = {
            "entity": entity,
            "field": field,
            "reason": reason,
        }
        if expected is not None:
            details["expected"] = expected
        subject = field_path(entity, field) if in_words else f"{entity}.{field}"
        super().__init__(f"{subject}: {reason}", **details)


class Conflict(DomainError):
    code = "conflict"

    def __init__(self, entity: str, reason: str, **details: Any) -> None:
        super().__init__(f"{entity}: {reason}", entity=entity, reason=reason, **details)


class PermissionDenied(DomainError):
    code = "permission_denied"

    def __init__(self, action: str, required_roles: list[str], actual_role: str) -> None:
        super().__init__(
            f"{action_label(action)} requires one of {required_roles}, actor has {actual_role}",
            action=action,
            required_roles=required_roles,
            actual_role=actual_role,
        )


class OutOfScope(DomainError):
    """A write the legal entity scope refused outright (REB-634, spec 2026-10-03 §3): Postgres
    answers `insufficient_privilege` when a policy's `WITH CHECK` says no, and the
    person is told what a read would have told them, that the row is not there for
    them. The same code as `NotFound`, so the status is the same 404."""

    code = "not_found"

    def __init__(self) -> None:
        super().__init__("la riga non esiste o non e' tra le tue aziende", entity="record")


class ScopedAdmin(DomainError):
    """An admin whose view is limited to some legal entities asked for a space-level change
    (spec 2026-10-03 §1.11). The same code as `PermissionDenied`, so the API answers 403
    and the SPA hides nothing it would not hide for a collaborator, but its own words:
    the role is right, the scope is what is missing, and only an unscoped admin can
    widen it from the Team panel."""

    code = "permission_denied"

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action_label(action)} requires an admin whose view covers the whole space",
            action=action,
            required_roles=["admin"],
            actual_role="admin",
            reason="serve un amministratore senza limiti di azienda",
        )


class AgentForbidden(DomainError):
    """An operation no agent may perform, whatever role its owner has.

    Deliberately not a `PermissionDenied`. That one answers "you are the wrong role,
    ask somebody senior"; this one answers "no token of this kind may do this at all",
    and telling an administrator to find a bigger administrator would be a lie they
    would waste time on.
    """

    code = "agent_forbidden"

    def __init__(self, action: str) -> None:
        super().__init__(
            f"{action_label(action)} non è eseguibile da un agente: "
            "è un atto che richiede una persona",
            action=action,
        )


class ModuleOff(DomainError):
    """A route that belongs to a module the space has switched off (spec 2026-11-09, A2).
    The API answers 404 with this sentence, so a stale link explains itself; `module` in
    the details is the machine key, the message names it as a person says it."""

    code = "module_off"

    def __init__(self, module: str, label: str) -> None:
        super().__init__(
            f"Il modulo {label} è spento: un amministratore può accenderlo in "
            "Impostazioni → Moduli",
            module=module,
        )


class StaleRow(DomainError):
    """A whole-row save built on a row that changed since it was read (REB-622, spec
    2026-10-03 §11). The caller sent the `updated_at` its draft was built on, the row's
    is another: the second admin's Salva would otherwise overwrite the first's fields in
    silence. The API answers 409; the message is what the panel shows, and `updated_at`
    in the details is the row's current version, so a client can tell whether a reload
    brought it."""

    code = "stale_row"

    def __init__(self, entity: str, *, updated_at: datetime | None) -> None:
        super().__init__(
            "qualcun altro ha salvato nel frattempo: ricarica e riprova",
            entity=entity,
            updated_at=updated_at,
        )


class ImmutableField(DomainError):
    code = "immutable_field"

    def __init__(self, entity: str, field: str, reason: str) -> None:
        super().__init__(
            f"{entity}.{field} cannot be changed: {reason}",
            entity=entity,
            field=field,
            reason=reason,
        )


class NoteNotChangeable(DomainError):
    """A note the caller may read but not edit or delete (REB-879): somebody else's, for a
    caller who is not an admin, or one a job or rebase wrote, which is an event of the
    record's life and nobody's remark. The same code as `PermissionDenied`, so the API
    answers 403."""

    code = "permission_denied"

    def __init__(self, note_id: UUID, *, recorded: bool) -> None:
        super().__init__(
            "questa nota la hanno scritta il sistema o rebase: non si modifica e non si cancella"
            if recorded
            else "una nota la modifica o la cancella chi l'ha scritta o un amministratore",
            action="change_note",
            note_id=str(note_id),
        )


class SigningUnavailable(DomainError):
    """Signing cannot run here: the installation has no Documenso, or no way to mail the link.
    The API answers 503 with the sentence, which says what the installation is missing, so the
    person is told to send the document by hand instead of waiting on a retry."""

    code = "signing_unavailable"


class DocumensoFailed(DomainError):
    """Documenso refused a call, did not answer, or answered something this code does not read.
    `message` is the sentence for a person (Documenso's own `message` and never its stack
    trace); `detail` is what the log needs to tell a 404 from a DNS failure, and is kept out of
    `details` so that it never reaches a response."""

    code = "documenso_failed"

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail
