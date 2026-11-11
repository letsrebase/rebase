"""The mail that carries the one-time code of an export's share link (accountant export design
§ 3): the files of an export with pay open only for whoever holds the link and also reads this
mail. It names the company and the period so the recipient knows what the code is for, and never
carries the link or the file."""

import html as html_escape

from pigrocrm.core.mail.frame import FONT, INK, INK_QUIET, frame
from pigrocrm.core.mail.transport import Mail


def export_code_mail(to: str, code: str, minutes: int, company: str, period: str) -> Mail:
    """`code` is six digits, `company` and `period` are already words («Lumen Studio S.r.l.»,
    «dal 01/10/2026 al 31/10/2026»)."""
    subject = f"Il tuo codice per aprire l'esportazione di {company}"
    text = (
        "Ciao,\n\n"
        f"questo è il codice per aprire l'esportazione di {company} ({period}):\n\n"
        f"{code}\n\n"
        f"Vale {minutes} minuti e si usa una volta sola. Se non l'hai chiesto tu, ignora questa "
        "mail: non succede niente.\n\n"
        "PigroCRM\n"
    )
    digits = (
        f'<p style="margin:0 0 24px 0;font-family:{FONT};font-size:34px;font-weight:500;'
        f'letter-spacing:8px;color:{INK};">{html_escape.escape(code)}</p>'
    )
    small = f"margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};"
    body = "\n".join(
        (
            '<p style="margin:0 0 20px 0;">Ciao,</p>',
            '<p style="margin:0 0 24px 0;">questo è il codice per aprire l\'esportazione di '
            f"<strong>{html_escape.escape(company)}</strong> ({html_escape.escape(period)}):</p>",
            digits,
            f'<p style="{small}">Vale {minutes} minuti e si usa una volta sola. Se non '
            "l'hai chiesto tu, ignora questa mail: non succede niente.</p>",
            '<p style="margin:24px 0 0 0;">PigroCRM</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
