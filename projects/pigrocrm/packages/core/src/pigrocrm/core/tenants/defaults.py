"""What a space contains before anyone types (spec 2026-09-12 §6.5).

The three seeds already exist and are idempotent on their own key (`code` for stages
and categories, `lower(nome)` for templates), and since REB-615 the fourth is the
space's one legal entity, the default row of `legal_entities`, written with the space's
name and nothing fiscal when the table is empty. What this adds is the one rule they
do not have: **a family is seeded only while its table is empty**. A person who deleted a
default template to make their own must not find it back at the next boot, and a
pipeline that somebody reshaped is theirs. Called from `TenantService.provision` for a
new space and from `pigrocrm ensure-space-defaults` for the spaces that already exist.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.letter_templates import seed_collaborator_templates
from pigrocrm.core.config import get_settings
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.modules.registry import enabled_modules
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.space_settings.service import SpaceSettingsService
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.models import CostCategory


@dataclass(frozen=True)
class DefaultsReport:
    """How many rows each family gained. Zero everywhere means the space was already
    furnished, which is what the CLI prints as «già a posto»."""

    stages: int
    templates: int
    categories: int
    legal_entities: int = 0
    # The texts of the `collaborators` module (framework agreements, engagement letters, the NDA
    # and the company's contract), written only in a space where that module is on.
    collaborator_templates: int = 0

    @property
    def seeded(self) -> bool:
        return bool(
            self.stages
            or self.templates
            or self.categories
            or self.legal_entities
            or self.collaborator_templates
        )


def _is_empty(session: Session, model: type[PipelineStage | Template | CostCategory]) -> bool:
    return (session.scalar(select(func.count()).select_from(model)) or 0) == 0


def ensure_defaults(session: Session, *, name: str | None = None) -> DefaultsReport:
    """On a session of one space (never of the registry). Commits, through the seeds.

    `name` is what the legal entity row is called when there is none yet: the space's own
    name at provisioning, its slug from `ensure-space-defaults`. A space that already
    has its row keeps it untouched, whatever `name` says.
    """
    actor = Actor.system()
    stages = templates = categories = legal_entities = 0
    if _is_empty(session, PipelineStage):
        # `PipelineService.seed_defaults` answers the whole list, not only what it
        # created; on an empty table the two are the same.
        stages = len(PipelineService(session).seed_defaults(actor))
    if _is_empty(session, Template):
        templates = len(TemplateService(session).seed_defaults(actor))
    if _is_empty(session, CostCategory):
        categories = len(CostCategoryService(session).seed_defaults(actor))
    if name and LegalEntityRepository(session).count() == 0:
        LegalEntityService(session).upsert_default(LegalEntityUpsert(legal_name=name), actor)
        legal_entities = 1
    collaborator_templates = 0
    if "collaborators" in enabled_modules(
        SpaceSettingsService(session, get_settings()).effective()
    ):
        collaborator_templates = len(seed_collaborator_templates(session, actor))
    return DefaultsReport(
        stages=stages,
        templates=templates,
        categories=categories,
        legal_entities=legal_entities,
        collaborator_templates=collaborator_templates,
    )
