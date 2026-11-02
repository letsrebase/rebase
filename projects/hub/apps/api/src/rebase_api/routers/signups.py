"""`POST /api/community/signups`: a signup for the rebase community.

Public, deliberately: the page that posts here is the community site, and its visitor
has no account -- that is the whole point of the form. The path is the one the website
posts to since 2026-09-15 (REB-212); before that it was `/api/orbiters/signups`, which
still proxies here from nginx so an old link or a cached page keeps working. Before
2026-09-09 the whole endpoint lived on PigroCRM's API; its body, its answer and its
limiter carried over unchanged, so the form and the ad conversion never noticed.

Two properties follow from being public, and both live here:

- The answer is `SignupAck` -- `{"ok": true}` and a 201, the same for a first signup
  and for an address already on the list. It used to be the stored row, which turned
  this endpoint into an oracle: post somebody else's address and the response handed
  back their real nome, cognome and LinkedIn profile. The whole row is admin-gated
  (the admin API of the hub, step 3 of its spec) and stays that way.
- A small per-client rate limit (`ratelimit.py`), shared with the hub's other two public
  writes.

The one thing this route does besides writing the row is measure the ad conversion, in a
background task, after the response. `rebase_core.conversions` says what is sent
and what deliberately is not; the reason it is here rather than in `SignupService` is
that everything it needs beyond the id -- the visitor's address, their user agent, the
`__obref` cookie -- is a property of this HTTP request and of nothing else.
"""

import ipaddress
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Request, status

from rebase_api.deps import SessionDep, SettingsDep
from rebase_api.ratelimit import observed_address, spend_one
from rebase_core.conversions import Visitor, pixel_from_settings
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupAck, SignupCreate

# The cookie the measurement SDK sets on our own domain. First-party, so the browser
# sends it to this endpoint too, which is the only reason the server event can carry it.
OBREF_COOKIE = "__obref"

router = APIRouter(prefix="/api/community", tags=["community"])


def _visitor_ip(request: Request) -> str | None:
    """The visitor's address, or `None` when what we have is not one.

    The limiter keys on `client_key`, which wants *a* key (an IPv6 address as its /64,
    a word when there is no address). This one is a value forwarded to a third party as
    an IP address, so the address as written must be a real one, and anything else
    becomes no field at all rather than nonsense in somebody else's database.
    """
    candidate = observed_address(request)
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _measure_the_conversion(data: SignupCreate, request: Request, settings: SettingsDep) -> None:
    """Schedules nothing and decides nothing: builds the event and returns.

    Called from a background task, so it runs *after* the response has been sent. Two
    things follow, and both are the point: the person who signed up never waits for
    OpenAI, and an outage there cannot turn a signup that was written into an error.
    """
    pixel = pixel_from_settings(settings)
    if pixel is None:
        return
    pixel.send(
        # The id the landing already gave the browser event, so the two are one
        # conversion. When there is none -- a client that posted without the pixel, a
        # curl -- one is invented here: it makes the event unpairable, which is correct,
        # since no browser event exists to pair it with.
        event_id=data.pixel_event_id or uuid4().hex,
        source_url=settings.signup_url,
        visitor=Visitor(
            oppref=data.oppref,
            obref=request.cookies.get(OBREF_COOKIE),
            ip_address=_visitor_ip(request),
            user_agent=request.headers.get("user-agent"),
            email=data.email,
        ),
    )


@router.post("/signups", response_model=SignupAck, status_code=status.HTTP_201_CREATED)
def subscribe(
    data: SignupCreate,
    session: SessionDep,
    request: Request,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> SignupAck:
    """201 and `{"ok": true}`, whether the address was new or already on the list.

    The person is on the list either way, which is the only thing the page says and the
    only thing this answers: telling a caller *which* of the two happened is telling
    them whether an address they do not own is a subscriber.

    The ad conversion is measured after the answer, never before it: see
    `_measure_the_conversion`. It is scheduled even for an address already on the list,
    because the browser fires its own event on the same submit and the two carry one id
    -- suppressing the server half would not remove that event, it would only remove the
    half that survives an ad blocker.
    """
    spend_one(request)
    SignupService(session).subscribe(data)
    background.add_task(_measure_the_conversion, data, request, settings)
    return SignupAck()
