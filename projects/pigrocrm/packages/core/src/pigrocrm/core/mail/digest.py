"""The weekly digest, as a mail (spec 2026-09-16 §3.5)."""

import html as html_escape
from decimal import Decimal

from pigrocrm.core.digest.schemas import DigestInvoice, WeeklyDigest
from pigrocrm.core.mail.formatting import day_short, euro
from pigrocrm.core.mail.frame import INK, INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail


def _hours_it(value: Decimal) -> str:
    """`12` for a whole number of hours, `12,50` otherwise -- the count a sentence reads,
    not a currency figure with a forced `,00`."""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _hours_recorded(value: Decimal) -> str:
    return "1 ora registrata" if value == 1 else f"{_hours_it(value)} ore registrate"


def _count(n: int, singolare: str, plurale: str) -> str:
    return f"1 {singolare}" if n == 1 else f"{n} {plurale}"


def _delay(days: int | None) -> str:
    """The lateness word of a «scadute» row: «scade oggi» for a debt due today (zero days
    late is a fact, not a delay to count), «N giorni di ritardo» after that, nothing for
    the rows of the other sections, which carry no lateness at all."""
    if days is None:
        return ""
    if days == 0:
        return "scade oggi"
    return f"{days} giorni di ritardo"


def _overdue_total(digest: WeeklyDigest) -> Decimal:
    """The one sum this module does: `overdue[].amount`. Used by the subject and by
    «Da incassare»'s own heading, so the figure in one cannot disagree with the other."""
    total = Decimal("0")
    for invoice in digest.overdue:
        total += invoice.amount
    return total


def digest_subject(digest: WeeklyDigest) -> str:
    """The week's non-zero facts, in the fixed order spec 2026-09-16 §3.1 gives: fatture
    emesse, da incassare, ore registrate, offerte in attesa, deal mossi. «La tua
    settimana in PigroCRM» when none of the five has anything to say."""
    parti: list[str] = []
    if digest.issued:
        parti.append(_count(len(digest.issued), "fattura emessa", "fatture emesse"))
    total_overdue = _overdue_total(digest)
    if total_overdue:
        parti.append(f"{euro(total_overdue)} da incassare")
    if digest.hours is not None and digest.hours.total_hours:
        parti.append(_hours_recorded(digest.hours.total_hours))
    if digest.offers_pending:
        parti.append(_count(len(digest.offers_pending), "offerta in attesa", "offerte in attesa"))
    if digest.deal_mossi:
        parti.append(_count(len(digest.deal_mossi), "deal mosso", "deal mossi"))
    if not parti:
        return "La tua settimana in PigroCRM"
    return "La tua settimana: " + ", ".join(parti)


def _da_digest(url: str) -> str:
    """Every link the report hands out carries `da=digest`, so PostHog can tell a click
    that came from the mail from the same page reached any other way (spec §3.1)."""
    return f"{url}{'&' if '?' in url else '?'}da=digest"


# `PaymentStatus`'s collected value (`invoices/schemas.py`). Written out rather than
# imported: this module renders, and a mail that imported the register's literals would
# make the register's types part of the mail's contract.
PAID = "paid"


def _status_readable(inv: DigestInvoice) -> str:
    """The word §3.1 item 3 asks for: «emessa, trasmessa, incassata».

    `inv.status` alone cannot say it. For every row of «Emesse questa settimana» it is the
    constant `"issued"` -- the section's own predicate is `stato = 'emessa'` -- so printing
    it was printing the heading again. What varies is the *payment* state, which is the
    other column, and whether the document has been transmitted, which is a third.

    In that order: collected is the end of the story whether or not the document was ever
    transmitted, so it wins, and «trasmessa» is only worth saying about an invoice nobody
    has paid yet.
    """
    if inv.payment_status == PAID:
        return "incassata"
    if inv.transmitted:
        return "trasmessa"
    return inv.status


def _invoice_line(inv: DigestInvoice, extra: str = "") -> str:
    """The four things every invoice row shows -- numero, cliente, importo, data --
    plus whichever one extra fact the section is about (§3.1 item 3: «Numero, cliente,
    importo, stato»). Not yet escaped: the caller decides, once, whether this line is
    going into the text or the html."""
    base = f"{inv.number} — {inv.customer} — {euro(inv.amount)} — {day_short(inv.date)}"
    return f"{base} — {extra}" if extra else base


def _row(text: str, *, url: str | None = None, label: str = "") -> tuple[str, str]:
    """One report row, as both its plain line and its escaped `<li>` -- the single place
    external text is escaped and a link is turned into `_quiet_link`, so the two
    representations of a row cannot drift apart."""
    if url is None:
        return text, html_escape.escape(text)
    safe_url = html_escape.escape(url, quote=True)
    return f"{text} — {url}", f"{html_escape.escape(text)} — {quiet_link(safe_url, label)}"


def digest_mail(to: str, digest: WeeklyDigest, *, public_url: str) -> Mail:
    """The weekly report, section by section, in the order `WeeklyDigest`'s fields carry
    (spec 2026-09-16 §3.1 and §3.5): a section prints only when it has rows, every link
    is built from `public_url` (or, for a signal, from its own `link`) and
    carries `da=digest`, and a still week closes with the prompt instead of a list."""
    e = html_escape.escape
    heading = f'style="margin:28px 0 8px 0;font-weight:600;color:{INK};"'
    li = 'style="margin:0 0 6px 0;"'
    paragraph = 'style="margin:24px 0 0 0;"'
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};"'

    def link(path: str) -> str:
        return _da_digest(f"{public_url}{path}")

    text_parts: list[str] = ["Ciao,", "", "ecco la tua settimana in PigroCRM."]
    html_parts: list[str] = [
        '<p style="margin:0 0 8px 0;">Ciao,</p>',
        '<p style="margin:0 0 8px 0;">ecco la tua settimana in PigroCRM.</p>',
    ]

    def section(title: str, lines: list[tuple[str, str]]) -> None:
        if not lines:
            return
        text_parts.append("")
        text_parts.append(title)
        text_parts.extend(f"- {testo}" for testo, _ in lines)
        html_parts.append(f"<p {heading}>{e(title)}</p>")
        html_parts.append(
            '<ul style="margin:0;padding:0 0 0 18px;">'
            + "".join(f"<li {li}>{html}</li>" for _, html in lines)
            + "</ul>"
        )

    # 1. «Da incassare»: the overdue invoices, most late first, then the ones falling due.
    total_overdue = _overdue_total(digest)
    lines_unpaid: list[tuple[str, str]] = [
        _row(
            _invoice_line(
                inv,
                _delay(inv.days_of_delay),
            ),
            url=link(f"/app/invoices/{inv.invoice_id}"),
            label="Prepara il sollecito",
        )
        for inv in digest.overdue
    ]
    if digest.overdue:
        lines_unpaid.append(
            _row(
                "Tutte le fatture scadute",
                url=link("/app/invoices?overdue=true"),
                label="Vai alle fatture",
            )
        )
    lines_unpaid += [_row(_invoice_line(inv, "in scadenza")) for inv in digest.in_due_date]
    title_unpaid = "Da incassare" + (f" — {euro(total_overdue)}" if total_overdue else "")
    section(title_unpaid, lines_unpaid)

    # 2. «Da emettere»: won deals with no invoice, billable hours not yet invoiced.
    lines_emettere: list[tuple[str, str]] = []
    if digest.won_to_invoice:
        text_deal_won = _count(
            digest.won_to_invoice, "deal vinto da fatturare", "deal vinti da fatturare"
        )
        lines_emettere.append(
            _row(
                text_deal_won,
                url=link("/app/deal/list?to_invoice=true"),
                label="Vai ai deal",
            )
        )
    if digest.hours_uninvoiced or digest.accrued_value:
        lines_emettere.append(
            _row(
                f"{_hours_it(digest.hours_uninvoiced)} ore fatturabili non fatturate — "
                f"{euro(digest.accrued_value)} maturati",
                # `/app/hours` bare: the page declares no `validateSearch`, so a
                # `?invoiced=false` would be dropped on arrival and the link would
                # promise a filtered list the reader never gets. Only `da=digest`
                # survives, and that one `link()` adds to every link the report hands out.
                url=link("/app/hours"),
                label="Vai alle ore",
            )
        )
    section("Da emettere", lines_emettere)

    # 3. «Emesse questa settimana», with this month's total next to last month's.
    lines_issued = [_row(_invoice_line(inv, _status_readable(inv))) for inv in digest.issued]
    if digest.issued:
        total = (
            f"Totale mese: {euro(digest.total_month_current)} "
            f"(mese scorso {euro(digest.total_month_previous)})"
        )
        lines_issued.append((total, total))
    section("Emesse questa settimana", lines_issued)

    # 4. «Incassate questa settimana».
    lines_paid = [_row(_invoice_line(inv)) for inv in digest.paid]
    section("Incassate questa settimana", lines_paid)

    # 5. «Le ore»: only when the week has some (`WeeklyDigest.hours` is `None` otherwise).
    # The total is `total_hours`; the coverage is how many days of the window have a time
    # entry, the only comparison `WeekHours` carries with it.
    hours = digest.hours
    if hours is not None:
        copertura = len(hours.days) - len(hours.days_without_hours)
        line_total = f"{_hours_recorded(hours.total_hours)} questa settimana"
        riga_copertura = f"{copertura} giorni su {len(hours.days)} con ore registrate"
        # Nothing external in either line, so text and html are the same string.
        section("Le ore", [(line_total, line_total), (riga_copertura, riga_copertura)])

    # 6. «In pipeline»: the open stages, the deals moved, the offers awaiting an answer.
    lines_pipeline: list[tuple[str, str]] = [
        _row(
            f"{stage.stage_name}: {_count(stage.number, 'deal', 'deal')} — "
            f"{euro(stage.total_value)}"
        )
        for stage in digest.pipeline
    ]
    if digest.pipeline:
        lines_pipeline.append(
            _row("La pipeline completa", url=link("/app/deal"), label="Vai alla pipeline")
        )
    lines_pipeline += [
        _row(f"{mossa.title} è passato a {mossa.stage_name} ({day_short(mossa.when)})")
        for mossa in digest.deal_mossi
    ]
    lines_pipeline += [
        _row(
            f"{offer.title} — in attesa da {_count(offer.days, 'day', 'days')}",
            url=link(f"/app/documents/{offer.document_id}"),
            label="Apri l'offerta",
        )
        for offer in digest.offers_pending
    ]
    section("In pipeline", lines_pipeline)

    # 7. «Da sistemare»: the three operational signals, when at least one is above zero.
    # `link` is already relative to the space (`/app/...`, the same shape as
    # `dashboard/service.py`), so it goes through `link()` like every other path.
    lines_signals = [
        _row(
            f"{segnale.label}: {segnale.count}",
            url=link(segnale.link),
            label="Vedi",
        )
        for segnale in digest.signals
    ]
    section("Da sistemare", lines_signals)

    # 8. «Da fare»: the recipient's own commitments, late first and then the coming seven
    # days. The week's view only (REB-726): the daily summary of the reminders spec is
    # another mail.
    tasks = digest.tasks
    lines_fare: list[tuple[str, str]] = []
    for group, totale, delay in (
        (tasks.overdue, tasks.overdue_total, True),
        (tasks.in_week, tasks.in_week_total, False),
    ):
        for riga in group:
            when = day_short(riga.due_date)
            if delay:
                when += f" — {_count(riga.days_of_delay or 0, 'day', 'days')} di ritardo"
            lines_fare.append(
                _row(
                    f"{riga.title} — {when}",
                    url=link(f"/app/activities?open={riga.task_id}"),
                    label="Apri",
                )
            )
        if totale > len(group):
            other = _count(totale - len(group), "altra", "altre")
            more = f"…e {other} {'in ritardo' if delay else 'questa settimana'}"
            lines_fare.append((more, e(more)))
    if lines_fare:
        lines_fare.append(
            _row(
                "Tutto quello che c'è da fare",
                url=link("/app/tasks?view=today"),
                label="Vai a Oggi",
            )
        )
    section("Da fare", lines_fare)

    # Variable closing: a still week offers the assistant instead of a list.
    if digest.week_stalled:
        stalled = (
            "Settimana ferma: se hai un preventivo da fare, l'assistente lo prepara in un minuto."
        )
        prompt = 'Chiedi al tuo assistente: "cosa devo fare questa settimana in PigroCRM?"'
        text_parts.append("")
        text_parts.append(stalled)
        text_parts.append(prompt)
        html_parts.append(f"<p {paragraph}>{e(stalled)}</p>")
        html_parts.append(f"<p {paragraph}>{e(prompt)}</p>")

    cta_url = link("/app")
    text_parts.append("")
    text_parts.append(f"Apri PigroCRM: {cta_url}")
    html_parts.append(f"<p {paragraph}>{button(e(cta_url, quote=True), 'Apri PigroCRM')}</p>")

    opt_out_url = link("/app/settings/profile")
    text_parts.append("")
    text_parts.append(f"Non inviarmi più il resoconto: {opt_out_url}")
    html_parts.append(
        f"<p {small}>{quiet_link(e(opt_out_url, quote=True), 'Non inviarmi più il resoconto')}</p>"
    )

    subject = digest_subject(digest)
    text = "\n".join(text_parts) + "\n"
    body = "\n".join(html_parts)
    return Mail(to=to, subject=subject, text=text, html=frame(subject, body))
