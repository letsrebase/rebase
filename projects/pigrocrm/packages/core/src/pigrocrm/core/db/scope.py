"""The azienda scope a session carries, bound as two transaction-local Postgres
settings the row-level policies read (REB-633, spec 2026-10-03 §4).

`pigrocrm.aziende` is `*` for an unscoped actor, or the comma-joined ids of the
actor's aziende, possibly the empty string for a scoped person with no active azienda
left. A connection with nothing set reads `NULL`, a pooled one whose transaction-local
value expired reads the empty string, and `azienda_visibile()` reads both as nothing:
the default is closed. `pigrocrm.user_id` is the actor's own user id, read by the one
policy that needs to know whose mailbox a row belongs to (`gmail_messages`).

Written with `set_config(name, value, true)`, exactly as `work_units/service.py` writes
`pigrocrm.actor`: local to the transaction, so a pooled connection can never leak one
request's scope into the next. A service commits in the middle of a request, which
ends the transaction and the setting with it, so `bind_scope` also registers an
`after_begin` listener that writes the same two values at the start of every later
transaction on that session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import event, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from pigrocrm.core.actor import Actor

SCOPE_SETTING = "pigrocrm.aziende"
USER_SETTING = "pigrocrm.user_id"
EVERY_AZIENDA = "*"
_BOUND = "pigrocrm_scope_listener"


def scope_value(actor: Actor) -> str:
    """What `pigrocrm.aziende` holds for `actor`: `*` for an unscoped one, the ids of
    the scope otherwise, and the empty string for a scope with no azienda left, which
    the policies read as nothing (spec §1.11: nothing turns an empty scope into «tutte»)."""
    if actor.aziende is None:
        return EVERY_AZIENDA
    return ",".join(str(azienda) for azienda in actor.aziende)


def user_value(actor: Actor) -> str:
    return str(actor.id) if actor.id is not None else ""


def _apply(connection: Any, scope: str, user: str) -> None:
    connection.execute(
        text("SELECT set_config(:scope_name, :scope, true), set_config(:user_name, :user, true)"),
        {"scope_name": SCOPE_SETTING, "scope": scope, "user_name": USER_SETTING, "user": user},
    )


def bind_scope(session: Session, actor: Actor) -> None:
    """Make every transaction of `session` run inside `actor`'s scope.

    Two things, in this order. First the settings go on the transaction that is already
    open, if one is: `get_actor` has read `users` on the request's session before the
    actor is known, that read began the transaction, and a listener registered now would
    not fire for it. Then the listener, so the values come back after every commit or
    rollback. On a session nothing has touched yet (the dashboards' snapshot session)
    only the listener is registered, since the first statement of that session is the
    one that fixes its `REPEATABLE READ` snapshot and must stay the service's own.

    Binding again on the same session replaces the previous actor's scope: the listener
    is one per session, keyed in `session.info`, and reads the values it was last given.
    """
    scope, user = scope_value(actor), user_value(actor)
    session.info[_BOUND] = (scope, user)
    if session.in_transaction():
        _apply(session.connection(), scope, user)
    if not session.info.get(f"{_BOUND}_registered"):
        session.info[f"{_BOUND}_registered"] = True

        @event.listens_for(session, "after_begin")
        def _rebind(target: Session, transaction: Any, connection: Any) -> None:
            bound = target.info.get(_BOUND)
            if bound is not None:
                _apply(connection, *bound)


# SQLSTATE of `insufficient_privilege`, and the sentence Postgres puts in front of a
# row-level policy's refusal. A missing grant answers the same SQLSTATE with another
# sentence («permission denied for table ...»), and that one is a deploy defect, not a
# row that is not there: it stays the 500 it always was.
INSUFFICIENT_PRIVILEGE = "42501"
_POLICY_SENTENCE = "row-level security policy"


def is_policy_refusal(exc: BaseException) -> bool:
    """Whether a database error is a row-level policy saying no to a write (REB-634):
    what the API and a tool answer as the row not existing for this person."""
    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != INSUFFICIENT_PRIVILEGE:
        return False
    diag = getattr(orig, "diag", None)
    message = getattr(diag, "message_primary", None) or str(orig)
    return _POLICY_SENTENCE in message


__all__ = [
    "EVERY_AZIENDA",
    "INSUFFICIENT_PRIVILEGE",
    "SCOPE_SETTING",
    "USER_SETTING",
    "bind_scope",
    "is_policy_refusal",
    "scope_value",
]
