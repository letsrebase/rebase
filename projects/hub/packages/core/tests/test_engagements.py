"""REB-498: an active match linked to its deal on Pigro, and that deal's hours read back.

Every test hands a recorded `HttpCall`, the way `test_freelancers_companies.py` hands its
`fake_http`: nothing here reaches a CRM, and a recording mailbox stands in for Resend.
The matches come from `MatchService.create` with `FakeRenderer`, then are signed by
hand, since how a letter gets signed is `test_signing.py`'s business.
"""

import json
import logging
import socket
import urllib.error
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from fakes_contracts import FakeRenderer
from fakes_pigro import CUSTOMER, DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro, linked_body
from match_fixtures import SIGNER, TABLES, TODAY, _body, _framework, _setup
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.engagement_report import HOURS_PER_DAY, REPORT_MAX_DAYS, group_report
from rebase_core.engagements import (
    REPORT_NOT_ACTIVE,
    EngagementService,
    normalise_fiscal_code,
    normalise_vat,
)
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.mail import Mail, RecordingSender
from rebase_core.mail_contracts import engagement_ready_mail
from rebase_core.match_words import (
    HTTPS_ONLY,
    PIGRO_NOT_CONFIGURED,
    PROFILE_WITHOUT_NAME,
    pigro_state_sentence,
)
from rebase_core.matches import MatchService
from rebase_core.models import Company, ContractDocument, Freelancer, Match, User
from rebase_core.pigro_door import (
    ANSWERED_STATUS,
    CAUSE_NOT_THE_SHAPE,
    CAUSE_REFUSED,
    CAUSE_TIMEOUT,
    CAUSE_TOO_LONG,
    CAUSE_UNREACHABLE,
    NOT_ANSWERING,
    NOT_THE_SHAPE,
    TOO_LONG,
    PigroUnavailable,
    SweepCount,
)

DEAL_GONE_SENTENCE = (
    "Pigro ha rifiutato il collegamento: Il deal di questa lettera è stato eliminato nello spazio."
)

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
SIGNED_AT = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
DEAL_GONE = "Il deal di questa lettera è stato eliminato nello spazio."
# The door's own 503 sentence (`SPAZIO_NON_RAGGIUNGIBILE` in the CRM's router).
SPACE_UNREACHABLE = (
    "Lo spazio del freelancer non è raggiungibile in questo momento: riprova più tardi."
)


def _crm_conflict(match_id: UUID, reason: str) -> bytes:
    """A `Conflict` as the CRM's `domain_error_handler` renders it: the RFC 9457 keys,
    `detail` as `entity: reason`, and the `Conflict`'s own `entity` and `reason` spread
    at the top level."""
    return json.dumps(
        {
            "type": "https://pigrocrm.dev/errors/conflict",
            "title": "Conflitto con lo stato attuale",
            "status": 409,
            "detail": f"engagement: {reason}",
            "code": "conflict",
            "instance": f"/api/rebase/engagements/{match_id}",
            "entity": "engagement",
            "reason": reason,
            "match_id": str(match_id),
        }
    ).encode()


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


class FlakySender:
    """A provider that turns the first `refusals` mails away, then accepts."""

    def __init__(self, refusals: int) -> None:
        self.refusals = refusals
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        if self.refusals > 0:
            self.refusals -= 1
            return False
        self.sent.append(mail)
        return True


def _settings(
    token: str = TOKEN, signer: dict[str, Any] | None = None, url: str = PIGRO + "/"
) -> Settings:
    return Settings(
        pigro_api_url=url,
        pigro_engagements_token=token,
        signer_json=json.dumps(SIGNER if signer is None else signer),
        _env_file=None,  # type: ignore[call-arg]
    )


def _service(
    session: Session,
    http: RecordedPigro,
    *,
    settings: Settings | None = None,
    sender: Any = None,
    today: date = TODAY,
) -> EngagementService:
    return EngagementService(
        session,
        settings or _settings(),
        http,
        sender=sender,
        now=lambda: NOW,
        today=lambda: today,
    )


def _matches(session: Session) -> MatchService:
    return MatchService(session, FakeRenderer(), SIGNER, today=lambda: TODAY)


def _letter(session: Session, match_id: UUID) -> ContractDocument:
    return session.scalars(
        select(ContractDocument).where(ContractDocument.match_id == match_id)
    ).one()


def _sign(session: Session, match_id: UUID, pigro_status: str | None = "to_link") -> None:
    """The letter signed and the match active, as `_confirm_completion` leaves them."""
    letter = _letter(session, match_id)
    letter.status = "signed"
    letter.signed_at = SIGNED_AT
    match = session.get(Match, match_id)
    assert match is not None
    match.status = "active"
    match.pigro_status = pigro_status
    session.commit()


def _draft(session: Session, admin_id: UUID, freelancer_id: UUID, company_id: UUID) -> UUID:
    read = _matches(session).create(freelancer_id, _body(company_id, expected_days=40), admin_id)
    return read.id


def _active(session: Session, pigro_status: str | None = "to_link") -> tuple[UUID, UUID]:
    """One freelancer, one request, one active match: `(admin_id, match_id)`."""
    admin_id, freelancer_id, company_id = _setup(session)
    _framework(session, freelancer_id, admin_id)
    match_id = _draft(session, admin_id, freelancer_id, company_id)
    _sign(session, match_id, pigro_status)
    return admin_id, match_id


def _parts(session: Session, match_id: UUID) -> tuple[Match, ContractDocument, User, Company]:
    match = session.get(Match, match_id)
    assert match is not None
    user = session.scalars(
        select(User)
        .join(Freelancer, Freelancer.user_id == User.id)
        .where(Freelancer.id == match.freelancer_id)
    ).one()
    company = session.get(Company, match.company_id)
    assert company is not None
    return match, _letter(session, match_id), user, company


def _match(session: Session, match_id: UUID) -> Match:
    session.expire_all()
    match = session.get(Match, match_id)
    assert match is not None
    return match


# ---- the body the CRM receives ---------------------------------------------------------


def test_payload_from_a_match_with_the_columns(clean: Session) -> None:
    """The dates and the fee come off the match's own columns when it has them, even
    where the printed letter says something else: `create` copied them as a date and a
    `Decimal`, which is what the CRM is sent."""
    _admin, match_id = _active(clean)
    match, letter, user, company = _parts(clean, match_id)
    match.letter_end_date = date(2026, 12, 31)
    match.letter_fee = Decimal("400.00")
    clean.commit()

    body = _service(clean, RecordedPigro([(500, b"")])).payload(match, letter, user, company)

    assert body == {
        "freelancer": {"email": "ada@studio.it", "first_name": "Ada", "last_name": "Lovelace"},
        "letter": {
            "number": letter.number,
            "role": "Backend developer",
            "company_name": "ACME Srl",
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
            "fee": "400.00",
            "expected_days": 40,
        },
        "rebase": {
            "legal_name": "rebase S.r.l.",
            "vat_number": "00000000000",
            "tax_code": "00000000000",
            "address": "Milano",
            "certified_email": "rebase@pec.example",
            "sdi_code": "0000000",
        },
    }
    json.dumps(body)


def test_payload_from_an_older_match_parses_the_printed_letter(clean: Session) -> None:
    """A match written before migration 0021 has no `lettera_*` values: the letter's own
    printed fields are read back instead, `1° ottobre 2026` as `2026-10-01`."""
    _admin, match_id = _active(clean)
    match, letter, user, company = _parts(clean, match_id)
    match.letter_start_date = None
    match.letter_end_date = None
    match.letter_fee = None
    match.expected_days = None
    letter.data = {**letter.data, "data-fine": "31 dicembre 2026"}
    clean.commit()
    assert letter.data["data-inizio"] == "1° ottobre 2026"

    letter_body = _service(clean, RecordedPigro([(500, b"")])).payload(
        match, letter, user, company
    )["letter"]

    assert (letter_body["start_date"], letter_body["end_date"]) == ("2026-10-01", "2026-12-31")
    assert (letter_body["fee"], letter_body["expected_days"]) == ("450.00", None)


def test_payload_normalises_rebase_fiscal_data(clean: Session) -> None:
    """The CRM's own customer rules, met before the call: the VAT number compacted and
    without its `IT`, sent only as eleven digits; the address cut to 255; the recipient
    code only at seven characters."""
    _admin, match_id = _active(clean)
    signer = {
        **SIGNER,
        "rebase-piva": "IT 0123 456 7890",
        "rebase-codice-destinatario": "ABC123",
        "rebase-sede": "V" * 300,
    }
    service = _service(clean, RecordedPigro([(500, b"")]), settings=_settings(signer=signer))

    rebase = service.payload(*_parts(clean, match_id))["rebase"]

    assert rebase["vat_number"] == "01234567890"
    assert rebase["sdi_code"] is None
    assert rebase["address"] == "V" * 255
    assert normalise_vat("IT 0123 456 7890") == "01234567890"
    assert normalise_vat("01234567890") == "01234567890"
    assert normalise_vat("0123456789") is None
    assert normalise_vat("IT0123456789X") is None
    assert normalise_vat("   ") is None
    assert normalise_vat(None) is None


def test_payload_sends_rebase_certified_email_and_tax_code_as_the_crm_takes_them(
    clean: Session,
) -> None:
    """The tax code compacted and in capitals, the PEC as it is: both pass the rules of
    the CRM's door (`EmailStr`, at most 16 characters)."""
    _admin, match_id = _active(clean)
    signer = {**SIGNER, "rebase-cf": " rss mra 80a01 h501u ", "rebase-pec": "rebase@pec.it"}
    service = _service(clean, RecordedPigro([(500, b"")]), settings=_settings(signer=signer))

    rebase = service.payload(*_parts(clean, match_id))["rebase"]

    assert (rebase["tax_code"], rebase["certified_email"]) == ("RSSMRA80A01H501U", "rebase@pec.it")
    assert normalise_fiscal_code("  ") is None
    assert normalise_fiscal_code(None) is None


@pytest.mark.parametrize(
    ("field", "value", "key"),
    [
        ("rebase-pec", "rebase at pec", "certified_email"),
        ("rebase-pec", "rebase@", "certified_email"),
        ("rebase-cf", "RSSMRA80A01H501UX", "tax_code"),
    ],
)
def test_link_sends_rebase_data_the_crm_would_refuse_as_null(
    clean: Session, field: str, value: str, key: str
) -> None:
    """A PEC that is not an address, a tax code longer than 16: the CRM's door would
    refuse either in English, so it is dropped, as a VAT number that is not eleven
    digits is. The link goes through all the same: a typo never blocks it, and the
    space's fiscal profile is completed later."""
    _admin, match_id = _active(clean)
    http = RecordedPigro([(201, linked_body())])
    settings = _settings(signer={**SIGNER, field: value})

    read = _service(clean, http, settings=settings).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("linked", None)
    [(_method, _url, _headers, sent)] = http.calls
    rebase = json.loads(sent)["rebase"]
    assert rebase[key] is None
    assert rebase["vat_number"] == "00000000000"


def test_payload_refuses_a_letter_that_is_not_signed(clean: Session) -> None:
    _admin, match_id = _active(clean)
    match, letter, user, company = _parts(clean, match_id)
    letter.status = "sent"
    clean.commit()

    with pytest.raises(InvalidState, match="La lettera non è firmata."):
        _service(clean, RecordedPigro([(500, b"")])).payload(match, letter, user, company)


# ---- link ------------------------------------------------------------------------------


def test_link_refuses_a_match_that_is_not_active(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    match_id = _draft(clean, admin_id, freelancer_id, company_id)
    http = RecordedPigro([(201, linked_body())])

    with pytest.raises(InvalidState, match="Si collega a Pigro solo un match attivo."):
        _service(clean, http).link(match_id)

    assert http.calls == []
    assert _match(clean, match_id).pigro_status is None


def test_link_without_token_marks_to_link_and_calls_nothing(
    clean: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Alembic's `fileConfig`, run by the session's migrations, disables every logger
    # that existed before it: this one is read back on for the test.
    monkeypatch.setattr(logging.getLogger("rebase_core.engagements"), "disabled", False)
    _admin, match_id = _active(clean, pigro_status=None)
    http = RecordedPigro([(201, linked_body())])

    with caplog.at_level(logging.INFO, logger="rebase_core.engagements"):
        read = _service(clean, http, settings=_settings(token="")).link(match_id)

    assert (read.pigro_status, read.pigro_attempted_at) == ("to_link", None)
    assert http.calls == []
    notes = [r for r in caplog.records if r.name == "rebase_core.engagements"]
    assert [r.levelno for r in notes] == [logging.INFO]
    assert TOKEN not in caplog.text and "ada@studio.it" not in caplog.text


def test_link_refuses_plain_http(clean: Session) -> None:
    """The bearer never travels in clear: a CRM named with `http://` is not asked at all,
    and the match says why. Plain HTTP to this machine stays open, for development."""
    _admin, match_id = _active(clean)
    http = RecordedPigro([(201, linked_body())])

    read = _service(clean, http, settings=_settings(url="http://pigro.example")).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("error", HTTPS_ONLY)
    assert http.calls == []
    # The hub's own sentence, shown alone: nothing was asked of Pigro.
    assert pigro_state_sentence("error", read.pigro_error) == HTTPS_ONLY

    local = _service(clean, http, settings=_settings(url="http://localhost:8000")).link(match_id)

    assert local.pigro_status == "linked"
    [(_method, url, _headers, _sent)] = http.calls
    assert url == f"http://localhost:8000/api/rebase/engagements/{match_id}"


def test_link_on_201_is_linked_and_mails_once(clean: Session) -> None:
    _admin, match_id = _active(clean)
    http = RecordedPigro(
        [(201, linked_body()), (200, linked_body(space_created=False, created=False))]
    )
    sender = RecordingSender()
    service = _service(clean, http, sender=sender)

    read = service.link(match_id)

    assert read.pigro_status == "linked"
    assert (read.pigro_slug, read.pigro_deal_id, read.pigro_url) == ("ada-lovelace", DEAL, DEAL_URL)
    assert (read.pigro_linked_at, read.pigro_attempted_at) == (NOW, NOW)
    assert (read.pigro_error, read.pigro_mail_sent_at) == (None, NOW)
    [(method, url, headers, body)] = http.calls
    assert (method, url) == ("PUT", f"{PIGRO}/api/rebase/engagements/{match_id}")
    assert headers == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert json.loads(body) == service.payload(*_parts(clean, match_id))
    [mail] = sender.sent
    number = _letter(clean, match_id).number
    assert mail.to == "ada@studio.it"
    assert mail.subject == f"La tua lettera n. {number} è attiva: le ore si registrano su Pigro"
    assert DEAL_URL in mail.text and "ACME Srl" in mail.text
    assert "a tuo nome" in mail.text

    again = service.link(match_id)

    assert len(http.calls) == 2
    assert (again.pigro_status, again.pigro_linked_at, again.pigro_mail_sent_at) == (
        "linked",
        NOW,
        NOW,
    )
    assert len(sender.sent) == 1


def test_two_links_racing_send_one_mail(hub_engine: Engine, clean: Session) -> None:
    """The sweep and an admin's «Riprova» on one match at once: while the first call
    waits on the CRM, a second `link` runs from start to end in its own session, links
    the match and mails. The first then finds the mail claimed and sends none."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()
    other = session_factory(hub_engine)()
    second = EngagementService(
        other,
        _settings(),
        RecordedPigro([(200, linked_body(created=False))]),
        sender=sender,
        now=lambda: NOW,
        today=lambda: TODAY,
    )
    raced: list[str | None] = []

    def crm_while_a_second_link_runs(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        raced.append(second.link(match_id).pigro_status)
        return 201, linked_body()

    first = EngagementService(
        clean,
        _settings(),
        crm_while_a_second_link_runs,
        sender=sender,
        now=lambda: NOW,
        today=lambda: TODAY,
    )
    try:
        read = first.link(match_id)
    finally:
        other.close()

    assert raced == ["linked"]
    assert (read.pigro_status, read.pigro_mail_sent_at) == ("linked", NOW)
    assert len(sender.sent) == 1


def test_a_link_while_the_mail_leaves_sends_no_second_mail(
    hub_engine: Engine, clean: Session
) -> None:
    """The narrower race: a second `link` runs while the first one's mail is still with
    the provider. The first claimed the mail before sending it, so the second, reading
    the row under its lock, finds the claim and sends nothing."""
    _admin, match_id = _active(clean)
    other = session_factory(hub_engine)()
    raced: list[datetime | None] = []

    class SenderThatRaces:
        def __init__(self) -> None:
            self.sent: list[Mail] = []

        def send(self, mail: Mail) -> bool:
            self.sent.append(mail)
            if len(self.sent) == 1:
                second = EngagementService(
                    other,
                    _settings(),
                    RecordedPigro([(200, linked_body(created=False))]),
                    sender=self,
                    now=lambda: NOW,
                    today=lambda: TODAY,
                )
                raced.append(second.link(match_id).pigro_mail_sent_at)
            return True

    sender = SenderThatRaces()
    try:
        read = _service(clean, RecordedPigro([(201, linked_body())]), sender=sender).link(match_id)
    finally:
        other.close()

    assert raced == [NOW]
    assert read.pigro_mail_sent_at == NOW
    assert len(sender.sent) == 1


def test_link_holds_no_lock_during_the_call(hub_engine: Engine, clean: Session) -> None:
    """The house rule of `_confirm_completion`: no row lock across a network call, and a
    first link can take the CRM up to 90 seconds. While the CRM is being asked, another
    session locks the match and the freelancer at once (`NOWAIT`) and writes the match;
    the outcome is then written over the row as it is now, not as it was read before."""
    _admin, match_id = _active(clean)
    freelancer_id = _match(clean, match_id).freelancer_id
    elsewhere = datetime(2026, 10, 2, 7, 59, tzinfo=UTC)
    seen: list[str] = []

    def crm_while_another_session_writes(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            for model, row_id in ((Freelancer, freelancer_id), (Match, match_id)):
                other.execute(
                    select(model.id).where(model.id == row_id).with_for_update(nowait=True)
                )
                seen.append(model.__tablename__)
            other.execute(
                text("UPDATE matches SET pigro_attempted_at = :at WHERE id = :id"),
                {"at": elsewhere, "id": match_id},
            )
            other.commit()
        except OperationalError:
            seen.append("locked")
            other.rollback()
        finally:
            other.close()
        return 201, linked_body()

    service = EngagementService(
        clean, _settings(), crm_while_another_session_writes, now=lambda: NOW, today=lambda: TODAY
    )
    read = service.link(match_id)

    assert seen == ["freelancers", "matches"]
    assert read.pigro_status == "linked"
    assert read.pigro_attempted_at == elsewhere


def test_link_leaves_a_match_another_caller_already_linked(
    hub_engine: Engine, clean: Session
) -> None:
    """Two callers (the sweep and «Riprova») overlap: the one that reaches the row second
    finds it `linked` and leaves it, whatever its own call answered."""
    _admin, match_id = _active(clean)
    winner = f"{PIGRO}/ada-lovelace/app/deal/{CUSTOMER}"

    def crm_after_the_winner(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_status = 'linked', pigro_url = :url, "
                    "pigro_slug = 'ada-lovelace' WHERE id = :id"
                ),
                {"url": winner, "id": match_id},
            )
            other.commit()
        finally:
            other.close()
        return 503, b""

    service = EngagementService(
        clean, _settings(), crm_after_the_winner, now=lambda: NOW, today=lambda: TODAY
    )
    read = service.link(match_id)

    assert (read.pigro_status, read.pigro_url, read.pigro_error) == ("linked", winner, None)


def test_link_writes_the_crm_sentence_on_409_as_rejected(clean: Session) -> None:
    """The sentence stored is the `Conflict`'s `reason`, not its `detail`, which the CRM
    prefixes with the entity («engagement: …»)."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()
    http = RecordedPigro([(409, _crm_conflict(match_id, DEAL_GONE))])

    read = _service(clean, http, sender=sender).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("rejected", DEAL_GONE)
    assert (read.pigro_slug, read.pigro_url, read.pigro_linked_at) == (None, None, None)
    assert read.pigro_attempted_at == NOW
    assert sender.sent == []


def test_link_on_409_turns_to_linked_match_rejected_until_retry(clean: Session) -> None:
    """The freelancer deleted the deal after the link (spec § 3.10): for a match that
    already has one, the door's only `409` says so, and it is written over `linked`.
    The sweep then leaves the match alone; once the deal is restored, «Riprova» finds it
    again, and the mail already sent is not sent twice."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()
    http = RecordedPigro(
        [
            (201, linked_body()),
            (409, _crm_conflict(match_id, DEAL_GONE)),
            (200, linked_body(space_created=False, created=False)),
        ]
    )
    service = _service(clean, http, sender=sender)
    assert service.link(match_id).pigro_status == "linked"

    refused = service.link(match_id)

    assert (refused.pigro_status, refused.pigro_error) == ("rejected", DEAL_GONE)
    # The registry row still points at the deal: the link is kept for «Riprova».
    assert (refused.pigro_url, refused.pigro_deal_id) == (DEAL_URL, DEAL)
    assert pigro_state_sentence(refused.pigro_status, refused.pigro_error) == DEAL_GONE_SENTENCE
    assert service.link_pending() == SweepCount(0, 0)
    assert len(http.calls) == 2

    again = service.link(match_id)

    assert (again.pigro_status, again.pigro_error, again.pigro_url) == (
        "linked",
        None,
        DEAL_URL,
    )
    assert len(sender.sent) == 1


def test_link_keeps_a_link_made_after_its_409_was_asked(hub_engine: Engine, clean: Session) -> None:
    """Two callers overlap: while this one's `PUT` comes back `409`, another links the
    match. That link is newer than this call, whose `409` then says nothing about the
    deal it found: the match stays `linked`."""
    _admin, match_id = _active(clean)

    def crm_after_a_newer_link(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_status = 'linked', pigro_url = :url, "
                    "pigro_linked_at = :at WHERE id = :id"
                ),
                {"url": DEAL_URL, "at": NOW + timedelta(seconds=1), "id": match_id},
            )
            other.commit()
        finally:
            other.close()
        return 409, _crm_conflict(match_id, DEAL_GONE)

    service = EngagementService(
        clean, _settings(), crm_after_a_newer_link, now=lambda: NOW, today=lambda: TODAY
    )
    read = service.link(match_id)

    assert (read.pigro_status, read.pigro_url, read.pigro_error) == ("linked", DEAL_URL, None)


def test_link_writes_nothing_on_a_match_closed_while_pigro_answered(
    hub_engine: Engine, clean: Session
) -> None:
    """An admin closes the match while its `PUT` is out: the CRM's `201` comes back to a
    match that is `concluded`, which is left as the close left it, with no link written
    and no mail telling the freelancer the engagement is active."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()

    def crm_while_the_match_closes(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text("UPDATE matches SET status = 'concluded' WHERE id = :id"), {"id": match_id}
            )
            other.commit()
        finally:
            other.close()
        return 201, linked_body()

    service = EngagementService(
        clean,
        _settings(),
        crm_while_the_match_closes,
        sender=sender,
        now=lambda: NOW,
        today=lambda: TODAY,
    )
    read = service.link(match_id)

    assert (read.status, read.pigro_status, read.pigro_url) == ("concluded", "to_link", None)
    assert (read.pigro_linked_at, read.pigro_mail_sent_at) == (None, None)
    assert sender.sent == []


def test_link_on_422_is_rejected(clean: Session) -> None:
    """A body the CRM's door refuses names the field, never in English: FastAPI's own
    list of errors as `field: non valido` for each field, the value left out; a
    `ValidationFailed` from the space by its `detail`, `entity.field: reason`, never by
    its bare `reason`, which would drop the field; an empty body by the status."""
    _admin, match_id = _active(clean)
    fastapi = {
        "detail": [
            {
                "loc": ["body", "rebase", "vat_number"],
                "msg": "String should match pattern '^\\d{11}$'",
                "type": "string_pattern_mismatch",
                "input": "0123",
            },
            {
                "loc": ["body", "rebase", "vat_number"],
                "msg": "Value error, twice",
                "type": "value_error",
                "input": "0123",
            },
            {
                "loc": ["body", "letter", "expected_days"],
                "msg": "Input should be less than or equal to 366",
                "type": "less_than_equal",
                "input": 400,
            },
        ]
    }
    # `ValidationFailed` as the CRM's `domain_error_handler` renders it: the RFC 9457
    # keys, `detail` as `entity.field: reason`, and its own details spread at the top.
    problem = {
        "type": "https://pigrocrm.dev/errors/validation_failed",
        "title": "Dati non validi",
        "status": 422,
        "detail": "customer.sdi_code: deve essere di 7 caratteri",
        "code": "validation_failed",
        "instance": f"/api/rebase/engagements/{match_id}",
        "entity": "customer",
        "field": "sdi_code",
        "reason": "deve essere di 7 caratteri",
        "expected": "7 caratteri",
    }
    for body, sentence in (
        (
            json.dumps(fastapi).encode(),
            "rebase.vat_number: non valido; letter.expected_days: non valido",
        ),
        (json.dumps(problem).encode(), "customer.sdi_code: deve essere di 7 caratteri"),
        (json.dumps({"detail": [{"msg": "Field required"}]}).encode(), "HTTP 422"),
        (b"", "HTTP 422"),
    ):
        read = _service(clean, RecordedPigro([(422, body)])).link(match_id)
        assert (read.pigro_status, read.pigro_error) == ("rejected", sentence)


def test_link_of_a_freelancer_without_a_surname_asks_nothing_and_retries(
    clean: Session,
) -> None:
    """The door wants a name and a surname: a profile without one is not sent at all,
    and the match waits as `error` with the hub's own sentence, which the card shows
    alone. The next link, once the profile is complete, goes through."""
    _admin, match_id = _active(clean)
    user = _parts(clean, match_id)[2]
    user.last_name = "  "
    clean.commit()
    http = RecordedPigro([(201, linked_body())])

    read = _service(clean, http).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("error", PROFILE_WITHOUT_NAME)
    assert read.pigro_attempted_at == NOW
    assert http.calls == []
    assert pigro_state_sentence("error", read.pigro_error) == PROFILE_WITHOUT_NAME

    user = _parts(clean, match_id)[2]
    user.last_name = "Lovelace"
    clean.commit()
    assert _service(clean, http).link(match_id).pigro_status == "linked"
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        (ConnectionRefusedError("[Errno 61] Connection refused"), CAUSE_REFUSED),
        (urllib.error.URLError(ConnectionRefusedError(61, "refused")), CAUSE_REFUSED),
        (TimeoutError("The read operation timed out"), CAUSE_TIMEOUT),
        (urllib.error.URLError(TimeoutError("timed out")), CAUSE_TIMEOUT),
        (urllib.error.URLError(socket.gaierror(8, "nodename nor servname")), CAUSE_UNREACHABLE),
    ],
)
def test_link_that_gets_no_answer_is_error_with_its_cause(
    clean: Session, failure: Exception, cause: str
) -> None:
    """The stored cause is short, since the card wraps it: «Pigro non ha risposto:
    timeout.», never «Pigro non ha risposto: Pigro non risponde.»"""
    _admin, match_id = _active(clean)
    http = RecordedPigro([failure])

    read = _service(clean, http, sender=RecordingSender()).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("error", cause)
    assert pigro_state_sentence("error", read.pigro_error) == f"Pigro non ha risposto: {cause}."
    assert read.pigro_attempted_at == NOW
    assert read.pigro_mail_sent_at is None


@pytest.mark.parametrize(
    ("answer", "cause"),
    [
        ((500, b"Internal Server Error"), "HTTP 500"),
        ((404, json.dumps({"detail": "Not Found"}).encode()), "HTTP 404"),
        # The door's «not now»: the space's lock busy or its database unreachable. A
        # retry, never a refusal, in the door's own words when it has some.
        ((503, json.dumps({"detail": SPACE_UNREACHABLE}).encode()), SPACE_UNREACHABLE),
        ((503, b"<html>Service Unavailable</html>"), "HTTP 503"),
        ((302, b""), "HTTP 302"),
        ((201, b"not json"), CAUSE_NOT_THE_SHAPE),
        ((201, b'{"slug": "ada-lovelace"}'), CAUSE_NOT_THE_SHAPE),
        ((201, linked_body().replace(b"https://", b"javascript://")), CAUSE_NOT_THE_SHAPE),
        # A deal address in clear is not taken either: it is a link people follow.
        ((201, linked_body().replace(b"https://", b"http://")), CAUSE_NOT_THE_SHAPE),
        ((201, b"x" * 1_048_577), CAUSE_TOO_LONG),
    ],
)
def test_link_on_any_other_answer_is_error_with_its_cause(
    clean: Session, answer: tuple[int, bytes], cause: str
) -> None:
    _admin, match_id = _active(clean)

    read = _service(clean, RecordedPigro([answer])).link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("error", cause)


def test_link_after_an_error_clears_the_sentence(clean: Session) -> None:
    _admin, match_id = _active(clean)
    http = RecordedPigro([(503, b""), (201, linked_body())])
    service = _service(clean, http)

    assert service.link(match_id).pigro_status == "error"
    read = service.link(match_id)

    assert (read.pigro_status, read.pigro_error) == ("linked", None)


def test_link_retries_a_refused_mail(clean: Session) -> None:
    """The provider refusing the freelancer's mail leaves the match `linked` and the
    stamp empty, so the next call (the sweep's) sends it and stamps it."""
    _admin, match_id = _active(clean)
    sender = FlakySender(refusals=1)
    service = _service(clean, RecordedPigro([(201, linked_body())]), sender=sender)

    first = service.link(match_id)

    assert (first.pigro_status, first.pigro_mail_sent_at) == ("linked", None)
    assert sender.sent == []

    second = service.link(match_id)

    assert second.pigro_mail_sent_at == NOW
    assert len(sender.sent) == 1


def test_link_records_an_admin_action_when_an_admin_asked(clean: Session) -> None:
    admin_id, match_id = _active(clean)
    http = RecordedPigro([(503, b""), (201, linked_body())])
    service = _service(clean, http)

    service.link(match_id)
    assert [a.kind for a in AdminActionService(clean).timeline("match", match_id)] == [
        "match_created"
    ]

    service.link(match_id, admin_id)

    [action, _created] = AdminActionService(clean).timeline("match", match_id)
    assert (action.kind, action.admin_id) == ("pigro_link", admin_id)
    assert action.payload == {"outcome": "linked", "error": None}


def test_the_mail_says_where_the_hours_go() -> None:
    mail = engagement_ready_mail(
        "ada@studio.it",
        first_name="Ada",
        number="2026-001",
        company="ACME <Srl>",
        deal_url=DEAL_URL + '?"x"',
        space_created=True,
    )
    assert mail.subject == "La tua lettera n. 2026-001 è attiva: le ore si registrano su Pigro"
    assert mail.text.startswith("Ciao Ada,\n\n")
    assert "ACME <Srl>" in mail.text and DEAL_URL in mail.text
    assert "nient'altro" in mail.text
    assert "a tuo nome" in mail.text
    assert mail.html is not None
    assert "ACME &lt;Srl&gt;" in mail.html and "ACME <Srl>" not in mail.html
    assert "&quot;x&quot;" in mail.html
    assert "Noi di rebase" in mail.text

    plain = engagement_ready_mail(
        "ada@studio.it",
        first_name="",
        number="2026-001",
        company="ACME Srl",
        deal_url=DEAL_URL,
        space_created=False,
    )
    assert plain.text.startswith("Ciao,\n\n")
    assert "a tuo nome" not in plain.text


# ---- link_pending ----------------------------------------------------------------------


def test_link_pending_counts(clean: Session) -> None:
    """The sweep's round: a match waiting is linked, one the CRM does not answer for
    stays `error`, one linked with its mail refused gets its mail, and one that cannot
    even build its body fails alone without stopping the others."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)
    waiting, failing, unmailed, broken = (
        _draft(clean, admin_id, freelancer_id, company_id) for _ in range(4)
    )
    _sign(clean, waiting)
    _sign(clean, failing, "error")
    _sign(clean, unmailed, "linked")
    _match(clean, unmailed).pigro_url = DEAL_URL
    _sign(clean, broken)
    letter = _letter(clean, broken)
    letter.status = "generated"
    clean.commit()
    answers = {waiting: (201, linked_body()), failing: (503, b""), unmailed: (200, linked_body())}
    asked: list[UUID] = []

    def crm(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        match_id = UUID(url.rsplit("/", 1)[1])
        asked.append(match_id)
        return answers[match_id]

    sender = RecordingSender()
    service = EngagementService(
        clean, _settings(), crm, sender=sender, now=lambda: NOW, today=lambda: TODAY
    )

    assert service.link_pending() == SweepCount(linked=1, failed=2)

    assert sorted(asked, key=str) == sorted([waiting, failing, unmailed], key=str)
    assert _match(clean, waiting).pigro_status == "linked"
    assert _match(clean, failing).pigro_status == "error"
    assert _match(clean, unmailed).pigro_mail_sent_at == NOW
    assert _match(clean, broken).pigro_status == "to_link"
    assert len(sender.sent) == 2


def test_link_pending_skips_rejected(clean: Session) -> None:
    """A refusal is the CRM's word on this match: only an admin's «Riprova» asks again.
    A match not active, or linked with its mail sent, is not asked for either."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)
    refused, draft, done = (_draft(clean, admin_id, freelancer_id, company_id) for _ in range(3))
    _sign(clean, refused, "rejected")
    _sign(clean, done, "linked")
    match = _match(clean, done)
    match.pigro_mail_sent_at = NOW
    clean.commit()
    http = RecordedPigro([(201, linked_body())])

    assert _service(clean, http, sender=RecordingSender()).link_pending() == SweepCount(0, 0)
    assert http.calls == []
    assert _match(clean, draft).pigro_status is None


def test_link_pending_without_token_or_mail_sender(clean: Session) -> None:
    """No token: nothing is asked, the matches wait. No mail sender: a linked match
    missing only its mail has nothing left to do here, and is not asked again."""
    _admin, match_id = _active(clean, pigro_status="linked")
    http = RecordedPigro([(200, linked_body())])

    assert _service(
        clean, http, sender=RecordingSender(), settings=_settings(token="")
    ).link_pending() == (SweepCount(0, 0))
    assert _service(clean, http).link_pending() == SweepCount(0, 0)
    assert http.calls == []


# ---- the report ------------------------------------------------------------------------


def _invoice(
    kind: str, number: int | None, year: int | None = 2026, **extra: Any
) -> dict[str, Any]:
    return {
        # One id per invoice, so two of them never merge when windows are joined.
        "id": str(uuid5(NAMESPACE_URL, f"{kind}/{number}/{year}")),
        "type": kind,
        "year": year,
        "number": number,
        "status": "issued",
        "payment_status": "unpaid",
        "date": "2027-01-31",
        **extra,
    }


def _entry(
    day: str, hours: str, description: str = "", invoice: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "date": day,
        "hours": hours,
        "description": description,
        "billable": True,
        "invoice": invoice,
    }


def _crm(
    days: list[dict[str, Any]],
    invoices: list[dict[str, Any]] | None = None,
    *,
    invoiced: str = "0.00",
    not_invoiced: str = "0.00",
) -> dict[str, Any]:
    return {
        "slug": "ada-lovelace",
        "deal_url": DEAL_URL,
        "deal": {
            "id": str(DEAL),
            "name": "Lettera n. 2026-001 · Backend developer per ACME Srl",
            "hourly_rate": "56.250000",
            "budgeted_hours": "320.00",
            "status": "in_progress",
        },
        "days": days,
        "total_hours": "0.00",
        "billed_hours": invoiced,
        "unbilled_hours": not_invoiced,
        "invoices": invoices or [],
    }


def test_report_names_an_invoice_by_the_codes_the_crm_sends() -> None:
    """The CRM's invoice type is `invoice` (it was `fattura`): a day's column names an
    invoice by its bare number and any other type, a proforma, by its type first."""
    invoice, proforma = _invoice("invoice", 7), _invoice("proforma", 7)
    crm = _crm(
        [_entry("2026-11-02", "8.00", "Setup", invoice), _entry("2026-11-03", "8.00", "", proforma)]
    )

    days = group_report(crm, None)["by_day"]

    assert [d.invoices for d in days] == [["7/2026"], ["proforma 7/2026"]]


def test_report_groups_by_iso_week_and_month() -> None:
    """ISO weeks cross the year: 1 January 2027 is still in 2026's week 53, and 4 January
    opens 2027's first. Days, weeks and months all sum to the same total."""
    invoice, proforma = _invoice("invoice", 12), _invoice("proforma", 3)
    crm = _crm(
        [
            _entry("2026-12-28", "8.00", "Setup", invoice),
            _entry("2026-12-28", "4", "  ", invoice),
            _entry("2026-12-28", "2.5", "Riunione", proforma),
            _entry("2026-12-31", "8.00", "API", invoice),
            _entry("2027-01-01", "4.00", "API"),
            _entry("2027-01-04", "8.00", "Test"),
        ],
        [{**invoice, "hours": "20.00"}, {**proforma, "hours": "2.50", "date": None}],
        invoiced="20.00",
        not_invoiced="14.50",
    )

    fields = group_report(crm, None)

    days = [(d.date, d.hours, d.descriptions, d.invoices) for d in fields["by_day"]]
    # 28 December: three entries, two on invoice 12 and one on proforma 3, both named once.
    assert days == [
        (
            date(2026, 12, 28),
            Decimal("14.50"),
            ["Setup", "Riunione"],
            ["12/2026", "proforma 3/2026"],
        ),
        (date(2026, 12, 31), Decimal("8.00"), ["API"], ["12/2026"]),
        (date(2027, 1, 1), Decimal("4.00"), ["API"], []),
        (date(2027, 1, 4), Decimal("8.00"), ["Test"], []),
    ]
    # The same invoices, each with its own share of the day (REB-505).
    shares = [[(i.number, i.kind, i.hours) for i in d.hours_by_invoice] for d in fields["by_day"]]
    assert shares == [
        [("12/2026", "invoice", Decimal("12.00")), ("3/2026", "proforma", Decimal("2.50"))],
        [("12/2026", "invoice", Decimal("8.00"))],
        [],
        [],
    ]
    weeks = [(w.week, w.from_date, w.to_date, w.hours) for w in fields["by_week"]]
    assert weeks == [
        ("2026-W53", date(2026, 12, 28), date(2027, 1, 3), Decimal("26.50")),
        ("2027-W01", date(2027, 1, 4), date(2027, 1, 10), Decimal("8.00")),
    ]
    assert [(m.month, m.hours) for m in fields["by_month"]] == [
        ("2026-12", Decimal("22.50")),
        ("2027-01", Decimal("12.00")),
    ]
    total = Decimal("34.50")
    assert fields["hours_total"] == total
    assert sum(w.hours for w in fields["by_week"]) == total
    assert sum(m.hours for m in fields["by_month"]) == total
    assert (fields["invoiced_hours"], fields["hours_uninvoiced"]) == (
        Decimal("20.00"),
        Decimal("14.50"),
    )
    assert (fields["expected_days"], fields["expected_hours"], fields["progress"]) == (
        None,
        None,
        None,
    )
    assert fields["equivalent_days"] == Decimal("4.31")
    invoices = [(i.number, i.kind, i.date, i.status, i.hours) for i in fields["invoices"]]
    assert invoices == [
        ("12/2026", "invoice", date(2027, 1, 31), "issued", Decimal("20.00")),
        ("3/2026", "proforma", None, "issued", Decimal("2.50")),
    ]

    twelve_days = _crm([_entry(f"2026-10-{day:02d}", "8.00") for day in range(1, 13)])
    progress = group_report(twelve_days, 40)
    assert (progress["hours_total"], progress["equivalent_days"]) == (
        Decimal("96.00"),
        Decimal("12.00"),
    )
    assert progress["expected_hours"] == Decimal("320.00") == 40 * HOURS_PER_DAY
    assert str(progress["progress"]) == "30.00"
    assert group_report(_crm([]), 40)["progress"] == Decimal("0.00")


def test_report_gives_each_invoice_its_own_share_of_a_day() -> None:
    """REB-505: 2 November holds 8 hours, 3 on invoice 14/2026 and 5 not billed, and
    14/2026 gets its 3, never the day's 8. 3 November splits between 13/2026 and
    14/2026, and a proforma numbered 14 stays apart from the invoice 14."""
    thirteen, fourteen = _invoice("invoice", 13), _invoice("invoice", 14)
    proforma = _invoice("proforma", 14)
    crm = _crm(
        [
            _entry("2026-11-02", "3.00", "API", fourteen),
            _entry("2026-11-02", "5.00", "Test"),
            _entry("2026-11-03", "3.00", "API", thirteen),
            _entry("2026-11-03", "2.50", "Riunione", fourteen),
            _entry("2026-11-03", "1.25", "Setup", proforma),
            _entry("2026-11-03", "1.25", "Setup", fourteen),
        ]
    )

    days = group_report(crm, None)["by_day"]

    assert [(d.date, d.hours, d.invoices) for d in days] == [
        (date(2026, 11, 2), Decimal("8.00"), ["14/2026"]),
        (date(2026, 11, 3), Decimal("8.00"), ["13/2026", "14/2026", "proforma 14/2026"]),
    ]
    assert [[(i.number, i.kind, i.hours) for i in d.hours_by_invoice] for d in days] == [
        [("14/2026", "invoice", Decimal("3.00"))],
        [
            ("13/2026", "invoice", Decimal("3.00")),
            ("14/2026", "invoice", Decimal("3.75")),
            ("14/2026", "proforma", Decimal("1.25")),
        ],
    ]


def test_report_refuses_a_match_not_linked(clean: Session) -> None:
    _admin, match_id = _active(clean)
    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])

    with pytest.raises(InvalidState) as refused:
        _service(clean, http).report(match_id)

    assert "Pigro non ha ancora il deal" in refused.value.message
    assert http.calls == []


def test_report_of_a_match_not_active_yet_says_so(clean: Session) -> None:
    """A match still waiting for its signature has no link state at all: the refusal
    says so in a sentence of its own, never the card's empty one."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match_id = _draft(clean, admin_id, freelancer_id, company_id)
    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])

    with pytest.raises(InvalidState) as refused:
        _service(clean, http).report(match_id)

    assert refused.value.message == REPORT_NOT_ACTIVE
    assert REPORT_NOT_ACTIVE == "Il match non è ancora attivo: nessun consuntivo da leggere."
    assert http.calls == []


def _linked_match(session: Session, start: date | None) -> UUID:
    _admin, match_id = _active(session, pigro_status="linked")
    match = _match(session, match_id)
    match.letter_start_date = start
    match.pigro_url = DEAL_URL
    session.commit()
    return match_id


def _asked(http: RecordedPigro) -> list[tuple[str, str]]:
    """The `from` and `to` of every report request, in order."""
    periods = []
    for method, url, _headers, _sent in http.calls:
        assert method == "GET"
        query = parse_qs(urlsplit(url).query)
        periods.append((query["from"][0], query["to"][0]))
    return periods


def test_report_answers_the_whole_engagement_by_default(clean: Session) -> None:
    today = date(2026, 11, 15)
    match_id = _linked_match(clean, date(2026, 10, 1))
    invoice = _invoice("invoice", 12)
    body = _crm(
        [_entry("2026-10-01", "8.00", "Setup", invoice)],
        [{**invoice, "hours": "8.00"}],
        invoiced="8.00",
    )
    http = RecordedPigro([(200, json.dumps(body).encode())])

    report = _service(clean, http, today=today).report(match_id)

    [(method, url, headers, sent)] = http.calls
    assert (method, sent) == ("GET", b"")
    assert url.startswith(f"{PIGRO}/api/rebase/engagements/{match_id}/report?")
    assert headers == {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    assert _asked(http) == [("2026-10-01", "2026-11-15")]
    assert (report.match_id, report.pigro_url, report.pigro_status) == (
        match_id,
        DEAL_URL,
        "linked",
    )
    assert (report.expected_days, report.expected_hours) == (40, Decimal("320.00"))
    assert (report.hours_total, report.progress, report.invoiced_hours) == (
        Decimal("8.00"),
        Decimal("2.50"),
        Decimal("8.00"),
    )
    assert [(i.number, i.hours) for i in report.invoices] == [("12/2026", Decimal("8.00"))]

    _service(clean, http, today=today).report(match_id, date(2026, 10, 5), date(2026, 10, 31))
    assert _asked(http)[-1] == ("2026-10-05", "2026-10-31")
    with pytest.raises(ValidationFailed):
        _service(clean, http, today=today).report(match_id, date(2026, 11, 2), date(2026, 11, 1))
    # A letter that starts after today has no hours yet: today's are asked for.
    _service(clean, http, today=date(2026, 9, 20)).report(match_id)
    assert _asked(http)[-1] == ("2026-09-20", "2026-09-20")


def test_report_of_a_letter_that_starts_tomorrow_is_empty(clean: Session) -> None:
    """A letter signed before its start: `from` would come after `to`, which the CRM
    refuses with a 422. Today alone is asked for, and the report is empty."""
    today = date(2026, 9, 30)
    match_id = _linked_match(clean, today + timedelta(days=1))
    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])

    report = _service(clean, http, today=today).report(match_id)

    assert _asked(http) == [(today.isoformat(), today.isoformat())]
    assert (report.hours_total, report.progress) == (Decimal("0.00"), Decimal("0.00"))
    assert (report.by_day, report.by_week, report.by_month, report.invoices) == (
        [],
        [],
        [],
        [],
    )


def test_report_default_start_for_an_older_match(clean: Session) -> None:
    """A match written before migration 0021 has no `letter_start_date`: the report
    starts where its letter printed the start, and, for a letter that printed none, on
    the day the match was written. Never today, which would hide every hour before."""
    today = date(2026, 11, 15)
    match_id = _linked_match(clean, None)
    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])
    service = _service(clean, http, today=today)

    service.report(match_id)
    assert _asked(http) == [("2026-10-01", "2026-11-15")]

    letter = _letter(clean, match_id)
    letter.data = {**letter.data, "data-inizio": None}
    clean.execute(
        text("UPDATE matches SET created_at = :at WHERE id = :id"),
        # 23:30 UTC on 31 August is already 1 September in Rome.
        {"at": datetime(2026, 8, 31, 23, 30, tzinfo=UTC), "id": match_id},
    )
    clean.commit()

    service.report(match_id)
    assert _asked(http)[-1] == ("2026-09-01", "2026-11-15")


def test_report_walks_800_day_windows(clean: Session) -> None:
    """The CRM answers at most 800 days a call: a 1000-day engagement is two calls over
    contiguous windows, their rows one report, an invoice spanning both counted once
    with the hours of both, and the billed hours summed."""
    start = date(2026, 10, 1)
    today = start + timedelta(days=1000)
    match_id = _linked_match(clean, start)
    invoice = _invoice("invoice", 12)
    first = _crm(
        [_entry("2026-10-01", "8.00", "Setup", invoice)],
        [{**invoice, "hours": "8.00"}],
        invoiced="8.00",
    )
    second = _crm(
        [_entry(today.isoformat(), "6.00", "Chiusura", invoice), _entry(today.isoformat(), "2")],
        [{**invoice, "hours": "6.00"}],
        invoiced="6.00",
        not_invoiced="2.00",
    )
    http = RecordedPigro([(200, json.dumps(first).encode()), (200, json.dumps(second).encode())])

    report = _service(clean, http, today=today).report(match_id)

    end_of_first = start + timedelta(days=REPORT_MAX_DAYS)
    assert _asked(http) == [
        (start.isoformat(), end_of_first.isoformat()),
        ((end_of_first + timedelta(days=1)).isoformat(), today.isoformat()),
    ]
    assert [(d.date, d.hours) for d in report.by_day] == [
        (start, Decimal("8.00")),
        (today, Decimal("8.00")),
    ]
    assert (report.hours_total, report.invoiced_hours, report.hours_uninvoiced) == (
        Decimal("16.00"),
        Decimal("14.00"),
        Decimal("2.00"),
    )
    assert [(i.number, i.hours) for i in report.invoices] == [("12/2026", Decimal("14.00"))]


def test_report_on_409_files_the_match_rejected(clean: Session) -> None:
    """The freelancer deleted the deal (spec § 3.10): the report is refused as a match
    not linked is, in the words the card then says, and the match is written
    `rejected` with the CRM's own sentence, the one the link would store. The sweep
    leaves it alone, the next report is refused before the CRM is asked, and «Riprova»
    links it again once the deal is restored. A 409 with nothing to say names its
    status."""
    match_id = _linked_match(clean, date(2026, 10, 1))
    problem = {"type": "x", "title": "Conflitto", "status": 409, "detail": DEAL_GONE}

    for body, cause in (
        (_crm_conflict(match_id, DEAL_GONE), DEAL_GONE),
        (json.dumps(problem).encode(), DEAL_GONE),
        (b"", "HTTP 409"),
    ):
        match = _match(clean, match_id)
        match.pigro_status, match.pigro_error = "linked", None
        clean.commit()
        with pytest.raises(InvalidState) as gone:
            _service(clean, RecordedPigro([(409, body)])).report(match_id)
        assert gone.value.message == pigro_state_sentence("rejected", cause)
        match = _match(clean, match_id)
        assert (match.pigro_status, match.pigro_error) == ("rejected", cause)
        assert match.pigro_url == DEAL_URL
    assert gone.value.message == "Pigro ha rifiutato il collegamento: HTTP 409."

    http = RecordedPigro([(200, linked_body(space_created=False, created=False))])
    service = _service(clean, http, sender=RecordingSender())
    assert service.link_pending() == SweepCount(0, 0)
    with pytest.raises(InvalidState) as still:
        service.report(match_id)
    assert still.value.message == "Pigro ha rifiutato il collegamento: HTTP 409."
    assert http.calls == []

    assert service.link(match_id).pigro_status == "linked"


def test_report_on_409_leaves_a_state_written_meanwhile(hub_engine: Engine, clean: Session) -> None:
    """The refusal is written only over `linked`: a match «Riprova» took elsewhere
    to `error` while the report was asked keeps what it says now."""
    match_id = _linked_match(clean, date(2026, 10, 1))

    def crm_while_the_match_moves(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_status = 'error', pigro_error = 'HTTP 503' "
                    "WHERE id = :id"
                ),
                {"id": match_id},
            )
            other.commit()
        finally:
            other.close()
        return 409, _crm_conflict(match_id, DEAL_GONE)

    service = EngagementService(
        clean, _settings(), crm_while_the_match_moves, now=lambda: NOW, today=lambda: TODAY
    )
    with pytest.raises(InvalidState, match="Pigro ha rifiutato il collegamento"):
        service.report(match_id)

    match = _match(clean, match_id)
    assert (match.pigro_status, match.pigro_error) == ("error", "HTTP 503")


def test_report_on_409_keeps_a_link_made_after_it_was_asked(
    hub_engine: Engine, clean: Session
) -> None:
    """While the report's `GET` comes back `409`, another caller links the match again
    (the deal restored, «Riprova» pressed): that link is newer than the report, whose
    `409` then says nothing about the deal it found, and the match stays `linked`."""
    match_id = _linked_match(clean, date(2026, 10, 1))

    def crm_after_a_newer_link(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_status = 'linked', pigro_error = NULL, "
                    "pigro_linked_at = :at WHERE id = :id"
                ),
                {"at": NOW + timedelta(seconds=1), "id": match_id},
            )
            other.commit()
        finally:
            other.close()
        return 409, _crm_conflict(match_id, DEAL_GONE)

    service = EngagementService(
        clean, _settings(), crm_after_a_newer_link, now=lambda: NOW, today=lambda: TODAY
    )
    with pytest.raises(InvalidState, match="Pigro ha rifiutato il collegamento"):
        service.report(match_id)

    match = _match(clean, match_id)
    assert (match.pigro_status, match.pigro_error, match.pigro_url) == (
        "linked",
        None,
        DEAL_URL,
    )


def test_report_when_pigro_does_not_answer_or_is_not_configured(clean: Session) -> None:
    match_id = _linked_match(clean, date(2026, 10, 1))

    for answer, sentence in (
        (ConnectionRefusedError("refused"), NOT_ANSWERING),
        ((500, b""), ANSWERED_STATUS.format(status=500)),
        ((200, b"[]"), NOT_THE_SHAPE),
        ((200, b'{"giorni": [{"data": "ieri"}]}'), NOT_THE_SHAPE),
        ((200, b"x" * 1_048_577), TOO_LONG),
    ):
        with pytest.raises(PigroUnavailable) as unavailable:
            _service(clean, RecordedPigro([answer])).report(match_id)
        assert str(unavailable.value) == sentence

    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])
    with pytest.raises(PigroUnavailable) as off:
        _service(clean, http, settings=_settings(token="")).report(match_id)
    assert str(off.value) == PIGRO_NOT_CONFIGURED
    with pytest.raises(PigroUnavailable) as clear:
        _service(clean, http, settings=_settings(url="http://pigro.example")).report(match_id)
    assert str(clear.value) == HTTPS_ONLY
    assert http.calls == []
