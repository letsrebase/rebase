"""The hub's two public writes: multipart with a CV, and JSON. Both mute, both limited."""

import hashlib
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import pytest
from fakes_cards import CARD, card_response, text_pdf
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_llm, get_sender, get_session_opener, get_tracker
from rebase_api.ratelimit import SIGNUPS_PER_MINUTE
from rebase_core.analytics import APPLICATION_COMPLETED, Tracker
from rebase_core.llm import LlmRequest, LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


def _form(**overrides: str) -> dict[str, str]:
    form = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": "ada@studio.it",
        "tariffa_giornaliera": "450,00",
        "posizione": "Backend developer",
        "remoto": "remoto",
        "utm_source": "linkedin",
    }
    form.update(overrides)
    return form


def _clean(session: Session) -> None:
    session.execute(text("DELETE FROM freelancers"))
    session.execute(text("DELETE FROM companies"))
    session.execute(text("DELETE FROM users"))
    session.commit()


def test_an_application_with_a_cv_is_accepted_and_stored(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    response = client.post(
        "/api/hub/freelancers",
        data={**_form(), "links": ["https://github.com/ada", "https://ada.dev/"]},
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"ok": True}
    row = api_session.execute(
        text(
            "SELECT u.email AS email, f.tariffa_giornaliera, f.cv_filename, f.cv_size, "
            "f.links, f.utm_source "
            "FROM freelancers f JOIN users u ON u.id = f.user_id"
        )
    ).one()
    assert row.email == "ada@studio.it"
    # The Italian comma is accepted and the value is the number, to the cent.
    assert str(row.tariffa_giornaliera) == "450.00"
    assert (row.cv_filename, row.cv_size) == ("Ada CV.pdf", len(PDF))
    assert row.links == ["https://github.com/ada", "https://ada.dev/"]
    assert row.utm_source == "linkedin"


def test_a_linkedin_address_pasted_from_a_phone_is_stored_as_the_profile(
    client: TestClient, api_session: Session
) -> None:
    """ORB-203: no scheme, a trailing slash and LinkedIn's share parameters are what the
    app's share sheet hands over. The row holds the profile, not the paste; something
    that is not on LinkedIn is still a 422 naming the field."""
    _clean(api_session)
    accepted = client.post(
        "/api/hub/freelancers",
        data=_form(linkedin_url="linkedin.com/in/ada-lovelace/?utm_source=share"),
    )
    assert accepted.status_code == 201, accepted.text
    stored = api_session.execute(
        text("SELECT u.linkedin_url FROM freelancers f JOIN users u ON u.id = f.user_id")
    ).scalar_one()
    assert stored == "https://www.linkedin.com/in/ada-lovelace"

    refused = client.post(
        "/api/hub/freelancers",
        data=_form(email="bob@studio.it", linkedin_url="evil.com/linkedin.com/in/ada"),
    )
    assert refused.status_code == 422
    assert {error["loc"][-1] for error in refused.json()["detail"]} == {"linkedin_url"}


def test_the_page_the_person_started_from_is_stored_beside_the_campaign(
    client: TestClient, api_session: Session
) -> None:
    """ORB-167: `origine` is the page of the site the door was on, `home` or `pigrocrm`,
    as the landing's script put it on the link. A slug, or nothing: a value that is not
    one is refused with the field's name, the same as any other field."""
    _clean(api_session)
    accepted = client.post(
        "/api/hub/freelancers",
        data={**_form(), "origine": "pigrocrm"},
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert accepted.status_code == 201, accepted.text
    row = api_session.execute(text("SELECT origine, utm_source FROM freelancers")).one()
    assert (row.origine, row.utm_source) == ("pigrocrm", "linkedin")

    refused = client.post(
        "/api/hub/freelancers",
        data={**_form(email="bob@studio.it"), "origine": "<script>"},
        files={"cv": ("CV.pdf", PDF, "application/pdf")},
    )
    assert refused.status_code == 422
    assert "origine" in [error["loc"][-1] for error in refused.json()["detail"]]

    company = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "XYZ",
            "referente_nome": "Grace",
            "referente_cognome": "Hopper",
            "email": "grace@xyz.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Un backend da rifare.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "500",
            "remoto": "remoto",
            "numero_risorse": 1,
            "utm": {"utm_source": "linkedin", "origine": "home"},
        },
    )
    assert company.status_code == 201, company.text
    assert api_session.execute(text("SELECT origine FROM companies")).scalar() == "home"


def test_a_cv_that_is_not_a_pdf_is_a_422_that_names_the_field(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    response = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("cv.pdf", b"nope", "application/pdf")}
    )
    assert response.status_code == 422
    assert {error["loc"][-1] for error in response.json()["detail"]} == {"cv"}
    assert api_session.execute(text("SELECT count(*) FROM freelancers")).scalar() == 0


def test_an_attached_but_empty_cv_is_refused_rather_than_read_as_no_cv(
    client: TestClient, api_session: Session
) -> None:
    """The difference the optional CV has to keep: a part that is there and carries no
    bytes is a broken upload, and telling the person nothing would leave them thinking
    they had sent a CV."""
    _clean(api_session)
    response = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("cv.pdf", b"", "application/pdf")}
    )
    assert response.status_code == 422
    assert {error["loc"][-1] for error in response.json()["detail"]} == {"cv"}
    assert api_session.execute(text("SELECT count(*) FROM freelancers")).scalar() == 0


def test_an_application_with_no_cv_at_all_is_accepted_and_stored_without_one(
    client: TestClient, api_session: Session
) -> None:
    """The CV is optional: somebody with no PDF to hand finishes the form and the card
    waits for it. The part is absent from the body, which is what the wizard sends."""
    _clean(api_session)
    response = client.post("/api/hub/freelancers", data=_form())
    assert response.status_code == 201, response.text
    email = api_session.execute(
        text("SELECT u.email FROM freelancers f JOIN users u ON u.id = f.user_id")
    ).scalar_one()
    row = api_session.execute(text("SELECT cv_bytes, cv_filename, cv_size FROM freelancers")).one()
    assert email == "ada@studio.it"
    assert (row.cv_bytes, row.cv_filename, row.cv_size) == (None, None, None)


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


def _card_and_identity(session: Session) -> tuple[object, ...]:
    """The card's own columns plus the identity fields now read off `users` through
    the join, combined into one tuple a before/after snapshot can compare whole."""
    card = session.execute(
        text(
            "SELECT tariffa_giornaliera, posizione, remoto, links, compilata_da, "
            "cv_bytes, cv_filename, cv_mime, cv_size FROM freelancers"
        )
    ).one()
    identity = session.execute(
        text(
            "SELECT u.nome, u.cognome, u.linkedin_url "
            "FROM freelancers f JOIN users u ON u.id = f.user_id"
        )
    ).one()
    return (*card, *identity)


def test_a_repeated_application_leaves_the_existing_card_unchanged(
    client: TestClient, api_session: Session
) -> None:
    """REB-272: this route is public and unauthenticated, so a second post to the same
    address proves nothing about who sent it. Every field, `compilata_da` and the CV
    bytes stay exactly as the first application left them, and the answer is still the
    same mute 201."""
    _clean(api_session)
    first = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("Ada CV.pdf", PDF, "application/pdf")}
    )
    assert first.status_code == 201, first.text
    before = _card_and_identity(api_session)
    again = client.post(
        "/api/hub/freelancers",
        data=_form(posizione="Tech lead", tariffa_giornaliera="900,00"),
        files={"cv": ("evil.pdf", PDF + b"v2", "application/pdf")},
    )
    assert again.status_code == 201, again.text
    assert again.json() == {"ok": True}
    after = _card_and_identity(api_session)
    assert after == before
    assert api_session.execute(text("SELECT count(*) FROM freelancers")).scalar() == 1


def test_a_repeated_application_sends_the_existing_card_a_magic_link_mail(
    client: TestClient, api_session: Session, sender: RecordingSender
) -> None:
    _clean(api_session)
    assert (
        client.post(
            "/api/hub/freelancers",
            data=_form(),
            files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
        ).status_code
        == 201
    )
    assert sender.sent == []
    again = client.post("/api/hub/freelancers", data=_form(posizione="Tech lead"))
    assert again.status_code == 201, again.text
    assert len(sender.sent) == 1
    mail = sender.sent[0]
    assert mail.to == "ada@studio.it"
    assert "Risulta già una scheda" in mail.text
    assert "/entra?t=" in mail.text


def test_a_field_the_form_refuses_is_a_422_in_the_same_shape_as_a_json_body(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data=_form(remoto="quando capita", tariffa_giornaliera="tanto"),
        files={"cv": ("cv.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 422
    fields = {error["loc"][-1] for error in response.json()["detail"]}
    # The number is checked first, on its own, because it is parsed by hand.
    assert fields == {"tariffa_giornaliera"}


def test_a_company_request_is_accepted_and_the_answer_says_nothing_else(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    response = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "ACME Srl",
            "referente_nome": "Wile",
            "referente_cognome": "E.",
            "email": "wile@acme.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Serve un backend developer per tre mesi.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "500",
            "remoto": "remoto",
            "numero_risorse": 1,
            "utm": {"utm_source": "google"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"ok": True}
    row = api_session.execute(
        text("SELECT nome_azienda, periodo_da, utm_source FROM companies")
    ).one()
    assert (row.nome_azienda, str(row.periodo_da), row.utm_source) == (
        "ACME Srl",
        "2026-10-01",
        "google",
    )


def test_the_three_public_writes_share_one_budget_per_client(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    body = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Un progetto",
        "periodo_da": "2026-10-01",
        "durata": "3 mesi",
        "budget_giornaliero": "500",
        "remoto": "remoto",
        "numero_risorse": 1,
    }
    for _ in range(SIGNUPS_PER_MINUTE - 1):
        assert client.post("/api/hub/companies", json=body).status_code == 201
    assert (
        client.post(
            "/api/community/signups", json={"email": "ada@studio.it", "nome": "Ada", "cognome": "L"}
        ).status_code
        == 201
    )
    refused = client.post("/api/hub/companies", json=body)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


# ---- the completion event (REB-215) ----------------------------------------------------


class RecordingCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        self.calls.append((event, distinct_id, properties))


@pytest.fixture
def tracked(client: TestClient) -> Iterator[RecordingCapture]:
    """A tracker whose SDK is this recorder. The default `client` has none at all --
    `Settings(_env_file=None)` carries no key -- which is what the last test relies on."""
    capture = RecordingCapture()
    client.app.dependency_overrides[get_tracker] = lambda: Tracker(capture)  # type: ignore[attr-defined]
    yield capture
    client.app.dependency_overrides.pop(get_tracker, None)  # type: ignore[attr-defined]


def _company() -> dict[str, object]:
    return {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Ada",
        "referente_cognome": "Lovelace",
        "email": "ada@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Dobbiamo rifare il backend del portale clienti.",
        "periodo_da": "2026-10-01",
        "durata": "3 mesi",
        "budget_giornaliero": "500",
        "remoto": "remoto",
        "numero_risorse": 1,
        "utm": {"utm_source": "linkedin", "origine": "home"},
    }


def test_an_application_is_one_completion_event_on_the_browsers_person(
    client: TestClient, api_session: Session, tracked: RecordingCapture
) -> None:
    _clean(api_session)
    response = client.post(
        "/api/hub/freelancers",
        data={**_form(), "distinct_id": "anon-1"},
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    # After the answer (a background task), on the browser's id, with what the funnel
    # needs and nothing about the person.
    assert tracked.calls == [
        (
            APPLICATION_COMPLETED,
            "anon-1",
            {
                "tipo": "freelance",
                "via": "server",
                "cv": True,
                "utm_source": "linkedin",
                "$process_person_profile": False,
            },
        )
    ]
    assert "ada@studio.it" not in str(tracked.calls)


def test_an_application_without_the_browser_id_still_counts(
    client: TestClient, api_session: Session, tracked: RecordingCapture
) -> None:
    """The SDK was blocked: that is the case the server event exists for."""
    _clean(api_session)
    assert client.post("/api/hub/freelancers", data=_form()).status_code == 201
    ((event, distinct_id, properties),) = tracked.calls
    assert event == APPLICATION_COMPLETED
    assert len(distinct_id) == 32
    assert properties["cv"] is False


def test_a_company_request_is_one_completion_event_too(
    client: TestClient, api_session: Session, tracked: RecordingCapture
) -> None:
    _clean(api_session)
    response = client.post("/api/hub/companies", json={**_company(), "distinct_id": "anon-2"})
    assert response.status_code == 201, response.text
    assert tracked.calls == [
        (
            APPLICATION_COMPLETED,
            "anon-2",
            {
                "tipo": "azienda",
                "via": "server",
                "utm_source": "linkedin",
                "origine": "home",
                "$process_person_profile": False,
            },
        )
    ]


def test_a_browser_id_that_is_not_one_is_refused_before_anything_is_written(
    client: TestClient, api_session: Session, tracked: RecordingCapture
) -> None:
    _clean(api_session)
    too_long = "x" * 300
    assert (
        client.post("/api/hub/freelancers", data={**_form(), "distinct_id": too_long}).status_code
        == 422
    )
    assert (
        client.post("/api/hub/companies", json={**_company(), "distinct_id": too_long}).status_code
        == 422
    )
    assert tracked.calls == []
    assert api_session.execute(text("SELECT count(*) FROM freelancers")).scalar() == 0


def test_without_a_key_nothing_is_tracked_and_the_application_still_lands(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    assert client.post("/api/hub/freelancers", data=_form()).status_code == 201
    assert client.post("/api/hub/companies", json=_company()).status_code == 201
    assert api_session.execute(text("SELECT count(*) FROM freelancers")).scalar() == 1


# ---- the anonymous card (REB-510): written after the answer, never before it ----------

CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, AWS.")


def test_the_wizard_schedules_the_card_write(
    client: TestClient, api_session: Session, llm: RecordingCall
) -> None:
    """A new card with a CV gets its anonymous description once the 201 is out, in a
    session of its own; a repeat of the address and an application with no CV ask
    Claude nothing."""
    _clean(api_session)
    response = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("Ada CV.pdf", CV, "application/pdf")}
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"ok": True}

    assert len(llm.requests) == 1
    assert "Ada Lovelace, backend developer" in llm.requests[0].messages[0]["content"][0]["text"]
    row = api_session.execute(text("SELECT cv_sha256, card, error FROM freelancer_cards")).one()
    assert (row.cv_sha256, row.card, row.error) == (hashlib.sha256(CV).hexdigest(), CARD, None)

    again = client.post(
        "/api/hub/freelancers",
        data=_form(),
        files={"cv": ("evil.pdf", text_pdf("Un altro CV."), "application/pdf")},
    )
    assert again.status_code == 201
    no_cv = client.post("/api/hub/freelancers", data=_form(email="bob@studio.it"))
    assert no_cv.status_code == 201
    assert len(llm.requests) == 1
    assert api_session.execute(text("SELECT count(*) FROM freelancer_cards")).scalar() == 1


class _AfterTheEvent:
    """Claude, noting whether the completion event had already gone out when it was
    called: the card write is queued after it, so PostHog never waits on Claude."""

    def __init__(self, tracked: RecordingCapture) -> None:
        self.tracked = tracked
        self.events_before: list[int] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.events_before.append(len(self.tracked.calls))
        return card_response()


def test_the_completion_event_is_not_held_behind_the_card(
    client: TestClient, api_session: Session, llm: RecordingCall, tracked: RecordingCapture
) -> None:
    _clean(api_session)
    claude = _AfterTheEvent(tracked)
    client.app.dependency_overrides[get_llm] = lambda: claude  # type: ignore[attr-defined]

    response = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("Ada CV.pdf", CV, "application/pdf")}
    )

    assert response.status_code == 201, response.text
    assert claude.events_before == [1]
    assert len(tracked.calls) == 1


def test_without_a_key_the_wizard_writes_no_card_and_opens_no_session(
    client: TestClient, api_session: Session
) -> None:
    _clean(api_session)
    opened: list[bool] = []

    def opener() -> Any:
        opened.append(True)
        return nullcontext(api_session)

    client.app.dependency_overrides[get_llm] = lambda: None  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_session_opener] = lambda: opener  # type: ignore[attr-defined]
    response = client.post(
        "/api/hub/freelancers", data=_form(), files={"cv": ("Ada CV.pdf", CV, "application/pdf")}
    )
    assert response.status_code == 201, response.text
    assert opened == []
    assert api_session.execute(text("SELECT count(*) FROM freelancer_cards")).scalar() == 0
