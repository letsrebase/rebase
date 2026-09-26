"""Outbound mail: a seam, one provider, and the two mails the hub sends today.

The seam exists so tests never send and production never guesses: `sender_from_settings`
answers `None` without a key, and the API turns that into a 503 sentence rather than a
mail that does not arrive. Like `conversions.py`, nothing here raises past `send` and
nothing logs an address or the key.

The HTML version is the landing's visual system (`projects/website/src/landing.css`)
translated into what mail clients render: tables, inline styles, the five brand values
written out because a mail has no stylesheet to read them from, and none of the things
Gmail and Outlook drop (gradients, box shadows, web fonts). The plain text stays beside
it as the fallback and as what every test reads.
"""

import base64
import html as html_escape
import json
from dataclasses import dataclass
from typing import Protocol

from rebase_core.config import Settings
from rebase_core.http import HttpCall, urllib_call

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class Attachment:
    """A file a mail carries: the name the reader saves it under, and its bytes."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class Mail:
    """The text is required and is what arrives everywhere; the HTML, when there is one,
    is the same words in the landing's box for the clients that render it. Since REB-387
    the signed contracts leave as attachments; every other mail carries none."""

    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()


class EmailSender(Protocol):
    def send(self, mail: Mail) -> bool:
        """`True` when the provider accepted it. Never raises."""
        ...


class RecordingSender:
    """Keeps every mail in a list. For tests, and for reading the link in development."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return True


class ResendSender:
    """Resend's `POST /emails`: one JSON object, the key as a bearer token."""

    def __init__(self, api_key: str, sender: str, http: HttpCall | None = None) -> None:
        self.api_key = api_key
        self.sender = sender
        self.http = http or urllib_call

    def send(self, mail: Mail) -> bool:
        body: dict[str, object] = {
            "from": self.sender,
            "to": [mail.to],
            "subject": mail.subject,
            "text": mail.text,
        }
        if mail.html is not None:
            body["html"] = mail.html
        if mail.attachments:
            # Resend takes each file as base64 inside the JSON body, beside its name.
            body["attachments"] = [
                {
                    "filename": item.filename,
                    "content": base64.b64encode(item.content).decode("ascii"),
                }
                for item in mail.attachments
            ]
        payload = json.dumps(body).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            status, _ = self.http("POST", RESEND_URL, headers, payload)
        except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
            return False
        return 200 <= status < 300


def sender_from_settings(settings: Settings) -> EmailSender | None:
    if not settings.resend_api_key:
        return None
    return ResendSender(settings.resend_api_key, settings.mail_from)


# ---- the landing's box, as a mail --------------------------------------------------------
#
# `shared/brand/palette.css` is the source of these six values; a mail cannot read a
# stylesheet, so they are written out here and `test_mail.py` pins them.
PAPER = "#f1f2f3"
INK = "#011936"
INK_QUIET = "#465362"
ROYAL_GOLD = "#f9dc5c"
WATERMELON = "#ed254e"
# White on raw Watermelon fails the body-text contrast floor; the landing's `.cta` uses
# this darker step for the same reason (`--color-watermelon-strong`).
CTA = "#e5133e"
FONT = "Outfit, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif"
# The landing's stepped shadow: the border colour, moved 8px right and down. A mail
# client draws no box-shadow, so the step is a cell of ink behind the card.
STEP = 8
# Every layout table in a mail is this: no spacing, no borders of its own, invisible to
# a screen reader.
TABLE = 'role="presentation" cellpadding="0" cellspacing="0" border="0"'
SITE = "https://letsrebase.com"


def _quiet_link(href: str, label: str) -> str:
    return f'<a href="{href}" style="color:{INK_QUIET};text-decoration:underline;">{label}</a>'


def _tile(colour: str) -> str:
    size = "width:10px;height:10px;line-height:0;font-size:0;"
    return f'<td width="10" height="10" bgcolor="{colour}" style="{size}">&nbsp;</td>'


def _mark() -> str:
    """The four tiles, in `shared/brand/mark.ts` order: ink, royal gold, watermelon, ink."""
    return (
        f'<table {TABLE} style="border-collapse:collapse;">'
        f"<tr>{_tile(INK)}{_tile(ROYAL_GOLD)}</tr>"
        f"<tr>{_tile(WATERMELON)}{_tile(INK)}</tr>"
        "</table>"
    )


def _button(href: str, label: str) -> str:
    """The landing's `.cta`: a filled rectangle, hard edges, white text at weight 500."""
    text = f"font-family:{FONT};font-size:17px;font-weight:500;color:#ffffff;"
    # The padding sits on the cell as well as on the anchor: Outlook's engine ignores
    # `display:inline-block` on a link and would shrink the box to the text.
    cell = (
        f'bgcolor="{CTA}" style="background-color:{CTA};border:2px solid {CTA};padding:14px 24px;"'
    )
    return (
        f'<table {TABLE} style="border-collapse:collapse;">'
        f"<tr><td {cell}>"
        f'<a href="{href}" style="display:inline-block;{text}text-decoration:none;">{label}</a>'
        "</td></tr></table>"
    )


def _frame(title: str, body: str) -> str:
    """The landing's card around `body` (already HTML): paper ground, the mark and the
    name, a white box with a 2px ink border and the 8px step, the fine footer."""
    ground = f'bgcolor="{PAPER}" style="background-color:{PAPER};"'
    name = f"font-family:{FONT};font-size:18px;font-weight:500;color:{INK};"
    step = f'bgcolor="{INK}" style="background-color:{INK};padding:0 {STEP}px {STEP}px 0;"'
    card = f'bgcolor="#ffffff" style="background-color:#ffffff;border:2px solid {INK};"'
    copy = f"font-family:{FONT};font-size:17px;font-weight:400;line-height:1.6;color:{INK};"
    fine = f"font-family:{FONT};font-size:13px;line-height:1.6;color:{INK_QUIET};"
    gap = "&nbsp;&nbsp;&nbsp;"
    footer = gap.join(
        (
            _quiet_link(f"{SITE}/", "rebase"),
            _quiet_link(f"{SITE}/privacy", "Privacy"),
            _quiet_link(f"{SITE}/terms", "Termini"),
        )
    )
    return "\n".join(
        (
            "<!DOCTYPE html>",
            '<html lang="it">',
            '<head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            # Both, or Apple Mail and Outlook.com invert the brand colours in dark mode.
            '<meta name="color-scheme" content="light">',
            '<meta name="supported-color-schemes" content="light">',
            f"<title>{html_escape.escape(title)}</title></head>",
            f'<body style="margin:0;padding:0;background-color:{PAPER};">',
            f'<table {TABLE} width="100%" {ground}>',
            '<tr><td align="center" style="padding:32px 16px 40px 16px;">',
            f'<table {TABLE} width="560" style="width:560px;max-width:100%;">',
            '<tr><td style="padding:0 0 20px 0;">',
            f"<table {TABLE}><tr>",
            f'<td valign="middle" style="padding-right:10px;">{_mark()}</td>',
            f'<td valign="middle" style="{name}">rebase</td>',
            "</tr></table>",
            "</td></tr>",
            f"<tr><td {step}>",
            f'<table {TABLE} width="100%" {card}>',
            f'<tr><td style="padding:36px 36px 32px 36px;{copy}">',
            body,
            "</td></tr></table>",
            "</td></tr>",
            f'<tr><td style="padding:24px 0 0 0;{fine}">{footer}</td></tr>',
            "</table>",
            "</td></tr></table>",
            "</body></html>",
        )
    )


def magic_link_mail(to: str, link: str, minutes: int, note: str | None = None) -> Mail:
    """The one mail the hub sends: the link, how long it lasts, and that ignoring it is
    fine. In the voice of `docs/design/positioning.md`, as text and as the landing's box.
    `note`, when given, is one more sentence before the link (REB-272): the wizard sends
    this same mail, with this note, to someone who applied again with a card already on
    file, instead of writing over it."""
    note_line = f"{note}\n\n" if note else ""
    text = (
        "Ciao,\n"
        "\n"
        f"{note_line}"
        "questo è il link per entrare nella tua area su rebase:\n"
        "\n"
        f"{link}\n"
        "\n"
        f"Vale {minutes} minuti e funziona una volta sola. Se non l'hai chiesto tu, ignora "
        "questa mail: non succede niente.\n"
        "\n"
        "Noi di rebase\n"
    )
    # The link is the only variable and it goes into an attribute and into text: escaped
    # both times, so a token that is not ours cannot close the tag it sits in.
    safe_link = html_escape.escape(link, quote=True)
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    rows = ['<p style="margin:0 0 20px 0;">Ciao,</p>']
    if note:
        rows.append(f'<p style="margin:0 0 20px 0;">{html_escape.escape(note)}</p>')
    rows.extend(
        (
            '<p style="margin:0 0 24px 0;">'
            "questo è il link per entrare nella tua area su rebase.</p>",
            _button(safe_link, "Entra nella tua area"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{_quiet_link(safe_link, safe_link)}</p>",
            f"<p {paragraph}>Vale {minutes} minuti e funziona una volta sola. "
            "Se non l'hai chiesto tu, ignora questa mail: non succede niente.</p>",
            f"<p {paragraph}>Noi di rebase</p>",
        )
    )
    body = "\n".join(rows)
    return Mail(
        to=to,
        subject="Il tuo accesso a rebase",
        text=text,
        html=_frame("Il tuo accesso a rebase", body),
    )


LINKEDIN_PAGE = "https://www.linkedin.com/company/letsrebase"
PIGROCRM_LINE = (
    "PigroCRM, gratis: preventivo, contratto, fattura, ore, con i dati fiscali già giusti."
)
GUIDE_LINE = (
    "La guida «I primi passi da freelance»: venti minuti sulla parte che nessuno spiega "
    "prima della prima fattura."
)


@dataclass(frozen=True)
class CardSummary:
    """What the welcome mail says about a card we wrote from public sources (ORB-157):
    the person's own words as we found them, so they can see what a company sees."""

    nome: str
    cognome: str
    posizione: str | None
    linkedin_url: str | None
    links: tuple[str, ...]


def welcome_mail(
    to: str,
    nome: str | None,
    accedi_link: str,
    *,
    kind: str,
    posizione: str | None = None,
    completa: bool = True,
    summary: CardSummary | None = None,
    wizard_link: str | None = None,
) -> Mail:
    """The one-off mail that tells a person their area is open (ORB-157), in three
    voices decided with Ivan on 2026-09-11:

    - `kind == "persona"`: they filled the card themselves; it is complete, they enter.
    - `kind == "admin"`: we wrote the card from public sources. A short recap of what we
      found, the ask to enter and add what nothing public states (CV, rate, remote
      option), and that an offer in line with the profile is already there: reply to
      talk about it.
    - `kind == "nessuna"`: nothing public was enough for a card. The wizard, five minutes,
      and from then on the address is the way in.

    Same box and voice as the magic link; the person's words are escaped in the HTML."""
    e = html_escape.escape
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    li = 'style="margin:0 0 8px 0;"'
    perks_text = (
        "Dentro trovi i vantaggi della community, disponibili dopo il login:\n"
        f"- {PIGROCRM_LINE}\n"
        f"- {GUIDE_LINE}\n"
    )
    perks_html = (
        f"<p {paragraph}>Dentro trovi i vantaggi della community, disponibili dopo il "
        "login:</p>"
        '<ul style="margin:8px 0 0 0;padding:0 0 0 22px;">'
        f"<li {li}>{e(PIGROCRM_LINE)}</li><li>{e(GUIDE_LINE)}</li></ul>"
    )
    linkedin_text = f"Un'altra cosa: segui la pagina LinkedIn di rebase, {LINKEDIN_PAGE}.\n"
    linkedin_html = (
        f"<p {paragraph}>Un'altra cosa: segui "
        f"{_quiet_link(LINKEDIN_PAGE, 'la pagina LinkedIn di rebase')}.</p>"
    )
    safe_accedi = e(accedi_link, quote=True)
    enter_text = (
        "la tua area su rebase è aperta. Si entra con la tua email, senza password: ti "
        "mandiamo un link e sei dentro.\n"
        "\n"
        f"{accedi_link}\n"
    )
    enter_html = (
        '<p style="margin:0 0 24px 0;">la tua area su rebase è aperta. Si entra con la '
        "tua email, senza password: ti mandiamo un link e sei dentro.</p>"
        + _button(safe_accedi, "Entra nella tua area")
        + f'<p {small}word-break:break-all;">'
        "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
        f"{_quiet_link(safe_accedi, safe_accedi)}</p>"
    )

    if kind == "persona":
        # «Completa» is a fact about the card, not about who wrote it: since the wizard
        # stopped demanding a CV a person can have filled the form themselves and still
        # be missing the file, and telling them the card is complete would be both
        # false and a reason never to come back and finish it.
        if not completa:
            card_text = (
                f"Sei {posizione}: manca solo il CV perché le aziende possano trovarti, "
                "e lo carichi dalla tua area."
                if posizione
                else "Manca solo il CV perché le aziende possano trovarti, e lo carichi "
                "dalla tua area."
            )
        else:
            card_text = (
                f"La tua scheda è completa: sei {posizione}, e le aziende possono trovarti."
                if posizione
                else "La tua scheda è completa: le aziende possono trovarti."
            )
        middle_text = enter_text + "\n" + card_text + "\n"
        middle_html = enter_html + f"<p {paragraph}>{e(card_text)}</p>"
    elif kind == "admin":
        assert summary is not None
        found: list[tuple[str, str]] = [("Nome", f"{summary.nome} {summary.cognome}")]
        if summary.posizione:
            found.append(("Posizione", summary.posizione))
        if summary.linkedin_url:
            found.append(("LinkedIn", summary.linkedin_url))
        if summary.links:
            found.append(("Link", ", ".join(summary.links)))
        intro = (
            "Abbiamo preparato la tua scheda con quello che si trova in pubblico su di te. "
            "Ecco cosa c'è:"
        )
        missing = (
            "Manca la tua parte: il CV, la tariffa a giornata, come preferisci lavorare. "
            "Sono le cose che le aziende cercano: entra e completala."
        )
        offer = (
            "Una cosa in più: abbiamo già un'offerta in linea con il tuo profilo. Se vuoi "
            "approfondire, rispondi a questa mail e ne parliamo."
        )
        middle_text = (
            enter_text
            + "\n"
            + intro
            + "\n"
            + "".join(f"- {label}: {value}\n" for label, value in found)
            + "\n"
            + missing
            + "\n"
            + "\n"
            + offer
            + "\n"
        )
        middle_html = (
            enter_html
            + f"<p {paragraph}>{e(intro)}</p>"
            + '<ul style="margin:8px 0 0 0;padding:0 0 0 22px;">'
            + "".join(
                f"<li {li}><strong>{e(label)}:</strong> {e(value)}</li>" for label, value in found
            )
            + "</ul>"
            + f"<p {paragraph}>{e(missing)}</p>"
            + f"<p {paragraph}>{e(offer)}</p>"
        )
    elif kind == "nessuna":
        assert wizard_link is not None
        safe_wizard = e(wizard_link, quote=True)
        intro = (
            "la tua area su rebase è aperta, ma la tua scheda non siamo riusciti a "
            "prepararla noi: in pubblico non c'era abbastanza. Compilala tu, ci vogliono "
            "cinque minuti."
        )
        after = f"Da quel momento entri con la tua email, senza password, da qui: {accedi_link}"
        middle_text = intro + "\n\n" + wizard_link + "\n\n" + after + "\n"
        middle_html = (
            f'<p style="margin:0 0 24px 0;">{e(intro)}</p>'
            + _button(safe_wizard, "Compila il tuo profilo")
            + f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{_quiet_link(safe_wizard, safe_wizard)}</p>"
            + f"<p {paragraph}>Da quel momento entri con la tua email, senza password, da "
            f"{_quiet_link(safe_accedi, safe_accedi)}.</p>"
        )
    else:
        raise ValueError(f"kind sconosciuto: {kind}")

    text = (
        f"{greeting}\n\n" + middle_text + "\n" + perks_text + "\n" + linkedin_text + "\n"
        "Noi di rebase\n"
    )
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            middle_html,
            perks_html,
            linkedin_html,
            f"<p {paragraph}>Noi di rebase</p>",
        )
    )
    return Mail(
        to=to,
        subject="La tua area su rebase è aperta",
        text=text,
        html=_frame("La tua area su rebase è aperta", body),
    )


# ---- the contracts (REB-387) --------------------------------------------------------------
#
# The hub sends the signing mails itself: Documenso distributes with
# `distributionMethod: NONE` and every mail of its own switched off, so these are the only
# mails a freelancer gets about a contract, in the same box as the magic link.


def document_name(kind: str, numero: str | None) -> str:
    """How a mail names a contract: «contratto quadro rebase», «lettera di incarico n.
    2026-001». `kind` is `quadro` or `lettera`."""
    if kind == "quadro":
        return "contratto quadro rebase"
    return f"lettera di incarico n. {numero}"


def _article(kind: str) -> str:
    return "il" if kind == "quadro" else "la"


def signing_request_mail(
    to: str, nome: str, kind: str, numero: str | None, signing_url: str
) -> Mail:
    """One mail per document to sign (spec § 6): the subject names it, one paragraph says
    what it is, one button opens the signing site. The link is the only way the
    document reaches the person, and it goes into an attribute and into text: escaped
    both times, like the magic link."""
    e = html_escape.escape
    name = document_name(kind, numero)
    what = (
        "il contratto quadro con rebase: le regole di ogni lavoro che fai tramite noi. "
        "Si firma una volta e si rinnova da solo ogni dodici mesi"
        if kind == "quadro"
        else f"la {name}: il lavoro, le date e il compenso che abbiamo concordato"
    )
    paragraph = (
        f"ti mandiamo da firmare {what}. Si firma online, senza creare un account, dal "
        "bottone qui sotto; la copia firmata ti arriva per email e resta nella tua area "
        "su rebase."
    )
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    subject = f"Da firmare: {name}"
    text = f"{greeting}\n\n{paragraph}\n\n{signing_url}\n\nNoi di rebase\n"
    safe_url = e(signing_url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(paragraph)}</p>',
            _button(safe_url, "Firma il documento"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{_quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))


def signing_cancelled_mail(to: str, nome: str, kind: str, numero: str | None) -> Mail:
    """A document that had already left for signature, cancelled by rebase before it was
    signed (REB-407): the same frame as `signing_request_mail`, with no button, since
    there is nothing left to sign. The link the earlier mail carried is dead from now
    on, and a new document, if one is needed, arrives on its own."""
    e = html_escape.escape
    name = document_name(kind, numero)
    cancelled = "annullato" if kind == "quadro" else "annullata"
    nuovo = "un nuovo contratto quadro" if kind == "quadro" else "una nuova lettera di incarico"
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    paragraph = (
        f"il link che ti avevamo mandato per firmare {_article(kind)} {name} non funziona "
        f"più: lo abbiamo annullato noi di rebase. Se serve {nuovo}, ti scriviamo."
    )
    subject = f"{name[0].upper()}{name[1:]} {cancelled}"
    text = f"{greeting}\n\n{paragraph}\n\nNoi di rebase\n"
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0;">{e(paragraph)}</p>',
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))


def signed_copy_mail(
    to: str,
    *,
    kind: str,
    numero: str | None,
    attachment: Attachment,
    nome: str,
    cognome: str,
    for_rebase: bool = False,
) -> Mail:
    """The sealed copy, attached (spec § 6): to the freelancer, and with `for_rebase` to
    rebase's contracts address. Its last page is Documenso's certificate of the
    signature, in English (probe § 7): the mail says so, so nobody takes it for a stray
    page."""
    e = html_escape.escape
    name = document_name(kind, numero)
    certificate = "L'ultima pagina, in inglese, è il certificato della firma elettronica."
    signed = "Firmato" if kind == "quadro" else "Firmata"
    if for_rebase:
        greeting = "Ciao,"
        paragraph = (
            f"{nome} {cognome} ha firmato {_article(kind)} {name}: la copia firmata è in "
            f"allegato. {certificate}"
        )
        subject = f"{signed} da {nome} {cognome}: {name}"
    else:
        greeting = f"Ciao {nome}," if nome else "Ciao,"
        paragraph = (
            f"hai firmato {_article(kind)} {name}: la copia firmata è in allegato, e la "
            f"trovi anche nella tua area su rebase. {certificate}"
        )
        subject = f"{signed}: {name}"
    text = f"{greeting}\n\n{paragraph}\n\nNoi di rebase\n"
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0;">{e(paragraph)}</p>',
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(
        to=to,
        subject=subject,
        text=text,
        html=_frame(subject, body),
        attachments=(attachment,),
    )


def team_request_mail(
    to: str, *, azienda: str, riassunto: str | None, talento: str | None, url: str
) -> Mail:
    """A team request arriving (spec § 3.5), to rebase's own address and never to the
    company: the subject names the company, the body carries the project's anonymous
    summary, or for a request of one talent from the cloud that talent's name, and the
    link to the request's page, where the contacts and the team are. The company's name
    and the summary were typed by a visitor, so both are escaped in the HTML, the way
    the magic link escapes its token."""
    e = html_escape.escape
    subject = f"Nuova richiesta team da {azienda}"
    if talento is not None:
        what = f"{azienda} ha chiesto un talento del talent cloud: {talento}."
        quoted = None
    else:
        what = f"{azienda} ha chiesto il team che il team builder ha proposto per questo progetto:"
        quoted = riassunto
    where = "La richiesta, con i contatti e i talenti, è qui:"
    text_parts = ["Ciao,", what]
    if quoted:
        text_parts.append(quoted)
    text_parts.extend((f"{where}\n{url}", "Noi di rebase"))
    text = "\n\n".join(text_parts) + "\n"
    safe_url = e(url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    rows = [
        '<p style="margin:0 0 20px 0;">Ciao,</p>',
        f'<p style="margin:0 0 20px 0;">{e(what)}</p>',
    ]
    if quoted:
        rows.append(
            f'<p style="margin:0 0 24px 0;padding:0 0 0 16px;border-left:2px solid {INK};">'
            f"{e(quoted)}</p>"
        )
    rows.extend(
        (
            _button(safe_url, "Apri la richiesta"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{_quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, "\n".join(rows)))
