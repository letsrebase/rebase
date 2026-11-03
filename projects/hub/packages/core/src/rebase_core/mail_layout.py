"""The landing's visual system, translated into what mail clients render.

`projects/website/src/landing.css` is the source: tables, inline styles, the brand values
written out because a mail has no stylesheet to read them from, and none of the things
Gmail and Outlook drop (gradients, box shadows, web fonts). Every template module
(`mail_member`, `mail_contracts`, `mail_team`, the campaigns' renderer) builds its body
from `button` and `quiet_link` and wraps it in `frame`.
"""

import html as html_escape

# ---- the landing's box, as a mail --------------------------------------------------------
#
# `shared/brand/palette.css` is the source of these six values; a mail cannot read a
# stylesheet, so they are written out here and `test_mail.py` pins them.
PAPER = "#f1f2f3"
INK = "#011936"
INK_QUIET = "#465362"
ROYAL_GOLD = "#f9dc5c"
WATERMELON = "#ed254e"
# White on raw Watermelon fails the body-text contrast floor; the landing's `.cta` uses
# this darker step for the same reason (`--color-watermelon-strong`).
CTA = "#e5133e"
# «Sono disponibile» in the availability mail (spec § 3.6): the palette has no green, so
# this one is the mail's own, for Ivan to confirm on the pull request. White on it
# measures 4.71:1 by WCAG's formula, over the 4.5:1 floor `CTA` was darkened for; the
# spec's `#2b8a3e`, a shade lighter, measures 4.37:1, under it.
AVAILABLE_GREEN = "#29843b"
FONT = "Outfit, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif"
# The landing's stepped shadow: the border colour, moved 8px right and down. A mail
# client draws no box-shadow, so the step is a cell of ink behind the card.
STEP = 8
# Every layout table in a mail is this: no spacing, no borders of its own, invisible to
# a screen reader.
TABLE = 'role="presentation" cellpadding="0" cellspacing="0" border="0"'
SITE = "https://letsrebase.com"


def quiet_link(href: str, label: str) -> str:
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


def button(href: str, label: str, colour: str = CTA) -> str:
    """The landing's `.cta`: a filled rectangle, hard edges, white text at weight 500, on
    `CTA` unless a mail offers two answers and colours them apart."""
    text = f"font-family:{FONT};font-size:17px;font-weight:500;color:#ffffff;"
    # The padding sits on the cell as well as on the anchor: Outlook's engine ignores
    # `display:inline-block` on a link and would shrink the box to the text.
    cell = (
        f'bgcolor="{colour}" style="background-color:{colour};border:2px solid {colour};'
        'padding:14px 24px;"'
    )
    return (
        f'<table {TABLE} style="border-collapse:collapse;">'
        f"<tr><td {cell}>"
        f'<a href="{href}" style="display:inline-block;{text}text-decoration:none;">{label}</a>'
        "</td></tr></table>"
    )


def frame(title: str, body: str) -> str:
    """The landing's card around `body` (already HTML): paper ground, the mark and the
    name, a white box with a 2px ink border and the 8px step, the fine footer."""
    ground = f'bgcolor="{PAPER}" style="background-color:{PAPER};"'
    name = f"font-family:{FONT};font-size:18px;font-weight:500;color:{INK};"
    step = f'bgcolor="{INK}" style="background-color:{INK};padding:0 {STEP}px {STEP}px 0;"'
    card = f'bgcolor="#ffffff" style="background-color:#ffffff;border:2px solid {INK};"'
    copy = f"font-family:{FONT};font-size:17px;font-weight:400;line-height:1.6;color:{INK};"
    fine = f"font-family:{FONT};font-size:13px;line-height:1.6;color:{INK_QUIET};"
    gap = "&nbsp;&nbsp;&nbsp;"
    footer = gap.join(
        (
            quiet_link(f"{SITE}/", "rebase"),
            quiet_link(f"{SITE}/privacy", "Privacy"),
            quiet_link(f"{SITE}/terms", "Termini"),
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
            f'<td valign="middle" style="{name}">rebase</td>',
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
