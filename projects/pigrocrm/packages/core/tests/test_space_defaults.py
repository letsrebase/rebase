"""`ensure_defaults`: what a space contains before anyone types, and only when the
table is empty (spec 2026-09-12 §6.5)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import DEFAULT_STAGES
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.schemas import TemplateCreate
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.tenants import ensure_defaults
from pigrocrm.core.timetracking.categories import SEED_CATEGORIES
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=None, type="system", role="admin")


def _count(session: Session, model: type[PipelineStage | Template | CostCategory]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_an_empty_space_gets_stages_templates_and_categories(db_session: Session) -> None:
    report = ensure_defaults(db_session)
    assert report.seeded is True
    assert _count(db_session, PipelineStage) == len(DEFAULT_STAGES)
    assert _count(db_session, CostCategory) == len(SEED_CATEGORIES)
    assert _count(db_session, Template) == 3
    assert (report.stages, report.categories, report.templates) == (
        len(DEFAULT_STAGES),
        len(SEED_CATEGORIES),
        3,
    )


def test_a_table_that_is_not_empty_is_left_alone(db_session: Session) -> None:
    TemplateService(db_session).create(
        TemplateCreate(nome="Il mio contratto", tipo="contratto", corpo_markdown="x"), ADMIN
    )
    report = ensure_defaults(db_session)
    # Templates: the one row the person made, and nothing added beside it.
    assert report.templates == 0
    assert [t.nome for t in db_session.scalars(select(Template)).all()] == ["Il mio contratto"]
    # The other two families were empty and are seeded regardless.
    assert report.stages == len(DEFAULT_STAGES)
    assert report.categories == len(SEED_CATEGORIES)


def test_a_second_call_seeds_nothing(db_session: Session) -> None:
    ensure_defaults(db_session)
    again = ensure_defaults(db_session)
    assert again.seeded is False
    assert (again.stages, again.templates, again.categories) == (0, 0, 0)


# -- the azienda (REB-615) ----------------------------------------------------------


def _bare(session: Session) -> None:
    from sqlalchemy import delete

    from pigrocrm.core.emitter.models import Azienda

    session.execute(delete(Azienda))
    session.flush()


def test_a_space_with_no_azienda_gets_one_named_after_the_space(db_session: Session) -> None:
    from pigrocrm.core.emitter.repository import AziendaRepository

    _bare(db_session)
    report = ensure_defaults(db_session, nome="Studio Ada")
    assert report.aziende == 1
    azienda = AziendaRepository(db_session).default()
    assert azienda is not None
    assert (azienda.ragione_sociale, azienda.nome, azienda.predefinita) == (
        "Studio Ada",
        "Studio Ada",
        True,
    )
    assert azienda.partita_iva is None


def test_a_space_that_has_its_azienda_keeps_it_whatever_the_name_says(
    db_session: Session,
) -> None:
    from pigrocrm.core.emitter.repository import AziendaRepository

    report = ensure_defaults(db_session, nome="Un altro nome")
    assert report.aziende == 0
    azienda = AziendaRepository(db_session).default()
    assert azienda is not None
    assert azienda.ragione_sociale == "Studio di prova"


def test_without_a_name_no_azienda_is_invented(db_session: Session) -> None:
    from pigrocrm.core.emitter.repository import AziendaRepository

    _bare(db_session)
    report = ensure_defaults(db_session)
    assert report.aziende == 0
    assert AziendaRepository(db_session).default() is None
