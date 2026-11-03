"""The mails about a contract and an engagement: signing, cancellation, the signed copy
and the hours' home on Pigro."""

import html as html_escape

from rebase_core.mail import Attachment, Mail
from rebase_core.mail_layout import (
    INK_QUIET,
    button,
    frame,
    quiet_link,
)
from rebase_core.regimes import OCCASIONAL, VAT_REGISTERED

# ---- the contracts (REB-387) --------------------------------------------------------------
#
# The hub sends the signing mails itself: Documenso distributes with
# `distributionMethod: NONE` and every mail of its own switched off, so these are the only
# mails a freelancer gets about a contract, in the same box as the magic link.


def document_name(kind: str, number: str | None, regime: str = VAT_REGISTERED) -> str:
    """How a mail and Documenso name a contract: «contratto quadro rebase», «lettera di
    incarico n. 2026-001», and for a prestazione occasionale (REB-687) «contratto quadro
    rebase per prestazioni occasionali», «lettera di incarico occasionale n. 2026-001».
    `kind` is `framework` or `letter`."""
    occasional = regime == OCCASIONAL
    if kind == "framework":
        if occasional:
            return "contratto quadro rebase per prestazioni occasionali"
        return "contratto quadro rebase"
    return f"lettera di incarico{' occasionale' if occasional else ''} n. {number}"


def signing_request_mail(
    to: str,
    nome: str,
    kind: str,
    number: str | None,
    signing_url: str,
    *,
    regime: str = VAT_REGISTERED,
) -> Mail:
    """One mail per document to sign (spec § 6): the subject names it, one paragraph says
    what it is, one button opens the signing site. The link is the only way the
    document reaches the person, and it goes into an attribute and into text: escaped
    both times, like the magic link. An occasional document says so (REB-687)."""
    e = html_escape.escape
    name = document_name(kind, number, regime)
    if kind != "framework":
        what = f"la {name}: il lavoro, le date e il compenso che abbiamo concordato"
    elif regime == OCCASIONAL:
        what = (
            "il contratto quadro per prestazioni occasionali con rebase: le regole di ogni "
            "prestazione occasionale che fai tramite noi. Si firma una volta e si rinnova "
            "da solo ogni dodici mesi"
        )
    else:
        what = (
            "il contratto quadro con rebase: le regole di ogni lavoro che fai tramite noi. "
            "Si firma una volta e si rinnova da solo ogni dodici mesi"
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
            button(safe_url, "Firma il documento"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))


def _article(kind: str) -> str:
    return "il" if kind == "framework" else "la"


def signing_cancelled_mail(
    to: str, nome: str, kind: str, number: str | None, *, regime: str = VAT_REGISTERED
) -> Mail:
    """A document that had already left for signature, cancelled by rebase before it was
    signed (REB-407): the same frame as `signing_request_mail`, with no button, since
    there is nothing left to sign. The link the earlier mail carried is dead from now
    on, and a new document, if one is needed, arrives on its own."""
    e = html_escape.escape
    name = document_name(kind, number, regime)
    cancelled = "cancelled" if kind == "framework" else "cancelled"
    nuovo = "un nuovo contratto quadro" if kind == "framework" else "una nuova lettera di incarico"
    if regime == OCCASIONAL:
        nuovo += " per prestazioni occasionali" if kind == "framework" else " occasionale"
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
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))


def signed_copy_mail(
    to: str,
    *,
    kind: str,
    number: str | None,
    attachment: Attachment,
    nome: str,
    last_name: str,
    for_rebase: bool = False,
    regime: str = VAT_REGISTERED,
) -> Mail:
    """The sealed copy, attached (spec § 6): to the freelancer, and with `for_rebase` to
    rebase's contracts address. Its last page is Documenso's certificate of the
    signature, in English (probe § 7): the mail says so, so nobody takes it for a stray
    page."""
    e = html_escape.escape
    name = document_name(kind, number, regime)
    certificate = "L'ultima pagina, in inglese, è il certificato della firma elettronica."
    signed = "Firmato" if kind == "framework" else "Firmata"
    if for_rebase:
        greeting = "Ciao,"
        paragraph = (
            f"{nome} {last_name} ha firmato {_article(kind)} {name}: la copia firmata è in "
            f"allegato. {certificate}"
        )
        subject = f"{signed} da {nome} {last_name}: {name}"
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
        html=frame(subject, body),
        attachments=(attachment,),
    )


# ---- the hours of an engagement (REB-498) -------------------------------------------------


def engagement_ready_mail(
    to: str, *, nome: str, number: str, company: str, deal_url: str, space_created: bool
) -> Mail:
    """Sent once a match is linked to its deal on Pigro (spec § 3.7): the letter is
    active, its hours are logged on Pigro in the project the button opens, rebase reads
    those hours and nothing else of the space, and, when the link opened the space
    itself, that Pigro's own mail carries the way in. The same box as the signing mails;
    the link goes into an attribute and into text, escaped both times."""
    e = html_escape.escape
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    subject = f"La tua lettera n. {number} è attiva: le ore si registrano su Pigro"
    active = (
        f"la lettera di incarico n. {number} con {company} è attiva. Le ore di questo "
        "incarico si registrano su Pigro, nel progetto che si apre dal bottone qui sotto."
    )
    after = [
        "rebase legge le ore di quel progetto per la rendicontazione al cliente, e "
        "nient'altro del tuo spazio."
    ]
    if space_created:
        after.append(
            "Su Pigro abbiamo aperto uno spazio a tuo nome: il link per entrare te lo manda "
            "Pigro, in una mail a parte."
        )
    text = (
        f"{greeting}\n\n{active}\n\n{deal_url}\n\n"
        + "".join(f"{paragraph}\n\n" for paragraph in after)
        + "Noi di rebase\n"
    )
    safe_url = e(deal_url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(active)}</p>',
            button(safe_url, "Apri il progetto su Pigro"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_url, safe_url)}</p>",
            *(f'<p style="margin:24px 0 0 0;">{e(paragraph)}</p>' for paragraph in after),
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
