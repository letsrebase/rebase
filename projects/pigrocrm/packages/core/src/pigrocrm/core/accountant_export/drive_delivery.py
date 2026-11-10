"""Writing a stored export to the space's Drive (spec accountant export § 3 and § 5).

«Write to Drive» means the space's storage when its backend is Google Drive
(`storage/factory.py`): the ZIPs of the export are copied under
`commercialista/<entity-slug>/<period>/<timestamp>/`, a folder a person can read as it is, beside
the build's own prefix (which names the period by its dates and the export by its id). With local
storage there is no Drive to write to, and the page says so; with Drive as the storage but no
account and folder chosen, the same one sentence the documents give (`StorageNotConfigured`).

It is a person's act: an administrator whose scope covers the export, never an agent, whatever
its credential carries (`agent_actions.PERSON_ONLY_ACTIONS`). The copy is one ZIP at a time in
memory, as the download reads them; a failure removes what was already written.
"""

import datetime as dt
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.access import visible_export
from pigrocrm.core.accountant_export.models import AccountantExportRow, ExportDriveWrite
from pigrocrm.core.accountant_export.schemas import DriveDeliveryStatus, DriveWriteRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.storage.errors import StorageNotConfigured

ENTITY = "accountant_export"
ACTION = "write_export_to_drive"
WRITTEN_KIND = "accountant_export_written_to_drive"
ZIP_MIME = "application/zip"
LOCAL_STORAGE = "local_storage"
NOT_CONNECTED = "not_connected"
LOCAL_STORAGE_SENTENCE = (
    "Questo spazio salva i file sul server, non su Google Drive: la consegna su Drive "
    "non è disponibile."
)

log = logging.getLogger(__name__)


def period_folder(period_from: dt.date, period_to: dt.date) -> str:
    """The period as a folder name a person reads: `2026-t3`, `2026-09`, `2026`, and the two
    dates joined for anything that is not one calendar month, quarter or year."""
    first_of_next = (
        dt.date(period_to.year + 1, 1, 1)
        if period_to.month == 12
        else dt.date(period_to.year, period_to.month + 1, 1)
    )
    ends_a_month = period_to == first_of_next - dt.timedelta(days=1)
    if period_from.day == 1 and ends_a_month and period_from.year == period_to.year:
        months = period_to.month - period_from.month + 1
        if months == 1:
            return f"{period_from.year}-{period_from.month:02d}"
        if months == 3 and period_from.month % 3 == 1:
            return f"{period_from.year}-t{period_from.month // 3 + 1}"
        if months == 12:
            return f"{period_from.year}"
    return f"{period_from.isoformat()}_{period_to.isoformat()}"


def drive_prefix(row: AccountantExportRow, written_at: dt.datetime) -> str:
    """`commercialista/<entity-slug>/<period>/<timestamp>`, lowercase as every storage key is.
    The entity folder is the one the build chose, the second segment of its own prefix."""
    folder = row.prefix.split("/")[1]
    stamp = f"{written_at.astimezone(dt.UTC):%Y%m%dt%H%M%Sz}"
    return f"commercialista/{folder}/{period_folder(row.period_from, row.period_to)}/{stamp}"


def drive_unavailable(session: Session, settings: Settings) -> str | None:
    """Why an export cannot be written to the space's Drive, or `None` when it can: the space
    keeps its files on the server (`local_storage`), or Drive is the storage and no service
    account, or no connected account with a folder, is there to write with (`not_connected`)."""
    if settings.storage_backend != "gdrive":
        return LOCAL_STORAGE
    if settings.gdrive_service_account_json and settings.gdrive_root_folder_id:
        return None
    return None if DriveRepository(session).storage_account() is not None else NOT_CONNECTED


class ExportDriveStatus:
    """The page's question, asked without building the storage: a Drive that is half configured
    must be answered «not connected», never a failure of the question."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def get_drive_status(self, actor: Actor) -> DriveDeliveryStatus:
        """Whether an export can be written to the space's Drive, and if not why: the page draws
        the button only when it can, and the sentence otherwise."""
        actor.require_admin(ACTION)
        reason = drive_unavailable(self.session, self.settings)
        return DriveDeliveryStatus(available=reason is None, reason=reason)


class ExportDriveService:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.activities = ActivityService(session)

    def write_export_to_drive(
        self, export_id: UUID, actor: Actor, *, now: dt.datetime | None = None
    ) -> DriveWriteRead:
        """Copies every ZIP of a stored export to the space's Drive and records where."""
        row = visible_export(self.session, export_id, actor, ACTION)
        reason = drive_unavailable(self.session, self.settings)
        if reason == LOCAL_STORAGE:
            raise ValidationFailed(ENTITY, "storage_backend", LOCAL_STORAGE_SENTENCE)
        if reason == NOT_CONNECTED:
            raise StorageNotConfigured
        if row.deleted_at is not None:
            raise ValidationFailed(
                ENTITY,
                "export_id",
                "l'esportazione è stata eliminata dopo 13 mesi: se serve, va rifatta",
            )
        written_at = now or dt.datetime.now(dt.UTC)
        prefix = drive_prefix(row, written_at)
        files: list[dict[str, object]] = []
        try:
            for entry in row.parts:
                key = f"{prefix}/{entry['filename']}"
                self.storage.put(key, self.storage.get(entry["key"]), ZIP_MIME)
                files.append(
                    {"key": key, "filename": entry["filename"], "size_bytes": entry["size_bytes"]}
                )
        except BaseException:
            self._delete(files)
            raise
        write = ExportDriveWrite(
            export_id=row.id,
            legal_entity_id=row.legal_entity_id,
            prefix=prefix,
            files=files,
            written_by=actor.id,
            written_at=written_at,
        )
        try:
            self.session.add(write)
            self.session.flush()
            self.activities.record(
                ENTITY,
                row.id,
                WRITTEN_KIND,
                actor,
                {"prefix": prefix, "files": len(files)},
            )
            self.session.commit()
        except BaseException:
            self.session.rollback()
            self._delete(files)
            raise
        return self._read(write)

    def list_drive_writes(self, export_id: UUID, actor: Actor) -> list[DriveWriteRead]:
        """Where an export was written to Drive, newest first. A copy past the export's 13
        months is deleted with it and is not listed."""
        row = visible_export(self.session, export_id, actor, ACTION)
        if row.deleted_at is not None:
            return []
        writes = self.session.scalars(
            select(ExportDriveWrite)
            .where(ExportDriveWrite.export_id == row.id)
            .order_by(ExportDriveWrite.written_at.desc(), ExportDriveWrite.id.desc())
        )
        return [self._read(write) for write in writes]

    def _delete(self, files: list[dict[str, object]]) -> None:
        for entry in files:
            try:
                self.storage.delete(str(entry["key"]))
            except Exception:  # noqa: BLE001 - the failure being handled is the one to raise
                log.warning("could not remove a half-written Drive copy: %s", entry["key"])

    @staticmethod
    def _read(write: ExportDriveWrite) -> DriveWriteRead:
        return DriveWriteRead(
            id=write.id,
            export_id=write.export_id,
            prefix=write.prefix,
            files=[str(entry["filename"]) for entry in write.files],
            written_at=write.written_at,
            written_by=write.written_by,
        )


__all__ = ["ExportDriveService", "ExportDriveStatus", "drive_prefix", "period_folder"]
