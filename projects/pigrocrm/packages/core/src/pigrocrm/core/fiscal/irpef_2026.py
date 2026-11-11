"""The IRPEF and the regional surcharges of tax year 2026 (REB-1062).

National brackets: art. 11 c. 1 D.P.R. 917/1986 (TUIR), text in force read on Normattiva
(`normattiva.it`, urn `presidente.repubblica:decreto:1986-12-22;917~art11`, in force on
2026-11-11): «a) fino a 28.000 euro, 23 per cento; b) oltre 28.000 euro e fino a 50.000 euro,
((33 per cento)); c) oltre 50.000 euro, 43 per cento». Letter b) reads 33 because L. 30 December
2025 n. 199 art. 1 c. 3 replaced the 35 with 33 (Normattiva, same date). The Revenue Agency's page
«Aliquote e calcolo dell'Irpef» (updated 13 January 2026), states the consequence: above 50,000
euro the tax is 13,700 euro plus 43% of the excess. The text of art. 11 is the one for tax
year 2026: D.Lgs. 19 June 2026 n. 117 (the new income tax code, GU n. 152 of 3 July 2026)
repeals the TUIR's articles 1-191 and applies from 1 January 2027, so the next year's entry
re-reads its article.

Regional surcharges: the Finance Department's page «Addizionale regionale all'IRPEF»,
aliquote applicabili, one page per region and autonomous province
(`www1.finanze.gov.it/finanze2/dipartimentopolitichefiscali/fiscalitalocale/addregirpef/
addregirpef.php?reg=NN`, NN the Finanze's own code given per rule), read on 2026-11-11, each
with the publication date the page prints. A region may keep, for 2025-2028, the four brackets
of the IRPEF before 2025, up to 15,000, 28,000 and 50,000 euro, then above (D.Lgs. 68/2011 art.
6 c. 1; L. 207/2024 art. 1 c. 727-728, as amended by L. 199/2025 art. 1 c. 649, in the Finanze's
«Disciplina del tributo» page); the table is entered as the page prints it, four, three or one
row. The base rate is 1.23% (art. 50 D.Lgs. 446/1997).

Two regions have two versions on the Finanze page and the page's default (the later) is the
one entered: Molise (19 June 2026: 2.03, 2.23, 3.63, 3.63; the page's norms include art. 2 c.
86 L. 191/2009, the automatic 0.30-point increase for a region under a health recovery plan;
the 29 January 2026 version was
1.73, 1.93, 3.33, 3.33) and Puglia (29 May 2026, decreto del Presidente della Regione n. 3 of
28 May 2026 as commissioner ad acta: 1.33, 2.13, 3.23, 3.33; the 28 January 2026 version was
1.33, 1.43, 1.63, 1.85). A region that raises its rates again during the year changes the
table: the figures are those of the read date.

Only tax year 2026 is here. The IRPEF of 2025 (23%, 35%, 43%: art. 1 c. 2 L. 207/2024) is not
entered, because the pack's other 2025 sections (advances, calendar, IVA) are not verified for
2025 and `law_for(2025)` must stay empty rather than half right.
"""

from __future__ import annotations

from decimal import Decimal

from pigrocrm.core.fiscal.law_irpef import (
    IncomeBandDetraction,
    IrpefLaw,
    RateBracket,
    RegionalSurchargeLaw,
)

_D = Decimal
_15K, _28K, _50K = _D(15000), _D(28000), _D(50000)

_FINANZE = (
    "Dipartimento delle Finanze, «Addizionale regionale all'IRPEF», aliquote applicabili, "
    "Finanze codice regione {code}, pubblicazione {published}, read 2026-11-11"
)


def _four(a: str, b: str, c: str, d: str) -> tuple[RateBracket, ...]:
    return (
        RateBracket(_15K, _D(a)),
        RateBracket(_28K, _D(b)),
        RateBracket(_50K, _D(c)),
        RateBracket(None, _D(d)),
    )


def _three(a: str, b: str, c: str) -> tuple[RateBracket, ...]:
    return (RateBracket(_28K, _D(a)), RateBracket(_50K, _D(b)), RateBracket(None, _D(c)))


def _single(rate: str) -> tuple[RateBracket, ...]:
    return (RateBracket(None, _D(rate)),)


IRPEF_BRACKETS_2026 = (
    RateBracket(_28K, _D(23)),
    RateBracket(_50K, _D(33)),
    RateBracket(None, _D(43)),
)

REGIONAL_SURCHARGES_2026: tuple[RegionalSurchargeLaw, ...] = (
    RegionalSurchargeLaw(
        key="01",
        name="Piemonte",
        brackets=_four("1.62", "2.68", "3.31", "3.33"),
        unmodelled=(
            "Detractions for 2026: 100 euro per child for a taxpayer with more than two "
            "dependent children, from the first; 500 euro per disabled dependent child "
            "(l.r. 28 March 2022 n. 4, l.r. 6 August 2025 n. 16)."
        ),
        source=_FINANZE.format(code="13", published="29-GEN-26"),
    ),
    RegionalSurchargeLaw(
        key="02",
        name="Valle d'Aosta",
        brackets=_single("1.23"),
        # «Per il periodo di imposta 2026, i soggetti con reddito complessivo ... fino a
        # 15.000 euro, sono esentati dal pagamento dell'addizionale regionale all'IRPEF. Ai
        # soggetti con reddito complessivo oltre 15.000 euro si applica l'aliquota ordinaria
        # sull'intero imponibile.» The page says «reddito complessivo»; the pack tests the
        # taxable income it is given, which differs by the deductible charges.
        exempt_up_to=_15K,
        unmodelled=(
            "The 15,000 euro exemption is tested on the «reddito complessivo» (before "
            "deductible charges); the lookup tests the taxable income it is given."
        ),
        source=_FINANZE.format(code="20", published="19-GEN-26")
        + "; l.r. 23 December 2025 n. 29 art. 1",
    ),
    RegionalSurchargeLaw(
        key="03",
        name="Lombardia",
        brackets=_four("1.23", "1.58", "1.72", "1.73"),
        source=_FINANZE.format(code="10", published="28-GEN-26")
        + "; l.r. 14 July 2003 n. 10 art. 72 c. 1",
    ),
    RegionalSurchargeLaw(
        key="05",
        name="Veneto",
        brackets=_single("1.23"),
        unmodelled=(
            "Reduced rate of 0.9% for a disabled taxpayer, or one with a disabled dependent "
            "relative, with a taxable income of at most 50,000 euro (the sum of the incomes "
            "of those the person is dependent on, when more than one)."
        ),
        source=_FINANZE.format(code="21", published="22-GEN-26"),
    ),
    RegionalSurchargeLaw(
        key="06",
        name="Friuli Venezia Giulia",
        # «Per soggetti aventi un reddito imponibile ... non superiore a 15.000 euro,
        # l'aliquota e' ridotta dello 0,53 per cento. Pertanto, in ipotesi di reddito
        # imponibile fino a euro 15.000 l'aliquota e' pari a 0,70 per cento. Per reddito
        # imponibile superiore a euro 15.000 l'aliquota e' pari a 1,23 per cento sull'intero
        # importo.»
        brackets=_four("0.70", "1.23", "1.23", "1.23"),
        method="whole_income",
        source=_FINANZE.format(code="07", published="19-GEN-26")
        + "; l.r. 25 July 2012 n. 14 art. 1 c. 5",
    ),
    RegionalSurchargeLaw(
        key="07",
        name="Liguria",
        brackets=_three("1.23", "3.18", "3.23"),
        source=_FINANZE.format(code="09", published="28-GEN-26")
        + "; l.r. 9 October 2024 n. 17 art. 2 bis as amended by l.r. 31 March 2025 n. 3",
    ),
    RegionalSurchargeLaw(
        key="08",
        name="Emilia-Romagna",
        brackets=_four("1.33", "1.93", "2.78", "3.33"),
        source=_FINANZE.format(code="06", published="19-GEN-26")
        + "; l.r. 20 December 2006 n. 19 art. 2 as amended by l.r. 31 March 2025 n. 1 and "
        "l.r. 25 July 2025 n. 9",
    ),
    RegionalSurchargeLaw(
        key="09",
        name="Toscana",
        brackets=_four("1.42", "1.43", "3.32", "3.33"),
        source=_FINANZE.format(code="17", published="30-GEN-26")
        + "; l.r. 28 December 2023 n. 48 art. 1",
    ),
    RegionalSurchargeLaw(
        key="10",
        name="Umbria",
        brackets=_four("1.73", "3.02", "3.12", "3.33"),
        # The page: the region's two surcharges (0.50 points up to 15,000 euro, 1.79 points from
        # 15,000 to 28,000) do not apply to a taxable income of up to 28,000 euro, and a
        # detraction of 150 euro goes to a taxable income from 28,001 to 50,000 euro. The 1.73
        # and 3.02 of the table are the base 1.23 plus those two, so up to 28,000 euro the rate
        # is the base 1.23%.
        flat_rate_up_to=_28K,
        flat_rate=_D("1.23"),
        detractions=(IncomeBandDetraction(_28K, _50K, _D(150)),),
        source=_FINANZE.format(code="19", published="19-GEN-26")
        + "; l.r. 11 April 2025 n. 2 art. 1",
    ),
    RegionalSurchargeLaw(
        key="11",
        name="Marche",
        brackets=_four("1.23", "1.53", "1.70", "1.73"),
        unmodelled=(
            "Rate of 1.23% for a taxpayer with a taxable income of at most 50,000 euro and "
            "one or more disabled dependent children (art. 3 l. 104/1992; the sum of the "
            "incomes of those they are dependent on, when more than one, at most 50,000)."
        ),
        source=_FINANZE.format(code="11", published="22-GEN-26")
        + "; l.r. 23 March 2022 n. 5 art. 1; L. 207/2024 art. 1 c. 728",
    ),
    RegionalSurchargeLaw(
        key="12",
        name="Lazio",
        brackets=_four("1.73", "3.33", "3.33", "3.33"),
        # «Per i soggetti con un reddito imponibile ... non superiore a euro 28.000,00
        # l'aliquota ... e' determinata in misura pari all'1,73% (articolo 2, comma 2, legge
        # regionale n. 20 del 31 dicembre 2025). Detrazione pari a 60,00 euro per i soggetti
        # con un reddito imponibile ... compreso tra 28.001 e 30.000,00 euro (articolo 2,
        # comma 3).»
        flat_rate_up_to=_28K,
        flat_rate=_D("1.73"),
        detractions=(IncomeBandDetraction(_28K, _D(30000), _D(60)),),
        source=_FINANZE.format(code="08", published="22-GEN-26")
        + "; l.r. 31 December 2025 n. 20 art. 2",
    ),
    RegionalSurchargeLaw(
        key="13",
        name="Abruzzo",
        brackets=_three("1.67", "2.87", "3.33"),
        source=_FINANZE.format(code="01", published="28-GEN-26")
        + "; l.r. 12 December 2006 n. 44 art. 1 c. 8; l.r. 4 April 2025 n. 9 art. 1 c. 1",
    ),
    RegionalSurchargeLaw(
        key="14",
        name="Molise",
        brackets=_four("2.03", "2.23", "3.63", "3.63"),
        source=_FINANZE.format(code="12", published="19-GIU-26")
        + " (the page's first version; the 29-GEN-26 one is 1.73, 1.93, 3.33, 3.33); "
        "l.r. 9/2013 art. 2; L. 311/2004 art. 1 c. 174; L. 191/2009 art. 2 c. 86",
    ),
    RegionalSurchargeLaw(
        key="15",
        name="Campania",
        brackets=_four("1.73", "2.96", "3.20", "3.33"),
        unmodelled=(
            "For a taxable income of at most 28,000 euro: a detraction of 30 euro per "
            "dependent child for a taxpayer with at least two (l.r. 30 March 2022 n. 7 art. 1 "
            "c. 2) and of 40 euro per disabled dependent child (c. 3), no credit."
        ),
        source=_FINANZE.format(code="05", published="29-GEN-26") + "; l.r. 30 March 2022 n. 7",
    ),
    RegionalSurchargeLaw(
        key="16",
        name="Puglia",
        brackets=_four("1.33", "2.13", "3.23", "3.33"),
        unmodelled=(
            "More than three dependent children: a detraction of 20 euro per child from the "
            "first, 375 euro more per disabled child (l.r. 29 December 2015 n. 40 art. 3); "
            "where the income and tax do not allow it, direct support measures set by the "
            "regional executive."
        ),
        source=_FINANZE.format(code="14", published="29-MAG-26")
        + " (the page's first version, decreto n. 3 of 28 May 2026 of the President as "
        "commissioner ad acta; the 28-GEN-26 one is 1.33, 1.43, 1.63, 1.85)",
    ),
    RegionalSurchargeLaw(
        key="17",
        name="Basilicata",
        brackets=_single("1.23"),
        source=_FINANZE.format(code="02", published="29-GEN-26")
        + "; D.Lgs. 68/2011 art. 6; D.Lgs. 446/1997 art. 50",
    ),
    RegionalSurchargeLaw(
        key="18",
        name="Calabria",
        brackets=_single("1.73"),
        source=_FINANZE.format(code="04", published="29-GEN-26")
        + "; l.r. 7 August 2002 n. 30 art. 1 as amended by l.r. 11 January 2006 n. 1",
    ),
    RegionalSurchargeLaw(
        key="19",
        name="Sicilia",
        brackets=_single("1.23"),
        source=_FINANZE.format(code="16", published="29-GEN-26")
        + "; D.Lgs. 446/1997 art. 50; l.r. 2 May 2007 n. 12 art. 1",
    ),
    RegionalSurchargeLaw(
        key="20",
        name="Sardegna",
        brackets=_single("1.23"),
        unmodelled=(
            "For 2026, a taxable income of at most 50,000 euro with minor dependent children "
            "(total income of at most 4,000 euro): a detraction of 200 euro per minor child, "
            "100 euro more per disabled child (l.r. 48/2018 art. 2), no credit."
        ),
        source=_FINANZE.format(code="15", published="29-GEN-26")
        + "; D.L. 201/2011 art. 28; l.r. 48/2018 art. 2",
    ),
    RegionalSurchargeLaw(
        key="TN",
        name="Provincia autonoma di Trento",
        brackets=_four("1.23", "1.23", "1.23", "1.73"),
        # «Ai contribuenti aventi un reddito imponibile non superiore a euro 30.000,00 spetta
        # una deduzione di euro 30.000,00. Tale deduzione non spetta ai contribuenti aventi
        # un reddito imponibile superiore a euro 30.000,00.»
        exempt_up_to=_D(30000),
        unmodelled=(
            "A detraction of 246 euro per dependent child (art. 12 c. 2 TUIR), pro rata of "
            "percentage and months, for a taxable income of at most 50,000 euro, no credit."
        ),
        source=_FINANZE.format(code="18", published="22-GEN-26")
        + "; l.p. 23 December 2019 n. 13 art. 1 c. 2-quater, 2-sexies and 3-bis, as amended by "
        "l.p. 29 December 2025 n. 11 art. 1",
    ),
    RegionalSurchargeLaw(
        key="BZ",
        name="Provincia autonoma di Bolzano",
        brackets=_three("1.23", "1.23", "1.73"),
        # «a) Ai contribuenti aventi un reddito imponibile ai fini dell'addizionale regionale
        # IRPEF non superiore a 90.000,00 euro spetta una detrazione d'imposta di 430,50
        # euro.» The detractions do not make a credit (the page's note).
        detractions=(IncomeBandDetraction(None, _D(90000), _D("430.50")),),
        unmodelled=(
            "Also: b) above 50,000 euro a detraction of 125 euro times (taxable income minus "
            "50,000) over 25,000, at most 125 euro; c) 340 euro per dependent child for a "
            "taxable income of at most 90,000 euro. The 90,000 euro test counts income from "
            "rental under the flat tax, tips under the substitute tax and flat-rate regime "
            "income, which the lookup does not know. Rule b) is not computed: the formula as "
            "the page prints it grows with income, and no second source was found to confirm "
            "its direction (A5, REB-975)."
        ),
        source=_FINANZE.format(code="03", published="29-GEN-26")
        + "; l.p. 11 August 1998 n. 9 art. 21-sexiesdecies",
    ),
)

IRPEF_2026 = IrpefLaw(brackets=IRPEF_BRACKETS_2026, regional=REGIONAL_SURCHARGES_2026)

__all__ = ["IRPEF_2026", "IRPEF_BRACKETS_2026", "REGIONAL_SURCHARGES_2026"]
