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

The household rules of eight regions (Piemonte, Veneto, Marche, Campania, Puglia, Sardegna,
Trento, Bolzano) and Bolzano's second detraction are computed since REB-1087, each from the text
its region or province publishes (the sources below, read 2026-11-12 in REB-1086). The detractions
of the IRPEF itself, art. 13 c. 5 and art. 12 TUIR, are the data `SELF_EMPLOYMENT_DETRACTION_2026`
and `FAMILY_DETRACTIONS_2026`.

Only tax year 2026 is here. The IRPEF of 2025 (23%, 35%, 43%: art. 1 c. 2 L. 207/2024) is not
entered, because the pack's other 2025 sections (advances, calendar, IVA) are not verified for
2025 and `law_for(2025)` must stay empty rather than half right. Each year is its own `IrpefLaw`
in the pack's `irpef`, read by exact year: a year with no entry gets no IRPEF figure, never
2026's, and the year's law is added next to this one without touching it.
"""

from __future__ import annotations

from decimal import Decimal

from pigrocrm.core.fiscal.law_irpef import (
    ChildDetractionRule,
    DeductibleCharge,
    FamilyDetractions,
    IncomeBandDetraction,
    IrpefLaw,
    ProgressiveDetraction,
    RateBracket,
    ReducedRateRule,
    RegionalSurchargeLaw,
    SelfEmploymentDetraction,
    SpouseBand,
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
        # The text, as read on 2026-11-12:
        # «1 bis. A decorrere dal periodo di imposta 2026, si applicano le seguenti detrazioni: a)
        # euro 100,00 per i contribuenti con più di due figli a carico, per ciascun figlio, a
        # partire dal primo [...]; b) euro 500,00 per i contribuenti con figli a carico,
        # portatori di handicap ai sensi dell'articolo 3 della legge 5 febbraio 1992, n. 104»
        # «[...], per ciascun figlio [...]. 2. Ai fini della spettanza e della ripartizione delle
        # detrazioni si applicano le disposizioni [dell'art. 12 TUIR]». The law's text rules for
        # 2026: the Region's own tax page still shows 250 euro per disabled child, the 2022-2025
        # figure. No income limit is stated. The children counted are those under 30 and the
        # disabled ones of any age (the art. 12 c. 1 lett. c) children).
        child_rule=ChildDetractionRule(
            per_child=_D(100),
            min_children=3,
            per_disabled=_D(500),
            scope="under_30_or_disabled",
        ),
        source=_FINANZE.format(code="13", published="29-GEN-26")
        + "; l.r. 28 March 2022 n. 4 art. 2 c. 1-bis and c. 2, as inserted by l.r. 6 August 2025 "
        "n. 16 art. 12 (Arianna, «Versione vigente», read 2026-11-12)",
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
        exempt_tested_on="total",
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
        # The text, as read on 2026-11-12:
        # «A decorrere dall'anno 2006 l'aliquota dell'addizionale regionale IRPEF è fissata nella
        # percentuale dello 0,9 per cento per i disabili aventi un reddito imponibile, ai fini
        # dell'addizionale regionale IRPEF, non superiore a euro 50.000,00, e per i soggetti con a
        # carico fiscalmente, ai sensi dell'articolo 12 [TUIR], un disabile e aventi un reddito»
        # «imponibile [...] non superiore a euro 50.000,00. Qualora il disabile sia a carico di più
        # soggetti, l'aliquota dello 0,9 per cento [si applica solo se la somma dei redditi
        # imponibili non supera 50.000].» Disabled means art. 3 L. 104/1992.
        reduced_rate=ReducedRateRule(rate=_D("0.9"), income_cap=_50K, who="taxpayer_or_dependant"),
        source=_FINANZE.format(code="21", published="22-GEN-26")
        + "; l.r. Veneto 26 November 2005 n. 19 art. 1 c. 5, testo vigente (consiglioveneto.it, "
        "read 2026-11-12)",
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
        # The text, as read on 2026-11-12:
        # «A decorrere dall'anno di imposta 2022, la maggiorazione dell'aliquota dell'addizionale
        # regionale all'IRPEF [...] non trova applicazione per i contribuenti con un reddito
        # imponibile [...] fino a 50.000,00 euro con uno o più figli portatori di handicap di
        # cui all'articolo 3 della legge 5 febbraio 1992, n. 104 [...], comunque a carico ai sensi»
        # «del comma 2 dell'articolo 12 [...]. Qualora i figli siano a carico di più soggetti, la
        # maggiorazione non si applica solo nel caso in cui la somma dei redditi imponibili [non
        # supera 50.000].» The increases not applying, the rate is the base 1.23% on all of it;
        # the child's age is irrelevant.
        reduced_rate=ReducedRateRule(rate=_D("1.23"), income_cap=_50K, who="disabled_child"),
        source=_FINANZE.format(code="11", published="22-GEN-26")
        + "; l.r. 23 March 2022 n. 5 art. 1 c. 2 (consiglio.marche.it, read 2026-11-12); "
        "L. 207/2024 art. 1 c. 728",
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
        # The text, as read on 2026-11-12:
        # «2. A decorrere dall'anno d'imposta 2022, ai soggetti aventi un reddito imponibile ai
        # fini dell'addizionale regionale IRPEF non superiore ad euro 28.000,00 lordi e con almeno
        # due figli fiscalmente a carico, spetta una detrazione [...] pari ad euro 30,00 per
        # ciascun figlio fiscalmente a carico, in proporzione alla percentuale ed ai mesi a»
        # «carico.» c. 3: 40 euro per child with a disability (art. 3 L. 104/1992), with no minimum
        # number of children; c. 4: no credit. Whether a disabled child also counts for the 30
        # euro is not settled (c. 4 says «le detrazioni di cui ai commi 2 e 3»): the 30 is left
        # off a disabled child, which still counts toward the two children.
        child_rule=ChildDetractionRule(
            per_child=_D(30),
            min_children=2,
            per_disabled=_D(40),
            per_child_counts_disabled=False,
            income_cap=_28K,
        ),
        unmodelled=(
            "The 30 euro is not given for a disabled child, which counts only toward the "
            "minimum of two children and gets the 40 euro: whether the two add up is not "
            "settled by the text (c. 4 speaks of «le detrazioni di cui ai commi 2 e 3»), for "
            "the accountant, at most 30 euro per disabled child."
        ),
        source=_FINANZE.format(code="05", published="29-GEN-26")
        + "; l.r. 30 March 2022 n. 7 art. 1 c. 2-4 (regione.campania.it, read 2026-11-12)",
    ),
    RegionalSurchargeLaw(
        key="16",
        name="Puglia",
        brackets=_four("1.33", "2.13", "3.23", "3.33"),
        # The text, as read on 2026-11-12:
        # «1. A decorrere dal periodo d'imposta 2016, le detrazioni previste dall'articolo 12,
        # comma 1, lettera c), del d.P.R. 917/1986 sono maggiorate [...] dei seguenti importi: a)
        # 20 euro per i contribuenti con più di tre figli a carico, per ciascun figlio, a partire
        # dal primo [...]; b) la detrazione spettante ai sensi della lettera a) è aumentata di un»
        # «importo pari a 375 euro per ogni figlio con diversa abilità [...]. 2. Ai fini della
        # spettanza e della ripartizione delle detrazioni si applicano le disposizioni previste
        # dall'articolo 12 [...].» Letter b) increases the detraction of letter a), which needs
        # more than three children: read literally, 375 euro is due only then. The law increases
        # the detractions of art. 12 c. 1 lett. c), so the children counted are those who have
        # one: aged 21 to under 30, or disabled from 21 on.
        child_rule=ChildDetractionRule(
            per_child=_D(20),
            min_children=4,
            per_disabled=_D(375),
            disabled_needs_min_children=True,
            scope="lett_c_children",
        ),
        unmodelled=(
            "The 375 euro per disabled child is given only with more than three dependent "
            "children, the literal reading of letter b) («la detrazione spettante ai sensi "
            "della lettera a)»); whether it is due with fewer is for the accountant. The "
            "children counted are those of art. 12 c. 1 lett. c), the law increasing that "
            "detraction: whether younger ones count is for the accountant too. Where the "
            "income and tax do not "
            "allow the detraction, a direct payment set by the regional executive: not a tax "
            "amount, never computed."
        ),
        source=_FINANZE.format(code="14", published="29-MAG-26")
        + " (the page's first version, decreto n. 3 of 28 May 2026 of the President as "
        "commissioner ad acta; the 28-GEN-26 one is 1.33, 1.43, 1.63, 1.85); l.r. Puglia 29 "
        "December 2015 n. 40 art. 3 (trasparenza.regione.puglia.it, read 2026-11-12)",
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
        # The text, as read on 2026-11-12:
        # «A decorrere dall'anno d'imposta 2022, ai soggetti aventi un reddito imponibile ai fini
        # dell'addizionale regionale all'IRPEF non superiore a euro 50.000 e con figli minorenni
        # fiscalmente a carico spetta una detrazione [...] di euro 200 per ogni figlio minorenne,
        # in proporzione alla percentuale e ai mesi a carico. Se l'imposta dovuta e' minore della»
        # «detrazione non sorge alcun credito d'imposta. La detrazione [...] e' aumentata di un
        # importo pari a euro 100 per ogni figlio [disabile].» SardegnaEntrate: «la detrazione va
        # calcolata per ogni figlio minore alla data del 1° gennaio di ciascun anno e in
        # proporzione al numero di mesi per cui la condizione sussiste»; a month counts in full
        # only if the minority lasted at least 15 days of it.
        child_rule=ChildDetractionRule(
            per_child=_D(200),
            per_disabled=_D(100),
            income_cap=_50K,
            scope="minor_on_1_january",
        ),
        source=_FINANZE.format(code="15", published="29-GEN-26")
        + "; D.L. 201/2011 art. 28; l.r. 48/2018 art. 2 as replaced by l.r. 11 July 2022 n. 13, "
        "quoted by Regione Sardegna, SardegnaEntrate, «Addizionale regionale IRPEF» "
        "(sardegnaentrate.it, read 2026-11-12)",
    ),
    RegionalSurchargeLaw(
        key="TN",
        name="Provincia autonoma di Trento",
        brackets=_four("1.23", "1.23", "1.23", "1.73"),
        # «Ai contribuenti aventi un reddito imponibile non superiore a euro 30.000,00 spetta
        # una deduzione di euro 30.000,00. Tale deduzione non spetta ai contribuenti aventi
        # un reddito imponibile superiore a euro 30.000,00.»
        exempt_up_to=_D(30000),
        # The text, as read on 2026-11-12:
        # «Per gli anni d'imposta 2025 e 2026, ai soggetti aventi un reddito imponibile ai fini
        # dell'addizionale regionale IRPEF non superiore a 50.000 euro spetta una detrazione [...]
        # di 246 euro, in proporzione alla percentuale e ai mesi di carico, per ogni figlio che si
        # trova nelle condizioni previste dall'articolo 12, comma 2, [TUIR]. Se l'imposta dovuta è»
        # «minore della detrazione non sorge alcun credito d'imposta.» No age limit in the text.
        child_rule=ChildDetractionRule(per_child=_D(246), income_cap=_50K),
        source=_FINANZE.format(code="18", published="22-GEN-26")
        + "; l.p. 23 December 2019 n. 13 art. 1 c. 2-quater, 2-sexies and 3-bis, as amended by "
        "l.p. 29 December 2025 n. 11 art. 1 (consiglio.provincia.tn.it, read 2026-11-12)",
    ),
    RegionalSurchargeLaw(
        key="BZ",
        name="Provincia autonoma di Bolzano",
        brackets=_three("1.23", "1.23", "1.73"),
        # «a) Ai contribuenti aventi un reddito imponibile ai fini dell'addizionale regionale
        # IRPEF non superiore a 90.000,00 euro spetta una detrazione d'imposta di 430,50
        # euro.» The detractions do not make a credit (the page's note).
        # Second detraction, c. 4-bis and 4-ter of the same article:
        # «(4-bis) A decorrere dall'anno d'imposta 2022, per i redditi imponibili ai fini
        # dell'addizionale regionale all'IRPEF di importo superiore a 50.000,00 euro, oltre alla
        # detrazione di cui al comma 1, spetta un'ulteriore detrazione. Tale detrazione è
        # determinata dall'importo di 125,00 euro moltiplicato per il rapporto tra il reddito»
        # «imponibile diminuito di 50.000,00 euro e l'importo di 25.000,00 euro. L'importo massimo
        # detraibile ammonta a 125,00 euro. (4-ter) Le detrazioni di cui ai commi 1, 2-bis e
        # 4-bis sono cumulabili ma, in nessun caso, generano credito d'imposta.» c. 4-bis has no
        # upper limit of income, but says «oltre alla detrazione di cui al comma 1», which stops at
        # 90,000: read literally it gives 125 euro above 90,000 too. That reading is doubtful (at
        # most 125 euro), so nothing is given above 90,000.
        detractions=(
            IncomeBandDetraction(None, _D(90000), _D("430.50")),
            ProgressiveDetraction(
                from_income=_50K, full_at=_D(75000), maximum=_D(125), to_income=_D(90000)
            ),
        ),
        # The text, as read on 2026-11-12:
        # «(2-bis) A decorrere dal periodo d'imposta 2025, ai soggetti aventi un reddito
        # imponibile ai fini dell'addizionale regionale IRPEF non superiore a 90.000,00 euro e con
        # figli/figlie fiscalmente a carico spetta una detrazione [...] di 340,00 euro per ogni
        # figlio/figlia in proporzione alla percentuale e ai mesi di carico.» Any age.
        child_rule=ChildDetractionRule(per_child=_D(340), income_cap=_D(90000)),
        unmodelled=(
            "The 90,000 euro test counts income from rental under the flat tax, tips under the "
            "substitute tax and flat-rate regime income (c. 3), which the profile does not "
            "hold. The second detraction (c. 4-bis) is not given above 90,000 euro, where c. 1 "
            "stops and the Agenzia and the Province print no limit: read literally it is "
            "125 euro there, which is for the accountant."
        ),
        source=_FINANZE.format(code="03", published="29-GEN-26")
        + "; l.p. 11 August 1998 n. 9 art. 21-sexiesdecies c. 1, 2-bis, 3, 4-bis and 4-ter "
        "(lexbrowser.provinz.bz.it, read 2026-11-12)",
    ),
)

# The compulsory social security contributions are deducted from the total income (REB-1072): art.
# 10 c. 1 lett. e) D.P.R. 917/1986 (TUIR), text in force from 31-12-2024 to 31-12-2026 on
# Normattiva, read 2026-11-11. The Agenzia's page «Aliquote e calcolo dell'Irpef» (updated 13
# January 2026) says the same: the gross tax is computed on the total income «al netto degli oneri
# deducibili» and the deductible charges are «per esempio i contributi previdenziali e
# assistenziali». The article is repealed from 1 January 2027 by D.Lgs. 19 June 2026 n. 117 (the
# new income tax code); the next year's entry re-reads the corresponding article.
SOCIAL_SECURITY_DEDUCTION_2026 = DeductibleCharge(
    wording=(
        "Dal reddito complessivo si deducono, se non sono deducibili nella determinazione dei "
        "singoli redditi che concorrono a formarlo, i seguenti oneri sostenuti dal contribuente: "
        "[...] e) i contributi previdenziali ed assistenziali versati in ottemperanza a "
        "disposizioni di legge"
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 10 c. 1 lett. e), Normattiva, text in force "
        "from 31-12-2024 to 31-12-2026, read 2026-11-11; Agenzia delle Entrate, «Aliquote e "
        "calcolo dell'Irpef», updated 13 January 2026"
    ),
)

# The detraction for self-employment income (REB-1087): art. 13 c. 5 TUIR, Normattiva, «in
# vigore dal 1-1-2025 al 31-12-2026», read 2026-11-12 (no amendment in force for 2026: L. 199/2025
# art. 1 c. 4 only adds c. 5-bis to art. 16-ter). The Agenzia's technical specifications for the
# 2026 return (§ 21.4, pp. 158-159) say it is not prorated by period and is computed on the
# «reddito di riferimento» (RN1 col. 1 + RS37 col. 14 - RN2, RN2 being the main-home deduction): the
# income before the deductible charges, so the INPS contributions do not lower it, and with
# the flat-rate regime's income (art. 1 c. 75 L. 190/2014). Worked values: 5,500 -> 1,265.00;
# 10,000 -> 1,112.00; 15,000 -> 941.94; 20,000 -> 771.96; 28,000 -> 500.00; 40,000 -> 227.25;
# 50,000 -> 0. The article is repealed from 1 January 2027 by D.Lgs. 19 June 2026 n. 117; the next
# year's entry re-reads the corresponding article.
SELF_EMPLOYMENT_DETRACTION_2026 = SelfEmploymentDetraction(
    wording=(
        "Se alla formazione del reddito complessivo concorrono uno o più redditi di cui agli "
        "articoli 50, comma 1, lettere e), f), g), h) e i), ad esclusione di quelli derivanti "
        "dagli assegni periodici indicati nell'articolo 10, comma 1, lettera c), fra gli oneri "
        "deducibili, 53, 66 e 67, comma 1, lettere i) e l), spetta una detrazione dall'imposta "
        "lorda, non cumulabile con quelle previste ai commi 1, 2, 3 e 4 del presente articolo, "
        "pari a: a) 1.265 euro, se il reddito complessivo non supera 5.500 euro; b) 500 euro, "
        "aumentata del prodotto fra 765 euro e l'importo corrispondente al rapporto fra 28.000 "
        "euro, diminuito del reddito complessivo, e 22.500 euro, se l'ammontare del reddito "
        "complessivo è superiore a 5.500 euro ma non a 28.000 euro; b-bis) 500 euro, se il "
        "reddito complessivo è superiore a 28.000 euro ma non a 50.000 euro. La detrazione spetta "
        "per la parte corrispondente al rapporto tra l'importo di 50.000 euro, diminuito del "
        "reddito complessivo, e l'importo di 22.000 euro."
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 13 c. 5 and c. 6 (ratios cut at four "
        "decimals), Normattiva, text in force from 1-1-2025 to 31-12-2026, read 2026-11-12; "
        "Agenzia delle Entrate, specifiche tecniche della dichiarazione dei redditi persone "
        "fisiche 2026, § 21.4 (reference income, no proration by period)"
    ),
    full_up_to=_D(5500),
    full_amount=_D(1265),
    middle_to=_28K,
    middle_base=_D(500),
    middle_extra=_D(765),
    middle_span=_D(22500),
    upper_to=_50K,
    upper_amount=_D(500),
    upper_span=_D(22000),
)

# The detractions for dependent family members (REB-1087): art. 12 c. 1 lett. a), c) and d) TUIR,
# Normattiva, «Testo in vigore dal 12-8-2026 al 31-12-2026», read 2026-11-12, with the 21-30 age
# band and the ascendants-only lett. d) that art. 1 c. 11 L. 207/2024 introduced (Agenzia,
# specifiche § 20.3). Who is dependent is c. 2 (own total income at most 2,840.51 euro, 4,000 euro
# for a child of 24 or less), prorated by month (c. 3), the ratios of lett. c) and d) cut at four
# decimals and the detraction not due when one is zero, negative or one (c. 4). The reference
# income is the one of art. 13 above. A child under 21 gives no detraction. Not computed: the
# first-child detraction of lett. a) for a taxpayer with no other parent (lett. c, «se più
# convenienti»), and the exclusion of a non-EU taxpayer's relatives living abroad (c. 2-bis): the
# profile holds neither.
FAMILY_DETRACTIONS_2026 = FamilyDetractions(
    wording=(
        "a) 800 euro, diminuiti del prodotto tra 110 euro e l'importo corrispondente al rapporto "
        "fra reddito complessivo e 15.000 euro, se il reddito complessivo non supera 15.000 euro; "
        "690 euro, se il reddito complessivo è superiore a 15.000 euro ma non a 40.000 euro; 690 "
        "euro, se il reddito complessivo è superiore a 40.000 euro ma non a 80.000 euro (la "
        "detrazione spetta per la parte corrispondente al rapporto tra l'importo di 80.000 euro, "
        "diminuito del reddito complessivo, e 40.000 euro), aumentata di 10, 20, 30, 20 e 10 euro "
        "per un reddito complessivo superiore a 29.000, 29.200, 34.700, 35.000 e 35.100 euro ma "
        "non a 29.200, 34.700, 35.000, 35.100 e 35.200 euro; c) 950 euro per ciascun figlio [...] "
        "di età pari o superiore a 21 anni ma inferiore a 30 anni, nonché per ciascun figlio di "
        "età pari o superiore a 30 anni con disabilità accertata ai sensi dell'articolo 3 della "
        "legge 5 febbraio 1992, n. 104 [...]. La detrazione spetta per la parte corrispondente al "
        "rapporto tra l'importo di 95.000 euro, diminuito del reddito complessivo, e 95.000 euro. "
        "In presenza di più figli che danno diritto alla detrazione, l'importo di 95.000 euro è "
        "aumentato per tutti di 15.000 euro per ogni figlio successivo al primo; d) 750 euro, da "
        "ripartire pro quota tra coloro che hanno diritto alla detrazione, per ciascun ascendente "
        "che conviva con il contribuente. La detrazione spetta per la parte corrispondente al "
        "rapporto tra l'importo di 80.000 euro, diminuito del reddito complessivo, e 80.000 euro."
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 12 c. 1, 2, 3 and 4, Normattiva, text in "
        "force from 12-8-2026 to 31-12-2026, read 2026-11-12, as amended by art. 1 c. 11 L. "
        "207/2024; Agenzia delle Entrate, Redditi PF 2026 fascicolo 1, rigo RN6, and specifiche "
        "tecniche §§ 20.2 and 20.3"
    ),
    spouse_full=_D(800),
    spouse_reduction=_D(110),
    spouse_low=_15K,
    spouse_mid=_D(690),
    spouse_mid_to=_D(40000),
    spouse_high=_D(80000),
    spouse_bands=(
        SpouseBand(_D(29000), _D(29200), _D(10)),
        SpouseBand(_D(29200), _D(34700), _D(20)),
        SpouseBand(_D(34700), _D(35000), _D(30)),
        SpouseBand(_D(35000), _D(35100), _D(20)),
        SpouseBand(_D(35100), _D(35200), _D(10)),
    ),
    child_amount=_D(950),
    child_limit=_D(95000),
    child_limit_step=_D(15000),
    child_from_age=21,
    child_until_age=30,
    ascendant_amount=_D(750),
    ascendant_limit=_D(80000),
)

IRPEF_2026 = IrpefLaw(
    year=2026,
    brackets=IRPEF_BRACKETS_2026,
    regional=REGIONAL_SURCHARGES_2026,
    social_security_deduction=SOCIAL_SECURITY_DEDUCTION_2026,
    self_employment_detraction=SELF_EMPLOYMENT_DETRACTION_2026,
    family_detractions=FAMILY_DETRACTIONS_2026,
    brackets_source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 11 c. 1, Normattiva, text in force on "
        "2026-11-11, with L. 199/2025 art. 1 c. 3; Agenzia delle Entrate, «Aliquote e calcolo "
        "dell'Irpef», updated 13 January 2026"
    ),
)

__all__ = [
    "FAMILY_DETRACTIONS_2026",
    "IRPEF_2026",
    "IRPEF_BRACKETS_2026",
    "REGIONAL_SURCHARGES_2026",
    "SELF_EMPLOYMENT_DETRACTION_2026",
    "SOCIAL_SECURITY_DEDUCTION_2026",
]
