"""How an earlier year's tax loss reduces a later year's income (REB-1088, taxes design § 1).

Law as data, with the primary source and the day it was read. Nothing here computes anything:
`fiscal/loss_carry.py` applies these rules to the losses a legal entity has, and the estimates
(`fiscal/company_estimate.py`, `fiscal/ordinary_estimate.py`) show what it applied.

An srl (IRES), art. 84 TUIR
---------------------------
The loss of a tax period is carried forward **without a time limit** and reduces the taxable
income of the following periods by no more than **80%** of it, for the whole amount that fits in
that 80% (c. 1). The losses of the **first three tax periods** from the date of constitution
reduce the income of the following periods up to **100%** of it, on the condition that they
relate to **a new productive activity** (c. 2). Both conditions are facts the product does not
hold: the date of constitution and whether the activity is new are typed with the loss
(`TaxLoss.activity_start_year`, `TaxLoss.new_activity`) and the 100% applies only to a loss that
states both and falls in the three periods; every other loss reads the general 80%.

Not modelled, and said so in the estimate: c. 1 reduces the loss by the exempt income that exceeds
the non-deducted costs of art. 109 c. 5, and c. 3 to 3-quater take the carry away when the
majority of the voting rights is acquired by others and the main activity changes.

An ordinary person (IRPEF), art. 8 TUIR
---------------------------------------
* **A loss of an art or a profession** (art. 54 TUIR) is not carried forward: art. 8 c. 1 only lets
  it be subtracted from the income of other categories of **the same year**. The estimate has no
  other income, so such a loss never reduces a later year, and the pack says so with its source.
* **A loss of a commercial business** (art. 8 c. 3, which includes the simplified-accounting
  businesses of art. 66) is carried to the following periods in no more than **80%** of the income
  of the same category, for the whole amount that fits; the same paragraph applies art. 84 c. 2, so
  the first three periods read 100% under the same condition. The profile does not say which
  category the person's income is, so the estimate takes the loss the administrator typed as the
  loss of the income it reduces and says so.

Read on 2026-11-12 on Normattiva, text in force on that date. D.Lgs. 192/2024 rewrote the
change-of-control rules of art. 84 c. 3-bis to 3-quater (not modelled), not the 80% limit or the
first-periods rule; D.L. 84/2025 art. 84 c. 2 only dates it. The TUIR is renumbered from 2027 by
D.Lgs. 117/2026 (its art. 377 sets the date it applies from): these articles then have other
numbers and a later entry of the pack cites them, never an edit of this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LossRule:
    """One carry-forward rule: the share of a year's income a carried loss may reduce
    (`limit_percent`), the same for a loss of the first `first_periods` periods of a new activity
    (`first_periods_limit_percent`), the statute's words and the source they were read from."""

    limit_percent: Decimal
    first_periods: int
    first_periods_limit_percent: Decimal
    wording: str
    source: str


@dataclass(frozen=True)
class LossCarryLaw:
    """The carry-forward law that governs the tax years from `from_year` (a statutory section of
    the pack: an entry holds until a later one replaces it). `company` is the rule of an srl's
    IRES and `person_business` of an ordinary person's business loss; either is `None` where the
    pack's regime has no such tax. `professional_not_carried_source` is the citation that a loss
    of an art or a profession is not carried forward, `None` where the pack has no such person."""

    from_year: int
    company: LossRule | None = None
    person_business: LossRule | None = None
    professional_not_carried_source: str | None = None


COMPANY_LOSS_RULE = LossRule(
    limit_percent=Decimal("80"),
    first_periods=3,
    first_periods_limit_percent=Decimal("100"),
    wording=(
        "La perdita di un periodo d'imposta, determinata con le stesse norme valevoli per la "
        "determinazione del reddito, può essere computata in diminuzione del reddito dei periodi "
        "d'imposta successivi in misura non superiore all'ottanta per cento del reddito "
        "imponibile di ciascuno di essi e per l'intero importo che trova capienza in tale "
        "ammontare (c. 1). Le perdite realizzate nei primi tre periodi d'imposta dalla data di "
        "costituzione possono, con le modalità previste al comma 1, essere computate in "
        "diminuzione del reddito complessivo dei periodi d'imposta successivi entro il limite del "
        "reddito imponibile di ciascuno di essi e per l'intero importo che trova capienza nel "
        "reddito imponibile di ciascuno di essi a condizione che si riferiscano ad una nuova "
        "attività produttiva (c. 2)"
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 84 c. 1 and 2, Normattiva, text in force on "
        "2026-11-12, read 2026-11-12"
    ),
)

PERSON_BUSINESS_LOSS_RULE = LossRule(
    limit_percent=Decimal("80"),
    first_periods=3,
    first_periods_limit_percent=Decimal("100"),
    wording=(
        "Le perdite derivanti dall'esercizio di imprese commerciali [...] sono computate in "
        "diminuzione dei relativi redditi conseguiti nei periodi d'imposta e, per la differenza, "
        "nei successivi, in misura non superiore all'80 per cento dei relativi redditi conseguiti "
        "in detti periodi d'imposta e per l'intero importo che trova capienza in essi. Si "
        "applicano le disposizioni dell'articolo 84, comma 2 (art. 8 c. 3)"
    ),
    source=(
        "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 8 c. 3 and art. 84 c. 2, Normattiva, text "
        "in force on 2026-11-12, read 2026-11-12"
    ),
)

PROFESSIONAL_LOSS_NOT_CARRIED = (
    "D.P.R. 22 dicembre 1986 n. 917 (TUIR), art. 8 c. 1, Normattiva, text in force on "
    "2026-11-12, read 2026-11-12: «Il reddito complessivo si determina sommando i redditi di ogni "
    "categoria che concorrono a formarlo e sottraendo le perdite [...] derivanti dall'esercizio "
    "di arti e professioni»: the loss is subtracted from the income of the same period, and "
    "nothing in the article carries it to the following ones (c. 3 carries the commercial "
    "businesses' losses alone)"
)

COMPANY_LOSS_CARRY = LossCarryLaw(from_year=2026, company=COMPANY_LOSS_RULE)

PERSON_LOSS_CARRY = LossCarryLaw(
    from_year=2026,
    person_business=PERSON_BUSINESS_LOSS_RULE,
    professional_not_carried_source=PROFESSIONAL_LOSS_NOT_CARRIED,
)


__all__ = [
    "COMPANY_LOSS_CARRY",
    "COMPANY_LOSS_RULE",
    "PERSON_BUSINESS_LOSS_RULE",
    "PERSON_LOSS_CARRY",
    "PROFESSIONAL_LOSS_NOT_CARRIED",
    "LossCarryLaw",
    "LossRule",
]
