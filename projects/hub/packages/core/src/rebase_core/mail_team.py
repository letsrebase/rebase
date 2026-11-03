"""The mails of the team builder and the talent cloud: the request that reaches rebase,
the availability question a talent gets, and the cloud opened for a company."""

import html as html_escape
from decimal import Decimal

from rebase_core.mail import Mail
from rebase_core.mail_layout import (
    AVAILABLE_GREEN,
    INK,
    INK_QUIET,
    TABLE,
    button,
    frame,
    quiet_link,
)


def team_request_mail(
    to: str, *, company: str, summary: str | None, talent: str | None, url: str
) -> Mail:
    """A team request arriving (spec § 3.5), to rebase's own address and never to the
    company: the subject names the company, the body carries the project's anonymous
    summary, or for a request of one talent from the cloud that talent's name, and the
    link to the request's page, where the contacts and the team are. The company's name
    and the summary were typed by a visitor, so both are escaped in the HTML, the way
    the magic link escapes its token."""
    e = html_escape.escape
    subject = f"Nuova richiesta team da {company}"
    if talent is not None:
        what = f"{company} ha chiesto un talento del talent cloud: {talent}."
        quoted = None
    else:
        what = f"{company} ha chiesto il team che il team builder ha proposto per questo progetto:"
        quoted = summary
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
            button(safe_url, "Apri la richiesta"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, "\n".join(rows)))


def _euro(amount: Decimal) -> str:
    """A daily rate the Italian way: «450 €», «1.450,50 €»; cents only when there are
    some."""
    places = 0 if amount == amount.to_integral_value() else 2
    figure = f"{amount:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{figure} €"


def team_availability_mail(
    to: str,
    *,
    nome: str,
    role: str,
    summary: str | None,
    rate: Decimal | None,
    yes_url: str,
    no_url: str,
) -> Mail:
    """The availability mail (REB-517, spec § 3.6), to one talent of a team request: rebase
    has a project the person fits, the project's anonymous summary, the role proposed to
    them, their own daily rate (never the client's band), and two answers, each a link
    to the hub's answer page, which records nothing until the person confirms there.

    It names no company, no product and no other talent: the summary is the one the
    admin checked (`team_requests.names_the_company` refuses it otherwise), and a
    request of one talent from the cloud, which has none, goes without it. The summary
    and the role were written by a model and an admin, so both are escaped in the HTML,
    and so are the links, which carry an `&`."""
    e = html_escape.escape
    subject = "Un progetto per te: sei disponibile?"
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    intro = "abbiamo un progetto per cui il tuo profilo è adatto."
    if summary:
        intro += " Eccolo, in breve:"
    role = f"Il ruolo che ti proponiamo: {role}."
    if rate is not None:
        fee = (
            "Il compenso sarebbe la tua tariffa giornaliera, quella del tuo profilo su "
            f"rebase: {_euro(rate)} al giorno."
        )
    else:
        fee = (
            "Il compenso sarebbe la tua tariffa giornaliera: sul tuo profilo non c'è "
            "ancora, la concordiamo con te."
        )
    ask = "Sei disponibile? Rispondi da qui:"
    after = (
        "Puoi rispondere una volta sola, entro trenta giorni. Se sei disponibile, ti "
        "riscriviamo noi con i dettagli."
    )
    yes_label, no_label = "Sono disponibile", "Non sono disponibile"
    text_parts = [greeting, intro]
    if summary:
        text_parts.append(summary)
    text_parts.extend(
        (
            role,
            fee,
            "Sei disponibile? Rispondi da uno di questi due link:",
            f"{yes_label}:\n{yes_url}",
            f"{no_label}:\n{no_url}",
            after,
            "Noi di rebase",
        )
    )
    text = "\n\n".join(text_parts) + "\n"
    safe_yes, safe_no = e(yes_url, quote=True), e(no_url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    # Two buttons, one under the other: side by side they overflow a phone's width.
    spacer = 'height="12" style="height:12px;line-height:12px;font-size:0;"'
    gap = f"<table {TABLE}><tr><td {spacer}>&nbsp;</td></tr></table>"
    rows = [
        f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
        f'<p style="margin:0 0 20px 0;">{e(intro)}</p>',
    ]
    if summary:
        rows.append(
            f'<p style="margin:0 0 20px 0;padding:0 0 0 16px;border-left:2px solid {INK};">'
            f"{e(summary)}</p>"
        )
    rows.extend(
        (
            f'<p style="margin:0 0 20px 0;">{e(role)}</p>',
            f'<p style="margin:0 0 20px 0;">{e(fee)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(ask)}</p>',
            button(safe_yes, yes_label, AVAILABLE_GREEN),
            gap,
            button(safe_no, no_label),
            f'<p {small}word-break:break-all;">'
            "Se i bottoni non si aprono, copia uno di questi indirizzi nel browser:<br>"
            f"{yes_label}: {quiet_link(safe_yes, safe_yes)}<br>"
            f"{no_label}: {quiet_link(safe_no, safe_no)}</p>",
            f'<p style="margin:24px 0 0 0;">{e(after)}</p>',
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, "\n".join(rows)))


def talent_cloud_opened_mail(to: str, *, nome: str, company: str, url: str) -> Mail:
    """The talent cloud open for a company (REB-518, spec § 4.1), to the referente of the
    request an admin opened it from: the subject names the company, the body says what
    the cloud is and that the way in is the one they already have, the magic link to
    their area, and the one button is the cloud's page there (`url`), which sends a
    signed-out visitor to the login first. The name and the company's were typed in the
    company wizard, so both are escaped in the HTML, and so is the link."""
    e = html_escape.escape
    subject = f"Il talent cloud di rebase è aperto per {company}"
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    opened = (
        f"il talent cloud di rebase è aperto per {company}: i profili dei talenti della "
        "community, per nome e con il CV, e il team builder per chiederci un team o una "
        "persona sola, senza moduli da compilare."
    )
    enter = (
        "Lo trovi nella tua area su rebase. Si entra con la tua email, senza password: ti "
        "mandiamo un link e sei dentro."
    )
    text = f"{greeting}\n\n{opened}\n\n{enter}\n\n{url}\n\nNoi di rebase\n"
    safe_url = e(url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0 0 20px 0;">{e(opened)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(enter)}</p>',
            button(safe_url, "Apri il talent cloud"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
