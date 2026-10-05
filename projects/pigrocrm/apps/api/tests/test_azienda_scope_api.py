"""The azienda boundary over HTTP, as the application role sees it (REB-634, spec
2026-10-03 §3, §4).

Every other file in this tree talks to Postgres as the suite's superuser, which is
outside every row-level policy by Postgres's own rule. This one builds a committed
two-azienda world as that superuser and then serves requests through sessions opened as
the role the boot creates (`db/role.py`): `LOGIN NOSUPERUSER NOBYPASSRLS`, which is what
production's `pigrocrm_app` is. A scoped member's cookie must then get their azienda's
rows and nothing else from every list, every dashboard, the search and a read by id, and
a write the policy refuses must come back as a 404 and never as a 500.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.invitation_models import Invitation
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.refresh_models import RefreshToken
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import Cost, CostCategory
from pigrocrm_api import deps
from pigrocrm_api.deps import get_storage
from pigrocrm_api.main import create_app
from pigrocrm_api.sessions import get_sender

_PREFIX = "ScopeApi"
PASSWORD = "supersegreta1"
ADMIN = f"{_PREFIX.lower()}-admin@pigro.it"
MEMBER = f"{_PREFIX.lower()}-member@pigro.it"
INVITED = f"{_PREFIX.lower()}-invited@pigro.it"
# The same role the core boundary test creates: cluster-wide, so whichever file asks
# first makes it and the other only re-applies the grants on its own database.
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
OGGI = date(2026, 3, 10)


class Rows(NamedTuple):
    customer: UUID
    deal: UUID
    contract: UUID
    document: UUID
    invoice: UUID


class ScopedWorld(NamedTuple):
    engine: Engine  # the superuser's
    app: Engine  # the application role's, same database
    studio: UUID
    ltd: UUID
    admin_id: UUID
    member_id: UUID
    studio_rows: Rows
    ltd_rows: Rows
    category: UUID


def _seed(session: Session, azienda: UUID, tag: str, stage: PipelineStage) -> Rows:
    customer = Customer(
        ragione_sociale=f"{_PREFIX} {tag}", nazione="IT", custom_fields={}, azienda_id=azienda
    )
    session.add(customer)
    session.flush()
    deal = Deal(
        nome=f"{_PREFIX} {tag} deal",
        customer_id=customer.id,
        azienda_id=azienda,
        pipeline_stage_id=stage.id,
        probabilita=20,
        valore_previsto=Decimal("1000.00"),
        custom_fields={},
    )
    contract = Contract(
        customer_id=customer.id,
        azienda_id=azienda,
        titolo=f"{_PREFIX} {tag} contratto",
        inizio=OGGI,
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"tipo": "non_rimborsabile"},
        custom_fields={},
    )
    document = Document(
        customer_id=customer.id,
        azienda_id=azienda,
        tipo="offerta",
        titolo=f"{_PREFIX} {tag} offerta",
        stato="inviata",
        stato_dal=OGGI,
        versione_corrente=1,
        custom_fields={},
    )
    session.add_all([deal, contract, document])
    session.flush()
    invoice = Invoice(
        customer_id=customer.id,
        azienda_id=azienda,
        deal_id=deal.id,
        tipo="fattura",
        stato="emessa",
        stato_pagamento="da_incassare",
        anno=2026,
        numero=1,
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("100.00"),
        data_emissione=OGGI,
        data_scadenza=OGGI,
        causale=f"{_PREFIX} {tag} fattura",
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            numero_linea=1,
            descrizione="Consulenza",
            quantita=Decimal("1.000000"),
            prezzo_unitario=Decimal("100.00"),
            prezzo_totale=Decimal("100.00"),
            aliquota_iva=Decimal("0.00"),
            natura="N2.2",
        )
    )
    return Rows(customer.id, deal.id, contract.id, document.id, invoice.id)


@pytest.fixture
def world(api_engine: Engine) -> Iterator[ScopedWorld]:
    """Two aziende with one of everything each, an admin who sees the whole space and a
    collaboratore scoped to «ltd», all committed; and the application role on the
    worker's database. Undone row by row at the end, since this file writes for real."""
    factory = session_factory(api_engine)
    with factory() as session:
        studio = session.execute(select(Azienda).where(Azienda.predefinita.is_(True))).scalar_one()
        ltd = Azienda(nome=f"{_PREFIX} ltd", ragione_sociale=f"{_PREFIX} Ltd", nazione="GB")
        stage = PipelineStage(
            nome=f"{_PREFIX} aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        category = CostCategory(nome=f"{_PREFIX} categoria")
        session.add_all([ltd, stage, category])
        session.flush()
        studio_rows = _seed(session, studio.id, "studio", stage)
        ltd_rows = _seed(session, ltd.id, "ltd", stage)
        session.commit()
        users = UserService(session)
        admin = users.create(
            UserCreate(email=ADMIN, password=PASSWORD, nome=f"{_PREFIX} Admin", ruolo="admin"),
            Actor.system(),
        )
        member = users.create(
            UserCreate(
                email=MEMBER, password=PASSWORD, nome=f"{_PREFIX} Member", ruolo="collaboratore"
            ),
            Actor.system(),
        )
        users.update(member.id, UserUpdate(aziende=[ltd.id]), Actor.system())
        ids = ScopedWorld(
            api_engine,
            _application_engine(api_engine),
            studio.id,
            ltd.id,
            admin.id,
            member.id,
            studio_rows,
            ltd_rows,
            category.id,
        )
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            everything = [
                *studio_rows,
                *ltd_rows,
                admin.id,
                member.id,
                ltd.id,
                stage.id,
                category.id,
            ]
            session.execute(delete(Activity).where(Activity.entity_id.in_(everything)))
            session.execute(delete(Activity).where(Activity.actor_id.in_([admin.id, member.id])))
            session.execute(delete(Invitation).where(Invitation.email == INVITED))
            session.execute(
                delete(RefreshToken).where(RefreshToken.user_id.in_([admin.id, member.id]))
            )
            session.execute(delete(Cost).where(Cost.category_id == category.id))
            for rows in (studio_rows, ltd_rows):
                session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == rows.invoice))
                session.execute(delete(Invoice).where(Invoice.id == rows.invoice))
                session.execute(delete(Document).where(Document.id == rows.document))
                session.execute(delete(Contract).where(Contract.id == rows.contract))
                session.execute(delete(Deal).where(Deal.id == rows.deal))
                session.execute(delete(Customer).where(Customer.id == rows.customer))
            session.execute(delete(User).where(User.id.in_([admin.id, member.id])))
            session.execute(delete(PipelineStage).where(PipelineStage.id == stage.id))
            session.execute(delete(CostCategory).where(CostCategory.id == category.id))
            session.execute(delete(Azienda).where(Azienda.id == ltd.id))
            session.commit()


def _application_engine(engine: Engine) -> Engine:
    app_url = engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
    assert ensure_application_role(engine.url, app_url) is True
    return create_engine(app_url, future=True)


@pytest.fixture
def served(
    world: ScopedWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """The app, every session of it opened as the application role: one per request,
    closed after it, the way production's pool hands them out. The two session
    dependencies run as written, `get_snapshot_session` with its own binding of the
    actor included; only the factory they draw from is this world's role."""
    factory = session_factory(world.app)
    monkeypatch.setattr(deps, "_factory_for", lambda request, settings: factory)

    app = create_app()
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(tmp_path)
    app.dependency_overrides[get_settings] = lambda: Settings(
        public_url="https://crm.example.test",
        _env_file=None,  # type: ignore[call-arg]
    )
    app.dependency_overrides[get_sender] = lambda: RecordingSender()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _login(client: TestClient, email: str) -> TestClient:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def _ids(client: TestClient, path: str, **params: Any) -> set[str]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


# --- the five lists, the search and a read by id ---------------------------------------


def test_a_scoped_member_lists_their_azienda_s_rows_and_nobody_else_s(
    served: TestClient, world: ScopedWorld
) -> None:
    client = _login(served, MEMBER)
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["aziende"] == [str(world.ltd)]
    ltd, studio = world.ltd_rows, world.studio_rows
    assert _ids(client, "/api/customers") == {str(ltd.customer)}
    assert _ids(client, "/api/deals") == {str(ltd.deal)}
    assert _ids(client, "/api/contracts") == {str(ltd.contract)}
    assert _ids(client, "/api/documents") == {str(ltd.document)}
    assert _ids(client, "/api/invoices") == {str(ltd.invoice)}
    # The aziende themselves: the selector of a scoped member has one entry.
    assert {a["id"] for a in client.get("/api/aziende").json()} == {str(world.ltd)}
    # A row of the other azienda is not there, whatever its id is.
    assert client.get(f"/api/customers/{studio.customer}").status_code == 404
    assert client.get(f"/api/invoices/{studio.invoice}").status_code == 404
    assert client.get(f"/api/aziende/{world.studio}").status_code == 404


def test_the_search_answers_inside_the_scope(served: TestClient, world: ScopedWorld) -> None:
    client = _login(served, MEMBER)
    response = client.get("/api/search", params={"q": _PREFIX})
    assert response.status_code == 200, response.text
    hits = {hit["id"] for group in response.json()["gruppi"] for hit in group["hits"]}
    # Five classes, and a contract is not one of them (spec 2026-09 §11.1).
    assert hits == {str(x) for x in world.ltd_rows} - {str(world.ltd_rows.contract)}
    assert all(group["totale"] <= 1 for group in response.json()["gruppi"])


def test_the_dashboards_sum_the_scope_alone(served: TestClient, world: ScopedWorld) -> None:
    """Three dashboards, three different reads of the same boundary: the pipeline counts
    deals, the receivables sum invoices, the economic one reads revenue through the
    deal's azienda. A member scoped to «ltd» gets one deal and one hundred euro, and the
    admin who sees everything gets two and two hundred."""
    client = _login(served, MEMBER)
    sales = client.get("/api/dashboard/sales")
    assert sales.status_code == 200, sales.text
    assert sum(stage["numero"] for stage in sales.json()["pipeline"]) == 1
    receivables = client.get("/api/dashboard/receivables")
    assert receivables.status_code == 200, receivables.text
    assert Decimal(receivables.json()["totale"]) == Decimal("100.00")
    economic = client.get("/api/dashboard/economic", params={"da": "2026-03-01", "a": "2026-03-31"})
    assert economic.status_code == 200, economic.text
    assert Decimal(economic.json()["pnl"]["in_corso"]["ricavi"]) == Decimal("100.00")


def test_the_admin_who_sees_everything_still_does(served: TestClient, world: ScopedWorld) -> None:
    client = _login(served, ADMIN)
    assert client.get("/api/auth/me").json()["aziende"] is None
    assert _ids(client, "/api/customers") == {
        str(world.studio_rows.customer),
        str(world.ltd_rows.customer),
    }
    sales = client.get("/api/dashboard/sales")
    assert sum(stage["numero"] for stage in sales.json()["pipeline"]) == 2
    receivables = client.get("/api/dashboard/receivables")
    assert Decimal(receivables.json()["totale"]) == Decimal("200.00")


# --- the writes -----------------------------------------------------------------------


def test_a_write_the_policy_refuses_is_a_404_and_never_a_500(
    served: TestClient, world: ScopedWorld
) -> None:
    """Two refusals with the same status. Moving a customer onto the other azienda is
    stopped by the service, which cannot resolve an azienda the policy hides. A cost
    without a deal and without an azienda is shared («tutte»'s alone, §1.7), and nothing
    in the service says a scoped member may not write one: Postgres does, with
    `insufficient_privilege`, and the API turns that into the same problem document a
    missing row gets instead of the 500 it was."""
    client = _login(served, MEMBER)
    moved = client.patch(
        f"/api/customers/{world.ltd_rows.customer}", json={"azienda_id": str(world.studio)}
    )
    assert moved.status_code == 404, moved.text
    shared = client.post(
        "/api/costs",
        json={
            "category_id": str(world.category),
            "data": "2026-03-10",
            "importo": "12.00",
            "descrizione": f"{_PREFIX} condiviso",
        },
    )
    assert shared.status_code == 404, shared.text
    problem = shared.json()
    assert problem["code"] == "not_found"
    assert "aziende" in problem["detail"]
    # A later request on the same client is served, by a fresh session: the refusal
    # poisoned nothing.
    assert _ids(client, "/api/customers") == {str(world.ltd_rows.customer)}


def test_the_scope_is_set_and_cleared_over_the_api_and_the_next_request_sees_it(
    served: TestClient, world: ScopedWorld
) -> None:
    admin = _login(served, ADMIN)
    widened = admin.patch(f"/api/users/{world.member_id}", json={"aziende": None})
    assert widened.status_code == 200, widened.text
    assert widened.json()["aziende"] is None
    member = _login(TestClient(served.app, base_url="https://testserver"), MEMBER)
    assert len(_ids(member, "/api/customers")) == 2
    narrowed = admin.patch(f"/api/users/{world.member_id}", json={"aziende": [str(world.ltd)]})
    assert narrowed.status_code == 200 and narrowed.json()["aziende"] == [str(world.ltd)]
    assert _ids(member, "/api/customers") == {str(world.ltd_rows.customer)}
    refused = admin.patch(f"/api/users/{world.member_id}", json={"aziende": []})
    assert refused.status_code == 422 and refused.json()["field"] == "aziende"


def test_an_invitation_carries_its_scope_and_the_list_shows_it(
    served: TestClient, world: ScopedWorld
) -> None:
    admin = _login(served, ADMIN)
    created = admin.post(
        "/api/users/invites", json={"email": INVITED, "nome": "Bea", "aziende": [str(world.ltd)]}
    )
    assert created.status_code == 201, created.text
    assert created.json()["aziende"] == [str(world.ltd)]
    listed = admin.get("/api/users/invites").json()
    assert [i["aziende"] for i in listed if i["email"] == INVITED] == [[str(world.ltd)]]


def test_accepting_a_scoped_invitation_through_the_role_opens_a_scoped_account(
    served: TestClient, world: ScopedWorld
) -> None:
    """The click lands on a public route with no actor: `accept` binds the system scope
    itself, or the application role reads no azienda and opens an account that sees
    nothing (the first independent review of this PR found exactly that)."""
    from pigrocrm.core.auth.invitations import InvitationService
    from pigrocrm.core.auth.schemas import InvitationCreate

    with session_factory(world.engine)() as session:
        _, raw = InvitationService(session).create(
            InvitationCreate(email=INVITED, nome="Bea", aziende=[world.ltd]),
            Actor(id=world.admin_id, type="user", role="admin"),
        )
    accepted = served.post("/api/auth/invite", json={"t": raw, "nome": None})
    assert accepted.status_code == 200, accepted.text
    try:
        assert accepted.json()["aziende"] == [str(world.ltd)]
        assert served.get("/api/auth/me").json()["aziende"] == [str(world.ltd)]
        assert _ids(served, "/api/customers") == {str(world.ltd_rows.customer)}
    finally:
        with session_factory(world.engine)() as session:
            new_id = session.execute(select(User.id).where(User.email == INVITED)).scalar_one()
            session.execute(delete(Activity).where(Activity.entity_id == new_id))
            session.execute(delete(Activity).where(Activity.actor_id == new_id))
            session.execute(delete(RefreshToken).where(RefreshToken.user_id == new_id))
            session.execute(delete(User).where(User.id == new_id))
            session.commit()


def test_a_scoped_admin_is_refused_the_space_level_writes(
    served: TestClient, world: ScopedWorld
) -> None:
    """The role stays admin, the scope takes the space-level actions away: a 403 whose
    sentence names the scope, not the role (`ScopedAdmin`, REB-633)."""
    factory = session_factory(world.engine)
    with factory() as session:
        UserService(session).update(world.admin_id, UserUpdate(ruolo="admin"), Actor.system())
        # A second unscoped admin, so the first may be scoped without emptying the space.
        second = UserService(session).create(
            UserCreate(
                email=f"{_PREFIX.lower()}-second@pigro.it",
                password=PASSWORD,
                nome=f"{_PREFIX} Second",
                ruolo="admin",
            ),
            Actor.system(),
        )
        UserService(session).update(world.admin_id, UserUpdate(aziende=[world.ltd]), Actor.system())
    try:
        client = _login(served, ADMIN)
        refused = client.post("/api/users/invites", json={"email": INVITED})
        assert refused.status_code == 403, refused.text
        problem = refused.json()
        assert problem["code"] == "permission_denied" and "invite_user" in problem["detail"]
        assert "senza limiti di azienda" in problem["reason"]
        assert client.post("/api/pipeline-stages/seed").status_code == 403
        # What the azienda's own admin keeps: the fiscal profile of an azienda in scope.
        assert client.get(f"/api/aziende/{world.ltd}").status_code == 200
    finally:
        with factory() as session:
            UserService(session).update(world.admin_id, UserUpdate(aziende=None), Actor.system())
            session.execute(delete(Activity).where(Activity.entity_id == second.id))
            session.execute(delete(Activity).where(Activity.actor_id == second.id))
            session.execute(delete(User).where(User.id == second.id))
            session.commit()
