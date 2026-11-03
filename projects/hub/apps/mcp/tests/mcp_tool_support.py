"""Helpers the tool tests of several families share."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_cards import text_pdf
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.models import User
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupCreate


def _seed(factory: sessionmaker[Session], addresses: list[str]) -> None:
    session = factory()
    try:
        for address in addresses:
            SignupService(session).subscribe(
                SignupCreate(
                    email=address,
                    nome="Ada",
                    cognome="Lovelace",
                    # One profile per address: the same profile is the same person (REB-800).
                    linkedin_url=f"https://www.linkedin.com/in/{address.split('@')[0]}",
                )
            )
    finally:
        session.close()


# The admin every test calls as: the transport resolves a token to this and hands it in.
IVAN = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000001"),
    email="ivan@rebase.it",
    nome="Ivan",
    attivo=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in (
        "admin_actions",
        "notes",
        "freelancer_cards",
        "freelancers",
        "companies",
        "users",
    ):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


def _seed_admin(factory: sessionmaker[Session]) -> None:
    """`IVAN`'s own `users` row: `Freelancer.vetted_by`, `TeamProposal.user_id`,
    `TalentCloudGrant.granted_by`/`revoked_by` and `AdminAction.admin_id` are all a
    real foreign key to `users.id`, which the fake `AdminRead` every test calls as is
    not, on its own -- only the tools that never write one of those (a read, or
    `CardWriter`, which takes no admin at all) can skip this."""
    session = factory()
    session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, cognome="Sala", role="admin"))
    session.commit()
    session.close()


def _seed_freelancer(factory: sessionmaker[Session]) -> str:
    from decimal import Decimal

    from rebase_core.freelancer_schemas import FreelancerCreate
    from rebase_core.freelancers import FreelancerService

    session = factory()
    try:
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
        return str(row.id)
    finally:
        session.close()


def _seed_company(factory: sessionmaker[Session]) -> str:
    from datetime import date
    from decimal import Decimal

    from rebase_core.companies import CompanyService
    from rebase_core.company_schemas import CompanyCreate

    session = factory()
    try:
        row, _ = CompanyService(session).request(
            CompanyCreate(
                nome_azienda="ACME Srl",
                referente_nome="Wile",
                referente_cognome="E.",
                email="wile@acme.it",
                telefono="+39 345 1234567",
                figura_richiesta="Backend developer",
                progetto="Un backend developer per tre mesi.",
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=Decimal("500"),
                remoto="remoto",
                numero_risorse=1,
            )
        )
        return str(row.id)
    finally:
        session.close()


def _seed_freelancer_with_text_cv(factory: sessionmaker[Session], text: str) -> str:
    """Like `_seed_freelancer`, but with a CV `pypdf` reads text out of (`PDF`, the
    module's own fixture, is a scan with none): the card writer needs a real CV to
    write anything from."""
    from rebase_core.freelancer_schemas import FreelancerCreate
    from rebase_core.freelancers import FreelancerService

    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
            ),
            text_pdf(text),
            "cv.pdf",
            "application/pdf",
        )
        return str(row.id)
    finally:
        session.close()


def _wipe_team(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "admin_actions",
        "talent_cloud_grants",
        "freelancer_cards",
        "notes",
        "freelancers",
        "companies",
        "users",
    ):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()
