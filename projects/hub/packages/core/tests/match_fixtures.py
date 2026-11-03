"""What the match tests share (REB-387, REB-892, HTEST-10): the constants, the `clean` fixture and
the builders of a card, a request, tax data, a framework agreement and a match body."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from admins import an_admin
from fakes_contracts import FakeRenderer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.contract_schemas import (
    CustomerDate,
    FiscalData,
    LetterFields,
    MatchCreate,
)
from rebase_core.contracts.fields import Value
from rebase_core.contracts.render import Renderer
from rebase_core.contracts.render import text_version as current_text_version
from rebase_core.fiscal import FiscalService
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Match
from rebase_core.regimes import Regime

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


TODAY = date(2026, 9, 23)


# The framework agreement's version today, read once from the real text so a future
# version bump does not silently make `_framework`'s default a stale literal.
FRAMEWORK_VERSION = current_text_version("framework-agreement")


# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER: dict[str, Value] = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}


# "signups" is here so `test_matches_api.py`'s own fixture can clear the same tables in
# the same order: the two lists must not drift.
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "notes",
    "freelancers",
    "companies",
    "users",
    "signups",
)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    return an_admin(session, "ivan@rebase.it", "Ivan")


def _card(session: Session) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            last_name="Lovelace",
            email="ada@studio.it",
            daily_rate=Decimal("450"),
            posizione="Backend developer",
            remote="remote",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(session: Session, **change: object) -> UUID:
    """A request whose budget is a number that must appear nowhere in the flow."""
    payload: dict[str, object] = {
        "company_name": "ACME Srl",
        "contact_first_name": "Wile",
        "contact_last_name": "E.",
        "email": "wile@acme.it",
        "phone": "+39 345 1234567",
        "requested_role": "Backend developer",
        "project": "Le API del prodotto, per tre mesi.",
        "period_from": date(2026, 10, 1),
        "duration": "3 mesi",
        "daily_budget": Decimal("777.77"),
        "remote": "hybrid",
        "onsite_days": 2,
        "headcount": 1,
    }
    payload.update(change)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def _fiscal(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    vat_number: str | None = "01234567890",
) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            tax_code="LVLDAA85T50H501Z",
            vat_number=vat_number,
            domicile="Via Roma 1, Milano",
        ),
        admin_id,
    )


def _setup(session: Session) -> tuple[UUID, UUID, UUID]:
    admin_id, freelancer_id, company_id = _admin(session), _card(session), _request(session)
    _fiscal(session, freelancer_id, admin_id)
    return admin_id, freelancer_id, company_id


def _setup_occasional(session: Session) -> tuple[UUID, UUID, UUID]:
    """A freelancer whose tax data were saved without a partita IVA (REB-687)."""
    admin_id, freelancer_id, company_id = _admin(session), _card(session), _request(session)
    _fiscal(session, freelancer_id, admin_id, vat_number=None)
    return admin_id, freelancer_id, company_id


def _body(
    company_id: UUID, *, expected_days: int | None = None, regime: Regime = "partita_iva"
) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        regime=regime,
        customer=CustomerDate(
            customer_legal_name="ACME S.r.l.",
            customer_vat_number="01234567890",
            customer_registered_office="Milano",
        ),
        letter=LetterFields(
            role="Backend developer",
            activities="Le API del prodotto.",
            start_date=date(2026, 10, 1),
            mode="to_work_unit",
            fee=Decimal("450"),
            payment_days=30,
            month_end=True,
        ),
        expected_days=expected_days,
    )


def _service(
    session: Session, renderer: Renderer | None = None, today: date = TODAY
) -> MatchService:
    return MatchService(session, renderer or FakeRenderer(), SIGNER, today=lambda: today)


def _framework(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    *,
    status: str = "signed",
    signed_at: datetime | None = datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    notice_at: datetime | None = None,
    text_version: str = FRAMEWORK_VERSION,
    regime: str = "partita_iva",
) -> ContractDocument:
    """A framework agreement as phase 3 will leave one: only a signature makes these."""
    document = ContractDocument(
        kind="framework",
        regime=regime,
        freelancer_id=freelancer_id,
        text_version=text_version,
        draft_text=False,
        data={},
        pdf=b"%PDF-quadro",
        status=status,
        signed_at=signed_at,
        notice_at=notice_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


def _documents(
    session: Session, freelancer_id: UUID, kind: str | None = None
) -> list[ContractDocument]:
    stmt = select(ContractDocument).where(ContractDocument.freelancer_id == freelancer_id)
    if kind is not None:
        stmt = stmt.where(ContractDocument.kind == kind)
    return list(session.scalars(stmt.order_by(ContractDocument.created_at, ContractDocument.id)))


class _FakeDiag:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _FakeOrig(Exception):
    """Stands in for the DBAPI exception `IntegrityError.orig` carries, with a
    `.diag.constraint_name` of a constraint that is not the match's own primary key."""

    def __init__(self, constraint_name: str) -> None:
        super().__init__("una violazione diversa")
        self.diag = _FakeDiag(constraint_name)


# ---- the admin's «Match» list (REB-413) -------------------------------------------------
def _second_card(session: Session, **change: object) -> UUID:
    payload: dict[str, object] = {
        "nome": "Grace",
        "last_name": "Hopper",
        "email": "grace@studio.it",
        "daily_rate": Decimal("500"),
        "posizione": "Frontend developer",
        "remote": "remote",
    }
    payload.update(change)
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(**payload),  # type: ignore[arg-type]
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


# ---- what a match and its documents are doing (REB-477) ---------------------------------
def _waiting(session: Session, match_id: UUID) -> None:
    """A match sent while its freelancer had no active framework agreement: in signature,
    its letter waiting (what `SigningService.send_match` leaves)."""
    match = session.get(Match, match_id)
    assert match is not None
    match.status = "awaiting_signature"
    session.commit()


def _framework_now(session: Session, freelancer_id: UUID, status: str) -> None:
    (framework,) = _documents(session, freelancer_id, "framework")
    framework.status = status
    if status in ("sent", "signed", "notice_served"):
        framework.sent_at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    if status in ("signed", "notice_served"):
        framework.signed_at = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    if status == "notice_served":
        framework.notice_at = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    session.commit()


# ---- the check before saving, and the proposal (REB-476) ---------------------------------
SAVED_TABLES = ("matches", "contract_documents", "contract_letter_counters", "admin_actions")


def _rows(session: Session) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(row) for row in session.execute(text(f"SELECT * FROM {table} ORDER BY 1"))]
        for table in SAVED_TABLES
    }


CUSTOMER_REST = {"customer_vat_number": "01234567890", "customer_registered_office": "Milano"}


# ---- the letter type (REB-687) -----------------------------------------------------------
REFUSED_WITHOUT_VAT_NUMBER = (
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
    "o salva la partita IVA."
)
