"""The accountant's declared tax figures (REB-1006, taxes design § 2 and § 5), typed beside the
estimate: an administrator types them, the year then reads as declared, a newer estimate leaves
them alone, and nobody else, agents without `full_access` included, may type one."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.models import DeclaredTaxFigure, FiscalProfile
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet
from pigrocrm.core.legal_entities.models import LegalEntity

ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
AGENT_COLLABORATOR_FULL = Actor(id=uuid4(), type="mcp", role="collaborator", full_access=True)
YEAR = 2025


def _entity(session: Session, tag: str = "x") -> UUID:
    entity = LegalEntity(name=f"declared {tag}", legal_name=f"Declared {tag}", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=Decimal("78.00"),
            substitute_tax_rate=Decimal("5.00"),
            social_security_rate=Decimal("26.07"),
        )
    )
    session.flush()
    return entity.id


def _figure(**overrides: object) -> DeclaredTaxFigureSet:
    values: dict[str, object] = {
        "tax": "substitute_tax",
        "year": YEAR,
        "tax_due": Decimal("1200.00"),
        "balance": Decimal("300.00"),
        "advances": Decimal("900.00"),
    }
    values.update(overrides)
    return DeclaredTaxFigureSet(**values)  # type: ignore[arg-type]


def test_an_admin_types_a_declared_balance_and_the_year_reads_as_declared(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    before = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity)
    assert (before.reading, before.declared) == ("estimate", [])

    read = DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        _figure(tax_due=None, advances=None, balance=Decimal("300.00")),
        ADMIN,
        legal_entity_id=entity,
    )
    assert (read.reading, read.tax, read.year, read.source) == (
        "declared",
        "substitute_tax",
        YEAR,
        "typed",
    )
    assert (read.tax_due, read.balance, read.advances) == (None, Decimal("300.00"), None)

    after = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity)
    assert after.reading == "declared"
    assert [(f.tax, f.balance) for f in after.declared] == [("substitute_tax", Decimal("300.00"))]
    # The estimate is still an estimate, shown beside the figure, and unchanged by it.
    assert after.estimate is True
    assert after.due_total == before.due_total


def test_a_newer_estimate_leaves_the_declared_figure_alone(db_session: Session) -> None:
    entity = _entity(db_session)
    DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        _figure(), ADMIN, legal_entity_id=entity
    )
    db_session.query(FiscalProfile).update({"profitability_coefficient": Decimal("40.00")})
    db_session.flush()
    # A fresh estimate on other parameters, and one computed on a «what if» revenue.
    analytics = AnalyticsService(db_session)
    newer = analytics.get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity)
    assert newer.profitability_coefficient == Decimal("40.00")
    assert newer.reading == "declared"
    assert [(f.tax_due, f.balance, f.advances) for f in newer.declared] == [
        (Decimal("1200.00"), Decimal("300.00"), Decimal("900.00"))
    ]
    what_if = analytics.get_fiscal_estimate(
        YEAR, ADMIN, revenue=Decimal("50000"), legal_entity_id=entity
    )
    assert (what_if.reading, what_if.declared) == ("estimate", [])
    assert db_session.query(DeclaredTaxFigure).count() == 1


def test_another_year_and_another_tax_are_not_declared(db_session: Session) -> None:
    entity = _entity(db_session)
    service = DeclaredTaxFigureService(db_session)
    service.set_declared_tax_figure(_figure(), ADMIN, legal_entity_id=entity)
    service.set_declared_tax_figure(_figure(tax="inps"), ADMIN, legal_entity_id=entity)
    analytics = AnalyticsService(db_session)
    assert analytics.get_fiscal_estimate(YEAR + 1, ADMIN, legal_entity_id=entity).declared == []
    same = analytics.get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity)
    assert [f.tax for f in same.declared] == ["inps", "substitute_tax"]


def test_typing_again_replaces_the_three_figures_together(db_session: Session) -> None:
    entity = _entity(db_session)
    service = DeclaredTaxFigureService(db_session)
    service.set_declared_tax_figure(_figure(), ADMIN, legal_entity_id=entity)
    second = service.set_declared_tax_figure(
        _figure(tax_due=Decimal("1500.00"), balance=Decimal("-50.00"), advances=None),
        ADMIN,
        legal_entity_id=entity,
    )
    assert (second.tax_due, second.balance, second.advances) == (
        Decimal("1500.00"),
        Decimal("-50.00"),
        None,
    )
    assert db_session.query(DeclaredTaxFigure).count() == 1


@pytest.mark.parametrize(
    "actor", [COLLABORATOR, READONLY, AGENT_COLLABORATOR_FULL], ids=lambda a: f"{a.type}-{a.role}"
)
def test_only_an_admin_types_a_figure(db_session: Session, actor: Actor) -> None:
    entity = _entity(db_session)
    with pytest.raises(PermissionDenied):
        DeclaredTaxFigureService(db_session).set_declared_tax_figure(
            _figure(), actor, legal_entity_id=entity
        )
    assert db_session.query(DeclaredTaxFigure).count() == 0


def test_a_token_without_full_access_is_refused_and_one_with_it_passes(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    service = DeclaredTaxFigureService(db_session)
    with pytest.raises(AgentForbidden):
        service.set_declared_tax_figure(_figure(), AGENT_ADMIN, legal_entity_id=entity)
    assert db_session.query(DeclaredTaxFigure).count() == 0
    read = service.set_declared_tax_figure(_figure(), AGENT_ADMIN_FULL, legal_entity_id=entity)
    assert read.balance == Decimal("300.00")


def test_a_space_with_two_legal_entities_asks_which(db_session: Session) -> None:
    first = _entity(db_session, "a")
    _entity(db_session, "b")
    service = DeclaredTaxFigureService(db_session)
    with pytest.raises(ValidationFailed):
        service.set_declared_tax_figure(_figure(), ADMIN)
    assert service.set_declared_tax_figure(_figure(), ADMIN, legal_entity_id=first).year == YEAR


def test_the_figure_needs_a_known_tax_one_amount_and_no_negative_tax(
    db_session: Session,
) -> None:
    for bad in (
        {"tax": "vat"},
        {"tax_due": None, "balance": None, "advances": None},
        {"tax_due": Decimal("-1")},
        {"advances": Decimal("-1")},
        {"year": 1999},
    ):
        with pytest.raises(ValidationError):
            _figure(**bad)


def test_the_table_refuses_a_row_without_any_figure_and_a_negative_tax(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    for values in ({}, {"tax_due": Decimal("-1")}):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(
                DeclaredTaxFigure(
                    legal_entity_id=entity,
                    tax="irpef",
                    tax_year=YEAR,
                    **{"balance": None, **values},  # type: ignore[arg-type]
                )
            )
            db_session.flush()
