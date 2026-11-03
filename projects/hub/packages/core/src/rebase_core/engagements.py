"""An active match linked to its deal on Pigro, and that deal's hours read back (REB-498).

The hub's twin of `rebase_core.pigro`'s registry client, through the same `HttpCall`
seam and the same door (`rebase_core.pigro_door`: the call, what its status means, the
sweep round and the write, shared with the leads door of `crm_link`), against the CRM's
door for rebase's engagements (`PUT` and `GET /api/rebase/engagements/{match_id}`, under
`REBASE_PIGRO_ENGAGEMENTS_TOKEN`). The one
difference is the seam it is handed in production, `urllib_engagements_call`: the first
`PUT` for a freelancer provisions their space's database and may take a minute and more.
The token travels only over HTTPS (`HTTPS_ONLY`), plain HTTP only to this machine.
Design record: `docs/superpowers/specs/2026-09-25-hours-report-per-match-design.md`,
§ 3.3 and § 3.5.

`link` never holds a row lock across a network call, the house rule of
`SigningRecovery._confirm_completion`: the freelancer's row and the match's are locked
to read what the CRM is sent and to stamp the attempt, released with that commit, and
locked again, in the same order, to write what the CRM answered over the row as it is
by then. A match another caller linked meanwhile keeps its state. The freelancer's mail
is claimed in that same locked write (`pigro_mail_sent_at` stamped before the send), so
of two callers racing on one match only the one that claimed it sends; a refusal gives
the claim back for the next call.

`report` stores nothing but one fact: every «Consuntivo» asks the CRM again, in windows
of at most the CRM's 800 days, and `engagement_report` sums the rows by day, ISO week and
month. The fact is the CRM's `409`, a deal deleted in the space (spec § 3.10), which
`report` writes over a `linked` match as `rejected`, the way `link` would, so the
card, the sweep and «Riprova» all know it.
"""

import logging
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, NamedTuple
from urllib.parse import urlencode
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    MatchRead,
    MatchReport,
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
from rebase_core.engagement_report import (
    CrmReport,
    group,
    join_windows,
    report_windows,
)
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.framework import ROME, rome_today
from rebase_core.http import HttpCall
from rebase_core.mail import EmailSender
from rebase_core.mail_contracts import engagement_ready_mail
from rebase_core.match_reads import MatchReads
from rebase_core.match_rows import ENTITY, lock_freelancer, lock_match, match_freelancer
from rebase_core.match_words import (
    ERROR,
    HTTPS_ONLY,
    LINKED,
    PIGRO_NOT_CONFIGURED,
    PROFILE_WITHOUT_NAME,
    REJECTED,
    TO_LINK,
    pigro_state_sentence,
)
from rebase_core.models import Company, ContractDocument, Freelancer, Match, User
from rebase_core.pigro_door import (
    Gone,
    Outcome,
    PigroDoor,
    PigroUnavailable,
    SweepCount,
    committed,
    speaks_https,
    sweep_round,
    write,
)

_log = logging.getLogger(__name__)

ENGAGEMENTS_PATH = "/api/rebase/engagements"
NOT_ACTIVE = "Si collega a Pigro solo un match attivo."
# The report's refusal for a match with no link state yet (`pigro_status` NULL: not
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
_VAT = re.compile(r"[0-9]{11}")


def normalise_vat(value: str | None) -> str | None:
    """`IT 0123 456 7890` as `01234567890`: spaces gone, a leading `IT` dropped, and
    `None` unless exactly eleven digits remain, which is all the CRM accepts."""
    if value is None:
        return None
    compact = "".join(value.split()).upper().removeprefix("IT")
    return compact if _VAT.fullmatch(compact) else None


def _text(value: Value) -> str | None:
    """A signer field as text: `None` for a blank or a value that is not text."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


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
    blank. Its length is `payload`'s to check: over 16 characters it is not sent."""
    if value is None:
        return None
    return "".join(value.split()).upper() or None


def _is_email(value: str) -> bool:
    try:
        _EMAIL.validate_python(value)
    except ValidationError:
        return False
    return True


# ---- what the CRM answers ---------------------------------------------------------------


class _Linked(BaseModel):
    """The door's answer to a `PUT`, as much of it as the hub keeps. `deal_url` becomes a
    link on the admin's card, in the member area and in a mail: only an HTTPS address is
    taken as one (plain HTTP only to this machine, as for the bearer: `speaks_https`),
    so no one follows it in clear."""

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(min_length=1, max_length=32)
    deal_id: UUID
    deal_url: str = Field(min_length=1)
    space_created: bool = False

    @field_validator("deal_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not speaks_https(value):
            raise ValueError("deal_url is not an https address")
        return value


class _NotAsked(Exception):
    """A match the hub will not send to the CRM yet, with the sentence its card shows
    (`PROFILE_WITHOUT_NAME`): recorded as `error`, never as `rejected`, since it is
    the hub's own data to complete, and a retry after that may well succeed."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


class _Recipient(NamedTuple):
    """What the freelancer's mail needs, read under the first lock."""

    email: str
    nome: str
    number: str
    company: str


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
        self.door = PigroDoor(settings.pigro_api_url, settings.pigro_engagements_token, http)
        self.sender = sender
        self.now = now
        self.today = today
        self.reads = MatchReads(
            session, today, pigro_configured=bool(settings.pigro_engagements_token)
        )

    # ---- the body ----------------------------------------------------------------------

    def payload(
        self, match: Match, letter: ContractDocument, user: User, company: Company
    ) -> dict[str, Any]:
        """The door's `PUT` body (spec § 2.3): the freelancer, the letter as the match
        kept it (its printed data for a match older than migration 0021), and rebase as
        `REBASE_SIGNER_JSON` names it over `rebase.json`, normalised to the CRM's own
        customer rules: a PEC that is not an address, or a tax code longer than 16
        characters, is sent as `null`, as a VAT number that is not eleven digits is, so a
        typo never blocks the link and the space's fiscal profile is completed later.
        Refused for a letter not signed: only an active match links. Refused with
        `_NotAsked` for a freelancer whose name or surname is empty, which the CRM's door
        would refuse in English: `link` records it as `error`, so the sweep and
        «Riprova» try again once the profile is complete."""
        if letter.status != "signed":
            raise InvalidState(LETTER_NOT_SIGNED, status=letter.status)
        if not user.nome.strip() or not user.last_name.strip():
            raise _NotAsked(PROFILE_WITHOUT_NAME)
        signer = merge_data(company_defaults(), signer_data(self.settings.signer_json))
        certified_email = _text(signer.get("rebase-pec"))
        tax_code = normalise_fiscal_code(_text(signer.get("rebase-cf")))
        data = letter.data
        start = match.letter_start_date or _printed_date(data, "data-inizio")
        if start is None:
            raise ContractFailed(f"letter {letter.number} printed no data-inizio")
        end = match.letter_end_date or _printed_date(data, "data-fine")
        fee = match.letter_fee if match.letter_fee is not None else amount(data, FEE)
        headquarters = _text(signer.get("rebase-sede"))
        sdi = _text(signer.get("rebase-codice-destinatario"))
        return {
            "freelancer": {"email": user.email, "nome": user.nome, "last_name": user.last_name},
            "letter": {
                "number": letter.number,
                "role": data.get("role"),
                "company": company.company_name,
                "start_date": start.isoformat(),
                "data_fine": end.isoformat() if end is not None else None,
                "fee": str(fee.quantize(CENT)) if fee is not None else None,
                "expected_days": match.expected_days,
            },
            "rebase": {
                "legal_name": _text(signer.get("rebase-ragione-sociale")),
                "partita_iva": normalise_vat(_text(signer.get("rebase-piva"))),
                "tax_code": (
                    tax_code
                    if tax_code is not None and len(tax_code) <= FISCAL_CODE_MAX_LENGTH
                    else None
                ),
                "address": headquarters[:ADDRESS_MAX_LENGTH] if headquarters is not None else None,
                "certified_email": certified_email
                if certified_email is not None and _is_email(certified_email)
                else None,
                "sdi_code": sdi if sdi is not None and len(sdi) == SDI_LENGTH else None,
            },
        }

    # ---- the link ----------------------------------------------------------------------

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        """Links an active match to its deal on Pigro, or records why not (spec § 3.3):
        `linked` on a `201` or `200`; `rejected` with the CRM's sentence on a `409`
        or `422`, which only an admin's «Riprova» asks again; `error` with its cause
        (`CAUSE_*`, or the CRM's sentence on a `503`) on anything else, and with
        `HTTPS_ONLY` for a CRM not on HTTPS, which the sweep retries. A match already
        `linked` keeps its state, except on a `409`: its deal was deleted in the
        space (spec § 3.10), unless another caller linked it after this call began. A
        match closed while the CRM answered is left as it stands and mailed nothing.
        Without a token nothing is asked: the match waits as `to_link`. With
        `admin_id`, the trail says who asked (`pigro_link`)."""
        unasked: Outcome[_Linked] | None = None
        body: dict[str, Any] = {}
        with committed(self.session):
            freelancer_id = match_freelancer(self.session, match_id)
            lock_freelancer(self.session, freelancer_id)
            match = lock_match(self.session, match_id)
            if match.status != "active":
                raise InvalidState(NOT_ACTIVE, status=match.status)
            waiting = not self.settings.pigro_engagements_token
            if waiting:
                if match.pigro_status is None:
                    match.pigro_status = TO_LINK
            else:
                letter, user, company = self._parts(match)
                try:
                    body = self.payload(match, letter, user, company)
                except _NotAsked as missing:
                    unasked = Outcome(ERROR, missing.sentence)
                recipient = _Recipient(
                    user.email, user.nome, letter.number or "", company.company_name
                )
                started = self.now()
                match.pigro_attempted_at = started
        if waiting:
            _log.info(
                "no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: match %s waits "
                "as da_collegare",
                match_id,
            )
            return self.reads.get(match_id)

        outcome = unasked if unasked is not None else self._put(match_id, body)

        claimed_by: EmailSender | None = None
        with committed(self.session):
            lock_freelancer(self.session, freelancer_id)
            match = lock_match(self.session, match_id)
            # A match closed while the CRM answered is left as it stands: no link
            # written over it, no mail telling the freelancer it is active.
            still_active = match.status == "active"
            # A link made by another caller after this one began may be newer than this
            # call's 409, which then says nothing about the deal it found.
            linked_since = match.pigro_linked_at is not None and match.pigro_linked_at > started
            if still_active and (
                match.pigro_status != LINKED or (outcome.gone and not linked_since)
            ):
                self._write(match, outcome)
            if (
                still_active
                and match.pigro_status == LINKED
                and match.pigro_mail_sent_at is None
                and match.pigro_url is not None
                and self.sender is not None
            ):
                # The claim: whoever stamps it under this lock is the one caller that
                # sends, and gives it back below if the provider refuses.
                match.pigro_mail_sent_at = self.now()
                claimed_by = self.sender
        status, error, deal_url = match.pigro_status, match.pigro_error, match.pigro_url
        if not still_active:
            _log.warning("match %s was closed while Pigro answered: nothing written", match_id)
        elif status != LINKED:
            _log.warning("match %s is not linked to Pigro: %s", match_id, status)
        if claimed_by is not None and deal_url is not None:
            space_created = outcome.answer is not None and outcome.answer.space_created
            self._mail(claimed_by, match_id, freelancer_id, recipient, deal_url, space_created)
        if admin_id is not None:
            AdminActionService(self.session).record(
                ENTITY, match_id, "pigro_link", admin_id, {"outcome": status, "error": error}
            )
        return self.reads.get(match_id)

    def link_pending(self) -> SweepCount:
        """The sweep's round (spec § 3.3): `link` on every active match waiting
        (`to_link`) or failed (`error`), and on a linked one whose mail the
        provider refused, each on its own, a failure logged without stopping the rest.
        A `rejected` match is not asked again. Without a token nothing is asked; without
        a mail sender a linked match has nothing left to do here."""
        if not self.settings.pigro_engagements_token:
            _log.info("no REBASE_PIGRO_ENGAGEMENTS_TOKEN on this environment: no match linked")
            return SweepCount(linked=0, failed=0)
        due: ColumnElement[bool] = Match.pigro_status.in_((TO_LINK, ERROR))
        if self.sender is not None:
            due = or_(due, and_(Match.pigro_status == LINKED, Match.pigro_mail_sent_at.is_(None)))
        rows = self.session.execute(
            select(Match.id, Match.pigro_status)
            .join(Freelancer, Freelancer.id == Match.freelancer_id)
            .where(Match.status == "active", Freelancer.deleted_at.is_(None), due)
            .order_by(Match.created_at, Match.id)
        ).all()
        before = dict(rows)

        def link(match_id: UUID) -> bool | None:
            if self.link(match_id).pigro_status != LINKED:
                return False
            # A match already `linked` was picked for its refused mail alone.
            return True if before[match_id] != LINKED else None

        return sweep_round(self.session, list(before), link, what="match")

    # ---- the report --------------------------------------------------------------------

    def report(self, match_id: UUID, da: date | None = None, a: date | None = None) -> MatchReport:
        """The hours on the match's deal (spec § 3.5), by default over the whole
        engagement: from the letter's start (the printed one for a match older than
        migration 0021, the match's own creation day when the letter has none) to
        today, asked in windows of at most `REPORT_MAX_DAYS`. `InvalidState` for a
        match not `linked`, with where its link stands (`REPORT_NOT_ACTIVE` when it
        has no link state yet), and for a deal the CRM says was deleted in the space (a
        `409`, spec § 3.10), after writing the match `rejected` with the CRM's sentence
        if it is still `linked`, as `link` would: the sweep then leaves it alone, and
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
        if not speaks_https(self.door.api_url):
            raise PigroUnavailable(HTTPS_ONLY)
        if match.pigro_status != LINKED:
            sentence = (
                REPORT_NOT_ACTIVE
                if match.pigro_status is None
                else pigro_state_sentence(match.pigro_status, match.pigro_error)
            )
            raise InvalidState(sentence, pigro_status=match.pigro_status)
        pigro_url, expected_days = match.pigro_url, match.expected_days
        freelancer_id = match.freelancer_id
        started = self.now()
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
                self._window(match_id, first, last)
                for first, last in report_windows(min(start, a), a)
            ]
        except Gone as gone:
            self._refused(match_id, freelancer_id, gone.cause, started)
            raise InvalidState(
                pigro_state_sentence(REJECTED, gone.cause), pigro_status=REJECTED
            ) from None
        return MatchReport(
            match_id=match_id,
            pigro_url=pigro_url,
            pigro_status=LINKED,
            **group(join_windows(windows), expected_days),
        )

    # ---- the steps ---------------------------------------------------------------------

    @staticmethod
    def _path(match_id: UUID) -> str:
        return f"{ENGAGEMENTS_PATH}/{match_id}"

    def _parts(self, match: Match) -> tuple[ContractDocument, User, Company]:
        """The match's letter, its freelancer's user and its request, read again rather
        than from the session's memory."""
        letter = self._letter(match.id)
        if letter is None:
            raise NotFound("letter", match.id)
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
        """The match's newest document, the letter `MatchReads.get` reads."""
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
        if match.letter_start_date is not None:
            return match.letter_start_date
        letter = self._letter(match.id)
        printed = _printed_date(letter.data, "data-inizio") if letter is not None else None
        return printed or match.created_at.astimezone(ROME).date()

    def _refused(self, match_id: UUID, freelancer_id: UUID, cause: str, started: datetime) -> None:
        """The report's `409` written over the match, under the locks `link` takes in the
        same order: `rejected` with the CRM's sentence, if the match is still
        `linked` (another caller may have written something newer meanwhile) and was
        not linked again after the report was asked (`started`), as `link` guards its
        own `409`: that link found the deal this answer says is gone."""
        with committed(self.session):
            lock_freelancer(self.session, freelancer_id)
            match = lock_match(self.session, match_id)
            linked_since = match.pigro_linked_at is not None and match.pigro_linked_at > started
            if match.pigro_status == LINKED and not linked_since:
                self._write(match, Outcome(REJECTED, cause))
        _log.warning("the deal of match %s is gone from its Pigro space", match_id)

    def _put(self, match_id: UUID, body: dict[str, Any]) -> Outcome[_Linked]:
        """The one call, through the door: a `409` is the deal gone from the space (spec
        § 3.10), a `422` a refusal."""
        return self.door.put(
            self._path(match_id),
            body,
            _Linked,
            subject=f"the link of match {match_id}",
            refused=(409, 422),
            gone=(409,),
        )

    def _write(self, match: Match, outcome: Outcome[_Linked]) -> None:
        write(match, outcome, state="pigro_status", error="pigro_errore", copy=self._copy_link)

    def _copy_link(self, match: Match, linked: _Linked) -> None:
        match.pigro_slug = linked.slug
        match.pigro_deal_id = linked.deal_id
        match.pigro_url = linked.deal_url
        match.pigro_linked_at = self.now()

    def _mail(
        self,
        sender: EmailSender,
        match_id: UUID,
        freelancer_id: UUID,
        recipient: _Recipient,
        deal_url: str,
        space_created: bool,
    ) -> None:
        """The freelancer's mail, by the one caller that claimed it, with no lock held; a
        refusal takes the claim back under the lock, for the next call to send."""
        mail = engagement_ready_mail(
            recipient.email,
            nome=recipient.nome,
            number=recipient.number,
            company=recipient.company,
            deal_url=deal_url,
            space_created=space_created,
        )
        if sender.send(mail):
            return
        _log.warning("the Pigro mail of match %s was refused by the provider", match_id)
        with committed(self.session):
            lock_freelancer(self.session, freelancer_id)
            lock_match(self.session, match_id).pigro_mail_sent_at = None

    def _window(self, match_id: UUID, da: date, a: date) -> CrmReport:
        """One `GET` of the report, `a - da` within the CRM's cap. A `409` is the deal
        deleted in the space (spec § 3.10): `report` files the match as refused with the
        CRM's own sentence, the one `link` would store."""
        query = urlencode({"da": da.isoformat(), "a": a.isoformat()})
        return self.door.get(
            f"{self._path(match_id)}/report?{query}", CrmReport.model_validate_json, gone=(409,)
        )
