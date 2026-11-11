"""The roles an organization plays, its fiscal kinds, and how a VAT number is compared.

One registry, one row per company (design `2026-11-09-suppliers-purchases-and-iva-design.md`
§ 1): the same company can be a customer and a supplier, so what it is to us is a set of
**roles** on the row and not a table of its own. `freelancer` is the role the collaborators
module gives (C5); it is not `collaborator`, which is a permission role.
"""

import re
from typing import Any, Literal, get_args

from sqlalchemy import ColumnElement, func

OrganizationRole = Literal["customer", "supplier", "freelancer"]
ORGANIZATION_ROLES: tuple[str, ...] = get_args(OrganizationRole)
DEFAULT_ROLE: OrganizationRole = "customer"

# A list filters one role, or `any` for the search across roles, the exceptions § 1 names. Every
# other read keeps the default and sees customers only. `customer_or_neither` is «Clienti»
# (REB-1053): the customers plus every organization that is neither a customer nor a supplier
# (a freelancer-only record), so that no organization is on no list; «Fornitori» is `supplier`.
RoleFilter = Literal["customer", "supplier", "freelancer", "any", "customer_or_neither"]

# What a payment to a supplier involves (§ 1). `foreign` also carries where it is and what it
# supplies, which § 7 reads to decide whether reverse charge applies.
SupplierKind = Literal["company", "professional", "flat_rate", "occasional", "foreign"]
SUPPLIER_KINDS: tuple[str, ...] = get_args(SupplierKind)
SupplierArea = Literal["eu", "extra_eu"]
SUPPLIER_AREAS: tuple[str, ...] = get_args(SupplierArea)
SupplierSupplies = Literal["goods", "services"]
SUPPLIER_SUPPLIES: tuple[str, ...] = get_args(SupplierSupplies)

# The fiscal kinds a freelancer can have (collaborators design § 1): the same words as the
# supplier kinds, minus the two that are not a person working for us (`company`, `foreign`).
# The kind decides the letter type, the withholding and the net, so an organization with the
# role says one of these or nothing yet (an imported freelancer is classified by an admin).
FREELANCER_KINDS: tuple[str, ...] = ("professional", "flat_rate", "occasional")

HUB_FREELANCER_UNIQUE_INDEX = "uq_organizations_hub_freelancer_id"

CustomerKind = Literal["business", "consumer", "public_body"]
CUSTOMER_KINDS: tuple[str, ...] = get_args(CustomerKind)

# A foreign number such as `NL123456789B01` (14 characters) fits; the longest national format
# in use is shorter than this.
VAT_NUMBER_MAX_LENGTH = 20

VAT_UNIQUE_INDEX = "uq_organizations_country_vat"
# The rows the unique index covers: live, with a number that is something once its punctuation
# is gone (a stored `''` or a placeholder like `-` is nobody's number, and would otherwise be
# one group of duplicates).
VAT_INDEX_PREDICATE = (
    "deleted_at IS NULL AND vat_number IS NOT NULL "
    "AND regexp_replace(upper(vat_number), '[^0-9A-Z]', '', 'g') <> ''"
)
_NOT_ALPHANUMERIC = re.compile(r"[^0-9A-Z]")


def normalise_vat(value: str) -> str:
    """The comparison form of a VAT number: upper case, letters and digits only, so
    `nl 1234.56.789 b01` and `NL123456789B01` are one number."""
    return _NOT_ALPHANUMERIC.sub("", value.upper())


def vat_key(column: Any) -> ColumnElement[str]:
    """`normalise_vat` as SQL, the expression the unique index is built on. Same rule, same
    result: `[^0-9A-Z]` removed after `upper`. ASCII only, like the numbers it compares."""
    return func.regexp_replace(func.upper(column), "[^0-9A-Z]", "", "g")


def country_key(column: Any) -> ColumnElement[str]:
    return func.upper(column)
