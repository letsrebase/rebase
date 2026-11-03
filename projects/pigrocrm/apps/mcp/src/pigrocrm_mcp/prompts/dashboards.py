"""Three of §10's four prompts. Each is several reads **plus a posture on how to read
them**, which is what makes it a prompt and not a tool.

The distinction is not stylistic. A tool is invoked by the *model*, when it decides it needs
one, and returns *data*. A prompt is invoked by the *user*, from a menu, and returns
*messages*. And the judgement sits in a different place: in a tool it is the model's, in a
prompt it is written down here. A tool that also returned "ask about the deals that have not
moved, do not summarise the ones that have" would be putting instructions inside a data
result -- the shape of an injection -- and the model would have to guess it should call it,
whereas here the human picks it and can see what was attached.

The context travels as **Markdown text inside the message**. It is a resource block only
where the resource already exists, which in this slice means `customer://{id}` and therefore
only `prompts/customer.py`. §11.1 adds no new resource, and inventing
`dashboard://commercial?from=...` in order to have a URI to embed would be adding one without
saying so.

**Every figure rendered here is a field of a dashboard response, printed.** Nothing in this
module computes anything -- the same rule as `core/dashboard/`, for the same reason. If a
figure is missing from the response, the fix is on the owning service.

**And every list is capped by a named constant.** That is the other half of §10, and the
half that is easy to lose: a prompt's cost is paid in the model's context window on *every*
render, so a section that grows with the register is a tax the user pays for rows nobody
asked about. Where a cap bites, the prompt says how many rows it did not show rather than
truncating silently -- a briefing that quietly omits its tail is worse than one that admits
it, because the reader cannot tell the difference from a short register.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.dashboard.schemas import PeriodQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds
from pigrocrm.core.deals.service import DealService
from pigrocrm_mcp.context import McpContext

# The caps. Each is the number of rows that is worth a place in the model's context window
# on every render of that section, and each is named so `test_mcp_prompts.py` can assert the
# bound against a corpus far larger than it -- which is the only way a cap is testable at
# all. A cap asserted only against a corpus smaller than itself is a comment.
PENDING_OFFERS_SHOWN = 10
RECENT_DEALS_SHOWN = 8


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": {"type": "text", "text": text}}


def _euro(value: Decimal) -> str:
    """The string the service produced, with a comma.

    `str(value)`, never a format spec: `Decimal` already carries its own scale, and
    `f"{value:.2f}"` would re-round a figure the owning service has already rounded --
    §7's second source of truth with a friendly tone. `float(value)` would be worse still,
    putting a binary rounding error into a briefing.
    """
    return f"{str(value).replace('.', ',')} €"


def _percent(value: Decimal | None) -> str:
    """A dash, never "0%".

    Zero per cent means "I lost everything"; `None` means nothing closed, or nothing was
    earned. Two different facts, and a briefing that conflates them is worse than one that
    omits the line -- the same rule `PnlTotals.percentage_margin` and
    `ClosedInPeriod.conversion_rate` are both typed `Decimal | None` to express.
    """
    return "—" if value is None else f"{str(value).replace('.', ',')}%"


def _and_the_rest(shown: int, total: int) -> list[str]:
    """The line that admits a truncation, or nothing at all.

    `total` is the count the owning service reported, not `len()` of a list this module
    already truncated: the point of the line is to name rows that are *not* here.
    """
    if total <= shown:
        return []
    return ["", f"_Altre {total - shown} righe non mostrate._"]


def review_pipeline(
    context: McpContext, date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    """The weekly review. Posture: ask about the deals that have not moved.

    The pending offers come from `board.offers_pending` rather than from a second call to
    `DocumentRepository.pending_offers`: the dashboard already read them inside its one
    `REPEATABLE READ` transaction, and a second read here would be a second instant -- a
    briefing whose list and whose count could disagree with each other by construction.
    """
    query = PeriodQuery(
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    board = DashboardService(context.session).get_commercial_dashboard(query, context.actor)

    lines = [
        f"# Revisione pipeline — {board.period.date_from} → {board.period.date_to}",
        "",
        "## Pipeline per stato",
        "",
        "| Stato | Deal | Valore | Senza valore | Valore ponderato (stima) |",
        "|---|---|---|---|---|",
    ]
    for row in board.pipeline:
        lines.append(
            f"| {row.stage_name} | {row.number} | {_euro(row.total_value)} "
            f"| {row.without_value} | {_euro(row.weighted_value)} |"
        )
    lines += [
        "",
        "Il valore ponderato è una **stima** (valore previsto × probabilità): non è "
        "fatturato e non va sommato ai ricavi. I deal senza valore previsto sono contati "
        "a parte e non valgono zero.",
        "",
        "Gli stati chiusi (vinto, perso) contano i deal che ci sono **oggi**, non quelli "
        "chiusi nel periodo: quelli sono nella sezione successiva.",
        "",
        "## Chiusure nel periodo",
        "",
        f"- Vinti: {board.closings.won}",
        f"- Persi: {board.closings.lost}",
        f"- Tasso di conversione: {_percent(board.closings.conversion_rate)}",
        f"- Valore vinto (dichiarato dai deal, **non** fatturato): "
        f"{_euro(board.closings.won_value)}",
        f"- Chiusure attese nei 30 giorni successivi al periodo: {board.expected_closings_30_days}",
    ]
    if board.closings_not_attributable:
        lines.append(
            f"- {board.closings_not_attributable} deal chiusi prima dell'introduzione di "
            "questa misura non sono attribuibili a un periodo e non sono nei numeri sopra."
        )
    lines += ["", "## Offerte inviate in attesa di risposta", ""]
    offers = board.offers_pending[:PENDING_OFFERS_SHOWN]
    if not offers:
        lines.append("Nessuna offerta in attesa.")
    else:
        for offer in offers:
            # `None` days is not zero days: an offer whose `status_since` was never recorded is
            # of unknown age, and "ferma da 0 giorni" would read as "sent today".
            age = "data ignota" if offer.days is None else f"{offer.days} giorni"
            lines.append(f"- {offer.title} — ferma da {age}")
        lines += _and_the_rest(len(offers), board.offers_pending_total)
    lines += [
        "",
        f"Segnale: {board.accepted_offers_deal_not_won} offerte accettate il cui deal non è vinto.",
        "",
        "---",
        "",
        "Fai la revisione settimanale su questi dati. Chiedimi dei deal **fermi** — quelli "
        "nello stesso stato da troppo tempo e le offerte in attesa da più di due settimane "
        "— e non riassumere quelli che si stanno muovendo: quelli li vedo già. Se il tasso "
        "di conversione è nullo dillo, non trattarlo come zero. Non proporre azioni "
        "automatiche: elenca le domande da fare ai clienti.",
    ]
    return [_user("\n".join(lines))]


def closing_month(context: McpContext, year: int, month: int) -> list[dict[str, Any]]:
    """The list of things to do before closing a month. **No fiscal figure** (§10.1).

    `month_bounds` refuses a month outside 1-12 with a `ValidationFailed` naming `month`,
    which the server's guard renders as guidance. Validating it here as well would be a
    second rule that agrees with the first until one of them is edited.
    """
    date_from, date_to = month_bounds(year, month)
    service = DashboardService(context.session)
    economic = service.get_economic_dashboard(
        PeriodQuery(date_from=date_from, date_to=date_to), context.actor
    )
    backlog = AnalyticsService(context.session).unbilled_backlog(context.actor)

    pnl = economic.pnl
    lines = [
        f"# Chiusura mese — {year}-{month:02d}",
        "",
        "## Conto economico del periodo",
        "",
        "| Voce | Deal chiusi | Deal in corso |",
        "|---|---|---|",
        f"| Ricavi (imponibile, emesso) | {_euro(pnl.closed.revenue)} "
        f"| {_euro(pnl.in_progress.revenue)} |",
        f"| Costi diretti | {_euro(pnl.closed.direct_costs)} "
        f"| {_euro(pnl.in_progress.direct_costs)} |",
        f"| Costo del lavoro | {_euro(pnl.closed.work_cost)} "
        f"| {_euro(pnl.in_progress.work_cost)} |",
        f"| Margine lordo | {_euro(pnl.closed.gross_margin)} "
        f"| {_euro(pnl.in_progress.gross_margin)} |",
        f"| Margine % | {_percent(pnl.closed.percentage_margin)} "
        f"| {_percent(pnl.in_progress.percentage_margin)} |",
        "",
        "La cifra riportabile è la colonna **deal chiusi**. Le due colonne non si sommano: "
        "il margine di un lavoro finito e quello di uno a metà non sono la stessa cosa.",
        "",
        f"Spese generali del periodo: {_euro(pnl.general_expenses)} — **non ripartite** "
        "su nessun deal.",
        "",
        "## Da chiudere prima della chiusura",
        "",
        f"- Ore fatturabili non fatturate **nel periodo**: "
        f"{pnl.billable_uninvoiced_hours} ore, valore maturato "
        f"{_euro(pnl.accrued_value)}",
        f"- Voci senza tariffa nel periodo: {pnl.hours_without_rate}",
        f"- Arretrato **in totale** (senza periodo): "
        f"{backlog.billable_uninvoiced_hours} ore, "
        f"{_euro(backlog.accrued_value)}, di cui {backlog.entries_without_rate} voci senza "
        "tariffa",
        f"- Fatture emesse nel periodo: {economic.issued_invoices}",
        f"- Da incassare (totale con IVA, senza periodo): {_euro(economic.unpaid)}",
        f"- Di cui **scaduto**: {_euro(economic.expired)}",
        "",
        f"Periodo chiuso: {'sì' if pnl.closed_period else 'no'}. "
        f"Voci scritte in ritardo: {pnl.written_entries_overdue}.",
        "",
        "---",
        "",
        "Prepara la lista delle cose da fare prima di chiudere questo mese. Segnala solo "
        "gli scostamenti che contano: ore non fatturate, voci senza tariffa, fatture "
        "scadute. «Da incassare» è un credito, non un ricavo: non sommarlo ai ricavi e non "
        "usarlo per calcolare un margine. Se il periodo non è chiuso e ci sono voci scritte "
        "in ritardo, dì che i numeri possono ancora muoversi. Non calcolare nessuna imposta "
        "e nessun contributo: non è un dato di cui disponi.",
    ]
    return [_user("\n".join(lines))]


def hours_to_log(context: McpContext, week: str | None = None) -> list[dict[str, Any]]:
    """The most useful prompt in the product (§10): it attacks "I never entered Tuesday".

    `week` is accepted and refused for anything but the current week rather than
    silently ignored: `get_operational_dashboard` is the current week by construction (§6),
    and an argument the prompt advertised and answered with a different week's figures would
    be worse than one it does not have -- the caller would believe it worked.

    `days_without_hours` is the field, not a set difference computed here. The service already
    knows which days have no *entry*, which is not the same statement as "these hours sum to
    zero", and re-deriving it from `days` would silently pick the second reading.
    """
    board = DashboardService(context.session).get_operational_dashboard(context.actor)
    current = board.week
    if week is not None and week != current.date_from.isoformat():
        return [
            _user(
                f"La dashboard operativa copre solo la settimana corrente "
                f"({current.date_from} → {current.date_to}); la settimana richiesta ({week}) non è "
                "disponibile. Per le ore di una settimana passata usa il rapporto ore."
            )
        ]

    lines = [
        f"# Ore da registrare — settimana {current.date_from} → {current.date_to}",
        "",
        "| Giorno | Ore |",
        "|---|---|",
    ]
    for day in current.days:
        lines.append(f"| {day.day} | {day.hours} |")
    lines += ["", f"Totale settimana: {current.total_hours} ore.", ""]
    if current.days_without_hours:
        lines.append("**Giorni senza nessuna ora registrata:**")
        lines += [f"- {day}" for day in current.days_without_hours]
    else:
        lines.append("Nessun giorno scoperto: la settimana è completa.")

    lines += ["", "## Deal su cui si è lavorato di recente", ""]
    # Names, not identifiers: a list of UUIDs is a section nobody can answer from. The feed
    # carries fifty rows and can name the same deal many times, so the names are resolved in
    # one batch, deduplicated in feed order and capped.
    feed = [a.entity_id for a in board.recent_task if a.entity_type == "deal"]
    deals = DealService(context.session).get_many(set(feed), context.actor)
    seen: list[str] = []
    for deal_id in feed:
        deal = deals.get(deal_id)
        if deal is None or deal.name in seen:
            continue
        seen.append(deal.name)
        if len(seen) == RECENT_DEALS_SHOWN:
            break
    if not seen:
        lines.append("Nessuna attività recente su un deal.")
    else:
        lines += [f"- {name}" for name in seen]
    lines += [
        "",
        "---",
        "",
        "Aiutami a recuperare le ore mancanti. Per ogni giorno senza ore, chiedimi cosa ho "
        "fatto — un giorno per volta, non tutti insieme — e proponi il deal più probabile "
        "fra quelli sopra. Quando ti rispondo, registra le ore con `log_time`. Non "
        "inventare né ore né deal: se non sai su cosa imputare un giorno, chiedi. Un giorno "
        "in cui non ho lavorato va lasciato vuoto, non registrato a zero.",
    ]
    return [_user("\n".join(lines))]
