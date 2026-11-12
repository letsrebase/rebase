"""What the estimate reads for the variations of an srl's income (REB-1089, taxes design § 1).

The estimate (`fiscal/company_estimate.py`) is pure, so the variations reach it as data: the law of
the year, the variations of the costs (`fiscal/cost_variations.py`) and the IRAP paid. The database
reader that builds them is `fiscal/income_facts.py`, the reader the analytics service calls before
the estimate as it calls the withholdings'. The IRAP deducted from IRES is
`fiscal/irap_deduction.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pigrocrm.core.fiscal.cost_variations import CostVariations
from pigrocrm.core.fiscal.law_ires import IresVariationsLaw


@dataclass(frozen=True)
class IrapPaid:
    """The IRAP paid in the financial year: `total` is every payment settled in it, `advances`
    the part of it that paid the advances of the tax year itself (counted only up to the IRAP due
    for the year)."""

    total: Decimal
    advances: Decimal


@dataclass(frozen=True)
class IncomeVariations:
    """What the estimate needs for the variations of the year: the law of the year, the variations
    of the costs, and the IRAP paid (`None` when no payment is registered)."""

    law: IresVariationsLaw
    costs: CostVariations
    irap_paid: IrapPaid | None


__all__ = ["IncomeVariations", "IrapPaid"]
