"""Rebuild `places/datasets/istat_municipalities.csv` from ISTAT's «Elenco comuni italiani».

ISTAT publishes the list at a permanent address that does not change at each update
(https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/,
section «Permalink aggiornato»). Use the `.xlsx`: the `.csv` beside it on the same
path is a frozen copy of January 2024 and misses the 2026 changes (the Sardinian
provinces, Castegnero Nanto).

    uv run python projects/pigrocrm/scripts/import_istat_municipalities.py
    uv run python projects/pigrocrm/scripts/import_istat_municipalities.py --file <workbook.xlsx>

The output is the CSV the application reads and a sidecar `istat_municipalities.source.json`
that says where it came from: the URL, the day it was downloaded, the sheet's own title
(which carries ISTAT's date of validity) and the SHA-256 of the file. Both are committed;
nothing downloads at runtime.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl

SOURCE_URL = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.xlsx"
PAGE_URL = "https://www.istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni/"
DATA_DIR = Path(__file__).resolve().parents[1] / "packages/core/src/pigrocrm/core/places/datasets"
CSV_PATH = DATA_DIR / "istat_municipalities.csv"
SOURCE_PATH = DATA_DIR / "istat_municipalities.source.json"

# The workbook's columns by the words its header carries, so a column ISTAT adds or moves
# does not shift the others; a header that no longer says one of these stops the import.
COLUMNS: dict[str, str] = {
    "istat_code": "Codice Comune formato alfanumerico",
    "cadastral_code": "Codice Catastale del Comune",
    "name": "Denominazione (Italiana e straniera)",
    "name_it": "Denominazione in italiano",
    "name_other": "Denominazione altra lingua",
    "province": "Sigla automobilistica",
    "region_code": "Codice Regione",
    "region_name": "Denominazione Regione",
}
SHEET_PREFIX = "CODICI"
# The count ISTAT's page states for the list: 7,894 municipalities from 21 February 2026.
EXPECTED_MIN_ROWS = 7_800


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse(content: bytes) -> tuple[str, list[dict[str, str]]]:
    book = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheets = [name for name in book.sheetnames if name.startswith(SHEET_PREFIX)]
    if len(sheets) != 1:
        raise SystemExit(f"expected one sheet named «{SHEET_PREFIX} ...», found {book.sheetnames}")
    title = sheets[0]
    rows = book[title].iter_rows(values_only=True)
    header = [re.sub(r"\s+", " ", text(cell)).lower() for cell in next(rows)]
    index: dict[str, int] = {}
    for key, label in COLUMNS.items():
        wanted = re.sub(r"\s+", " ", label).lower()
        if wanted not in header:
            raise SystemExit(f"column «{label}» is not in the workbook's header: {header}")
        index[key] = header.index(wanted)
    parsed: list[dict[str, str]] = []
    for row in rows:
        if not any(row):
            continue
        parsed.append({key: text(row[position]) for key, position in index.items()})
    return title, parsed


def check(rows: list[dict[str, str]]) -> None:
    if len(rows) < EXPECTED_MIN_ROWS:
        raise SystemExit(f"{len(rows)} rows: far fewer municipalities than Italy has")
    for key in ("istat_code", "cadastral_code"):
        values = [row[key] for row in rows]
        if len(set(values)) != len(values):
            raise SystemExit(f"{key} is not unique in the file")
    for row in rows:
        if not re.fullmatch(r"\d{6}", row["istat_code"]):
            raise SystemExit(f"odd ISTAT code {row['istat_code']!r} for {row['name']!r}")
        if not re.fullmatch(r"[A-Z]\d{3}", row["cadastral_code"]):
            raise SystemExit(f"odd cadastral code {row['cadastral_code']!r} for {row['name']!r}")
        if not re.fullmatch(r"[A-Z]{2}", row["province"]):
            raise SystemExit(f"odd province {row['province']!r} for {row['name']!r}")
        if not row["name"] or not row["region_code"] or not row["region_name"]:
            raise SystemExit(f"an empty name or region in {row}")


def write(rows: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["istat_code"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--file", type=Path, help="a workbook already downloaded")
    parser.add_argument(
        "--downloaded",
        type=date.fromisoformat,
        default=date.today(),
        help="the day the workbook was downloaded, YYYY-MM-DD (default: today)",
    )
    args = parser.parse_args()
    if args.file:
        content = args.file.read_bytes()
    else:
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            content = response.read()
    title, rows = parse(content)
    check(rows)
    write(rows)
    SOURCE_PATH.write_text(
        json.dumps(
            {
                "source": "ISTAT, Elenco comuni italiani (codici delle unita amministrative)",
                "page": PAGE_URL,
                "url": SOURCE_URL,
                "downloaded": args.downloaded.isoformat(),
                "sheet": title,
                "rows": len(rows),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(rows)} municipalities written to {CSV_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
