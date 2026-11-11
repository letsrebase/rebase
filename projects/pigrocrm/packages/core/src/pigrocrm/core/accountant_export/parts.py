"""What a part of the bundle is, and what one produces.

A part is a definition (what it is called, which date places a row in the period, which
modules and packs it needs) and a builder that reads its source and returns the files. The
registry (`registry.py`) lists the parts that exist; `UNAVAILABLE` lists the ones whose
source is not built yet, so the index says so instead of staying silent about them.
"""

import datetime as dt
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.modules.registry import ModuleKey, missing_modules
from pigrocrm.core.storage.base import DocumentStorage

# A part of the entity's own books, or of the purchases that belong to no legal entity.
Scope = Literal["entity", "shared"]
# Why a part is left out: its module is off (the page never names it) or the entity's pack
# does not carry it (the page names the part).
OmissionCause = Literal["module", "pack"]


@dataclass(frozen=True)
class Period:
    """Both days included."""

    date_from: dt.date
    date_to: dt.date


@dataclass(frozen=True)
class BundleFile:
    """One file of the ZIP, `path` relative to the part's folder. Small generated files (CSVs)
    hold their `data`; an original file out of the storage sits on disk in the build's spool
    (`source`) and is read in chunks when the ZIP is written, so a year of PDFs is never in
    memory at once."""

    path: str
    data: bytes = b""
    source: Path | None = None
    size: int = -1

    def __post_init__(self) -> None:
        if self.size < 0:
            object.__setattr__(
                self, "size", self.source.stat().st_size if self.source else len(self.data)
            )

    def under(self, folder: str) -> "BundleFile":
        return BundleFile(f"{folder}{self.path}", self.data, self.source, self.size)


class Spool:
    """A directory for the files a build holds on disk instead of in memory. The caller owns
    it (a temporary directory that goes with the build); a part asks it to keep a file and
    gets back the `BundleFile` that points at it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._numbers = itertools.count()

    def keep(self, path: str, data: bytes) -> BundleFile:
        target = self.directory / f"{next(self._numbers):08d}"
        target.write_bytes(data)
        return BundleFile(path, source=target, size=len(data))


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
    # The purchases of no legal entity belong to this entity's export: the space has only one
    # legal entity and no all-entities export is being built, so «shared» can only mean it.
    owns_shared_purchases: bool = False
    # Where the original files of the bundle wait on disk; `None` keeps them in memory.
    spool: Spool | None = None

    def attach(self, path: str, data: bytes) -> BundleFile:
        """An original file for the bundle: on the spool when there is one."""
        return self.spool.keep(path, data) if self.spool else BundleFile(path, data)


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
    # What the part is made of, in the words the export page prints beside it.
    contents: str
    build: Callable[[PartContext], PartOutput]
    # Where the part runs: for one legal entity, for the purchases of no legal entity (the
    # «all entities» export only), or both.
    scopes: tuple[Scope, ...] = ("entity",)
    # Every module listed must be on, or the part is omitted.
    requires_modules: tuple[ModuleKey, ...] = ()
    # Packs the part does not apply to (no IVA register for `non-it`).
    not_for_packs: tuple[str, ...] = ()
    # The name an administrator ticks to ask for the part (the payslips, § 4): a part with one
    # is built only when asked and is not in the list of what a build holds, which the page
    # draws as checkboxes of their own.
    opt_in: str | None = None

    def omission_cause(self, ctx: PartContext) -> OmissionCause | None:
        """Why this part is left out, as a kind: a module that is off, or a pack it does not
        apply to. `None` when it is built."""
        if missing_modules(ctx.settings, self.requires_modules):
            return "module"
        if ctx.pack_id is not None and ctx.pack_id in self.not_for_packs:
            return "pack"
        return None

    def omission(self, ctx: PartContext) -> str | None:
        """Why this part is left out of the export, `None` when it is built."""
        cause = self.omission_cause(ctx)
        if cause == "module":
            off = missing_modules(ctx.settings, self.requires_modules)
            return f"il modulo «{off[0]}» è spento"
        if cause == "pack":
            return f"non si applica al regime dell'azienda ({ctx.pack_id})"
        return None


@dataclass(frozen=True)
class UnavailablePart:
    """A part of the design whose source does not exist yet."""

    key: str
    label: str
    reason: str
    scopes: tuple[Scope, ...] = ("entity",)
