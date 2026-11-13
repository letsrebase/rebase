"""Every route of the API is either behind `AdminDep` or on a written list (HTEST-08, REB-892).

The admin-only checks used to be hand-listed per router file, so a new admin route without
a test, or without the dependency, passed silently. This module reads the routes the app
really serves, follows each one's dependency tree, and holds three lines:

- every route that depends on `get_admin` answers 401 to nobody signed in and 403 to a
  signed-in member (the contract of `get_admin`, design record 2026-09-17 § 4);
- every route that does not is in `NOT_ADMIN` below, with the reason it is open to
  that audience, and nothing in `NOT_ADMIN` is a route that is gone or that has become
  admin-only: so a new public route, or an admin route that lost its dependency, fails here
  and has to be decided on purpose;
- the speed bump of the two sign-in doors answers 429 past `SIGNUPS_PER_MINUTE` a minute.
"""

import re
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api import deps
from rebase_api.main import create_app
from rebase_api.ratelimit import RETRY_AFTER_SECONDS, SIGNUPS_PER_MINUTE
from rebase_core.mail import RecordingSender
from rebase_core.models import User

Route = tuple[str, str]

# The audience of a route that is not admin-only. `member` routes answer 401 to nobody
# signed in; `cloud` ones need a live talent-cloud grant on top of that (401, then 403);
# `public` ones are open on purpose.
PUBLIC = "public"
MEMBER = "member"
CLOUD = "cloud"

NOT_ADMIN: dict[Route, tuple[str, str]] = {
    # --- the public forms and the transport's own doors
    ("GET", "/health"): (PUBLIC, "the proxy's and the deploy's liveness probe"),
    ("POST", "/api/community/signups"): (PUBLIC, "the landing's signup form, behind spend_one"),
    ("POST", "/api/hub/freelancers"): (PUBLIC, "the freelancer wizard, behind spend_one"),
    ("POST", "/api/hub/companies"): (PUBLIC, "the company wizard, behind spend_one"),
    ("POST", "/api/hub/documenso/webhook"): (PUBLIC, "Documenso's webhook: the shared secret"),
    ("POST", "/api/hub/webhooks/resend"): (PUBLIC, "Resend's webhook: the Svix signature"),
    ("GET", "/api/hub/referral-rates"): (PUBLIC, "the rates the landing and the guide show"),
    ("GET", "/api/hub/campaigns/unsubscribe"): (PUBLIC, "the unsubscribe page's token read"),
    ("POST", "/api/hub/campaigns/unsubscribe"): (PUBLIC, "the unsubscribe's one click"),
    ("POST", "/api/hub/campaigns/confirm"): (PUBLIC, "the address confirmation's button"),
    # The one-click link in every mail sent before the rename keeps working.
    ("GET", "/api/hub/campagne/disiscrizione"): (PUBLIC, "the legacy unsubscribe page"),
    ("POST", "/api/hub/campagne/disiscrizione"): (PUBLIC, "the legacy unsubscribe click"),
    # --- signing in
    ("POST", "/api/hub/auth/link"): (PUBLIC, "asks a magic link, behind spend_one"),
    ("POST", "/api/hub/auth/enter"): (PUBLIC, "spends a magic link, behind spend_one"),
    ("POST", "/api/hub/members/lookup"): (PUBLIC, "the wizards' «sei già dei nostri?» check"),
    ("POST", "/api/hub/engagement-notices"): (
        PUBLIC,
        "PigroCRM's notices about its engagements: the shared bearer, 404 without it set",
    ),
    ("POST", "/api/hub/referrer-name"): (
        PUBLIC,
        "PigroCRM's question about who referred somebody: the same bearer",
    ),
    ("POST", "/api/hub/me/logout"): (PUBLIC, "ends whatever session the cookie holds, if any"),
    # --- the team builder's public page
    ("POST", "/api/hub/team/proposals"): (PUBLIC, "«Proponi il team», behind spend_one and caps"),
    ("GET", "/api/hub/team/proposals/{proposal_id}"): (PUBLIC, "a proposal by its random id"),
    ("POST", "/api/hub/team/requests"): (PUBLIC, "«Assumi team», behind spend_one"),
    ("POST", "/api/hub/team/availability"): (PUBLIC, "a talent's answer, by its 256-bit token"),
    # --- the signed-in member's own area
    ("GET", "/api/hub/me"): (MEMBER, "the member's own page"),
    ("PATCH", "/api/hub/me"): (MEMBER, "edits the member's own profile"),
    ("POST", "/api/hub/me/company"): (MEMBER, "the member's own company request"),
    ("PATCH", "/api/hub/me/company"): (MEMBER, "edits the member's own company request"),
    ("PATCH", "/api/hub/me/company/{company_id}"): (MEMBER, "edits one of their requests"),
    ("PUT", "/api/hub/me/cv"): (MEMBER, "replaces the member's own CV"),
    ("PUT", "/api/hub/me/contact-points"): (
        MEMBER,
        "replaces the member's own list of extra contact points",
    ),
    ("GET", "/api/hub/me/cv"): (MEMBER, "the member's own CV, never another's"),
    ("GET", "/api/hub/me/guide"): (MEMBER, "the guide download"),
    ("GET", "/api/hub/me/contracts"): (MEMBER, "the member's own contracts"),
    ("GET", "/api/hub/me/contracts/{document_id}/pdf"): (MEMBER, "one of their own contracts"),
    ("GET", "/api/hub/me/referral"): (MEMBER, "the member's own referral page"),
    # --- the talent cloud: signed in, then a live grant
    ("GET", "/api/hub/me/cloud/talents"): (CLOUD, "the cloud's talents"),
    ("GET", "/api/hub/me/cloud/talents/{freelancer_id}/cv"): (CLOUD, "a cloud talent's CV"),
    ("POST", "/api/hub/me/cloud/proposals"): (CLOUD, "«Proponi il team» inside the cloud"),
    ("POST", "/api/hub/me/cloud/requests"): (CLOUD, "«Assumi team» / «Richiedi» in the cloud"),
}

_CONCRETE = str(UUID(int=1))
_IGNORED_METHODS = {"HEAD", "OPTIONS"}


def _api_routes(routes: Any) -> Iterator[APIRoute]:
    """The `APIRoute`s the app serves, through the routers it includes."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router.routes)


def _depends_on(dependant: Any, call: Any) -> bool:
    return any(sub.call is call or _depends_on(sub, call) for sub in dependant.dependencies)


def _routes() -> tuple[dict[Route, APIRoute], dict[Route, APIRoute]]:
    """The routes behind `get_admin` and the others, by (method, path)."""
    admin: dict[Route, APIRoute] = {}
    other: dict[Route, APIRoute] = {}
    for route in _api_routes(create_app().routes):
        for method in route.methods - _IGNORED_METHODS:
            target = admin if _depends_on(route.dependant, deps.get_admin) else other
            target[(method, route.path)] = route
    return admin, other


ADMIN_ROUTES, OTHER_ROUTES = _routes()


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", _CONCRETE, path)


def test_the_walk_finds_every_route_the_app_serves() -> None:
    """The check below is only as good as its list of routes: it must be the one OpenAPI
    publishes (every route here is in the schema), not a subset a router change hid."""
    paths = create_app().openapi()["paths"]
    published = {
        (method.upper(), path)
        for path, item in paths.items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    # The pre-rename unsubscribe path stays served for the mails already sent, and out of
    # the schema.
    old = "/api/hub/campagne/disiscrizione"
    legacy = {("GET", old), ("POST", old)}
    assert (set(ADMIN_ROUTES) | set(OTHER_ROUTES)) - legacy == published
    assert ADMIN_ROUTES, "no admin route found: the dependency walk is broken"


def test_every_route_that_is_not_admin_only_is_on_the_list() -> None:
    unlisted = sorted(set(OTHER_ROUTES) - set(NOT_ADMIN))
    assert not unlisted, (
        "routes with no AdminDep that are not in NOT_ADMIN: give them AdminDep, or write "
        f"their audience and reason in the list: {unlisted}"
    )
    stale = sorted(set(NOT_ADMIN) - set(OTHER_ROUTES))
    assert not stale, f"NOT_ADMIN entries that are gone, or admin-only now: {stale}"


@pytest.fixture
def guarded(api_session: Session) -> Iterator[Session]:
    """The tables the sign-in below writes, emptied after."""
    yield api_session
    api_session.rollback()
    for table in ("sessions", "magic_link_tokens", "logins", "admin_actions", "users"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender, email: str) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
    # Signing in spent two of the five a minute the public writes share.


def test_admin_routes_are_401_for_nobody_and_403_for_a_member(
    client: TestClient, guarded: Session, sender: RecordingSender
) -> None:
    # Everything first, in one pass: a failure names every route that is open, not the
    # first one. Nobody signed in...
    anonymous = {
        route: client.request(route[0], _url(route[1])).status_code for route in ADMIN_ROUTES
    }
    assert {route: code for route, code in anonymous.items() if code != 401} == {}

    # ... then a signed-in member, who is a real identity at the wrong door.
    guarded.add(User(email="membro@studio.it", first_name="Ada", last_name="Membro"))
    guarded.commit()
    _login(client, sender, "membro@studio.it")
    member = {route: client.request(route[0], _url(route[1])).status_code for route in ADMIN_ROUTES}
    assert {route: code for route, code in member.items() if code != 403} == {}


def test_member_and_cloud_routes_answer_401_to_nobody(client: TestClient) -> None:
    gated = [route for route, (audience, _) in NOT_ADMIN.items() if audience != PUBLIC]
    answers = {route: client.request(route[0], _url(route[1])).status_code for route in gated}
    assert {route: code for route, code in answers.items() if code != 401} == {}


# ---- the speed bump of the two sign-in doors (HTEST-08) --------------------------------------


# A door's request and what it answers while the bump lets it through. An address nobody
# holds is 202 whether or not it applied (and no mail either way); a token nobody holds is
# the same 401 for unknown, spent and expired.
LINK = "/api/hub/auth/link"
ENTER = "/api/hub/auth/enter"
DOORS = {
    LINK: ({"email": "nessuno@studio.it"}, 202),
    ENTER: ({"token": "x" * 43}, 401),
}


@pytest.mark.parametrize(("door", "twin"), [(LINK, ENTER), (ENTER, LINK)])
def test_the_sign_in_doors_are_429_past_the_speed_bump(
    client: TestClient,
    guarded: Session,
    sender: RecordingSender,
    door: str,
    twin: str,
    strict_rate_limit: None,
) -> None:
    body, answer = DOORS[door]
    for _ in range(SIGNUPS_PER_MINUTE):
        assert client.post(door, json=body).status_code == answer

    refused = client.post(door, json=body)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    # The bump is per client: another address behind the proxy is not refused.
    other = client.post(door, json=body, headers={"x-real-ip": "198.51.100.77"})
    assert other.status_code == answer
    # And the budget is the client's, shared by the two doors, not each route's own.
    assert client.post(twin, json=DOORS[twin][0]).status_code == 429
