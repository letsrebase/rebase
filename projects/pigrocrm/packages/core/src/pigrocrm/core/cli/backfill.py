"""The two commands an operator runs by hand: `owner-backfill` and `vapid-keys`."""

import sys
from collections.abc import Sequence

from pigrocrm.core.actor import Actor
from pigrocrm.core.cli.spaces_walk import read_spaces, report_unreadable, space_engine
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.owner_backfill import apply_owner_backfill, plan_owner_backfill
from pigrocrm.core.reminders.push import generate_vapid_keys


def owner_backfill(
    settings: Settings, *, slug: str, generic_accounts: Sequence[str], apply: bool
) -> int:
    """`pigrocrm owner-backfill --slug <space>`: gives every live customer and deal of one
    space the owner its creation entry names (REB-797), and says what it left alone.

    A one-off, run by hand over SSH on the host, so the shape differs from the cron
    commands in the one way that matters: **it writes nothing unless `--apply` is given**,
    and prints the plan (a line per record with the owner it would get, then the records it
    lists instead, with the reason) the same way both times. `--generic-account <email>`
    (repeatable) names an account that several people or agents act through: its records
    are listed, not set. The rule and its refusals are `pigrocrm.core.owner_backfill`'s.

    `--slug root` is the root installation, as in the other commands. Exit 1 when the space
    is not in the registry or cannot be read, 0 otherwise; a failure prints the exception's
    type and never its text, which may carry the database URL.
    """
    walk = read_spaces(settings, slug=slug)
    if report_unreadable(walk):
        return 1
    if not walk.targets:
        print(f"{slug}: non nel registro", file=sys.stderr)
        return 1
    target = walk.targets[0]

    try:
        with space_engine(target.url) as engine, session_factory(engine)() as space:
            bind_scope(space, Actor.system())
            plan = plan_owner_backfill(space, generic_emails=generic_accounts)
            written = apply_owner_backfill(space, plan) if apply else 0
    except Exception as exc:  # noqa: BLE001 - one line, never a traceback
        print(f"{slug}: saltato ({type(exc).__name__})", file=sys.stderr)
        return 1

    label = {"customer": "cliente", "deal": "deal"}
    for assignment in plan.assignments:
        print(f"{slug}: {label[assignment.entity]} «{assignment.label}» -> {assignment.owner_name}")
    for listed in plan.listed:
        who = f" (creato da {listed.creator_name})" if listed.creator_name else ""
        print(
            f"{slug}: {label[listed.entity]} «{listed.label}» lasciato senza responsabile: "
            f"{listed.reason}{who}"
        )
    for entity in ("customer", "deal"):
        print(
            f"{slug}: {label[entity]}: {plan.assigned(entity)} da assegnare, "
            f"{plan.left(entity)} lasciati, {plan.already_owned[entity]} già con responsabile"
        )
    print(f"{slug}: {'scritti ' + str(written) if apply else 'prova, non scritto niente'}")
    return 0


def vapid_keys() -> int:
    """`pigrocrm vapid-keys`: a new VAPID key pair for the push channel (REB-738), as the
    lines an operator pastes into the environment's `.env`. Writes nothing anywhere: the
    private key is a secret, and installing it on a host is the operator's step. The public
    key is printed for reference only, since the application derives it from the private
    one."""
    private, public = generate_vapid_keys()
    print(f"PIGROCRM_VAPID_PRIVATE_KEY={private}")
    print(f"# chiave pubblica, derivata dalla privata: {public}")
    return 0
