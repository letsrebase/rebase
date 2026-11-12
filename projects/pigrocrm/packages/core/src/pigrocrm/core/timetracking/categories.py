from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm.core.timetracking.schemas import (
    CostCategoryCreate,
    CostCategoryRead,
    CostCategoryUpdate,
)

ENTITY = "cost_category"

# (code, nome, posizione). `code` is the stable identity a rename cannot touch; the
# five names come straight from spec §4.2.
SEED_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("external_consulting", "Consulenza esterna", 0),
    ("software_licenses", "Software e licenze", 1),
    ("travel", "Viaggi e trasferte", 2),
    ("materials", "Materiali", 3),
    ("other", "Altro", 4),
)


def _conflicting_name(name: str) -> Conflict:
    return Conflict(ENTITY, "esiste già una categoria con questo nome", name=name)


class CostCategoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, category_id: UUID) -> CostCategory | None:
        return self.session.get(CostCategory, category_id)

    def get_by_code(self, code: str) -> CostCategory | None:
        return self.session.execute(
            select(CostCategory).where(CostCategory.code == code)
        ).scalar_one_or_none()

    def get_by_name(self, name: str) -> CostCategory | None:
        """Case-insensitive, matching `uq_cost_categories_nome`'s functional index.
        A pre-check on the raw column would pass for `"trasferte"` against a stored
        `"Trasferte"` and then be refused by the database with a raw
        `IntegrityError`."""
        return self.session.execute(
            select(CostCategory).where(func.lower(CostCategory.name) == name.strip().lower())
        ).scalar_one_or_none()

    def add(self, category: CostCategory) -> CostCategory:
        self.session.add(category)
        self.session.flush()
        return category

    def count_purchases(self, category_id: UUID) -> int:
        return int(
            self.session.execute(
                select(func.count())
                .select_from(Purchase)
                .where(Purchase.category_id == category_id, Purchase.deleted_at.is_(None))
            ).scalar_one()
        )

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, *, include_archived: bool = False) -> list[CostCategory]:
        stmt = select(CostCategory)
        if not include_archived:
            stmt = stmt.where(CostCategory.archived.is_(False))
        return list(
            self.session.execute(stmt.order_by(CostCategory.position, CostCategory.name)).scalars()
        )


class CostCategoryService:
    """Configuration, so every write is `admin` and every write records an activity.

    The three write methods are deliberately named `create_cost_category`,
    `update_cost_category` and `archive_cost_category` rather than the shorter
    `create`/`update`/`archive` used elsewhere: spec §11 fixes the MCP exclusion list
    to exactly ten literal method names, three of which are these, and the
    architecture test in Task 4A-13 matches on the name. A shorter name here would
    make the ban unenforceable by exactly the mechanism §11 exists to provide.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CostCategoryRepository(session)
        self.activities = ActivityService(session)

    def create_cost_category(self, data: CostCategoryCreate, actor: Actor) -> CostCategoryRead:
        actor.require_unscoped_admin("create_cost_category")
        name = data.name.strip()
        if not name:
            raise ValidationFailed(ENTITY, "name", "nome vuoto", expected="un nome non vuoto")
        if self.repo.get_by_name(name):
            raise _conflicting_name(name)

        category = CostCategory(
            name=name,
            position=data.position,
            code=None,
            archived=False,
            deductibility_kind=data.deductibility_kind,
        )
        # The pre-check above cannot cover a race between two concurrent requests: there the
        # functional unique index is the only authority, and the savepoint keeps its failure
        # from touching the rest of the caller's session.
        with savepoint(self.session, lambda exc: _conflicting_name(name)):
            self.repo.add(category)
        self.activities.record(ENTITY, category.id, "created", actor, {"name": name})
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def update_cost_category(
        self, category_id: UUID, data: CostCategoryUpdate, actor: Actor
    ) -> CostCategoryRead:
        actor.require_unscoped_admin("update_cost_category")
        category = self._require(category_id)
        changes = supplied_changes(data)
        reject_cleared_columns(ENTITY, CostCategory, changes)
        if "name" in changes:
            name = changes["name"].strip()
            if not name:
                raise ValidationFailed(ENTITY, "name", "nome vuoto", expected="un nome non vuoto")
            existing = self.repo.get_by_name(name)
            if existing is not None and existing.id != category.id:
                raise _conflicting_name(name)
            changes["name"] = name
        # The savepoint holds the write that can lose the race on the unique name, so only
        # it is rolled back and the caller's session stays usable.
        shown = str(changes.get("name", category.name))
        with savepoint(self.session, lambda exc: _conflicting_name(shown)):
            for key, value in changes.items():
                setattr(category, key, value)
        self.activities.record(ENTITY, category.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def archive_cost_category(self, category_id: UUID, actor: Actor) -> CostCategoryRead:
        """Archive, never delete. A deleted category with costs still attached leaves
        orphan rows nobody can see -- the first of slice 1 §5.6's three rules. The
        stored `category_id` on every existing cost keeps resolving, so a report from
        last year still names its categories."""
        actor.require_unscoped_admin("archive_cost_category")
        category = self._require(category_id)
        category.archived = True
        self.activities.record(
            ENTITY,
            category.id,
            "archived",
            actor,
            {"purchases": self.repo.count_purchases(category.id)},
        )
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def unarchive_cost_category(self, category_id: UUID, actor: Actor) -> CostCategoryRead:
        actor.require_unscoped_admin("unarchive_cost_category")
        category = self._require(category_id)
        category.archived = False
        self.activities.record(ENTITY, category.id, "unarchived", actor)
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def seed_defaults(self, actor: Actor) -> list[CostCategoryRead]:
        """Idempotent, deduplicating on `code` and never on `name`.

        Residual R11 records the bug this avoids: `PipelineService.seed_defaults`
        deduplicated on `name`, the one field a user is free to change, so a renamed
        seed row produced a duplicate on the next seed. Returns only what it actually
        created, so a caller can tell "seeded" from "already there".
        """
        actor.require_unscoped_admin("seed_cost_categories")
        created: list[CostCategoryRead] = []
        for code, name, position in SEED_CATEGORIES:
            if self.repo.get_by_code(code) is not None:
                continue
            category = self.repo.add(
                CostCategory(name=name, position=position, code=code, archived=False)
            )
            self.activities.record(
                ENTITY, category.id, "created", actor, {"name": name, "code": code}
            )
            created.append(CostCategoryRead.model_validate(category))
        self.session.commit()
        return created

    def require_active(self, category_id: UUID) -> CostCategory:
        """What `PurchaseService` calls on create and update. `ValidationFailed`, not
        `NotFound`, for an archived category: the row exists and the caller can see it
        in the archived list -- what is wrong is choosing it for a new cost, which is a
        validation problem naming the field."""
        category = self.repo.get(category_id)
        if category is None:
            raise NotFound(ENTITY, category_id)
        if category.archived:
            raise ValidationFailed(
                ENTITY,
                "category_id",
                "la categoria è archiviata",
                expected="una categoria attiva",
            )
        return category

    def _require(self, category_id: UUID) -> CostCategory:
        category = self.repo.get(category_id)
        if category is None:
            raise NotFound(ENTITY, category_id)
        return category

    def list_cost_categories(self, *, include_archived: bool = False) -> list[CostCategoryRead]:
        return [
            CostCategoryRead.model_validate(c)
            for c in self.repo.list(include_archived=include_archived)
        ]
