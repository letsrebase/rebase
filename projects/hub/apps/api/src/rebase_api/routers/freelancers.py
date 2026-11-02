"""`POST /api/hub/freelancers`: the wizard's application, with the CV when there is one.

Multipart, because the CV is a file: the fields arrive as form values and the PDF as
`cv`, which is optional -- a person with no PDF to hand finishes the form and adds it
later from their area. Everything is validated by `FreelancerCreate` and `check_cv`
exactly as the MCP server would validate it, so the two adapters cannot accept
different things. Public, rate-limited, and mute like the signup: the answer is
`{"ok": true}` whether this was a first application or a repeat of one.

An address already on file gets nothing overwritten by this route (REB-272): it is
unauthenticated, so a second post cannot prove who is sending it. Instead the person
gets the same magic-link mail `/auth/link` sends, with one more sentence, so they land
in their own area to make the change themselves.

The completion is reported to PostHog from here, after the answer, as a background
task (`rebase_core.analytics`, REB-215): the browser's own event is the one an ad
blocker eats, and `distinct_id` -- the id the browser's SDK carries, when it was
allowed to run -- is what lands the two halves on the same person.

A new card that came with a CV gets its anonymous description the same way, after the
answer and in a session of its own (`rebase_core.card_writer.write_after_response`, REB-510):
Claude takes seconds, and the person waiting for the 201 has nothing to wait for.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError

from rebase_api.deps import (
    CardLlmDep,
    SenderDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
    TrackerDep,
)
from rebase_api.ratelimit import spend_one
from rebase_core.amounts import NotAnAmount, italian_amount
from rebase_core.card_writer import write_after_response
from rebase_core.config import Settings
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import (
    ALREADY_HAS_CARD_BY_LINKEDIN_NOTE,
    ALREADY_HAS_CARD_NOTE,
    FreelancerService,
)
from rebase_core.mail import send_after_response
from rebase_core.models import CV_MAX_BYTES
from rebase_core.schema_common import DISTINCT_ID_MAX_LENGTH, Ack
from rebase_core.signup_schemas import SignupUtm
from rebase_core.users import VIA_WIZARD, UserService

router = APIRouter(prefix="/api/hub", tags=["hub"])

_log = logging.getLogger(__name__)

# What the 429 of the hourly ceiling tells a client to wait: the ceiling is an hour wide.
HOURLY_RETRY_AFTER_SECONDS = 3600


def _refuse_above_the_hourly_ceiling(service: FreelancerService, settings: Settings) -> None:
    """The whole world's applications with a CV in the last hour, against
    `applications_with_cv_per_hour`. Each stores up to 5 MB and may start a model call,
    and the per-client speed bump is no ceiling against many addresses (an IPv6 range, a
    botnet): past this one the wizard answers 429 and the person tries again later."""
    since = datetime.now(UTC) - timedelta(hours=1)
    if service.applications_with_cv_since(since) >= settings.applications_with_cv_per_hour:
        _log.warning(
            "applications with a CV are over %s an hour", settings.applications_with_cv_per_hour
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Stiamo ricevendo molte candidature in questo momento. Riprova tra un'ora.",
            headers={"Retry-After": str(HOURLY_RETRY_AFTER_SECONDS)},
        )


def _decimal(value: str, field: str) -> Decimal:
    """The wizard's rate as the person typed it, read the Italian way: «1.500» is 1500,
    «1.234,50» is 1234.50 (`rebase_core.amounts`, REB-485)."""
    try:
        return italian_amount(value)
    except NotAnAmount as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", field], "msg": "serve un numero", "type": "value_error"}],
        ) from exc


def _validation_422(exc: ValidationError) -> HTTPException:
    """The same shape FastAPI gives a JSON body's errors, so the wizard can point at the
    field: `detail[].loc[-1]` is the field name, whatever the transport was."""
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[
            {"loc": ["body", *error["loc"]], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ],
    )


@router.post("/freelancers", response_model=Ack, status_code=status.HTTP_201_CREATED)
def apply(
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    tracker: TrackerDep,
    settings: SettingsDep,
    sender: SenderDep,
    llm: CardLlmDep,
    open_session: SessionOpenerDep,
    nome: Annotated[str, Form()],
    cognome: Annotated[str, Form()],
    email: Annotated[str, Form()],
    tariffa_giornaliera: Annotated[str, Form()],
    posizione: Annotated[str, Form()],
    remoto: Annotated[str, Form()],
    # Optional: the wizard leaves the part out of the body when nobody attached a file,
    # and FastAPI hands `None` here for a part with no filename and for `cv` sent as an
    # empty string field, which is what other clients do for "nothing chosen". A part
    # that *is* a file is a file, even when it carries no bytes: see below.
    cv: Annotated[UploadFile | None, File()] = None,
    linkedin_url: Annotated[str | None, Form()] = None,
    links: Annotated[list[str] | None, Form()] = None,
    utm_source: Annotated[str | None, Form()] = None,
    utm_medium: Annotated[str | None, Form()] = None,
    utm_campaign: Annotated[str | None, Form()] = None,
    utm_content: Annotated[str | None, Form()] = None,
    utm_term: Annotated[str | None, Form()] = None,
    utm_id: Annotated[str | None, Form()] = None,
    origine: Annotated[str | None, Form()] = None,
    distinct_id: Annotated[str | None, Form(max_length=DISTINCT_ID_MAX_LENGTH)] = None,
    rif: Annotated[str | None, Form()] = None,
) -> Ack:
    spend_one(request)
    if cv is not None:
        _refuse_above_the_hourly_ceiling(FreelancerService(session), settings)
    try:
        data = FreelancerCreate(
            nome=nome,
            cognome=cognome,
            email=email,
            linkedin_url=linkedin_url or None,
            tariffa_giornaliera=_decimal(tariffa_giornaliera, "tariffa_giornaliera"),
            posizione=posizione,
            remoto=remoto,  # type: ignore[arg-type]
            links=links or [],
            utm=SignupUtm(
                utm_source=utm_source,
                utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                utm_content=utm_content,
                utm_term=utm_term,
                utm_id=utm_id,
                origine=origine or None,
            ),
            rif=rif or None,
        )
    except ValidationError as exc:
        raise _validation_422(exc) from exc
    # No CV at all is not a refusal: the card is stored without one and `completa` says
    # so. An attached file is checked, and an attached file of zero bytes is checked
    # too, which is the whole reason these two cases are told apart rather than folded
    # into one falsy test: a truncated or empty upload would otherwise be read as "this
    # person chose not to send a CV", and they would be told nothing. A CV that is not a
    # small PDF raises `ValidationFailed`, which the app's handler renders as the same
    # 422 shape as the fields above.
    service = FreelancerService(session)
    if cv is None:
        read, created = service.apply(data)
    else:
        # Never more than the limit `check_cv` enforces: a larger body is refused by it
        # without this process having held the rest.
        read, created = service.apply(
            data, cv.file.read(CV_MAX_BYTES + 1), cv.filename or "", cv.content_type or ""
        )
    if not created and sender is not None:
        users = UserService(session, settings)
        if users.wizard_links_left() == 0:
            # The circuit breaker of the wizard's mail budget (`config.py`): the person is
            # told nothing different, since the answer never says whether the address
            # was new; they can still ask for a link from the login page tomorrow.
            _log.warning("public mail budget spent: the existing-card mail was not sent")
            return Ack()
        # The address typed is on file (or was merged into a card, REB-800): the mail goes
        # there. Otherwise the card was found by its LinkedIn profile, and the mail goes to
        # the address on file, which the person controls and the form never reveals.
        typed = users.person(data.email)
        if typed is not None and typed.email.lower() == read.email.lower():
            mail = users.request_link(data.email, note=ALREADY_HAS_CARD_NOTE, via=VIA_WIZARD)
        else:
            mail = users.request_link(
                read.email, note=ALREADY_HAS_CARD_BY_LINKEDIN_NOTE, via=VIA_WIZARD
            )
        if mail is not None:
            background.add_task(send_after_response, sender, mail, "the existing-card mail")
    if created and tracker is not None:
        background.add_task(
            tracker.application,
            "freelance",
            distinct_id or None,
            cv=cv is not None,
            utm=data.utm,
        )
    # Last: background tasks run in order, and the completion event must not wait
    # behind the seconds Claude takes to write the card.
    if created and cv is not None:
        background.add_task(write_after_response, open_session, llm, read.id)
    return Ack()
