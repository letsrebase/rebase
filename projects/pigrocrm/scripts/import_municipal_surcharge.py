"""Rebuild `fiscal/datasets/municipal_surcharge_<year>.csv` from the Dipartimento delle Finanze.

The Department publishes, on «Addizionale comunale all'Irpef > Aliquote applicabili > Elenchi
generali aggiornati quotidianamente» (https://www.finanze.gov.it/it/fiscalita/fiscalita-regionale-e-locale/
Addizionale-comunale-allIRPEF/aliquote-applicabili/Elenchi-generali-aggiornati-quotidianamente), one
CSV per tax year with the rate, the brackets and the exemption of every municipality, rebuilt
every day from the deliberations published on the portal. This reads one of them and writes the
rows the application reads, in a layout of its own: the Department's brackets are sentences
(«Applicabile a scaglione di reddito da euro 28.000,01 fino a euro 50.000,00») and are parsed
here, once, so that nothing at run time reads a sentence.

    uv run python projects/pigrocrm/scripts/import_municipal_surcharge.py --year 2026
    uv run python projects/pigrocrm/scripts/import_municipal_surcharge.py --year 2025 --file <list>

Two things the list says about itself and the importer keeps:

* `0*` in the rate column means no deliberation is on the portal. Until 20 December of the year
  that is also what a municipality that has not deliberated yet shows; after it, the Department
  carries the previous year's rate over for a municipality that had the surcharge and keeps `0*`
  only for one that never instituted it. So a list read before 21 December is `final: false` in
  its sidecar and its `0*` rows are `pending` (no figure), and a list read after is `final: true`
  and its `0*` rows are `none` (no surcharge instituted).
* A row whose sentences do not parse into brackets that start at zero, follow one another and
  end open is `irregular`: it carries no figure and the run says how many there are.

The output is the CSV and a sidecar `municipal_surcharge_<year>.source.json` (URL, day downloaded,
row counts, SHA-256 of the file). Both are committed; nothing downloads at runtime.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

PAGE_URL = (
    "https://www.finanze.gov.it/it/fiscalita/fiscalita-regionale-e-locale/"
    "Addizionale-comunale-allIRPEF/aliquote-applicabili/Elenchi-generali-aggiornati-quotidianamente"
)
DOWNLOAD_URL = (
    "https://www1.finanze.gov.it/finanze2/dipartimentopolitichefiscali/fiscalitalocale/"
    "nuova_addcomirpef/download/download.php?anno={year}"
)
DATASETS_DIR = (
    Path(__file__).resolve().parents[1] / "packages/core/src/pigrocrm/core/fiscal/datasets"
)

FIELDS = [
    "cadastral_code",
    "name",
    "province",
    "status",
    "flag",
    "category_exemption",
    "exemption_up_to",
    "brackets",
    "deliberation_number",
    "deliberation_date",
    "published",
    "note",
]
RATE_COLUMNS = ["ALIQUOTA"] + [f"ALIQUOTA_{n}" for n in range(2, 13)]
BRACKET_COLUMNS = ["FASCIA"] + [f"FASCIA_{n}" for n in range(2, 13)]
# The date after which an absent deliberation no longer means «not yet» (the list's own text).
LAST_DELIBERATION_DAY = (12, 20)
# Two brackets that the Department writes «fino a 28.000,00» and «da 28.000,01» (or «da 28.001»)
# are contiguous: a bracket starts within a euro of where the one before ended.
CONTIGUOUS_WITHIN = Decimal(1)
NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?")
STANDARD_EXEMPTION_START = "esenzione per redditi imponibili fino a euro"
STANDARD_EXEMPTION = re.compile(rf"{STANDARD_EXEMPTION_START} ({NUMBER.pattern})")


class Irregular(Exception):
    """A row whose sentences are not the brackets they should be."""


def number(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def money(value: Decimal) -> str:
    return format(value.normalize(), "f")


def parse_rate(text: str) -> Decimal:
    try:
        return Decimal(text.strip().replace(",", "."))
    except InvalidOperation as error:
        raise Irregular(f"rate {text!r}") from error


def parse_pairs(
    row: dict[str, str],
) -> tuple[Decimal | None, bool, list[tuple[Decimal | None, Decimal]]]:
    """The general exemption threshold (or `None`), whether the municipality also exempts a
    category of taxpayer, and the brackets of a row, as `(upper bound or `None` for the open
    one, rate in percent)` in income order.

    A general exemption is the Department's own sentence «Esenzione per redditi imponibili fino
    a euro N» on a rate-0 pair. The list's `IMPORTO_ESENTE` column repeats N for the rows with
    flags 2 and 4 and is 0 for many others (flag 0 and 5, where the sentence is still there), so
    the sentence decides and the column is a cross-check when it is not 0; when the flag says
    there is a general exemption and the sentence is worded some other way (a municipality's
    own), the column is the threshold. Any other rate-0 pair is an exemption for a category
    (pensioners, employees, a family with children), which is not a figure for the taxpayer
    this module is asked about, and is only flagged."""
    general = number(row["IMPORTO_ESENTE"]) if row["IMPORTO_ESENTE"].strip() else Decimal(0)
    has_general = row["FLAG_NUOVA"].strip() in {"2", "4"}
    exemption: Decimal | None = None
    category = False
    parsed: list[tuple[Decimal | None, Decimal | None, Decimal]] = []  # lower, upper, rate
    for rate_column, text_column in zip(RATE_COLUMNS, BRACKET_COLUMNS, strict=True):
        rate_text, text = (
            row[rate_column].strip(),
            re.sub(r"\s+", " ", row[text_column]).strip().lower(),
        )
        if not rate_text and not text:
            continue
        rate = parse_rate(rate_text)
        values = [number(n) for n in NUMBER.findall(text)]
        if text.startswith("esenzione") and rate == 0:
            standard = STANDARD_EXEMPTION.fullmatch(text)
            if text.startswith(STANDARD_EXEMPTION_START) and not standard:
                raise Irregular(f"exemption {text!r}")
            if standard:
                amount = number(standard.group(1))
                if exemption is not None or (general > 0 and amount != general):
                    raise Irregular(f"exemption {text!r}, the list's own column says {general}")
                exemption = amount
            elif has_general and exemption is None and general > 0:
                exemption = general
            else:
                category = True
        elif "aliquota unica" in text:
            parsed.append((None, None, rate))
        elif "oltre" in text and values:
            parsed.append((values[0], values[1] if len(values) > 1 else None, rate))
        elif re.search(r"\b(da|dal|a|al)\b", text) and len(values) == 2:
            parsed.append((values[0], values[1], rate))
        elif re.search(r"\b(da|dal)\b", text) and len(values) == 1:
            parsed.append((values[0], None, rate))
        elif "fino" in text and len(values) == 1:
            parsed.append((None, values[0], rate))
        else:
            raise Irregular(f"bracket {text!r}")
    if has_general and exemption is None:
        raise Irregular("flag says an exemption, the pairs have none")
    if not parsed:
        raise Irregular("no bracket")
    brackets: list[tuple[Decimal | None, Decimal]] = []
    previous_upper: Decimal | None = Decimal(0)
    for index, (lower, upper, rate) in enumerate(parsed):
        if previous_upper is None:
            raise Irregular("a bracket after the open one")
        # «da 28.000,01», «oltre 50.000,00» and «da 0» all start where the one before ended, to
        # the cent; a bracket that states no start (the first, «fino a»; the only one, «unica»)
        # starts at zero.
        if lower is not None and not Decimal(0) <= lower - previous_upper <= CONTIGUOUS_WITHIN:
            raise Irregular(
                f"bracket {index + 1} starts at {lower}, the one before ended at {previous_upper}"
            )
        if upper is not None and upper <= previous_upper:
            raise Irregular(f"bracket {index + 1} ends at {upper}, before its start")
        brackets.append((upper, rate))
        previous_upper = upper
    if previous_upper is not None:
        raise Irregular("the last bracket is not open")
    return exemption, category, brackets


def iso(value: str) -> str:
    value = value.strip()
    return datetime.strptime(value, "%d/%m/%Y").date().isoformat() if value else ""


def convert(content: str, *, final: bool) -> tuple[list[dict[str, str]], Counter[str]]:
    rows: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    for source in csv.DictReader(StringIO(content), delimiter=";"):
        out = {
            "cadastral_code": source["CODICE_CATASTALE"].strip(),
            "name": source["COMUNE"].strip(),
            "province": source["PR"].strip(),
            "status": "",
            "flag": source["FLAG_NUOVA"].strip(),
            "category_exemption": "",
            "exemption_up_to": "",
            "brackets": "",
            "deliberation_number": source["NUMERO_DELIBERA"].strip(),
            "deliberation_date": iso(source["DATA_DELIBERA"]),
            "published": iso(source["DATA_PUBBLICAZIONE"]),
            "note": re.sub(r"\s+", " ", source["NOTE"]).strip(),
        }
        if source["ALIQUOTA"].strip() == "0*":
            out["status"] = "none" if final else "pending"
        else:
            try:
                exemption, category, brackets = parse_pairs(source)
                out["status"] = "adopted"
                out["category_exemption"] = "1" if category else ""
                out["exemption_up_to"] = money(exemption) if exemption is not None else ""
                out["brackets"] = "|".join(
                    f"{money(upper) if upper is not None else ''}:{money(rate)}"
                    for upper, rate in brackets
                )
            except Irregular as error:
                out["status"] = "irregular"
                print(f"irregular: {out['cadastral_code']} {out['name']}: {error}", file=sys.stderr)
        counts[out["status"]] += 1
        rows.append(out)
    return rows, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--year", type=int, required=True, help="the tax year of the list")
    parser.add_argument("--file", type=Path, help="a list already downloaded")
    parser.add_argument(
        "--downloaded",
        type=date.fromisoformat,
        default=date.today(),
        help="the day the list was downloaded, YYYY-MM-DD (default: today); it decides `final`",
    )
    args = parser.parse_args()
    url = DOWNLOAD_URL.format(year=args.year)
    if args.file:
        raw = args.file.read_bytes()
    else:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            raw = response.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp1252")
    final = args.downloaded > date(args.year, *LAST_DELIBERATION_DAY)
    rows, counts = convert(content, final=final)
    if len(rows) < 7_800 or len({row["cadastral_code"] for row in rows}) != len(rows):
        raise SystemExit(
            f"{len(rows)} rows, or a repeated cadastral code: not a list of municipalities"
        )
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    stem = DATASETS_DIR / f"municipal_surcharge_{args.year}"
    with stem.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["cadastral_code"]))
    stem.with_suffix(".source.json").write_text(
        json.dumps(
            {
                "source": "Dipartimento delle Finanze, addizionale comunale all'Irpef",
                "page": PAGE_URL,
                "url": url,
                "tax_year": args.year,
                "downloaded": args.downloaded.isoformat(),
                "final": final,
                "rows": len(rows),
                "by_status": dict(sorted(counts.items())),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{args.year}: {len(rows)} municipalities {dict(counts)}", file=sys.stderr)


if __name__ == "__main__":
    main()
