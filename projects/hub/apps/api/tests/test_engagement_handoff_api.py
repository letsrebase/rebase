"""REB-1105 over HTTP: «Crea match» hands the pair over to the CRM. The CRM finds or writes the
freelancer's organization by the hub id, the hub keeps one `handoff` row under the minted engagement
id with the company's budget snapshot, and the answer is the CRM's engagement form, filled in."""

import json
from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from contract_api_helpers import (
    ADMIN_EMAIL,
    BUDGET,
    TABLES,
    apply_freelancer,
    login,
    request_company,
)
from fakes_pigro import CUSTOMER, DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from rebase_api.deps import get_freelancer_door
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Company, HubEngagement, User
from rebase_core.pigro_door import freelancer_door

ORGANIZATION = UUID("0197f0b2-0000-7000-8000-000000000001")


def _organization_answer(*, created: bool = True) -> bytes:
    return json.dumps(
        {"organization_id": str(ORGANIZATION), "created": created, "joined": False}
    ).encode()


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in ("hub_engagements", *TABLES):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _crm(
    client: TestClient, *answers: tuple[int, bytes] | Exception, token: str = TOKEN
) -> RecordedPigro:
    fake = RecordedPigro(list(answers) or [(200, _organization_answer())])
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url=PIGRO, pigro_engagements_token=token
    )
    client.app.dependency_overrides[get_freelancer_door] = lambda: freelancer_door(settings, fake)  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    return fake


def _linked(session: Session, company_id: str) -> None:
    session.execute(
        update(Company)
        .where(Company.id == UUID(company_id))
        .values(
            crm_status="linked",
            crm_deal_id=DEAL,
            crm_deal_url=DEAL_URL,
            crm_customer_id=CUSTOMER,
        )
    )
    session.commit()


def _hand_off(client: TestClient, freelancer: str, company: str) -> object:
    return client.post(
        f"/api/hub/freelancers/{freelancer}/engagement-handoff", json={"company_id": company}
    )


def _rows(session: Session) -> list[HubEngagement]:
    session.expire_all()
    return list(session.scalars(select(HubEngagement)))


def test_the_route_wants_the_admin_cookie(client: TestClient, admin: None) -> None:
    answer = client.post(
        f"/api/hub/freelancers/{uuid4()}/engagement-handoff", json={"company_id": str(uuid4())}
    )
    assert answer.status_code == 401


def test_the_pair_is_handed_over_and_the_admin_gets_the_crm_form_filled_in(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    crm = _crm(client)

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["state"] == "ready"
    (row,) = _rows(api_session)
    assert (row.state, row.sequence, str(row.freelancer_id), str(row.company_id)) == (
        "handoff",
        0,
        freelancer,
        company,
    )
    assert row.company_daily_budget == Decimal(BUDGET)
    assert body["engagement_id"] == str(row.id)
    parts = urlsplit(body["url"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{PIGRO}/ada-lovelace/app/engagements"
    assert {key: values[0] for key, values in parse_qs(parts.query).items()} == {
        "hub_match_id": str(row.id),
        "freelancer_id": str(ORGANIZATION),
        "deal_id": str(DEAL),
        "client_id": str(CUSTOMER),
    }
    # The CRM was asked for the organization by the hub's id, from the card, never by name.
    ((method, url, headers, raw),) = crm.calls
    assert (method, url) == ("PUT", f"{PIGRO}/api/rebase/freelancers/{freelancer}/organization")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    sent = json.loads(raw)
    assert sent["legal_name"] == "Ada Lovelace" and sent["email"] == "ada@studio.it"


def test_opening_the_same_pair_again_reuses_the_row_and_its_id(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    _crm(client, (200, _organization_answer()), (200, _organization_answer(created=False)))

    first = _hand_off(client, freelancer, company).json()
    api_session.execute(
        update(Company).where(Company.id == UUID(company)).values(daily_budget=Decimal("500"))
    )
    api_session.commit()
    second = _hand_off(client, freelancer, company).json()

    assert first["engagement_id"] == second["engagement_id"]
    assert first["url"] == second["url"]
    (row,) = _rows(api_session)
    # The snapshot is the budget as it stood at the last opening.
    assert row.company_daily_budget == Decimal("500")


def test_a_pair_whose_engagement_the_crm_already_noticed_gets_a_new_id(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    _crm(client)
    first = _hand_off(client, freelancer, company).json()
    api_session.execute(update(HubEngagement).values(state="draft", sequence=1))
    api_session.commit()

    second = _hand_off(client, freelancer, company).json()

    assert second["engagement_id"] != first["engagement_id"]
    assert sorted(row.state for row in _rows(api_session)) == ["draft", "handoff"]


def test_a_request_not_in_the_crm_is_an_answer_and_writes_nothing(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    crm = _crm(client)

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 200, answer.text
    assert answer.json()["state"] == "no_deal"
    assert answer.json()["url"] is None and answer.json()["engagement_id"] is None
    assert "CRM" in answer.json()["message"]
    assert crm.calls == [] and _rows(api_session) == []


def test_the_crm_refusing_the_card_is_a_409_with_its_sentence_and_leaves_no_row(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    _crm(client, (409, b'{"detail": "la partita IVA e\' gia\' di un altro freelance dell\'hub"}'))

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 409, answer.text
    assert "partita IVA" in answer.json()["detail"]
    assert _rows(api_session) == []


def test_a_crm_that_does_not_answer_is_a_502_and_leaves_no_row(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    _crm(client, (503, b'{"detail": "not now"}'))

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 502, answer.text
    assert _rows(api_session) == []


def test_without_the_engagements_token_the_route_says_so(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    crm = _crm(client, token="")

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 503, answer.text
    assert crm.calls == [] and _rows(api_session) == []


def test_an_unknown_card_or_request_is_a_404(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    _crm(client)

    assert _hand_off(client, str(uuid4()), company).status_code == 404
    assert _hand_off(client, freelancer, str(uuid4())).status_code == 404


def test_a_deal_link_on_another_origin_than_the_configured_crm_is_no_deal(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    login(client, sender)
    freelancer, company = apply_freelancer(client), request_company(client)
    _linked(api_session, company)
    api_session.execute(
        update(Company)
        .where(Company.id == UUID(company))
        .values(crm_deal_url=DEAL_URL.replace("pigro.test", "altro.example"))
    )
    api_session.commit()
    crm = _crm(client)

    answer = _hand_off(client, freelancer, company)

    assert answer.status_code == 200, answer.text
    assert answer.json()["state"] == "no_deal" and answer.json()["url"] is None
    assert crm.calls == [] and _rows(api_session) == []
