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
