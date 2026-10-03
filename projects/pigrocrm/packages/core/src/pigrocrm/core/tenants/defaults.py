"""What a space contains before anyone types (spec 2026-09-12 §6.5).

The three seeds already exist and are idempotent on their own key (`code` for stages
and categories, `lower(nome)` for templates), and since REB-615 the fourth is the
space's one azienda, the default row of `emitter_profile`, written with the space's
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
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import PipelineService
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
    aziende: int = 0

    @property
    def seeded(self) -> bool:
        return bool(self.stages or self.templates or self.categories or self.aziende)


def _is_empty(session: Session, model: type[PipelineStage | Template | CostCategory]) -> bool:
    return (session.scalar(select(func.count()).select_from(model)) or 0) == 0


def ensure_defaults(session: Session, *, nome: str | None = None) -> DefaultsReport:
    """On a session of one space (never of the registry). Commits, through the seeds.

    `nome` is what the azienda row is called when there is none yet: the space's own
    name at provisioning, its slug from `ensure-space-defaults`. A space that already
    has its row keeps it untouched, whatever `nome` says.
    """
    actor = Actor.system()
    stages = templates = categories = aziende = 0
    if _is_empty(session, PipelineStage):
        # `PipelineService.seed_defaults` answers the whole list, not only what it
        # created; on an empty table the two are the same.
        stages = len(PipelineService(session).seed_defaults(actor))
    if _is_empty(session, Template):
        templates = len(TemplateService(session).seed_defaults(actor))
    if _is_empty(session, CostCategory):
        categories = len(CostCategoryService(session).seed_defaults(actor))
    if nome and AziendaRepository(session).count() == 0:
        AziendaService(session).upsert_default(AziendaUpsert(ragione_sociale=nome), actor)
        aziende = 1
    return DefaultsReport(
        stages=stages, templates=templates, categories=categories, aziende=aziende
    )
