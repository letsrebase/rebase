"""REB-293: nobody could prove a `readonly` person is refused on every writing route.

Authorization lives in the services (`actor.require_write()`, `actor.require_admin()`),
route by route, and no suite walked that route list with the two restricted roles. This
file is that walk. The route list is *generated* from the OpenAPI schema (see
`test_the_matrix_classifies_every_route_in_the_schema`): every non-GET route the schema
declares must appear in exactly one of the two lists below -- `ROWS`, walked with a
refusal expected, or `PUBLIC_ROUTES`, open on purpose with a sentence saying why. A new
writing route that forgets its `require_write()` therefore fails this suite two ways: it
is unclassified (the guard test), and once classified by whoever added it it answers 404
or 201 where the sweep demanded 403.

Two assertions, one per role:

* every row as `readonly` must answer 403 with `code: permission_denied`;
* every row as `collaboratore` must answer 403 iff the route is admin-gated, and must
  *not* be role-refused otherwise (the walk past the gate may land on a 404 or a 422,
  which is the point: the refusal the suite forbids is the role's, not the entity's).

The bodies are schema-valid on purpose. FastAPI validates the body *before* the route
function runs, so an invalid body would answer 422 from the validation layer and prove
nothing about the gate; a valid body with a nonexistent id proves the stronger claim the
card asked for -- the gate fires before the service looks anything up, so a readonly
request learns nothing (not even whether the row exists).

The rows marked `google=True` are the ones whose route answers 409 or 503 for an
*unconfigured* installation before the role gate sees the request (the Gmail services
call `require_gmail_configured`; the invite routes resolve a sender, `mail.py:121`).
Under the configured settings the role refusal is what surfaces. The one exception is
`PATCH /api/drive/account/roots`, whose service deliberately skips the config gate (a
revoked credential must stay reconfigurable); its `require_write` is the first
statement, so it holds either way.

Nothing here changes service code: the walk came up clean. Two decisions are recorded
in this file's `PUBLIC_ROUTES` sentences rather than in a service, because they are
"open on purpose" calls, not missing gates. The spec of 2026-09-17 says the matrix is
not a table in the document; this file is the table:

* `POST /api/tokens` / `DELETE /api/tokens/{id}` are *not* role-gated, and the sweep
  asserts that stays true: a token inherits its owner's role, so a readonly person's
  token can do exactly what their session already can (the write routes refuse it), and
  revocation is own-scoped (`PatService.revoke` filters on `user_id`). REB-295 landed
  that inheritance as CURRENT rather than frozen: `resolve()` reads the owner fresh on
  every call (demotion lowers the ceiling on the next request, deactivation answers
  401), and a deactivation revokes the rows in its own transaction. None of that gates
  minting, so the two rows and the spec §10 table below are unchanged.
* `POST /api/templates/{id}/preview` is a render, not a write; the same content is
  reachable through the ungated `GET describe`.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_api.deps import get_session, get_storage
from pigrocrm_api.main import create_app

# The same value `conftest.py` carries. A `from conftest import` is not the convention
# here (no other file in the suite does it, and the rootdir the suite runs under resolves
# `conftest` to packages/core/tests' anyway), so the literal is local, named identically
# so a reader lands on the same string the login fixture uses.
ADMIN_PASSWORD = "supersegreta1"

# A path/bearer id used wherever the gate must fire before any lookup; a real row would
# make a 404 answerable two ways. `re.match` on the Drive id pattern accepts it.
FAKE = str(uuid4())
# bodies also carry template markdown with real `{{...}}` placeholders.
SEED_IDS = {
    "__FAKE__": FAKE,
    "__CUST__": "",  # filled by the fixture
}


@dataclass(frozen=True)
class Row:
    """One route, as the sweep walks it.

    `expect` is "refused" (the role gate must answer 403 `permission_denied`) or "open"
    (the role must NOT be the refusal; the walk-through status is whatever the route
    answers for a well-formed request against a nonexistent row). `admin_only` marks the
    rows whose service calls `require_admin` rather than `require_write`, which decides
    what the collaboratore half asserts. `google` marks the rows whose service refuses
    unconfigured installations before the role gate sees the request.
    """

    method: str
    path: str
    expect: str = "refused"
    admin_only: bool = False
    google: bool = False
    body: Any = None
    params: dict[str, Any] | None = None
    multipart: bool = False


# Path parameters all stand in for "a row that does not exist". The two integers are the
# period/year shapes the paths ask for; every gate that runs after them is admin-gated,
# so their values never matter -- but they must parse or FastAPI answers 422 and the
# assertion goes vacuous.
PATH_VALUES = {"anno": "2026", "mese": "1", "numero": "1"}

ROWS: list[Row] = [
    # --- activities: `require_write` on every method (attivita/service.py) ----------
    Row("POST", "/api/activities", body={"titolo": "matrice-attivita"}),
    Row("DELETE", "/api/activities/{attivita_id}"),
    Row("PATCH", "/api/activities/{attivita_id}", body={}),
    Row("POST", "/api/activities/{attivita_id}/cancel"),
    Row("POST", "/api/activities/{attivita_id}/complete"),
    Row("POST", "/api/activities/{attivita_id}/reopen"),
    Row("POST", "/api/activities/{attivita_id}/restore"),
    # --- auth: the one write a readonly person legitimately performs ----------------
    # Their own digest switch (spec 2026-09-16 §3.6; `update_own_digest` has no gate
    # deliberately). Asserted open here so nobody adds one by accident.
    Row("PATCH", "/api/auth/me", expect="open", body={"digest_settimanale": False}),
    # --- automation config: admin (config_service.py:103) ---------------------------
    Row("PUT", "/api/automation-config", admin_only=True, body={}),
    # --- cost categories: admin on every method (timetracking/categories.py) --------
    Row("POST", "/api/cost-categories", admin_only=True, body={"nome": "matrice-cat"}),
    Row("POST", "/api/cost-categories/seed", admin_only=True),
    Row("PATCH", "/api/cost-categories/{category_id}", admin_only=True, body={}),
    Row("POST", "/api/cost-categories/{category_id}/archive", admin_only=True),
    Row("POST", "/api/cost-categories/{category_id}/unarchive", admin_only=True),
    # --- costs: `require_write` (timetracking/costs.py) ------------------------------
    Row(
        "POST",
        "/api/costs",
        body={
            "category_id": "__FAKE__",
            "data": "2026-01-05",
            "importo": "10.00",
            "descrizione": "matrice-costo",
        },
    ),
    Row("DELETE", "/api/costs/{cost_id}"),
    Row("PATCH", "/api/costs/{cost_id}", body={}),
    Row("POST", "/api/costs/{cost_id}/restore"),
    # --- contracts / rate cards (REB-358): `require_write` (contracts/service.py) ----
    Row(
        "POST",
        "/api/contracts",
        body={
            "customer_id": "__CUST__",
            "titolo": "Matrice",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": {"tipo": "non_rimborsabile"},
        },
    ),
    Row(
        "POST",
        "/api/contracts/{contract_id}/rate-cards",
        body={
            "valido_da": "2026-01-01",
            "tipo": "ricorrente_fisso",
            "importo": "1000.00",
            "unita": "mese",
        },
    ),
    # --- contracts / expenses (REB-360): `require_write` (contract_expenses/service.py)
    Row(
        "POST",
        "/api/contracts/{contract_id}/expenses",
        body={
            "category_id": "__FAKE__",
            "data": "2026-01-01",
            "importo": "10.00",
            "descrizione": "Matrice",
        },
    ),
    Row("PATCH", "/api/contracts/{contract_id}/expenses/{expense_id}", body={}),
    # --- contracts / renewal assumption (REB-375): `require_write` -------------------
    Row(
        "PUT",
        "/api/contracts/{contract_id}/renewal-assumption",
        body={"probabilita": 50, "volume_atteso": "1000.00", "orizzonte_al": "2027-01-01"},
    ),
    # --- customers / people / deals: `require_write` on every plain write ------------
    Row("POST", "/api/customers", body={"ragione_sociale": "Matrice Due Srl"}),
    Row("DELETE", "/api/customers/{customer_id}"),
    Row("PATCH", "/api/customers/{customer_id}", body={}),
    Row("POST", "/api/customers/{customer_id}/restore"),
    Row("POST", "/api/deals", body={"nome": "matrice-deal", "customer_id": "__CUST__"}),
    Row("DELETE", "/api/deals/{deal_id}"),
    Row("PATCH", "/api/deals/{deal_id}", body={}),
    # Rates rewrite what a quarter's work was worth: admin (timetracking/service.py:440,
    # :472), and `bind_time_to_invoice` is the same class (analytics/service.py:613).
    Row("PUT", "/api/deals/{deal_id}/rate", admin_only=True, body={"tariffa_oraria": "50.00"}),
    Row(
        "POST",
        "/api/deals/{deal_id}/rates/recalculate",
        admin_only=True,
        body={"da": "2026-01-01", "a": "2026-01-31"},
    ),
    Row("POST", "/api/deals/{deal_id}/restore"),
    Row("PATCH", "/api/deals/{deal_id}/stage", body={"stage_id": "__FAKE__"}),
    Row(
        "POST",
        "/api/deals/{deal_id}/time-entries/to-invoice-draft",
        admin_only=True,
        body={"entry_ids": ["__FAKE__"]},
    ),
    # --- documents: `require_write` on every method (documents/service.py) -----------
    Row("POST", "/api/documents", body={"titolo": "matrice-doc"}),
    Row(
        "POST",
        "/api/documents/from-template",
        body={"template_id": "__FAKE__", "titolo": "matrice-doc"},
    ),
    Row("DELETE", "/api/documents/{document_id}"),
    Row("PATCH", "/api/documents/{document_id}", body={}),
    Row("POST", "/api/documents/{document_id}/restore"),
    Row("POST", "/api/documents/{document_id}/status", body={"stato": "inviata"}),
    Row("POST", "/api/documents/{document_id}/versions", multipart=True),
    Row("POST", "/api/documents/{document_id}/versions/{numero}/regenerate"),
    # --- drive: connect/disconnect/roots are writes on the credential, not on data --
    # (`require_write`; `GoogleDriveOAuthService` mirrors the Gmail one). The two
    # `oauth/start|callback` GETs are in this sweep because they *are* gated
    # (`require_write`, drive/oauth.py:94,:134): they are GETs only in HTTP-shape, they
    # connect an account. The rest of the GET surface (health) is an open read.
    Row("DELETE", "/api/drive/account", google=True),
    Row("PATCH", "/api/drive/account/roots", body={"root_folder_ids": ["abcdefghijkl"]}),
    Row("GET", "/api/drive/oauth/start", google=True),
    Row(
        "GET",
        "/api/drive/oauth/callback",
        google=True,
        params={"code": "codice-finto", "state": "stato-finto"},
    ),
    # --- email drafts: every mutation is `require_write` (gmail/drafts.py, send.py) --
    # `send` and `reconcile` behind the installation-configured gate too: unconfigured
    # they answer 409 before the role asks anything, so they walk under Google settings.
    Row(
        "POST",
        "/api/email-drafts",
        body={
            "entity_type": "customer",
            "entity_id": "__CUST__",
            "to_addresses": ["uno@pigro.it"],
            "subject": "matrice",
            "body_markdown": "ciao",
        },
    ),
    Row("DELETE", "/api/email-drafts/{draft_id}"),
    Row("PATCH", "/api/email-drafts/{draft_id}", body={}),
    Row("POST", "/api/email-drafts/{draft_id}/reconcile", google=True),
    Row("POST", "/api/email-drafts/{draft_id}/send", google=True),
    # --- the two fiscal profiles and the emitter: admin (fiscal/, emitter/) ----------
    Row("PUT", "/api/emitter", admin_only=True, body={"ragione_sociale": "Matrice Srl"}),
    Row("PUT", "/api/fiscal-profile", admin_only=True, body={"codice_regime": "RF19"}),
    # --- field definitions: admin, they reshape every entity's stored data -----------
    Row(
        "POST",
        "/api/field-definitions",
        admin_only=True,
        body={
            "entity_type": "customer",
            "key": "matrice_k",
            "label": "Matrice",
            "field_type": "text",
        },
    ),
    Row("PATCH", "/api/field-definitions/{field_id}", admin_only=True, body={}),
    Row("POST", "/api/field-definitions/{field_id}/archive", admin_only=True),
    Row("POST", "/api/field-definitions/{field_id}/unarchive", admin_only=True),
    # --- gmail: same shape as drive, plus sync/backfill which spend the owner's quota
    Row("DELETE", "/api/gmail/account", google=True),
    Row("PATCH", "/api/gmail/account", google=True, body={"gmail_store_bodies": False}),
    Row(
        "POST",
        "/api/gmail/backfill",
        google=True,
        body={"entity_type": "customer", "entity_id": "__CUST__"},
    ),
    Row("POST", "/api/gmail/sync", google=True),
    Row("GET", "/api/gmail/oauth/start", google=True),
    Row(
        "GET",
        "/api/gmail/oauth/callback",
        google=True,
        params={"code": "codice-finto", "state": "stato-finto"},
    ),
    # --- invoices, proforma half: `require_write` (invoices/service.py) --------------
    Row("POST", "/api/invoices", body={"customer_id": "__CUST__"}),
    Row("DELETE", "/api/invoices/{invoice_id}"),
    Row("PATCH", "/api/invoices/{invoice_id}", body={}),
    Row("POST", "/api/invoices/{invoice_id}/artifacts"),
    Row("POST", "/api/invoices/{invoice_id}/confirm"),
    Row("PUT", "/api/invoices/{invoice_id}/lines", body={"righe": []}),
    Row("PATCH", "/api/invoices/{invoice_id}/payment", body={"stato_pagamento": "da_incassare"}),
    # --- invoices, fiscal half: `require_admin` -- every one of these consumes or
    # rewrites the gap-free register (spec 2026-08-20 slice 3 §4; the AGENT_FORBIDDEN
    # list in core/actor.py names the same five for agents).
    Row(
        "POST",
        "/api/invoices/import",
        admin_only=True,
        body={
            "anno": 2026,
            "numero": 9500,
            "data_emissione": "2026-01-02",
            "customer_id": "__CUST__",
            "righe": [
                {
                    "descrizione": "matrice",
                    "prezzo_unitario": "10.00",
                    "prezzo_totale": "10.00",
                    "aliquota_iva": "0.00",
                    "natura": "N2.2",
                }
            ],
            "imponibile": "10.00",
            "imposta": "0.00",
            "bollo": "0.00",
            "totale": "10.00",
        },
    ),
    Row(
        "POST",
        "/api/invoices/import/review",
        admin_only=True,
        body={"document_ids": ["__FAKE__"]},
    ),
    Row(
        "POST",
        "/api/invoices/register/{anno}/gaps",
        admin_only=True,
        body={"buchi": [{"numero": 9501, "motivo": "matrice"}]},
    ),
    Row("POST", "/api/invoices/{invoice_id}/annul", admin_only=True, body={"motivo": "matrice"}),
    Row("POST", "/api/invoices/{invoice_id}/issue", admin_only=True, body={}),
    Row(
        "POST",
        "/api/invoices/{invoice_id}/transmitted",
        admin_only=True,
        body={"data": "2026-01-02"},
    ),
    # --- payment reminders: preparing one is a write (gmail/solleciti.py:220) --------
    Row("POST", "/api/payment-reminders", body={"invoice_id": "__FAKE__"}),
    # --- people: same CRUD shape as customers ----------------------------------------
    Row("POST", "/api/people", body={"nome": "Matrice Persona"}),
    Row("DELETE", "/api/people/{person_id}"),
    Row("PATCH", "/api/people/{person_id}", body={}),
    Row("POST", "/api/people/{person_id}/restore"),
    # --- period locks: closing a period freezes the register: admin ------------------
    Row("POST", "/api/period-locks", admin_only=True, body={"anno": 2020, "mese": 1}),
    Row("DELETE", "/api/period-locks/{anno}/{mese}", admin_only=True),
    # --- pipeline: configuration, admin (pipeline/service.py) -------------------------
    Row("POST", "/api/pipeline-stages", admin_only=True, body={"nome": "Matrice", "posizione": 99}),
    Row("POST", "/api/pipeline-stages/seed", admin_only=True),
    Row("DELETE", "/api/pipeline-stages/{stage_id}", admin_only=True),
    Row("PATCH", "/api/pipeline-stages/{stage_id}", admin_only=True, body={}),
    # --- space settings: admin on read *and* write (space_settings/service.py:78,:84) -
    Row("PUT", "/api/settings/space", admin_only=True, body={}),
    Row("GET", "/api/settings/space", admin_only=True),
    # --- templates: configuration, admin on every mutation (templates/service.py) -----
    Row(
        "POST",
        "/api/templates",
        admin_only=True,
        body={"nome": "matrice-tpl", "tipo": "offerta", "corpo_markdown": "ciao"},
    ),
    Row("DELETE", "/api/templates/{template_id}", admin_only=True),
    Row("PATCH", "/api/templates/{template_id}", admin_only=True, body={}),
    Row("POST", "/api/templates/{template_id}/activate", admin_only=True),
    # Preview is a render, not a write (the same content is reachable through the
    # ungated `GET /api/templates/{id}/describe`), so it is asserted *not* role-refused;
    # the fake id makes it a 404, which is the wanted answer for the wrong-id shape.
    Row("POST", "/api/templates/{template_id}/preview", expect="open", body={}),
    # --- time entries: `require_write` (timetracking/service.py, timer.py) ------------
    Row(
        "POST",
        "/api/time-entries",
        body={
            "deal_id": "__FAKE__",
            "user_id": "__ADMIN__",
            "data": "2026-01-05",
            "ore": "1.00",
            "descrizione": "matrice",
        },
    ),
    Row("DELETE", "/api/time-entries/timer"),
    Row("PATCH", "/api/time-entries/timer", body={}),
    Row("POST", "/api/time-entries/timer/start", body={}),
    Row("POST", "/api/time-entries/timer/stop", body={}),
    Row("DELETE", "/api/time-entries/{entry_id}"),
    Row("PATCH", "/api/time-entries/{entry_id}", body={}),
    Row("POST", "/api/time-entries/{entry_id}/restore"),
    # --- personal access tokens: own-scoped, not role-gated. A readonly user's token
    # inherits readonly, so every write route above refuses it the same way it refuses
    # the session; minting one is self-service like the digest switch. `revoke` filters
    # on `user_id`, so a foreign id is a 404 rather than a refusal -- which is the
    # right shape: the row's nonexistence is the answer, not the role. REB-295 landed
    # with the inheritance current, not frozen (see this module's docstring), and it
    # did NOT gate minting: both rows and the spec §10 table stand as written.
    Row("POST", "/api/tokens", expect="open", body={"nome": "matrice-token"}),
    Row("DELETE", "/api/tokens/{token_id}", expect="open"),
    # --- users, invites, rates: admin (auth/service.py, auth/invitations.py) ---------
    # `PATCH /api/users/{user_id}` is REB-292's route: its last-admin guard sits behind
    # `require_admin`, so as `collaboratore` this answers 403 before and after that
    # lands. What 292 can change is only what an *admin* caller sees, and no caller
    # here is one.
    Row(
        "POST",
        "/api/users",
        admin_only=True,
        body={"email": "matrice-nuovo@pigro.it", "nome": "Matrice", "password": ADMIN_PASSWORD},
    ),
    Row("GET", "/api/users", admin_only=True),
    # `google=True` here is "the installation is configured", not Google: the invite
    # router resolves a sender before its body runs, and an installation without
    # `PIGROCRM_RESEND_API_KEY` answers 503 whatever the role, the same class as the
    # Gmail rows. GOOGLE_SETTINGS carries the key for that reason.
    Row(
        "POST",
        "/api/users/invites",
        admin_only=True,
        google=True,
        body={"email": "matrice-inv@pigro.it"},
    ),
    Row("GET", "/api/users/invites", admin_only=True),
    Row("POST", "/api/users/invites/{invitation_id}/resend", admin_only=True, google=True),
    Row("DELETE", "/api/users/invites/{invitation_id}", admin_only=True),
    Row("PATCH", "/api/users/{user_id}", admin_only=True, body={}),
    Row("PUT", "/api/users/{user_id}/rates", admin_only=True, body={}),
    # --- the fiscal estimate: the one *read* admin gates (analytics/service.py:552) --
    Row("GET", "/api/analytics/fiscal", admin_only=True, params={"anno": 2026}),
]

# The routes the sweep never calls, each open on purpose. Every entry is a
# (method, path) the OpenAPI schema carries, so the guard test below proves this list
# cannot silently rot.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): (
        "Opens a session for anybody with a password; unauthenticated by design "
        "(spec 2026-09-12), throttled per client. Calling it from the sweep would "
        "re-login the role whose client is doing the calling and swap its cookies."
    ),
    ("POST", "/api/auth/verify"): (
        "Spends a magic-link token and opens a session; unauthenticated by design. "
        "No token exists to spend, so it answers 401 -- an auth refusal, not an "
        "authorization one, and the wrong thing to assert 403 about."
    ),
    ("POST", "/api/auth/link"): (
        "Requests the magic link; unauthenticated by design (spec 2026-09-12 §6.2), "
        "and it queues a mail the suite has no sender for."
    ),
    ("POST", "/api/auth/refresh"): (
        "Session mechanics: rotates the refresh cookie, which the role clients never "
        "received in a form worth refreshing. No actor dependency exists here to gate."
    ),
    ("POST", "/api/auth/logout"): (
        "Idempotent and deliberately ungated (its docstring in routers/auth.py says "
        "why: logging out must work for a dying session). Calling it would end the "
        "very session the sweep is walking."
    ),
    ("POST", "/api/auth/invite"): (
        "The public half of the invitation flow (REB-290, spec 2026-09-17 §1): open to "
        "anybody holding a valid token, and it creates a user -- exactly the write the "
        "authenticated half of this sweep must not be reachable for. Not called: "
        "spending a token this suite never minted would answer 404/410, a status that "
        "says nothing about roles."
    ),
    ("POST", "/api/tenants/"): (
        "Space signup: public by design (the person has no account yet), and it "
        "provisions a real database. The sweep has no business creating spaces; the "
        "root-admin-bearer half of the tenants surface is a GET and stays out of this "
        "walk's method set."
    ),
    ("POST", "/api/tenants/member"): (
        "The pre-signup question about an address, public by design and throttled per "
        "client; it reaches the hub over the network and answers even when the hub is "
        "down (ORB-173)."
    ),
    ("POST", "/api/identity/logout"): (
        "Root-scoped and deliberately ungated (design 2026-09-23 §2/§3, REB-376): the "
        "cookie it reads is not a space actor, so there is no role to gate on, and it "
        "is idempotent for the same reason /api/auth/logout is. Calling it from the "
        "sweep would end an identity session none of the three role clients hold in a "
        "form worth revoking."
    ),
    ("POST", "/api/identity/enter/{slug}"): (
        "Root-scoped and gated by the identity cookie, not a space actor (design "
        "2026-09-23 §3, REB-377): there is no role to check before a space's own "
        "database is even open, and the three role clients here hold no identity "
        "session worth entering with."
    ),
}

_SAFE_METHODS = {"get", "head", "options", "trace"}


def _fill(value: Any, ids: dict[str, str]) -> Any:
    """Substitute the `__NAME__` tokens in bodies with seeded ids."""
    if isinstance(value, str):
        for token, real in ids.items():
            value = value.replace(token, real)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


def _url(path: str) -> str:
    # One capture group, so findall yields plain strings, not tuples.
    for name in re.findall(r"\{(\w+)\}", path):
        path = path.replace("{" + name + "}", PATH_VALUES.get(name, FAKE))
    return path


class Matrix:
    """Three logged-in clients (one per role) over one app and one session, plus the
    seeded rows the bodies need. Module-scoped: the walk is ~110 requests and every one
    of them is a refusal or a self-service call that commits nothing the next test can
    trip on, so paying the argon2 logins once per module is the whole cost of the file.
    """

    def __init__(self, app: FastAPI, clients: dict[str, TestClient], ids: dict[str, str]) -> None:
        self.app = app
        self.clients = clients
        self.ids = ids

    def call(self, role: str, row: Row) -> Any:
        """Fire one row's request as `role`, with the Google half configured for the
        rows that need it. The override is per call and restored in `finally` because
        the app is shared by the three role clients."""
        assert role in self.clients
        if row.google:
            self.app.dependency_overrides[get_settings] = lambda: GOOGLE_SETTINGS
        else:
            self.app.dependency_overrides[get_settings] = lambda: BASE_SETTINGS
        try:
            kwargs: dict[str, Any] = {}
            if row.params:
                kwargs["params"] = row.params
            if row.multipart:
                kwargs["files"] = {"file": ("matrice.txt", b"matrice", "text/plain")}
            else:
                kwargs["json"] = _fill(row.body, {**self.ids, "__FAKE__": FAKE})
            return self.clients[role].request(row.method, _url(row.path), **kwargs)
        finally:
            self.app.dependency_overrides[get_settings] = lambda: BASE_SETTINGS


BASE_SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
# The same shape `test_gmail_router._configured` uses -- a copy of the base, never a
# fresh `Settings(...)`, because `jwt_secret` is what the login cookies were signed
# with. The Google fields are what `gmail_configured` asks for (config.py:273).

GOOGLE_SETTINGS = BASE_SETTINGS.model_copy(
    update={
        "google_client_id": "cid.apps.googleusercontent.com",
        "google_client_secret": "il-segreto-del-client",
        "google_token_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "public_url": "https://crm.example.it",
        # "configured" covers the mail too: the invite rows resolve a sender before
        # their body runs (mail.py:121), and without the key the installation answers
        # 503 whatever the role. A fake literal is enough; nothing here sends.
        "resend_api_key": "re_madre-finta-per-la-sweep",
    }
)


def _logged_in_as(app: FastAPI, session: Session, *, email: str, ruolo: str) -> TestClient:
    UserService(session).create(
        UserCreate(email=email, password=ADMIN_PASSWORD, nome="Matrice", ruolo=ruolo),  # type: ignore[arg-type]
        Actor.system(),
    )
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={"email": email, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture(scope="module")
def matrix(api_engine: Engine) -> Iterator[Matrix]:
    app = create_app()
    # The same isolation `api_session` gives every single test, lifted to module
    # scope: one connection, one external transaction the whole fixture lives
    # inside, rolled back at the end. The collaboratore half of the sweep PASSES the
    # role gate, so its rows really write (the activity it POSTs is created and
    # committed), and the container is session-scoped and shared with every other
    # file in the directory: without the outer rollback, `test_calendario_api` finds
    # `matrice-attivita` in a list it asserts empty. `create_savepoint` makes the
    # sweep's own commits nest instead of ending the transaction (see api_session's
    # comment, same trap, established in Task 2).
    connection = api_engine.connect()
    transaction = connection.begin()
    session = session_factory(api_engine)(bind=connection, join_transaction_mode="create_savepoint")
    storage_dir = Path(tempfile.mkdtemp(prefix="role-matrix-"))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_storage] = lambda: LocalFileStorage(storage_dir)
    app.dependency_overrides[get_settings] = lambda: BASE_SETTINGS
    try:
        admin = _logged_in_as(app, session, email="matrice-admin@pigro.it", ruolo="admin")
        users = {
            "readonly": _logged_in_as(
                app, session, email="matrice-readonly@pigro.it", ruolo="readonly"
            ),
            "collaboratore": _logged_in_as(
                app, session, email="matrice-collab@pigro.it", ruolo="collaboratore"
            ),
        }
        admin_id = users and admin.get("/api/auth/me").json()["id"]
        ids = {"__ADMIN__": str(admin_id)}
        # Seed the rows whose bodies need a *real* id for FastAPI to parse (UUID fields
        # would parse a fake too -- these exist so at least one swept row proves the
        # gate is not merely "the id was wrong": with a real customer and deal, a
        # readonly request that skipped its gate would 201, not 404.) The deal also
        # needs an *open* stage to exist (the service refuses a deal with none), and
        # the space the sweep runs in is a bare schema, not a provisioned one.
        # `POST /api/pipeline-stages/seed` is itself a swept row, allowed to admin and
        # refused to the two roles exactly as the other admin routes; the seed here
        # happens before the sweep, so running it early costs the sweep nothing.
        assert admin.post("/api/pipeline-stages/seed").status_code == 200
        customer = admin.post("/api/customers", json={"ragione_sociale": "Matrice Srl"})
        assert customer.status_code == 201, customer.text
        ids["__CUST__"] = customer.json()["id"]
        deal = admin.post("/api/deals", json={"nome": "Matrice", "customer_id": ids["__CUST__"]})
        assert deal.status_code == 201, deal.text
        ids["__DEAL__"] = deal.json()["id"]
        yield Matrix(app, {"admin": admin, **users}, ids)
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _assert_role_refused(response: Any, *, action_required_roles: list[str], role: str) -> None:
    assert response.status_code == 403, (
        f"{response.request.method} {response.url} -> {response.status_code} {response.text[:200]}"
    )
    problem = response.json()
    assert problem["code"] == "permission_denied", problem
    assert problem["required_roles"] == action_required_roles, problem
    assert problem["actual_role"] == role, problem


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r.method} {r.path}")
def test_readonly_is_refused_on_every_gated_route(row: Row, matrix: Matrix) -> None:
    response = matrix.call("readonly", row)
    if row.expect == "open":
        assert not (
            response.status_code == 403 and response.json().get("code") == "permission_denied"
        ), f"{row.method} {row.path} is open on purpose but role-refused: {response.text[:200]}"
        return
    _assert_role_refused(
        response,
        action_required_roles=["admin"] if row.admin_only else ["admin", "collaboratore"],
        role="readonly",
    )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r.method} {r.path}")
def test_collaboratore_holds_writes_and_is_refused_on_admin_routes(
    row: Row, matrix: Matrix
) -> None:
    response = matrix.call("collaboratore", row)
    if row.admin_only:
        _assert_role_refused(response, action_required_roles=["admin"], role="collaboratore")
    else:
        # The pass-through half of the card: a collaboratore must clear the role gate on
        # every row that is not admin-only, whatever the service then makes of a
        # nonexistent row. A 403 `permission_denied` here means a route gated on admin
        # where it should gate on write (wrong but survivable) or a regression against
        # REB-294's premise; a 5xx means the walk-through crashed. Either way it is a
        # finding, and this test is where it surfaces.
        refused = response.status_code == 403 and response.json().get("code") == "permission_denied"
        assert not refused, (
            f"{row.method} {row.path} role-refused a collaboratore: {response.text[:200]}"
        )
        assert response.status_code < 500, response.text


def test_the_matrix_classifies_every_route_in_the_schema(matrix: Matrix) -> None:
    """The guard that makes the file a tripwire: every non-GET route the schema declares
    must be in exactly one of the two lists. A new writing route cannot be *forgotten*
    by this suite -- it fails collection of this test with its method and path named in
    the assertion message, and the fix is to add the service's `require_write` and the
    row, not to edit this list to make the test pass."""
    schema = matrix.app.openapi()
    declared = {
        (method.upper(), path)
        for path, ops in schema["paths"].items()
        for method in ops
        if method not in _SAFE_METHODS
    }
    rows = {(row.method, row.path) for row in ROWS}
    # `declared` holds only the unsafe methods; the sweep also walks GETs that are
    # gates in HTTP shape (the two oauth/start pairs connect an account), so the
    # stale check compares just the walked write methods against the schema.
    walked_writes = {(m, p) for (m, p) in rows if m.lower() not in _SAFE_METHODS}
    unclassified = declared - walked_writes - set(PUBLIC_ROUTES)
    stale = walked_writes - declared
    ghost_public = set(PUBLIC_ROUTES) - declared
    assert not unclassified, f"routes the matrix does not classify: {sorted(unclassified)}"
    assert not stale, f"rows no longer in the schema (remove them): {sorted(stale)}"
    assert not ghost_public, f"allowlisted routes no longer in the schema: {sorted(ghost_public)}"
    # And the two lists are disjoint: a route cannot be both walked and exempt.
    assert not rows & set(PUBLIC_ROUTES)


def test_the_digest_switch_is_the_readonly_users_own_profile_write(matrix: Matrix) -> None:
    """The narrowest reading of the card's one named exception, kept apart from the
    sweep because it is the only row that *succeeds* as readonly and therefore must not
    leave the flag flipped for whoever reads this module next: PATCH, flip back."""
    client = matrix.clients["readonly"]
    before = client.get("/api/auth/me").json()["digest_settimanale"]
    flipped = client.patch("/api/auth/me", json={"digest_settimanale": not before})
    assert flipped.status_code == 200
    assert flipped.json()["digest_settimanale"] is (not before)
    restored = client.patch("/api/auth/me", json={"digest_settimanale": before})
    assert restored.status_code == 200
