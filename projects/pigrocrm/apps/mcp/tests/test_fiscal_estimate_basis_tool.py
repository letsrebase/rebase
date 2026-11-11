"""`get_fiscal_estimate` over the MCP client answers the figure the service does, on the basis
the regime's law names (REB-1071, taxes design § 2.1): the same revenue, the same lines and the
same `basis` the REST route and the economic overview read, for the same year."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fakes.customer_fixtures import make_customer
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="system", role="admin")


def _server(session: Session, tmp_path: Path) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=True),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=True),  # type: ignore[call-arg]
    )


def _invoice(
    session: Session, entity: LegalEntity, number: int, issued: dt.date, collected: dt.date | None
) -> None:
    session.add(
        Invoice(
            legal_entity_id=entity.id,
            customer_id=make_customer(session).id,
            type="invoice",
            status="issued",
            year=issued.year,
            number=number,
            issue_date=issued,
            due_date=issued + dt.timedelta(days=30),
            taxable_amount=Decimal("1000.00"),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("1000.00"),
            payment_status="unpaid" if collected is None else "paid",
            payment_date=collected,
        )
    )
    session.flush()


async def test_the_tool_answers_the_services_figure_and_its_basis(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = LegalEntity(name="basis", legal_name="Basis", country="IT")
    mcp_session.add(entity)
    mcp_session.flush()
    mcp_session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=Decimal("67.00"),
            substitute_tax_rate=Decimal("5.00"),
            social_security_rate=Decimal("26.07"),
        )
    )
    # Issued in December 2026 and collected in January 2027: 2027's revenue under the cash
    # basis the flat-rate regime is on, though it is 2026's invoice.
    _invoice(mcp_session, entity, 1, dt.date(2026, 12, 10), dt.date(2027, 1, 15))
    _invoice(mcp_session, entity, 2, dt.date(2027, 5, 3), None)
    mcp_session.commit()

    async with Client(_server(mcp_session, tmp_path)) as client:
        answers = {
            year: (
                await client.call_tool(
                    "get_fiscal_estimate", {"year": year, "legal_entity_id": str(entity.id)}
                )
            )
            for year in (2026, 2027)
        }
    for year, collected in ((2026, "0.00"), (2027, "1000.00")):
        result = answers[year]
        assert not result.is_error, result
        body = result.structured_content or json.loads(result.content[0].text)
        service = AnalyticsService(mcp_session).get_fiscal_estimate(
            year, ADMIN, legal_entity_id=entity.id
        )
        assert body["revenue"] == collected
        assert body["basis"] == service.model_dump(mode="json")["basis"]
        assert body["basis"]["basis"] == "cash"
        for field in ("revenue", "taxable_amount", "contributions", "substitute_tax", "due_total"):
            assert body[field] == str(getattr(service, field)), field
    # What the year invoiced and did not collect is said, so a zero is not read as nothing.
    assert [line["code"] for line in answers[2027].structured_content["basis"]["left_out"]] == [
        "not_collected"
    ]


async def test_the_tool_answers_an_srls_ires_and_irap_as_the_service_does(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-1073: the srl's two taxes, each with its lines and rate, are the service's own. A Milan
    srl with 1,000.00 of 2026 revenue and no costs: IRES 24% of 1,000.00 = 240.00; IRAP: the
    art. 11 c. 4-bis deduction (8,000.00) is capped at the base: base and tax are 0.00."""
    entity = LegalEntity(name="srl", legal_name="Srl", country="IT", city="Milano", province="MI")
    mcp_session.add(entity)
    mcp_session.flush()
    mcp_session.add(
        FiscalProfile(legal_entity_id=entity.id, pack_id="it-company", regime_code="RF01")
    )
    _invoice(mcp_session, entity, 1, dt.date(2026, 6, 1), None)
    mcp_session.commit()

    async with Client(_server(mcp_session, tmp_path)) as client:
        result = await client.call_tool(
            "get_fiscal_estimate", {"year": 2026, "legal_entity_id": str(entity.id)}
        )
    assert not result.is_error, result
    body = result.structured_content or json.loads(result.content[0].text)
    service = AnalyticsService(mcp_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=entity.id
    )
    # `_invoice` issues 1,000.00: IRES 240.00; IRAP base 1,000.00 - 1,000.00 (the deduction is
    # capped at the base) = 0.00, so 0.00.
    assert body["company"]["ires"]["amount"] == "240.00"
    assert body["company"]["irap"]["amount"] == "0.00"
    assert body["company"] == service.model_dump(mode="json")["company"]
    assert body["due_total"] == str(service.due_total) == "240.00"
    assert [(line["tax"], line["estimate"]) for line in body["readings"]["lines"]] == [
        ("ires", "240.00"),
        ("irap", "0.00"),
    ]


async def test_an_ordinary_persons_tool_answer_is_the_services_person_estimate(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-1072: over MCP the answer carries the IRPEF (with its surcharges), the INPS, the lines
    each was computed from and the readings, exactly as the service returns them."""
    entity = LegalEntity(
        name="albinea", legal_name="Rossi", country="IT", city="Albinea", province="RE"
    )
    mcp_session.add(entity)
    mcp_session.flush()
    mcp_session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-standard",
            regime_code="RF01",
            inps_category="gestione_separata",
            inps_covered_elsewhere=True,
        )
    )
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(mcp_session).id,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=dt.date(2026, 2, 1),
        due_date=dt.date(2026, 3, 3),
        taxable_amount=Decimal("65789.47"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("65789.47"),
        payment_status="paid",
        payment_date=dt.date(2026, 2, 10),
    )
    mcp_session.add(invoice)
    mcp_session.commit()

    async with Client(_server(mcp_session, tmp_path)) as client:
        result = await client.call_tool(
            "get_fiscal_estimate", {"year": 2026, "legal_entity_id": str(entity.id)}
        )
    assert not result.is_error, result
    body = result.structured_content or json.loads(result.content[0].text)
    service = AnalyticsService(mcp_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=entity.id
    )
    assert body["person"] == service.model_dump(mode="json")["person"]
    assert body["person"]["irpef"]["amount"] == "13700.00"
    assert body["person"]["irpef"]["with_surcharges"] == "15070.00"
    assert body["person"]["inps"]["amount"] == "15789.47"
    assert body["readings"] == service.model_dump(mode="json")["readings"]
    assert body["due_total"] == "30859.47" and body["missing_parameters"] == []
