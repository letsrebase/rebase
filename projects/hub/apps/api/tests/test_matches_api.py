"""REB-387 phase 2 over HTTP: tax data, a match created from a card and a request, both
PDFs downloadable, a draft cancelled; nothing at all without the admin cookie."""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from contract_api_helpers import (
    ADMIN_EMAIL,
    BUDGET,
    CUSTOMER,
    DAY_RATE_LETTER,
    FISCAL,
    LETTER,
    MISSING,
    PDF,
    TABLES,
    apply_freelancer,
    apply_member,
    login,
    login_as,
    ready,
    request_company,
)
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
from rebase_core.audit import utcnow
from rebase_core.config import Settings, get_settings
from rebase_core.http import urllib_call, urllib_engagements_call
from rebase_core.mail import RecordingSender
from rebase_core.match_words import PIGRO_NOT_CONFIGURED
from rebase_core.models import AdminAction, Company, ContractDocument, Match, User
from rebase_core.pigro_door import NOT_ANSWERING
from rebase_core.referrals import ReferralService


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer()
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


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
    login(client, sender)
    freelancer_id, company_id = apply_freelancer(client), request_company(client)
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/fiscal").json() is None
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["vat_number"] == "01234567890"

    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.status_code == 200, prefill.text
    assert prefill.json()["letter"]["fee"] == "450.00"
    assert prefill.json()["required_framework"] is True
    assert BUDGET not in prefill.text

    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 201, created.text
    match = created.json()
    letter = match["letter"]
    assert match["status"] == "draft"
    assert re.fullmatch(r"\d{4}-001", letter["number"])
    assert letter["status"] == "pending"
    assert BUDGET not in created.text

    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["framework"]["status"] == "generated"
    assert [item["id"] for item in page["matches"]] == [match["id"]]
    assert page["tax"]["tax_code"] == "LVLDAA85T50H501Z"
    assert (
        client.get(f"/api/hub/matches/{match['id']}").json()["letter"]["number"] == letter["number"]
    )

    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert f"lettera-di-incarico-{letter['number']}.pdf" in pdf.headers["content-disposition"]
    assert (
        client.get(f"/api/hub/contract-documents/{page['framework']['id']}/pdf").status_code == 200
    )
    signed = client.get(
        f"/api/hub/contract-documents/{letter['id']}/pdf", params={"signed": "true"}
    )
    assert signed.status_code == 404


def test_a_repeated_create_with_the_same_id_writes_the_match_once(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: the wizard's own retry after a lost response sends the same
    client-generated id again with «Salva senza inviare» or «Invia per la firma», and
    gets the match already written back, never a second one with another letter
    number."""
    freelancer_id, company_id = ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    second = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"] == given_id
    assert first.json()["letter"]["number"] == second.json()["letter"]["number"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_same_id_used_by_another_freelancers_match_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 201, created.text

    apply_member(client, "grace@studio.it")
    other_freelancer_id = str(client.get("/api/hub/freelancers").json()["items"][0]["id"])
    assert (
        client.put(f"/api/hub/freelancers/{other_freelancer_id}/fiscal", json=FISCAL).status_code
        == 200
    )

    refused = client.post(
        f"/api/hub/freelancers/{other_freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
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
    freelancer_id, company_id = ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    committed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert committed.status_code == 201, committed.text

    apply_member(client, "grace@studio.it")
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
        json={"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )

    assert refused.status_code == 409, refused.text
    assert "un altro freelance" in refused.json()["detail"]


def test_a_repeated_create_with_the_same_id_and_changed_data_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: a retry that corrects the letter or the client before sending it again
    must not be handed back the stale match under cover of the idempotent id -- the
    request no longer matches what was saved, so this is a 409, not a 201."""
    freelancer_id, company_id = ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "customer": CUSTOMER, "letter": LETTER}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    assert first.status_code == 201, first.text

    changed = {**body, "letter": {**LETTER, "role": "Un altro ruolo"}}
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=changed)

    assert refused.status_code == 409, refused.text
    assert "dati diversi" in refused.json()["detail"]
    assert "Match e contratti" in refused.json()["detail"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_preview_renders_a_document_without_saving_or_numbering_it(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    body = {"company_id": company_id, "customer": CUSTOMER, "letter": LETTER}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"document": "letter"},
        json=body,
    )
    assert preview.status_code == 200, preview.text
    assert preview.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "engagement-letter"
    assert renderer.calls[-1][1]["numero"] is None
    framework = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"document": "framework"},
        json=body,
    )
    assert framework.status_code == 200
    assert renderer.calls[-1][0] == "framework-agreement"
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_a_match_without_tax_data_names_the_step_that_is_missing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    login(client, sender)
    freelancer_id, company_id = apply_freelancer(client), request_company(client)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 422
    assert created.json()["detail"][0]["loc"] == ["body", "tax"]


def test_a_closed_request_and_a_fee_the_law_refuses_are_422s_on_their_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    over = {**LETTER, "payment_days": 45}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": over},
    )
    assert refused.status_code == 422
    assert (
        client.patch(f"/api/hub/companies/{company_id}", json={"status": "closed"}).status_code
        == 200
    )
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_an_end_date_before_the_start_names_end_date(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    backwards = {**LETTER, "end_date": "2026-09-01"}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": backwards},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "end_date"
    assert detail["msg"] == "la fine prevista viene prima dell'inizio"


@pytest.mark.parametrize("mode", ["a giornata (non è a corpo)", "A giornata", "giornaliera", ""])
def test_a_payment_mode_outside_the_two_choices_is_refused(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer, mode: str
) -> None:
    freelancer_id, company_id = ready(client, sender)
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={
            "company_id": company_id,
            "customer": CUSTOMER,
            "letter": {**LETTER, "mode": mode},
        },
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "mode"
    assert detail["msg"] == "la modalità di pagamento è «a giornata» oppure «a corpo»"


def test_a_letter_without_a_payment_mode_is_refused(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    letter = {key: value for key, value in LETTER.items() if key != "mode"}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": letter},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "mode"


def test_to_payment_term_past_thirty_days_from_month_end_names_payment_days(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    over = {**LETTER, "payment_days": 45, "month_end": True}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": over},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "payment_days"
    assert (
        detail["msg"]
        == "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)"
    )


def test_a_draft_is_cancelled_once_and_only_an_active_match_closes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    ).json()
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 409
    cancelled = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["status"], cancelled.json()["letter"]["status"]) == (
        "cancelled",
        "cancelled",
    )
    again = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert again.status_code == 409
    assert "bozza" in again.json()["detail"]


def test_a_soft_deleted_freelancer_hides_all_four_match_routes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    ).json()
    letter_id = match["letter"]["id"]
    assert client.delete(f"/api/hub/freelancers/{freelancer_id}").status_code == 200

    assert client.get(f"/api/hub/matches/{match['id']}").status_code == 404
    assert client.get(f"/api/hub/contract-documents/{letter_id}/pdf").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/cancel").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 404


def test_a_render_that_fails_is_a_503_with_a_sentence(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    client.app.dependency_overrides[get_renderer] = lambda: FailingRenderer()  # type: ignore[attr-defined]
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 503
    assert created.json()["detail"].startswith("La generazione del contratto non è riuscita")
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_without_the_cookie_the_match_list_is_a_401(client: TestClient, admin: None) -> None:
    assert client.get("/api/hub/matches").status_code == 401


def test_a_signed_in_member_hitting_the_match_list_is_403_not_401(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    apply_member(client, "bob@studio.it")
    login_as(client, sender, "bob@studio.it")
    assert client.get("/api/hub/matches").status_code == 403


def test_the_match_list_projects_a_referral_without_ever_answering_the_budget(
    client: TestClient,
    api_session: Session,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
) -> None:
    """The projection is the first code on this list that reads the client's budget
    (`company_daily_budget`, through `reward_base`): the figure it yields may
    travel, the budget and the base it is a share of may not."""
    freelancer_id, company_id = ready(client, sender)
    referrer = User(
        email="mario@community.it", first_name="Mario", last_name="Rossi", role="member"
    )
    api_session.add(referrer)
    api_session.commit()
    service = ReferralService(api_session)
    service.link_signup(
        "company", UUID(company_id), service.code_for(referrer.id), new_user_id=UUID(MISSING)
    )
    # The referente has logged in since: a pending company referral projects nothing (REB-658).
    company = api_session.get(Company, UUID(company_id))
    assert company is not None
    service.verify_on_login(company.user_id, utcnow())
    api_session.commit()
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": DAY_RATE_LETTER},
    )
    assert created.status_code == 201, created.text

    listed = client.get("/api/hub/matches")

    assert listed.status_code == 200, listed.text
    (row,) = listed.json()["items"]
    (side,) = row["referrals"]
    # (777.77 - 450) * 1 day at 30%: with no estimate of days a day-rate letter earns one.
    assert side == {
        "kind": "company",
        "referrer_name": "Mario Rossi",
        "referrer_freelancer_id": None,
        "rate": "0.3000",
        "amount": "98.33",
        "status": "expected",
        "reward_id": None,
    }
    assert row["company_id"] == company_id
    assert (row["letter_fee"], row["unit_letter"]) == ("450.00", "to_work_unit")
    assert BUDGET not in listed.text
    assert "327.77" not in listed.text
    assert not any("budget" in key or "base" in key for key in row)


def test_the_match_list_answers_200_with_filters_and_carries_no_budget_or_tax_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 201, created.text
    match = created.json()

    listed = client.get("/api/hub/matches")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [match["id"]]
    row = body["items"][0]
    assert row["company_name"] == "ACME Srl"
    assert row["number_letter"] == match["letter"]["number"]
    assert BUDGET not in listed.text
    assert not any(key in row for key in ("tax_code", "vat_number", "domicile", "certified_email"))

    by_state = client.get("/api/hub/matches", params={"status": "draft"})
    assert by_state.status_code == 200
    assert by_state.json()["total"] == 1
    by_wrong_state = client.get("/api/hub/matches", params={"status": "active"})
    assert by_wrong_state.json()["total"] == 0

    by_search = client.get("/api/hub/matches", params={"q": "ACME"})
    assert [item["id"] for item in by_search.json()["items"]] == [match["id"]]
    by_missing_search = client.get("/api/hub/matches", params={"q": "nessuno"})
    assert by_missing_search.json()["items"] == []


def test_the_match_list_422s_on_an_unknown_state(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    login(client, sender)
    refused = client.get("/api/hub/matches", params={"status": "chissà"})
    assert refused.status_code == 422


def test_a_malformed_signer_setting_is_a_503_not_a_crash(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,
        signer_json="{non json",  # type: ignore[call-arg]
    )
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert created.status_code == 503
    assert "REBASE_SIGNER_JSON" in created.json()["detail"]


# ---- what a match is doing, and the check before saving (REB-476, REB-477) -------------


def test_a_match_and_its_documents_say_what_they_are_doing_and_what_comes_next(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    ).json()
    number = match["letter"]["number"]
    draft = f"La lettera n. {number} è pronta: il freelance non ha ancora ricevuto nulla."
    assert (match["situation"], match["next_action"], match["other_actions"]) == (
        draft,
        "send",
        ["cancel"],
    )
    assert match["letter"]["situation"] == "Parte da sola dopo la firma del contratto quadro."
    framework = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["framework"]
    assert (framework["next_action"], framework["other_actions"]) == (None, ["cancel"])
    assert framework["situation"].startswith("Parte con «Invia per la firma» sul suo match.")
    (row,) = client.get("/api/hub/matches").json()["items"]
    assert row["situation"] == draft


def test_the_check_says_what_saving_would_do_and_writes_nothing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    checked = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json() == {
        "recap": [
            "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
            "Compenso: 450,00 € a corpo, IVA esclusa, pagato a 30 giorni fine mese.",
        ],
        "what_happens": (
            "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua "
            "firma."
        ),
        "required_framework": True,
        "missing_tax_data": False,
        "regime": "vat_registered",
    }
    assert BUDGET not in checked.text
    assert renderer.calls == []
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert (page["framework"], page["matches"]) == (None, [])


def test_the_check_reports_missing_tax_data_instead_of_refusing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    login(client, sender)
    freelancer_id, company_id = apply_freelancer(client), request_company(client)
    checked = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["missing_tax_data"] is True
    assert checked.json()["recap"][-1] == (
        "Mancano i dati fiscali del freelance: servono prima di salvare."
    )


def test_the_check_is_a_422_naming_the_field_as_create_is(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = ready(client, sender)
    over = {**LETTER, "payment_days": 45, "month_end": True}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": over},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "payment_days"
    assert (
        client.patch(f"/api/hub/companies/{company_id}", json={"status": "closed"}).status_code
        == 200
    )
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/check",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_without_the_cookie_the_check_is_a_401(client: TestClient, admin: None) -> None:
    answered = client.post(
        f"/api/hub/freelancers/{MISSING}/matches/check",
        json={"company_id": MISSING, "customer": CUSTOMER, "letter": LETTER},
    )
    assert answered.status_code == 401


# ---- the link to Pigro and the report (REB-499) ------------------------------------------

INVOICE = {
    "id": "0192e0a0-0000-7000-8000-0000000f0012",
    "type": "invoice",
    "year": 2026,
    "number": 12,
    "status": "issued",
    "payment_status": "unpaid",
    "date": "2026-10-31",
}
# The CRM's report of the deal (its door's `GET .../report`): one row per time entry.
CRM_REPORT = {
    "slug": "ada-lovelace",
    "deal_url": DEAL_URL,
    "deal": {"id": str(DEAL), "name": "Lettera n. 2026-001", "status": "in_progress"},
    "days": [
        {"date": "2026-10-01", "hours": "8.00", "description": "Setup", "invoice": INVOICE},
        {"date": "2026-10-02", "hours": "4.00", "description": "API", "invoice": None},
    ],
    "total_hours": "12.00",
    "billed_hours": "8.00",
    "unbilled_hours": "4.00",
    "invoices": [{**INVOICE, "hours": "8.00"}],
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
    pigro_status: str | None = "to_link",
    **columns: Any,
) -> str:
    """A match as a signature leaves it: made over HTTP with 40 expected days, then its
    letter signed and the match active in the database, since how a letter gets signed
    is `test_signing_api.py`'s business."""
    freelancer_id, company_id = ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={
            "company_id": company_id,
            "customer": CUSTOMER,
            "letter": LETTER,
            "expected_days": 40,
        },
    )
    assert created.status_code == 201, created.text
    match_id = UUID(created.json()["id"])
    session.execute(
        update(ContractDocument)
        .where(ContractDocument.match_id == match_id)
        .values(status="signed", signed_at=datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    )
    session.execute(
        update(Match)
        .where(Match.id == match_id)
        .values(status="active", pigro_status=pigro_status, **columns)
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
    assert (body["id"], body["status"], body["pigro_status"]) == (match_id, "active", "linked")
    assert (body["pigro_slug"], body["pigro_deal_id"], body["pigro_url"]) == (
        "ada-lovelace",
        str(DEAL),
        DEAL_URL,
    )
    assert body["pigro_error"] is None
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
    assert action.payload == {"outcome": "linked", "error": None}


def test_post_pigro_link_of_a_draft_is_409(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    pigro: RecordedPigro,
) -> None:
    freelancer_id, company_id = ready(client, sender)
    draft = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "customer": CUSTOMER, "letter": LETTER},
    ).json()

    refused = client.post(f"/api/hub/matches/{draft['id']}/pigro/link")

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Si collega a Pigro solo un match attivo."
    assert pigro.calls == []
    assert client.get(f"/api/hub/matches/{draft['id']}").json()["pigro_status"] is None


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
        client, sender, api_session, "linked", pigro_url=DEAL_URL, pigro_deal_id=DEAL
    )
    pigro.answers = [(200, json.dumps(CRM_REPORT).encode())]

    answered = client.get(
        f"/api/hub/matches/{match_id}/report", params={"from": "2026-10-01", "to": "2026-10-31"}
    )

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "match_id": match_id,
        "pigro_url": DEAL_URL,
        "pigro_status": "linked",
        "expected_days": 40,
        "expected_hours": "320.00",
        "hours_total": "12.00",
        "equivalent_days": "1.50",
        "progress": "3.75",
        "invoiced_hours": "8.00",
        "hours_uninvoiced": "4.00",
        "by_day": [
            {
                "date": "2026-10-01",
                "hours": "8.00",
                "descriptions": ["Setup"],
                "invoices": ["12/2026"],
                "hours_by_invoice": [{"number": "12/2026", "kind": "invoice", "hours": "8.00"}],
            },
            {
                "date": "2026-10-02",
                "hours": "4.00",
                "descriptions": ["API"],
                "invoices": [],
                "hours_by_invoice": [],
            },
        ],
        "by_week": [
            {
                "week": "2026-W40",
                "from_date": "2026-09-28",
                "to_date": "2026-10-04",
                "hours": "12.00",
            }
        ],
        "by_month": [{"month": "2026-10", "hours": "12.00"}],
        "invoices": [
            {
                "number": "12/2026",
                "kind": "invoice",
                "date": "2026-10-31",
                "status": "issued",
                "payment_status": "unpaid",
                "hours": "8.00",
            }
        ],
    }
    [(method, url, _headers, _sent)] = pigro.calls
    assert (method, url) == (
        "GET",
        f"{PIGRO}/api/rebase/engagements/{match_id}/report?from=2026-10-01&to=2026-10-31",
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
    match_id = _signed_match(client, sender, api_session, "linked", pigro_url=DEAL_URL)
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
    assert (read["pigro_status"], read["pigro_attempted_at"]) == ("to_link", None)
    assert read["situation"].endswith(f". {PIGRO_NOT_CONFIGURED}")
    assert read["other_actions"] == ["close"]
    [card] = client.get(f"/api/hub/freelancers/{read['freelancer_id']}/matches").json()["matches"]
    assert (card["situation"], card["other_actions"]) == (read["situation"], ["close"])
    [row] = client.get("/api/hub/matches").json()["items"]
    assert row["situation"] == read["situation"]


def test_with_the_token_the_card_offers_retry(
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

    assert read["situation"].endswith(" Pigro non ha ancora il deal: riprova o aspetta lo sweep.")
    assert read["other_actions"] == ["close", "retry_pigro"]


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
        assert engagements.door.http is expected
        assert signing.engagements is not None
        assert signing.engagements.door.http is expected


@pytest.mark.parametrize("vat_number", [None, "", "   "])
def test_tax_data_may_be_saved_without_a_vat_number(
    client: TestClient, admin: None, sender: RecordingSender, vat_number: str | None
) -> None:
    """REB-687: omitted, null or blank is no partita IVA; given, it still has 11 digits."""
    login(client, sender)
    freelancer_id = apply_freelancer(client)
    url = f"/api/hub/freelancers/{freelancer_id}/fiscal"
    saved = client.put(url, json={**FISCAL, "vat_number": vat_number})
    assert saved.status_code == 200, saved.text
    assert saved.json()["vat_number"] is None
    omitted = client.put(url, json={k: v for k, v in FISCAL.items() if k != "vat_number"})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["vat_number"] is None
    short = client.put(url, json={**FISCAL, "vat_number": "0123"})
    assert short.status_code == 422
    assert short.json()["detail"][0]["loc"][-1] == "vat_number"


def test_to_match_for_to_member_without_to_vat_number_is_occasional_end_to_end(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-687 over HTTP: the prefill proposes the occasional letter, the ordinary one is a
    422 on `regime` with the spec's sentence, and the occasional match reads and downloads
    as its own type."""
    freelancer_id, company_id = ready(client, sender)
    fiscal = client.put(
        f"/api/hub/freelancers/{freelancer_id}/fiscal", json={**FISCAL, "vat_number": None}
    )
    assert fiscal.status_code == 200, fiscal.text
    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.json()["regime"] == "occasional"
    body = {"company_id": company_id, "customer": CUSTOMER, "letter": LETTER}
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    assert refused.status_code == 422
    assert refused.json()["detail"] == [
        {
            "loc": ["body", "regime"],
            "msg": 'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione '
            'occasionale" o salva la partita IVA.',
            "type": "value_error",
        }
    ]
    occasional = {**body, "regime": "occasional"}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"document": "letter"},
        json=occasional,
    )
    assert preview.status_code == 200, preview.text
    assert "anteprima-lettera-di-incarico-occasionale.pdf" in preview.headers["content-disposition"]
    created = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=occasional)
    assert created.status_code == 201, created.text
    letter = created.json()["letter"]
    assert (letter["regime"], letter["label"]) == ("occasional", "Lettera di incarico occasionale")
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["framework"] is None
    assert page["occasional_framework"]["label"] == "Contratto quadro per prestazioni occasionali"
    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert (
        f"lettera-di-incarico-occasionale-{letter['number']}.pdf"
        in (pdf.headers["content-disposition"])
    )
