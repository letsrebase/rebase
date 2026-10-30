"""The admin override/clear/delete/restore audit trail (REB-347): a small module of its
own, analogous to PigroCRM's `pigrocrm.core.activities` (`diff.py`, `sanitize.py`) and
`pigrocrm.core.auth.service`'s `_AUDITED_FIELDS`/`_snapshot` pattern for a `User` row --
but written again here, never imported from there: `rebase_core` may not depend on
PigroCRM's code (split out 2026-09-09).

Three things this module owns:

* `field_changes`: the `changed`/`before`/`after` shape a field override or clear is
  recorded with, restricted to the keys that actually differ (a patch that resends the
  value already on the row is not a change, and recording it as one would fill the
  timeline with events that never happened).
* `supplied_changes`/`reject_cleared_columns`: the same PATCH-with-null contract
  PigroCRM's `schemas.py` gives its own `Update` models, reimplemented for the hub's:
  `model_fields_set` is what tells "the caller sent `null`" apart from "the caller never
  mentioned this field", which a plain `model_dump(exclude_none=True)` cannot.
* `AdminActionService`: the append-only trail itself. `record` must never be able to
  fail the change it is recording -- the opposite failure mode from the change itself,
  which does need to fail loudly on a bad value. It is therefore called only *after* the
  caller's own commit, in its own transaction, wrapped so that a recording failure never
  reaches the caller.
"""

import logging
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import AdminAction, User

_log = logging.getLogger(__name__)

ENTITY = "admin_action"
TIMELINE_LIMIT_DEFAULT = 50
TIMELINE_LIMIT_MAX = 200
_ENTITY_TYPE_MAX_LENGTH = 20
_KIND_MAX_LENGTH = 20


# ---- the diff shape ----------------------------------------------------------------


def field_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """`{}` when nothing in `after` actually differs from `before`, else
    `{"changed": [...], "before": {...}, "after": {...}}` restricted to the keys whose
    value really changed. `before.get(key)` rather than `before[key]`, so a field set for
    the first time reads as `None -> value` instead of raising inside an audit path,
    which must never be able to fail the operation it records."""
    changed = sorted(key for key, value in after.items() if before.get(key) != value)
    if not changed:
        return {}
    return {
        "changed": changed,
        "before": {key: before.get(key) for key in changed},
        "after": {key: after[key] for key in changed},
    }


def supplied_changes(data: BaseModel, *, exclude: set[str] | None = None) -> dict[str, Any]:
    """Only the fields the caller actually supplied, `None` included: an explicit
    `null` for a field must survive as a key present with value `None`, which
    `model_dump(exclude_none=True)` cannot tell apart from the caller never having
    mentioned the field at all."""
    return data.model_dump(exclude_unset=True, exclude=exclude or set())


def reject_cleared_columns(entity: str, model: type[Any], changes: dict[str, Any]) -> None:
    """Refuses a `null` aimed at a column the database declares `NOT NULL`, read from
    the mapper rather than a hand-written list per service so the rule cannot drift the
    day somebody adds a column. A key that is not a mapped column at all is left alone:
    this function answers one question, and the caller answers the rest."""
    columns = sa_inspect(model).columns
    for key, value in changes.items():
        if value is not None or key not in columns:
            continue
        if not columns[key].nullable:
            raise ValidationFailed(entity, key, "il campo non può essere svuotato")


def coerce_stored_value(model: type[Any], field: str, value: Any) -> Any:
    """Reverses what `sanitize_payload` did to a `before`/`after` value on its way into
    storage, so a revert can `setattr` a properly typed value instead of a string that
    merely looks right until the next comparison. `None` passes through untouched -- a
    cleared column reverting to "no value" needs no coercion. Every column this module
    reverts is either a plain string, a `Decimal` (`tariffa_giornaliera`,
    `budget_giornaliero`) or a `date` (`periodo_da`); a column whose type declines to
    say what its Python type is (`links`' `JSONB`) is left exactly as stored, since
    `sanitize_payload` never reshapes a list of strings in the first place."""
    if value is None:
        return None
    column = sa_inspect(model).columns.get(field)
    if column is None:
        return value
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is Decimal:
        return Decimal(str(value))
    if python_type is date and not isinstance(value, date):
        return date.fromisoformat(value)
    return value


# ---- sanitizing a payload before it reaches Postgres --------------------------------

# Bounded above `PROGETTO_MAX_LENGTH`/`COMMENT_MAX_LENGTH` (both 4000, `models.py`) --
# the widest text field `FreelancerOverride`/`CompanyOverride` ever carries (`note`,
# `progetto`). `revert` reapplies a stored `before` value verbatim: truncating a value
# no wider than the column itself would silently replace a genuine override with a
# shorter one on the very next revert, corrupting exactly the data this trail exists
# to protect. What is bounded here is a payload nobody validated, not one of these.
MAX_STRING_LENGTH = 4000
MAX_DEPTH = 10
_TRUNCATION_SUFFIX = "…"
_DEPTH_PLACEHOLDER = "…(troncato: profondità massima superata)…"


def _sanitize_float(value: float) -> float | str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return value


def _sanitize_string(value: str) -> str:
    cleaned = value.replace("\x00", "")
    if len(cleaned) > MAX_STRING_LENGTH:
        return cleaned[:MAX_STRING_LENGTH] + _TRUNCATION_SUFFIX
    return cleaned


def _sanitize_value(value: Any, depth: int) -> Any:
    if isinstance(value, dict):
        if depth >= MAX_DEPTH:
            return _DEPTH_PLACEHOLDER
        return {key: _sanitize_value(inner, depth + 1) for key, inner in value.items()}
    if isinstance(value, list):
        if depth >= MAX_DEPTH:
            return _DEPTH_PLACEHOLDER
        return [_sanitize_value(inner, depth + 1) for inner in value]
    if isinstance(value, float):
        return _sanitize_float(value)
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    # `datetime` is a subclass of `date`, so this one isinstance covers both, and
    # `.isoformat()` is defined on both with the meaning we want.
    if isinstance(value, date):
        return value.isoformat()
    return value


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitizes every value in `payload`, recursively, and never raises: an audit entry
    must not be able to fail the very change it is recording. `Decimal` and `UUID`
    become their canonical text form for the same reason `datetime`/`date` do -- the
    driver's JSONB serialization has no encoder for any of them, and raising at
    `flush()` would take the audited change down with it. The payload's own top-level
    keys are never dropped or truncated by the depth limit; only nested values are."""
    return {key: _sanitize_value(value, depth=1) for key, value in payload.items()}


# ---- the trail itself ----------------------------------------------------------------


class AdminActionRead(BaseModel):
    """One entry of the trail, as an admin reads it: who, when, what kind, and the
    diff for an `overridden`/`cleared` entry (`payload["changed"/"before"/"after"]`,
    empty for `deleted`/`restored`, whose kind is the whole story)."""

    id: UUID
    entity_type: str
    entity_id: UUID
    kind: str
    admin_id: UUID
    admin_nome: str
    payload: dict[str, Any]
    created_at: datetime


class AdminActionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        entity_type: str,
        entity_id: UUID,
        kind: str,
        admin_id: UUID,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Writes one entry and commits it on its own, deliberately never inside the
        caller's own transaction: every caller here has already committed the change
        this entry describes by the time it calls `record`, so a failure writing the
        entry -- a constraint nobody expected, a session left unusable by an earlier
        error -- can only cost the entry itself, never the change it was about to
        describe. Logged rather than raised, for the same reason: a lost line in the
        trail is a smaller failure than refusing an admin's override because the trail
        could not keep up with it."""
        try:
            row = AdminAction(
                entity_type=entity_type[:_ENTITY_TYPE_MAX_LENGTH],
                entity_id=entity_id,
                kind=kind[:_KIND_MAX_LENGTH],
                admin_id=admin_id,
                payload=sanitize_payload(payload or {}),
            )
            self.session.add(row)
            self.session.commit()
        except Exception:
            self.session.rollback()
            _log.warning(
                "failed to record an admin action (%s on %s %s)",
                kind,
                entity_type,
                entity_id,
                exc_info=True,
            )

    def timeline(
        self, entity_type: str, entity_id: UUID, limit: int = TIMELINE_LIMIT_DEFAULT
    ) -> list[AdminActionRead]:
        limit = max(1, min(limit, TIMELINE_LIMIT_MAX))
        rows = self.session.execute(
            select(AdminAction, User)
            .join(User, User.id == AdminAction.admin_id)
            .where(AdminAction.entity_type == entity_type, AdminAction.entity_id == entity_id)
            .order_by(AdminAction.created_at.desc(), AdminAction.id.desc())
            .limit(limit)
        ).all()
        return [
            AdminActionRead(
                id=action.id,
                entity_type=action.entity_type,
                entity_id=action.entity_id,
                kind=action.kind,
                admin_id=action.admin_id,
                admin_nome=f"{admin.nome} {admin.cognome}".strip(),
                payload=action.payload,
                created_at=action.created_at,
            )
            for action, admin in rows
        ]

    def require(self, action_id: UUID) -> AdminAction:
        row = self.session.get(AdminAction, action_id)
        if row is None:
            raise NotFound(ENTITY, action_id)
        return row


def utcnow() -> datetime:
    """The one place `soft_delete`/`restore` reach for "now": a bare helper so a test
    can freeze it the way it freezes anything else in this package, and so a future
    caller never types `datetime.now(UTC)` slightly differently."""
    return datetime.now(UTC)
