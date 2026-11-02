"""The mail that invites a person into a space."""

import html as html_escape

from pigrocrm.core.mail.frame import INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail


def invitation_mail(
    to: str, spazio: str, invitato_da: str, url: str, *, nome: str | None = None, giorni: int
) -> Mail:
    """A space gains a person (spec 2026-09-17 §5): who invited them, the space, the
    link, and that no password is involved. `nome` is the person's own name when the
    admin typed one; the greeting stays anonymous when nobody knows it, exactly like
    `welcome_mail`'s. The window and the single use are stated plainly, and ignoring
    an unexpected mail is fine -- the same sentence `magic_link_mail` uses for the
    same reassurance. Every external value is escaped in the HTML."""
    e = html_escape.escape
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    safe_url = e(url, quote=True)
    text = (
        f"{greeting}\n\n"
        f"{invitato_da} ti ha invitato a entrare nello spazio PigroCRM di {spazio}. "
        "Basta un click, senza scegliere una password:\n\n"
        f"{url}\n\n"
        f"Il link vale {giorni} giorni e funziona una volta sola. Se non te lo aspettavi, "
        "ignora questa mail: non succede niente.\n\n"
        "PigroCRM\n"
    )
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(invitato_da)} ti ha invitato a entrare nello '
            f"spazio PigroCRM di {e(spazio)}. Basta un click, senza scegliere una password.</p>",
            button(safe_url, "Entra nello spazio"),
            f'<p {small}word-break:break-all;">Se il bottone non si apre, copia questo '
            f"indirizzo nel browser:<br>{quiet_link(safe_url, e(url))}</p>",
            f"<p {paragraph}>Il link vale {giorni} giorni e funziona una volta sola. "
            "Se non te lo aspettavi, ignora questa mail: non succede niente.</p>",
            f"<p {paragraph}>PigroCRM</p>",
        )
    )
    subject = f"Sei stato invitato in {spazio} su PigroCRM"
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
