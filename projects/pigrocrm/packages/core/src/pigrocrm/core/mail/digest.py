"""The weekly digest, as a mail (spec 2026-09-16 §3.5)."""

import html as html_escape
from decimal import Decimal

from pigrocrm.core.digest.schemas import DigestInvoice, WeeklyDigest
from pigrocrm.core.mail.formatting import euro, giorno_breve
from pigrocrm.core.mail.frame import INK, INK_QUIET, button, frame, quiet_link
from pigrocrm.core.mail.transport import Mail


def _ore_it(value: Decimal) -> str:
    """`12` for a whole number of hours, `12,50` otherwise -- the count a sentence reads,
    not a currency figure with a forced `,00`."""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


def _ore_registrate(value: Decimal) -> str:
    return "1 ora registrata" if value == 1 else f"{_ore_it(value)} ore registrate"


def _conta(n: int, singolare: str, plurale: str) -> str:
    return f"1 {singolare}" if n == 1 else f"{n} {plurale}"


def _ritardo(giorni: int | None) -> str:
    """The lateness word of a «scadute» row: «scade oggi» for a debt due today (zero days
    late is a fact, not a delay to count), «N giorni di ritardo» after that, nothing for
    the rows of the other sections, which carry no lateness at all."""
    if giorni is None:
        return ""
    if giorni == 0:
        return "scade oggi"
    return f"{giorni} giorni di ritardo"


def _scadute_totale(digest: WeeklyDigest) -> Decimal:
    """The one sum this module does: `scadute[].importo`. Used by the subject and by
    «Da incassare»'s own heading, so the figure in one cannot disagree with the other."""
    totale = Decimal("0")
    for fattura in digest.scadute:
        totale += fattura.importo
    return totale


def digest_subject(digest: WeeklyDigest) -> str:
    """The week's non-zero facts, in the fixed order spec 2026-09-16 §3.1 gives: fatture
    emesse, da incassare, ore registrate, offerte in attesa, deal mossi. «La tua
    settimana in PigroCRM» when none of the five has anything to say."""
    parti: list[str] = []
    if digest.emesse:
        parti.append(_conta(len(digest.emesse), "fattura emessa", "fatture emesse"))
    scadute_totale = _scadute_totale(digest)
    if scadute_totale:
        parti.append(f"{euro(scadute_totale)} da incassare")
    if digest.ore is not None and digest.ore.ore_totali:
        parti.append(_ore_registrate(digest.ore.ore_totali))
    if digest.offerte_in_attesa:
        parti.append(
            _conta(len(digest.offerte_in_attesa), "offerta in attesa", "offerte in attesa")
        )
    if digest.deal_mossi:
        parti.append(_conta(len(digest.deal_mossi), "deal mosso", "deal mossi"))
    if not parti:
        return "La tua settimana in PigroCRM"
    return "La tua settimana: " + ", ".join(parti)


def _da_digest(url: str) -> str:
    """Every link the report hands out carries `da=digest`, so PostHog can tell a click
    that came from the mail from the same page reached any other way (spec §3.1)."""
    return f"{url}{'&' if '?' in url else '?'}da=digest"


# `StatoPagamento`'s collected value (`invoices/schemas.py`). Written out rather than
# imported: this module renders, and a mail that imported the register's literals would
# make the register's types part of the mail's contract.
INCASSATO = "incassato"


def _stato_leggibile(inv: DigestInvoice) -> str:
    """The word §3.1 item 3 asks for: «emessa, trasmessa, incassata».

    `inv.stato` alone cannot say it. For every row of «Emesse questa settimana» it is the
    constant `"emessa"` -- the section's own predicate is `stato = 'emessa'` -- so printing
    it was printing the heading again. What varies is the *payment* state, which is the
    other column, and whether the document has been transmitted, which is a third.

    In that order: collected is the end of the story whether or not the document was ever
    transmitted, so it wins, and «trasmessa» is only worth saying about an invoice nobody
    has paid yet.
    """
    if inv.stato_pagamento == INCASSATO:
        return "incassata"
    if inv.trasmessa:
        return "trasmessa"
    return inv.stato


def _invoice_line(inv: DigestInvoice, extra: str = "") -> str:
    """The four things every invoice row shows -- numero, cliente, importo, data --
    plus whichever one extra fact the section is about (§3.1 item 3: «Numero, cliente,
    importo, stato»). Not yet escaped: the caller decides, once, whether this line is
    going into the text or the html."""
    base = f"{inv.numero} — {inv.cliente} — {euro(inv.importo)} — {giorno_breve(inv.data)}"
    return f"{base} — {extra}" if extra else base


def _row(testo: str, *, url: str | None = None, label: str = "") -> tuple[str, str]:
    """One report row, as both its plain line and its escaped `<li>` -- the single place
    external text is escaped and a link is turned into `_quiet_link`, so the two
    representations of a row cannot drift apart."""
    if url is None:
        return testo, html_escape.escape(testo)
    safe_url = html_escape.escape(url, quote=True)
    return f"{testo} — {url}", f"{html_escape.escape(testo)} — {quiet_link(safe_url, label)}"


def digest_mail(to: str, digest: WeeklyDigest, *, public_url: str) -> Mail:
    """The weekly report, section by section, in the order `WeeklyDigest`'s fields carry
    (spec 2026-09-16 §3.1 and §3.5): a section prints only when it has rows, every link
    is built from `public_url` (or, for a signal, from its own `collegamento`) and
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

    def sezione(titolo: str, righe: list[tuple[str, str]]) -> None:
        if not righe:
            return
        text_parts.append("")
        text_parts.append(titolo)
        text_parts.extend(f"- {testo}" for testo, _ in righe)
        html_parts.append(f"<p {heading}>{e(titolo)}</p>")
        html_parts.append(
            '<ul style="margin:0;padding:0 0 0 18px;">'
            + "".join(f"<li {li}>{html}</li>" for _, html in righe)
            + "</ul>"
        )

    # 1. «Da incassare»: the overdue invoices, most late first, then the ones falling due.
    scadute_totale = _scadute_totale(digest)
    righe_incassare: list[tuple[str, str]] = [
        _row(
            _invoice_line(
                inv,
                _ritardo(inv.giorni_di_ritardo),
            ),
            url=link(f"/app/invoices/{inv.invoice_id}"),
            label="Prepara il sollecito",
        )
        for inv in digest.scadute
    ]
    if digest.scadute:
        righe_incassare.append(
            _row(
                "Tutte le fatture scadute",
                url=link("/app/invoices?scadute=true"),
                label="Vai alle fatture",
            )
        )
    righe_incassare += [_row(_invoice_line(inv, "in scadenza")) for inv in digest.in_scadenza]
    titolo_incassare = "Da incassare" + (f" — {euro(scadute_totale)}" if scadute_totale else "")
    sezione(titolo_incassare, righe_incassare)

    # 2. «Da emettere»: won deals with no invoice, billable hours not yet invoiced.
    righe_emettere: list[tuple[str, str]] = []
    if digest.vinti_da_fatturare:
        testo_deal_vinti = _conta(
            digest.vinti_da_fatturare, "deal vinto da fatturare", "deal vinti da fatturare"
        )
        righe_emettere.append(
            _row(
                testo_deal_vinti,
                url=link("/app/deal/list?da_fatturare=true"),
                label="Vai ai deal",
            )
        )
    if digest.ore_non_fatturate or digest.valore_maturato:
        righe_emettere.append(
            _row(
                f"{_ore_it(digest.ore_non_fatturate)} ore fatturabili non fatturate — "
                f"{euro(digest.valore_maturato)} maturati",
                # `/app/hours` bare: the page declares no `validateSearch`, so a
                # `?fatturato=false` would be dropped on arrival and the link would
                # promise a filtered list the reader never gets. Only `da=digest`
                # survives, and that one `link()` adds to every link the report hands out.
                url=link("/app/hours"),
                label="Vai alle ore",
            )
        )
    sezione("Da emettere", righe_emettere)

    # 3. «Emesse questa settimana», with this month's total next to last month's.
    righe_emesse = [_row(_invoice_line(inv, _stato_leggibile(inv))) for inv in digest.emesse]
    if digest.emesse:
        totali = (
            f"Totale mese: {euro(digest.totale_mese_corrente)} "
            f"(mese scorso {euro(digest.totale_mese_precedente)})"
        )
        righe_emesse.append((totali, totali))
    sezione("Emesse questa settimana", righe_emesse)

    # 4. «Incassate questa settimana».
    righe_incassate = [_row(_invoice_line(inv)) for inv in digest.incassate]
    sezione("Incassate questa settimana", righe_incassate)

    # 5. «Le ore»: only when the week has some (`WeeklyDigest.ore` is `None` otherwise).
    # The total is `ore_totali`; the coverage is how many days of the window have a time
    # entry, the only comparison `WeekHours` carries with it.
    ore = digest.ore
    if ore is not None:
        copertura = len(ore.giorni) - len(ore.giorni_senza_ore)
        riga_totale = f"{_ore_registrate(ore.ore_totali)} questa settimana"
        riga_copertura = f"{copertura} giorni su {len(ore.giorni)} con ore registrate"
        # Nothing external in either line, so text and html are the same string.
        sezione("Le ore", [(riga_totale, riga_totale), (riga_copertura, riga_copertura)])

    # 6. «In pipeline»: the open stages, the deals moved, the offers awaiting an answer.
    righe_pipeline: list[tuple[str, str]] = [
        _row(
            f"{stage.stage_nome}: {_conta(stage.numero, 'deal', 'deal')} — "
            f"{euro(stage.valore_totale)}"
        )
        for stage in digest.pipeline
    ]
    if digest.pipeline:
        righe_pipeline.append(
            _row("La pipeline completa", url=link("/app/deal"), label="Vai alla pipeline")
        )
    righe_pipeline += [
        _row(f"{mossa.titolo} è passato a {mossa.stage_nome} ({giorno_breve(mossa.quando)})")
        for mossa in digest.deal_mossi
    ]
    righe_pipeline += [
        _row(
            f"{offerta.titolo} — in attesa da {_conta(offerta.giorni, 'giorno', 'giorni')}",
            url=link(f"/app/documents/{offerta.document_id}"),
            label="Apri l'offerta",
        )
        for offerta in digest.offerte_in_attesa
    ]
    sezione("In pipeline", righe_pipeline)

    # 7. «Da sistemare»: the three operational signals, when at least one is above zero.
    # `collegamento` is already relative to the space (`/app/...`, the same shape as
    # `dashboard/service.py`), so it goes through `link()` like every other path.
    righe_segnali = [
        _row(
            f"{segnale.etichetta}: {segnale.conteggio}",
            url=link(segnale.collegamento),
            label="Vedi",
        )
        for segnale in digest.segnali
    ]
    sezione("Da sistemare", righe_segnali)

    # 8. «Da fare»: the recipient's own commitments, late first and then the coming seven
    # days. The week's view only (REB-726): the daily summary of the reminders spec is
    # another mail.
    attivita = digest.attivita
    righe_fare: list[tuple[str, str]] = []
    for gruppo, totale, ritardo in (
        (attivita.in_ritardo, attivita.in_ritardo_totale, True),
        (attivita.in_settimana, attivita.in_settimana_totale, False),
    ):
        for riga in gruppo:
            quando = giorno_breve(riga.scadenza)
            if ritardo:
                quando += f" — {_conta(riga.giorni_di_ritardo or 0, 'giorno', 'giorni')} di ritardo"
            righe_fare.append(
                _row(
                    f"{riga.titolo} — {quando}",
                    url=link(f"/app/activities?apri={riga.attivita_id}"),
                    label="Apri",
                )
            )
        if totale > len(gruppo):
            altre = _conta(totale - len(gruppo), "altra", "altre")
            piu = f"…e {altre} {'in ritardo' if ritardo else 'questa settimana'}"
            righe_fare.append((piu, e(piu)))
    if righe_fare:
        righe_fare.append(
            _row("Tutto quello che c'è da fare", url=link("/app/oggi"), label="Vai a Oggi")
        )
    sezione("Da fare", righe_fare)

    # Variable closing: a still week offers the assistant instead of a list.
    if digest.settimana_ferma:
        ferma = (
            "Settimana ferma: se hai un preventivo da fare, l'assistente lo prepara in un minuto."
        )
        prompt = 'Chiedi al tuo assistente: "cosa devo fare questa settimana in PigroCRM?"'
        text_parts.append("")
        text_parts.append(ferma)
        text_parts.append(prompt)
        html_parts.append(f"<p {paragraph}>{e(ferma)}</p>")
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
