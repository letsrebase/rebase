"""The CRM's outbound mail: a seam, Resend behind it, and the one mail this slice sends."""

import logging
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
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
    RecordingSender,
    ResendSender,
    digest_mail,
    digest_subject,
    euro,
    giorno_breve,
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
    entra = "https://pigro.test/ada/app/verify?t=abc"
    login = "https://pigro.test/ada/app/login"
    member = welcome_mail("ada@x.it", entra, login, membro=True)
    assert member.subject == "Il tuo spazio PigroCRM è pronto"
    assert member.text.startswith("Ciao,")
    assert entra in member.text and login in member.text
    assert (
        "assistente" in member.text
        and "dati fiscali" in member.text
        and "primo cliente" in member.text
    )
    assert "letsrebase.com/hub/freelance" not in member.text
    assert member.html is not None and entra in member.html
    guest = welcome_mail("bob@x.it", entra, login, membro=False)
    assert "letsrebase.com/hub/freelance" in guest.text
    assert guest.html is not None and "hub/freelance" in guest.html
    hostile = welcome_mail(
        "x@x.it", 'https://pigro.test/x/app/verify?t="><script>', login, membro=True
    )
    assert hostile.html is not None and "<script>" not in hostile.html


def test_the_invitation_mail_names_the_inviter_and_the_window() -> None:
    """Spec 2026-09-17 §5: who invited, the space, a click and no password, seven
    days and one use, and the ignore-it reassurance. The greeting carries the
    person's name only when the admin typed one."""
    url = "https://pigro.test/studio-ada/app/invite?t=abc"
    mail = invitation_mail("luca@x.it", "studio-ada", "Ada", url, nome="Luca", giorni=7)
    assert mail.to == "luca@x.it"
    assert mail.subject == "Sei stato invitato in studio-ada su PigroCRM"
    assert mail.text.startswith("Ciao Luca,")
    assert "Ada ti ha invitato" in mail.text and "senza scegliere una password" in mail.text
    assert url in mail.text and "Il link vale 7 giorni e funziona una volta sola." in mail.text
    assert "Se non te lo aspettavi, ignora questa mail" in mail.text
    assert mail.html is not None and "Entra nello spazio" in mail.html
    # No name on the invitation: the greeting stays anonymous, like `welcome_mail`'s.
    quiet = invitation_mail("x@x.it", "s", "Ada", url, nome=None, giorni=7)
    assert quiet.text.startswith("Ciao,\n")
    # A space or an inviter that tried to close the tag is escaped in the HTML.
    hostile = invitation_mail("x@x.it", 's"><script>', 'A"><i>', url, nome=None, giorni=7)
    assert (
        hostile.html is not None and "<script>" not in hostile.html and '"><i>' not in hostile.html
    )


# ---- the weekly digest, as a mail ---------------------------------------------------

_WEEK_START = date(2026, 9, 7)  # a Monday


def _invoice(
    *,
    numero: str = "2026/1",
    cliente: str = "Cliente Prova",
    importo: Decimal = Decimal("0"),
    data: date = date(2026, 9, 8),
    stato: str = "emessa",
    # `StatoPagamento`'s own two values, and this is the one an issued invoice carries
    # until somebody pays it. Not a word of its own: the mail now reads this column to
    # decide the state it prints, so a value the register cannot hold would test nothing.
    stato_pagamento: str = "da_incassare",
    trasmessa: bool = False,
    giorni_di_ritardo: int | None = None,
) -> DigestInvoice:
    return DigestInvoice(
        invoice_id=uuid4(),
        numero=numero,
        cliente=cliente,
        importo=importo,
        data=data,
        stato=stato,
        stato_pagamento=stato_pagamento,
        trasmessa=trasmessa,
        giorni_di_ritardo=giorni_di_ritardo,
    )


def digest_with(
    *,
    emesse: int = 0,
    scaduto: Decimal = Decimal("0"),
    cliente: str | None = None,
    numero: str | None = None,
    incassate: int = 0,
    vinti_da_fatturare: int = 0,
    ore_non_fatturate: Decimal = Decimal("0"),
    valore_maturato: Decimal = Decimal("0"),
    ore_totali: Decimal | None = None,
    giorni_senza_ore: int = 0,
    pipeline: int = 0,
    deal_mossi: int = 0,
    offerte: int = 0,
    segnali: int = 0,
) -> WeeklyDigest:
    """A `WeeklyDigest` with every list empty and every figure zero -- a still week --
    save for whichever section a test asks for by count. `cliente` or `numero` alone
    (with no `scaduto`) is enough to put one row in «scadute», which is what the
    escaping tests need without also claiming a debt."""
    scadute = (
        [
            _invoice(
                cliente=cliente or "Cliente Prova",
                numero=numero or "2026/1",
                importo=scaduto,
                giorni_di_ritardo=12,
            )
        ]
        if scaduto or cliente is not None or numero is not None
        else []
    )
    ore = (
        WeekHours(
            da=_WEEK_START,
            a=_WEEK_START + timedelta(days=6),
            giorni=[
                DayHours(giorno=_WEEK_START + timedelta(days=i), ore=Decimal("2.00"))
                for i in range(7)
            ],
            giorni_senza_ore=[_WEEK_START + timedelta(days=i) for i in range(giorni_senza_ore)],
            ore_totali=ore_totali,
        )
        if ore_totali is not None
        else None
    )
    return WeeklyDigest(
        settimana="2026-W37",
        da=_WEEK_START,
        a=_WEEK_START + timedelta(days=6),
        scadute=scadute,
        in_scadenza=[],
        vinti_da_fatturare=vinti_da_fatturare,
        ore_non_fatturate=ore_non_fatturate,
        valore_maturato=valore_maturato,
        emesse=[_invoice(cliente=cliente or "Cliente Prova") for _ in range(emesse)],
        totale_mese_corrente=Decimal("0"),
        totale_mese_precedente=Decimal("0"),
        incassate=[_invoice(cliente=cliente or "Cliente Prova") for _ in range(incassate)],
        ore=ore,
        pipeline=[
            DigestStage(stage_nome=f"Fase {i}", numero=1, valore_totale=Decimal("100"))
            for i in range(pipeline)
        ],
        deal_mossi=[
            DigestDealMove(
                deal_id=uuid4(), titolo=f"Deal {i}", stage_nome="Vinto", quando=_WEEK_START
            )
            for i in range(deal_mossi)
        ],
        offerte_in_attesa=[
            DigestOffer(document_id=uuid4(), titolo=f"Offerta {i}", giorni=3)
            for i in range(offerte)
        ],
        segnali=[
            DigestSignal(
                codice=f"S{i}",
                etichetta=f"Segnale {i}",
                conteggio=1,
                collegamento="/app/dashboard",
            )
            for i in range(segnali)
        ],
    )


def test_euro_formats_the_italian_way() -> None:
    assert euro(Decimal("1800")) == "1.800,00 €"
    assert euro(Decimal("0")) == "0,00 €"


def test_giorno_breve_uses_hard_coded_italian_abbreviations() -> None:
    assert giorno_breve(date(2026, 9, 7)) == "lun 7 set"
    assert giorno_breve(date(2026, 1, 1)) == "gio 1 gen"


def test_the_subject_names_the_facts_that_are_not_zero() -> None:
    assert (
        digest_subject(digest_with(emesse=2, scaduto=Decimal("1800")))
        == "La tua settimana: 2 fatture emesse, 1.800,00 € da incassare"
    )
    assert digest_subject(digest_with()) == "La tua settimana in PigroCRM"


def test_the_subject_uses_the_singular_for_one_of_a_kind() -> None:
    assert digest_subject(digest_with(emesse=1)) == "La tua settimana: 1 fattura emessa"


def test_only_sections_with_rows_appear_and_every_link_says_da_digest() -> None:
    mail = digest_mail(
        "ada@example.it", digest_with(emesse=1), public_url="https://pigro.letsrebase.com/ada"
    )
    assert "Emesse questa settimana" in mail.html and "Da incassare" not in mail.html
    assert "da=digest" in mail.html and "Non inviarmi più il resoconto" in mail.html
    # The switch lives in the profile tab every user can reach (spec §3.6), not the
    # admin-only users panel -- the opt-out link must land somewhere a non-admin
    # recipient can actually open.
    assert "https://pigro.letsrebase.com/ada/app/settings/profile?da=digest" in mail.html
    assert "Emesse questa settimana" in mail.text
    # §3.1 item 3: «Numero, cliente, importo, stato» -- the number is part of the row.
    assert "2026/1" in mail.text and "2026/1" in mail.html


def test_an_issued_row_prints_the_state_that_tells_the_three_apart() -> None:
    """§3.1 item 3: «Numero, cliente, importo, stato (emessa, trasmessa, incassata)».

    `stato` alone cannot say it, and printing it was printing the heading twice: the
    section's own predicate is `stato = 'emessa'`, so every row of it carried that one
    word whatever had happened to the invoice since. The three words come from three
    places -- `stato_pagamento`, `trasmessa_esternamente_il` and `stato` -- and this is
    the test that they do.
    """
    emessa = digest_mail("a@b.it", digest_with(emesse=1), public_url="https://x")
    assert "2026/1 — Cliente Prova — 0,00 € — mar 8 set — emessa" in emessa.text
    assert emessa.html is not None and "mar 8 set — emessa" in emessa.html

    # Deposited with the user's own intermediary and still unpaid.
    trasmessa = digest_with(emesse=1)
    trasmessa.emesse[0].trasmessa = True
    assert "mar 8 set — trasmessa" in digest_mail("a@b.it", trasmessa, public_url="https://x").text

    # Paid. The register's `stato` is still «emessa» -- an invoice is not annulled by
    # being collected -- so this word can only come from `stato_pagamento`.
    incassata = digest_with(emesse=1)
    incassata.emesse[0].stato_pagamento = "incassato"
    assert incassata.emesse[0].stato == "emessa"
    assert "mar 8 set — incassata" in digest_mail("a@b.it", incassata, public_url="https://x").text
    # And paid wins over transmitted: an invoice that was collected was transmitted too,
    # and «trasmessa» about money already in the bank is the older, smaller fact.
    incassata.emesse[0].trasmessa = True
    assert "mar 8 set — incassata" in digest_mail("a@b.it", incassata, public_url="https://x").text

    # Only the issued rows: «Incassate questa settimana» is a list of what arrived, and
    # the state of an invoice that has been paid says nothing a reader of that heading
    # does not already know.
    incassate = digest_mail("a@b.it", digest_with(incassate=1), public_url="https://x")
    assert "2026/1 — Cliente Prova — 0,00 € — mar 8 set\n" in incassate.text


def test_external_values_are_escaped() -> None:
    mail = digest_mail("ada@example.it", digest_with(cliente="<b>ACME</b>"), public_url="https://x")
    assert mail.html is not None
    assert "<b>ACME</b>" not in mail.html and "&lt;b&gt;ACME&lt;/b&gt;" in mail.html
    hostile = digest_mail(
        "ada@example.it", digest_with(numero="<img src=x onerror=alert(1)>"), public_url="https://x"
    )
    assert hostile.html is not None
    assert "<img src=x" not in hostile.html and "&lt;img src=x" in hostile.html


def test_the_overdue_list_links_to_the_full_list() -> None:
    mail = digest_mail(
        "a@b.it", digest_with(scaduto=Decimal("100")), public_url="https://pigro.test/ada"
    )
    assert mail.html is not None
    assert "https://pigro.test/ada/app/invoices?scadute=true&da=digest" in mail.text


def test_a_quiet_week_offers_the_assistant() -> None:
    mail = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Settimana ferma" in mail.text


def test_the_hours_section_only_appears_with_hours() -> None:
    still = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Le ore" not in still.html
    worked = digest_mail(
        "a@b.it",
        digest_with(ore_totali=Decimal("12.50"), giorni_senza_ore=2),
        public_url="https://x",
    )
    assert "Le ore" in worked.html and "12,50" in worked.text and "5 giorni su 7" in worked.text


def test_every_link_carries_da_digest_including_signals() -> None:
    # `collegamento` is space-relative, the same shape `dashboard/service.py` builds it
    # in; the mail must still resolve it against `public_url`, not hand it out bare.
    mail = digest_mail("a@b.it", digest_with(segnali=1), public_url="https://pigro.test/ada")
    assert mail.html is not None
    assert "https://pigro.test/ada/app/dashboard?da=digest" in mail.html


def test_the_pipeline_section_links_to_the_pipeline() -> None:
    mail = digest_mail("a@b.it", digest_with(pipeline=1), public_url="https://pigro.test/ada")
    assert mail.html is not None and "In pipeline" in mail.html
    assert "https://pigro.test/ada/app/deal?da=digest" in mail.html


def test_da_emettere_only_appears_with_something_to_bill() -> None:
    still = digest_mail("a@b.it", digest_with(), public_url="https://x")
    assert "Da emettere" not in still.html
    mail = digest_mail("a@b.it", digest_with(vinti_da_fatturare=2), public_url="https://x")
    assert "Da emettere" in mail.html and "2 deal vinti da fatturare" in mail.text
    assert "https://x/app/deal/list?da_fatturare=true&da=digest" in mail.text
    # The hours link is `/app/hours` bare: `routes/app/hours.tsx` declares no `validateSearch`,
    # so a `?fatturato=false` would be dropped on arrival and the link would promise a
    # filtered list nobody ever sees. Only `da=digest` survives the trip.
    ore = digest_mail("a@b.it", digest_with(ore_non_fatturate=Decimal("8")), public_url="https://x")
    assert "8 ore fatturabili non fatturate" in ore.text
    assert "https://x/app/hours?da=digest" in ore.text
    assert "fatturato=false" not in ore.text


def test_a_debt_due_today_says_so_instead_of_counting_zero_days() -> None:
    digest = digest_with(scaduto=Decimal("100")).model_copy(
        update={"scadute": [_invoice(importo=Decimal("100"), giorni_di_ritardo=0)]}
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


def test_a_send_that_never_reached_the_provider_leaves_a_line_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other silent path: no status at all, because the call itself failed."""

    def boom(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        raise OSError("down")

    with caplog.at_level(logging.WARNING):
        assert ResendSender("re_secret", "x", http=boom).send(Mail("a@x.it", "s", "t")) is False
    assert caplog.text != ""
    assert "a@x.it" not in caplog.text
    assert "re_secret" not in caplog.text
