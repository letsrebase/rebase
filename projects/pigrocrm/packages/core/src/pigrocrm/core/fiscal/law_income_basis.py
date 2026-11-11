"""Which basis each regime measures its income on (REB-1066, taxes design § 2).

A year's income is the same set of invoices and payments read two ways: a person's and the
flat-rate regime's income counts what was **collected** in the period (cash), a company's counts
what **accrued** in it. The pack states the basis as data, with the words of the law that say
so, and the estimate that builds one revenue figure per basis (REB-1071) reads it from here
instead of choosing it in a service. Nothing in this module computes anything.

Every basis below is a verified row of the register in `2026-11-09-space-profiles-modules-and-
the-money-picture-design.md` § A5 («Cash or accrual basis of the ordinary person's and the srl's
income», «Flat-rate basis...»), read on 2026-11-11.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IncomeBasis = Literal["cash", "accrual"]


@dataclass(frozen=True)
class IncomeBasisLaw:
    """The basis a regime's income is measured on, with the text that says so.

    `wording` is the statute's or the Agenzia's own Italian text, kept verbatim so a later
    reader can check the basis against the source without a translation in between;
    `source` names the primary source and the date it was read."""

    basis: IncomeBasis
    wording: str
    source: str


# An ordinary-regime person who practises an art or a profession: art. 54 c. 1 TUIR. The pack
# reads the ordinary person as that (the design's own reading, A5 row for REB-975); a person
# whose income is business income is outside it. Simplified accounting is cash too (art. 66 c.
# 1 TUIR, «percepiti nel periodo d'imposta»), ordinary accounting is accrual (art. 56 c. 1
# sends business income to art. 109), and the profile does not say which one applies, so the
# pack does not guess it: the accountant's list carries the question (REB-975).
PERSON_CASH = IncomeBasisLaw(
    basis="cash",
    wording=(
        "Il reddito derivante dall'esercizio di arti e professioni è costituito dalla "
        "differenza tra tutte le somme e i valori in genere a qualunque titolo percepiti nel "
        "periodo di imposta in relazione all'attività artistica o professionale e l'ammontare "
        "delle spese sostenute nel periodo stesso nell'esercizio dell'attività"
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 54 c. 1, Normattiva, text in force from "
        "12-8-2026, read 2026-11-11"
    ),
)

# An srl: the total income starts from the profit of the income statement of the financial year
# closed in the period (art. 83 c. 1 TUIR) and the components belong to the year they accrue in
# (art. 109 c. 1 TUIR). IRAP's base is also read off the income statement (art. 5 c. 1 D.Lgs.
# 446/1997), so the same accrual figures feed both taxes.
COMPANY_ACCRUAL = IncomeBasisLaw(
    basis="accrual",
    wording=(
        "Il reddito complessivo è determinato apportando all'utile o alla perdita risultante "
        "dal conto economico, relativo all'esercizio chiuso nel periodo d'imposta, le "
        "variazioni in aumento o in diminuzione conseguenti all'applicazione dei criteri "
        "stabiliti nelle successive disposizioni della presente sezione (art. 83 c. 1); i "
        "ricavi, le spese e gli altri componenti positivi e negativi, per i quali le "
        "precedenti norme della presente Sezione non dispongono diversamente, concorrono a "
        "formare il reddito nell'esercizio di competenza (art. 109 c. 1)"
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 83 c. 1 and art. 109 c. 1, Normattiva, "
        "text in force from 4-7-2026; D.Lgs. 15 dicembre 1997 n. 446, art. 5 c. 1 for IRAP "
        "(«la differenza tra il valore e i costi della produzione di cui alle lettere A) e B) "
        "dell'articolo 2425 del codice civile»); read 2026-11-11"
    ),
)

# The flat-rate regime: revenue and fees collected (L. 190/2014 art. 1 c. 64), whether the
# income is a profession's or a business's. The Agenzia's own instructions state the principle.
FLAT_RATE_CASH = IncomeBasisLaw(
    basis="cash",
    wording=(
        "determinano il reddito imponibile applicando all'ammontare dei ricavi o dei compensi "
        "percepiti il coefficiente di redditività (L. 190/2014 art. 1 c. 64); nel regime in "
        "esame, i ricavi ed i compensi vengono imputati al periodo d'imposta sulla base del "
        "cd. «principio di cassa», e cioè in considerazione del momento di effettiva "
        "percezione del ricavo o compenso: tale criterio, pertanto, si applica tanto in caso "
        "di reddito derivante dall'esercizio di arti o professione, quanto in caso di reddito "
        "d'impresa (Istruzioni)"
    ),
    source=(
        "L. 23 dicembre 2014 n. 190, art. 1 c. 64, Normattiva, text in force from 12-8-2026; "
        "Agenzia delle Entrate, Redditi Persone Fisiche 2026, Fascicolo 3, Istruzioni, quadro "
        "LM sezione III, «Determinazione del reddito» (page 45); read 2026-11-11"
    ),
)


__all__ = [
    "COMPANY_ACCRUAL",
    "FLAT_RATE_CASH",
    "PERSON_CASH",
    "IncomeBasis",
    "IncomeBasisLaw",
]
