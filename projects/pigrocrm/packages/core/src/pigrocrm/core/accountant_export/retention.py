"""Exports are kept 13 months and then deleted, leaving a trace (spec § 3).

The files leave the storage (the copies written to Drive with them) and the row stays with
`deleted_at`, and an `activities` entry (`accountant_export_deleted`) says which export it was,
for which period and how many files: never a figure out of the bundle. The pass runs inside the
daily `pigrocrm automations`, which is already scheduled and watched for lateness, so no cron
line is added. One export whose files cannot be deleted is counted and left for the next day's
pass; it never stops the others.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.models import AccountantExportRow, ExportDriveWrite
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.storage.base import DocumentStorage

RETENTION_MONTHS = 13
ENTITY = "accountant_export"
DELETED_KIND = "accountant_export_deleted"


def add_months(moment: dt.datetime, months: int) -> dt.datetime:
    """`moment` plus whole calendar months, the day clamped to the last of the month (31 March
    plus one month is 30 April)."""
    year, zero_based_month = divmod(moment.year * 12 + moment.month - 1 + months, 12)
    month = zero_based_month + 1
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    last_day = (dt.date(next_year, next_month, 1) - dt.timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


@dataclass(frozen=True)
class RetentionReport:
    """`deleted` exports past their time (or that would be, in a rehearsal), `failed` the
    ones whose files the storage would not delete."""

    deleted: int
    failed: int = 0


class ExportRetention:
    def __init__(self, session: Session, storage: Callable[[], DocumentStorage]) -> None:
        """`storage` is called only when an export is due: building a Drive backend checks the
        folder over the network, which a daily pass has no reason to do for nothing."""
        self.session = session
        self.storage = storage

    def run(self, *, now: dt.datetime, dry_run: bool) -> RetentionReport:
        actor = Actor.system()
        bind_scope(self.session, actor)
        due = list(
            self.session.scalars(
                select(AccountantExportRow)
                .where(
                    AccountantExportRow.deleted_at.is_(None), AccountantExportRow.expires_at <= now
                )
                .order_by(AccountantExportRow.expires_at)
            )
        )
        if dry_run:
            return RetentionReport(deleted=len(due))
        if not due:
            return RetentionReport(deleted=0)
        storage = self.storage()
        activities = ActivityService(self.session)
        deleted = failed = 0
        for row in due:
            try:
                for part in row.parts:
                    storage.delete(part["key"])
                # The copies a person wrote to Drive live as long as the export does.
                for write in self.session.scalars(
                    select(ExportDriveWrite).where(ExportDriveWrite.export_id == row.id)
                ):
                    for file in write.files:
                        storage.delete(file["key"])
            except Exception:  # noqa: BLE001 - kept for the next pass, the others go on
                failed += 1
                continue
            row.deleted_at = now
            activities.record(
                ENTITY,
                row.id,
                DELETED_KIND,
                actor,
                {
                    "period_from": row.period_from.isoformat(),
                    "period_to": row.period_to.isoformat(),
                    "built_at": row.built_at.isoformat(),
                    "files": len(row.parts),
                },
            )
            self.session.commit()
            deleted += 1
        return RetentionReport(deleted=deleted, failed=failed)
