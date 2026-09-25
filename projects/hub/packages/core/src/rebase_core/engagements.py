"""An active match linked to its deal on Pigro, and that deal's hours read back (REB-498).

The hub's twin of `rebase_core.pigro`'s registry client, through the same `HttpCall`
seam, against the CRM's door for rebase's engagements (`PUT` and `GET
/api/rebase/engagements/{match_id}`, under `REBASE_PIGRO_ENGAGEMENTS_TOKEN`). The one
difference is the seam it is handed in production, `urllib_engagements_call`: the first
`PUT` for a freelancer provisions their space's database and may take a minute and more.
The token travels only over HTTPS (`HTTPS_ONLY`), plain HTTP only to this machine.
Design record: `docs/superpowers/specs/2026-09-25-hours-report-per-match-design.md`,
§ 3.3 and § 3.5.

`link` never holds a row lock across a network call, the house rule of
`SigningService._confirm_completion`: the freelancer's row and the match's are locked
to read what the CRM is sent and to stamp the attempt, released with that commit, and
locked again, in the same order, to write what the CRM answered over the row as it is
by then. A match another caller linked meanwhile keeps its state. The freelancer's mail
is claimed in that same locked write (`pigro_mail_sent_at` stamped before the send), so
of two callers racing on one match only the one that claimed it sends; a refusal gives
the claim back for the next call.

`report` stores nothing: every «Consuntivo» asks the CRM again, in windows of at most
the CRM's 800 days, and `group_report` sums the rows by day, ISO week and month.
"""

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    MatchRead,
    MatchReport,
    ReportDay,
    ReportInvoice,
    ReportMonth,
    ReportWeek,
)
from rebase_core.contracts.fields import (
    CENT,
    FEE,
    ContractFailed,
    Value,
    amount,
    merge_data,
    parse_italian_date,
    signer_data,
)
from rebase_core.contracts.render import company_defaults
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.framework import ROME, rome_today
from rebase_core.http import MAX_BODY_BYTES, HttpCall
from rebase_core.mail import EmailSender, engagement_ready_mail
from rebase_core.matches import ENTITY, MatchService
from rebase_core.models import Company, ContractDocument, Freelancer, Match, User
from rebase_core.pigro import (
    ANSWERED_STATUS,
    NOT_ANSWERING,
    NOT_THE_SHAPE,
    TOO_LONG,
    PigroUnavailable,
)

_log = logging.getLogger(__name__)

PIGRO_NOT_CONFIGURED = "Consuntivo non configurato su questo ambiente."
HTTPS_ONLY = "Pigro è raggiungibile solo su https."
HOURS_PER_DAY = Decimal(8)
# The CRM's own cap on a report's period, `a - da` in days: a longer span it refuses, so
# a longer engagement is read in consecutive windows of this size.
REPORT_MAX_DAYS = 800
ENGAGEMENTS_PATH = "/api/rebase/engagements"
# Where plain HTTP is still acceptable: a CRM running on this machine, in development.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
NOT_ACTIVE = "Si collega a Pigro solo un match attivo."
LETTER_NOT_SIGNED = "La lettera non è firmata."
# The CRM's own customer rules (`CustomerService._check_fiscal`), met before the call so
# a value the space would refuse never leaves: an address of 255 characters at most, a
# recipient code of exactly seven.
ADDRESS_MAX_LENGTH = 255
SDI_LENGTH = 7
# A refusal's sentence as stored on the match: the CRM's own words, bounded, since a
# body of a megabyte is still a body the seam lets through.
ERRORE_MAX_LENGTH = 500
_VAT = re.compile(r"[0-9]{11}")

DA_COLLEGARE, COLLEGATO, ERRORE, RIFIUTATO = "da_collegare", "collegato", "errore", "rifiutato"


class PigroLinkResult(NamedTuple):
    """`link_pending`'s count, for the sweep's line: `linked` is how many matches this
    round took to `collegato`, `failed` how many it tried and left otherwise (an
    `errore`, a `rifiutato`, or a call that raised)."""

    linked: int
    failed: int


def normalise_vat(value: str | None) -> str | None:
    """`IT 0123 456 7890` as `01234567890`: spaces gone, a leading `IT` dropped, and
    `None` unless exactly eleven digits remain, which is all the CRM accepts."""
    if value is None:
        return None
    compact = "".join(value.split()).upper().removeprefix("IT")
    return compact if _VAT.fullmatch(compact) else None


def speaks_https(url: str) -> bool:
    """Whether the bearer may travel to `url`: HTTPS, or plain HTTP to this machine."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def _text(value: Value) -> str | None:
    """A signer field as text: `None` for a blank or a value that is not text."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _hours(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _printed_date(data: Mapping[str, Any], key: str) -> date | None:
    """A date as the letter printed it (`1° ottobre 2026`), `None` when it printed none."""
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ContractFailed(f"{key} is {value!r}, not a date as a contract writes it")
    return parse_italian_date(value)


def _pigro_state_sentence(pigro_stato: str | None, pigro_errore: str | None) -> str:
    """Why a match has no report: where its link stands, in the words its card uses."""
    if pigro_stato == DA_COLLEGARE:
        return "Pigro non ha ancora il deal: riprova o aspetta lo sweep."
    if pigro_stato == ERRORE:
        return f"Pigro non ha risposto: {pigro_errore or NOT_ANSWERING}"
    if pigro_stato == RIFIUTATO:
        refused = "Pigro ha rifiutato il collegamento"
        return f"{refused}: {pigro_errore}" if pigro_errore else f"{refused}."
    return "Il match non è collegato a Pigro: si collega quando la lettera è firmata."


def _refusal(raw: bytes, status: int) -> str:
    """The CRM's own sentence for a refusal: a problem document's `detail`, or FastAPI's
    list of errors as `field: reason` (the rejected value itself left out); the status
    when the body says neither."""
    fallback = ANSWERED_STATUS.format(status=status)
    if not raw or len(raw) > MAX_BODY_BYTES:
        return fallback
    try:
        body = json.loads(raw)
    except ValueError:
        return fallback
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:ERRORE_MAX_LENGTH]
    if isinstance(detail, list):
        reasons = []
        for error in detail:
            if not isinstance(error, dict) or not isinstance(error.get("msg"), str):
                continue
            loc = error.get("loc")
            where = (
                ".".join(str(part) for part in loc if part != "body")
                if isinstance(loc, list)
                else ""
            )
            reasons.append(f"{where}: {error['msg']}" if where else error["msg"])
        if reasons:
            return "; ".join(reasons)[:ERRORE_MAX_LENGTH]
    return fallback


def _windows(da: date, a: date) -> list[tuple[date, date]]:
    """`[da, a]` in consecutive windows the CRM accepts: `[da, da + 800]`, then from the
    next day on, the last one ending on `a`."""
    windows = []
    start = da
    while True:
        end = min(start + timedelta(days=REPORT_MAX_DAYS), a)
        windows.append((start, end))
        if end >= a:
            return windows
        start = end + timedelta(days=1)


# ---- what the CRM answers ---------------------------------------------------------------


class _Linked(BaseModel):
    """The door's answer to a `PUT`, as much of it as the hub keeps. `deal_url` becomes a
    link on the admin's card, in the member area and in a mail: only a web address is
    taken as one."""

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(min_length=1, max_length=32)
    deal_id: UUID
    deal_url: str = Field(min_length=1, pattern=r"^https?://")
    spazio_creato: bool = False


class _CrmInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID | None = None
    tipo: str
    anno: int | None = None
    numero: int | None = None
    stato: str
    stato_pagamento: str
    data: date | None = None
    ore: Decimal | None = None


class _CrmEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: date
    ore: Decimal
    descrizione: str | None = None
    fattura: _CrmInvoice | None = None


class _CrmReport(BaseModel):
    """The door's report, as much of it as the hub reads: its rows, the CRM's own split
    of billed hours, the invoices, and the deal as it stands."""

    model_config = ConfigDict(extra="ignore")

    deal: dict[str, Any] | None = None
    giorni: list[_CrmEntry]
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    fatture: list[_CrmInvoice] = Field(default_factory=list)


def _joined(windows: list[_CrmReport]) -> _CrmReport:
    """The windows of one period as one report: the rows one after the other, the billed
    hours summed, an invoice that holds hours of two windows once with both, newest
    first as the CRM lists them, and the deal as the last window found it."""
    invoices: dict[UUID | int, _CrmInvoice] = {}
    for window in reversed(windows):
        for invoice in window.fatture:
            key: UUID | int = invoice.id if invoice.id is not None else id(invoice)
            seen = invoices.get(key)
            if seen is None:
                invoices[key] = invoice
            else:
                total = (seen.ore or Decimal(0)) + (invoice.ore or Decimal(0))
                invoices[key] = seen.model_copy(update={"ore": total})
    return _CrmReport(
        deal=windows[-1].deal,
        giorni=[entry for window in windows for entry in window.giorni],
        ore_fatturate=sum((window.ore_fatturate for window in windows), Decimal(0)),
        ore_non_fatturate=sum((window.ore_non_fatturate for window in windows), Decimal(0)),
        fatture=list(invoices.values()),
    )


class _Outcome(NamedTuple):
    """What one `PUT` came to: the state to write, the sentence for anything but a
    link, and the CRM's answer for a link."""

    stato: str
    errore: str | None = None
    linked: _Linked | None = None


def _invoice_number(invoice: _CrmInvoice) -> str:
    if invoice.numero is None:
        return "senza numero"
    return f"{invoice.numero}/{invoice.anno}" if invoice.anno is not None else str(invoice.numero)


def _invoice_label(invoice: _CrmInvoice) -> str:
    number = _invoice_number(invoice)
    return number if invoice.tipo == "fattura" else f"{invoice.tipo} {number}"


def _labels(entries: Iterable[_CrmEntry]) -> list[str]:
    """The invoices a day's hours sit on, each named once, in the order they came."""
    return list(dict.fromkeys(_invoice_label(e.fattura) for e in entries if e.fattura is not None))


def group_report(crm: dict[str, Any], giorni_previsti: int | None) -> dict[str, Any]:
    """The CRM's report (one row per time entry) as `MatchReport`'s figures: summed by
    day, by ISO week (Monday to Sunday, «2026-W53» running into January) and by month,
    the total and its share of `giorni_previsti` times eight. The billed hours are the
    CRM's own figures, since only the CRM knows which invoices count. Pure: a body that
    is not the report raises `ValidationError`."""
    return _group(_CrmReport.model_validate(crm), giorni_previsti)


def _group(report: _CrmReport, giorni_previsti: int | None) -> dict[str, Any]:
    by_day: dict[date, list[_CrmEntry]] = {}
    for entry in sorted(report.giorni, key=lambda row: row.data):
        by_day.setdefault(entry.data, []).append(entry)
    per_giorno = [
        ReportDay(
            data=day,
            ore=_hours(sum((e.ore for e in entries), Decimal(0))),
            descrizioni=[
                e.descrizione.strip()
                for e in entries
                if e.descrizione is not None and e.descrizione.strip()
            ],
            fatture=_labels(entries),
        )
        for day, entries in by_day.items()
    ]
    weeks: dict[tuple[int, int], Decimal] = {}
    months: dict[tuple[int, int], Decimal] = {}
    for day in per_giorno:
        iso = day.data.isocalendar()
        weeks[(iso.year, iso.week)] = weeks.get((iso.year, iso.week), Decimal(0)) + day.ore
        month = (day.data.year, day.data.month)
        months[month] = months.get(month, Decimal(0)) + day.ore
    totale = _hours(sum((day.ore for day in per_giorno), Decimal(0)))
    ore_previste = _hours(giorni_previsti * HOURS_PER_DAY) if giorni_previsti else None
    return {
        "giorni_previsti": giorni_previsti,
        "ore_previste": ore_previste,
        "totale_ore": totale,
        "giorni_equivalenti": _hours(totale / HOURS_PER_DAY),
        "avanzamento": _hours(totale / ore_previste * 100) if ore_previste else None,
        "ore_fatturate": _hours(report.ore_fatturate),
        "ore_non_fatturate": _hours(report.ore_non_fatturate),
        "per_giorno": per_giorno,
        "per_settimana": [
            ReportWeek(
                settimana=f"{year}-W{week:02d}",
                da=date.fromisocalendar(year, week, 1),
                a=date.fromisocalendar(year, week, 7),
                ore=_hours(hours),
            )
            for (year, week), hours in sorted(weeks.items())
        ],
        "per_mese": [
            ReportMonth(mese=f"{year}-{month:02d}", ore=_hours(hours))
            for (year, month), hours in sorted(months.items())
        ],
        "fatture": [
            ReportInvoice(
                numero=_invoice_number(invoice),
                tipo=invoice.tipo,
                data=invoice.data,
                stato=invoice.stato,
                stato_pagamento=invoice.stato_pagamento,
                ore=_hours(invoice.ore or Decimal(0)),
            )
            for invoice in report.fatture
        ],
    }


class _Recipient(NamedTuple):
    """What the freelancer's mail needs, read under the first lock."""

    email: str
    nome: str
    numero: str
    azienda: str


class EngagementService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        http: HttpCall,
        *,
        sender: EmailSender | None = None,
        now: Callable[[], datetime] = utcnow,
        today: Callable[[], date] = rome_today,
    ) -> None:
        """`http` is `urllib_engagements_call` in production; `sender` is needed only for
        the freelancer's mail, and without one a link still happens, unannounced."""
        self.session = session
        self.settings = settings
        self.http = http
        self.sender = sender
        self.now = now
        self.today = today
        self.matches = MatchService(session, today=today)

    # ---- the body ----------------------------------------------------------------------

    def payload(
        self, match: Match, letter: ContractDocument, user: User, company: Company
    ) -> dict[str, Any]:
        """The door's `PUT` body (spec § 2.3): the freelancer, the letter as the match
        kept it (its printed data for a match older than migration 0021), and rebase as
        `REBASE_SIGNER_JSON` names it over `rebase.json`, normalised to the CRM's own
        customer rules. Refused for a letter not signed: only an active match links."""
        if letter.stato != "firmato":
            raise InvalidState(LETTER_NOT_SIGNED, stato=letter.stato)
        data = letter.data
        start = match.lettera_data_inizio or _printed_date(data, "data-inizio")
        if start is None:
            raise ContractFailed(f"letter {letter.numero} printed no data-inizio")
        end = match.lettera_data_fine or _printed_date(data, "data-fine")
        fee = match.lettera_compenso if match.lettera_compenso is not None else amount(data, FEE)
        signer = merge_data(company_defaults(), signer_data(self.settings.signer_json))
        sede = _text(signer.get("rebase-sede"))
        sdi = _text(signer.get("rebase-codice-destinatario"))
        return {
            "freelancer": {"email": user.email, "nome": user.nome, "cognome": user.cognome},
            "lettera": {
                "numero": letter.numero,
                "ruolo": data.get("ruolo"),
                "azienda": company.nome_azienda,
                "data_inizio": start.isoformat(),
                "data_fine": end.isoformat() if end is not None else None,
                "compenso": str(fee.quantize(CENT)) if fee is not None else None,
                "giorni_previsti": match.giorni_previsti,
            },
            "rebase": {
                "ragione_sociale": _text(signer.get("rebase-ragione-sociale")),
                "partita_iva": normalise_vat(_text(signer.get("rebase-piva"))),
                "codice_fiscale": _text(signer.get("rebase-cf")),
                "indirizzo": sede[:ADDRESS_MAX_LENGTH] if sede is not None else None,
                "pec": _text(signer.get("rebase-pec")),
                "codice_sdi": sdi if sdi is not None and len(sdi) == SDI_LENGTH else None,
            },
        }

    # ---- the link ----------------------------------------------------------------------

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        """Links an active match to its deal on Pigro, or records why not (spec § 3.3):
        `collegato` on a `201` or `200`; `rifiutato` with the CRM's sentence on a `409`
        or `422`, which only an admin's «Riprova» asks again; `errore` with the seam's
        sentence on anything else, a CRM not on HTTPS among them, which the sweep
        retries. Without a token nothing is asked: the match waits as `da_collegare`.
        With `admin_id`, the trail says who asked (`pigro_link`)."""
        try:
            freelancer_id = self.matches.match_freelancer(match_id)
            self.matches.lock_freelancer(freelancer_id)
            match = self.matches.lock_match(match_id)
            if match.stato != "attivo":
                raise InvalidState(NOT_ACTIVE, stato=match.stato)
            if not self.settings.pigro_engagements_token:
                if match.pigro_stato is None:
                    match.pigro_stato = DA_COLLEGARE
                self.session.commit()
                _log.info(
                    "no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: match %s waits "
                    "as da_collegare",
                    match_id,
                )
                return self.matches.get(match_id)
            letter, user, company = self._parts(match)
            body = self.payload(match, letter, user, company)
            recipient = _Recipient(user.email, user.nome, letter.numero or "", company.nome_azienda)
            match.pigro_attempted_at = self.now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        outcome = self._put(match_id, body)

        claimed = False
        try:
            self.matches.lock_freelancer(freelancer_id)
            match = self.matches.lock_match(match_id)
            if match.pigro_stato != COLLEGATO:
                self._write(match, outcome)
            if (
                match.pigro_stato == COLLEGATO
                and match.pigro_mail_sent_at is None
                and match.pigro_url is not None
                and self.sender is not None
            ):
                # The claim: whoever stamps it under this lock is the one caller that
                # sends, and gives it back below if the provider refuses.
                match.pigro_mail_sent_at = self.now()
                claimed = True
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        stato, errore, deal_url = match.pigro_stato, match.pigro_errore, match.pigro_url
        if stato != COLLEGATO:
            _log.warning("match %s is not linked to Pigro: %s", match_id, stato)
        if claimed and deal_url is not None:
            spazio_creato = outcome.linked is not None and outcome.linked.spazio_creato
            self._mail(match_id, freelancer_id, recipient, deal_url, spazio_creato)
        if admin_id is not None:
            AdminActionService(self.session).record(
                ENTITY, match_id, "pigro_link", admin_id, {"esito": stato, "errore": errore}
            )
        return self.matches.get(match_id)

    def link_pending(self) -> PigroLinkResult:
        """The sweep's round (spec § 3.3): `link` on every active match waiting
        (`da_collegare`) or failed (`errore`), and on a linked one whose mail the
        provider refused, each on its own, a failure logged without stopping the rest.
        A `rifiutato` match is not asked again. Without a token nothing is asked; without
        a mail sender a linked match has nothing left to do here."""
        if not self.settings.pigro_engagements_token:
            _log.info("no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: no match linked")
            return PigroLinkResult(linked=0, failed=0)
        due: ColumnElement[bool] = Match.pigro_stato.in_((DA_COLLEGARE, ERRORE))
        if self.sender is not None:
            due = or_(due, and_(Match.pigro_stato == COLLEGATO, Match.pigro_mail_sent_at.is_(None)))
        rows = self.session.execute(
            select(Match.id, Match.pigro_stato)
            .join(Freelancer, Freelancer.id == Match.freelancer_id)
            .where(Match.stato == "attivo", Freelancer.deleted_at.is_(None), due)
            .order_by(Match.created_at, Match.id)
        ).all()
        self.session.rollback()
        linked = failed = 0
        for match_id, before in rows:
            try:
                read = self.link(match_id)
            except Exception:
                self.session.rollback()
                _log.warning("match %s could not be linked to Pigro", match_id, exc_info=True)
                failed += 1
                continue
            if read.pigro_stato != COLLEGATO:
                failed += 1
            elif before != COLLEGATO:
                linked += 1
        return PigroLinkResult(linked=linked, failed=failed)

    # ---- the report --------------------------------------------------------------------

    def report(self, match_id: UUID, da: date | None = None, a: date | None = None) -> MatchReport:
        """The hours on the match's deal (spec § 3.5), by default over the whole
        engagement: from the letter's start (the printed one for a match older than
        migration 0021, the match's own creation day when the letter has none) to
        today, asked in windows of at most `REPORT_MAX_DAYS`. `InvalidState` for a
        match not `collegato`, with where its link stands; `PigroUnavailable` for a CRM
        not configured here (`PIGRO_NOT_CONFIGURED`), not on HTTPS (`HTTPS_ONLY`) or not
        answering with a report."""
        match = self.session.scalars(
            select(Match).where(Match.id == match_id).execution_options(populate_existing=True)
        ).first()
        if match is None:
            raise NotFound(ENTITY, match_id)
        if not self.settings.pigro_engagements_token:
            raise PigroUnavailable(PIGRO_NOT_CONFIGURED)
        if not speaks_https(self.settings.pigro_api_url):
            raise PigroUnavailable(HTTPS_ONLY)
        if match.pigro_stato != COLLEGATO:
            raise InvalidState(
                _pigro_state_sentence(match.pigro_stato, match.pigro_errore),
                pigro_stato=match.pigro_stato,
            )
        pigro_url, giorni_previsti = match.pigro_url, match.giorni_previsti
        a = a if a is not None else self.today()
        start = da if da is not None else self._start(match)
        # Nothing the CRM is asked for keeps this transaction open while it answers.
        self.session.rollback()
        if da is not None and da > a:
            raise ValidationFailed(ENTITY, "da", "il periodo inizia dopo la sua fine")
        # A letter that starts after today has no hours yet: the CRM is still asked for
        # today's, so a deal gone or a CRM down shows here as everywhere else.
        windows = [
            self._window(match_id, first, last) for first, last in _windows(min(start, a), a)
        ]
        return MatchReport(
            match_id=match_id,
            pigro_url=pigro_url,
            pigro_stato=COLLEGATO,
            **_group(_joined(windows), giorni_previsti),
        )

    # ---- the steps ---------------------------------------------------------------------

    def _url(self, match_id: UUID) -> str:
        return f"{self.settings.pigro_api_url.rstrip('/')}{ENGAGEMENTS_PATH}/{match_id}"

    def _headers(self, *, with_body: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.pigro_engagements_token}",
            "Accept": "application/json",
        }
        if with_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _parts(self, match: Match) -> tuple[ContractDocument, User, Company]:
        """The match's letter, its freelancer's user and its request, read again rather
        than from the session's memory."""
        letter = self._letter(match.id)
        if letter is None:
            raise NotFound("lettera", match.id)
        user = self.session.scalars(
            select(User)
            .join(Freelancer, Freelancer.user_id == User.id)
            .where(Freelancer.id == match.freelancer_id)
            .execution_options(populate_existing=True)
        ).one()
        company = self.session.scalars(
            select(Company)
            .where(Company.id == match.company_id)
            .execution_options(populate_existing=True)
        ).one()
        return letter, user, company

    def _letter(self, match_id: UUID) -> ContractDocument | None:
        """The match's newest document, the letter `MatchService.get` reads."""
        return self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).first()

    def _start(self, match: Match) -> date:
        """Where the engagement's report starts by default: the letter's start as the
        match kept it, else as the letter printed it, else the day the match was
        written, in Rome. Never today, which would hide every hour before it."""
        if match.lettera_data_inizio is not None:
            return match.lettera_data_inizio
        letter = self._letter(match.id)
        printed = _printed_date(letter.data, "data-inizio") if letter is not None else None
        return printed or match.created_at.astimezone(ROME).date()

    def _put(self, match_id: UUID, body: dict[str, Any]) -> _Outcome:
        """The one call, with no row lock held and only over HTTPS. Never raises:
        whatever happens is an outcome to write."""
        if not speaks_https(self.settings.pigro_api_url):
            return _Outcome(ERRORE, HTTPS_ONLY)
        try:
            status, raw = self.http(
                "PUT", self._url(match_id), self._headers(with_body=True), json.dumps(body).encode()
            )
        except Exception as exc:  # noqa: BLE001 - a refused connection, a DNS miss, a timeout
            _log.warning(
                "Pigro did not answer the link of match %s (%s)", match_id, type(exc).__name__
            )
            return _Outcome(ERRORE, NOT_ANSWERING)
        if status in (200, 201):
            if len(raw) > MAX_BODY_BYTES:
                return _Outcome(ERRORE, TOO_LONG)
            try:
                return _Outcome(COLLEGATO, linked=_Linked.model_validate_json(raw))
            except ValidationError:
                return _Outcome(ERRORE, NOT_THE_SHAPE)
        if status in (409, 422):
            return _Outcome(RIFIUTATO, _refusal(raw, status))
        return _Outcome(ERRORE, ANSWERED_STATUS.format(status=status))

    def _write(self, match: Match, outcome: _Outcome) -> None:
        match.pigro_stato = outcome.stato
        if outcome.linked is None:
            match.pigro_errore = outcome.errore
            return
        match.pigro_slug = outcome.linked.slug
        match.pigro_deal_id = outcome.linked.deal_id
        match.pigro_url = outcome.linked.deal_url
        match.pigro_linked_at = self.now()
        match.pigro_errore = None

    def _mail(
        self,
        match_id: UUID,
        freelancer_id: UUID,
        recipient: _Recipient,
        deal_url: str,
        spazio_creato: bool,
    ) -> None:
        """The freelancer's mail, by the one caller that claimed it, with no lock held; a
        refusal takes the claim back under the lock, for the next call to send."""
        assert self.sender is not None
        mail = engagement_ready_mail(
            recipient.email,
            nome=recipient.nome,
            numero=recipient.numero,
            azienda=recipient.azienda,
            deal_url=deal_url,
            spazio_creato=spazio_creato,
        )
        if self.sender.send(mail):
            return
        _log.warning("the Pigro mail of match %s was refused by the provider", match_id)
        try:
            self.matches.lock_freelancer(freelancer_id)
            self.matches.lock_match(match_id).pigro_mail_sent_at = None
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _window(self, match_id: UUID, da: date, a: date) -> _CrmReport:
        """One `GET` of the report, `a - da` within the CRM's cap."""
        query = urlencode({"da": da.isoformat(), "a": a.isoformat()})
        try:
            status, raw = self.http(
                "GET", f"{self._url(match_id)}/report?{query}", self._headers(), b""
            )
        except Exception as exc:  # noqa: BLE001 - a refused connection, a DNS miss, a timeout
            raise PigroUnavailable(NOT_ANSWERING) from exc
        if status == 409:
            # The deal was deleted in the space (spec § 3.10): the CRM's own sentence,
            # the same one `link` stores, since this page is where an admin learns it.
            raise PigroUnavailable(_refusal(raw, status))
        if status != 200:
            raise PigroUnavailable(ANSWERED_STATUS.format(status=status))
        if len(raw) > MAX_BODY_BYTES:
            raise PigroUnavailable(TOO_LONG)
        try:
            return _CrmReport.model_validate_json(raw)
        except ValidationError as exc:
            raise PigroUnavailable(NOT_THE_SHAPE) from exc
