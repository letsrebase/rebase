"""Builds the bundle: one ZIP for one legal entity and one period, or for every legal entity
(spec accountant export § 1).

An export holds every fiscal figure of an entity, so it is an administrator's whose view
covers it: the one-entity export needs the entity inside the admin's scope, the
«all entities» export an admin whose scope is not limited, and it carries the shared
purchases (those of no legal entity) in a section of their own. An agent reaches it only
with `full_access`.

The service returns bytes and the index it wrote; nothing is stored here (the stored
artefact, its retention and its delivery are REB-1012's) and nothing is written to the
database: it only reads.
"""

import datetime as dt
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.index import (
    INDEX_FILE,
    ExportIndex,
    MissingLine,
    PartLine,
)
from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartDefinition,
    PartOutput,
    Period,
)
from pigrocrm.core.accountant_export.registry import PARTS, UNAVAILABLE
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.base import DocumentStorage

ENTITY = "accountant_export"
ACTION = "build_accountant_export"
ALL_ENTITIES_SECTION = "Tutte le aziende"
SHARED_SECTION = "Acquisti condivisi"
SHARED_FOLDER = "condivisi"
# A month, a quarter or a year: nothing longer is one period.
MAX_PERIOD_DAYS = 366
ZIP_MIME = "application/zip"


@dataclass(frozen=True)
class AccountantExport:
    filename: str
    content: bytes
    index: ExportIndex


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "azienda"


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

    def build(
        self,
        period: Period,
        actor: Actor,
        legal_entity_id: UUID | None = None,
        *,
        all_entities: bool = False,
        now: dt.datetime | None = None,
    ) -> AccountantExport:
        """The bundle of `period`: one legal entity's (the named one, or the only one), or
        every legal entity's with the shared purchases when `all_entities`."""
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
                raise NotFound("legal_entities", legal_entity_id)
            entities = [
                self.legal_entities.require_single(legal_entity_id, entity=ENTITY),
            ]
            scope_name = entities[0].name

        index = ExportIndex(period=period, scope=scope_name, built_at=built_at)
        files: list[BundleFile] = []
        for legal_entity in entities:
            prefix = ""
            section = legal_entity.name
            if all_entities:
                prefix = f"{self._folder(legal_entity, entities)}/"
            files.extend(self._entity_parts(legal_entity, actor, period, prefix, section, index))
        if all_entities:
            files.extend(self._shared_parts(actor, period, index))

        files.append(BundleFile(INDEX_FILE, index.render().encode("utf-8")))
        return AccountantExport(
            filename=self._filename(entities, all_entities, period),
            content=self._zip(files, built_at),
            index=index,
        )

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
        self, actor: Actor, period: Period, legal_entity: LegalEntity | None
    ) -> PartContext:
        pack_id: str | None = None
        if legal_entity is not None:
            profile = FiscalProfileRepository(self.session).get(legal_entity.id)
            pack_id = profile.pack_id if profile is not None else PACK_IT_FLAT_RATE
        return PartContext(
            session=self.session,
            storage=self.storage,
            settings=self.settings,
            actor=actor,
            period=period,
            legal_entity=legal_entity,
            pack_id=pack_id,
        )

    def _entity_parts(
        self,
        legal_entity: LegalEntity,
        actor: Actor,
        period: Period,
        prefix: str,
        section: str,
        index: ExportIndex,
    ) -> list[BundleFile]:
        ctx = self._context(actor, period, legal_entity)
        files = self._run(
            [part for part in PARTS if part.scope == "entity"], ctx, prefix, section, index
        )
        for missing in UNAVAILABLE:
            if missing.scope == "entity":
                index.parts.append(
                    PartLine(section, missing.key, missing.label, "unavailable", "", missing.reason)
                )
        return files

    def _shared_parts(self, actor: Actor, period: Period, index: ExportIndex) -> list[BundleFile]:
        ctx = self._context(actor, period, None)
        files = self._run(
            [part for part in PARTS if part.scope == "shared"],
            ctx,
            f"{SHARED_FOLDER}/",
            SHARED_SECTION,
            index,
        )
        for missing in UNAVAILABLE:
            if missing.scope == "shared":
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
                    PartLine(section, part.key, part.label, "omitted", part.date_rule, reason)
                )
                continue
            output: PartOutput = part.build(ctx)
            base = f"{prefix}{part.folder}/"
            files.extend(BundleFile(f"{base}{file.path}", file.data) for file in output.files)
            for entry in output.entries:
                shown = IndexEntry(f"{base}{entry.path}", entry.count, entry.unit)
                index.parts.append(
                    PartLine(section, part.key, part.label, "included", part.date_rule, None, shown)
                )
            index.missing.extend(MissingLine(section, item) for item in output.missing)
        return files

    @staticmethod
    def _folder(legal_entity: LegalEntity, entities: list[LegalEntity]) -> str:
        """The entity's folder in an all-entities bundle: its name, made unique by the start
        of its id when two entities slug alike."""
        slug = _slug(legal_entity.name)
        clash = sum(1 for other in entities if _slug(other.name) == slug) > 1
        return f"{slug}-{str(legal_entity.id)[:8]}" if clash else slug

    @staticmethod
    def _filename(entities: list[LegalEntity], all_entities: bool, period: Period) -> str:
        scope = "tutte-le-aziende" if all_entities else _slug(entities[0].name)
        days = f"{period.date_from.isoformat()}_{period.date_to.isoformat()}"
        return f"commercialista-{scope}-{days}.zip"

    @staticmethod
    def _zip(files: list[BundleFile], built_at: dt.datetime) -> bytes:
        buffer = io.BytesIO()
        stamp = _zip_time(built_at)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                info = zipfile.ZipInfo(file.path, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, file.data)
        return buffer.getvalue()


__all__ = ["ZIP_MIME", "AccountantExport", "AccountantExportService"]
