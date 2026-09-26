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
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_matches import SIGNER, TABLES, TODAY, _body, _framework, _setup

from rebase_core.audit import AdminActionService
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.engagements import (
    CAUSE_NOT_THE_SHAPE,
    CAUSE_REFUSED,
    CAUSE_TIMEOUT,
    CAUSE_TOO_LONG,
    CAUSE_UNREACHABLE,
    HOURS_PER_DAY,
    REPORT_MAX_DAYS,
    REPORT_NOT_ACTIVE,
    EngagementService,
    PigroLinkResult,
    group_report,
    normalise_fiscal_code,
    normalise_vat,
)
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.mail import Mail, RecordingSender, engagement_ready_mail
from rebase_core.match_words import (
    HTTPS_ONLY,
    PIGRO_NOT_CONFIGURED,
    PROFILE_WITHOUT_NAME,
    SIGNER_CF_TOO_LONG,
    SIGNER_PEC_INVALID,
    pigro_state_sentence,
)
from rebase_core.matches import MatchService
from rebase_core.models import Company, ContractDocument, Freelancer, Match, User
from rebase_core.pigro import (
    ANSWERED_STATUS,
    NOT_ANSWERING,
    NOT_THE_SHAPE,
    TOO_LONG,
    PigroUnavailable,
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


def _sign(session: Session, match_id: UUID, pigro_stato: str | None = "da_collegare") -> None:
    """The letter signed and the match active, as `_confirm_completion` leaves them."""
    letter = _letter(session, match_id)
    letter.stato = "firmato"
    letter.signed_at = SIGNED_AT
    match = session.get(Match, match_id)
    assert match is not None
    match.stato = "attivo"
    match.pigro_stato = pigro_stato
    session.commit()


def _draft(session: Session, admin_id: UUID, freelancer_id: UUID, company_id: UUID) -> UUID:
    read = _matches(session).create(freelancer_id, _body(company_id, giorni_previsti=40), admin_id)
    return read.id


def _active(session: Session, pigro_stato: str | None = "da_collegare") -> tuple[UUID, UUID]:
    """One freelancer, one request, one active match: `(admin_id, match_id)`."""
    admin_id, freelancer_id, company_id = _setup(session)
    _framework(session, freelancer_id, admin_id)
    match_id = _draft(session, admin_id, freelancer_id, company_id)
    _sign(session, match_id, pigro_stato)
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
    match.lettera_data_fine = date(2026, 12, 31)
    match.lettera_compenso = Decimal("400.00")
    clean.commit()

    body = _service(clean, RecordedPigro([(500, b"")])).payload(match, letter, user, company)

    assert body == {
        "freelancer": {"email": "ada@studio.it", "nome": "Ada", "cognome": "Lovelace"},
        "lettera": {
            "numero": letter.numero,
            "ruolo": "Backend developer",
            "azienda": "ACME Srl",
            "data_inizio": "2026-10-01",
            "data_fine": "2026-12-31",
            "compenso": "400.00",
            "giorni_previsti": 40,
        },
        "rebase": {
            "ragione_sociale": "rebase S.r.l.",
            "partita_iva": "00000000000",
            "codice_fiscale": "00000000000",
            "indirizzo": "Milano",
            "pec": "rebase@pec.example",
            "codice_sdi": "0000000",
        },
    }
    json.dumps(body)


def test_payload_from_an_older_match_parses_the_printed_letter(clean: Session) -> None:
    """A match written before migration 0021 has no `lettera_*` values: the letter's own
    printed fields are read back instead, `1° ottobre 2026` as `2026-10-01`."""
    _admin, match_id = _active(clean)
    match, letter, user, company = _parts(clean, match_id)
    match.lettera_data_inizio = None
    match.lettera_data_fine = None
    match.lettera_compenso = None
    match.giorni_previsti = None
    letter.data = {**letter.data, "data-fine": "31 dicembre 2026"}
    clean.commit()
    assert letter.data["data-inizio"] == "1° ottobre 2026"

    lettera = _service(clean, RecordedPigro([(500, b"")])).payload(match, letter, user, company)[
        "lettera"
    ]

    assert (lettera["data_inizio"], lettera["data_fine"]) == ("2026-10-01", "2026-12-31")
    assert (lettera["compenso"], lettera["giorni_previsti"]) == ("450.00", None)


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

    assert rebase["partita_iva"] == "01234567890"
    assert rebase["codice_sdi"] is None
    assert rebase["indirizzo"] == "V" * 255
    assert normalise_vat("IT 0123 456 7890") == "01234567890"
    assert normalise_vat("01234567890") == "01234567890"
    assert normalise_vat("0123456789") is None
    assert normalise_vat("IT0123456789X") is None
    assert normalise_vat("   ") is None
    assert normalise_vat(None) is None


def test_payload_sends_rebase_pec_and_tax_code_as_the_crm_takes_them(clean: Session) -> None:
    """The tax code compacted and in capitals, the PEC as it is: both pass the rules of
    the CRM's door (`EmailStr`, at most 16 characters)."""
    _admin, match_id = _active(clean)
    signer = {**SIGNER, "rebase-cf": " rss mra 80a01 h501u ", "rebase-pec": "rebase@pec.it"}
    service = _service(clean, RecordedPigro([(500, b"")]), settings=_settings(signer=signer))

    rebase = service.payload(*_parts(clean, match_id))["rebase"]

    assert (rebase["codice_fiscale"], rebase["pec"]) == ("RSSMRA80A01H501U", "rebase@pec.it")
    assert normalise_fiscal_code("  ") is None
    assert normalise_fiscal_code(None) is None


@pytest.mark.parametrize(
    ("field", "value", "sentence"),
    [
        ("rebase-pec", "rebase at pec", SIGNER_PEC_INVALID),
        ("rebase-pec", "rebase@", SIGNER_PEC_INVALID),
        ("rebase-cf", "RSSMRA80A01H501UX", SIGNER_CF_TOO_LONG),
    ],
)
def test_link_with_rebase_data_the_crm_would_refuse_asks_nothing(
    clean: Session, field: str, value: str, sentence: str
) -> None:
    """A PEC that is not an address, a tax code longer than 16: the CRM's door would
    refuse either in English, so neither leaves. The match waits as `errore` with the
    hub's own sentence, which the card shows alone, and the sweep tries again once
    `REBASE_SIGNER_JSON` is fixed."""
    _admin, match_id = _active(clean)
    http = RecordedPigro([(201, linked_body())])
    settings = _settings(signer={**SIGNER, field: value})

    read = _service(clean, http, settings=settings).link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("errore", sentence)
    assert http.calls == []
    assert pigro_state_sentence("errore", read.pigro_errore) == sentence

    assert _service(clean, http).link(match_id).pigro_stato == "collegato"


def test_payload_refuses_a_letter_that_is_not_signed(clean: Session) -> None:
    _admin, match_id = _active(clean)
    match, letter, user, company = _parts(clean, match_id)
    letter.stato = "inviato"
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
    assert _match(clean, match_id).pigro_stato is None


def test_link_without_token_marks_da_collegare_and_calls_nothing(
    clean: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Alembic's `fileConfig`, run by the session's migrations, disables every logger
    # that existed before it: this one is read back on for the test.
    monkeypatch.setattr(logging.getLogger("rebase_core.engagements"), "disabled", False)
    _admin, match_id = _active(clean, pigro_stato=None)
    http = RecordedPigro([(201, linked_body())])

    with caplog.at_level(logging.INFO, logger="rebase_core.engagements"):
        read = _service(clean, http, settings=_settings(token="")).link(match_id)

    assert (read.pigro_stato, read.pigro_attempted_at) == ("da_collegare", None)
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

    assert (read.pigro_stato, read.pigro_errore) == ("errore", HTTPS_ONLY)
    assert http.calls == []
    # The hub's own sentence, shown alone: nothing was asked of Pigro.
    assert pigro_state_sentence("errore", read.pigro_errore) == HTTPS_ONLY

    local = _service(clean, http, settings=_settings(url="http://localhost:8000")).link(match_id)

    assert local.pigro_stato == "collegato"
    [(_method, url, _headers, _sent)] = http.calls
    assert url == f"http://localhost:8000/api/rebase/engagements/{match_id}"


def test_link_on_201_is_collegato_and_mails_once(clean: Session) -> None:
    _admin, match_id = _active(clean)
    http = RecordedPigro(
        [(201, linked_body()), (200, linked_body(spazio_creato=False, creato=False))]
    )
    sender = RecordingSender()
    service = _service(clean, http, sender=sender)

    read = service.link(match_id)

    assert read.pigro_stato == "collegato"
    assert (read.pigro_slug, read.pigro_deal_id, read.pigro_url) == ("ada-lovelace", DEAL, DEAL_URL)
    assert (read.pigro_linked_at, read.pigro_attempted_at) == (NOW, NOW)
    assert (read.pigro_errore, read.pigro_mail_sent_at) == (None, NOW)
    [(method, url, headers, body)] = http.calls
    assert (method, url) == ("PUT", f"{PIGRO}/api/rebase/engagements/{match_id}")
    assert headers == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert json.loads(body) == service.payload(*_parts(clean, match_id))
    [mail] = sender.sent
    numero = _letter(clean, match_id).numero
    assert mail.to == "ada@studio.it"
    assert mail.subject == f"La tua lettera n. {numero} è attiva: le ore si registrano su Pigro"
    assert DEAL_URL in mail.text and "ACME Srl" in mail.text
    assert "a tuo nome" in mail.text

    again = service.link(match_id)

    assert len(http.calls) == 2
    assert (again.pigro_stato, again.pigro_linked_at, again.pigro_mail_sent_at) == (
        "collegato",
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
        RecordedPigro([(200, linked_body(creato=False))]),
        sender=sender,
        now=lambda: NOW,
        today=lambda: TODAY,
    )
    raced: list[str | None] = []

    def crm_while_a_second_link_runs(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        raced.append(second.link(match_id).pigro_stato)
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

    assert raced == ["collegato"]
    assert (read.pigro_stato, read.pigro_mail_sent_at) == ("collegato", NOW)
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
                    RecordedPigro([(200, linked_body(creato=False))]),
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
    assert read.pigro_stato == "collegato"
    assert read.pigro_attempted_at == elsewhere


def test_link_leaves_a_match_another_caller_already_linked(
    hub_engine: Engine, clean: Session
) -> None:
    """Two callers (the sweep and «Riprova») overlap: the one that reaches the row second
    finds it `collegato` and leaves it, whatever its own call answered."""
    _admin, match_id = _active(clean)
    winner = f"{PIGRO}/ada-lovelace/app/deal/{CUSTOMER}"

    def crm_after_the_winner(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_stato = 'collegato', pigro_url = :url, "
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

    assert (read.pigro_stato, read.pigro_url, read.pigro_errore) == ("collegato", winner, None)


def test_link_writes_the_crm_sentence_on_409_as_rifiutato(clean: Session) -> None:
    """The sentence stored is the `Conflict`'s `reason`, not its `detail`, which the CRM
    prefixes with the entity («engagement: …»)."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()
    http = RecordedPigro([(409, _crm_conflict(match_id, DEAL_GONE))])

    read = _service(clean, http, sender=sender).link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("rifiutato", DEAL_GONE)
    assert (read.pigro_slug, read.pigro_url, read.pigro_linked_at) == (None, None, None)
    assert read.pigro_attempted_at == NOW
    assert sender.sent == []


def test_link_on_409_turns_a_linked_match_rifiutato_until_riprova(clean: Session) -> None:
    """The freelancer deleted the deal after the link (spec § 3.10): for a match that
    already has one, the door's only `409` says so, and it is written over `collegato`.
    The sweep then leaves the match alone; once the deal is restored, «Riprova» finds it
    again, and the mail already sent is not sent twice."""
    _admin, match_id = _active(clean)
    sender = RecordingSender()
    http = RecordedPigro(
        [
            (201, linked_body()),
            (409, _crm_conflict(match_id, DEAL_GONE)),
            (200, linked_body(spazio_creato=False, creato=False)),
        ]
    )
    service = _service(clean, http, sender=sender)
    assert service.link(match_id).pigro_stato == "collegato"

    refused = service.link(match_id)

    assert (refused.pigro_stato, refused.pigro_errore) == ("rifiutato", DEAL_GONE)
    # The registry row still points at the deal: the link is kept for «Riprova».
    assert (refused.pigro_url, refused.pigro_deal_id) == (DEAL_URL, DEAL)
    assert pigro_state_sentence(refused.pigro_stato, refused.pigro_errore) == DEAL_GONE_SENTENCE
    assert service.link_pending() == PigroLinkResult(0, 0)
    assert len(http.calls) == 2

    again = service.link(match_id)

    assert (again.pigro_stato, again.pigro_errore, again.pigro_url) == (
        "collegato",
        None,
        DEAL_URL,
    )
    assert len(sender.sent) == 1


def test_link_keeps_a_link_made_after_its_409_was_asked(hub_engine: Engine, clean: Session) -> None:
    """Two callers overlap: while this one's `PUT` comes back `409`, another links the
    match. That link is newer than this call, whose `409` then says nothing about the
    deal it found: the match stays `collegato`."""
    _admin, match_id = _active(clean)

    def crm_after_a_newer_link(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_stato = 'collegato', pigro_url = :url, "
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

    assert (read.pigro_stato, read.pigro_url, read.pigro_errore) == ("collegato", DEAL_URL, None)


def test_link_on_422_is_rifiutato(clean: Session) -> None:
    """A body the CRM's door refuses names the field, never in English: FastAPI's own
    list of errors as `field: non valido` for each field, the value left out; a
    `ValidationFailed` from the space by its `detail`, `entity.field: reason`, never by
    its bare `reason`, which would drop the field; an empty body by the status."""
    _admin, match_id = _active(clean)
    fastapi = {
        "detail": [
            {
                "loc": ["body", "rebase", "partita_iva"],
                "msg": "String should match pattern '^\\d{11}$'",
                "type": "string_pattern_mismatch",
                "input": "0123",
            },
            {
                "loc": ["body", "rebase", "partita_iva"],
                "msg": "Value error, twice",
                "type": "value_error",
                "input": "0123",
            },
            {
                "loc": ["body", "lettera", "giorni_previsti"],
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
        "detail": "customer.codice_sdi: deve essere di 7 caratteri",
        "code": "validation_failed",
        "instance": f"/api/rebase/engagements/{match_id}",
        "entity": "customer",
        "field": "codice_sdi",
        "reason": "deve essere di 7 caratteri",
        "expected": "7 caratteri",
    }
    for body, sentence in (
        (
            json.dumps(fastapi).encode(),
            "rebase.partita_iva: non valido; lettera.giorni_previsti: non valido",
        ),
        (json.dumps(problem).encode(), "customer.codice_sdi: deve essere di 7 caratteri"),
        (json.dumps({"detail": [{"msg": "Field required"}]}).encode(), "HTTP 422"),
        (b"", "HTTP 422"),
    ):
        read = _service(clean, RecordedPigro([(422, body)])).link(match_id)
        assert (read.pigro_stato, read.pigro_errore) == ("rifiutato", sentence)


def test_link_of_a_freelancer_without_a_surname_asks_nothing_and_retries(
    clean: Session,
) -> None:
    """The door wants a name and a surname: a profile without one is not sent at all,
    and the match waits as `errore` with the hub's own sentence, which the card shows
    alone. The next link, once the profile is complete, goes through."""
    _admin, match_id = _active(clean)
    user = _parts(clean, match_id)[2]
    user.cognome = "  "
    clean.commit()
    http = RecordedPigro([(201, linked_body())])

    read = _service(clean, http).link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("errore", PROFILE_WITHOUT_NAME)
    assert read.pigro_attempted_at == NOW
    assert http.calls == []
    assert pigro_state_sentence("errore", read.pigro_errore) == PROFILE_WITHOUT_NAME

    user = _parts(clean, match_id)[2]
    user.cognome = "Lovelace"
    clean.commit()
    assert _service(clean, http).link(match_id).pigro_stato == "collegato"
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
def test_link_that_gets_no_answer_is_errore_with_its_cause(
    clean: Session, failure: Exception, cause: str
) -> None:
    """The stored cause is short, since the card wraps it: «Pigro non ha risposto:
    timeout.», never «Pigro non ha risposto: Pigro non risponde.»"""
    _admin, match_id = _active(clean)
    http = RecordedPigro([failure])

    read = _service(clean, http, sender=RecordingSender()).link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("errore", cause)
    assert pigro_state_sentence("errore", read.pigro_errore) == f"Pigro non ha risposto: {cause}."
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
        ((201, b"x" * 1_048_577), CAUSE_TOO_LONG),
    ],
)
def test_link_on_any_other_answer_is_errore_with_its_cause(
    clean: Session, answer: tuple[int, bytes], cause: str
) -> None:
    _admin, match_id = _active(clean)

    read = _service(clean, RecordedPigro([answer])).link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("errore", cause)


def test_link_after_an_error_clears_the_sentence(clean: Session) -> None:
    _admin, match_id = _active(clean)
    http = RecordedPigro([(503, b""), (201, linked_body())])
    service = _service(clean, http)

    assert service.link(match_id).pigro_stato == "errore"
    read = service.link(match_id)

    assert (read.pigro_stato, read.pigro_errore) == ("collegato", None)


def test_link_retries_a_refused_mail(clean: Session) -> None:
    """The provider refusing the freelancer's mail leaves the match `collegato` and the
    stamp empty, so the next call (the sweep's) sends it and stamps it."""
    _admin, match_id = _active(clean)
    sender = FlakySender(refusals=1)
    service = _service(clean, RecordedPigro([(201, linked_body())]), sender=sender)

    first = service.link(match_id)

    assert (first.pigro_stato, first.pigro_mail_sent_at) == ("collegato", None)
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
    assert action.payload == {"esito": "collegato", "errore": None}


def test_the_mail_says_where_the_hours_go() -> None:
    mail = engagement_ready_mail(
        "ada@studio.it",
        nome="Ada",
        numero="2026-001",
        azienda="ACME <Srl>",
        deal_url=DEAL_URL + '?"x"',
        spazio_creato=True,
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
        nome="",
        numero="2026-001",
        azienda="ACME Srl",
        deal_url=DEAL_URL,
        spazio_creato=False,
    )
    assert plain.text.startswith("Ciao,\n\n")
    assert "a tuo nome" not in plain.text


# ---- link_pending ----------------------------------------------------------------------


def test_link_pending_counts(clean: Session) -> None:
    """The sweep's round: a match waiting is linked, one the CRM does not answer for
    stays `errore`, one linked with its mail refused gets its mail, and one that cannot
    even build its body fails alone without stopping the others."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)
    waiting, failing, unmailed, broken = (
        _draft(clean, admin_id, freelancer_id, company_id) for _ in range(4)
    )
    _sign(clean, waiting)
    _sign(clean, failing, "errore")
    _sign(clean, unmailed, "collegato")
    _match(clean, unmailed).pigro_url = DEAL_URL
    _sign(clean, broken)
    letter = _letter(clean, broken)
    letter.stato = "generato"
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

    assert service.link_pending() == PigroLinkResult(linked=1, failed=2)

    assert sorted(asked, key=str) == sorted([waiting, failing, unmailed], key=str)
    assert _match(clean, waiting).pigro_stato == "collegato"
    assert _match(clean, failing).pigro_stato == "errore"
    assert _match(clean, unmailed).pigro_mail_sent_at == NOW
    assert _match(clean, broken).pigro_stato == "da_collegare"
    assert len(sender.sent) == 2


def test_link_pending_skips_rifiutato(clean: Session) -> None:
    """A refusal is the CRM's word on this match: only an admin's «Riprova» asks again.
    A match not active, or linked with its mail sent, is not asked for either."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)
    refused, draft, done = (_draft(clean, admin_id, freelancer_id, company_id) for _ in range(3))
    _sign(clean, refused, "rifiutato")
    _sign(clean, done, "collegato")
    match = _match(clean, done)
    match.pigro_mail_sent_at = NOW
    clean.commit()
    http = RecordedPigro([(201, linked_body())])

    assert _service(clean, http, sender=RecordingSender()).link_pending() == PigroLinkResult(0, 0)
    assert http.calls == []
    assert _match(clean, draft).pigro_stato is None


def test_link_pending_without_token_or_mail_sender(clean: Session) -> None:
    """No token: nothing is asked, the matches wait. No mail sender: a linked match
    missing only its mail has nothing left to do here, and is not asked again."""
    _admin, match_id = _active(clean, pigro_stato="collegato")
    http = RecordedPigro([(200, linked_body())])

    assert _service(
        clean, http, sender=RecordingSender(), settings=_settings(token="")
    ).link_pending() == (PigroLinkResult(0, 0))
    assert _service(clean, http).link_pending() == PigroLinkResult(0, 0)
    assert http.calls == []


# ---- the report ------------------------------------------------------------------------


def _invoice(
    tipo: str, numero: int | None, anno: int | None = 2026, **extra: Any
) -> dict[str, Any]:
    return {
        # One id per invoice, so two of them never merge when windows are joined.
        "id": str(uuid5(NAMESPACE_URL, f"{tipo}/{numero}/{anno}")),
        "tipo": tipo,
        "anno": anno,
        "numero": numero,
        "stato": "emessa",
        "stato_pagamento": "da_incassare",
        "data": "2027-01-31",
        **extra,
    }


def _entry(
    day: str, ore: str, descrizione: str = "", fattura: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "data": day,
        "ore": ore,
        "descrizione": descrizione,
        "fatturabile": True,
        "fattura": fattura,
    }


def _crm(
    giorni: list[dict[str, Any]],
    fatture: list[dict[str, Any]] | None = None,
    *,
    fatturate: str = "0.00",
    non_fatturate: str = "0.00",
) -> dict[str, Any]:
    return {
        "slug": "ada-lovelace",
        "deal_url": DEAL_URL,
        "deal": {
            "id": str(DEAL),
            "nome": "Lettera n. 2026-001 · Backend developer per ACME Srl",
            "tariffa_oraria": "56.250000",
            "ore_preventivate": "320.00",
            "stato": "in corso",
        },
        "giorni": giorni,
        "totale_ore": "0.00",
        "ore_fatturate": fatturate,
        "ore_non_fatturate": non_fatturate,
        "fatture": fatture or [],
    }


def test_report_groups_by_iso_week_and_month() -> None:
    """ISO weeks cross the year: 1 January 2027 is still in 2026's week 53, and 4 January
    opens 2027's first. Days, weeks and months all sum to the same total."""
    fattura, proforma = _invoice("fattura", 12), _invoice("proforma", 3)
    crm = _crm(
        [
            _entry("2026-12-28", "8.00", "Setup", fattura),
            _entry("2026-12-28", "4", "  ", fattura),
            _entry("2026-12-28", "2.5", "Riunione", proforma),
            _entry("2026-12-31", "8.00", "API", fattura),
            _entry("2027-01-01", "4.00", "API"),
            _entry("2027-01-04", "8.00", "Test"),
        ],
        [{**fattura, "ore": "20.00"}, {**proforma, "ore": "2.50", "data": None}],
        fatturate="20.00",
        non_fatturate="14.50",
    )

    fields = group_report(crm, None)

    days = [(d.data, d.ore, d.descrizioni, d.fatture) for d in fields["per_giorno"]]
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
    weeks = [(w.settimana, w.da, w.a, w.ore) for w in fields["per_settimana"]]
    assert weeks == [
        ("2026-W53", date(2026, 12, 28), date(2027, 1, 3), Decimal("26.50")),
        ("2027-W01", date(2027, 1, 4), date(2027, 1, 10), Decimal("8.00")),
    ]
    assert [(m.mese, m.ore) for m in fields["per_mese"]] == [
        ("2026-12", Decimal("22.50")),
        ("2027-01", Decimal("12.00")),
    ]
    total = Decimal("34.50")
    assert fields["totale_ore"] == total
    assert sum(w.ore for w in fields["per_settimana"]) == total
    assert sum(m.ore for m in fields["per_mese"]) == total
    assert (fields["ore_fatturate"], fields["ore_non_fatturate"]) == (
        Decimal("20.00"),
        Decimal("14.50"),
    )
    assert (fields["giorni_previsti"], fields["ore_previste"], fields["avanzamento"]) == (
        None,
        None,
        None,
    )
    assert fields["giorni_equivalenti"] == Decimal("4.31")
    invoices = [(i.numero, i.tipo, i.data, i.stato, i.ore) for i in fields["fatture"]]
    assert invoices == [
        ("12/2026", "fattura", date(2027, 1, 31), "emessa", Decimal("20.00")),
        ("3/2026", "proforma", None, "emessa", Decimal("2.50")),
    ]

    twelve_days = _crm([_entry(f"2026-10-{day:02d}", "8.00") for day in range(1, 13)])
    progress = group_report(twelve_days, 40)
    assert (progress["totale_ore"], progress["giorni_equivalenti"]) == (
        Decimal("96.00"),
        Decimal("12.00"),
    )
    assert progress["ore_previste"] == Decimal("320.00") == 40 * HOURS_PER_DAY
    assert str(progress["avanzamento"]) == "30.00"
    assert group_report(_crm([]), 40)["avanzamento"] == Decimal("0.00")


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
    _admin, match_id = _active(session, pigro_stato="collegato")
    match = _match(session, match_id)
    match.lettera_data_inizio = start
    match.pigro_url = DEAL_URL
    session.commit()
    return match_id


def _asked(http: RecordedPigro) -> list[tuple[str, str]]:
    """The `da` and `a` of every report request, in order."""
    periods = []
    for method, url, _headers, _sent in http.calls:
        assert method == "GET"
        query = parse_qs(urlsplit(url).query)
        periods.append((query["da"][0], query["a"][0]))
    return periods


def test_report_answers_the_whole_engagement_by_default(clean: Session) -> None:
    today = date(2026, 11, 15)
    match_id = _linked_match(clean, date(2026, 10, 1))
    fattura = _invoice("fattura", 12)
    body = _crm(
        [_entry("2026-10-01", "8.00", "Setup", fattura)],
        [{**fattura, "ore": "8.00"}],
        fatturate="8.00",
    )
    http = RecordedPigro([(200, json.dumps(body).encode())])

    report = _service(clean, http, today=today).report(match_id)

    [(method, url, headers, sent)] = http.calls
    assert (method, sent) == ("GET", b"")
    assert url.startswith(f"{PIGRO}/api/rebase/engagements/{match_id}/report?")
    assert headers == {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    assert _asked(http) == [("2026-10-01", "2026-11-15")]
    assert (report.match_id, report.pigro_url, report.pigro_stato) == (
        match_id,
        DEAL_URL,
        "collegato",
    )
    assert (report.giorni_previsti, report.ore_previste) == (40, Decimal("320.00"))
    assert (report.totale_ore, report.avanzamento, report.ore_fatturate) == (
        Decimal("8.00"),
        Decimal("2.50"),
        Decimal("8.00"),
    )
    assert [(i.numero, i.ore) for i in report.fatture] == [("12/2026", Decimal("8.00"))]

    _service(clean, http, today=today).report(match_id, date(2026, 10, 5), date(2026, 10, 31))
    assert _asked(http)[-1] == ("2026-10-05", "2026-10-31")
    with pytest.raises(ValidationFailed):
        _service(clean, http, today=today).report(match_id, date(2026, 11, 2), date(2026, 11, 1))
    # A letter that starts after today has no hours yet: today's are asked for.
    _service(clean, http, today=date(2026, 9, 20)).report(match_id)
    assert _asked(http)[-1] == ("2026-09-20", "2026-09-20")


def test_report_of_a_letter_that_starts_tomorrow_is_empty(clean: Session) -> None:
    """A letter signed before its start: `da` would come after `a`, which the CRM
    refuses with a 422. Today alone is asked for, and the report is empty."""
    today = date(2026, 9, 30)
    match_id = _linked_match(clean, today + timedelta(days=1))
    http = RecordedPigro([(200, json.dumps(_crm([])).encode())])

    report = _service(clean, http, today=today).report(match_id)

    assert _asked(http) == [(today.isoformat(), today.isoformat())]
    assert (report.totale_ore, report.avanzamento) == (Decimal("0.00"), Decimal("0.00"))
    assert (report.per_giorno, report.per_settimana, report.per_mese, report.fatture) == (
        [],
        [],
        [],
        [],
    )


def test_report_default_start_for_an_older_match(clean: Session) -> None:
    """A match written before migration 0021 has no `lettera_data_inizio`: the report
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
    fattura = _invoice("fattura", 12)
    first = _crm(
        [_entry("2026-10-01", "8.00", "Setup", fattura)],
        [{**fattura, "ore": "8.00"}],
        fatturate="8.00",
    )
    second = _crm(
        [_entry(today.isoformat(), "6.00", "Chiusura", fattura), _entry(today.isoformat(), "2")],
        [{**fattura, "ore": "6.00"}],
        fatturate="6.00",
        non_fatturate="2.00",
    )
    http = RecordedPigro([(200, json.dumps(first).encode()), (200, json.dumps(second).encode())])

    report = _service(clean, http, today=today).report(match_id)

    end_of_first = start + timedelta(days=REPORT_MAX_DAYS)
    assert _asked(http) == [
        (start.isoformat(), end_of_first.isoformat()),
        ((end_of_first + timedelta(days=1)).isoformat(), today.isoformat()),
    ]
    assert [(d.data, d.ore) for d in report.per_giorno] == [
        (start, Decimal("8.00")),
        (today, Decimal("8.00")),
    ]
    assert (report.totale_ore, report.ore_fatturate, report.ore_non_fatturate) == (
        Decimal("16.00"),
        Decimal("14.00"),
        Decimal("2.00"),
    )
    assert [(i.numero, i.ore) for i in report.fatture] == [("12/2026", Decimal("14.00"))]


def test_report_on_409_files_the_match_rifiutato(clean: Session) -> None:
    """The freelancer deleted the deal (spec § 3.10): the report is refused as a match
    not linked is, in the words the card then says, and the match is written
    `rifiutato` with the CRM's own sentence, the one the link would store. The sweep
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
        match.pigro_stato, match.pigro_errore = "collegato", None
        clean.commit()
        with pytest.raises(InvalidState) as gone:
            _service(clean, RecordedPigro([(409, body)])).report(match_id)
        assert gone.value.message == pigro_state_sentence("rifiutato", cause)
        match = _match(clean, match_id)
        assert (match.pigro_stato, match.pigro_errore) == ("rifiutato", cause)
        assert match.pigro_url == DEAL_URL
    assert gone.value.message == "Pigro ha rifiutato il collegamento: HTTP 409."

    http = RecordedPigro([(200, linked_body(spazio_creato=False, creato=False))])
    service = _service(clean, http, sender=RecordingSender())
    assert service.link_pending() == PigroLinkResult(0, 0)
    with pytest.raises(InvalidState) as still:
        service.report(match_id)
    assert still.value.message == "Pigro ha rifiutato il collegamento: HTTP 409."
    assert http.calls == []

    assert service.link(match_id).pigro_stato == "collegato"


def test_report_on_409_leaves_a_state_written_meanwhile(hub_engine: Engine, clean: Session) -> None:
    """The refusal is written only over `collegato`: a match «Riprova» took elsewhere
    to `errore` while the report was asked keeps what it says now."""
    match_id = _linked_match(clean, date(2026, 10, 1))

    def crm_while_the_match_moves(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        other = session_factory(hub_engine)()
        try:
            other.execute(
                text(
                    "UPDATE matches SET pigro_stato = 'errore', pigro_errore = 'HTTP 503' "
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
    assert (match.pigro_stato, match.pigro_errore) == ("errore", "HTTP 503")


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
