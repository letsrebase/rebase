"""The mails about a framework agreement and an engagement letter: the link to sign, the
cancellation and the signed copy (REB-1019, moved from the hub's `mail_contracts`, REB-387).

Documenso distributes with `distributionMethod: NONE` and every mail of its own switched off, so
these are the only mails a freelancer gets about a contract, in the same box as the magic link.
They speak in the name of the legal entity that issues the document (`issuer`, its short name):
the copy is Italian, like every mail, and `kind` is `framework_agreement` or `engagement_letter`.
The reserved area's copy of the signed PDF is the hub's and is not promised here.
"""

import html as html_escape

from pigrocrm.core.mail.frame import INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Attachment, Mail

FRAMEWORK = "framework_agreement"
OCCASIONAL = "occasional"


def document_name(kind: str, number: str | None, regime: str, issuer: str) -> str:
    """How a mail and Documenso name a document: «contratto quadro rebase», «lettera di incarico
    n. 2026-001» and, for an occasional one, «contratto quadro rebase per prestazioni
    occasionali» and «lettera di incarico occasionale n. 2026-001»."""
    occasional = regime == OCCASIONAL
    if kind == FRAMEWORK:
        if occasional:
            return f"contratto quadro {issuer} per prestazioni occasionali"
        return f"contratto quadro {issuer}"
    return f"lettera di incarico{' occasionale' if occasional else ''} n. {number}"


def _greeting(first_name: str | None) -> str:
    return f"Ciao {first_name}," if first_name else "Ciao,"


def _article(kind: str) -> str:
    return "il" if kind == FRAMEWORK else "la"


def _plain_box(subject: str, greeting: str, paragraph: str, issuer: str) -> str:
    e = html_escape.escape
    return frame(
        subject,
        "\n".join(
            (
                f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
                f'<p style="margin:0;">{e(paragraph)}</p>',
                f'<p style="margin:24px 0 0 0;">Noi di {e(issuer)}</p>',
            )
        ),
    )


def signing_request_mail(
    to: str,
    first_name: str | None,
    kind: str,
    number: str | None,
    signing_url: str,
    *,
    regime: str,
    issuer: str,
) -> Mail:
    """One mail per document to sign: the subject names it, one paragraph says what it is, one
    button opens the signing site. The link is the only way the document reaches the person, and
    it goes into an attribute and into text: escaped both times, like the magic link."""
    e = html_escape.escape
    name = document_name(kind, number, regime, issuer)
    if kind != FRAMEWORK:
        what = f"la {name}: il lavoro, le date e il compenso che abbiamo concordato"
    elif regime == OCCASIONAL:
        what = (
            f"il contratto quadro per prestazioni occasionali con {issuer}: le regole di ogni "
            "prestazione occasionale che fai tramite noi. Si firma una volta e si rinnova da "
            "solo ogni dodici mesi"
        )
    else:
        what = (
            f"il contratto quadro con {issuer}: le regole di ogni lavoro che fai tramite noi. "
            "Si firma una volta e si rinnova da solo ogni dodici mesi"
        )
    paragraph = (
        f"ti mandiamo da firmare {what}. Si firma online, senza creare un account, dal "
        "bottone qui sotto; la copia firmata ti arriva per email."
    )
    greeting = _greeting(first_name)
    subject = f"Da firmare: {name}"
    text = f"{greeting}\n\n{paragraph}\n\n{signing_url}\n\nNoi di {issuer}\n"
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
            f'<p style="margin:24px 0 0 0;">Noi di {e(issuer)}</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))


def signing_cancelled_mail(
    to: str,
    first_name: str | None,
    kind: str,
    number: str | None,
    *,
    regime: str,
    issuer: str,
) -> Mail:
    """A document that had already left for signature, cancelled before it was signed: the same
    frame as `signing_request_mail`, with no button, since there is nothing left to sign. The link
    the earlier mail carried is dead from now on, and a new document, if one is needed, arrives on
    its own."""
    name = document_name(kind, number, regime, issuer)
    cancelled = "annullato" if kind == FRAMEWORK else "annullata"
    new_one = "un nuovo contratto quadro" if kind == FRAMEWORK else "una nuova lettera di incarico"
    if regime == OCCASIONAL:
        new_one += " per prestazioni occasionali" if kind == FRAMEWORK else " occasionale"
    greeting = _greeting(first_name)
    paragraph = (
        f"il link che ti avevamo mandato per firmare {_article(kind)} {name} non funziona "
        f"più: lo abbiamo annullato noi di {issuer}. Se serve {new_one}, ti scriviamo."
    )
    subject = f"{name[0].upper()}{name[1:]} {cancelled}"
    text = f"{greeting}\n\n{paragraph}\n\nNoi di {issuer}\n"
    return Mail(
        to=to, subject=subject, text=text, html=_plain_box(subject, greeting, paragraph, issuer)
    )


def signed_copy_mail(
    to: str,
    *,
    kind: str,
    number: str | None,
    attachment: Attachment,
    first_name: str | None,
    freelancer_name: str,
    for_issuer: bool,
    regime: str,
    issuer: str,
) -> Mail:
    """The sealed copy, attached: to the freelancer, and with `for_issuer` to the issuer's own
    address. Its last page is Documenso's certificate of the signature, in English (probe § 7): the
    mail says so, so nobody takes it for a stray page."""
    name = document_name(kind, number, regime, issuer)
    certificate = "L'ultima pagina, in inglese, è il certificato della firma elettronica."
    signed = "Firmato" if kind == FRAMEWORK else "Firmata"
    if for_issuer:
        greeting = "Ciao,"
        paragraph = (
            f"{freelancer_name} ha firmato {_article(kind)} {name}: la copia firmata è in "
            f"allegato. {certificate}"
        )
        subject = f"{signed} da {freelancer_name}: {name}"
    else:
        greeting = _greeting(first_name)
        paragraph = (
            f"hai firmato {_article(kind)} {name}: la copia firmata è in allegato. {certificate}"
        )
        subject = f"{signed}: {name}"
    text = f"{greeting}\n\n{paragraph}\n\nNoi di {issuer}\n"
    return Mail(
        to=to,
        subject=subject,
        text=text,
        html=_plain_box(subject, greeting, paragraph, issuer),
        attachments=(attachment,),
    )
