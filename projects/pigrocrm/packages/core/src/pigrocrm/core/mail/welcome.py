"""The one-off mail that says a space exists."""

import html as html_escape

from pigrocrm.core.mail.frame import INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail

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
    community = (
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
        + (f"{community}\n\n" if not membro else "")
        + "PigroCRM\n"
    )
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            '<p style="margin:0 0 24px 0;">il tuo spazio PigroCRM è pronto. Il bottone '
            "vale poco e funziona una volta sola.</p>",
            button(safe_entra, "Entra nel tuo spazio"),
            f'<p {small}word-break:break-all;">Se il bottone non si apre, copia questo '
            f"indirizzo nel browser:<br>{quiet_link(safe_entra, e(entra_url))}</p>",
            f"<p {paragraph}>Le altre volte si entra con la tua email, senza password: "
            f"da {quiet_link(e(login_url, quote=True), e(login_url))} scrivi l'indirizzo e "
            "ti arriva un link.</p>",
            f"<p {paragraph}>{e(assistant)}</p>",
            f"<p {paragraph}>Le prime tre cose da fare:</p>",
            '<ol style="margin:8px 0 0 0;padding:0 0 0 22px;">'
            + "".join(f"<li {li}>{e(step)}</li>" for step in steps)
            + "</ol>",
            (
                f"<p {paragraph}>PigroCRM è il perk della community rebase, developer e CTO "
                "freelance in Italia: progetti da aziende vere e persone che ci sono già "
                f"passate. Se vuoi entrarci: {quiet_link(HUB_WIZARD_URL, HUB_WIZARD_URL)}</p>"
                if not membro
                else ""
            ),
            f"<p {paragraph}>PigroCRM</p>",
        )
    )
    subject = "Il tuo spazio PigroCRM è pronto"
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
