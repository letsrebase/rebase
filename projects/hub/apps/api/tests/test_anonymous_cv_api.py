"""The anonymous CV's download over HTTP (REB-862): who may press it, what it answers when
there is nothing to print, and the PDF with its audit entry. The model is a `RecordingCall`
and the printer `FakeCvRenderer`, so no key and no binary."""

import copy
import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, text_pdf
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_cv_llm, get_cv_renderer
from rebase_core.anonymous_cv.render import CvHead, FakeCvRenderer
from rebase_core.anonymous_cv.schema import AnonymousCvDraft
from rebase_core.llm import LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.models import AdminAction, Freelancer, FreelancerCard, User

ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
CV = text_pdf("Ada Lovelace Backend developer TIM 2019-2024")
MODEL = "claude-opus-5"

BODY: dict[str, Any] = {
    "lingua": "it",
    "titolo": "Backend developer",
    "sommario": "Backend developer con cinque anni di servizi di fatturazione.",
    "esperienze": [
        {
            "ruolo": "Backend developer",
            "azienda": "una società di telecomunicazioni",
            "luogo": None,
            "periodo": "2019 – 2024",
            "punti": [{"titolo": None, "testo": "Ha sviluppato i servizi di fatturazione."}],
        }
    ],
    "competenze": [{"area": "Competenze", "voci": ["Python", "PostgreSQL"]}],
    "formazione": [],
    "certificazioni": [],
    "lingue": [{"lingua": "Italiano", "livello": "Madrelingua"}],
    "altre_sezioni": [],
}
NO_IDENTITA: dict[str, Any] = {
    "nome": None,
    "cognome": None,
    "email": None,
    "telefono": None,
    "link": [],
}


def _answer(body: dict[str, Any] | None = None) -> LlmResponse:
    text_ = json.dumps({"cv": body or BODY, "identita": NO_IDENTITA})
    return LlmResponse(
        text=text_,
        parsed=AnonymousCvDraft.model_validate_json(text_),
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        provider="anthropic",
        input_tokens=3000,
        output_tokens=2500,
        cache_read_tokens=0,
        thinking_tokens=400,
    )


def _naming_ada() -> LlmResponse:
    body = copy.deepcopy(BODY)
    body["sommario"] = "Ada Lovelace è una backend developer."
    return _answer(body)


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in (
        "ai_usage",
        "anonymous_cvs",
        "freelancer_cards",
        "admin_actions",
        "freelancers",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    from rebase_api.deps import get_sender

    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def printer(client: TestClient) -> FakeCvRenderer:
    fake = FakeCvRenderer()
    client.app.dependency_overrides[get_cv_renderer] = lambda: fake  # type: ignore[attr-defined]
    return fake


def _model(client: TestClient, call: RecordingCall | None) -> None:
    client.app.dependency_overrides[get_cv_llm] = lambda: call  # type: ignore[attr-defined]


def _apply(
    client: TestClient, session: Session, email: str = "ada@studio.it", cv: bool = True
) -> str:
    """A talent through the public form (which answers no id: it is read back by email),
    then without a CV on file when `cv` is false, as a card born from a signup."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": email,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "ibrido",
        },
        files={"cv": ("Ada CV.pdf", CV, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    row = session.scalar(
        select(Freelancer).join(User, User.id == Freelancer.user_id).where(User.email == email)
    )
    assert row is not None
    if not cv:
        row.cv_bytes = row.cv_filename = row.cv_mime = None
        session.commit()
    return str(row.id)


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _url(freelancer_id: str) -> str:
    return f"/api/hub/freelancers/{freelancer_id}/cv-anonimo"


def _trail(session: Session, freelancer_id: str) -> list[AdminAction]:
    session.expire_all()
    return list(
        session.scalars(
            select(AdminAction).where(
                AdminAction.entity_id == UUID(freelancer_id),
                AdminAction.kind == "anon_cv_downloaded",
            )
        )
    )


def _every_string(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _every_string(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _every_string(item)


def test_requires_an_admin(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session, "membro@studio.it")
    assert client.post(_url(freelancer_id)).status_code == 401
    _login(client, sender, "membro@studio.it")
    assert client.post(_url(freelancer_id)).status_code == 403
    assert printer.calls == []


def test_no_cv_is_404_with_the_sentence(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session, cv=False)
    _login(client, sender)
    _model(client, RecordingCall([_answer()]))
    response = client.post(_url(freelancer_id))
    assert response.status_code == 404
    assert response.json() == {"detail": "Nessun CV caricato"}
    assert client.post(_url(MISSING)).status_code == 404


def test_task_off_is_502_with_the_sentence(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session)
    _login(client, sender)
    _model(client, None)
    response = client.post(_url(freelancer_id))
    assert response.status_code == 502
    assert response.json() == {
        "detail": "Il compito «CV anonimo» non è configurato in «Modelli AI»."
    }
    assert printer.calls == []


def test_download_answers_the_pdf_and_records_it(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session)
    _card(api_session, freelancer_id)
    row = api_session.get(Freelancer, UUID(freelancer_id))
    assert row is not None
    row.anni_admin = 12
    api_session.commit()
    _login(client, sender)
    call = RecordingCall([_answer()])
    _model(client, call)

    response = client.post(_url(freelancer_id))

    assert response.status_code == 200, response.text
    assert response.content == b"%PDF-fake"
    assert response.headers["content-type"].startswith("application/pdf")
    # The id's random tail: its head is a UUIDv7's timestamp, shared by talents created
    # together and telling when the person signed up.
    ref = freelancer_id[-8:]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="rebase-cv-backend-developer-{ref}.pdf"'
    )
    [(cv, head)] = printer.calls
    assert head == CvHead(seniority="senior", anni=12, luogo="Torino", modalita="ibrido", ref=ref)
    # No identity in the PDF: nothing the printer was handed carries the person.
    printed = " ".join(_every_string(cv.model_dump())).lower()
    for word in ("ada", "lovelace", "ada@studio.it"):
        assert word not in printed
    [entry] = _trail(api_session, freelancer_id)
    assert entry.payload == {"riusato": False}
    # Read back from the column, not through the filter above: a kind cut to the column's
    # width must never pass silently.
    kinds = api_session.scalars(
        select(AdminAction.kind).where(AdminAction.entity_id == UUID(freelancer_id))
    ).all()
    assert kinds == ["anon_cv_downloaded"]
    admin_id = api_session.scalar(select(User.id).where(User.email == ADMIN_EMAIL))
    assert entry.admin_id == admin_id
    assert len(call.requests) == 1


def test_a_talent_with_no_card_prints_a_bare_head(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session)
    _login(client, sender)
    _model(client, RecordingCall([_answer()]))
    assert client.post(_url(freelancer_id)).status_code == 200
    [(_, head)] = printer.calls
    assert head == CvHead(
        seniority=None, anni=None, luogo=None, modalita="ibrido", ref=freelancer_id[-8:]
    )


def _card(
    session: Session, freelancer_id: str, card: dict[str, Any] = CARD, cv: bytes = CV
) -> None:
    """A card written from `cv`, the CV on file by default."""
    session.add(
        FreelancerCard(
            freelancer_id=UUID(freelancer_id),
            cv_sha256=hashlib.sha256(cv).hexdigest(),
            card=card,
            model=MODEL,
            generated_at=datetime.now(UTC),
        )
    )
    session.commit()


def test_an_uncertain_seniority_is_left_out(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    """A CV that dates no role gives a seniority read from the responsibility alone, which
    the admin page marks uncertain: the PDF says nothing rather than a guess, until an
    admin says one."""
    freelancer_id = _apply(client, api_session)
    _card(api_session, freelancer_id, {**CARD, "anni": None})
    _login(client, sender)
    _model(client, RecordingCall([_answer()]))

    assert client.post(_url(freelancer_id)).status_code == 200
    row = api_session.get(Freelancer, UUID(freelancer_id))
    assert row is not None
    row.seniority_admin = "mid"
    api_session.commit()
    assert client.post(_url(freelancer_id)).status_code == 200

    [(_, guessed), (_, said)] = printer.calls
    ref = freelancer_id[-8:]
    assert guessed == CvHead(seniority=None, anni=None, luogo="Torino", modalita="ibrido", ref=ref)
    assert said == CvHead(seniority="mid", anni=None, luogo="Torino", modalita="ibrido", ref=ref)


def test_a_card_of_an_older_cv_lends_nothing_to_the_head(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    """A card written from another CV may say another level or city: the head keeps only
    the work mode, which is the profile's."""
    freelancer_id = _apply(client, api_session)
    _card(api_session, freelancer_id, cv=text_pdf("Un CV precedente"))
    _login(client, sender)
    _model(client, RecordingCall([_answer()]))

    assert client.post(_url(freelancer_id)).status_code == 200

    [(_, head)] = printer.calls
    assert head == CvHead(
        seniority=None, anni=None, luogo=None, modalita="ibrido", ref=freelancer_id[-8:]
    )


def test_second_download_reuses(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session)
    _login(client, sender)
    call = RecordingCall([_answer()])
    _model(client, call)
    assert client.post(_url(freelancer_id)).status_code == 200
    # Served from what is kept, even with the task switched off.
    _model(client, None)
    assert client.post(_url(freelancer_id)).status_code == 200

    assert len(call.requests) == 1
    kinds = sorted(entry.payload["riusato"] for entry in _trail(api_session, freelancer_id))
    assert kinds == [False, True]
    assert len(printer.calls) == 2


def test_guard_failure_is_422_with_the_sentence(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    printer: FakeCvRenderer,
    api_session: Session,
) -> None:
    freelancer_id = _apply(client, api_session)
    _login(client, sender)
    _model(client, RecordingCall([_naming_ada(), _naming_ada()]))
    response = client.post(_url(freelancer_id))
    assert response.status_code == 422
    assert response.json() == {
        "detail": (
            "Il CV contiene ancora il nome, un recapito o un link della persona anche dopo "
            "la riscrittura."
        )
    }
    assert printer.calls == []
    assert _trail(api_session, freelancer_id) == []


def test_a_print_failure_is_503_and_records_nothing(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    api_session: Session,
) -> None:
    from rebase_core.anonymous_cv.render import CvPrintFailed

    class Broken:
        def render(self, cv: Any, head: CvHead) -> bytes:
            raise CvPrintFailed("typst is not on PATH")

    client.app.dependency_overrides[get_cv_renderer] = lambda: Broken()  # type: ignore[attr-defined]
    freelancer_id = _apply(client, api_session)
    _login(client, sender)
    _model(client, RecordingCall([_answer()]))
    response = client.post(_url(freelancer_id))
    assert response.status_code == 503
    assert response.json() == {"detail": "Non riesco a stampare il CV anonimo."}
    assert _trail(api_session, freelancer_id) == []
