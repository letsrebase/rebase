"""The shapes of a legal entity's year basis (`fiscal/basis.py`, REB-1071), kept apart from the
function that computes it so the estimate's payload can carry one without importing the staff
and due-item machinery the computation reads."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.fiscal.law_income_basis import IncomeBasis

LineCode = Literal[
    "not_collected",
    "inps_surcharge",
    "shared_purchases",
    "staff_cost_not_estimated",
    "purchases_by_document_date",
]


class BasisLine(BaseModel):
    """One thing a basis left out of its figures, or counted on a stand-in, and why. `code` is
    stable (a screen may key on it); `reason` is the Italian sentence a person reads. `amount`
    is `None` where an amount means nothing (people with no cost estimated), `count` where a
    count does."""

    model_config = ConfigDict(frozen=True)

    code: LineCode
    amount: Decimal | None = None
    count: int | None = None
    reason: str


class BasisCosts(BaseModel):
    """The costs of the year by what IRAP and the income tax tell apart: what the purchases
    cost (`Purchase.cost_amount`, frozen from the buyer's pack), the staff's and the directors'
    cost. `total` is their sum, computed once here."""

    model_config = ConfigDict(frozen=True)

    purchases: Decimal
    staff: Decimal
    director: Decimal
    total: Decimal


class YearBasis(BaseModel):
    """What a legal entity's year is made of, on the basis its regime's law names.

    `revenue` is the taxable amount the estimate starts from, net of the pack's tagged charges.
    `costs` is `None` where the regime's income needs none (the flat-rate regime). `period_from`
    and `period_to` are the days the figures are of, both included. `label` is the one Italian
    line a screen prints under the figure; `wording` and `source` are the law's own text for the
    basis (`IncomeBasisLaw`)."""

    model_config = ConfigDict(frozen=True)

    year: int
    legal_entity_id: UUID
    basis: IncomeBasis
    period_from: date
    period_to: date
    label: str
    wording: str
    source: str
    revenue: Decimal
    costs: BasisCosts | None
    left_out: list[BasisLine]
    assumptions: list[BasisLine]


__all__ = ["BasisCosts", "BasisLine", "LineCode", "YearBasis"]
