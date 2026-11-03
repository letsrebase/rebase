"""The mail that carries the way in: one link per space the address owns."""

import html as html_escape
from collections.abc import Sequence

from pigrocrm.core.mail.frame import INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail


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
        parts.append(button(safe_url, "Entra nel tuo spazio"))
        parts.append(
            f'<p {small}word-break:break-all;">Se il bottone non si apre, copia questo '
            f"indirizzo nel browser:<br>{quiet_link(safe_url, safe_url)}</p>"
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
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
