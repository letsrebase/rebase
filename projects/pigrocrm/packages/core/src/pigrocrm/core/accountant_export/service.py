"""Builds the bundle and stores it: one export for one legal entity and one period, or for
every legal entity (spec accountant export § 1 and § 3).

An export holds every fiscal figure of an entity, so it is an administrator's whose view
covers it: the one-entity export needs the entity inside the admin's scope, the
«all entities» export an admin whose scope is not limited, and it carries the shared
purchases (those of no legal entity) in a section of their own. An agent reaches it only
with `full_access`.

**A build leaves an export behind** (REB-1012): a row and its ZIP files under a prefix of its
own in the space's storage, named with the moment of the build, so building the same period
twice keeps both. Nothing is built in memory: an original file out of the storage goes to a
spool on disk as soon as it is read, and the ZIP is written member by member from there. A
bundle that would not fit one `put` is cut into several ZIPs, each a complete archive that
opens on its own. The index inside says which archive holds which file.

The ceiling of one part is the 100 MiB `DocumentService` already puts through
`DocumentStorage.put` on both backends (`documents.schemas.SIZE_MAX`), less a margin for the
index, headers and a deflate that grows an incompressible file a little. Members are placed by
their size before compression, so a part is never larger than the ceiling; a single file that
is larger on its own gets a part to itself.
"""

import datetime as dt
import hashlib
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Select, extract, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.access import visible_export
from pigrocrm.core.accountant_export.index import (
    INDEX_FILE,
    ExportIndex,
    MissingLine,
    PartLine,
)
from pigrocrm.core.accountant_export.models import AccountantExportRow
from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartDefinition,
    PartOutput,
    Period,
    Spool,
)
from pigrocrm.core.accountant_export.registry import PARTS, UNAVAILABLE
from pigrocrm.core.accountant_export.retention import (
    ENTITY as EXPORT_TRACE,
)
from pigrocrm.core.accountant_export.retention import (
    RETENTION_MONTHS,
    add_months,
)
from pigrocrm.core.accountant_export.schemas import ExportPartRead, ExportRead, PartPreviewRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.documents.schemas import SIZE_MAX
from pigrocrm.core.errors import DomainError, NotFound, ValidationFailed
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.service import ENTITY as LEGAL_ENTITY
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.base import DocumentStorage, validate_storage_key

ENTITY = "accountant_export"
ACTION = "build_accountant_export"
DOWNLOAD_ACTION = "get_export_download_url"
ALL_ENTITIES_SECTION = "Tutte le aziende"
SHARED_SECTION = "Acquisti condivisi"
SHARED_FOLDER = "condivisi"
# A month, a quarter or a year: nothing longer is one period.
MAX_PERIOD_DAYS = 366
ZIP_MIME = "application/zip"
STORAGE_ROOT = "commercialista"
ALL_ENTITIES_FOLDER = "tutte-le-aziende"
# What a part leaves free under the ceiling: the index, the ZIP's headers and the little a
# deflate adds to a file that does not compress.
PART_MARGIN_BYTES = 2 * 1024 * 1024
PART_LIMIT_BYTES = SIZE_MAX - PART_MARGIN_BYTES
COPY_CHUNK = 1024 * 1024
# The slug appears twice in a key (the folder and the file name) beside a period, a stamp and
# the part suffix: 40 leaves the longest key near 210 of the 255 characters.
SLUG_MAX_LENGTH = 40


@dataclass(frozen=True)
class StoredExport:
    export: ExportRead
    index: ExportIndex


def download_path(export_id: UUID, part: int) -> str:
    return f"/api/accountant-export/exports/{export_id}/parts/{part}"


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "azienda"


def _storage_slug(legal_entity: LegalEntity) -> str:
    """The entity's folder in the storage key and in the ZIP's name: its slug, cut so the key
    of the longest part stays under the storage's 255 characters, and the end of its id in
    place of a slug the storage refuses (a Windows device name such as «aux»)."""
    slug = _slug(legal_entity.name)[:SLUG_MAX_LENGTH].strip("-")
    try:
        validate_storage_key(f"{slug}/x")
    except DomainError:
        return f"azienda-{legal_entity.id.hex[-8:]}"
    return slug


def _zip_time(moment: dt.datetime) -> tuple[int, int, int, int, int, int]:
    """The ZIP's own timestamp, the moment the bundle was built, so equal inputs and an equal
    `now` make equal bytes."""
    utc = moment.astimezone(dt.UTC)
    return (max(utc.year, 1980), utc.month, utc.day, utc.hour, utc.minute, utc.second)


class AccountantExportService:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.legal_entities = LegalEntityService(session)
        self.activities = ActivityService(session)
        # How many bytes a part may hold before the next one starts; tests lower it.
        self.part_limit = PART_LIMIT_BYTES
        # The spool of the build in progress, read by `_context` for the parts.
        self._spool: Spool | None = None

    def build_accountant_export(
        self,
        period: Period,
        actor: Actor,
        legal_entity_id: UUID | None = None,
        *,
        all_entities: bool = False,
        now: dt.datetime | None = None,
    ) -> StoredExport:
        """Builds the bundle of `period` and stores it: one legal entity's (the named one, or
        the only one), or every legal entity's with the shared purchases when `all_entities`.
        Answers where each ZIP part can be fetched, never the bytes."""
        actor.require_admin(ACTION)
        self._check_period(period)
        built_at = now or dt.datetime.now(dt.UTC)
        if all_entities:
            actor.require_unscoped_admin(ACTION)
            if legal_entity_id is not None:
                raise ValidationFailed(
                    ENTITY,
                    "legal_entity_id",
                    "un'esportazione di tutte le aziende non ne nomina una",
                    expected="legal_entity_id oppure all_entities, non entrambi",
                )
            entities = list(self.legal_entities.repo.list(only_active=False))
            scope_name = ALL_ENTITIES_SECTION
        else:
            if (
                legal_entity_id is not None
                and actor.legal_entities is not None
                and legal_entity_id not in actor.legal_entities
            ):
                # Not told apart from an entity that does not exist: the scope hides it.
                raise NotFound(LEGAL_ENTITY, legal_entity_id)
            entities = [
                self.legal_entities.require_single(legal_entity_id, entity=ENTITY),
            ]
            scope_name = entities[0].name

        index = ExportIndex(period=period, scope=scope_name, built_at=built_at)
        export_id = uuid7()
        stamp = f"{built_at.astimezone(dt.UTC):%Y%m%dt%H%M%Sz}-{export_id.hex[-8:]}"
        folder = ALL_ENTITIES_FOLDER if all_entities else _storage_slug(entities[0])
        days = f"{period.date_from.isoformat()}_{period.date_to.isoformat()}"
        prefix = f"{STORAGE_ROOT}/{folder}/{days}/{stamp}"
        base = f"commercialista-{folder}-{days}-{stamp}"

        with tempfile.TemporaryDirectory(prefix="pigrocrm-export-") as scratch:
            spool_dir = Path(scratch) / "spool"
            spool_dir.mkdir()
            self._spool = Spool(spool_dir)
            try:
                files: list[BundleFile] = []
                for legal_entity in entities:
                    entity_prefix = ""
                    if all_entities:
                        entity_prefix = f"{self._folder(legal_entity, entities)}/"
                    files.extend(
                        self._entity_parts(
                            legal_entity,
                            actor,
                            period,
                            entity_prefix,
                            legal_entity.name,
                            index,
                            all_entities=all_entities,
                        )
                    )
                if all_entities:
                    files.extend(self._shared_parts(actor, period, index))
            finally:
                self._spool = None

            groups = self._partition(files)
            names = self._archive_names(base, len(groups))
            if len(groups) > 1:
                index.archives = {
                    file.path: name
                    for name, group in zip(names, groups, strict=True)
                    for file in group
                }
            index_file = BundleFile(INDEX_FILE, index.render().encode("utf-8"))
            groups[0].insert(0, index_file)
            archives = [
                self._write_zip(Path(scratch) / f"part-{number}.zip", group, built_at)
                for number, group in enumerate(groups, start=1)
            ]
            parts = [
                {
                    "key": f"{prefix}/{name}",
                    "filename": name,
                    "size_bytes": archive.stat().st_size,
                    "sha256": _sha256(archive),
                }
                for name, archive in zip(names, archives, strict=True)
            ]
            self._put_all(parts, archives)

        row = AccountantExportRow(
            id=export_id,
            legal_entity_id=None if all_entities else entities[0].id,
            scope="all_entities" if all_entities else "entity",
            period_from=period.date_from,
            period_to=period.date_to,
            prefix=prefix,
            parts=parts,
            checklist=index.snapshot(),
            built_by=actor.id,
            built_by_type=actor.type,
            built_at=built_at,
            expires_at=add_months(built_at, RETENTION_MONTHS),
        )
        try:
            self.session.add(row)
            self.session.flush()
            # Who built it and for which period; never a figure out of the bundle.
            self.activities.record(
                EXPORT_TRACE,
                row.id,
                "accountant_export_built",
                actor,
                {
                    "period_from": period.date_from.isoformat(),
                    "period_to": period.date_to.isoformat(),
                    "scope": row.scope,
                    "files": len(parts),
                },
            )
            self.session.commit()
        except BaseException:
            self.session.rollback()
            self._delete_all(parts)
            raise
        return StoredExport(export=self._read(row), index=index)

    def get_export_download_url(self, export_id: UUID, actor: Actor) -> ExportRead:
        """Where each ZIP of a stored export can be fetched: REST paths, never bytes. An export
        past its 13 months has no files, and says so."""
        return self._read(self._visible(export_id, actor, DOWNLOAD_ACTION))

    def download_part(self, export_id: UUID, part: int, actor: Actor) -> tuple[bytes, str, str]:
        """The bytes of one ZIP of a stored export, its content type and its name."""
        row = self._visible(export_id, actor, DOWNLOAD_ACTION)
        if row.deleted_at is not None:
            raise NotFound(
                ENTITY,
                export_id,
                message="l'esportazione è stata eliminata dopo 13 mesi: se serve, va rifatta",
            )
        if not 1 <= part <= len(row.parts):
            raise NotFound(ENTITY, f"{export_id}/parts/{part}")
        entry = row.parts[part - 1]
        return self.storage.get(entry["key"]), ZIP_MIME, entry["filename"]

    def list_exports(
        self,
        actor: Actor,
        legal_entity_id: UUID | None = None,
        *,
        year: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ExportRead]:
        """The stored exports, newest first, deleted ones included (they are the trace). What
        the actor's scope hides, the database hides. `year` keeps the exports whose period
        starts in that calendar year."""
        actor.require_admin(DOWNLOAD_ACTION)
        query = self._filtered(select(AccountantExportRow), legal_entity_id, year)
        query = query.order_by(AccountantExportRow.built_at.desc(), AccountantExportRow.id.desc())
        return [self._read(row) for row in self.session.scalars(query.offset(offset).limit(limit))]

    def count_exports(
        self, actor: Actor, legal_entity_id: UUID | None = None, *, year: int | None = None
    ) -> int:
        """How many stored exports `list_exports` would reach with no page."""
        actor.require_admin(DOWNLOAD_ACTION)
        query = self._filtered(select(func.count(AccountantExportRow.id)), legal_entity_id, year)
        return self.session.scalar(query) or 0

    def describe_parts(
        self, actor: Actor, legal_entity_id: UUID | None = None, *, all_entities: bool = False
    ) -> list[PartPreviewRead]:
        """The parts a build for this company would hold: those that apply to its modules and
        fiscal pack, in the order of the index. For every company, a part that applies to any
        of them. A part left out is not named here: the build's index says why."""
        actor.require_admin(ACTION)
        if all_entities:
            actor.require_unscoped_admin(ACTION)
            if legal_entity_id is not None:
                raise ValidationFailed(
                    ENTITY,
                    "legal_entity_id",
                    "un'esportazione di tutte le aziende non ne nomina una",
                    expected="legal_entity_id oppure all_entities, non entrambi",
                )
            entities = list(self.legal_entities.repo.list(only_active=False))
        else:
            if (
                legal_entity_id is not None
                and actor.legal_entities is not None
                and legal_entity_id not in actor.legal_entities
            ):
                raise NotFound(LEGAL_ENTITY, legal_entity_id)
            entities = [self.legal_entities.require_single(legal_entity_id, entity=ENTITY)]
        today = today_local()
        contexts = [
            self._context(actor, Period(today, today), entity, all_entities=all_entities)
            for entity in entities
        ]
        return [
            PartPreviewRead(
                key=part.key, label=part.label, date_rule=part.date_rule, contents=part.contents
            )
            for part in PARTS
            if "entity" in part.scopes and any(part.omission(ctx) is None for ctx in contexts)
        ]

    @staticmethod
    def _filtered(
        query: Select[Any], legal_entity_id: UUID | None, year: int | None
    ) -> Select[Any]:
        if legal_entity_id is not None:
            query = query.where(AccountantExportRow.legal_entity_id == legal_entity_id)
        if year is not None:
            query = query.where(extract("year", AccountantExportRow.period_from) == year)
        return query

    # -- storage -------------------------------------------------------------

    def _visible(self, export_id: UUID, actor: Actor, action: str) -> AccountantExportRow:
        return visible_export(self.session, export_id, actor, action)

    @staticmethod
    def _read(row: AccountantExportRow) -> ExportRead:
        deleted = row.deleted_at is not None
        return ExportRead(
            id=row.id,
            legal_entity_id=row.legal_entity_id,
            scope=row.scope,
            period_from=row.period_from,
            period_to=row.period_to,
            built_by=row.built_by,
            built_at=row.built_at,
            expires_at=row.expires_at,
            deleted_at=row.deleted_at,
            # A deleted export's files are gone: its row is the trace, with nothing to fetch.
            parts=[]
            if deleted
            else [
                ExportPartRead(
                    part=number,
                    filename=entry["filename"],
                    size_bytes=entry["size_bytes"],
                    sha256=entry["sha256"],
                    download_url=download_path(row.id, number),
                )
                for number, entry in enumerate(row.parts, start=1)
            ],
            checklist=row.checklist,
        )

    def _put_all(self, parts: list[dict[str, object]], archives: list[Path]) -> None:
        """Puts each ZIP, one in memory at a time; a failure removes what was already put."""
        done: list[dict[str, object]] = []
        try:
            for entry, archive in zip(parts, archives, strict=True):
                self.storage.put(str(entry["key"]), archive.read_bytes(), ZIP_MIME)
                done.append(entry)
        except BaseException:
            self._delete_all(done)
            raise

    def _delete_all(self, parts: list[dict[str, object]]) -> None:
        for entry in parts:
            try:
                self.storage.delete(str(entry["key"]))
            except Exception:  # noqa: BLE001 - the failure being handled is the one to raise
                continue

    def _partition(self, files: list[BundleFile]) -> list[list[BundleFile]]:
        """The files in order, a new group whenever the next one would take the current group
        over the ceiling. A file larger than the ceiling gets a group to itself."""
        groups: list[list[BundleFile]] = [[]]
        used = 0
        for file in files:
            if groups[-1] and used + file.size > self.part_limit:
                groups.append([])
                used = 0
            groups[-1].append(file)
            used += file.size
        return groups

    @staticmethod
    def _archive_names(base: str, count: int) -> list[str]:
        if count == 1:
            return [f"{base}.zip"]
        return [f"{base}-part-{number}-of-{count}.zip" for number in range(1, count + 1)]

    @staticmethod
    def _write_zip(target: Path, files: list[BundleFile], built_at: dt.datetime) -> Path:
        stamp = _zip_time(built_at)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                info = zipfile.ZipInfo(file.path, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                info.file_size = file.size
                with archive.open(info, "w") as out:
                    if file.source is None:
                        out.write(file.data)
                    else:
                        with file.source.open("rb") as source:
                            shutil.copyfileobj(source, out, COPY_CHUNK)
        return target

    # -- steps ---------------------------------------------------------------

    @staticmethod
    def _check_period(period: Period) -> None:
        if period.date_from > period.date_to:
            raise ValidationFailed(
                ENTITY,
                "date_from",
                "la data iniziale è successiva a quella finale",
                expected=f"da <= {period.date_to.isoformat()}",
            )
        if (period.date_to - period.date_from).days >= MAX_PERIOD_DAYS:
            raise ValidationFailed(
                ENTITY,
                "date_to",
                f"il periodo non può superare {MAX_PERIOD_DAYS} giorni",
                expected="un mese, un trimestre o un anno",
            )

    def _context(
        self,
        actor: Actor,
        period: Period,
        legal_entity: LegalEntity | None,
        *,
        all_entities: bool = False,
    ) -> PartContext:
        pack_id: str | None = None
        if legal_entity is not None:
            profile = FiscalProfileRepository(self.session).get(legal_entity.id)
            pack_id = profile.pack_id if profile is not None else PACK_IT_FLAT_RATE
        only = self.legal_entities.single() if legal_entity is not None else None
        return PartContext(
            session=self.session,
            storage=self.storage,
            settings=self.settings,
            actor=actor,
            period=period,
            legal_entity=legal_entity,
            pack_id=pack_id,
            owns_shared_purchases=(
                not all_entities
                and legal_entity is not None
                and only is not None
                and only.id == legal_entity.id
            ),
            spool=self._spool,
        )

    def _entity_parts(
        self,
        legal_entity: LegalEntity,
        actor: Actor,
        period: Period,
        prefix: str,
        section: str,
        index: ExportIndex,
        *,
        all_entities: bool,
    ) -> list[BundleFile]:
        ctx = self._context(actor, period, legal_entity, all_entities=all_entities)
        files = self._run(
            [part for part in PARTS if "entity" in part.scopes], ctx, prefix, section, index
        )
        for missing in UNAVAILABLE:
            if "entity" in missing.scopes:
                index.parts.append(
                    PartLine(section, missing.key, missing.label, "unavailable", "", missing.reason)
                )
        return files

    def _shared_parts(self, actor: Actor, period: Period, index: ExportIndex) -> list[BundleFile]:
        ctx = self._context(actor, period, None)
        files = self._run(
            [part for part in PARTS if "shared" in part.scopes],
            ctx,
            f"{SHARED_FOLDER}/",
            SHARED_SECTION,
            index,
        )
        for missing in UNAVAILABLE:
            if "shared" in missing.scopes:
                index.parts.append(
                    PartLine(
                        SHARED_SECTION,
                        missing.key,
                        missing.label,
                        "unavailable",
                        "",
                        missing.reason,
                    )
                )
        return files

    @staticmethod
    def _run(
        parts: list[PartDefinition],
        ctx: PartContext,
        prefix: str,
        section: str,
        index: ExportIndex,
    ) -> list[BundleFile]:
        files: list[BundleFile] = []
        for part in parts:
            reason = part.omission(ctx)
            if reason is not None:
                index.parts.append(
                    PartLine(
                        section,
                        part.key,
                        part.label,
                        "omitted",
                        part.date_rule,
                        reason,
                        cause=part.omission_cause(ctx),
                    )
                )
                continue
            output: PartOutput = part.build(ctx)
            base = f"{prefix}{part.folder}/"
            files.extend(file.under(base) for file in output.files)
            for entry in output.entries:
                shown = IndexEntry(f"{base}{entry.path}", entry.count, entry.unit)
                index.parts.append(
                    PartLine(section, part.key, part.label, "included", part.date_rule, None, shown)
                )
            index.missing.extend(MissingLine(section, item) for item in output.missing)
        return files

    @staticmethod
    def _folder(legal_entity: LegalEntity, entities: list[LegalEntity]) -> str:
        """The entity's folder in an all-entities bundle: its name, made unique by the end
        of its id when two entities slug alike."""
        slug = _slug(legal_entity.name)
        clash = slug == SHARED_FOLDER or sum(1 for o in entities if _slug(o.name) == slug) > 1
        return f"{slug}-{legal_entity.id.hex[-8:]}" if clash else slug


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["ZIP_MIME", "AccountantExportService", "StoredExport", "download_path"]
