"""A configurable taxonomy, on the pipeline-stage model: `code` is the stable identity
of a seed, `name` is the label a user may rename, and `archived` replaces deletion."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.timetracking.categories import SEED_CATEGORIES, CostCategoryService
from pigrocrm.core.timetracking.schemas import CostCategoryCreate, CostCategoryUpdate

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaborator")


def test_seed_creates_the_five_named_categories(db_session: Session) -> None:
    created = CostCategoryService(db_session).seed_defaults(ADMIN)
    assert [c.name for c in created] == [
        "Consulenza esterna",
        "Software e licenze",
        "Viaggi e trasferte",
        "Materiali",
        "Altro",
    ]
    assert [c.code for c in created] == [code for code, _, _ in SEED_CATEGORIES]
    assert [c.position for c in created] == [0, 1, 2, 3, 4]


def test_seed_deduplicates_on_code_not_on_name(db_session: Session) -> None:
    """The exact bug residual R11 records against `pipeline_stages`: `seed_defaults`
    deduplicated on `name`, the one field the user is free to change, so renaming a
    seeded row made the next seed create a duplicate."""
    service = CostCategoryService(db_session)
    first = service.seed_defaults(ADMIN)
    renamed = first[0]
    service.update_cost_category(renamed.id, CostCategoryUpdate(name="Fornitori terzi"), ADMIN)

    again = service.seed_defaults(ADMIN)
    assert again == []
    assert len(service.list_cost_categories(include_archived=True)) == len(SEED_CATEGORIES)


def test_a_duplicate_name_is_a_conflict_case_insensitively(db_session: Session) -> None:
    service = CostCategoryService(db_session)
    service.create_cost_category(CostCategoryCreate(name="Trasferte"), ADMIN)
    with pytest.raises(Conflict):
        service.create_cost_category(CostCategoryCreate(name="  trasferte "), ADMIN)


def test_archiving_keeps_existing_costs_readable(db_session: Session, seeded_deal_id: UUID) -> None:
    """§5.6's first rule, applied: a deleted category with costs still attached would
    produce orphan rows nobody can see. Archiving hides it from the picker and leaves
    the data readable."""
    service = CostCategoryService(db_session)
    category = service.create_cost_category(CostCategoryCreate(name="Hosting"), ADMIN)
    db_session.add(
        Purchase(
            deal_id=seeded_deal_id,
            category_id=category.id,
            date=date(2026, 3, 1),
            amount=Decimal("42.00"),
            description="VPS",
        )
    )
    db_session.flush()

    archived = service.archive_cost_category(category.id, ADMIN)
    assert archived.archived is True
    assert category.id not in {c.id for c in service.list_cost_categories()}
    assert category.id in {c.id for c in service.list_cost_categories(include_archived=True)}
    with pytest.raises(ValidationFailed) as excinfo:
        service.require_active(category.id)
    assert excinfo.value.details["field"] == "category_id"


def test_unarchive_brings_it_back(db_session: Session) -> None:
    service = CostCategoryService(db_session)
    category = service.create_cost_category(CostCategoryCreate(name="Hosting"), ADMIN)
    service.archive_cost_category(category.id, ADMIN)
    assert service.unarchive_cost_category(category.id, ADMIN).archived is False


def test_every_write_records_an_activity(db_session: Session) -> None:
    """R5 closes here for this table: the timeline is what reconstructs when the
    taxonomy changed, and that is not hygiene -- it is what lets §5 skip historicising
    anything."""
    service = CostCategoryService(db_session)
    category = service.create_cost_category(CostCategoryCreate(name="Hosting"), ADMIN)
    service.update_cost_category(category.id, CostCategoryUpdate(name="Cloud"), ADMIN)
    service.archive_cost_category(category.id, ADMIN)
    kinds = [
        entry.kind for entry in ActivityService(db_session).timeline("cost_category", category.id)
    ]
    assert kinds == ["archived", "updated", "created"]  # newest first


def test_every_write_is_admin_only(db_session: Session) -> None:
    service = CostCategoryService(db_session)
    for call in (
        lambda: service.create_cost_category(CostCategoryCreate(name="X"), COLLABORATOR),
        lambda: service.archive_cost_category(uuid4(), COLLABORATOR),
        lambda: service.seed_defaults(COLLABORATOR),
    ):
        with pytest.raises(PermissionDenied):
            call()


def test_an_unknown_id_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        CostCategoryService(db_session).archive_cost_category(uuid4(), ADMIN)
