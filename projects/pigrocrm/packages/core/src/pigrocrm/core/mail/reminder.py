"""The mail that carries the reminders a person has not seen yet (REB-737)."""

import html as html_escape
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pigrocrm.core.mail.frame import INK, INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail


@dataclass(frozen=True)
class ReminderLine:
    """One reminder as a mail prints it: how serious, what it says, where it opens (an
    absolute URL) and whether the person's settings call it urgent."""

    severity: Literal["warning", "serious", "critical"]
    title: str
    detail: str
    url: str
    urgent: bool


# The word each severity wears in the app (`features/reminders/fields.ts`).
_REMINDER_SEVERITY_WORD = {"critical": "Urgente", "serious": "Grave", "warning": "Da vedere"}
_SUBJECT_TITLE_MAX = 80


def _one_line(text: str, limit: int = _SUBJECT_TITLE_MAX) -> str:
    """A subject is one line: whatever a title holds (a name typed by somebody, a line
    break) is collapsed, and a long one is cut."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def reminder_subject(
    lines: Sequence[ReminderLine], *, kind: Literal["immediate", "summary"]
) -> str:
    """`Urgente: Fattura 12 scaduta` for one reminder at once, `3 promemoria urgenti` for
    several, `Promemoria di oggi: 5 da vedere` for the daily summary."""
    if kind == "summary":
        return f"Promemoria di oggi: {len(lines)} da vedere"
    if len(lines) == 1:
        line = lines[0]
        prefix = "Urgente" if line.urgent else "Promemoria"
        return f"{prefix}: {_one_line(line.title)}"
    if all(line.urgent for line in lines):
        return f"{len(lines)} promemoria urgenti"
    return f"{len(lines)} nuovi promemoria"


def reminder_mail(
    to: str,
    lines: Sequence[ReminderLine],
    *,
    kind: Literal["immediate", "summary"],
    reminders_url: str,
    settings_url: str,
) -> Mail:
    """The reminders a person has not seen, severest first (the caller sorts): one mail at
    once for what is urgent, or the daily summary of the rest (spec 2026-10-26 §6.2). Each
    line opens its record; the list of reminders and the settings that decide what comes
    by mail close it. Every value from outside is escaped in the HTML."""
    e = html_escape.escape
    intro = (
        "ecco cosa è urgente adesso in PigroCRM:"
        if kind == "immediate"
        else "ecco i promemoria di oggi in PigroCRM, dal più grave:"
    )
    heading = f'style="margin:0 0 4px 0;font-weight:600;color:{INK};"'
    detail_style = f'style="margin:0 0 6px 0;font-size:15px;color:{INK_QUIET};"'
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};"'

    text_parts: list[str] = ["Ciao,", "", intro, ""]
    html_parts: list[str] = [
        '<p style="margin:0 0 8px 0;">Ciao,</p>',
        f'<p style="margin:0 0 20px 0;">{e(intro)}</p>',
    ]
    for line in lines:
        word = _REMINDER_SEVERITY_WORD[line.severity]
        text_parts.append(f"[{word}] {line.title}")
        if line.detail:
            text_parts.append(f"  {line.detail}")
        text_parts.append(f"  {line.url}")
        text_parts.append("")
        html_parts.append(
            f"<p {heading}>{e(word)}: {quiet_link(e(line.url, quote=True), e(line.title))}</p>"
        )
        if line.detail:
            html_parts.append(f"<p {detail_style}>{e(line.detail)}</p>")
    html_parts.append(
        f"<p {paragraph}>{button(e(reminders_url, quote=True), 'Apri i promemoria')}</p>"
    )
    text_parts.append(f"Apri i promemoria: {reminders_url}")
    footer = "Cambia cosa ricevi, su quali canali e a che ora"
    text_parts.append("")
    text_parts.append(f"{footer}: {settings_url}")
    html_parts.append(f"<p {small}>{quiet_link(e(settings_url, quote=True), footer)}</p>")

    subject = reminder_subject(lines, kind=kind)
    return Mail(
        to=to,
        subject=subject,
        text="\n".join(text_parts) + "\n",
        html=frame(subject, "\n".join(html_parts)),
    )
