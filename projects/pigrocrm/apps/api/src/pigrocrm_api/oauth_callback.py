"""The end of an OAuth consent, written once for Gmail, Drive and Slack.

The callback is a browser navigation, not an XHR, so it always ends on a page of the SPA
with an outcome code the SPA renders in Italian, and never on a problem document: an RFC
9457 body in the address bar strands the person outside the SPA at the end of a consent
flow, with the one message they could act on in a shape no browser displays usefully. The
page re-reads `GET /account` and shows the state that is true.

`outcome` is looked up in a fixed table on the page, so nothing an attacker appends to the
URL can put words of their own on the screen; Google's and Slack's own `error` is never
forwarded, since it is English and occasionally embeds the client id.
"""

from collections.abc import Callable
from enum import StrEnum

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict, DomainError
from pigrocrm_api.deps import callback_actor
from pigrocrm_api.oauth_relay import relay_to_space
from pigrocrm_api.tenancy import cookie_path


class ConsentOutcome(StrEnum):
    """The `outcome` the SPA reads; the values are the wire contract."""

    CONNECTED = "connected"
    DENIED = "denied"
    FAILED = "error"
    # The browser came back with no live session (REB-446): no access cookie that still
    # works and no refresh cookie that could renew it. Nothing was redeemed, so the person
    # only has to start again once they are signed in.
    NO_SESSION = "session_expired"


# The page of the SPA that renders the outcome, which may depend on who is back: `None` is
# the browser with no session.
LandingPage = str | Callable[[Actor | None], str]


def finish_consent(
    request: Request,
    session: Session,
    settings: Settings,
    *,
    callback_path: str,
    code: str | None,
    state: str | None,
    error: str | None,
    complete: Callable[[str, str, Actor], object],
    page: LandingPage,
    refusals: tuple[type[DomainError], ...] = (Conflict,),
    lends: bool | None = None,
) -> RedirectResponse:
    """Answers the provider's redirect: relays it to the space the `state` names, or
    redeems it with `complete(code, state, actor)` and sends the browser to `page` with the
    outcome.

    A `refusals` exception from `complete` is the outcome `FAILED`: every refusal the flow
    can produce (a replayed or expired state, an account that is not the connected one, a
    grant that came back incomplete) arrives through a browser redirect. Anything else,
    a `PermissionDenied` of a caller who may not perform the flow, is not an outcome of the
    consent and belongs in the problem document like every other 403.

    `lends` is passed to `relay_to_space` as is (see there)."""
    # Before the actor, on purpose: a consent a space started through the root's client
    # comes back here carrying that space's cookie only (`oauth_relay.py`, REB-394).
    relayed = relay_to_space(
        request, settings, callback_path, code=code, state=state, error=error, lends=lends
    )
    if relayed is not None:
        return relayed
    # The access cookie may have run out while the person was on the provider's screens,
    # and the SPA had no chance to renew it before this navigation: `callback_actor` reads
    # the refresh cookie too (REB-446). The state is still redeemed against this actor's
    # own id, so what it adds is a way back for the person who started the flow.
    actor = callback_actor(request, session, settings)
    if actor is None:
        # Never a 401 document in the address bar. The state stays unredeemed and runs out
        # on its own; the page asks the person to sign in and start again.
        return _back(request, page, None, ConsentOutcome.NO_SESSION)
    if error is not None or code is None or state is None:
        return _back(request, page, actor, ConsentOutcome.DENIED)
    try:
        complete(code, state, actor)
    except refusals:
        return _back(request, page, actor, ConsentOutcome.FAILED)
    return _back(request, page, actor, ConsentOutcome.CONNECTED)


def _back(
    request: Request, page: LandingPage, actor: Actor | None, outcome: ConsentOutcome
) -> RedirectResponse:
    # Under the prefix the request wore: a space's consent must end on that space's page,
    # not on the root's. `cookie_path` is the one place that already knows the prefix, and
    # its `/` is the bare root.
    prefix = cookie_path(request).rstrip("/")
    path = page if isinstance(page, str) else page(actor)
    # Snyk Code python/OR here is a false positive: one of the SPA's fixed pages, under the
    # request's own prefix (a slug that matched SLUG_PATTERN in `tenancy.py`, or none for
    # the root), with one of the four constant `outcome` codes.
    return RedirectResponse(f"{prefix}{path}?esito={outcome.value}", status_code=307)
