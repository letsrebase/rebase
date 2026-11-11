"""Italy's municipalities, provinces and regions, from ISTAT's «Elenco comuni italiani».

The list is data (`datasets/istat_municipalities.csv`, rebuilt by
`projects/pigrocrm/scripts/import_istat_municipalities.py`, its origin in
`istat_municipalities.source.json`), read once per process. It answers one question: which
municipality, and so which region, does a `city` and a `province` typed on an address name
(REB-1069). Nothing here is a tax rule: the cards that need a region or a municipality
(IRAP, the municipal surcharge) ask for it by the codes this module hands out.
"""

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "datasets" / "istat_municipalities.csv"

# The apostrophes a person or a keyboard writes for the one in «Sant'Agata»: typewriter,
# the right and left single quotation marks, the modifier letter, the acute accent and the
# grave accent. They carry no meaning in a name, so they are dropped before comparing.
_APOSTROPHES = "'\u2019\u2018\u02bc\u00b4`"
_APOSTROPHE_TABLE = {ord(char): None for char in _APOSTROPHES}
_SEPARATORS = re.compile(r"[\s\-\u2010-\u2015]+")


def fold(name: str) -> str:
    """A name as it is compared: case and accents gone, and no apostrophe, hyphen or space at
    all, since a keyboard writes the apostrophe of «Sant'Agata» as a quote, a space or
    nothing. «Sant'Agata de' Goti», «SANT’AGATA DE’ GOTI» and «sant agata de goti» fold to the
    same string; «Forlì» and «Forli» do too."""
    stripped = name.translate(_APOSTROPHE_TABLE)
    decomposed = unicodedata.normalize("NFKD", stripped)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _SEPARATORS.sub("", plain.casefold())


@dataclass(frozen=True, slots=True)
class Municipality:
    """One row of the list. `istat_code` is the six-digit alphanumeric code (province
    code and progressive), `cadastral_code` the Belfiore code the tax offices use."""

    istat_code: str
    cadastral_code: str
    name: str
    province: str
    region_code: str
    region_name: str


@dataclass(frozen=True, slots=True)
class Region:
    code: str
    name: str


class MunicipalityList:
    """Every municipality of the list, indexed by code and by folded name within its
    province. Built once from the file; immutable."""

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.municipalities: tuple[Municipality, ...] = tuple(
            Municipality(
                istat_code=row["istat_code"],
                cadastral_code=row["cadastral_code"],
                name=row["name"],
                province=row["province"],
                region_code=row["region_code"],
                region_name=row["region_name"],
            )
            for row in rows
        )
        self.by_istat_code = {m.istat_code: m for m in self.municipalities}
        self.by_cadastral_code = {m.cadastral_code: m for m in self.municipalities}
        self._by_name: dict[tuple[str, str], Municipality] = {}
        self._in_province: dict[str, list[Municipality]] = {}
        self._region_of_province: dict[str, Region] = {}
        for municipality, row in zip(self.municipalities, rows, strict=True):
            self._in_province.setdefault(municipality.province, []).append(municipality)
            self._region_of_province.setdefault(
                municipality.province, Region(municipality.region_code, municipality.region_name)
            )
            for alias in _names_of(row):
                self._by_name.setdefault((municipality.province, alias), municipality)
        for municipalities in self._in_province.values():
            municipalities.sort(key=lambda m: fold(m.name))

    def region_of(self, province: str | None) -> Region | None:
        """The region a province abbreviation belongs to, or `None` for a code ISTAT's list
        does not carry (a province that no longer exists, a typo)."""
        return self._region_of_province.get((province or "").strip().upper())

    def in_province(self, province: str | None) -> tuple[Municipality, ...]:
        """The municipalities of a province, by name; empty for a code the list does not carry."""
        return tuple(self._in_province.get((province or "").strip().upper(), ()))

    def match(self, name: str | None, province: str | None) -> Municipality | None:
        """The municipality `name` names in `province`: the same name case, accent and
        apostrophe apart, in Italian or in the other official language of a bilingual
        one («Bolzano», «Bozen» and «Bolzano/Bozen»). `None` when nothing in the province
        reads that way: no guess across provinces, since the same name exists in several."""
        folded = fold(name or "")
        code = (province or "").strip().upper()
        if not folded or not code:
            return None
        return self._by_name.get((code, folded))


def _names_of(row: dict[str, str]) -> set[str]:
    """Every spelling a person may type for a municipality: the combined name ISTAT prints
    («Aldino/Aldein»), the Italian one, the other language's, and each half of the
    combined one."""
    names = {row["name"], row["name_it"], row["name_other"], *row["name"].split("/")}
    return {fold(name) for name in names if name}


@lru_cache(maxsize=1)
def municipality_list() -> MunicipalityList:
    with DATA_FILE.open(encoding="utf-8", newline="") as handle:
        return MunicipalityList(list(csv.DictReader(handle)))
