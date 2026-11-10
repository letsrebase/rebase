"""What a part of the bundle is, and what one produces.

A part is a definition (what it is called, which date places a row in the period, which
modules and packs it needs) and a builder that reads its source and returns the files. The
registry (`registry.py`) lists the parts that exist; `UNAVAILABLE` lists the ones whose
source is not built yet, so the index says so instead of staying silent about them.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.modules.registry import ModuleKey, missing_modules
from pigrocrm.core.storage.base import DocumentStorage

# A part of the entity's own books, or of the purchases that belong to no legal entity.
Scope = Literal["entity", "shared"]


@dataclass(frozen=True)
class Period:
    """Both days included."""

    date_from: dt.date
    date_to: dt.date


@dataclass(frozen=True)
class PartContext:
    """What a builder reads. `legal_entity` is `None` only for a `shared` part."""

    session: Session
    storage: DocumentStorage
    settings: Settings
    actor: Actor
    period: Period
    legal_entity: LegalEntity | None
    # The pack of the entity's fiscal profile, `None` for the shared section.
    pack_id: str | None


@dataclass(frozen=True)
class BundleFile:
    """One file of the ZIP, `path` relative to the part's folder."""

    path: str
    data: bytes


@dataclass(frozen=True)
class IndexEntry:
    """A line of the index: a file with its row count, or the part's folder (an empty `path`)
    with its file count. `path` is relative to the part's folder."""

    path: str
    count: int
    # `rows` for a CSV, `files` for a folder of original files.
    unit: Literal["rows", "files"]


@dataclass(frozen=True)
class MissingFile:
    """A document the part expected to attach and did not find (or could not vouch for)."""

    document: str
    kind: str
    reason: str


@dataclass
class PartOutput:
    files: list[BundleFile] = field(default_factory=list)
    entries: list[IndexEntry] = field(default_factory=list)
    missing: list[MissingFile] = field(default_factory=list)


@dataclass(frozen=True)
class PartDefinition:
    key: str
    # The folder of the part inside the entity's folder, and the words the index uses.
    folder: str
    label: str
    # The date that places a row in the period, in the words the index prints.
    date_rule: str
    build: Callable[[PartContext], PartOutput]
    scope: Scope = "entity"
    # Every module listed must be on, or the part is omitted.
    requires_modules: tuple[ModuleKey, ...] = ()
    # Packs the part does not apply to (no IVA register for `non-it`).
    not_for_packs: tuple[str, ...] = ()

    def omission(self, ctx: PartContext) -> str | None:
        """Why this part is left out of the export, `None` when it is built."""
        off = missing_modules(ctx.settings, self.requires_modules)
        if off:
            return f"il modulo «{off[0]}» è spento"
        if ctx.pack_id is not None and ctx.pack_id in self.not_for_packs:
            return f"non si applica al regime dell'azienda ({ctx.pack_id})"
        return None


@dataclass(frozen=True)
class UnavailablePart:
    """A part of the design whose source does not exist yet."""

    key: str
    label: str
    reason: str
    scope: Scope = "entity"
