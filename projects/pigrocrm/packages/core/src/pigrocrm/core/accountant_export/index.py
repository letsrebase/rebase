"""The index of a bundle: what it holds, what it leaves out and why, and what it could not
attach. A CSV like the others (`csv_cells`), so the accountant opens it the same way.

Three tables separated by a blank line: who and when, the parts (one line per file or
folder, one line for a part left out), and the documents a part expected to attach and did
not find. A bundle cut into several archives adds a fourth: which archive holds each file.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from pigrocrm.core.accountant_export.parts import IndexEntry, MissingFile, OmissionCause, Period
from pigrocrm.core.csv_cells import BOM, row_line, text_cell

INDEX_FILE = "indice.csv"

Status = Literal["included", "omitted", "unavailable"]
STATUS_WORDS: dict[Status, str] = {
    "included": "Inclusa",
    "omitted": "Omessa",
    "unavailable": "Non disponibile",
}
UNIT_WORDS = {"rows": "righe", "files": "file"}

PART_COLUMNS = (
    "Sezione",
    "Parte",
    "Stato",
    "File",
    "Quantità",
    "Unità",
    "Regola di data",
    "Motivo",
)
MISSING_COLUMNS = ("Sezione", "Documento", "File", "Motivo")
ARCHIVE_COLUMNS = ("File", "Archivio")


@dataclass(frozen=True)
class PartLine:
    """One line of the parts table. `entry` is `None` for a part that was not built."""

    section: str
    key: str
    label: str
    status: Status
    date_rule: str
    reason: str | None = None
    entry: IndexEntry | None = None
    # Only for a part left out: what kind of reason it was.
    cause: OmissionCause | None = None


@dataclass(frozen=True)
class MissingLine:
    section: str
    missing: MissingFile


@dataclass
class ExportIndex:
    period: Period
    scope: str
    built_at: dt.datetime
    parts: list[PartLine] = field(default_factory=list)
    missing: list[MissingLine] = field(default_factory=list)
    # Which archive holds each file, filled in only when the bundle is cut into several.
    archives: dict[str, str] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        """The index as data, kept on the stored export (its checklist snapshot)."""
        return {
            "parts": [
                {
                    "section": line.section,
                    "key": line.key,
                    "label": line.label,
                    "status": line.status,
                    "date_rule": line.date_rule,
                    "reason": line.reason,
                    "cause": line.cause,
                    "path": line.entry.path if line.entry else None,
                    "count": line.entry.count if line.entry else None,
                    "unit": line.entry.unit if line.entry else None,
                }
                for line in self.parts
            ],
            "missing": [
                {
                    "section": item.section,
                    "document": item.missing.document,
                    "kind": item.missing.kind,
                    "reason": item.missing.reason,
                }
                for item in self.missing
            ],
        }

    def render(self) -> str:
        out = [
            BOM,
            row_line([text_cell("Esportazione per il commercialista")]),
            row_line([text_cell("Azienda"), text_cell(self.scope)]),
            row_line(
                [
                    text_cell("Periodo"),
                    f"{self.period.date_from.isoformat()} - {self.period.date_to.isoformat()}",
                ]
            ),
            row_line(
                [
                    text_cell("Creata il"),
                    self.built_at.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
                ]
            ),
            "\r\n",
            row_line([text_cell(column) for column in PART_COLUMNS]),
        ]
        for line in self.parts:
            entry = line.entry
            out.append(
                row_line(
                    [
                        text_cell(line.section),
                        text_cell(line.label),
                        STATUS_WORDS[line.status],
                        text_cell(entry.path) if entry else "",
                        str(entry.count) if entry else "",
                        UNIT_WORDS[entry.unit] if entry else "",
                        text_cell(line.date_rule),
                        text_cell(line.reason),
                    ]
                )
            )
        out.append("\r\n")
        out.append(row_line([text_cell(column) for column in MISSING_COLUMNS]))
        for item in self.missing:
            out.append(
                row_line(
                    [
                        text_cell(item.section),
                        text_cell(item.missing.document),
                        text_cell(item.missing.kind),
                        text_cell(item.missing.reason),
                    ]
                )
            )
        if len(set(self.archives.values())) > 1:
            out.append("\r\n")
            out.append(row_line([text_cell(column) for column in ARCHIVE_COLUMNS]))
            for path, archive in self.archives.items():
                out.append(row_line([text_cell(path), text_cell(archive)]))
        return "".join(out)
