"""Organizations that share a VAT number, and the unique index that keeps it from happening.

Design `2026-11-09-suppliers-purchases-and-iva-design.md` § 1: one record per company. Before
the unique index on `(country, normalised VAT number)` could be built, the rows that already
share one are listed and a person resolves them, by merging one into the other
(`OrganizationService.merge`, `merge.py`) or by correcting a number. Nothing in this module
writes a row.

The same report is read three ways: `pigrocrm duplicate-organizations` for an operator before a
deploy, `GET /api/organizations/duplicates` and the `list_duplicate_organizations` tool for the
person who resolves them. The migration that adds the index (0078) builds it only when the
report is empty and leaves a space with duplicates running without it, so a deploy never fails
on data; `ensure_vat_index` builds it later, at the boot after the last duplicate is gone.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Table, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.roles import VAT_UNIQUE_INDEX, country_key, vat_key


@dataclass(frozen=True)
class DuplicateRow:
    id: UUID
    legal_name: str
    vat_number: str
    roles: tuple[str, ...]
    legal_entity_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class DuplicateGroup:
    """The live rows of one country that share one normalised VAT number, oldest first."""

    country: str
    vat_key: str
    organizations: tuple[DuplicateRow, ...]


def duplicate_vat_groups(session: Session) -> list[DuplicateGroup]:
    """Every group of two or more live organizations with the same country and normalised
    VAT number, groups by country then number and rows oldest first. Empty when the index
    can be built."""
    country = country_key(Organization.country)
    key = vat_key(Organization.vat_number)
    live = (
        Organization.deleted_at.is_(None),
        Organization.vat_number.is_not(None),
        key != "",
    )
    shared = (
        select(country, key).where(*live).group_by(country, key).having(func.count() > 1)
    ).subquery()
    rows = session.execute(
        select(Organization, country, key)
        .join(shared, (shared.c[0] == country) & (shared.c[1] == key))
        .where(*live)
        .order_by(country, key, Organization.created_at, Organization.id)
    ).all()
    groups: dict[tuple[str, str], list[DuplicateRow]] = {}
    for organization, row_country, row_key in rows:
        assert organization.vat_number is not None
        groups.setdefault((row_country, row_key), []).append(
            DuplicateRow(
                id=organization.id,
                legal_name=organization.legal_name,
                vat_number=organization.vat_number,
                roles=tuple(organization.roles),
                legal_entity_id=organization.legal_entity_id,
                created_at=organization.created_at,
            )
        )
    return [
        DuplicateGroup(country=c, vat_key=k, organizations=tuple(members))
        for (c, k), members in groups.items()
    ]


def ensure_vat_index(session: Session) -> Literal["present", "created", "blocked"]:
    """The unique index on `(country, normalised VAT number)`, built when it is missing and
    nothing collides. `blocked` while duplicates remain: the boot reports it and the index
    stays missing, which `OrganizationService` makes up for by refusing a duplicate itself.
    On a session of one space, as an owner that may create indexes. Commits."""
    present = session.execute(
        text("SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() AND indexname = :name"),
        {"name": VAT_UNIQUE_INDEX},
    ).first()
    if present is not None:
        return "present"
    if duplicate_vat_groups(session):
        return "blocked"
    table = cast(Table, Organization.__table__)
    index = next(i for i in table.indexes if i.name == VAT_UNIQUE_INDEX)
    index.create(bind=session.connection())
    session.commit()
    return "created"
