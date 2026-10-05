from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.models import Cost, CostCategory
from pigrocrm.core.timetracking.schemas import (
    CostCategoryCreate,
    CostCategoryRead,
    CostCategoryUpdate,
)

ENTITY = "cost_category"

# (code, nome, posizione). `code` is the stable identity a rename cannot touch; the
# five names come straight from spec §4.2.
SEED_CATEGORIES: tuple[tuple[str, str, int], ...] = (
    ("consulenza_esterna", "Consulenza esterna", 0),
    ("software_licenze", "Software e licenze", 1),
    ("viaggi_trasferte", "Viaggi e trasferte", 2),
    ("materiali", "Materiali", 3),
    ("altro", "Altro", 4),
)


def _conflicting_nome(nome: str) -> Conflict:
    return Conflict(ENTITY, "esiste già una categoria con questo nome", nome=nome)


class CostCategoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, category_id: UUID) -> CostCategory | None:
        return self.session.get(CostCategory, category_id)

    def get_by_code(self, code: str) -> CostCategory | None:
        return self.session.execute(
            select(CostCategory).where(CostCategory.code == code)
        ).scalar_one_or_none()

    def get_by_nome(self, nome: str) -> CostCategory | None:
        """Case-insensitive, matching `uq_cost_categories_nome`'s functional index.
        A pre-check on the raw column would pass for `"trasferte"` against a stored
        `"Trasferte"` and then be refused by the database with a raw
        `IntegrityError`."""
        return self.session.execute(
            select(CostCategory).where(func.lower(CostCategory.nome) == nome.strip().lower())
        ).scalar_one_or_none()

    def add(self, category: CostCategory) -> CostCategory:
        self.session.add(category)
        self.session.flush()
        return category

    def count_costs(self, category_id: UUID) -> int:
        return int(
            self.session.execute(
                select(func.count())
                .select_from(Cost)
                .where(Cost.category_id == category_id, Cost.deleted_at.is_(None))
            ).scalar_one()
        )

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, *, include_archived: bool = False) -> list[CostCategory]:
        stmt = select(CostCategory)
        if not include_archived:
            stmt = stmt.where(CostCategory.archiviata.is_(False))
        return list(
            self.session.execute(stmt.order_by(CostCategory.posizione, CostCategory.nome)).scalars()
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
        nome = data.nome.strip()
        if not nome:
            raise ValidationFailed(ENTITY, "nome", "nome vuoto", expected="un nome non vuoto")
        if self.repo.get_by_nome(nome):
            raise _conflicting_nome(nome)

        category = CostCategory(nome=nome, posizione=data.posizione, code=None, archiviata=False)
        try:
            self.repo.add(category)
            self.activities.record(ENTITY, category.id, "created", actor, {"nome": nome})
            self.session.commit()
        except IntegrityError as exc:
            # The pre-check above cannot cover a race between two concurrent requests:
            # there the functional unique index is the only authority. The rollback is
            # mandatory -- without it the caller's session is unusable.
            self.session.rollback()
            raise _conflicting_nome(nome) from exc
        return CostCategoryRead.model_validate(category)

    def update_cost_category(
        self, category_id: UUID, data: CostCategoryUpdate, actor: Actor
    ) -> CostCategoryRead:
        actor.require_unscoped_admin("update_cost_category")
        category = self._require(category_id)
        changes = supplied_changes(data)
        reject_cleared_columns(ENTITY, CostCategory, changes)
        if "nome" in changes:
            nome = changes["nome"].strip()
            if not nome:
                raise ValidationFailed(ENTITY, "nome", "nome vuoto", expected="un nome non vuoto")
            existing = self.repo.get_by_nome(nome)
            if existing is not None and existing.id != category.id:
                raise _conflicting_nome(nome)
            changes["nome"] = nome
        for key, value in changes.items():
            setattr(category, key, value)

        try:
            self.activities.record(
                ENTITY, category.id, "updated", actor, {"changed": sorted(changes)}
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise _conflicting_nome(str(changes.get("nome", category.nome))) from exc
        return CostCategoryRead.model_validate(category)

    def archive_cost_category(self, category_id: UUID, actor: Actor) -> CostCategoryRead:
        """Archive, never delete. A deleted category with costs still attached leaves
        orphan rows nobody can see -- the first of slice 1 §5.6's three rules. The
        stored `category_id` on every existing cost keeps resolving, so a report from
        last year still names its categories."""
        actor.require_unscoped_admin("archive_cost_category")
        category = self._require(category_id)
        category.archiviata = True
        self.activities.record(
            ENTITY, category.id, "archived", actor, {"costi": self.repo.count_costs(category.id)}
        )
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def unarchive_cost_category(self, category_id: UUID, actor: Actor) -> CostCategoryRead:
        actor.require_unscoped_admin("unarchive_cost_category")
        category = self._require(category_id)
        category.archiviata = False
        self.activities.record(ENTITY, category.id, "unarchived", actor)
        self.session.commit()
        return CostCategoryRead.model_validate(category)

    def seed_defaults(self, actor: Actor) -> list[CostCategoryRead]:
        """Idempotent, deduplicating on `code` and never on `nome`.

        Residual R11 records the bug this avoids: `PipelineService.seed_defaults`
        deduplicated on `nome`, the one field a user is free to change, so a renamed
        seed row produced a duplicate on the next seed. Returns only what it actually
        created, so a caller can tell "seeded" from "already there".
        """
        actor.require_unscoped_admin("seed_cost_categories")
        created: list[CostCategoryRead] = []
        for code, nome, posizione in SEED_CATEGORIES:
            if self.repo.get_by_code(code) is not None:
                continue
            category = self.repo.add(
                CostCategory(nome=nome, posizione=posizione, code=code, archiviata=False)
            )
            self.activities.record(
                ENTITY, category.id, "created", actor, {"nome": nome, "code": code}
            )
            created.append(CostCategoryRead.model_validate(category))
        self.session.commit()
        return created

    def require_active(self, category_id: UUID) -> CostCategory:
        """What `CostService` calls on create and update. `ValidationFailed`, not
        `NotFound`, for an archived category: the row exists and the caller can see it
        in the archived list -- what is wrong is choosing it for a new cost, which is a
        validation problem naming the field."""
        category = self.repo.get(category_id)
        if category is None:
            raise NotFound(ENTITY, category_id)
        if category.archiviata:
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
