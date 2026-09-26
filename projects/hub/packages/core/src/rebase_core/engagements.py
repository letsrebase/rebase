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

`report` stores nothing but one fact: every «Consuntivo» asks the CRM again, in windows
of at most the CRM's 800 days, and `group_report` sums the rows by day, ISO week and
month. The fact is the CRM's `409`, a deal deleted in the space (spec § 3.10), which
`report` writes over a `collegato` match as `rifiutato`, the way `link` would, so the
card, the sweep and «Riprova» all know it.
"""

import json
import logging
import re
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError
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
from rebase_core.match_words import (
    HTTPS_ONLY,
    PIGRO_NOT_CONFIGURED,
    PROFILE_WITHOUT_NAME,
    SIGNER_CF_TOO_LONG,
    SIGNER_PEC_INVALID,
    pigro_state_sentence,
)
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

# What a failed or refused link stores in `pigro_errore`: the cause alone, never a
# sentence of its own, since the card wraps it (spec § 3.5): «Pigro non ha risposto:
# HTTP 503.», «Pigro ha rifiutato il collegamento: <the CRM's sentence>». A seam
# sentence there would say «Pigro» twice. The CRM's own sentence is the cause whenever
# its answer carries one, on a 409, a 422 or a 503. The exceptions are the hub's own
# sentences for a link it never asked (`match_words.NOT_ASKED`), shown alone.
CAUSE_STATUS = "HTTP {status}"
CAUSE_TIMEOUT = "timeout"
CAUSE_REFUSED = "connessione rifiutata"
CAUSE_UNREACHABLE = "nessuna connessione"
CAUSE_TOO_LONG = "risposta troppo lunga"
CAUSE_NOT_THE_SHAPE = "risposta non leggibile"
# A field of the body the CRM's door refused, named, never in FastAPI's English.
CAUSE_FIELD = "{field}: non valido"
HOURS_PER_DAY = Decimal(8)
# The CRM's own cap on a report's period, `a - da` in days: a longer span it refuses, so
# a longer engagement is read in consecutive windows of this size.
REPORT_MAX_DAYS = 800
ENGAGEMENTS_PATH = "/api/rebase/engagements"
# Where plain HTTP is still acceptable: a CRM running on this machine, in development.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
NOT_ACTIVE = "Si collega a Pigro solo un match attivo."
# The report's refusal for a match with no link state yet (`pigro_stato` NULL: not
# signed, so not active): `pigro_state_sentence` says nothing there, since the card
# has its own words for a match waiting on its signature.
REPORT_NOT_ACTIVE = "Il match non è ancora attivo: nessun consuntivo da leggere."
LETTER_NOT_SIGNED = "La lettera non è firmata."
# The CRM's own customer rules (`CustomerService._check_fiscal`, the door's
# `EngagementRebase`), met before the call so a value the space would refuse never
# leaves: an address of 255 characters at most, a recipient code of exactly seven, a tax
# code of 16 at most, and a PEC that is an address by the rule of the CRM's `EmailStr`.
ADDRESS_MAX_LENGTH = 255
SDI_LENGTH = 7
FISCAL_CODE_MAX_LENGTH = 16
_EMAIL: TypeAdapter[str] = TypeAdapter(EmailStr)
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


def normalise_fiscal_code(value: str | None) -> str | None:
    """rebase's tax code as the CRM is sent it: spaces gone, upper case, `None` for a
    blank. Its length is `payload`'s to check."""
    if value is None:
        return None
    return "".join(value.split()).upper() or None


def _is_email(value: str) -> bool:
    try:
        _EMAIL.validate_python(value)
    except ValidationError:
        return False
    return True


def _cause(raw: bytes, status: int) -> str:
    """What a link the CRM refused, or answered «not now», stores: the CRM's own
    sentence, or the bare status (`CAUSE_STATUS`)."""
    return _crm_sentence(raw) or CAUSE_STATUS.format(status=status)


def _failure(exc: Exception) -> str:
    """The cause of a call that never got an answer: `urllib` wraps the socket's own
    error in `URLError`, a read that stalls raises `TimeoutError` bare."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return CAUSE_TIMEOUT
    if isinstance(reason, ConnectionRefusedError):
        return CAUSE_REFUSED
    return CAUSE_UNREACHABLE


def _crm_sentence(raw: bytes) -> str | None:
    """The CRM's own sentence in an answer, `None` when it carries none. A `Conflict`'s
    problem document (`code` `conflict`) by its `reason`: its `detail` is `entity:
    reason`, «engagement: Il deal di questa lettera è stato eliminato nello spazio.»,
    and an admin reads the sentence, not the entity. Any other problem document by its
    `detail`, which for a `ValidationFailed` is `entity.field: reason` and keeps the
    field's name. FastAPI's own list of errors, whose words are English and repeat the
    value, as `field: non valido` for each field it names."""
    if not raw or len(raw) > MAX_BODY_BYTES:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    reason = body.get("reason")
    if body.get("code") == "conflict" and isinstance(reason, str) and reason.strip():
        return reason.strip()[:ERRORE_MAX_LENGTH]
    detail = body.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:ERRORE_MAX_LENGTH]
    if isinstance(detail, list):
        fields = dict.fromkeys(
            ".".join(str(part) for part in error["loc"] if part != "body")
            for error in detail
            if isinstance(error, dict) and isinstance(error.get("loc"), list)
        )
        named = [CAUSE_FIELD.format(field=field) for field in fields if field]
        if named:
            return "; ".join(named)[:ERRORE_MAX_LENGTH]
    return None


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
    link, and the CRM's answer for a link. `gone` marks the door's `409`: for a match
    already linked, the one `409` it has is its deal deleted in the space (spec
    § 3.10), which `link` writes even over `collegato`."""

    stato: str
    errore: str | None = None
    linked: _Linked | None = None
    gone: bool = False


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


class _NotAsked(Exception):
    """A match the hub will not send to the CRM yet, with the sentence its card shows
    (`PROFILE_WITHOUT_NAME`, `SIGNER_PEC_INVALID`, `SIGNER_CF_TOO_LONG`): recorded as
    `errore`, never as `rifiutato`, since it is the hub's own data to complete, and a
    retry after that may well succeed."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


class _Gone(Exception):
    """The report's `409`: the match's deal was deleted in the space, with the cause the
    match stores (`_cause`)."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


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
        self.matches = MatchService(
            session, today=today, pigro_configurato=bool(settings.pigro_engagements_token)
        )

    # ---- the body ----------------------------------------------------------------------

    def payload(
        self, match: Match, letter: ContractDocument, user: User, company: Company
    ) -> dict[str, Any]:
        """The door's `PUT` body (spec § 2.3): the freelancer, the letter as the match
        kept it (its printed data for a match older than migration 0021), and rebase as
        `REBASE_SIGNER_JSON` names it over `rebase.json`, normalised to the CRM's own
        customer rules. Refused for a letter not signed: only an active match links.
        Refused with `_NotAsked` for a freelancer whose name or surname is empty, and for
        a PEC or a tax code of rebase's that the CRM's door would refuse in English:
        `link` records it as `errore`, so the sweep and «Riprova» try again once the
        profile or `REBASE_SIGNER_JSON` is complete."""
        if letter.stato != "firmato":
            raise InvalidState(LETTER_NOT_SIGNED, stato=letter.stato)
        if not user.nome.strip() or not user.cognome.strip():
            raise _NotAsked(PROFILE_WITHOUT_NAME)
        signer = merge_data(company_defaults(), signer_data(self.settings.signer_json))
        pec = _text(signer.get("rebase-pec"))
        if pec is not None and not _is_email(pec):
            raise _NotAsked(SIGNER_PEC_INVALID)
        codice_fiscale = normalise_fiscal_code(_text(signer.get("rebase-cf")))
        if codice_fiscale is not None and len(codice_fiscale) > FISCAL_CODE_MAX_LENGTH:
            raise _NotAsked(SIGNER_CF_TOO_LONG)
        data = letter.data
        start = match.lettera_data_inizio or _printed_date(data, "data-inizio")
        if start is None:
            raise ContractFailed(f"letter {letter.numero} printed no data-inizio")
        end = match.lettera_data_fine or _printed_date(data, "data-fine")
        fee = match.lettera_compenso if match.lettera_compenso is not None else amount(data, FEE)
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
                "codice_fiscale": codice_fiscale,
                "indirizzo": sede[:ADDRESS_MAX_LENGTH] if sede is not None else None,
                "pec": pec,
                "codice_sdi": sdi if sdi is not None and len(sdi) == SDI_LENGTH else None,
            },
        }

    # ---- the link ----------------------------------------------------------------------

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        """Links an active match to its deal on Pigro, or records why not (spec § 3.3):
        `collegato` on a `201` or `200`; `rifiutato` with the CRM's sentence on a `409`
        or `422`, which only an admin's «Riprova» asks again; `errore` with its cause
        (`CAUSE_*`, or the CRM's sentence on a `503`) on anything else, and with
        `HTTPS_ONLY` for a CRM not on HTTPS, which the sweep retries. A match already
        `collegato` keeps its state, except on a `409`: its deal was deleted in the
        space (spec § 3.10), unless another caller linked it after this call began.
        Without a token nothing is asked: the match waits as `da_collegare`. With
        `admin_id`, the trail says who asked (`pigro_link`)."""
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
            unasked: _Outcome | None = None
            body: dict[str, Any] = {}
            try:
                body = self.payload(match, letter, user, company)
            except _NotAsked as missing:
                unasked = _Outcome(ERRORE, missing.sentence)
            recipient = _Recipient(user.email, user.nome, letter.numero or "", company.nome_azienda)
            started = self.now()
            match.pigro_attempted_at = started
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        outcome = unasked if unasked is not None else self._put(match_id, body)

        claimed = False
        try:
            self.matches.lock_freelancer(freelancer_id)
            match = self.matches.lock_match(match_id)
            # A link made by another caller after this one began may be newer than this
            # call's 409, which then says nothing about the deal it found.
            linked_since = match.pigro_linked_at is not None and match.pigro_linked_at > started
            if match.pigro_stato != COLLEGATO or (outcome.gone and not linked_since):
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
        match not `collegato`, with where its link stands (`REPORT_NOT_ACTIVE` when it
        has no link state yet), and for a deal the CRM says was deleted in the space (a
        `409`, spec § 3.10), after writing the match `rifiutato` with the CRM's sentence
        if it is still `collegato`, as `link` would: the sweep then leaves it alone, and
        «Riprova» links it again once the freelancer restores the deal.
        `PigroUnavailable` for a CRM not configured here (`PIGRO_NOT_CONFIGURED`), not on
        HTTPS (`HTTPS_ONLY`) or not answering with a report."""
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
            sentence = (
                REPORT_NOT_ACTIVE
                if match.pigro_stato is None
                else pigro_state_sentence(match.pigro_stato, match.pigro_errore)
            )
            raise InvalidState(sentence, pigro_stato=match.pigro_stato)
        pigro_url, giorni_previsti = match.pigro_url, match.giorni_previsti
        freelancer_id = match.freelancer_id
        a = a if a is not None else self.today()
        start = da if da is not None else self._start(match)
        # Nothing the CRM is asked for keeps this transaction open while it answers.
        self.session.rollback()
        if da is not None and da > a:
            raise ValidationFailed(ENTITY, "da", "il periodo inizia dopo la sua fine")
        # `da = min(start, a)`: a letter signed before its start would otherwise send a
        # `da` after `a`, which the CRM refuses with a 422. It has no hours yet, so today
        # alone is asked for and the report is empty, while a deal gone or a CRM down
        # still shows here as everywhere else.
        try:
            windows = [
                self._window(match_id, first, last) for first, last in _windows(min(start, a), a)
            ]
        except _Gone as gone:
            self._refused(match_id, freelancer_id, gone.cause)
            raise InvalidState(
                pigro_state_sentence(RIFIUTATO, gone.cause), pigro_stato=RIFIUTATO
            ) from None
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

    def _refused(self, match_id: UUID, freelancer_id: UUID, cause: str) -> None:
        """The report's `409` written over the match, under the locks `link` takes in the
        same order: `rifiutato` with the CRM's sentence, if the match is still
        `collegato` (another caller may have written something newer meanwhile)."""
        try:
            self.matches.lock_freelancer(freelancer_id)
            match = self.matches.lock_match(match_id)
            if match.pigro_stato == COLLEGATO:
                self._write(match, _Outcome(RIFIUTATO, cause))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        _log.warning("the deal of match %s is gone from its Pigro space", match_id)

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
            return _Outcome(ERRORE, _failure(exc))
        if status in (200, 201):
            if len(raw) > MAX_BODY_BYTES:
                return _Outcome(ERRORE, CAUSE_TOO_LONG)
            try:
                return _Outcome(COLLEGATO, linked=_Linked.model_validate_json(raw))
            except ValidationError:
                return _Outcome(ERRORE, CAUSE_NOT_THE_SHAPE)
        if status == 409:
            return _Outcome(RIFIUTATO, _cause(raw, status), gone=True)
        if status == 422:
            return _Outcome(RIFIUTATO, _cause(raw, status))
        if status == 503:
            # The door's «not now» (its lock busy, the space unreachable), in its words.
            return _Outcome(ERRORE, _cause(raw, status))
        return _Outcome(ERRORE, CAUSE_STATUS.format(status=status))

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
            # The deal was deleted in the space (spec § 3.10): `report` files the match
            # as refused with the CRM's own sentence, the one `link` would store.
            raise _Gone(_cause(raw, status))
        if status != 200:
            raise PigroUnavailable(ANSWERED_STATUS.format(status=status))
        if len(raw) > MAX_BODY_BYTES:
            raise PigroUnavailable(TOO_LONG)
        try:
            return _CrmReport.model_validate_json(raw)
        except ValidationError as exc:
            raise PigroUnavailable(NOT_THE_SHAPE) from exc
