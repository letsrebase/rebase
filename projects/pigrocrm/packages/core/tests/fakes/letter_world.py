"""A space with the letters' world in it: a legal entity, the pipeline, the seeded texts, a client
with a deal, and freelancers and engagements to write letters for (REB-1018, REB-1019).

Moved out of `test_letter_service.py` so the signing tests build the same world.
"""

import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from fakes.customer_fixtures import make_customer
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.letter_templates import seed_collaborator_templates
from pigrocrm.core.collaborators.schemas import EngagementCreate, EngagementRead
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.deals.schemas import DealCreate, DealValueLineIn
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.templates.models import Template

needs_binaries = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("typst") is None,
    reason="pandoc e typst vivono nell'immagine dell'API",
)
ADMIN = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]
COLLABORATOR = Actor(id=None, type="user", role="collaborator")  # type: ignore[arg-type]
SYSTEM = Actor(id=None, type="system", role="admin")  # type: ignore[arg-type]
CLIENT_PRICE = "7331"
LEGAL_ENTITY_FIELDS: dict[str, str] = {
    "legal_name": "rebase S.r.l.",
    "vat_number": "01234567890",
    "tax_code": "01234567890",
    "address": "Via Roma 1",
    "postal_code": "20100",
    "city": "Milano",
    "province": "MI",
    "certified_email": "rebase@pec.it",
    "sdi_code": "ABC1234",
    "legal_representative": "Mario Rossi",
}
MONDAY = dt.date(2026, 11, 2)


class World:
    def __init__(self, session: Session, tmp_path: Path) -> None:
        self.session = session
        self.storage = LocalFileStorage(tmp_path)
        LegalEntityService(session).upsert_default(LegalEntityUpsert(**LEGAL_ENTITY_FIELDS), ADMIN)
        PipelineService(session).seed_defaults(ADMIN)
        seed_collaborator_templates(session, SYSTEM)
        self.letters = LetterService(session, self.storage)
        self.engagements = EngagementService(session)
        self.client = make_customer(
            session,
            legal_name="Acme S.r.l.",
            vat_number="09876543210",
            address="Via Milano 3",
            postal_code="10100",
            city="Torino",
            province="TO",
        )
        self.deal_id = (
            DealService(session)
            .create(
                DealCreate(
                    name="Acme backend",
                    customer_id=self.client.id,
                    value_lines=[
                        DealValueLineIn.model_validate(
                            {
                                "unit": "day",
                                "delivery_period": "one_off",
                                "unit_price": CLIENT_PRICE,
                                "quantity": "10",
                                "start": "2026-11-02",
                            }
                        )
                    ],
                ),
                ADMIN,
            )
            .id
        )

    def freelancer(self, **overrides: object) -> Organization:
        values: dict[str, object] = {
            "legal_name": "Ada Lovelace",
            "roles": ["freelancer"],
            "hub_freelancer_id": uuid4(),
            "tax_code": "LVLDAA15B10Z114X",
            "vat_number": f"{uuid4().int % 10**11:011d}",
            "address": "Via dei Calcoli 2",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "email": "ada@example.com",
            "supplier_kind": "professional",
        }
        values.update(overrides)
        return make_customer(self.session, **values)

    def engagement(self, who: Organization, **overrides: object) -> EngagementRead:
        values: dict[str, object] = {
            "freelancer_id": who.id,
            "deal_id": self.deal_id,
            "role": "Backend developer",
            "starts_on": MONDAY,
            "ends_on": MONDAY + dt.timedelta(days=27),
            "days_per_week": Decimal("3"),
            "fee": Decimal("450.00"),
            "fee_unit": "day",
            "payment_rule": "fixed_term",
            "payment_days": 30,
            "payment_end_of_month": True,
        }
        values.update(overrides)
        return self.engagements.create(EngagementCreate(**values), ADMIN)  # type: ignore[arg-type]

    def final_template(self, kind: str, letter_type: str) -> Template:
        found = self.session.scalars(
            select(Template).where(Template.type == kind, Template.letter_type == letter_type)
        ).one()
        return found
