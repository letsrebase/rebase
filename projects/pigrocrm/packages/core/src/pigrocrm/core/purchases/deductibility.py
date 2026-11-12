"""What a person can state about how the law treats a purchase (REB-1089).

Two optional facts, both stated by a person and never guessed from a description:

* `deductibility_kind`, on a purchase category (every purchase of it) or on one purchase (which
  overrides its category): the sort of cost whose deduction the law limits, or `full` for a cost
  stated as deductible without limit. A purchase with neither states nothing and is deducted in
  full, which is what the estimate has always done.
* `asset_group`, on one purchase: the cost is a depreciable tangible asset, and the group names
  the line of the ministerial depreciation table (D.M. 31 December 1988) it belongs to. `other`
  says it is an asset whose group the pack's table does not carry.

This module holds only the vocabulary, so the purchase's model and schemas can name the values
without importing the tax law. The rule behind each value (the percentage, the cap, the
coefficient and the norm that sets it) is data in the pack: `fiscal/law_ires.py`.
"""

from __future__ import annotations

from typing import Final, Literal

DeductibilityKind = Literal[
    "full",
    "vehicle",
    "vehicle_agent",
    "vehicle_employee",
    "phone",
    "hospitality",
    "representation",
]
DEDUCTIBILITY_KINDS: Final[tuple[str, ...]] = (
    "full",
    "vehicle",
    "vehicle_agent",
    "vehicle_employee",
    "phone",
    "hospitality",
    "representation",
)

AssetGroup = Literal["office_furniture", "office_electronics", "cars", "goods_vehicles", "other"]
ASSET_GROUPS: Final[tuple[str, ...]] = (
    "office_furniture",
    "office_electronics",
    "cars",
    "goods_vehicles",
    "other",
)
# The group a person picks when the asset is not on the pack's table: no coefficient is known,
# so the estimate says what is missing instead of guessing one.
OTHER_ASSET_GROUP: Final = "other"

# The seeded categories whose name does not say what was bought, so a purchase in one with no
# stated kind may be a vehicle, phone, hotel or restaurant cost the law limits. They are told apart
# by `code` (`timetracking/categories.py` seeds them). The estimate says how many such purchases it
# deducted in full.
AMBIGUOUS_CATEGORY_CODES: Final[tuple[str, ...]] = ("travel", "other")

__all__ = [
    "AMBIGUOUS_CATEGORY_CODES",
    "ASSET_GROUPS",
    "DEDUCTIBILITY_KINDS",
    "OTHER_ASSET_GROUP",
    "AssetGroup",
    "DeductibilityKind",
]
