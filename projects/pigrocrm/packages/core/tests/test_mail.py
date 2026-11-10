"""The CRM's outbound mail: a seam, Resend behind it, and the one mail this slice sends."""

import http.client
import io
import json
import logging
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from email.message import Message
from uuid import uuid4

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.dashboard.schemas import DayHours, WeekHours
from pigrocrm.core.digest.schemas import (
    DigestDealMove,
    DigestInvoice,
    DigestOffer,
    DigestSignal,
    DigestStage,
    WeeklyDigest,
)
from pigrocrm.core.mail import (
    RESEND_URL,
    USER_AGENT,
    Mail,
    Receipt,
    RecordingSender,
    ResendSender,
    day_short,
    digest_mail,
    digest_subject,
    euro,
    invitation_mail,
    magic_link_mail,
    sender_from_settings,
    urllib_call,
    welcome_mail,
)


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, amt: int = -1) -> bytes:
        return b"{}"


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_without_a_key_there_is_no_sender() -> None:
    assert sender_from_settings(_settings()) is None


def test_with_a_key_the_sender_is_resend_with_the_configured_from() -> None:
    sender = sender_from_settings(
        _settings(resend_api_key="re_x", mail_from="PigroCRM <ciao@x.it>")
    )
    assert isinstance(sender, ResendSender)
    assert sender.sender == "PigroCRM <ciao@x.it>"


def test_resend_posts_one_json_object_with_the_key_as_bearer() -> None:
    calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def http(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        calls.append((method, url, headers, body))
        return 200, b"{}"

    sender = ResendSender("re_x", "PigroCRM <ciao@x.it>", http=http)
    assert sender.send(Mail(to="ada@x.it", subject="s", text="t", html="<p>t</p>")) is True
    (method, url, headers, body) = calls[0]
    assert (method, url) == ("POST", RESEND_URL)
    assert headers["Authorization"] == "Bearer re_x"
    assert b'"to": ["ada@x.it"]' in body and b'"html": "<p>t</p>"' in body


def test_resend_never_raises_and_answers_false_on_a_failure() -> None:
    def boom(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise OSError("down")

    mail = Mail(to="a@x.it", subject="s", text="t")
    assert ResendSender("re_x", "x", http=boom).send(mail) is False
    assert ResendSender("re_x", "x", http=lambda *a: (500, b"")).send(mail) is False


def test_the_magic_link_mail_carries_every_link_and_the_minutes() -> None:
    mail = magic_link_mail(
        "ada@x.it",
        [
            ("studio-ada", "https://pigro.test/studio-ada/app/verify?t=abc"),
            ("secondo", "https://pigro.test/secondo/app/verify?t=def"),
        ],
        15,
    )
    assert mail.to == "ada@x.it"
    assert "15 minuti" in mail.text
    assert "https://pigro.test/studio-ada/app/verify?t=abc" in mail.text
    assert "https://pigro.test/secondo/app/verify?t=def" in mail.text
    assert mail.html is not None and "studio-ada" in mail.html and "&lt;" not in mail.text
    # One space: no label, just the door.
    one = magic_link_mail("a@x.it", [("x", "https://pigro.test/x/app/verify?t=abc")], 15)
    assert one.text.count("https://pigro.test/x/app/verify?t=abc") == 1 and "x:" not in one.text
    # A token that tried to close the tag is escaped in the HTML.
    hostile = magic_link_mail("a@x.it", [("x", 'https://pigro.test/x/app/verify?t="><script>')], 15)
    assert hostile.html is not None and "<script>" not in hostile.html


def test_the_recording_sender_keeps_what_it_was_given() -> None:
    recording = RecordingSender()
    mail = Mail(to="a@x.it", subject="s", text="t")
    assert recording.send(mail) is True
    assert recording.sent == [mail]


def test_the_welcome_mail_enters_with_a_link_and_says_what_to_do_first() -> None:
    verify = "https://pigro.test/ada/app/verify?t=abc"
    login = "https://pigro.test/ada/app/login"
    member = welcome_mail("ada@x.it", verify, login, member=True)
    assert member.subject == "Il tuo spazio PigroCRM è pronto"
    assert member.text.startswith("Ciao,")
    assert verify in member.text and login in member.text
    assert (
        "assistente" in member.text
        and "dati fiscali" in member.text
        and "primo cliente" in member.text
    )
    assert "letsrebase.com/hub/freelance" not in member.text
    assert member.html is not None and verify in member.html
    guest = welcome_mail("bob@x.it", verify, login, member=False)
    assert "letsrebase.com/hub/freelance" in guest.text
    assert guest.html is not None and "hub/freelance" in guest.html
    hostile = welcome_mail(
        "x@x.it", 'https://pigro.test/x/app/verify?t="><script>', login, member=True
    )
    assert hostile.html is not None and "<script>" not in hostile.html


def test_the_invitation_mail_names_the_inviter_and_the_window() -> None:
    """Spec 2026-09-17 §5: who invited, the space, a click and no password, seven
    days and one use, and the ignore-it reassurance. The greeting carries the
    person's name only when the admin typed one."""
    url = "https://pigro.test/studio-ada/app/invite?t=abc"
    mail = invitation_mail("luca@x.it", "studio-ada", "Ada", url, name="Luca", days=7)
    assert mail.to == "luca@x.it"
    assert mail.subject == "Sei stato invitato in studio-ada su PigroCRM"
    assert mail.text.startswith("Ciao Luca,")
    assert "Ada ti ha invitato" in mail.text and "senza scegliere una password" in mail.text
    assert url in mail.text and "Il link vale 7 giorni e funziona una volta sola." in mail.text
    assert "Se non te lo aspettavi, ignora questa mail" in mail.text
    assert mail.html is not None and "Entra nello spazio" in mail.html
    # No name on the invitation: the greeting stays anonymous, like `welcome_mail`'s.
    quiet = invitation_mail("x@x.it", "s", "Ada", url, name=None, days=7)
    assert quiet.text.startswith("Ciao,\n")
    # A space or an inviter that tried to close the tag is escaped in the HTML.
    hostile = invitation_mail("x@x.it", 's"><script>', 'A"><i>', url, name=None, days=7)
    assert (
        hostile.html is not None and "<script>" not in hostile.html and '"><i>' not in hostile.html
    )


# ---- the weekly digest, as a mail ---------------------------------------------------

_WEEK_START = date(2026, 9, 7)  # a Monday


def _invoice(
    *,
    number: str = "2026/1",
    customer: str = "Cliente Prova",
    amount: Decimal = Decimal("0"),
    date_: date = date(2026, 9, 8),
    status: str = "issued",
    # `PaymentStatus`'s own two values, and this is the one an issued invoice carries
    # until somebody pays it. Not a word of its own: the mail now reads this column to
    # decide the state it prints, so a value the register cannot hold would test nothing.
    payment_status: str = "unpaid",
    transmitted: bool = False,
    days_of_delay: int | None = None,
) -> DigestInvoice:
    return DigestInvoice(
        invoice_id=uuid4(),
        number=number,
        customer=customer,
        amount=amount,
        date=date_,
        status=status,
        payment_status=payment_status,
        transmitted=transmitted,
        days_of_delay=days_of_delay,
    )


def digest_with(
    *,
    issued: int = 0,
    expired: Decimal = Decimal("0"),
    customer: str | None = None,
    number: str | None = None,
    paid: int = 0,
    won_to_invoice: int = 0,
    unbilled_hours: Decimal = Decimal("0"),
    accrued_value: Decimal = Decimal("0"),
    total_hours: Decimal | None = None,
    days_without_hours: int = 0,
    pipeline: int = 0,
    deal_moves: int = 0,
    offers: int = 0,
    signals: int = 0,
) -> WeeklyDigest:
    """A `WeeklyDigest` with every list empty and every figure zero -- a still week --
    save for whichever section a test asks for by count. `customer` or `number` alone
    (with no `expired`) is enough to put one row in «scadute», which is what the
    escaping tests need without also claiming a debt."""
    overdue = (
        [
            _invoice(
                customer=customer or "Cliente Prova",
                number=number or "2026/1",
                amount=expired,
                days_of_delay=12,
            )
        ]
        if expired or customer is not None or number is not None
        else []
    )
    hours = (
        WeekHours(
            date_from=_WEEK_START,
            date_to=_WEEK_START + timedelta(days=6),
            days=[
                DayHours(day=_WEEK_START + timedelta(days=i), hours=Decimal("2.00"))
                for i in range(7)
            ],
            days_without_hours=[_WEEK_START + timedelta(days=i) for i in range(days_without_hours)],
            total_hours=total_hours,
        )
        if total_hours is not None
        else None
    )
    return WeeklyDigest(
        week="2026-W37",
        date_from=_WEEK_START,
        date_to=_WEEK_START + timedelta(days=6),
        overdue=overdue,
        in_due_date=[],
        won_to_invoice=won_to_invoice,
        unbilled_hours=unbilled_hours,
        accrued_value=accrued_value,
        issued=[_invoice(customer=customer or "Cliente Prova") for _ in range(issued)],
        total_month_current=Decimal("0"),
        total_month_previous=Decimal("0"),
        paid=[_invoice(customer=customer or "Cliente Prova") for _ in range(paid)],
        hours=hours,
        pipeline=[
            DigestStage(stage_name=f"Fase {i}", number=1, total_value=Decimal("100"))
            for i in range(pipeline)
        ],
        deal_moves=[
            DigestDealMove(deal_id=uuid4(), title=f"Deal {i}", stage_name="Vinto", when=_WEEK_START)
            for i in range(deal_moves)
        ],
        offers_pending=[
            DigestOffer(document_id=uuid4(), title=f"Offerta {i}", days=3) for i in range(offers)
        ],
        signals=[
            DigestSignal(
                code=f"S{i}",
                label=f"Segnale {i}",
                count=1,
                link="/app/dashboard",
            )
            for i in range(signals)
        ],
    )


def test_euro_formats_the_italian_way() -> None:
    assert euro(Decimal("1800")) == "1.800,00 €"
    assert euro(Decimal("0")) == "0,00 €"


def test_day_short_uses_hard_coded_italian_abbreviations() -> None:
    assert day_short(date(2026, 9, 7)) == "lun 7 set"
    assert day_short(date(2026, 1, 1)) == "gio 1 gen"


def test_the_subject_names_the_facts_that_are_not_zero() -> None:
    assert (
        digest_subject(digest_with(issued=2, expired=Decimal("1800")))
        == "La tua settimana: 2 fatture emesse, 1.800,00 € da incassare"
    )
    assert digest_subject(digest_with()) == "La tua settimana in PigroCRM"


def test_the_subject_uses_the_singular_for_one_of_a_kind() -> None:
    assert digest_subject(digest_with(issued=1)) == "La tua settimana: 1 fattura emessa"


def test_only_sections_with_rows_appear_and_every_link_says_from_digest() -> None:
    mail = digest_mail(
        "ada@example.it", digest_with(issued=1), public_url="https://pigro.letsrebase.com/ada"
    )
    assert "Emesse questa settimana" in mail.html and "Da incassare" not in mail.html
    assert "from=digest" in mail.html and "Non inviarmi più il resoconto" in mail.html
    # The switch lives in the profile tab every user can reach (spec §3.6), not the
    # admin-only users panel -- the opt-out link must land somewhere a non-admin
    # recipient can actually open.
    assert "https://pigro.letsrebase.com/ada/app/settings/profile?from=digest" in mail.html
    assert "Emesse questa settimana" in mail.text
    # §3.1 item 3: «Numero, cliente, importo, stato» -- the number is part of the row.
    assert "2026/1" in mail.text and "2026/1" in mail.html


def test_an_issued_row_prints_the_state_that_tells_the_three_apart() -> None:
    """§3.1 item 3: «Numero, cliente, importo, stato (emessa, trasmessa, incassata)».

    `status` alone cannot say it, and printing it was printing the heading twice: the
    section's own predicate is `status = 'issued'`, so every row of it carried that one
    word whatever had happened to the invoice since. The three words come from three
    places -- `payment_status`, `transmitted_externally_at` and `status` -- and this is
    the test that they do.
    """
    issued = digest_mail("a@b.it", digest_with(issued=1), public_url="https://x")
    assert "2026/1 — Cliente Prova — 0,00 € — mar 8 set — emessa" in issued.text
    assert issued.html is not None and "mar 8 set — emessa" in issued.html

    # Deposited with the user's own intermediary and still unpaid.
    transmitted = digest_with(issued=1)
    transmitted.issued[0].transmitted = True
    assert (
        "mar 8 set — trasmessa" in digest_mail("a@b.it", transmitted, public_url="https://x").text
    )

    # Paid. The register's `status` is still «emessa» -- an invoice is not annulled by
    # being collected -- so this word can only come from `payment_status`.
    paid = digest_with(issued=1)
    paid.issued[0].payment_status = "paid"
    assert paid.issued[0].status == "issued"
    assert "mar 8 set — incassata" in digest_mail("a@b.it", paid, public_url="https://x").text
    # And paid wins over transmitted: an invoice that was collected was transmitted too,
    # and «trasmessa» about money already in the bank is the older, smaller fact.
    paid.issued[0].transmitted = True
    assert "mar 8 set — incassata" in digest_mail("a@b.it", paid, public_url="https://x").text

    # Only the issued rows: «Incassate questa settimana» is a list of what arrived, and
    # the state of an invoice that has been paid says nothing a reader of that heading
    # does not already know.
    collected = digest_mail("a@b.it", digest_with(paid=1), public_url="https://x")
    assert "2026/1 — Cliente Prova — 0,00 € — mar 8 set\n" in collected.text


def test_external_values_are_escaped() -> None:
    mail = digest_mail(
        "ada@example.it", digest_with(customer="<b>ACME</b>"), public_url="https://x"
    )
    assert mail.html is not None
    assert "<b>ACME</b>" not in mail.html and "&lt;b&gt;ACME&lt;/b&gt;" in mail.html
    hostile = digest_mail(
        "ada@example.it", digest_with(number="<img src=x onerror=alert(1)>"), public_url="https://x"
    )
    assert hostile.html is not None
    assert "<img src=x" not in hostile.html and "&lt;img src=x" in hostile.html


def test_the_overdue_list_links_to_the_full_list() -> None:
    mail = digest_mail(
        "a@b.it", digest_with(expired=Decimal("100")), public_url="https://pigro.test/ada"
    )
    assert mail.html is not None
    assert "https://pigro.test/ada/app/invoices?overdue=true&from=digest" in mail.text


def test_a_quiet_week_offers_the_assistant() -> None:
    mail = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Settimana ferma" in mail.text


def test_the_hours_section_only_appears_with_hours() -> None:
    still = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Le ore" not in still.html
    worked = digest_mail(
        "a@b.it",
        digest_with(total_hours=Decimal("12.50"), days_without_hours=2),
        public_url="https://x",
    )
    assert "Le ore" in worked.html and "12,50" in worked.text and "5 giorni su 7" in worked.text


def test_every_link_carries_from_digest_including_signals() -> None:
    # `link` is space-relative, the same shape `dashboard/service.py` builds it
    # in; the mail must still resolve it against `public_url`, not hand it out bare.
    mail = digest_mail("a@b.it", digest_with(signals=1), public_url="https://pigro.test/ada")
    assert mail.html is not None
    assert "https://pigro.test/ada/app/dashboard?from=digest" in mail.html


def test_the_pipeline_section_links_to_the_pipeline() -> None:
    mail = digest_mail("a@b.it", digest_with(pipeline=1), public_url="https://pigro.test/ada")
    assert mail.html is not None and "In pipeline" in mail.html
    assert "https://pigro.test/ada/app/deal?from=digest" in mail.html


def test_to_issue_only_appears_with_something_to_bill() -> None:
    still = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Da emettere" not in still.html
    mail = digest_mail("a@b.it", digest_with(won_to_invoice=2), public_url="https://x")
    assert "Da emettere" in mail.html and "2 deal vinti da fatturare" in mail.text
    assert "https://x/app/deal/list?to_invoice=true&from=digest" in mail.text
    # The hours link is `/app/hours` bare: `routes/app/hours.tsx` declares no `validateSearch`,
    # so a `?invoiced=false` would be dropped on arrival and the link would promise a
    # filtered list nobody ever sees. Only `from=digest` survives the trip.
    hours = digest_mail("a@b.it", digest_with(unbilled_hours=Decimal("8")), public_url="https://x")
    assert "8 ore fatturabili non fatturate" in hours.text
    assert "https://x/app/hours?from=digest" in hours.text
    assert "invoiced=false" not in hours.text


def test_a_debt_due_today_says_so_instead_of_counting_zero_days() -> None:
    digest = digest_with(expired=Decimal("100")).model_copy(
        update={"overdue": [_invoice(amount=Decimal("100"), days_of_delay=0)]}
    )
    mail = digest_mail("a@b.it", digest, public_url="https://x")
    assert "scade oggi" in mail.text
    assert "0 giorni di ritardo" not in mail.text
    assert mail.html is not None and "scade oggi" in mail.html


def test_every_call_names_itself_unless_the_caller_already_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloudflare in front of Resend answers `403 error code: 1010` to urllib's default
    signature: without a name of our own every link by mail is refused before reaching
    the API. Found in production on 2026-09-16 (REB-261), with no mail ever delivered
    since the feature shipped; the hub's seam learned the same lesson on 2026-09-10."""
    seen: list[urllib.request.Request] = []

    def fake_open(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        return _Response()

    # `mail.urllib_call` resolves `urllib.request.urlopen` at call time, so patching the
    # module attribute is enough and reaches no private name of the module under test.
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    urllib_call("POST", "https://api.example.test/x", {"Content-Type": "application/json"}, b"{}")
    urllib_call("GET", "https://api.example.test/y", {"User-Agent": "altro/1"}, b"")
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert USER_AGENT.startswith("pigrocrm/")
    assert seen[1].get_header("User-agent") == "altro/1"
    assert seen[0].get_header("Content-type") == "application/json"


def test_a_refused_send_leaves_a_line_behind_with_no_address_and_no_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`send` answering `False` is dropped by the caller's background task, so the log
    is the only trace a mail did not leave. The status is enough to tell a blocked
    signature from a rejected address; neither the recipient nor the key may appear."""
    mail = Mail(to="ada@x.it", subject="s", text="t")
    with caplog.at_level(logging.WARNING):
        assert (
            ResendSender("re_secret", "x", http=lambda *a: (403, b"error code: 1010")).send(mail)
            is False
        )
    assert "403" in caplog.text
    assert "ada@x.it" not in caplog.text
    assert "re_secret" not in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        OSError("down"),
        http.client.IncompleteRead(b"{"),
        # `http.client` quotes the offending header value in this one: here, the bearer key.
        ValueError("Invalid header value b'Bearer re_secret'"),
    ],
    ids=["socket", "unreadable-response", "refused-header"],
)
def test_a_send_that_never_reached_the_provider_leaves_a_line_too(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The other silent path: no status at all, because the call itself failed. Every way
    the call can fail is the same answer for `send` and for `status`, and none of them puts
    the address or the key in the log."""

    def boom(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise failure

    sender = ResendSender("re_secret", "x", http=boom)
    with caplog.at_level(logging.WARNING):
        assert sender.send(Mail("a@x.it", "s", "t")) is False
        assert sender.status("abc") is None
    assert caplog.text != ""
    assert "a@x.it" not in caplog.text
    assert "re_secret" not in caplog.text


def test_a_bug_in_the_call_is_not_taken_for_a_refused_send() -> None:
    """The seam answers for a call that failed, not for a defect in the code around it."""

    def buggy(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise RuntimeError("a defect")

    with pytest.raises(RuntimeError):
        ResendSender("re_x", "x", http=buggy).send(Mail("a@x.it", "s", "t"))


def test_a_refusal_with_a_status_comes_back_as_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """`urlopen` raises `HTTPError` for a 4xx or 5xx; the sender wants the status and the
    body, not the exception."""

    def refuse(request: urllib.request.Request, timeout: float) -> _Response:
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", Message(), io.BytesIO(b"error code: 1010")
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert urllib_call("POST", "https://api.example.test/x", {}, b"{}") == (
        403,
        b"error code: 1010",
    )


# --- what became of a mail the provider took (REB-678) -------------------------------


def test_submit_answers_the_id_the_provider_filed_the_mail_under() -> None:
    http = lambda *a: (200, b'{"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"}')  # noqa: E731
    receipt = ResendSender("re_x", "x", http=http).submit(Mail("a@x.it", "s", "t"))
    assert receipt == Receipt(True, "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794")


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"id": 7}', b'{"id": ""}', b"{}"])
def test_a_body_that_names_no_id_is_still_an_accepted_mail(body: bytes) -> None:
    """A 2xx is the answer to «did it take it»: a body that is not what it was last time
    costs the id, never the send."""
    receipt = ResendSender("re_x", "x", http=lambda *a: (200, body)).submit(
        Mail("a@x.it", "s", "t")
    )
    assert receipt == Receipt(True, None)


def test_a_refused_or_unreachable_submit_is_a_receipt_that_says_so() -> None:
    mail = Mail("a@x.it", "s", "t")
    assert ResendSender("re_x", "x", http=lambda *a: (422, b"{}")).submit(mail) == Receipt(False)

    def boom(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise OSError("down")

    assert ResendSender("re_x", "x", http=boom).submit(mail) == Receipt(False)


@pytest.mark.parametrize(
    ("event", "state"),
    [
        ("queued", "sent"),
        ("scheduled", "sent"),
        ("sent", "sent"),
        ("delivery_delayed", "delayed"),
        ("delivered", "delivered"),
        ("opened", "delivered"),
        ("clicked", "delivered"),
        ("bounced", "bounced"),
        ("suppressed", "suppressed"),
        ("complained", "complained"),
        ("failed", "failed"),
        ("canceled", "failed"),
    ],
)
def test_status_names_what_the_provider_last_recorded(event: str, state: str) -> None:
    """The events are Resend's own, as `GET /emails/{id}` answers `last_event`; the
    `suppressed`, `bounced` and `clicked` ones were read off the production record of the
    invitation that never arrived (2026-10-07)."""
    calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def http(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        calls.append((method, url, headers, body))
        return 200, json.dumps({"id": "abc", "last_event": event}).encode()

    assert ResendSender("re_x", "x", http=http).status("abc") == state
    assert calls == [("GET", f"{RESEND_URL}/abc", calls[0][2], b"")]
    assert calls[0][2]["Authorization"] == "Bearer re_x"


def test_status_quotes_the_id_it_puts_in_the_path() -> None:
    seen: list[str] = []

    def http(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        seen.append(url)
        return 200, b'{"last_event": "delivered"}'

    ResendSender("re_x", "x", http=http).status("../domains?x=1#")
    assert seen == [f"{RESEND_URL}/..%2Fdomains%3Fx%3D1%23"]


@pytest.mark.parametrize(
    "answer",
    [(401, b'{"name": "restricted_api_key"}'), (404, b"{}"), (200, b"{}"), (200, b"nope")],
)
def test_status_is_none_when_the_provider_cannot_say(
    answer: tuple[int, bytes], caplog: pytest.LogCaptureFixture
) -> None:
    """A key that may only send is refused here with a 401, and a body with no
    `last_event` or one nobody mapped says nothing: all of them leave the row as it was,
    and none of them leaks the key or the id into the log."""
    with caplog.at_level(logging.WARNING):
        assert ResendSender("re_secret", "x", http=lambda *a: answer).status("abc") is None
    assert "re_secret" not in caplog.text


def test_status_never_raises_and_says_nothing_for_an_event_nobody_mapped() -> None:
    def boom(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise OSError("down")

    assert ResendSender("re_x", "x", http=boom).status("abc") is None
    novel = lambda *a: (200, b'{"last_event": "a_new_event"}')  # noqa: E731
    assert ResendSender("re_x", "x", http=novel).status("abc") is None


def test_a_get_leaves_with_no_body_and_no_form_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[urllib.request.Request] = []

    def fake_open(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    urllib_call("GET", "https://api.example.test/y", {}, b"")
    assert seen[0].data is None and seen[0].get_method() == "GET"
    assert seen[0].get_header("Content-type") is None


def test_the_recording_sender_can_refuse_and_can_be_asked() -> None:
    mail = Mail(to="a@x.it", subject="s", text="t")
    refusing = RecordingSender(accepts=False)
    assert refusing.submit(mail) == Receipt(False) and refusing.send(mail) is False
    assert refusing.sent == []

    recording = RecordingSender()
    assert recording.submit(mail) == Receipt(True, "recorded-1")
    assert recording.status("recorded-1") == "sent"
    recording.states["recorded-1"] = "bounced"
    assert recording.status("recorded-1") == "bounced"
    assert RecordingSender(readable=False).status("recorded-1") is None
