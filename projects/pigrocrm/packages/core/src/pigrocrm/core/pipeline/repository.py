from uuid import UUID

from sqlalchemy import column, func, select, table
from sqlalchemy.orm import Session

from pigrocrm.core.pipeline.models import PipelineStage

_DEALS = table("deals", column("pipeline_stage_id"))


class PipelineRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, stage_id: UUID) -> PipelineStage | None:
        return self.session.get(PipelineStage, stage_id)

    def get_by_code(self, code: str) -> PipelineStage | None:
        stmt = select(PipelineStage).where(PipelineStage.code == code)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_tipo(self, tipo: str) -> list[PipelineStage]:
        """Every stage of a kind, ordered by `posizione`.

        Returns a **list** and not an `Optional`, deliberately: residuo **R14** records
        that nothing forbids two `tipo='won'` stages, and "there are two" is an answer the
        automation runner has to be able to see so it can decline instead of picking one.
        An `Optional` signature would force this method to choose, which is exactly the
        decision it must not make.

        Defined above `list` because its return annotation is a bare `list[...]`: after
        `def list` rebinds that name in the class namespace, evaluating this annotation
        would resolve `list` to the method and raise `TypeError` at import time on 3.13.
        """
        return list(
            self.session.execute(
                select(PipelineStage)
                .where(PipelineStage.tipo == tipo)
                .order_by(PipelineStage.posizione, PipelineStage.id)
            ).scalars()
        )

    def list(self) -> list[PipelineStage]:
        stmt = select(PipelineStage).order_by(PipelineStage.posizione, PipelineStage.nome)
        return list(self.session.execute(stmt).scalars())

    def add(self, stage: PipelineStage) -> PipelineStage:
        self.session.add(stage)
        self.session.flush()
        return stage

    def delete(self, stage: PipelineStage) -> None:
        self.session.delete(stage)
        self.session.flush()

    def count_deals_in_stage(self, stage_id: UUID) -> int:
        """Every deal in the stage, archived or not: `pipeline_stage_id` is `NOT NULL` with
        no `ON DELETE` rule, so an archived deal's row references the stage as much as an
        active one's. `deals` is a bare table and not the model: `deals` imports this
        package's neighbours, which import this one."""
        stmt = (
            select(func.count()).select_from(_DEALS).where(_DEALS.c.pipeline_stage_id == stage_id)
        )
        return self.session.execute(stmt).scalar_one()
