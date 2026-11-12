"""The regional IRAP rows that depend on what an srl's activity is (REB-1086, from REB-975).

The 2026 dataset of the Finance Department (`irp2026.csv`) holds, beside each region's
ordinary rate, rows that a company pays instead when its prevailing activity is on a list, and
a second layer that every region carries: the increase of art. 3 c. 1 D.L. 20 February 2026
n. 21 for the activities of its table 1. Which row an entity pays is therefore a fact about the
entity (its ATECO code), not a figure of law, and the lookup needs it.

What the data says
------------------
* `RegionAtecoRules.rows` are the rows the dataset selects by code, in priority order: the
  first one whose condition holds is the rate. A row that applies only to an entity on table 1
  of the D.L. says `table_1=True`; one that applies only to an entity off it says `False`.
* `Table1.codes_2025` is table 1, «ATECO» of the D.L. in force 2026 (ATECO 2025: the table lists
  `35.4`, a class ATECO 2007 does not have). The regional lists are written in ATECO 2007, so
  the lookup reads two codes of the entity, `ateco_2007` and `ateco_2025`; a row says which one
  it is written in (`AtecoMatch.classification`).
* `RegionAtecoRules.blocked` are codes whose row the pack cannot decide: a list or a
  correspondence it has not read (Emilia-Romagna's rows 004 and 005 name codes of the
  «ATECOFIN 2004» classification, and no correspondence to ATECO 2007 is in the dataset; the
  Basilicata rows 012 to 014 print a list whose codes cannot be told apart). An entity on them
  gets no rate.
* `RegionAtecoRules.reductions` are rows that lower the rate for a code but also need the
  region's own requirements or state aid (*de minimis*): they are never applied, and the answer
  says that one exists for the code.

What the lookup refuses to guess
--------------------------------
* A region with a row selected by code, and an entity whose code is not stated: no rate. For
  Toscana, Umbria and Emilia-Romagna the ordinary row is the wrong rate for most companies, and
  for Piemonte, Liguria and Veneto it is for some; the figure is the one the entity can fix by
  stating its code.
* An entity that may be on table 1 (its ATECO 2007 code is in the energy divisions) with no
  ATECO 2025 code: no rate. With no code at all in a region with no list, the ordinary rate and
  a note that table 1 was not checked.
* An entity on table 1 in a region that publishes no row for it (Toscana, Basilicata): no rate.

Sources, read on 2026-11-12
---------------------------
* Dipartimento delle Finanze, «Fiscalità regionale e locale - IRAP - Aliquote applicabili»,
  «Elenchi generali aggiornati quotidianamente», the 2026 list (last update 09/10/2026),
  https://www1.finanze.gov.it/finanze2/dipartimentopolitichefiscali/fiscalitalocale/aliquoteirap/download/download.php?tipo=irp&anno=2026
  : every row below carries its `CODICE_ALI`, rate and norm.
* D.L. 20 February 2026 n. 21 art. 3 c. 1 and its annex «Tabella 1» (Gazzetta Ufficiale
  Serie Generale n. 42 of 20-2-2026, https://www.gazzettaufficiale.it/eli/gu/2026/02/20/42/sg/pdf ):
  «Per il periodo d'imposta successivo a quello in corso al 31 dicembre 2025 e per quello
  successivo, l'aliquota di cui all'articolo 16, commi 1 e 1-bis, del decreto legislativo
  15 dicembre 1997, n. 446, è aumentata di due punti percentuali per i soggetti esercenti, in via
  prevalente, le attività economiche individuate dai codici ATECO di cui alla tabella 1 allegata
  al presente decreto». Table 1: B 06, 09.1; C 19.2; D 35.1, 35.2, 35.4; H 49.50.1.
* Regione Emilia-Romagna, Finanze, «Novità IRAP 2026», Annex A to L.R. 31 March 2025 n. 1:
  the ATECO 2007 (agg. 2022) list of row 002.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from pigrocrm.core.fiscal.law_irap_ateco_lists import (
    EMILIA_ROMAGNA_ANNEX_A_ATECO_2007,
    TOSCANA_007_ATECO_2007,
    UMBRIA_002_ATECO_2007,
    VENETO_017_ATECO_2007,
)

AtecoClassification = Literal["ateco_2007", "ateco_2025"]


def digits(code: str | None) -> str | None:
    """The digits of an ATECO code, `None` when there are none: `35.11.00` is `351100`, and a
    list entry `35.1` is `351`, a prefix of it."""
    if code is None:
        return None
    only = "".join(char for char in code if char.isdigit())
    return only or None


@dataclass(frozen=True)
class AtecoMatch:
    """A condition on one of the entity's codes: it holds when the code starts with one of
    `prefixes` and with none of `except_prefixes`."""

    classification: AtecoClassification
    prefixes: tuple[str, ...]
    except_prefixes: tuple[str, ...] = ()

    def holds(self, ateco_2007: str | None, ateco_2025: str | None) -> bool | None:
        """`None` when the code this condition reads is not stated."""
        code = ateco_2007 if self.classification == "ateco_2007" else ateco_2025
        if code is None:
            return None
        if any(code.startswith(digits(prefix) or "x") for prefix in self.except_prefixes):
            return False
        return any(code.startswith(digits(prefix) or "x") for prefix in self.prefixes)


@dataclass(frozen=True)
class AtecoRate:
    """One row of a region's table selected by the activity: `code` is the dataset's
    `CODICE_ALI`, `rate` its `VALORE_ALI`, `text` the dataset's `REGIME_ALI` (shortened) and
    `norm` its `NORMA_ALIQUOTE`. `table_1` is `True` for a row that applies only to an entity on
    table 1 of D.L. 21/2026, `False` for one that applies only to an entity off it. With no
    `matches` the row applies to every entity its `table_1` allows. When there are several, any
    one holding selects the row."""

    code: str
    rate: Decimal
    table_1: bool | None
    matches: tuple[AtecoMatch, ...]
    text: str
    norm: str


@dataclass(frozen=True)
class AtecoReduction:
    """A row that lowers the rate for a code under requirements the CRM does not hold (`aid`:
    the dataset flags it as state aid, so the company's *de minimis* history decides). Never
    applied; the estimate says it exists."""

    code: str
    rate: Decimal
    matches: tuple[AtecoMatch, ...]
    text: str
    norm: str
    aid: bool


@dataclass(frozen=True)
class RegionAtecoRules:
    rows: tuple[AtecoRate, ...] = ()
    reductions: tuple[AtecoReduction, ...] = ()
    blocked: tuple[AtecoMatch, ...] = ()
    blocked_reason: str = ""


@dataclass(frozen=True)
class Table1:
    """Art. 3 c. 1 D.L. 20 February 2026 n. 21: for the `years` the rate of a subject whose
    prevailing activity is one of `codes_2025` (table 1, ATECO 2025) is increased by `points`.
    `codes_2007_divisions` are the ATECO 2007 codes the pack cannot rule out from a code of
    that version alone (the correspondence between the two classifications is not in the
    sources): an entity in them that states no ATECO 2025 code has no rate."""

    years: tuple[int, ...]
    points: Decimal
    codes_2025: tuple[str, ...]
    codes_2007_divisions: tuple[str, ...]
    source: str


TABLE_1: Final = Table1(
    years=(2026, 2027),
    points=Decimal("2.00"),
    codes_2025=("06", "09.1", "19.2", "35.1", "35.2", "35.4", "49.50.1"),
    codes_2007_divisions=("06", "09", "19", "35", "49.5"),
    source=(
        "art. 3 c. 1 D.L. 20 February 2026 n. 21 and its Tabella 1 (Gazzetta Ufficiale n. 42 of "
        "20-2-2026), read 2026-11-12"
    ),
)


def _m07(*prefixes: str, but: tuple[str, ...] = ()) -> AtecoMatch:
    return AtecoMatch("ateco_2007", prefixes, but)


def _m25(*prefixes: str) -> AtecoMatch:
    return AtecoMatch("ateco_2025", prefixes)


def _row(
    code: str,
    rate: str,
    table_1: bool | None,
    matches: tuple[AtecoMatch, ...],
    text: str,
    norm: str,
) -> AtecoRate:
    return AtecoRate(code, Decimal(rate), table_1, matches, text, norm)


_DL21 = "art. 3 c. 1 D.L. 20 febbraio 2026 n. 21"


def _dl21_row(code: str, rate: str, norm: str = _DL21) -> AtecoRate:
    """The region's row for every subject on table 1: the dataset's «aliquota per i soggetti
    esercenti, in via prevalente, le attivita' economiche individuate dai codici ATECO di cui
    all'art. 3, comma 1, del D.L. 20 febbraio 2026, n. 21»."""
    return _row(
        code,
        rate,
        True,
        (),
        "Aliquota per i soggetti che esercitano in via prevalente le attivita' della tabella 1 "
        "del D.L. 21/2026",
        norm,
    )


def _reduction(
    code: str, rate: str, matches: tuple[AtecoMatch, ...], text: str, norm: str, aid: bool
) -> AtecoReduction:
    return AtecoReduction(code, Decimal(rate), matches, text, norm, aid)


_PIEMONTE: Final = RegionAtecoRules(
    rows=(
        _row(
            "026",
            "6.82",
            True,
            (_m25("35.1"),),
            "Attivita' di produzione, trasmissione e distribuzione di energia elettrica, di cui "
            "al codice ATECO 35.1",
            "Legge regionale 26/2024 - art. 1 c. 1 lett. a), D.L. 21/2026",
        ),
        _dl21_row("031", "5.90"),
        _row(
            "027",
            "4.82",
            False,
            (_m07("47.1"),),
            "Attivita' di commercio al dettaglio in esercizi non specializzati con prevalenza di "
            "prodotti alimentari e bevande, di cui al codice ATECO 47.1",
            "Legge regionale 26/2024 - art. 1 c. 1 lett. b)",
        ),
        _row(
            "028",
            "4.82",
            False,
            (_m07("64"),),
            "Attivita' di servizi finanziari, escluse le assicurazioni e i fondi pensione, di cui "
            "al codice ATECO 64",
            "Legge regionale 26/2024 - art. 1 c. 1 lett. c)",
        ),
        _row(
            "029",
            "4.82",
            False,
            (_m07("66.1"),),
            "Attivita' ausiliarie dei servizi finanziari, escluse le assicurazioni e i fondi "
            "pensione, di cui al codice ATECO 66.1",
            "Legge regionale 26/2024 - art. 1 c. 1 lett. d)",
        ),
        _row(
            "030",
            "4.82",
            False,
            (_m07("66.2"),),
            "Attivita' ausiliarie delle assicurazioni e dei fondi pensione, di cui al codice "
            "ATECO 66.2",
            "Legge regionale 26/2024 - art. 1 c. 1 lett. e)",
        ),
    ),
    reductions=(
        _reduction(
            "013",
            "3.00",
            (_m07("90"),),
            "Aliquota agevolata per le attivita' ricreative, artistiche e di intrattenimento - "
            "codice ATECO 90",
            "art. 10 L.R. 4/2018",
            True,
        ),
        _reduction(
            "014",
            "3.00",
            (_m07("91"),),
            "Aliquota agevolata per le attivita' di biblioteche, archivi e musei e altre "
            "attivita' culturali - codice ATECO 91",
            "art. 10 L.R. 4/2018",
            True,
        ),
        _reduction(
            "045",
            "2.98",
            (_m07("47.62.10"),),
            "Aliquota agevolata per il commercio al dettaglio di giornali ed altre pubblicazioni "
            "periodiche - codice ATECO 47.62.10",
            "L.R. 2/2026",
            True,
        ),
    ),
)

_LIGURIA: Final = RegionAtecoRules(
    rows=(
        _row(
            "014",
            "6.82",
            True,
            (
                _m07(
                    "06.10.00",
                    "06.20.00",
                    "09.10.00",
                    "19.20.10",
                    "35.11.00",
                    "35.14.00",
                    "35.15.00",
                    "35.21.00",
                    "35.22.00",
                    "35.23.00",
                ),
            ),
            "Aliquota maggiorata per i soggetti che esercitano in via prevalente le attivita' dei "
            "codici ATECO 06.10.00, 06.20.00, 09.10.00, 19.20.10, 35.11.00, 35.14.00, 35.15.00, "
            "35.21.00, 35.22.00, 35.23.00",
            "art. 16 c. 1 D.Lgs. 446/1997, art. 2 L.R. 43/2007, art. 3 D.L. 21/2026",
        ),
        _row(
            "012",
            "5.90",
            True,
            (
                _m07(
                    "19.2", "35.12", "35.13", "35.16", "35.24", "35.4", "49.50.1", but=("19.20.10",)
                ),
            ),
            "Aliquota maggiorata per i soggetti che esercitano in via prevalente le attivita' dei "
            "codici ATECO 19.2 (eccetto 19.20.10), 35.12, 35.13, 35.16, 35.24, 35.4, 49.50.1",
            "art. 16 c. 1 D.Lgs. 446/1997, art. 3 D.L. 21/2026",
        ),
        _row(
            "006",
            "4.82",
            False,
            (
                _m07(
                    "19.10.00",
                    "24.46.00",
                    "35.30.00",
                    "53.10.00",
                    "53.20.00",
                    "61.10.01",
                    "61.10.02",
                ),
            ),
            "Aliquota maggiorata per i soggetti che esercitano in via prevalente le attivita' dei "
            "codici ATECO 19.10.00, 24.46.00, 35.30.00, 53.10.00, 53.20.00, 61.10.01, 61.10.02",
            "art. 16 c. 1 D.Lgs. 446/1997 e art. 2 L.R. 14 dicembre 2007 n. 43",
        ),
    ),
)

_EMILIA_ROMAGNA: Final = RegionAtecoRules(
    rows=(
        _row(
            "003",
            "6.20",
            True,
            (_m07(*EMILIA_ROMAGNA_ANNEX_A_ATECO_2007),),
            "Aliquota per le attivita' dell'allegato A della L.R. 1/2025 che sono anche nella "
            "tabella 1 del D.L. 21/2026",
            "art. 1 c. 1 L.R. 1/2025, art. 3 c. 1 D.L. 21/2026",
        ),
        _row(
            "002",
            "4.20",
            False,
            (_m07(*EMILIA_ROMAGNA_ANNEX_A_ATECO_2007),),
            "Aliquota prevista per alcune attivita' economiche identificate dai soli codici "
            "ATECO 2007 (agg 2022) riportati nell'allegato A della L.R. 31/3/2025, n. 1",
            "art. 1 c. 1 L.R. 31/3/2025 n. 1",
        ),
    ),
    # Rows 004 (4.82) and 005 (6.82): «settori contraddistinti dai codici 23, 40, 64 e 67 della
    # classificazione ATECOFIN 2004». The dataset gives no correspondence to ATECO 2007, so the
    # pack blocks the ATECO 2007 divisions those sectors correspond to (NACE Rev. 1.1 division 23,
    # coke and refined petroleum, is ATECO 2007 division 19; 40, electricity, gas, steam, is 35;
    # 64, post and telecommunications, is 53 and 61; 67, auxiliary financial activities, is 66):
    # the reading of the pack, not a source's, and it only ever withholds a figure.
    blocked=(_m07("19", "35", "53", "61", "66"),),
    blocked_reason="emilia_romagna_atecofin_2004",
)

_TOSCANA: Final = RegionAtecoRules(
    rows=(
        _row(
            "007",
            "4.82",
            False,
            (_m07(*TOSCANA_007_ATECO_2007),),
            "Aliquota maggiorata per le attivita' economiche individuate dai codici ISTAT (Ateco "
            "2007) elencati dalla Regione; per 45, 46, 47, 55, 56, 86.10, 86.21, 86.22, 86.90 "
            "solo per le societa' di capitali",
            "art. 5 L.R. 22/12/06 n. 64, art. 1 c. 1 L.R. 27/12/12 n. 77",
        ),
    ),
    reductions=(
        _reduction(
            "005",
            "2.98",
            (_m07("77.11", "77.12"),),
            "Aliquota agevolata per i settori di attivita' economica 77.11 e 77.12 della "
            "classificazione ATECO 2007",
            "art. 5 L.R. 24/12/13 n. 79",
            True,
        ),
    ),
)

_UMBRIA: Final = RegionAtecoRules(
    rows=(
        _row(
            "014",
            "6.82",
            True,
            (_m07(*UMBRIA_002_ATECO_2007),),
            "Aliquota maggiorata per i soggetti della tabella 1 del D.L. 21/2026 che svolgono "
            "attivita' dell'art. 23 L.R. 36/2007",
            "art. 16 c. 1 D.Lgs. 446/1997, art. 3 D.L. 21/2026, art. 23 L.R. 24/12/2007 n. 36",
        ),
        _dl21_row(
            "013",
            "6.30",
            "art. 16 c. 1 D.Lgs. 446/1997, art. 3 D.L. 21/2026, art. 1 c. 4 L.R. 11/04/2025 n. 2",
        ),
        _row(
            "002",
            "4.82",
            False,
            (_m07(*UMBRIA_002_ATECO_2007),),
            "Aliquota maggiorata per i settori di attivita' economiche contraddistinti dai codici "
            "ATECO 2007 dell'art. 23 L.R. 36/2007",
            "art. 23 L.R. 24/12/2007 n. 36",
        ),
    ),
)

_VENETO: Final = RegionAtecoRules(
    rows=(
        _row(
            "021",
            "6.82",
            True,
            (_m25("19.2", "35.1", "35.2", "35.4"),),
            "Aliquota per i soggetti esercenti in via prevalente le attivita' ATECO 19.2, 35.1, "
            "35.2, 35.4 di cui all'art. 3 c. 1 D.L. 21/2026",
            "art. 2 c. 2 L.R. 30/2022, art. 3 c. 1 D.L. 21/2026",
        ),
        _row(
            "020",
            "6.55",
            True,
            (_m25("09.1"),),
            "Aliquota per i soggetti esercenti in via prevalente le attivita' ATECO 09.1 di cui "
            "all'art. 3 c. 1 D.L. 21/2026",
            "art. 2 c. 1 lett. a) L.R. 27/2024, art. 3 c. 1 D.L. 21/2026",
        ),
        _row(
            "019",
            "6.08",
            True,
            (_m25("06", "49.50.1"),),
            "Aliquota per i soggetti esercenti in via prevalente le attivita' ATECO 06 e 49.50.1 "
            "di cui all'art. 3 c. 1 D.L. 21/2026",
            "art. 2 c. 1 lett. b) L.R. 27/2024, art. 3 c. 1 D.L. 21/2026",
        ),
        _row(
            "015",
            "4.82",
            False,
            (_m07("19", "35", "53", "61", "64", "65", "66"),),
            "Aliquota maggiorata per le attivita' dei settori ATECOFIN 2007 19, 35, 53, 61, 64, "
            "65, 66 svolte da soggetti diversi da banche, enti finanziari e assicurazioni",
            "art. 2 c. 2 L.R. 30/2022",
        ),
        _row(
            "017",
            "4.55",
            False,
            (_m07(*VENETO_017_ATECO_2007),),
            "Aliquota maggiorata per le attivita' economiche dei settori ATECOFIN 2007 elencati "
            "dalla Regione",
            "art. 2 c. 1 lett. a) L.R. 32/2024",
        ),
    ),
)

_BASILICATA: Final = RegionAtecoRules(
    # Rows 012 (4.82), 013 (6.82) and 014 (5.90) mix ATECO 2007 and ATECO 2025 codes and print
    # «19.1,9.9» and «6.1,6.2,9.1,19.2» for the oil and gas sector, which no source read says how
    # to complete to codes of one classification: an entity in the electricity, gas and oil
    # sectors of either classification gets no rate.
    blocked=(_m07("06", "09", "19", "35", "49.5"), _m25("06", "09", "19", "35", "49.5")),
    blocked_reason="basilicata_energy_lists",
)

_MARCHE: Final = RegionAtecoRules(
    reductions=(
        _reduction(
            "005",
            "3.90",
            (_m07("72.11.00", "72.19.01", "72.19.09", "72.20.00"),),
            "Aliquota agevolata per il settore ricerca e sviluppo (codici ATECO 2007 72.11.00, "
            "72.19.01, 72.19.09, 72.20.00)",
            "art. 1 c. 4 lett. e-bis) L.R. 19/12/2001 n. 35, art. 29 L.R. 37/2008",
            True,
        ),
        _reduction(
            "006",
            "3.90",
            (
                _m07(
                    "79.90.11",
                    "85.52.01",
                    "90.01.01",
                    "90.01.09",
                    "90.02.01",
                    "90.02.02",
                    "90.02.09",
                    "90.03.02",
                    "90.03.09",
                    "90.04.00",
                    "93.21.00",
                    "93.29.10",
                    "93.29.90",
                ),
            ),
            "Aliquota agevolata per il settore «altre attivita' dello spettacolo, di "
            "intrattenimento e divertimento»",
            "art. 1 c. 4 lett. e-ter) L.R. 19/12/2001 n. 35, art. 29 L.R. 37/2008",
            True,
        ),
        _reduction(
            "009",
            "4.13",
            (_m07("15.20.10", "15.20.20", "16.29.11", "22.19.01", "22.29.01", "15.12.09"),),
            "Aliquota agevolata per la fabbricazione di calzature, articoli da viaggio, borse, "
            "articoli da correggiaio e selleria",
            "art. 1 c. 1 L.R. 22/12/2003 n. 25, art. 30 c. 1 L.R. 19/02/2004 n. 2, art. 29 L.R. "
            "37/2008",
            True,
        ),
        _reduction(
            "012",
            "4.13",
            (_m07("15.11.00"),),
            "Aliquota agevolata per le attivita' di preparazione e concia del cuoio",
            "art. 25 L.R. 10/02/2006 n. 2, art. 29 L.R. 37/2008",
            True,
        ),
    ),
)

# Lombardia, code 011: «Agevolazione per le micro, piccole e medie imprese che esercitano attivita'
# economiche di proiezione cinematografica di cui al codice ATECO 591400», 2.90 under Reg. (UE)
# n. 2831/2023 (state aid, and SME status the CRM does not hold).
_LOMBARDIA_CINEMAS = _reduction(
    "011",
    "2.90",
    (_m07("59.14"),),
    "Agevolazione per le micro, piccole e medie imprese che esercitano attivita' di proiezione "
    "cinematografica (codice ATECO 59.14.00), Reg. UE 2831/2023",
    "L.R. Lombardia, Reg. (UE) n. 2831/2023",
    True,
)

# The region's rows by activity. The regions whose table has no row selected by code beyond table
# 1 carry the general row of D.L. 21/2026 alone, which the dataset publishes as the ordinary rate
# plus the two points (`test_every_general_table_1_row_is_the_ordinary_rate_plus_two_points`).
ATECO_RULES_2026: Final[dict[str, RegionAtecoRules]] = {
    "01": _PIEMONTE,
    "02": RegionAtecoRules(rows=(_dl21_row("003", "5.90"),)),
    "03": RegionAtecoRules(rows=(_dl21_row("016", "5.90"),), reductions=(_LOMBARDIA_CINEMAS,)),
    "BZ": RegionAtecoRules(rows=(_dl21_row("010", "5.90"),)),
    "TN": RegionAtecoRules(rows=(_dl21_row("015", "4.68"),)),
    "05": _VENETO,
    "06": RegionAtecoRules(rows=(_dl21_row("046", "5.90"),)),
    "07": _LIGURIA,
    "08": _EMILIA_ROMAGNA,
    "09": _TOSCANA,
    "10": _UMBRIA,
    "11": RegionAtecoRules(rows=(_dl21_row("020", "6.73"),), reductions=_MARCHE.reductions),
    "12": RegionAtecoRules(rows=(_dl21_row("002", "6.82"),)),
    "13": RegionAtecoRules(rows=(_dl21_row("011", "6.82"),)),
    "14": RegionAtecoRules(rows=(_dl21_row("019", "6.97"),)),
    "15": RegionAtecoRules(rows=(_dl21_row("002", "6.97"),)),
    "16": RegionAtecoRules(rows=(_dl21_row("002", "6.82"),)),
    "17": _BASILICATA,
    "18": RegionAtecoRules(rows=(_dl21_row("002", "6.82"),)),
    "19": RegionAtecoRules(rows=(_dl21_row("002", "5.90"),)),
    "20": RegionAtecoRules(rows=(_dl21_row("009", "4.93"),)),
}

# What a region's lookup can answer. A rate carries the row it came from (`None` for the
# ordinary rate), the notes the estimate must say, and, instead of a rate, why there is none.
AtecoNote = Literal[
    "table_1_not_checked",  # no ATECO 2025 code: the two points of D.L. 21/2026 were not tested
    "reduction_exists",  # a reduction row exists for the code and is not applied
]
Unanswered = Literal[
    "ateco_2007_needed",  # a regional list needs the ATECO 2007 code
    "ateco_2025_needed",  # table 1 needs the ATECO 2025 code
    "list_not_decidable",  # the pack cannot tell which of the region's rows applies
    "table_1_unpublished",  # the region publishes no row for a subject on table 1
]


@dataclass(frozen=True)
class AtecoReductionNote:
    code: str
    rate: Decimal
    text: str
    aid: bool


@dataclass(frozen=True)
class IrapRateAnswer:
    """The rate an entity pays in a region, or why none is given. `row` is the dataset row that
    selected it, `None` for the region's ordinary rate; `unanswered` names the fact or the list
    that stops a rate, and `blocked_reason` which list when it is `list_not_decidable`."""

    rate: Decimal | None
    row: AtecoRate | None
    notes: tuple[AtecoNote, ...] = ()
    reductions: tuple[AtecoReductionNote, ...] = ()
    unanswered: Unanswered | None = None
    blocked_reason: str = ""
    on_table_1: bool | None = None


def on_table_1(table: Table1, year: int, ateco_2025: str | None) -> bool | None:
    """Whether a subject whose ATECO 2025 code is `ateco_2025` (digits) is on `table` for
    `year`: `False` for a year the increase does not cover, `None` when no code is stated."""
    if year not in table.years:
        return False
    if ateco_2025 is None:
        return None
    return any(ateco_2025.startswith(digits(code) or "x") for code in table.codes_2025)


def _needs(match: AtecoMatch) -> Unanswered:
    return "ateco_2007_needed" if match.classification == "ateco_2007" else "ateco_2025_needed"


def select_rate(
    year: int,
    ordinary: Decimal,
    rules: RegionAtecoRules,
    table: Table1,
    ateco_2007: str | None,
    ateco_2025: str | None,
) -> IrapRateAnswer:
    """The rate of a region for an entity with these codes (digits, `None` when not stated):
    the first row whose condition holds, else the ordinary rate, else why there is none."""
    table_1 = on_table_1(table, year, ateco_2025)
    covered = year in table.years
    energy_2007 = ateco_2007 is not None and any(
        ateco_2007.startswith(digits(code) or "x") for code in table.codes_2007_divisions
    )
    if table_1 is None and covered and energy_2007:
        # The ATECO 2007 code cannot rule out table 1 in the energy divisions.
        return IrapRateAnswer(None, None, unanswered="ateco_2025_needed")
    notes: list[AtecoNote] = []
    if table_1 is None and covered:
        notes.append("table_1_not_checked")
    on_list = table_1 is True

    blocked = [(match, match.holds(ateco_2007, ateco_2025)) for match in rules.blocked]
    if any(held is True for _, held in blocked):
        return IrapRateAnswer(
            None, None, unanswered="list_not_decidable", blocked_reason=rules.blocked_reason
        )
    for match, held in blocked:
        if held is None:
            return IrapRateAnswer(None, None, unanswered=_needs(match))

    selected: AtecoRate | None = None
    for row in rules.rows:
        if row.table_1 is not None and row.table_1 != on_list:
            continue
        if not row.matches:
            selected = row
            break
        results = [match.holds(ateco_2007, ateco_2025) for match in row.matches]
        if any(result is True for result in results):
            selected = row
            break
        for match in row.matches:
            if match.holds(ateco_2007, ateco_2025) is None:
                return IrapRateAnswer(None, None, unanswered=_needs(match))
    if selected is None and on_list:
        return IrapRateAnswer(None, None, unanswered="table_1_unpublished", on_table_1=True)

    reductions = tuple(
        AtecoReductionNote(reduction.code, reduction.rate, reduction.text, reduction.aid)
        for reduction in rules.reductions
        if any(match.holds(ateco_2007, ateco_2025) is True for match in reduction.matches)
    )
    if reductions:
        notes.append("reduction_exists")
    return IrapRateAnswer(
        selected.rate if selected is not None else ordinary,
        selected,
        notes=tuple(notes),
        reductions=reductions,
        on_table_1=table_1,
    )


__all__ = [
    "ATECO_RULES_2026",
    "TABLE_1",
    "AtecoClassification",
    "AtecoMatch",
    "AtecoNote",
    "AtecoRate",
    "AtecoReduction",
    "AtecoReductionNote",
    "IrapRateAnswer",
    "RegionAtecoRules",
    "Table1",
    "Unanswered",
    "digits",
    "on_table_1",
    "select_rate",
]
