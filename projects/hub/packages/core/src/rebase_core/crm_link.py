"""A completed company request filed in rebase's own CRM (REB-868, spec 2026-10-30 § 4).

`CrmLeadService` is `EngagementService`'s twin for a request: the same states as a match's
Pigro link (`da_collegare`, `collegato`, `errore`, `rifiutato`), the same house rule (**no
database lock is held across the AI call or the HTTP call**: the row is locked to read what
is sent and to stamp the attempt, released with that commit, and locked again to write what
the CRM answered), the same seam (`HttpCall`, `urllib_engagements_call` in production) and
the same token (`REBASE_PIGRO_ENGAGEMENTS_TOKEN`). The door is
`PUT {pigro_api_url}/api/rebase/leads/{request_id}`, idempotent on the request's id.

The deal is built in code. The AI (`rebase_core.crm_lead`) gives a bounded draft, made once
and kept in `crm_bozza`, so a CRM that is down for a day is retried every sweep without
spending the task's cap again; a call that cannot answer files the request with no draft
(the AI improves a filing, it never blocks one). Every value is cut or dropped to the CRM's
limits before it leaves, so a long value never becomes a permanent 422.

Who runs it: a `BackgroundTasks` job right after the response (`link_after_response`), on
every path that writes a request; `link_pending`, the recovery round of `rebase
contracts-sweep`, which picks up only the rows in `da_collegare` or `errore` that are not
being attempted right now (a row written before this shipped has a `NULL` state and is
never picked up); and an admin's «Riprova», or «Collega a un altro deal» with a `deal_id`,
which makes the door link that deal and create nothing. A request changed after it was
linked changes nothing in the CRM, and a soft-deleted one is not linked.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.crm_lead import LeadDraft, draft_lead
from rebase_core.crm_words import REFERENTE_WITHOUT_NAME, crm_state_sentence
from rebase_core.db import SessionOpener
from rebase_core.errors import InvalidState, NotFound
from rebase_core.framework import rome_today
from rebase_core.http import HttpCall
from rebase_core.llm import LlmCall
from rebase_core.match_words import COLLEGATO, DA_COLLEGARE, ERRORE, HTTPS_ONLY
from rebase_core.models import Company, User
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
LEADS_PATH = "/api/rebase/leads"
# The states the sweep picks up; `rifiutato` waits for a person.
PENDING = (DA_COLLEGARE, ERRORE)
# The sweep leaves a row alone for this long after it was written or last attempted, so it
# never races the job that was started with the request (two calls would spend the AI twice).
GRACE = timedelta(minutes=5)

# The CRM's own limits (`LeadReferente`, `LeadDeal`), met before the call.
COMPANY_MAX = 255
PERSON_NAME_MAX = 120
PHONE_MAX = 40
LINKEDIN_MAX = 255
DEAL_NAME_MAX = 255
# `deals.valore_previsto` is Numeric(12, 2): ten integer digits at most.
VALUE_LIMIT = Decimal(10) ** 10
CENT = Decimal("0.01")

NOT_CONFIGURED = (
    "Il CRM non è configurato su questo ambiente: manca REBASE_PIGRO_ENGAGEMENTS_TOKEN."
)
NO_DRAFT_NOTE = "Nessuna bozza AI: il modello non ha risposto, la scheda è da compilare a mano."
REMOTE_WORDS = {"remoto": "da remoto", "ibrido": "ibrido", "in_sede": "in sede"}


def _cut(value: str | None, limit: int) -> str | None:
    """`value` trimmed and cut to `limit`, `None` for a blank."""
    if value is None:
        return None
    text = value.strip()
    return text[:limit].rstrip() or None


class _Lead(BaseModel):
    """The door's answer, as much as the hub keeps. `deal_url` becomes a link on the admin's
    page: only an HTTPS address is taken as one (plain HTTP only to this machine)."""

    model_config = ConfigDict(extra="ignore")

    customer_id: UUID
    deal_id: UUID
    deal_url: str
    creato: bool = False

    @field_validator("deal_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not speaks_https(value):
            raise ValueError("deal_url is not an https address")
        return value


class _Sent(NamedTuple):
    """What the request says, read under the first lock as plain values."""

    company_id: UUID
    nome_azienda: str
    figura_richiesta: str
    progetto: str
    durata: str
    periodo_da: date
    budget_giornaliero: Decimal
    remoto: str
    giorni_presenza: int | None
    numero_risorse: int
    nome: str
    cognome: str
    email: str
    telefono: str | None
    linkedin_url: str | None
    bozza: dict[str, Any] | None
    stato: str | None
    # The CRM deal an admin named when opening the request (REB-870), kept on the row so a
    # retry or the sweep asks for that deal and never files the customer by its address.
    deal_id: UUID | None


@dataclass(frozen=True)
class CrmLinkResult:
    """What `link` leaves the request at, for the caller that answers or logs it."""

    stato: str | None
    errore: str | None
    deal_url: str | None
    # False when nothing was asked of the CRM: a request deleted, waiting for a token, or one
    # the sweep found already taken care of.
    asked: bool = True


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
        """The door's `PUT` body (spec § 2, § 3): the company's name, the referente, the
        deal as built here, and `deal_id` for a request linked to a deal the CRM has.
        Refused with `_NotAsked` for a referente with no name, which the door would refuse
        in English: `link` records it as `errore`, so a retry after it is completed works."""
        nome = _cut(sent.nome, PERSON_NAME_MAX)
        if nome is None:
            raise _NotAsked(REFERENTE_WITHOUT_NAME)
        ruolo = draft.ruolo if draft is not None else ""
        deal: dict[str, Any] = {
            "nome": (f"{sent.nome_azienda}: {ruolo or sent.figura_richiesta}")[:DEAL_NAME_MAX],
            "valore_previsto": self._value(sent, draft),
            "data_chiusura_prevista": (
                sent.periodo_da.isoformat() if sent.periodo_da > self.today() else None
            ),
            "note": self._note(sent, draft),
        }
        return {
            "azienda": sent.nome_azienda[:COMPANY_MAX],
            "referente": {
                "nome": nome,
                "cognome": _cut(sent.cognome, PERSON_NAME_MAX),
                "email": sent.email,
                "telefono": _cut(sent.telefono, PHONE_MAX),
                "linkedin_url": _cut(sent.linkedin_url, LINKEDIN_MAX),
            },
            "deal": deal,
            "deal_id": str(deal_id) if deal_id is not None else None,
        }

    @staticmethod
    def _value(sent: _Sent, draft: LeadDraft | None) -> str | None:
        """The budget a day times the days a week, the weeks and the people: only when
        every input is there, and dropped when it does not fit the CRM's column."""
        if draft is None or draft.giorni_settimana is None or draft.durata_settimane is None:
            return None
        value = (
            sent.budget_giornaliero
            * Decimal(str(draft.giorni_settimana))
            * draft.durata_settimane
            * sent.numero_risorse
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        if value < 0 or value >= VALUE_LIMIT:
            return None
        return format(value, "f")

    def _note(self, sent: _Sent, draft: LeadDraft | None) -> str:
        """Two blocks, in Italian: what the company wrote, `progetto` verbatim, and the
        AI's draft under its own heading."""
        modo = REMOTE_WORDS.get(sent.remoto, sent.remoto)
        if sent.giorni_presenza is not None:
            modo += f", {sent.giorni_presenza} giorni a settimana in sede"
        hub = f"{self.settings.hub_url.rstrip('/')}/admin/companies/{sent.company_id}"
        written = [
            "Richiesta arrivata dall'hub di rebase.",
            "",
            f"Azienda: {sent.nome_azienda}",
            f"Figura richiesta: {sent.figura_richiesta}",
            f"Persone richieste: {sent.numero_risorse}",
            f"Inizio desiderato: {sent.periodo_da.strftime('%d/%m/%Y')}",
            f"Durata: {sent.durata}",
            f"Budget giornaliero: {sent.budget_giornaliero:.2f} €",
            f"Modalità: {modo}",
            f"Richiesta sull'hub: {hub}",
            "",
            "Progetto, come l'ha scritto l'azienda:",
            sent.progetto,
        ]
        block = ["", "Bozza AI, da rivedere"]
        if draft is None:
            block.append(NO_DRAFT_NOTE)
        else:
            block += [
                f"Ruolo: {draft.ruolo or '-'}",
                "Giorni a settimana: "
                + ("-" if draft.giorni_settimana is None else f"{draft.giorni_settimana:g}"),
                "Durata in settimane: "
                + ("-" if draft.durata_settimane is None else str(draft.durata_settimane)),
                f"Competenze: {', '.join(draft.competenze) or '-'}",
                f"Sintesi: {draft.sintesi or '-'}",
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
        """Files the request in the CRM, or records why not (spec § 4): `collegato` on a
        `201` or `200`; `rifiutato` with the CRM's sentence on a `422`, which only an
        admin's «Riprova» asks again; `errore` with its cause on anything else, which the
        sweep retries. A request with no state (written before the link existed) is set to
        `da_collegare` first, as an admin asking for it means it. Without a token nothing is
        asked and the request waits as `da_collegare`. A request already `collegato` by
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
            unasked = Outcome(ERRORE, HTTPS_ONLY)
        else:
            if deal_id is None:
                draft = self._draft(sent)
            try:
                body = self.payload(sent, draft, deal_id)
            except _NotAsked as missing:
                unasked = Outcome(ERRORE, missing.sentence)
        outcome = unasked if unasked is not None else self._put(company_id, body)

        try:
            row = self._lock(company_id)
            if deal_id is not None and not inherited and outcome.answer is None:
                refusal = crm_state_sentence(outcome.stato, outcome.errore)
                self.session.rollback()
                self._record(admin_id, company_id, outcome, deal_id)
                raise InvalidState(refusal, crm_stato=outcome.stato)
            # A request another caller linked meanwhile, or one that was linked before this
            # call, keeps its link: only a deal the admin named replaces it, and a failure
            # never takes a link away.
            if (deal_id is not None and not inherited) or row.crm_stato != COLLEGATO:
                self._write(row, outcome)
            self.session.commit()
        except InvalidState:
            raise
        except Exception:
            self.session.rollback()
            raise
        if outcome.answer is None:
            _log.warning("request %s is not filed in the CRM: %s", company_id, outcome.stato)
        self._record(admin_id, company_id, outcome, deal_id)
        return self._result(company_id)

    def link_pending(self) -> SweepCount:
        """The sweep's round: `link` on every live request waiting (`da_collegare`) or
        failed (`errore`) that nothing is attempting right now, each on its own, a failure
        logged without stopping the rest. A `rifiutato` request is not asked again, nor a
        row with no state. Without a token nothing is asked."""
        if not self.configured:
            _log.info("no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: no request filed")
            return SweepCount(linked=0, failed=0)
        cutoff = self.now() - GRACE
        rows = self.session.scalars(
            select(Company.id)
            .where(
                Company.crm_stato.in_(PENDING),
                Company.deleted_at.is_(None),
                Company.created_at <= cutoff,
                or_(Company.crm_attempted_at.is_(None), Company.crm_attempted_at <= cutoff),
            )
            .order_by(Company.created_at, Company.id)
        ).all()

        def link(company_id: UUID) -> bool | None:
            result = self.link(company_id, sweep=True)
            return None if not result.asked else result.stato == COLLEGATO

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
        to `da_collegare` if it had none (not for a deal the admin named: a refusal must
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
                if row.crm_stato not in PENDING or busy or row.created_at > cutoff:
                    return None
            if row.crm_stato is None and deal_id is None:
                row.crm_stato = DA_COLLEGARE
            if not self.configured:
                _log.info(
                    "no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: request %s waits "
                    "as da_collegare",
                    company_id,
                )
                return None
            user = self.session.scalars(
                select(User).where(User.id == row.user_id).execution_options(populate_existing=True)
            ).one()
            sent = _Sent(
                company_id=row.id,
                nome_azienda=row.nome_azienda,
                figura_richiesta=row.figura_richiesta,
                progetto=row.progetto,
                durata=row.durata,
                periodo_da=row.periodo_da,
                budget_giornaliero=row.budget_giornaliero,
                remoto=row.remoto,
                giorni_presenza=row.giorni_presenza,
                numero_risorse=row.numero_risorse,
                nome=user.nome,
                cognome=user.cognome,
                email=user.email,
                telefono=user.telefono,
                linkedin_url=user.linkedin_url,
                bozza=row.crm_bozza,
                stato=row.crm_stato,
                deal_id=row.crm_deal_id if row.crm_stato != COLLEGATO else None,
            )
            row.crm_attempted_at = self.now()
        return sent

    def _draft(self, sent: _Sent) -> LeadDraft | None:
        """The kept draft, or one asked for now with no lock held and kept at once: so the
        AI is spent once however many times the CRM has to be asked."""
        if sent.bozza is not None:
            return LeadDraft.from_json(sent.bozza)
        llm = self.llm() if self.llm is not None else None
        draft = draft_lead(
            llm,
            figura_richiesta=sent.figura_richiesta,
            progetto=sent.progetto,
            durata=sent.durata,
            periodo_da=sent.periodo_da,
        )
        if draft is None:
            return None
        with committed(self.session):
            row = self._lock(sent.company_id)
            if row.crm_bozza is None:
                row.crm_bozza = draft.to_json()
            else:  # another caller kept one first: that is the draft
                draft = LeadDraft.from_json(row.crm_bozza)
        return draft

    def _put(self, company_id: UUID, body: dict[str, Any]) -> Outcome[_Lead]:
        """The one call, through the door: a `422` is a refusal."""
        return self.door.put(
            f"{LEADS_PATH}/{company_id}", body, _Lead, subject=f"the request {company_id}"
        )

    @staticmethod
    def _write(row: Company, outcome: Outcome[_Lead]) -> None:
        write(row, outcome, state="crm_stato", error="crm_errore", copy=_copy_lead)

    def _record(
        self, admin_id: UUID | None, company_id: UUID, outcome: Outcome[_Lead], deal_id: UUID | None
    ) -> None:
        if admin_id is None:
            return
        payload: dict[str, Any] = {"esito": outcome.stato, "errore": outcome.errore}
        if deal_id is not None:
            payload["deal_id"] = str(deal_id)
        AdminActionService(self.session).record(ENTITY, company_id, "crm_link", admin_id, payload)

    def _result(self, company_id: UUID, *, asked: bool = True) -> CrmLinkResult:
        row = self.session.scalars(
            select(Company)
            .where(Company.id == company_id)
            .execution_options(populate_existing=True)
        ).one()
        result = CrmLinkResult(row.crm_stato, row.crm_errore, row.crm_deal_url, asked)
        self.session.rollback()
        return result


def _copy_lead(row: Company, lead: _Lead) -> None:
    row.crm_customer_id = lead.customer_id
    row.crm_deal_id = lead.deal_id
    row.crm_deal_url = lead.deal_url


class _NotAsked(Exception):
    """A request the hub will not send to the CRM yet, with the sentence its page shows:
    recorded as `errore`, never `rifiutato`, since it is the hub's own data to complete."""

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
    in `da_collegare` or `errore` and the sweep takes it from there."""
    try:
        with open_session() as session:
            CrmLeadService(session, settings, http, llm).link(company_id)
    except Exception:
        _log.warning("request %s could not be filed in the CRM", company_id, exc_info=True)
