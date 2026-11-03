"""The member-facing mails: the magic link and the welcome."""

import html as html_escape
from dataclasses import dataclass

from rebase_core.mail import Mail
from rebase_core.mail_layout import (
    INK_QUIET,
    button,
    frame,
    quiet_link,
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
            button(safe_link, "Entra nella tua area"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_link, safe_link)}</p>",
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
        html=frame("Il tuo accesso a rebase", body),
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

    first_name: str
    last_name: str
    job_title: str | None
    linkedin_url: str | None
    links: tuple[str, ...]


def welcome_mail(
    to: str,
    first_name: str | None,
    login_link: str,
    *,
    kind: str,
    job_title: str | None = None,
    complete: bool = True,
    summary: CardSummary | None = None,
    wizard_link: str | None = None,
) -> Mail:
    """The one-off mail that tells a person their area is open (ORB-157), in three
    voices decided with Ivan on 2026-09-11:

    - `kind == "person"`: they filled the card themselves; it is complete, they enter.
    - `kind == "admin"`: we wrote the card from public sources. A short recap of what we
      found, the ask to enter and add what nothing public states (CV, rate, remote
      option), and that an offer in line with the profile is already there: reply to
      talk about it.
    - `kind == "none"`: nothing public was enough for a card. The wizard, five minutes,
      and from then on the address is the way in.

    Same box and voice as the magic link; the person's words are escaped in the HTML."""
    e = html_escape.escape
    greeting = f"Ciao {first_name}," if first_name else "Ciao,"
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
        f"{quiet_link(LINKEDIN_PAGE, 'la pagina LinkedIn di rebase')}.</p>"
    )
    safe_login = e(login_link, quote=True)
    enter_text = (
        "la tua area su rebase è aperta. Si entra con la tua email, senza password: ti "
        "mandiamo un link e sei dentro.\n"
        "\n"
        f"{login_link}\n"
    )
    enter_html = (
        '<p style="margin:0 0 24px 0;">la tua area su rebase è aperta. Si entra con la '
        "tua email, senza password: ti mandiamo un link e sei dentro.</p>"
        + button(safe_login, "Entra nella tua area")
        + f'<p {small}word-break:break-all;">'
        "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
        f"{quiet_link(safe_login, safe_login)}</p>"
    )

    if kind == "person":
        # «Completa» is a fact about the card, not about who wrote it: since the wizard
        # stopped demanding a CV a person can have filled the form themselves and still
        # be missing the file, and telling them the card is complete would be both
        # false and a reason never to come back and finish it.
        if not complete:
            card_text = (
                f"Sei {job_title}: manca solo il CV perché le aziende possano trovarti, "
                "e lo carichi dalla tua area."
                if job_title
                else "Manca solo il CV perché le aziende possano trovarti, e lo carichi "
                "dalla tua area."
            )
        else:
            card_text = (
                f"La tua scheda è completa: sei {job_title}, e le aziende possono trovarti."
                if job_title
                else "La tua scheda è completa: le aziende possono trovarti."
            )
        middle_text = enter_text + "\n" + card_text + "\n"
        middle_html = enter_html + f"<p {paragraph}>{e(card_text)}</p>"
    elif kind == "admin":
        if summary is None:
            raise ValueError("a welcome mail of kind 'admin' needs a card summary")
        found: list[tuple[str, str]] = [("Nome", f"{summary.first_name} {summary.last_name}")]
        if summary.job_title:
            found.append(("Posizione", summary.job_title))
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
    elif kind == "none":
        if wizard_link is None:
            raise ValueError("a welcome mail of kind 'none' needs the wizard link")
        safe_wizard = e(wizard_link, quote=True)
        intro = (
            "la tua area su rebase è aperta, ma la tua scheda non siamo riusciti a "
            "prepararla noi: in pubblico non c'era abbastanza. Compilala tu, ci vogliono "
            "cinque minuti."
        )
        after = f"Da quel momento entri con la tua email, senza password, da qui: {login_link}"
        middle_text = intro + "\n\n" + wizard_link + "\n\n" + after + "\n"
        middle_html = (
            f'<p style="margin:0 0 24px 0;">{e(intro)}</p>'
            + button(safe_wizard, "Compila il tuo profilo")
            + f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{quiet_link(safe_wizard, safe_wizard)}</p>"
            + f"<p {paragraph}>Da quel momento entri con la tua email, senza password, da "
            f"{quiet_link(safe_login, safe_login)}.</p>"
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
        html=frame("La tua area su rebase è aperta", body),
    )
