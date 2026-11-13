"""What leaves the process when a member asks for a link, and what a test sees instead.

The network is the only thing faked, as in `test_conversions.py`: the URL, the header,
the JSON and the failure classification run for real.
"""

import base64
import json
import logging

import pytest

from rebase_core.config import Settings
from rebase_core.mail import (
    RESEND_URL,
    Attachment,
    Mail,
    RecordingSender,
    ResendSender,
    sender_from_settings,
)
from rebase_core.mail_member import CardSummary, magic_link_mail, welcome_mail
from rebase_core.mail_team import talent_cloud_opened_mail

KEY = "re_non_una_chiave_vera"
FROM = "Rebase <ciao@letsrebase.com>"


class FakeHttp:
    def __init__(self, status: int = 200, raises: bool = False) -> None:
        self.status = status
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if self.raises:
            raise OSError("la rete non c'e'")
        return self.status, b'{"id":"x"}'


def test_resend_posts_one_json_object_with_the_bearer_key() -> None:
    http = FakeHttp()
    sender = ResendSender(KEY, FROM, http=http)
    mail = Mail(to="ada@studio.it", subject="Il tuo accesso a rebase", text="ciao")
    assert sender.send(mail) is True
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", RESEND_URL)
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "from": FROM,
        "to": ["ada@studio.it"],
        "subject": "Il tuo accesso a rebase",
        "text": "ciao",
    }


def test_resend_sends_the_html_beside_the_text_when_there_is_one() -> None:
    http = FakeHttp()
    mail = Mail(to="ada@studio.it", subject="x", text="ciao", html="<p>ciao</p>")
    assert ResendSender(KEY, FROM, http=http).send(mail) is True
    body = json.loads(http.calls[0][3])
    assert body["text"] == "ciao" and body["html"] == "<p>ciao</p>"


def test_resend_answers_false_and_never_raises_on_any_failure() -> None:
    mail = Mail(to="ada@studio.it", subject="x", text="y")
    assert ResendSender(KEY, FROM, http=FakeHttp(status=422)).send(mail) is False
    assert ResendSender(KEY, FROM, http=FakeHttp(status=500)).send(mail) is False
    assert ResendSender(KEY, FROM, http=FakeHttp(raises=True)).send(mail) is False


@pytest.mark.parametrize("status", [401, 429, 503])
def test_a_refusal_logs_the_provider_status_and_never_the_address_or_the_key(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    mail = Mail(to="ada@studio.it", subject="x", text="y")
    # Alembic's `fileConfig` (run by any test that built the database first) disables the
    # loggers that already existed; this one is about what the logger says.
    logging.getLogger("rebase_core.mail").disabled = False

    with caplog.at_level("WARNING", logger="rebase_core.mail"):
        assert ResendSender(KEY, FROM, http=FakeHttp(status=status)).send(mail) is False

    assert f"status {status}" in caplog.text
    assert "ada@studio.it" not in caplog.text
    assert KEY not in caplog.text


def test_an_unreachable_provider_is_logged_by_the_exception_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mail = Mail(to="ada@studio.it", subject="x", text="y")
    # Alembic's `fileConfig` (run by any test that built the database first) disables the
    # loggers that already existed; this one is about what the logger says.
    logging.getLogger("rebase_core.mail").disabled = False

    with caplog.at_level("WARNING", logger="rebase_core.mail"):
        ResendSender(KEY, FROM, http=FakeHttp(raises=True)).send(mail)

    assert "OSError" in caplog.text
    assert "la rete" not in caplog.text


def test_whatever_the_transport_raises_the_sender_answers_false() -> None:
    """The protocol says `send` never raises, so an error of a kind nobody listed does not
    escape either."""

    def broken(*args: object) -> tuple[int, bytes]:
        raise ValueError("a bug in the transport")

    mail = Mail(to="ada@studio.it", subject="x", text="y")

    assert ResendSender(KEY, FROM, http=broken).send(mail) is False


def test_the_recording_sender_keeps_what_was_sent() -> None:
    sender = RecordingSender()
    mail = Mail(to="ada@studio.it", subject="x", text="y")
    assert sender.send(mail) is True
    assert sender.sent == [mail]


def test_no_key_means_no_sender() -> None:
    assert sender_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    configured = sender_from_settings(
        Settings(resend_api_key=KEY, _env_file=None)  # type: ignore[call-arg]
    )
    assert isinstance(configured, ResendSender)


def test_the_magic_link_mail_carries_the_link_and_how_long_it_lasts() -> None:
    mail = magic_link_mail("ada@studio.it", "https://letsrebase.com/hub/verify?t=abc", 15)
    assert mail.to == "ada@studio.it"
    assert mail.subject == "Il tuo accesso a rebase"
    assert "https://letsrebase.com/hub/verify?t=abc" in mail.text
    assert "15 minuti" in mail.text
    assert "una volta sola" in mail.text


def test_the_magic_link_mail_has_an_html_version_in_the_landings_system() -> None:
    """The HTML is the landing's box: paper ground, Prussian Blue ink, the four tiles,
    the Watermelon call to action, hard edges. The text version stays the fallback."""
    mail = magic_link_mail("ada@studio.it", "https://letsrebase.com/hub/verify?t=abc", 15)
    assert mail.html is not None
    html = mail.html
    # The link three times: the button's href, and the bare URL as href and as text for
    # the clients that block buttons.
    assert html.count("https://letsrebase.com/hub/verify?t=abc") == 3
    assert "Entra nella tua area" in html
    assert "15 minuti" in html and "una volta sola" in html
    # The brand's five values and nothing rounded.
    for colour in ("#f1f2f3", "#011936", "#465362", "#f9dc5c", "#ed254e", "#e5133e"):
        assert colour in html, colour
    assert "border-radius" not in html
    assert 'name="supported-color-schemes"' in html
    assert "Outfit" in html
    assert "Privacy" in html and "Termini" in html
    # Nothing the person typed is interpolated unescaped: the link is the only variable
    # and it is attribute-safe.
    hostile = magic_link_mail("ada@studio.it", 'https://x.it/?t="><script>', 15)
    assert hostile.html is not None and "<script>" not in hostile.html


def test_the_magic_link_mail_carries_an_optional_note_before_the_link() -> None:
    """REB-272: the extra sentence `apply` asks for shows up once, in both bodies,
    ahead of the link, and never shows up at all when no caller asks for it."""
    plain = magic_link_mail("ada@studio.it", "https://letsrebase.com/hub/verify?t=abc", 15)
    assert "Risulta già" not in plain.text
    assert plain.html is not None and "Risulta già" not in plain.html
    note = (
        "Risulta già una scheda su rebase con questo indirizzo: la trovi e la modifichi "
        "dalla tua area."
    )
    noted = magic_link_mail("ada@studio.it", "https://letsrebase.com/hub/verify?t=abc", 15, note)
    assert noted.text.index(note) < noted.text.index("https://letsrebase.com/hub/verify?t=abc")
    assert noted.html is not None and note in noted.html


# ---- the welcome mail (ORB-157) ---------------------------------------------------------

LOGIN = "https://letsrebase.com/hub/login"
WIZARD = "https://letsrebase.com/hub/freelance"


def _common(mail: Mail) -> None:
    assert mail.subject == "La tua area su rebase è aperta"
    assert "PigroCRM, gratis" in mail.text and "I primi passi da freelance" in mail.text
    assert "disponibili dopo il login" in mail.text
    assert "https://www.linkedin.com/company/letsrebase" in mail.text
    assert "joinorbiters" not in mail.text
    # The dated sentence ("Oggi pomeriggio esce il post...") is a one-off promise that
    # would date itself on every later send; `welcome_mail` has no argument for one.
    assert "Oggi pomeriggio" not in mail.text
    assert mail.html is not None
    assert "https://www.linkedin.com/company/letsrebase" in mail.html
    assert "joinorbiters" not in mail.html
    assert "Oggi pomeriggio" not in mail.html
    assert "Privacy" in mail.html and "border-radius" not in mail.html
    for colour in ("#f1f2f3", "#011936", "#ed254e", "#e5133e"):
        assert colour in mail.html, colour


def test_a_person_who_filled_their_card_is_told_it_is_complete() -> None:
    mail = welcome_mail("ada@studio.it", "Ada", LOGIN, kind="person", job_title="Backend developer")
    _common(mail)
    assert mail.text.startswith("Ciao Ada,")
    assert LOGIN in mail.text and "senza password" in mail.text
    assert "sei Backend developer" in mail.text and "scheda è completa" in mail.text
    assert mail.html is not None and mail.html.count(LOGIN) == 3
    assert "Entra nella tua area" in mail.html


def test_a_person_who_skipped_the_cv_is_asked_for_it_rather_than_congratulated() -> None:
    """The CV is optional in the wizard, so «person» no longer implies a complete card:
    telling somebody their card is complete when it is missing the one thing a company
    searches by would be false, and a reason never to come back and finish it."""
    mail = welcome_mail(
        "ada@studio.it",
        "Ada",
        LOGIN,
        kind="person",
        job_title="Backend developer",
        complete=False,
    )
    _common(mail)
    assert "scheda è completa" not in mail.text
    assert "manca solo il CV" in mail.text and "dalla tua area" in mail.text
    assert "Sei Backend developer" in mail.text


def test_a_card_we_drafted_gets_a_recap_the_ask_to_complete_and_the_offer() -> None:
    summary = CardSummary(
        first_name="Bruna",
        last_name="Esposito",
        job_title="Senior Frontend Developer",
        linkedin_url="https://www.linkedin.com/in/bruna-esposito/",
        links=("https://github.com/brunaesposito",),
    )
    mail = welcome_mail("bruna@studio.it", "Bruna", LOGIN, kind="admin", summary=summary)
    _common(mail)
    assert "quello che si trova in pubblico" in mail.text
    for line in (
        "- Nome: Bruna Esposito",
        "- Posizione: Senior Frontend Developer",
        "- LinkedIn: https://www.linkedin.com/in/bruna-esposito/",
        "- Link: https://github.com/brunaesposito",
    ):
        assert line in mail.text, line
    assert "il CV, la tariffa a giornata" in mail.text and "entra e completala" in mail.text
    assert "già un'offerta in linea con il tuo profilo" in mail.text
    assert "rispondi a questa mail" in mail.text
    # A recap with nothing but the name lists only the name.
    bare = welcome_mail(
        "bruna@studio.it",
        "Bruna",
        LOGIN,
        kind="admin",
        summary=CardSummary("Bruna", "Esposito", None, None, ()),
    )
    assert "- Nome: Bruna Esposito" in bare.text and "- Posizione" not in bare.text


def test_an_address_without_a_card_is_sent_to_the_wizard() -> None:
    mail = welcome_mail("x@studio.it", None, LOGIN, kind="none", wizard_link=WIZARD)
    _common(mail)
    assert mail.text.startswith("Ciao,\n")
    assert "non siamo riusciti a prepararla noi" in mail.text
    assert WIZARD in mail.text and LOGIN in mail.text
    assert mail.html is not None and "Compila il tuo profilo" in mail.html
    named = welcome_mail("x@studio.it", "Marco", LOGIN, kind="none", wizard_link=WIZARD)
    assert named.text.startswith("Ciao Marco,")


def test_the_welcome_mail_escapes_the_persons_words_and_refuses_an_unknown_kind() -> None:
    summary = CardSummary("<Ada>", "L", "<b>x</b>", None, ())
    mail = welcome_mail("ada@studio.it", "<Ada>", LOGIN, kind="admin", summary=summary)
    assert mail.html is not None
    assert "<Ada>" not in mail.html and "&lt;Ada&gt;" in mail.html
    assert "<b>x</b>" not in mail.html and "&lt;b&gt;x&lt;/b&gt;" in mail.html
    with pytest.raises(ValueError):
        welcome_mail("ada@studio.it", "Ada", LOGIN, kind="boh")


def test_resend_sends_each_attachment_as_base64_with_its_name() -> None:
    """REB-387: the signed contracts leave as attachments. Resend takes each file as
    base64 in the JSON body; a mail with none sends no `attachments` key at all."""
    http = FakeHttp()
    attachment = Attachment("lettera-di-incarico-2026-001-firmato.pdf", b"%PDF-1.7 firmato")
    mail = Mail(to="ada@studio.it", subject="x", text="y", attachments=(attachment,))
    assert ResendSender(KEY, FROM, http=http).send(mail) is True
    body = json.loads(http.calls[0][3])
    assert body["attachments"] == [
        {
            "filename": "lettera-di-incarico-2026-001-firmato.pdf",
            "content": base64.b64encode(b"%PDF-1.7 firmato").decode("ascii"),
        }
    ]
    plain = Mail(to="ada@studio.it", subject="x", text="y")
    assert ResendSender(KEY, FROM, http=http).send(plain) is True
    assert "attachments" not in json.loads(http.calls[1][3])


def test_the_talent_cloud_mail_names_the_company_and_links_to_the_cloud() -> None:
    """REB-518, spec § 4.1: the referente learns the cloud is open for their company,
    and the one link is the cloud's page in the member area, where the magic link they
    already know lets them in."""
    url = "https://letsrebase.com/hub/me/cloud"
    mail = talent_cloud_opened_mail(
        "wile@acme.it", first_name="Wile", company="Acme S.r.l.", url=url
    )
    assert mail.to == "wile@acme.it"
    assert mail.subject == "Il talent cloud di rebase è aperto per Acme S.r.l."
    assert mail.text.startswith("Ciao Wile,")
    assert "il talent cloud di rebase è aperto per Acme S.r.l." in mail.text
    assert url in mail.text
    assert "senza password" in mail.text
    assert mail.html is not None
    # The button's href, and the bare URL as href and as text for blocked buttons.
    assert mail.html.count(url) == 3
    assert "Apri il talent cloud" in mail.html
    assert mail.attachments == ()

    nameless = talent_cloud_opened_mail("wile@acme.it", first_name="", company="Acme", url=url)
    assert nameless.text.startswith("Ciao,")
    hostile = talent_cloud_opened_mail(
        "wile@acme.it",
        first_name="<b>Wile</b>",
        company="<i>Acme</i>",
        url='https://x.it/?t="><script>',
    )
    assert hostile.html is not None
    for raw in ("<script>", "<b>Wile", "<i>Acme"):
        assert raw not in hostile.html
