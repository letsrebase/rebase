"""REB-387 phase 2 over HTTP: tax data, a match created from a card and a request, both
PDFs downloadable, a draft cancelled; nothing at all without the admin cookie."""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from fakes_pigro import DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro, linked_body
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from rebase_api.deps import (
    _engagements_call,
    get_engagements,
    get_http_call,
    get_renderer,
    get_signing_factory,
)
from rebase_core.config import Settings, get_settings
from rebase_core.http import urllib_call, urllib_engagements_call
from rebase_core.mail import RecordingSender
from rebase_core.match_words import PIGRO_NOT_CONFIGURED
from rebase_core.models import AdminAction, ContractDocument, Match, User
from rebase_core.pigro import NOT_ANSWERING

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
# What the company would pay rebase a day: it must appear nowhere this flow answers.
BUDGET = "777.77"
FISCAL = {
    "codice_fiscale": "LVLDAA85T50H501Z",
    "partita_iva": "01234567890",
    "domicilio": "Via Roma 1, Milano",
    "pec": None,
}
CLIENTE = {
    "cliente_ragione_sociale": "ACME S.r.l.",
    "cliente_piva": "01234567890",
    "cliente_sede": "Milano",
}
LETTERA = {
    "ruolo": "Backend developer",
    "attivita": "Le API del prodotto.",
    "data_inizio": "2026-10-01",
    "compenso": "450",
    "giorni_pagamento": 30,
    "fine_mese": True,
}
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
    "signups",
)


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer()
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender) -> None:
    assert client.post("/api/hub/auth/link", json={"email": ADMIN_EMAIL}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient) -> str:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": "ada@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/freelancers").json()["items"][0]["id"])


def _request_company(client: TestClient) -> str:
    response = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "ACME Srl",
            "referente_nome": "Wile",
            "referente_cognome": "E.",
            "email": "wile@acme.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Un backend developer per tre mesi.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": BUDGET,
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/companies").json()["items"][0]["id"])


def _ready(client: TestClient, sender: RecordingSender) -> tuple[str, str]:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert (
        client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL).status_code == 200
    )
    return freelancer_id, company_id


def test_without_the_cookie_every_match_route_is_a_401(client: TestClient, admin: None) -> None:
    for method, path in (
        ("GET", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("PUT", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches/prefill?company_id={MISSING}"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches/preview"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches/check"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", "/api/hub/matches"),
        ("GET", f"/api/hub/matches/{MISSING}"),
        ("POST", f"/api/hub/matches/{MISSING}/cancel"),
        ("POST", f"/api/hub/matches/{MISSING}/close"),
        ("POST", f"/api/hub/matches/{MISSING}/pigro/link"),
        ("GET", f"/api/hub/matches/{MISSING}/report"),
        ("GET", f"/api/hub/contract-documents/{MISSING}/pdf"),
    ):
        assert client.request(method, path, json={}).status_code == 401, (method, path)


def test_an_admin_matches_a_card_with_a_request_and_downloads_both_pdfs(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/fiscal").json() is None
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["partita_iva"] == "01234567890"

    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.status_code == 200, prefill.text
    assert prefill.json()["lettera"]["compenso"] == "450.00"
    assert prefill.json()["quadro_necessario"] is True
    assert BUDGET not in prefill.text

    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    match = created.json()
    letter = match["lettera"]
    assert match["stato"] == "bozza"
    assert re.fullmatch(r"\d{4}-001", letter["numero"])
    assert letter["stato"] == "in_attesa"
    assert BUDGET not in created.text

    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["quadro"]["stato"] == "generato"
    assert [item["id"] for item in page["matches"]] == [match["id"]]
    assert page["fiscale"]["codice_fiscale"] == "LVLDAA85T50H501Z"
    assert (
        client.get(f"/api/hub/matches/{match['id']}").json()["lettera"]["numero"]
        == letter["numero"]
    )

    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert f"lettera-di-incarico-{letter['numero']}.pdf" in pdf.headers["content-disposition"]
    assert client.get(f"/api/hub/contract-documents/{page['quadro']['id']}/pdf").status_code == 200
    signed = client.get(
        f"/api/hub/contract-documents/{letter['id']}/pdf", params={"firmato": "true"}
    )
    assert signed.status_code == 404


def test_a_repeated_create_with_the_same_id_writes_the_match_once(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: the wizard's own retry after a lost response sends the same
    client-generated id again with «Salva senza inviare» or «Invia per la firma», and
    gets the match already written back, never a second one with another letter
    number."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    second = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"] == given_id
    assert first.json()["lettera"]["numero"] == second.json()["lettera"]["numero"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_same_id_used_by_another_freelancers_match_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text

    _apply_member(client, "grace@studio.it")
    other_freelancer_id = str(client.get("/api/hub/freelancers").json()["items"][0]["id"])
    assert (
        client.put(f"/api/hub/freelancers/{other_freelancer_id}/fiscal", json=FISCAL).status_code
        == 200
    )

    refused = client.post(
        f"/api/hub/freelancers/{other_freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )

    assert refused.status_code == 409, refused.text
    assert client.get(f"/api/hub/freelancers/{other_freelancer_id}/matches").json()["matches"] == []


def test_a_real_id_collision_at_the_flush_is_a_409_not_a_500(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REB-433: two different freelancers can race past the id check -- each reads
    "nothing written under this id yet" before the other commits (the real race, under
    Postgres, is `test_matches.py`'s own two-session gate test) -- and only the insert
    itself, protected by the id's primary key, catches the second one. This drives a
    real collision rather than forging the exception: a first freelancer's match is
    actually committed under `given_id`, the ordinary id check is made to miss that row
    once (standing in for the race window: it read "nothing written yet" a moment
    before the first committed), and the second freelancer's own `INSERT` then hits the
    real `matches_pkey` constraint. The route must still answer 409, not an unhandled
    500."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    committed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert committed.status_code == 201, committed.text

    _apply_member(client, "grace@studio.it")
    other_freelancer_id = str(client.get("/api/hub/freelancers").json()["items"][0]["id"])
    assert (
        client.put(f"/api/hub/freelancers/{other_freelancer_id}/fiscal", json=FISCAL).status_code
        == 200
    )
    real_get = Session.get
    missed = False

    def get_missing_the_committed_match(
        self: Session, entity: type, ident: object, **kwargs: object
    ) -> object:
        nonlocal missed
        if not missed and entity is Match and str(ident) == given_id:
            missed = True
            return None
        return real_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(Session, "get", get_missing_the_committed_match)

    refused = client.post(
        f"/api/hub/freelancers/{other_freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )

    assert refused.status_code == 409, refused.text
    assert "un altro freelance" in refused.json()["detail"]


def test_a_repeated_create_with_the_same_id_and_changed_data_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: a retry that corrects the letter or the client before sending it again
    must not be handed back the stale match under cover of the idempotent id -- the
    request no longer matches what was saved, so this is a 409, not a 201."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    assert first.status_code == 201, first.text

    changed = {**body, "lettera": {**LETTERA, "ruolo": "Un altro ruolo"}}
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=changed)

    assert refused.status_code == 409, refused.text
    assert "dati diversi" in refused.json()["detail"]
    assert "Match e contratti" in refused.json()["detail"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_preview_renders_a_document_without_saving_or_numbering_it(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    body = {"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"documento": "lettera"},
        json=body,
    )
    assert preview.status_code == 200, preview.text
    assert preview.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    quadro = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"documento": "quadro"},
        json=body,
    )
    assert quadro.status_code == 200
    assert renderer.calls[-1][0] == "contratto-quadro"
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_a_match_without_tax_data_names_the_step_that_is_missing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 422
    assert created.json()["detail"][0]["loc"] == ["body", "fiscale"]


def test_a_closed_request_and_a_fee_the_law_refuses_are_422s_on_their_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    assert (
        client.patch(f"/api/hub/companies/{company_id}", json={"stato": "chiuso"}).status_code
        == 200
    )
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_an_end_date_before_the_start_names_data_fine(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    backwards = {**LETTERA, "data_fine": "2026-09-01"}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": backwards},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "data_fine"
    assert detail["msg"] == "la fine prevista viene prima dell'inizio"


def test_a_payment_term_past_thirty_days_from_month_end_names_giorni_pagamento(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45, "fine_mese": True}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "giorni_pagamento"
    assert (
        detail["msg"]
        == "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)"
    )


def test_a_draft_is_cancelled_once_and_only_an_active_match_closes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 409
    cancelled = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["stato"], cancelled.json()["lettera"]["stato"]) == (
        "annullato",
        "annullato",
    )
    again = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert again.status_code == 409
    assert "bozza" in again.json()["detail"]


def test_a_soft_deleted_freelancer_hides_all_four_match_routes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    letter_id = match["lettera"]["id"]
    assert client.delete(f"/api/hub/freelancers/{freelancer_id}").status_code == 200

    assert client.get(f"/api/hub/matches/{match['id']}").status_code == 404
    assert client.get(f"/api/hub/contract-documents/{letter_id}/pdf").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/cancel").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 404


def test_a_render_that_fails_is_a_503_with_a_sentence(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_renderer] = lambda: FailingRenderer()  # type: ignore[attr-defined]
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert created.json()["detail"].startswith("La generazione del contratto non è riuscita")
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def _apply_member(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Bob",
            "cognome": "Ross",
            "email": email,
            "tariffa_giornaliera": "300",
            "posizione": "Designer",
            "remoto": "remoto",
        },
        files={"cv": ("Bob CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _login_as(client: TestClient, sender: RecordingSender, email: str) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def test_without_the_cookie_the_match_list_is_a_401(client: TestClient, admin: None) -> None:
    assert client.get("/api/hub/matches").status_code == 401


def test_a_signed_in_member_hitting_the_match_list_is_403_not_401(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply_member(client, "bob@studio.it")
    _login_as(client, sender, "bob@studio.it")
    assert client.get("/api/hub/matches").status_code == 403


def test_the_match_list_answers_200_with_filters_and_carries_no_budget_or_tax_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    match = created.json()

    listed = client.get("/api/hub/matches")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["totale"] == 1
    assert [item["id"] for item in body["items"]] == [match["id"]]
    row = body["items"][0]
    assert row["nome_azienda"] == "ACME Srl"
    assert row["lettera_numero"] == match["lettera"]["numero"]
    assert BUDGET not in listed.text
    assert not any(key in row for key in ("codice_fiscale", "partita_iva", "domicilio", "pec"))

    by_state = client.get("/api/hub/matches", params={"stato": "bozza"})
    assert by_state.status_code == 200
    assert by_state.json()["totale"] == 1
    by_wrong_state = client.get("/api/hub/matches", params={"stato": "attivo"})
    assert by_wrong_state.json()["totale"] == 0

    by_search = client.get("/api/hub/matches", params={"q": "ACME"})
    assert [item["id"] for item in by_search.json()["items"]] == [match["id"]]
    by_missing_search = client.get("/api/hub/matches", params={"q": "nessuno"})
    assert by_missing_search.json()["items"] == []


def test_the_match_list_422s_on_an_unknown_state(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    refused = client.get("/api/hub/matches", params={"stato": "chissà"})
    assert refused.status_code == 422


def test_a_malformed_signer_setting_is_a_503_not_a_crash(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,
        signer_json="{non json",  # type: ignore[call-arg]
    )
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert "REBASE_SIGNER_JSON" in created.json()["detail"]


# ---- what a match is doing, and the check before saving (REB-476, REB-477) -------------


def test_a_match_and_its_documents_say_what_they_are_doing_and_what_comes_next(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    numero = match["lettera"]["numero"]
    draft = f"La lettera n. {numero} è pronta: il freelance non ha ancora ricevuto nulla."
    assert (match["situazione"], match["prossima_azione"], match["altre_azioni"]) == (
        draft,
        "invia",
        ["annulla"],
    )
    assert match["lettera"]["situazione"] == "Parte da sola dopo la firma del contratto quadro."
    quadro = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["quadro"]
    assert (quadro["prossima_azione"], quadro["altre_azioni"]) == (None, ["annulla"])
    assert quadro["situazione"].startswith("Parte con «Invia per la firma» sul suo match.")
    (row,) = client.get("/api/hub/matches").json()["items"]
    assert row["situazione"] == draft


def test_the_check_says_what_saving_would_do_and_writes_nothing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    checked = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json() == {
        "riepilogo": [
            "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
            "Compenso: 450,00 €, IVA esclusa, pagato a 30 giorni fine mese.",
        ],
        "cosa_succede": (
            "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua "
            "firma."
        ),
        "quadro_necessario": True,
        "dati_fiscali_mancanti": False,
    }
    assert BUDGET not in checked.text
    assert renderer.calls == []
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert (page["quadro"], page["matches"]) == (None, [])


def test_the_check_reports_missing_tax_data_instead_of_refusing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    checked = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["dati_fiscali_mancanti"] is True
    assert checked.json()["riepilogo"][-1] == (
        "Mancano i dati fiscali del freelance: servono prima di salvare."
    )


def test_the_check_is_a_422_naming_the_field_as_create_is(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45, "fine_mese": True}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "giorni_pagamento"
    assert (
        client.patch(f"/api/hub/companies/{company_id}", json={"stato": "chiuso"}).status_code
        == 200
    )
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_without_the_cookie_the_check_is_a_401(client: TestClient, admin: None) -> None:
    answered = client.post(
        f"/api/hub/freelancers/{MISSING}/matches/check",
        json={"company_id": MISSING, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert answered.status_code == 401


# ---- the link to Pigro and the report (REB-499) ------------------------------------------

FATTURA = {
    "id": "0192e0a0-0000-7000-8000-0000000f0012",
    "tipo": "fattura",
    "anno": 2026,
    "numero": 12,
    "stato": "emessa",
    "stato_pagamento": "da_incassare",
    "data": "2026-10-31",
}
# The CRM's report of the deal (its door's `GET .../report`): one row per time entry.
CRM_REPORT = {
    "slug": "ada-lovelace",
    "deal_url": DEAL_URL,
    "deal": {"id": str(DEAL), "nome": "Lettera n. 2026-001", "stato": "in corso"},
    "giorni": [
        {"data": "2026-10-01", "ore": "8.00", "descrizione": "Setup", "fattura": FATTURA},
        {"data": "2026-10-02", "ore": "4.00", "descrizione": "API", "fattura": None},
    ],
    "totale_ore": "12.00",
    "ore_fatturate": "8.00",
    "ore_non_fatturate": "4.00",
    "fatture": [{**FATTURA, "ore": "8.00"}],
}


@pytest.fixture
def pigro(client: TestClient) -> Iterator[RecordedPigro]:
    """The CRM's door behind the API's own seam, and the token that opens it: the one
    override `get_http_call` hands every route that talks to Pigro."""
    fake = RecordedPigro([(201, linked_body())])
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url=PIGRO, pigro_engagements_token=TOKEN
    )
    client.app.dependency_overrides[get_http_call] = lambda: fake  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    yield fake


def _signed_match(
    client: TestClient,
    sender: RecordingSender,
    session: Session,
    pigro_stato: str | None = "da_collegare",
    **columns: Any,
) -> str:
    """A match as a signature leaves it: made over HTTP with 40 expected days, then its
    letter signed and the match active in the database, since how a letter gets signed
    is `test_signing_api.py`'s business."""
    freelancer_id, company_id = _ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={
            "company_id": company_id,
            "cliente": CLIENTE,
            "lettera": LETTERA,
            "giorni_previsti": 40,
        },
    )
    assert created.status_code == 201, created.text
    match_id = UUID(created.json()["id"])
    session.execute(
        update(ContractDocument)
        .where(ContractDocument.match_id == match_id)
        .values(stato="firmato", signed_at=datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    )
    session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(stato="attivo", pigro_stato=pigro_stato, **columns)
    )
    session.commit()
    return str(match_id)


def _with_signed_copy(session: Session, match_id: str) -> None:
    """The sealed copy arrived: the card speaks of the match, not of the copy it waits
    for."""
    session.execute(
        update(ContractDocument)
        .where(ContractDocument.match_id == UUID(match_id))
        .values(signed_pdf=PDF)
    )
    session.commit()


def test_post_pigro_link_answers_the_match(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
    api_session: Session,
) -> None:
    """«Riprova su Pigro»: the link runs now, and the answer is the match as it stands,
    linked; the freelancer is told, and the match's trail says which admin asked."""
    match_id = _signed_match(client, sender, api_session)

    linked = client.post(f"/api/hub/matches/{match_id}/pigro/link")

    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert (body["id"], body["stato"], body["pigro_stato"]) == (match_id, "attivo", "collegato")
    assert (body["pigro_slug"], body["pigro_deal_id"], body["pigro_url"]) == (
        "ada-lovelace",
        str(DEAL),
        DEAL_URL,
    )
    assert body["pigro_errore"] is None
    [(method, url, headers, _sent)] = pigro.calls
    assert (method, url) == ("PUT", f"{PIGRO}/api/rebase/engagements/{match_id}")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert sender.sent[-1].to == "ada@studio.it"
    assert "le ore si registrano su Pigro" in sender.sent[-1].subject
    action = api_session.scalars(
        select(AdminAction)
        .where(AdminAction.entity_id == UUID(match_id))
        .where(AdminAction.kind == "pigro_link")
    ).one()
    assert action.payload == {"esito": "collegato", "errore": None}


def test_post_pigro_link_of_a_draft_is_409(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    draft = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()

    refused = client.post(f"/api/hub/matches/{draft['id']}/pigro/link")

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Si collega a Pigro solo un match attivo."
    assert pigro.calls == []
    assert client.get(f"/api/hub/matches/{draft['id']}").json()["pigro_stato"] is None


def test_get_report_answers_the_grouped_report(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
    api_session: Session,
) -> None:
    """«Consuntivo»: the CRM's rows for the period asked, summed by day, ISO week and
    month, against the 40 days expected; every figure a string with two places."""
    match_id = _signed_match(
        client, sender, api_session, "collegato", pigro_url=DEAL_URL, pigro_deal_id=DEAL
    )
    pigro.answers = [(200, json.dumps(CRM_REPORT).encode())]

    answered = client.get(
        f"/api/hub/matches/{match_id}/report", params={"da": "2026-10-01", "a": "2026-10-31"}
    )

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "match_id": match_id,
        "pigro_url": DEAL_URL,
        "pigro_stato": "collegato",
        "giorni_previsti": 40,
        "ore_previste": "320.00",
        "totale_ore": "12.00",
        "giorni_equivalenti": "1.50",
        "avanzamento": "3.75",
        "ore_fatturate": "8.00",
        "ore_non_fatturate": "4.00",
        "per_giorno": [
            {"data": "2026-10-01", "ore": "8.00", "descrizioni": ["Setup"], "fatture": ["12/2026"]},
            {"data": "2026-10-02", "ore": "4.00", "descrizioni": ["API"], "fatture": []},
        ],
        "per_settimana": [
            {"settimana": "2026-W40", "da": "2026-09-28", "a": "2026-10-04", "ore": "12.00"}
        ],
        "per_mese": [{"mese": "2026-10", "ore": "12.00"}],
        "fatture": [
            {
                "numero": "12/2026",
                "tipo": "fattura",
                "data": "2026-10-31",
                "stato": "emessa",
                "stato_pagamento": "da_incassare",
                "ore": "8.00",
            }
        ],
    }
    [(method, url, _headers, _sent)] = pigro.calls
    assert (method, url) == (
        "GET",
        f"{PIGRO}/api/rebase/engagements/{match_id}/report?da=2026-10-01&a=2026-10-31",
    )


def test_get_report_of_unlinked_match_is_409(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
    api_session: Session,
) -> None:
    match_id = _signed_match(client, sender, api_session)

    refused = client.get(f"/api/hub/matches/{match_id}/report")

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Pigro non ha ancora il deal: riprova o aspetta lo sweep."
    assert pigro.calls == []


def test_get_report_when_pigro_is_down_is_502(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
    api_session: Session,
) -> None:
    match_id = _signed_match(client, sender, api_session, "collegato", pigro_url=DEAL_URL)
    pigro.answers = [ConnectionRefusedError("refused")]

    answered = client.get(f"/api/hub/matches/{match_id}/report")

    assert answered.status_code == 502
    assert answered.json()["detail"] == NOT_ANSWERING


def test_routes_answer_503_without_the_token(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    api_session: Session,
) -> None:
    """No `REBASE_PIGRO_ENGAGEMENTS_TOKEN` on this environment: both routes say so, the
    CRM is not asked, and the match is left as it was, waiting for the sweep. Its card
    says the same sentence (spec § 3.2) on every read, and offers no «Riprova», which
    would only answer this 503."""
    fake = RecordedPigro([(201, linked_body())])
    client.app.dependency_overrides[get_http_call] = lambda: fake  # type: ignore[attr-defined]
    waiting = _signed_match(client, sender, api_session)
    _with_signed_copy(api_session, waiting)

    for answered in (
        client.post(f"/api/hub/matches/{waiting}/pigro/link"),
        client.get(f"/api/hub/matches/{waiting}/report"),
    ):
        assert answered.status_code == 503
        assert answered.json()["detail"] == PIGRO_NOT_CONFIGURED

    assert fake.calls == []
    read = client.get(f"/api/hub/matches/{waiting}").json()
    assert (read["pigro_stato"], read["pigro_attempted_at"]) == ("da_collegare", None)
    assert read["situazione"].endswith(f". {PIGRO_NOT_CONFIGURED}")
    assert read["altre_azioni"] == ["chiudi"]
    [card] = client.get(f"/api/hub/freelancers/{read['freelancer_id']}/matches").json()["matches"]
    assert (card["situazione"], card["altre_azioni"]) == (read["situazione"], ["chiudi"])
    [row] = client.get("/api/hub/matches").json()["items"]
    assert row["situazione"] == read["situazione"]


def test_with_the_token_the_card_offers_riprova(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
    api_session: Session,
) -> None:
    waiting = _signed_match(client, sender, api_session)
    _with_signed_copy(api_session, waiting)

    read = client.get(f"/api/hub/matches/{waiting}").json()

    assert read["situazione"].endswith(" Pigro non ha ancora il deal: riprova o aspetta lo sweep.")
    assert read["altre_azioni"] == ["chiudi", "riprova_pigro"]


def test_the_link_reads_through_the_long_seam_in_production(api_session: Session) -> None:
    """Production's ten-second `urllib_call` becomes `urllib_engagements_call` for the
    link and the report, whose 90 seconds leave room for a new space to be opened, in
    both places the API builds the engagement service; a test's fake passes through
    unchanged."""
    fake = RecordedPigro([(201, linked_body())])
    assert _engagements_call(urllib_call) is urllib_engagements_call
    assert _engagements_call(fake) is fake

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    for http, expected in ((urllib_call, urllib_engagements_call), (fake, fake)):
        engagements = get_engagements(api_session, settings, http, None)
        signing = get_signing_factory(settings, FakeRenderer(), None, None, http)(api_session)
        assert engagements.http is expected
        assert signing.engagements is not None
        assert signing.engagements.http is expected
