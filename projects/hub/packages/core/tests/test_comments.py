"""Comments on a freelancer or a company: appended, never edited, read newest first."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.comments import COMMENT_MAX_LENGTH, CommentService
from rebase_core.companies import CompanyService
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.schemas import CompanyCreate, CompanyRead, FreelancerCreate, FreelancerRead

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    for table in ("comments", "freelancers", "companies", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _freelancer(session: Session) -> FreelancerRead:
    read, _ = FreelancerService(session).apply(
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
    return read


def _company(session: Session) -> CompanyRead:
    read, _ = CompanyService(session).request(
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
    return read


def test_a_comment_is_appended_and_the_thread_reads_newest_first(clean: Session) -> None:
    freelancer = _freelancer(clean)
    comments = CommentService(clean)
    first = comments.add(
        "freelancer", freelancer.id, "  Sentito al telefono.\nRichiamare lunedì.  ", "Ivan"
    )
    second = comments.add("freelancer", freelancer.id, "Ha mandato il portfolio.", "MCP")
    assert first.testo == "Sentito al telefono.\nRichiamare lunedì."
    assert (first.autore, first.entity_type, first.entity_id) == (
        "Ivan",
        "freelancer",
        freelancer.id,
    )
    thread = comments.list("freelancer", freelancer.id)
    assert [c.id for c in thread] == [second.id, first.id]
    # The detail carries the thread, the list does not load it.
    read = FreelancerService(clean).get(freelancer.id)
    assert [c.testo for c in read.commenti] == ["Ha mandato il portfolio.", first.testo]
    listed = FreelancerService(clean).list_recent()
    assert listed.items[0].commenti == []
    # The summary note is a different thing and is left alone.
    assert read.note is None


def test_a_company_thread_is_its_own_and_shows_on_its_detail(clean: Session) -> None:
    company = _company(clean)
    freelancer = _freelancer(clean)
    comments = CommentService(clean)
    comments.add("company", company.id, "Budget confermato.", "Ivan")
    comments.add("freelancer", freelancer.id, "Altro thread.", "Ivan")
    assert [c.testo for c in comments.list("company", company.id)] == ["Budget confermato."]
    assert [c.testo for c in CompanyService(clean).get(company.id).commenti] == [
        "Budget confermato."
    ]
    assert CompanyService(clean).list_recent().items[0].commenti == []


def test_a_comment_needs_an_existing_row_a_text_and_an_author(clean: Session) -> None:
    freelancer = _freelancer(clean)
    comments = CommentService(clean)
    with pytest.raises(NotFound):
        comments.add("freelancer", uuid4(), "Nessuno qui.", "Ivan")
    with pytest.raises(NotFound):
        comments.add("company", freelancer.id, "Un freelance non è un'azienda.", "Ivan")
    with pytest.raises(ValidationFailed) as kind:
        comments.add("signup", freelancer.id, "Niente commenti sulle iscrizioni.", "Ivan")
    assert kind.value.details["field"] == "entity_type"
    with pytest.raises(ValidationFailed) as empty:
        comments.add("freelancer", freelancer.id, "   \n ", "Ivan")
    assert empty.value.details["field"] == "testo"
    with pytest.raises(ValidationFailed) as long:
        comments.add("freelancer", freelancer.id, "x" * (COMMENT_MAX_LENGTH + 1), "Ivan")
    assert "4000" in long.value.details["reason"]
    with pytest.raises(ValidationFailed) as control:
        comments.add("freelancer", freelancer.id, "ok\x07", "Ivan")
    assert control.value.details["field"] == "testo"
    with pytest.raises(ValidationFailed) as anonymous:
        comments.add("freelancer", freelancer.id, "Va bene.", "  ")
    assert anonymous.value.details["field"] == "autore"
    assert comments.list("freelancer", freelancer.id) == []


def test_the_service_offers_no_way_to_edit_or_delete(clean: Session) -> None:
    public = {name for name in dir(CommentService) if not name.startswith("_")}
    assert public == {"add", "list"}
