"""The ledger of the tax law each year was computed with (REB-1084).

Once a year has figures in a pack, its estimates, readings and due items must keep reading the
law they were computed with. Nothing in the data model stops a later edit of a 2026 constant
from changing every 2026 figure in every space, so this module does: `law_ledger.json` holds a
fingerprint of every section of every pack for every year that has law, and
`tests/test_law_ledger.py` recomputes them.

How it decides what is "closed"
-------------------------------
There is no `closed_years` list to keep in step with the calendar. Every year that has an entry
is fingerprinted, and an existing fingerprint may not change. A brand-new `(pack, section, year)`
is an addition and passes, so entering the 2027 law (REB-1085) needs no ceremony beyond running
`--add-year`; what is already recorded is frozen from the day it is recorded. A list of closed
years would need someone to remember to close 2026, and forgetting would leave exactly the silent
edit this ledger exists to refuse. The cost of this choice is that a year is protected only once
its fingerprints are in the ledger, which is why the runbook has `--add-year` in the same PR that
adds the entry.

What a fingerprint is
---------------------
The SHA-256 of the canonical JSON of the section's data for that year, read through the section's
own lookup (`FiscalPack.law_for`, `irpef_for`, `inps_for`, `chamber_fee_for`, `calendar_for`,
`payment_rules_for`, `ceilings_for`, `charges_for`, `loss_carry_for`), so an edit to a statutory
entry shows up as a change to every year it governs, not only to the year it starts in. Dataclass
fields are included with their class names, Decimals as strings, dates as ISO text, and so are the
sources: correcting a citation is a correction. Three yearly tables live inside a larger object
and are split out, so that adding a year to them never changes an earlier year's fingerprint: the
IRAP regional rates (`irap_rates`, taken out of `laws`), the legal interest rates (`legal_interest`,
taken out of `payment_rules`) and the municipal surcharge lists (`municipal_surcharge`, the
SHA-256 of the dataset's CSV and of its `.source.json`, recorded only once the sidecar says the
list is `final`: the Finanze rebuilds an unfinished list daily, and refreshing it is not a
correction).

A deliberate fix
----------------
A change to a recorded year fails the test unless the same diff adds a correction entry to the
ledger: the pack, the section, the year, the primary source, the reason and the new fingerprint.
`python -m pigrocrm.core.fiscal.law_ledger --correct ...` writes it. The original fingerprint
stays in the ledger as the record of what the year first read; the latest correction is what the
code is now expected to hash to. Deleting a recorded year's law is a change too and is refused the
same way.

Commands (from the repository root)::

    uv run python -m pigrocrm.core.fiscal.law_ledger                # check, exit 1 on a violation
    uv run python -m pigrocrm.core.fiscal.law_ledger --add-year     # record what is not recorded
    uv run python -m pigrocrm.core.fiscal.law_ledger --correct it-standard:1 irpef 2026 \\
        --source "..." --reason "..."
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from pathlib import Path
from typing import Any

from pigrocrm.core.fiscal.law_municipal_surcharge import DATASETS
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.pack import shipped_packs as _shipped

LEDGER_PATH = Path(__file__).resolve().parent / "law_ledger.json"
LEDGER_VERSION = 1

Ledger = dict[str, Any]


# --- the canonical form --------------------------------------------------------------------


def canonical(value: object) -> object:
    """`value` as plain JSON data, the same on every run: a Decimal keeps its exact text, a
    dataclass carries its class name, a set is sorted. A type that has no stable form raises
    `TypeError` instead of being hashed by its address."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, Fraction):
        return {"fraction": f"{value.numerator}/{value.denominator}"}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, Enum):
        return {"enum": f"{type(value).__name__}.{value.name}"}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data: dict[str, object] = {"__class__": type(value).__name__}
        for field in dataclasses.fields(value):
            data[field.name] = canonical(getattr(value, field.name))
        return data
    if isinstance(value, tuple | list):
        return [canonical(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((canonical(item) for item in value), key=_dump)
    if isinstance(value, Mapping):
        return {str(key): canonical(item) for key, item in sorted(value.items(), key=_by_key)}
    raise TypeError(f"no stable form for {type(value).__name__}")


def _by_key(item: tuple[object, object]) -> str:
    return str(item[0])


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value: object) -> str:
    """The SHA-256 of `value`'s canonical JSON."""
    return hashlib.sha256(_dump(canonical(value)).encode("utf-8")).hexdigest()


# --- what each section hands the ledger ------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One section of a pack: the years it has law for and the data it reads for one of them.

    A statutory section (an entry holds until a later one replaces it) is fingerprinted for every
    year from its first entry to the pack's horizon; a yearly one for the years it has an entry
    of its own."""

    name: str
    statutory: bool
    first_years: Callable[[FiscalPack], list[int]]
    material: Callable[[FiscalPack, int], object | None]


def _without_irap_rates(pack: FiscalPack, year: int) -> object | None:
    law = pack.law_for(year)
    if law is None:
        return None
    company = law.company_tax
    if company is not None and company.irap is not None:
        law = replace(
            law, company_tax=replace(company, irap=replace(company.irap, regional_rates=()))
        )
    return law


def _irap_years(pack: FiscalPack) -> list[int]:
    return sorted(
        {
            table.year
            for law in pack.laws
            if law.company_tax is not None and law.company_tax.irap is not None
            for table in law.company_tax.irap.regional_rates
        }
    )


def _irap_table(pack: FiscalPack, year: int) -> object | None:
    for law in pack.laws:
        if law.company_tax is not None and law.company_tax.irap is not None:
            for table in law.company_tax.irap.regional_rates:
                if table.year == year:
                    return table
    return None


def _payment_rules(pack: FiscalPack, year: int) -> object | None:
    rules = pack.payment_rules_for(year)
    return (
        None
        if rules is None
        else replace(rules, shortfall=replace(rules.shortfall, legal_interest=()))
    )


def _legal_interest_years(pack: FiscalPack) -> list[int]:
    return sorted(
        {rate.year for entry in pack.payment_rules for rate in entry.shortfall.legal_interest}
    )


def _legal_interest(pack: FiscalPack, year: int) -> object | None:
    for entry in pack.payment_rules:
        for rate in entry.shortfall.legal_interest:
            if rate.year == year:
                return rate
    return None


def _municipal_years(pack: FiscalPack) -> list[int]:
    return [] if pack.municipal_surcharge is None else list(pack.municipal_surcharge.years())


def _municipal_files(pack: FiscalPack, year: int) -> object | None:
    if pack.municipal_surcharge is None or year not in pack.municipal_surcharge.years():
        return None
    stem = DATASETS / f"municipal_surcharge_{year}"
    if not json.loads(stem.with_suffix(".source.json").read_text(encoding="utf-8"))["final"]:
        # The Finanze rebuilds a list daily until the year's deliberations are in: a refresh of
        # a list that says it is not final is expected, not a correction.
        return None
    return {
        suffix: hashlib.sha256(stem.with_suffix(suffix).read_bytes()).hexdigest()
        for suffix in (".csv", ".source.json")
    }


def _withholding_years(pack: FiscalPack) -> list[int]:
    """The withholding is stated once for the pack, so it is read from the pack's first law."""
    return [] if pack.sales_withholding is None or not pack.laws else [pack.laws[0].from_year]


def _starts(entries: tuple[Any, ...], attribute: str) -> list[int]:
    return sorted({getattr(entry, attribute) for entry in entries})


SECTIONS: tuple[Section, ...] = (
    Section("laws", True, lambda p: _starts(p.laws, "from_year"), _without_irap_rates),
    Section(
        "calendars",
        True,
        lambda p: _starts(p.calendars, "from_year"),
        lambda p, y: p.calendar_for(y),
    ),
    Section("payment_rules", True, lambda p: _starts(p.payment_rules, "from_year"), _payment_rules),
    Section(
        "ceilings",
        True,
        lambda p: _starts(p.ceilings, "from_year"),
        lambda p, y: p.ceilings_for(y) or None,
    ),
    Section(
        "charges",
        True,
        lambda p: _starts(p.charges, "from_year"),
        lambda p, y: p.charges_for(y) or None,
    ),
    Section(
        "loss_carry",
        True,
        lambda p: _starts(p.loss_carry, "from_year"),
        lambda p, y: p.loss_carry_for(y),
    ),
    Section("sales_withholding", True, _withholding_years, lambda p, y: p.sales_withholding),
    Section("inps", False, lambda p: _starts(p.inps, "year"), lambda p, y: p.inps_for(y)),
    Section("irpef", False, lambda p: _starts(p.irpef, "year"), lambda p, y: p.irpef_for(y)),
    Section(
        "chamber_fee",
        False,
        lambda p: _starts(p.chamber_fees, "year"),
        lambda p, y: p.chamber_fee_for(y),
    ),
    Section("irap_rates", False, _irap_years, _irap_table),
    Section("legal_interest", False, _legal_interest_years, _legal_interest),
    Section("municipal_surcharge", False, _municipal_years, _municipal_files),
)


def pack_key(pack: FiscalPack) -> str:
    return f"{pack.id}:{pack.version}"


def shipped_packs() -> dict[str, FiscalPack]:
    """Every pack the code ships, by `pack_key`."""
    return {pack_key(pack): pack for pack in _shipped()}


def compute(packs: Mapping[str, FiscalPack] | None = None) -> dict[str, dict[str, dict[str, str]]]:
    """The fingerprint of every section of every pack for every year that has law:
    `{pack: {section: {year: sha256}}}`. A statutory section is read for each year from its first
    entry to the latest year any section of the pack has law for (its horizon); a yearly one for
    the years it has an entry of its own."""
    result: dict[str, dict[str, dict[str, str]]] = {}
    for key, pack in sorted((packs if packs is not None else shipped_packs()).items()):
        starts = {section.name: section.first_years(pack) for section in SECTIONS}
        horizon = max((years[-1] for years in starts.values() if years), default=0)
        sections: dict[str, dict[str, str]] = {}
        for section in SECTIONS:
            first = starts[section.name]
            if not first:
                continue
            years = range(first[0], horizon + 1) if section.statutory else first
            found = {
                str(year): fingerprint(data)
                for year in years
                if (data := section.material(pack, year)) is not None
            }
            if found:
                sections[section.name] = found
        result[key] = sections
    return result


# --- the ledger file and its check -----------------------------------------------------------


def load(path: Path = LEDGER_PATH) -> Ledger:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save(ledger: Ledger, path: Path = LEDGER_PATH) -> None:
    path.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Violation:
    pack: str
    section: str
    year: int
    problem: str

    def __str__(self) -> str:
        return f"{self.pack} / {self.section} / {self.year}: {self.problem}"


@dataclass(frozen=True)
class Verdict:
    """What the check found: `violations` stop a change, `added` are the `(pack, section, year)`
    the ledger does not record yet."""

    violations: list[Violation]
    added: list[tuple[str, str, int]]


def _expected(ledger: Ledger, pack: str, section: str, year: int, recorded: str) -> str:
    expected = recorded
    for entry in ledger.get("corrections", []):
        if (entry["pack"], entry["section"], entry["year"]) == (pack, section, year):
            expected = entry["fingerprint"]
    return expected


def check(ledger: Ledger, packs: Mapping[str, FiscalPack] | None = None) -> Verdict:
    """Compares the packs with the ledger. A recorded year whose fingerprint differs from the
    recorded one (or from the latest correction's) is a violation, and so is a recorded year
    that has no law any more; an unrecorded one is an addition. A correction with no source or no
    reason, or for a year the ledger does not record, is a violation too."""
    violations: list[Violation] = []
    added: list[tuple[str, str, int]] = []
    current = compute(packs)
    recorded_all: dict[str, dict[str, dict[str, str]]] = ledger["fingerprints"]
    for pack, sections in current.items():
        for section, years in sections.items():
            for year, digest in years.items():
                recorded = recorded_all.get(pack, {}).get(section, {}).get(year)
                if recorded is None:
                    added.append((pack, section, int(year)))
                elif digest != _expected(ledger, pack, section, int(year), recorded):
                    violations.append(
                        Violation(
                            pack,
                            section,
                            int(year),
                            "the law changed after the year was recorded; a deliberate fix "
                            "needs a correction entry (pack, section, year, source, reason) in "
                            "law_ledger.json",
                        )
                    )
    for pack, sections in recorded_all.items():
        if packs is not None and pack not in packs:
            continue
        for section, years in sections.items():
            for year in years:
                if year not in current.get(pack, {}).get(section, {}):
                    violations.append(
                        Violation(pack, section, int(year), "a recorded year has no law any more")
                    )
    for entry in ledger.get("corrections", []):
        where = (entry["pack"], entry["section"], entry["year"])
        if not str(entry.get("source", "")).strip() or not str(entry.get("reason", "")).strip():
            violations.append(Violation(*where, "a correction needs a source and a reason"))
        if str(entry["year"]) not in recorded_all.get(entry["pack"], {}).get(entry["section"], {}):
            violations.append(
                Violation(*where, "a correction for a year the ledger does not record")
            )
    return Verdict(violations, added)


def add_missing(
    ledger: Ledger, packs: Mapping[str, FiscalPack] | None = None
) -> list[tuple[str, str, int]]:
    """Records every unrecorded `(pack, section, year)` and nothing else: a recorded
    fingerprint is never rewritten."""
    added: list[tuple[str, str, int]] = []
    recorded_all = ledger.setdefault("fingerprints", {})
    for pack, sections in compute(packs).items():
        for section, years in sections.items():
            target = recorded_all.setdefault(pack, {}).setdefault(section, {})
            for year, digest in years.items():
                if year not in target:
                    target[year] = digest
                    added.append((pack, section, int(year)))
    return added


def add_correction(
    ledger: Ledger,
    pack: str,
    section: str,
    year: int,
    source: str,
    reason: str,
    packs: Mapping[str, FiscalPack] | None = None,
) -> None:
    """Appends the correction that accepts what the code hashes to now."""
    if not source.strip() or not reason.strip():
        raise ValueError("a correction needs a source and a reason")
    digest = compute(packs).get(pack, {}).get(section, {}).get(str(year))
    if digest is None:
        raise ValueError(f"{pack} has no {section} law for {year}")
    ledger.setdefault("corrections", []).append(
        {
            "pack": pack,
            "section": section,
            "year": year,
            "source": source.strip(),
            "reason": reason.strip(),
            "fingerprint": digest,
        }
    )


def empty_ledger() -> Ledger:
    return {"version": LEDGER_VERSION, "fingerprints": {}, "corrections": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or update the tax law ledger.")
    parser.add_argument("--add-year", action="store_true", help="record what is not recorded yet")
    parser.add_argument(
        "--correct", nargs=3, metavar=("PACK", "SECTION", "YEAR"), help="record a deliberate fix"
    )
    parser.add_argument("--source", default="", help="the primary source of a correction")
    parser.add_argument("--reason", default="", help="why a correction is needed")
    args = parser.parse_args(argv)
    ledger = load() if LEDGER_PATH.exists() else empty_ledger()
    if args.correct:
        pack, section, year = args.correct
        add_correction(ledger, pack, section, int(year), args.source, args.reason)
        save(ledger)
        print(f"correction recorded for {pack} / {section} / {year}")
        return 0
    if args.add_year:
        added = add_missing(ledger)
        save(ledger)
        print(f"{len(added)} fingerprint(s) recorded")
        return 0
    verdict = check(ledger)
    for violation in verdict.violations:
        print(violation)
    if verdict.added:
        print(f"{len(verdict.added)} unrecorded (pack, section, year): run --add-year")
    return 1 if verdict.violations else 0


if __name__ == "__main__":
    sys.exit(main())
