"""What a merge of two cards does with a work profile and a CLI sign-in (spec 2026-11-17 § 8).

A card has one work profile (`uq_work_profiles_freelancer_id`), so it cannot simply move with
the other rows: the merge keeps the NEWER submission of the two on the surviving card and deletes
the other, like the sources of the absorbed card (`sources.delete_sources`). That deletion is not
undone by `undo`: a profile is the person's to send again, and it expires in 12 months anyway.

The absorbed person's device authorizations are deleted: a row lives ten or fifteen minutes and
nothing in it is worth keeping, whereas moving a pending one would hand the survivor a sign-in
the absorbed login started.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from rebase_core.models import CliAuthorization, WorkProfile


def keep_newest_work_profile(session: Session, survivor_card: UUID, absorbed_card: UUID) -> None:
    absorbed = session.scalar(select(WorkProfile).where(WorkProfile.freelancer_id == absorbed_card))
    if absorbed is None:
        return
    survivor = session.scalar(select(WorkProfile).where(WorkProfile.freelancer_id == survivor_card))
    if survivor is not None and survivor.submitted_at >= absorbed.submitted_at:
        session.delete(absorbed)
        return
    if survivor is not None:
        session.delete(survivor)
        session.flush()  # the unique index on the card must be free before the move
    absorbed.freelancer_id = survivor_card


def drop_cli_authorizations(session: Session, absorbed_user: UUID) -> None:
    session.execute(delete(CliAuthorization).where(CliAuthorization.user_id == absorbed_user))
