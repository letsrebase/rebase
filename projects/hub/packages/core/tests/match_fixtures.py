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
    ClienteData,
    FiscalData,
    LetteraFields,
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
QUADRO_VERSION = current_text_version("contratto-quadro")


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
            cognome="Lovelace",
            email="ada@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(session: Session, **change: object) -> UUID:
    """A request whose budget is a number that must appear nowhere in the flow."""
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Le API del prodotto, per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("777.77"),
        "remoto": "ibrido",
        "giorni_presenza": 2,
        "numero_risorse": 1,
    }
    payload.update(change)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def _fiscal(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    partita_iva: str | None = "01234567890",
) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z",
            partita_iva=partita_iva,
            domicilio="Via Roma 1, Milano",
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
    _fiscal(session, freelancer_id, admin_id, partita_iva=None)
    return admin_id, freelancer_id, company_id


def _body(
    company_id: UUID, *, giorni_previsti: int | None = None, regime: Regime = "partita_iva"
) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        regime=regime,
        cliente=ClienteData(
            cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
        ),
        lettera=LetteraFields(
            ruolo="Backend developer",
            attivita="Le API del prodotto.",
            data_inizio=date(2026, 10, 1),
            modalita="a giornata",
            compenso=Decimal("450"),
            giorni_pagamento=30,
            fine_mese=True,
        ),
        giorni_previsti=giorni_previsti,
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
    stato: str = "firmato",
    signed_at: datetime | None = datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    notice_at: datetime | None = None,
    text_version: str = QUADRO_VERSION,
    regime: str = "partita_iva",
) -> ContractDocument:
    """A framework agreement as phase 3 will leave one: only a signature makes these."""
    document = ContractDocument(
        kind="quadro",
        regime=regime,
        freelancer_id=freelancer_id,
        text_version=text_version,
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato=stato,
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
        "cognome": "Hopper",
        "email": "grace@studio.it",
        "tariffa_giornaliera": Decimal("500"),
        "posizione": "Frontend developer",
        "remoto": "remoto",
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
    match.stato = "in_firma"
    session.commit()


def _framework_now(session: Session, freelancer_id: UUID, stato: str) -> None:
    (framework,) = _documents(session, freelancer_id, "quadro")
    framework.stato = stato
    if stato in ("inviato", "firmato", "disdetto"):
        framework.sent_at = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
    if stato in ("firmato", "disdetto"):
        framework.signed_at = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    if stato == "disdetto":
        framework.notice_at = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    session.commit()


# ---- the check before saving, and the proposal (REB-476) ---------------------------------
SAVED_TABLES = ("matches", "contract_documents", "contract_letter_counters", "admin_actions")


def _rows(session: Session) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(row) for row in session.execute(text(f"SELECT * FROM {table} ORDER BY 1"))]
        for table in SAVED_TABLES
    }


CLIENTE_REST = {"cliente_piva": "01234567890", "cliente_sede": "Milano"}


# ---- the letter type (REB-687) -----------------------------------------------------------
REFUSED_WITHOUT_PARTITA_IVA = (
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
    "o salva la partita IVA."
)
