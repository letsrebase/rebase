"""A completed company request filed in rebase's own CRM (REB-868, spec 2026-10-30 § 4).

`CrmLeadService` files a request through the CRM's leads door: the states of its link
(`to_link`, `linked`, `error`, `rejected`, `crm_words`), the house rule (**no
database lock is held across the AI call or the HTTP call**: the row is locked to read what
is sent and to stamp the attempt, released with that commit, and locked again to write what
the CRM answered), the seam (`HttpCall`, `urllib_engagements_call` in production) and
the token (`REBASE_PIGRO_ENGAGEMENTS_TOKEN`). The door is
`PUT {pigro_api_url}/api/rebase/leads/{request_id}`, idempotent on the request's id.

The deal is built in code. The AI (`rebase_core.crm_lead`) gives a bounded draft, made once
and kept in `crm_draft`, so a CRM that is down for a day is retried every sweep without
spending the task's cap again; a call that cannot answer files the request with no draft
(the AI improves a filing, it never blocks one). Every value is cut or dropped to the CRM's
limits before it leaves, so a long value never becomes a permanent 422. The contact goes with
its login address (`email`), its primary phone and LinkedIn profile, and every stored contact
point (`contact_points`, REB-945): a point the CRM would refuse is left out, never cut. The CRM
must accept `contact.contact_points` before the hub sends it (its door refuses unknown fields),
so `pigrocrm-v*` ships before `hub-v*`.

Who runs it: a `BackgroundTasks` job right after the response (`link_after_response`), on
every path that writes a request; `link_pending`, the recovery round of `rebase
contracts-sweep`, which picks up only the rows in `to_link` or `error` that are not
being attempted right now (a row written before this shipped has a `NULL` state and is
never picked up); and an admin's «Riprova», or «Collega a un altro deal» with a `deal_id`,
which makes the door link that deal and create nothing. A request changed after it was
linked changes nothing in the CRM, and a soft-deleted one is not linked.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.crm_lead import LeadDraft, draft_lead
from rebase_core.crm_words import (
    CONTACT_PERSON_WITHOUT_NAME,
    ERROR,
    HTTPS_ONLY,
    LINKED,
    TO_LINK,
    crm_state_sentence,
)
from rebase_core.db import SessionOpener
from rebase_core.errors import InvalidState, NotFound
from rebase_core.http import HttpCall
from rebase_core.llm import LlmCall
from rebase_core.models import (
    CONTACT_NOTE_MAX_LENGTH,
    CONTACT_VALUE_MAX_LENGTH,
    Company,
    User,
    UserContactPoint,
)
from rebase_core.pigro_door import (
    Outcome,
    PigroDoor,
    SweepCount,
    committed,
    speaks_https,
    sweep_round,
    write,
)

_log = logging.getLogger(__name__)

ENTITY = "company"
# Dates are Rome's, where rebase works: a request filed at 23:30 UTC on 30 September is dated
# 1 October.
ROME = ZoneInfo("Europe/Rome")
LEADS_PATH = "/api/rebase/leads"
# The states the sweep picks up; `rejected` waits for a person.
PENDING = (TO_LINK, ERROR)
# The sweep leaves a row alone for this long after it was written or last attempted, so it
# never races the job that was started with the request (two calls would spend the AI twice).
GRACE = timedelta(minutes=5)

# The CRM's own limits (`LeadContactPerson`, `LeadDeal`), met before the call.
COMPANY_MAX = 255
PERSON_NAME_MAX = 120
PHONE_MAX = 40
LINKEDIN_MAX = 255
# `ContactPointIn` and `LeadContactPerson.contact_points`: a point's value and note are the
# hub's own bounds (`UserContactPoint`) and the list is capped at `MAX_CONTACT_POINTS`.
CONTACT_POINTS_MAX = 30
DEAL_NAME_MAX = 255
# A line's price is Numeric(12, 2) and its quantity Numeric(8, 2) in the CRM, and a line's total
# (and so the deal's) must fit Numeric(12, 2): ten integer digits at most.
VALUE_LIMIT = Decimal(10) ** 10
QUANTITY_LIMIT = Decimal(10) ** 6
CENT = Decimal("0.01")
WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12

NOT_CONFIGURED = (
    "Il CRM non è configurato su questo ambiente: manca REBASE_PIGRO_ENGAGEMENTS_TOKEN."
)
NO_DRAFT_NOTE = "Nessuna bozza AI: il modello non ha risposto, la scheda è da compilare a mano."
REMOTE_WORDS = {"remote": "da remoto", "hybrid": "ibrido", "on_site": "in sede"}


def _cut(value: str | None, limit: int) -> str | None:
    """`value` trimmed and cut to `limit`, `None` for a blank."""
    if value is None:
        return None
    text = value.strip()
    return text[:limit].rstrip() or None


def contact_point_payload(points: Sequence[UserContactPoint]) -> list[dict[str, Any]]:
    """The contact's stored points in the shape the door takes (`kind`, `value`, `label`,
    `note`, `is_primary`; no id, no position), the login address left out: it is the door's
    `email`. A point the CRM would refuse, a value or note past its bound or with a NUL byte,
    is left out and never cut, and so is whatever is past the list's bound. What the CRM's
    own rules refuse (an address that is not one, a number with too few digits) the door
    leaves out itself, so the request is never refused for a point."""
    sent: list[dict[str, Any]] = []
    for point in points:
        value = point.value.strip()
        note = (point.note or "").strip() or None
        if (
            not value
            or len(value) > CONTACT_VALUE_MAX_LENGTH
            or (note is not None and len(note) > CONTACT_NOTE_MAX_LENGTH)
            or "\x00" in value
            or (note is not None and "\x00" in note)
        ):
            continue
        sent.append(
            {
                "kind": point.kind,
                "value": value,
                "label": point.label,
                "note": note,
                "is_primary": point.is_primary,
            }
        )
    return sent[:CONTACT_POINTS_MAX]


class _Lead(BaseModel):
    """The door's answer, as much as the hub keeps. `deal_url` becomes a link on the admin's
    page: only an HTTPS address is taken as one (plain HTTP only to this machine)."""

    model_config = ConfigDict(extra="ignore")

    customer_id: UUID
    deal_id: UUID
    deal_url: str
    created: bool = False

    @field_validator("deal_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not speaks_https(value):
            raise ValueError("deal_url is not an https address")
        return value


class _Sent(NamedTuple):
    """What the request says, read under the first lock as plain values."""

    company_id: UUID
    company_name: str
    requested_role: str
    project: str
    duration: str
    period_from: date
    daily_budget: Decimal
    remote: str
    onsite_days: int | None
    headcount: int
    first_name: str
    last_name: str
    email: str
    phone: str | None
    linkedin_url: str | None
    # The person's stored points, the login address not among them (REB-945).
    contact_points: list[dict[str, Any]]
    draft: dict[str, Any] | None
    status: str | None
    # The CRM deal an admin named when opening the request (REB-870), kept on the row so a
    # retry or the sweep asks for that deal and never files the customer by its address.
    deal_id: UUID | None


@dataclass(frozen=True)
class CrmLinkResult:
    """What `link` leaves the request at, for the caller that answers or logs it."""

    status: str | None
    error: str | None
    deal_url: str | None
    # False when nothing was asked of the CRM: a request deleted, waiting for a token, or one
    # the sweep found already taken care of.
    asked: bool = True


def rome_today() -> date:
    return datetime.now(ROME).date()


class CrmLeadService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        http: HttpCall,
        llm: Callable[[], LlmCall | None] | None = None,
        *,
        now: Callable[[], datetime] = utcnow,
        today: Callable[[], date] = rome_today,
    ) -> None:
        """`http` is `urllib_engagements_call` in production. `llm` answers the seam of the
        `crm_lead` task when it is asked for, so «Modelli AI» is read when the draft is
        needed and never earlier; without one every request is filed with no draft."""
        self.session = session
        self.settings = settings
        self.door = PigroDoor(settings.pigro_api_url, settings.pigro_engagements_token, http)
        self.llm = llm
        self.now = now
        self.today = today

    @property
    def configured(self) -> bool:
        return bool(self.settings.pigro_engagements_token)

    # ---- the body ----------------------------------------------------------------------

    def payload(self, sent: _Sent, draft: LeadDraft | None, deal_id: UUID | None) -> dict[str, Any]:
        """The door's `PUT` body (spec § 2, § 3): the company's name, the `referente`, the
        deal as built here, and `deal_id` for a request linked to a deal the CRM has.
        Refused with `_NotAsked` for a `referente` with no name, which the door would refuse
        in English: `link` records it as `error`, so a retry after it is completed works."""
        first_name = _cut(sent.first_name, PERSON_NAME_MAX)
        if first_name is None:
            raise _NotAsked(CONTACT_PERSON_WITHOUT_NAME)
        role = draft.role if draft is not None else ""
        deal: dict[str, Any] = {
            "name": (f"{sent.company_name}: {role or sent.requested_role}")[:DEAL_NAME_MAX],
            "value_lines": self._value_lines(sent, draft),
            "expected_close_date": (
                sent.period_from.isoformat() if sent.period_from > self.today() else None
            ),
            "notes": self._note(sent, draft),
        }
        return {
            "company": sent.company_name[:COMPANY_MAX],
            "contact": {
                "first_name": first_name,
                "last_name": _cut(sent.last_name, PERSON_NAME_MAX),
                "email": sent.email,
                "phone": _cut(sent.phone, PHONE_MAX),
                "linkedin_url": _cut(sent.linkedin_url, LINKEDIN_MAX),
                "contact_points": sent.contact_points,
            },
            "deal": deal,
            "deal_id": str(deal_id) if deal_id is not None else None,
        }

    @staticmethod
    def _value_lines(sent: _Sent, draft: LeadDraft | None) -> list[dict[str, Any]] | None:
        """A request is always a day rate (spec 2026-11-03 § 9): one `day` line priced at the
        daily budget and starting on `period_from`, even when that date is past.

        With a duration in the draft the line is `one_off`: the days are the days a week times
        the weeks and the people, and they fall in the months the weeks cover. With none it
        is `monthly` and open-ended, at the days a month the same week makes, so the CRM
        counts it at 12 months instead of leaving the deal with no value. No line without a
        daily budget or the days a week, nor when the price, the quantity or the line's total
        would not fit the CRM's columns, or when its months would run past year 9999, so a large
        value or a far date never becomes a permanent 422."""
        if draft is None or draft.week_days is None or sent.daily_budget <= 0:
            return None
        price = sent.daily_budget.quantize(CENT, rounding=ROUND_HALF_UP)
        days_a_week = Decimal(str(draft.week_days)) * sent.headcount
        duration_months: int | None
        if draft.duration_weeks is not None:
            period, occurrences = "one_off", 1
            quantity = days_a_week * draft.duration_weeks
            duration_months = -(-draft.duration_weeks * MONTHS_PER_YEAR // WEEKS_PER_YEAR)
        else:
            # The CRM counts an open-ended line at 12 occurrences.
            period, occurrences = "monthly", MONTHS_PER_YEAR
            quantity = days_a_week * WEEKS_PER_YEAR / MONTHS_PER_YEAR
            duration_months = None
        quantity = quantity.quantize(CENT, rounding=ROUND_HALF_UP)
        if not 0 < quantity < QUANTITY_LIMIT or not 0 <= price < VALUE_LIMIT:
            return None
        # Rounded once over every occurrence, as the CRM's `term_total` does.
        if (price * quantity * occurrences).quantize(CENT, rounding=ROUND_HALF_UP) >= VALUE_LIMIT:
            return None
        # The CRM steps the line's dates by calendar months, and a date past year 9999 is an
        # error there: a request starting that late is sent with no line.
        reach = sent.period_from.year * MONTHS_PER_YEAR + sent.period_from.month - 1
        if (reach + (duration_months or MONTHS_PER_YEAR)) // MONTHS_PER_YEAR > date.max.year:
            return None
        return [
            {
                "unit": "day",
                "delivery_period": period,
                "unit_price": format(price, "f"),
                "quantity": format(quantity, "f"),
                "start": sent.period_from.isoformat(),
                "duration_months": duration_months,
            }
        ]

    def _note(self, sent: _Sent, draft: LeadDraft | None) -> str:
        """Two blocks, in Italian: what the company wrote, `project` verbatim, and the
        AI's draft under its own heading."""
        mode = REMOTE_WORDS.get(sent.remote, sent.remote)
        if sent.onsite_days is not None:
            mode += f", {sent.onsite_days} giorni a settimana in sede"
        hub = f"{self.settings.hub_url.rstrip('/')}/admin/companies/{sent.company_id}"
        written = [
            "Richiesta arrivata dall'hub di rebase.",
            "",
            f"Azienda: {sent.company_name}",
            f"Figura richiesta: {sent.requested_role}",
            f"Persone richieste: {sent.headcount}",
            f"Inizio desiderato: {sent.period_from.strftime('%d/%m/%Y')}",
            f"Durata: {sent.duration}",
            f"Budget giornaliero: {sent.daily_budget:.2f} €",
            f"Modalità: {mode}",
            f"Richiesta sull'hub: {hub}",
            "",
            "Progetto, come l'ha scritto l'azienda:",
            sent.project,
        ]
        block = ["", "Bozza AI, da rivedere"]
        if draft is None:
            block.append(NO_DRAFT_NOTE)
        else:
            block += [
                f"Ruolo: {draft.role or '-'}",
                "Giorni a settimana: "
                + ("-" if draft.week_days is None else f"{draft.week_days:g}"),
                "Durata in settimane: "
                + ("-" if draft.duration_weeks is None else str(draft.duration_weeks)),
                f"Competenze: {', '.join(draft.skills) or '-'}",
                f"Sintesi: {draft.profile_summary or '-'}",
            ]
        return "\n".join(written + block)

    # ---- the link ----------------------------------------------------------------------

    def link(
        self,
        company_id: UUID,
        admin_id: UUID | None = None,
        deal_id: UUID | None = None,
        *,
        sweep: bool = False,
    ) -> CrmLinkResult:
        """Files the request in the CRM, or records why not (spec § 4): `linked` on a
        `201` or `200`; `rejected` with the CRM's sentence on a `422`, which only an
        admin's «Riprova» asks again; `error` with its cause on anything else, which the
        sweep retries. A request with no state (written before the link existed) is set to
        `to_link` first, as an admin asking for it means it. Without a token nothing is
        asked and the request waits as `to_link`. A request already `linked` by
        another caller while this one was away keeps its link.

        With `deal_id` the door links that deal and creates nothing («Collega a un altro
        deal»): no draft is asked for, and a failure changes nothing stored, since the
        sweep would otherwise file the request by its address and make the second customer
        the admin is correcting; the refusal comes back as an `InvalidState` with the
        CRM's sentence. With `admin_id` the trail says who asked (`crm_link`).

        With `sweep`, the row is looked at again under its lock and left alone unless it is
        still waiting or failed and nothing attempted it within `GRACE`: the sweep chose its
        rows a while ago, and another caller may have filed one since."""
        sent = self._begin(company_id, deal_id, sweep)
        if sent is None:
            return self._result(company_id, asked=False)

        # A request opened for a deal the CRM has (REB-870) carries it on its own row until
        # linked: the same ask whoever repeats it, and a refusal is written, not raised.
        inherited = deal_id is None and sent.deal_id is not None
        if inherited:
            deal_id = sent.deal_id

        draft: LeadDraft | None = None
        body: dict[str, Any] = {}
        unasked: Outcome[_Lead] | None = None
        if not speaks_https(self.door.api_url):
            unasked = Outcome(ERROR, HTTPS_ONLY)
        else:
            if deal_id is None:
                draft = self._draft(sent)
            try:
                body = self.payload(sent, draft, deal_id)
            except _NotAsked as missing:
                unasked = Outcome(ERROR, missing.sentence)
        outcome = unasked if unasked is not None else self._put(company_id, body)

        try:
            row = self._lock(company_id)
            if deal_id is not None and not inherited and outcome.answer is None:
                refusal = crm_state_sentence(outcome.status, outcome.error)
                self.session.rollback()
                self._record(admin_id, company_id, outcome, deal_id)
                raise InvalidState(refusal, crm_status=outcome.status)
            # A request another caller linked meanwhile, or one that was linked before this
            # call, keeps its link: only a deal the admin named replaces it, and a failure
            # never takes a link away.
            if (deal_id is not None and not inherited) or row.crm_status != LINKED:
                self._write(row, outcome)
            self.session.commit()
        except InvalidState:
            raise
        except Exception:
            self.session.rollback()
            raise
        if outcome.answer is None:
            _log.warning("request %s is not filed in the CRM: %s", company_id, outcome.status)
        self._record(admin_id, company_id, outcome, deal_id)
        return self._result(company_id)

    def link_pending(self) -> SweepCount:
        """The sweep's round: `link` on every live request waiting (`to_link`) or
        failed (`error`) that nothing is attempting right now, each on its own, a failure
        logged without stopping the rest. A `rejected` request is not asked again, nor a
        row with no state. Without a token nothing is asked."""
        if not self.configured:
            _log.info("no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: no request filed")
            return SweepCount(linked=0, failed=0)
        cutoff = self.now() - GRACE
        rows = self.session.scalars(
            select(Company.id)
            .where(
                Company.crm_status.in_(PENDING),
                Company.deleted_at.is_(None),
                Company.created_at <= cutoff,
                or_(Company.crm_attempted_at.is_(None), Company.crm_attempted_at <= cutoff),
            )
            .order_by(Company.created_at, Company.id)
        ).all()

        def link(company_id: UUID) -> bool | None:
            result = self.link(company_id, sweep=True)
            return None if not result.asked else result.status == LINKED

        return sweep_round(self.session, rows, link, what="request")

    # ---- the steps ---------------------------------------------------------------------

    def _lock(self, company_id: UUID) -> Company:
        row = self.session.scalars(
            select(Company)
            .where(Company.id == company_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if row is None:
            raise NotFound(ENTITY, company_id)
        return row

    def _begin(self, company_id: UUID, deal_id: UUID | None, sweep: bool) -> _Sent | None:
        """Under the row's lock: what the request says, the attempt stamped, the state set
        to `to_link` if it had none (not for a deal the admin named: a refusal must
        leave the row out of the sweep), then committed and released. `None` when nothing is
        to be asked: a soft-deleted request, which is not linked, a sweep that finds the row
        taken care of, or no token (the request waits)."""
        with committed(self.session):
            row = self._lock(company_id)
            if row.deleted_at is not None:
                return None
            if sweep:
                cutoff = self.now() - GRACE
                busy = row.crm_attempted_at is not None and row.crm_attempted_at > cutoff
                if row.crm_status not in PENDING or busy or row.created_at > cutoff:
                    return None
            if row.crm_status is None and deal_id is None:
                row.crm_status = TO_LINK
            if not self.configured:
                _log.info(
                    "no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: request %s waits "
                    "as to_link",
                    company_id,
                )
                return None
            user = self.session.scalars(
                select(User).where(User.id == row.user_id).execution_options(populate_existing=True)
            ).one()
            sent = _Sent(
                company_id=row.id,
                company_name=row.company_name,
                requested_role=row.requested_role,
                project=row.project,
                duration=row.duration,
                period_from=row.period_from,
                daily_budget=row.daily_budget,
                remote=row.remote,
                onsite_days=row.onsite_days,
                headcount=row.headcount,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                phone=user.phone,
                linkedin_url=user.linkedin_url,
                contact_points=contact_point_payload(user.contact_points),
                draft=row.crm_draft,
                status=row.crm_status,
                deal_id=row.crm_deal_id if row.crm_status != LINKED else None,
            )
            row.crm_attempted_at = self.now()
        return sent

    def _draft(self, sent: _Sent) -> LeadDraft | None:
        """The kept draft, or one asked for now with no lock held and kept at once: so the
        AI is spent once however many times the CRM has to be asked."""
        if sent.draft is not None:
            return LeadDraft.from_json(sent.draft)
        llm = self.llm() if self.llm is not None else None
        draft = draft_lead(
            llm,
            requested_role=sent.requested_role,
            project=sent.project,
            duration=sent.duration,
            period_from=sent.period_from,
        )
        if draft is None:
            return None
        with committed(self.session):
            row = self._lock(sent.company_id)
            if row.crm_draft is None:
                row.crm_draft = draft.to_json()
            else:  # another caller kept one first: that is the draft
                draft = LeadDraft.from_json(row.crm_draft)
        return draft

    def _put(self, company_id: UUID, body: dict[str, Any]) -> Outcome[_Lead]:
        """The one call, through the door: a `422` is a refusal."""
        return self.door.put(
            f"{LEADS_PATH}/{company_id}", body, _Lead, subject=f"the request {company_id}"
        )

    @staticmethod
    def _write(row: Company, outcome: Outcome[_Lead]) -> None:
        write(row, outcome, state="crm_status", error="crm_error", copy=_copy_lead)

    def _record(
        self, admin_id: UUID | None, company_id: UUID, outcome: Outcome[_Lead], deal_id: UUID | None
    ) -> None:
        if admin_id is None:
            return
        payload: dict[str, Any] = {"outcome": outcome.status, "error": outcome.error}
        if deal_id is not None:
            payload["deal_id"] = str(deal_id)
        AdminActionService(self.session).record(ENTITY, company_id, "crm_link", admin_id, payload)

    def _result(self, company_id: UUID, *, asked: bool = True) -> CrmLinkResult:
        row = self.session.scalars(
            select(Company)
            .where(Company.id == company_id)
            .execution_options(populate_existing=True)
        ).one()
        result = CrmLinkResult(row.crm_status, row.crm_error, row.crm_deal_url, asked)
        self.session.rollback()
        return result


def _copy_lead(row: Company, lead: _Lead) -> None:
    row.crm_customer_id = lead.customer_id
    row.crm_deal_id = lead.deal_id
    row.crm_deal_url = lead.deal_url


class _NotAsked(Exception):
    """A request the hub will not send to the CRM yet, with the sentence its page shows:
    recorded as `error`, never `rejected`, since it is the hub's own data to complete."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


def link_after_response(
    open_session: SessionOpener,
    settings: Settings,
    http: HttpCall,
    llm: Callable[[], LlmCall | None] | None,
    company_id: UUID,
) -> None:
    """The first attempt, run by `BackgroundTasks` after the response, in a session of its
    own (the request's is closed by then). It never raises: whatever fails, the request is
    in `to_link` or `error` and the sweep takes it from there."""
    try:
        with open_session() as session:
            CrmLeadService(session, settings, http, llm).link(company_id)
    except Exception:
        _log.warning("request %s could not be filed in the CRM", company_id, exc_info=True)
