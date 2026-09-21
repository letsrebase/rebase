"""Outbound mail: a seam, one provider, and the mails PigroCRM sends (spec 2026-09-12 §6.1).

The seam exists so tests never send and production never guesses: `sender_from_settings`
answers `None` without a key, and the API turns that into a 503 sentence rather than a
mail that does not arrive. Nothing here raises past `send`; a refusal leaves a warning
carrying the status and nothing else, because the caller drops what `send` answers and
the log is the only trace a mail did not leave. No address and no key are ever logged.

The HTML is the same box the hub's mails use (tables, inline styles, the brand values
written out), with PigroCRM's name and the site's legal pages.

Same shape as `rebase_core/mail.py` on purpose, and not an import of it: the two
products do not import each other (root `AGENTS.md`), and eighty lines are cheaper than
a dependency between two release trains.
"""

import html as html_escape
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from pigrocrm.core.config import Settings
from pigrocrm.core.digest.schemas import (
    DigestInvoice,
    WeeklyDigest,
)
from pigrocrm.core.money import round_money

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
# Resend sits behind Cloudflare, which answers `403 error code: 1010` to urllib's
# default `Python-urllib/3.x` signature and never reaches the API behind it. Every
# mail this module sent was refused there between the feature shipping and REB-261
# (2026-09-16), silently, because a refusal is only a `False` nobody reads. The same
# name `pigrocrm.core.tenants.hub` already uses, and the same lesson `rebase_core.http`
# learned on 2026-09-10.
USER_AGENT = "pigrocrm/0.1 (+https://pigro.letsrebase.com)"

HttpCall = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


def urllib_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """The one HTTP call this module makes, as a function so a test can replace it.

    The request names itself, unless the caller already named it."""
    sent = {"User-Agent": USER_AGENT, **headers}
    request = urllib.request.Request(url, data=body, method=method, headers=sent)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), bytes(exc.read())


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(Protocol):
    def send(self, mail: Mail) -> bool:
        """`True` when the provider accepted it. Never raises."""
        ...


class RecordingSender:
    """Keeps every mail in a list. For tests, and for reading a link in development."""

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
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            status, _ = self.http("POST", RESEND_URL, headers, payload)
        except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
            # The caller hands `send` to a background task and drops what it answers,
            # so this line is the only trace the mail did not leave. The status, and
            # nothing else: neither the address nor the key may reach a log.
            logger.warning("mail non inviata: la chiamata al provider non ha risposto")
            return False
        if not 200 <= status < 300:
            logger.warning("mail non inviata: il provider ha risposto %s", status)
            return False
        return True


def sender_from_settings(settings: Settings) -> EmailSender | None:
    if not settings.resend_api_key:
        return None
    return ResendSender(settings.resend_api_key, settings.mail_from)


# ---- the box, as a mail ------------------------------------------------------------
# `shared/brand/palette.css` is the source of these values; a mail cannot read a
# stylesheet, so they are written out here.
PAPER = "#f1f2f3"
INK = "#011936"
INK_QUIET = "#465362"
ROYAL_GOLD = "#f9dc5c"
WATERMELON = "#ed254e"
# White on raw Watermelon fails the body-text contrast floor; the landing's `.cta` uses
# this darker step for the same reason.
CTA = "#e5133e"
FONT = "Outfit, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif"
STEP = 8
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
    """A filled rectangle, hard edges, white text at weight 500. The padding sits on the
    cell as well as on the anchor: Outlook ignores `display:inline-block` on a link."""
    text = f"font-family:{FONT};font-size:17px;font-weight:500;color:#ffffff;"
    cell = (
        f'bgcolor="{CTA}" style="background-color:{CTA};border:2px solid {CTA};padding:14px 24px;"'
    )
    return (
        f'<table {TABLE} style="border-collapse:collapse;"><tr><td {cell}>'
        f'<a href="{href}" style="display:inline-block;{text}text-decoration:none;">{label}</a>'
        "</td></tr></table>"
    )


def _frame(title: str, body: str) -> str:
    """The card around `body` (already HTML): paper ground, the mark and the name, a
    white box with a 2px ink border and the 8px step, the fine footer."""
    ground = f'bgcolor="{PAPER}" style="background-color:{PAPER};"'
    name = f"font-family:{FONT};font-size:18px;font-weight:500;color:{INK};"
    step = f'bgcolor="{INK}" style="background-color:{INK};padding:0 {STEP}px {STEP}px 0;"'
    card = f'bgcolor="#ffffff" style="background-color:#ffffff;border:2px solid {INK};"'
    copy = f"font-family:{FONT};font-size:17px;font-weight:400;line-height:1.6;color:{INK};"
    fine = f"font-family:{FONT};font-size:13px;line-height:1.6;color:{INK_QUIET};"
    gap = "&nbsp;&nbsp;&nbsp;"
    footer = gap.join(
        (
            _quiet_link(f"{SITE}/pigrocrm", "PigroCRM"),
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
            f'<td valign="middle" style="{name}">PigroCRM</td>',
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


def magic_link_mail(to: str, links: Sequence[tuple[str, str]], minutes: int) -> Mail:
    """The way in (spec 2026-09-12 §6.2): one link per space the address owns (usually
    one), how long they last, and that ignoring the mail is fine. `links` are
    `(label, url)`; the label is the space's name, shown only when there is more than
    one. The URL goes into an attribute and into text: escaped both times, so a token
    that is not ours cannot close the tag it sits in."""
    several = len(links) > 1
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    intro = (
        "questi sono i link per entrare nei tuoi spazi PigroCRM:"
        if several
        else "questo è il link per entrare nel tuo spazio PigroCRM:"
    )
    text_links = "\n".join((f"{label}: {url}" if several else url) for label, url in links)
    text = (
        "Ciao,\n\n"
        f"{intro}\n\n{text_links}\n\n"
        f"Vale {minutes} minuti e funziona una volta sola. Se non l'hai chiesto tu, ignora "
        "questa mail: non succede niente.\n\n"
        "PigroCRM\n"
    )
    parts: list[str] = []
    for label, url in links:
        safe_url = html_escape.escape(url, quote=True)
        if several:
            parts.append(f"<p {paragraph}><strong>{html_escape.escape(label)}</strong></p>")
        parts.append(_button(safe_url, "Entra nel tuo spazio"))
        parts.append(
            f'<p {small}word-break:break-all;">Se il bottone non si apre, copia questo '
            f"indirizzo nel browser:<br>{_quiet_link(safe_url, safe_url)}</p>"
        )
    body = "\n".join(
        (
            '<p style="margin:0 0 20px 0;">Ciao,</p>',
            f'<p style="margin:0 0 24px 0;">{intro}</p>',
            *parts,
            f"<p {paragraph}>Vale {minutes} minuti e funziona una volta sola. "
            "Se non l'hai chiesto tu, ignora questa mail: non succede niente.</p>",
            f"<p {paragraph}>PigroCRM</p>",
        )
    )
    subject = "Il tuo accesso a PigroCRM"
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))


HUB_WIZARD_URL = "https://letsrebase.com/hub/freelance"


def welcome_mail(to: str, entra_url: str, login_url: str, *, membro: bool) -> Mail:
    """The one-off mail that says a space exists (spec 2026-09-12 §6.6). Its button is a
    link that enters (`entra_url`, a magic link): the click proves the address, opens the
    durable session and closes whatever somebody else may have opened with this email at
    the signup. Then how to come back (the login, the email, a link), the assistant in
    one sentence, the three first steps, and for whoever is not in the community yet a
    paragraph on rebase. No name in the greeting: what the signup asked is the space's
    name, not the person's. Every value from outside is escaped in the HTML."""
    e = html_escape.escape
    greeting = "Ciao,"
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    li = 'style="margin:0 0 8px 0;"'
    steps = (
        "I tuoi dati fiscali, in Impostazioni: finiscono su offerte e fatture.",
        "Il primo cliente: tutto il resto parte da lì.",
        "La prima offerta, dal deal: il template è già pronto.",
    )
    assistant = (
        "Il CRM lavora al posto tuo: dalla Home, «Collega l'assistente» e chiedi a Claude di "
        "registrare le ore, preparare un'offerta, riassumere la settimana."
    )
    orbiters = (
        "PigroCRM è il perk della community rebase, developer e CTO freelance in Italia: "
        "progetti da aziende vere e persone che ci sono già passate. Se vuoi entrarci: "
        f"{HUB_WIZARD_URL}"
    )
    safe_entra = e(entra_url, quote=True)
    text = (
        f"{greeting}\n\n"
        f"il tuo spazio PigroCRM è pronto. Entra da qui (il link vale poco e funziona una "
        f"volta sola):\n\n{entra_url}\n\n"
        "Le altre volte si entra con la tua email, senza password: scrivi l'indirizzo qui e "
        f"ti arriva un link.\n\n{login_url}\n\n"
        f"{assistant}\n\n"
        "Le prime tre cose da fare:\n"
        + "".join(f"- {step}\n" for step in steps)
        + "\n"
        + (f"{orbiters}\n\n" if not membro else "")
        + "PigroCRM\n"
    )
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            '<p style="margin:0 0 24px 0;">il tuo spazio PigroCRM è pronto. Il bottone '
            "vale poco e funziona una volta sola.</p>",
            _button(safe_entra, "Entra nel tuo spazio"),
            f'<p {small}word-break:break-all;">Se il bottone non si apre, copia questo '
            f"indirizzo nel browser:<br>{_quiet_link(safe_entra, e(entra_url))}</p>",
            f"<p {paragraph}>Le altre volte si entra con la tua email, senza password: "
            f"da {_quiet_link(e(login_url, quote=True), e(login_url))} scrivi l'indirizzo e "
            "ti arriva un link.</p>",
            f"<p {paragraph}>{e(assistant)}</p>",
            f"<p {paragraph}>Le prime tre cose da fare:</p>",
            '<ol style="margin:8px 0 0 0;padding:0 0 0 22px;">'
            + "".join(f"<li {li}>{e(step)}</li>" for step in steps)
            + "</ol>",
            (
                f"<p {paragraph}>PigroCRM è il perk della community rebase, developer e CTO "
                "freelance in Italia: progetti da aziende vere e persone che ci sono già "
                f"passate. Se vuoi entrarci: {_quiet_link(HUB_WIZARD_URL, HUB_WIZARD_URL)}</p>"
                if not membro
                else ""
            ),
            f"<p {paragraph}>PigroCRM</p>",
        )
    )
    subject = "Il tuo spazio PigroCRM è pronto"
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))


# ---- the weekly digest, as a mail (spec 2026-09-16 §3.5) ---------------------------

GIORNI_BREVI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
MESI_BREVI = (
    "gen",
    "feb",
    "mar",
    "apr",
    "mag",
    "giu",
    "lug",
    "ago",
    "set",
    "ott",
    "nov",
    "dic",
)


def giorno_breve(day: date) -> str:
    """`lun 8 set`. Hard-coded Italian abbreviations, never `strftime`'s `%a`/`%b` or the
    `locale` module: core runs wherever the process happens to run, and a date printed in
    the host's locale is a date that reads wrong the day somebody moves the container."""
    return f"{GIORNI_BREVI[day.isoweekday() - 1]} {day.day} {MESI_BREVI[day.month - 1]}"


def euro(value: Decimal) -> str:
    """`1.800,00 €`. Italian grouping and decimal separators, the same recipe
    `gmail/solleciti.py`'s own `_euro` uses -- copied rather than imported, because a
    mail is not a reminder and the two are free to diverge without either breaking."""
    quantized = round_money(value)
    grouped = f"{quantized:,.2f}"
    return grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".") + " €"


def _ore_it(value: Decimal) -> str:
    """`12` for a whole number of hours, `12,50` otherwise -- the count a sentence reads,
    not a currency figure with a forced `,00`."""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _ore_registrate(value: Decimal) -> str:
    return "1 ora registrata" if value == 1 else f"{_ore_it(value)} ore registrate"


def _conta(n: int, singolare: str, plurale: str) -> str:
    return f"1 {singolare}" if n == 1 else f"{n} {plurale}"


def _ritardo(giorni: int | None) -> str:
    """The lateness word of a «scadute» row: «scade oggi» for a debt due today (zero days
    late is a fact, not a delay to count), «N giorni di ritardo» after that, nothing for
    the rows of the other sections, which carry no lateness at all."""
    if giorni is None:
        return ""
    if giorni == 0:
        return "scade oggi"
    return f"{giorni} giorni di ritardo"


def _scadute_totale(digest: WeeklyDigest) -> Decimal:
    """The one sum this module does: `scadute[].importo`. Used by the subject and by
    «Da incassare»'s own heading, so the figure in one cannot disagree with the other."""
    totale = Decimal("0")
    for fattura in digest.scadute:
        totale += fattura.importo
    return totale


def digest_subject(digest: WeeklyDigest) -> str:
    """The week's non-zero facts, in the fixed order spec 2026-09-16 §3.1 gives: fatture
    emesse, da incassare, ore registrate, offerte in attesa, deal mossi. «La tua
    settimana in PigroCRM» when none of the five has anything to say."""
    parti: list[str] = []
    if digest.emesse:
        parti.append(_conta(len(digest.emesse), "fattura emessa", "fatture emesse"))
    scadute_totale = _scadute_totale(digest)
    if scadute_totale:
        parti.append(f"{euro(scadute_totale)} da incassare")
    if digest.ore is not None and digest.ore.ore_totali:
        parti.append(_ore_registrate(digest.ore.ore_totali))
    if digest.offerte_in_attesa:
        parti.append(
            _conta(len(digest.offerte_in_attesa), "offerta in attesa", "offerte in attesa")
        )
    if digest.deal_mossi:
        parti.append(_conta(len(digest.deal_mossi), "deal mosso", "deal mossi"))
    if not parti:
        return "La tua settimana in PigroCRM"
    return "La tua settimana: " + ", ".join(parti)


def _da_digest(url: str) -> str:
    """Every link the report hands out carries `da=digest`, so PostHog can tell a click
    that came from the mail from the same page reached any other way (spec §3.1)."""
    return f"{url}{'&' if '?' in url else '?'}da=digest"


# `StatoPagamento`'s collected value (`invoices/schemas.py`). Written out rather than
# imported: this module renders, and a mail that imported the register's literals would
# make the register's types part of the mail's contract.
INCASSATO = "incassato"


def _stato_leggibile(inv: DigestInvoice) -> str:
    """The word §3.1 item 3 asks for: «emessa, trasmessa, incassata».

    `inv.stato` alone cannot say it. For every row of «Emesse questa settimana» it is the
    constant `"emessa"` -- the section's own predicate is `stato = 'emessa'` -- so printing
    it was printing the heading again. What varies is the *payment* state, which is the
    other column, and whether the document has been transmitted, which is a third.

    In that order: collected is the end of the story whether or not the document was ever
    transmitted, so it wins, and «trasmessa» is only worth saying about an invoice nobody
    has paid yet.
    """
    if inv.stato_pagamento == INCASSATO:
        return "incassata"
    if inv.trasmessa:
        return "trasmessa"
    return inv.stato


def _invoice_line(inv: DigestInvoice, extra: str = "") -> str:
    """The four things every invoice row shows -- numero, cliente, importo, data --
    plus whichever one extra fact the section is about (§3.1 item 3: «Numero, cliente,
    importo, stato»). Not yet escaped: the caller decides, once, whether this line is
    going into the text or the html."""
    base = f"{inv.numero} — {inv.cliente} — {euro(inv.importo)} — {giorno_breve(inv.data)}"
    return f"{base} — {extra}" if extra else base


def _row(testo: str, *, url: str | None = None, label: str = "") -> tuple[str, str]:
    """One report row, as both its plain line and its escaped `<li>` -- the single place
    external text is escaped and a link is turned into `_quiet_link`, so the two
    representations of a row cannot drift apart."""
    if url is None:
        return testo, html_escape.escape(testo)
    safe_url = html_escape.escape(url, quote=True)
    return f"{testo} — {url}", f"{html_escape.escape(testo)} — {_quiet_link(safe_url, label)}"


def digest_mail(to: str, digest: WeeklyDigest, *, public_url: str) -> Mail:
    """The weekly report, section by section, in the order `WeeklyDigest`'s fields carry
    (spec 2026-09-16 §3.1 and §3.5): a section prints only when it has rows, every link
    is built from `public_url` (or, for a signal, from its own `collegamento`) and
    carries `da=digest`, and a still week closes with the prompt instead of a list."""
    e = html_escape.escape
    heading = f'style="margin:28px 0 8px 0;font-weight:600;color:{INK};"'
    li = 'style="margin:0 0 6px 0;"'
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};"'

    def link(path: str) -> str:
        return _da_digest(f"{public_url}{path}")

    text_parts: list[str] = ["Ciao,", "", "ecco la tua settimana in PigroCRM."]
    html_parts: list[str] = [
        '<p style="margin:0 0 8px 0;">Ciao,</p>',
        '<p style="margin:0 0 8px 0;">ecco la tua settimana in PigroCRM.</p>',
    ]

    def sezione(titolo: str, righe: list[tuple[str, str]]) -> None:
        if not righe:
            return
        text_parts.append("")
        text_parts.append(titolo)
        text_parts.extend(f"- {testo}" for testo, _ in righe)
        html_parts.append(f"<p {heading}>{e(titolo)}</p>")
        html_parts.append(
            '<ul style="margin:0;padding:0 0 0 18px;">'
            + "".join(f"<li {li}>{html}</li>" for _, html in righe)
            + "</ul>"
        )

    # 1. Da incassare: le scadute, dalla più in ritardo, poi quelle in scadenza.
    scadute_totale = _scadute_totale(digest)
    righe_incassare: list[tuple[str, str]] = [
        _row(
            _invoice_line(
                inv,
                _ritardo(inv.giorni_di_ritardo),
            ),
            url=link(f"/app/invoices/{inv.invoice_id}"),
            label="Prepara il sollecito",
        )
        for inv in digest.scadute
    ]
    if digest.scadute:
        righe_incassare.append(
            _row(
                "Tutte le fatture scadute",
                url=link("/app/invoices?scadute=true"),
                label="Vai alle fatture",
            )
        )
    righe_incassare += [_row(_invoice_line(inv, "in scadenza")) for inv in digest.in_scadenza]
    titolo_incassare = "Da incassare" + (f" — {euro(scadute_totale)}" if scadute_totale else "")
    sezione(titolo_incassare, righe_incassare)

    # 2. Da emettere: i deal vinti senza fattura, le ore fatturabili non fatturate.
    righe_emettere: list[tuple[str, str]] = []
    if digest.vinti_da_fatturare:
        testo_deal_vinti = _conta(
            digest.vinti_da_fatturare, "deal vinto da fatturare", "deal vinti da fatturare"
        )
        righe_emettere.append(
            _row(
                testo_deal_vinti,
                url=link("/app/deal/list?da_fatturare=true"),
                label="Vai ai deal",
            )
        )
    if digest.ore_non_fatturate or digest.valore_maturato:
        righe_emettere.append(
            _row(
                f"{_ore_it(digest.ore_non_fatturate)} ore fatturabili non fatturate — "
                f"{euro(digest.valore_maturato)} maturati",
                # `/app/hours` bare: the page declares no `validateSearch`, so a
                # `?fatturato=false` would be dropped on arrival and the link would
                # promise a filtered list the reader never gets. Only `da=digest`
                # survives, and that one `link()` adds to every link the report hands out.
                url=link("/app/hours"),
                label="Vai alle ore",
            )
        )
    sezione("Da emettere", righe_emettere)

    # 3. Emesse questa settimana, col totale del mese accanto a quello precedente.
    righe_emesse = [_row(_invoice_line(inv, _stato_leggibile(inv))) for inv in digest.emesse]
    if digest.emesse:
        totali = (
            f"Totale mese: {euro(digest.totale_mese_corrente)} "
            f"(mese scorso {euro(digest.totale_mese_precedente)})"
        )
        righe_emesse.append((totali, totali))
    sezione("Emesse questa settimana", righe_emesse)

    # 4. Incassate questa settimana.
    righe_incassate = [_row(_invoice_line(inv)) for inv in digest.incassate]
    sezione("Incassate questa settimana", righe_incassate)

    # 5. Le ore: solo se la settimana ne ha (`WeeklyDigest.ore` è `None` altrimenti).
    # Il totale è `ore_totali`; la quota è quanti dei giorni della finestra hanno una
    # voce di tempo, il solo confronto che `WeekHours` porta con sé.
    ore = digest.ore
    if ore is not None:
        copertura = len(ore.giorni) - len(ore.giorni_senza_ore)
        riga_totale = f"{_ore_registrate(ore.ore_totali)} questa settimana"
        riga_copertura = f"{copertura} giorni su {len(ore.giorni)} con ore registrate"
        # Nothing external in either line, so text and html are the same string.
        sezione("Le ore", [(riga_totale, riga_totale), (riga_copertura, riga_copertura)])

    # 6. In pipeline: le fasi aperte, i deal mossi, le offerte in attesa di risposta.
    righe_pipeline: list[tuple[str, str]] = [
        _row(
            f"{stage.stage_nome}: {_conta(stage.numero, 'deal', 'deal')} — "
            f"{euro(stage.valore_totale)}"
        )
        for stage in digest.pipeline
    ]
    if digest.pipeline:
        righe_pipeline.append(
            _row("La pipeline completa", url=link("/app/deal"), label="Vai alla pipeline")
        )
    righe_pipeline += [
        _row(f"{mossa.titolo} è passato a {mossa.stage_nome} ({giorno_breve(mossa.quando)})")
        for mossa in digest.deal_mossi
    ]
    righe_pipeline += [
        _row(
            f"{offerta.titolo} — in attesa da {_conta(offerta.giorni, 'giorno', 'giorni')}",
            url=link(f"/app/documents/{offerta.document_id}"),
            label="Apri l'offerta",
        )
        for offerta in digest.offerte_in_attesa
    ]
    sezione("In pipeline", righe_pipeline)

    # 7. Da sistemare: i tre segnali dell'operativa, quando almeno uno è sopra zero.
    # `collegamento` è già relativo allo spazio (`/app/...`, la stessa forma di
    # `dashboard/service.py`), quindi passa per `link()` come ogni altro path.
    righe_segnali = [
        _row(
            f"{segnale.etichetta}: {segnale.conteggio}",
            url=link(segnale.collegamento),
            label="Vedi",
        )
        for segnale in digest.segnali
    ]
    sezione("Da sistemare", righe_segnali)

    # Chiusura variabile: la settimana ferma offre l'assistente invece di una lista.
    if digest.settimana_ferma:
        ferma = (
            "Settimana ferma: se hai un preventivo da fare, l'assistente lo prepara in un minuto."
        )
        prompt = 'Chiedi al tuo assistente: "cosa devo fare questa settimana in PigroCRM?"'
        text_parts.append("")
        text_parts.append(ferma)
        text_parts.append(prompt)
        html_parts.append(f"<p {paragraph}>{e(ferma)}</p>")
        html_parts.append(f"<p {paragraph}>{e(prompt)}</p>")

    cta_url = link("/app")
    text_parts.append("")
    text_parts.append(f"Apri PigroCRM: {cta_url}")
    html_parts.append(f"<p {paragraph}>{_button(e(cta_url, quote=True), 'Apri PigroCRM')}</p>")

    opt_out_url = link("/app/settings/profile")
    text_parts.append("")
    text_parts.append(f"Non inviarmi più il resoconto: {opt_out_url}")
    html_parts.append(
        f"<p {small}>{_quiet_link(e(opt_out_url, quote=True), 'Non inviarmi più il resoconto')}</p>"
    )

    subject = digest_subject(digest)
    text = "\n".join(text_parts) + "\n"
    body = "\n".join(html_parts)
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))
