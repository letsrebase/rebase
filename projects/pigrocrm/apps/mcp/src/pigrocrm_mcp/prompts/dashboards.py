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
`dashboard://commerciale?da=...` in order to have a URI to embed would be adding one without
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
from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds
from pigrocrm.core.deals.repository import DealRepository
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
    omits the line -- the same rule `PnlTotals.margine_percentuale` and
    `ClosedInPeriod.tasso_conversione` are both typed `Decimal | None` to express.
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


def revisione_pipeline(
    context: McpContext, da: str | None = None, a: str | None = None
) -> list[dict[str, Any]]:
    """The weekly review. Posture: ask about the deals that have not moved.

    The pending offers come from `board.offerte_in_attesa` rather than from a second call to
    `DocumentRepository.pending_offers`: the dashboard already read them inside its one
    `REPEATABLE READ` transaction, and a second read here would be a second instant -- a
    briefing whose list and whose count could disagree with each other by construction.
    """
    query = PeriodoQuery(
        da=date.fromisoformat(da) if da else None,
        a=date.fromisoformat(a) if a else None,
    )
    board = DashboardService(context.session).get_commercial_dashboard(query, context.actor)

    lines = [
        f"# Revisione pipeline — {board.periodo.da} → {board.periodo.a}",
        "",
        "## Pipeline per stato",
        "",
        "| Stato | Deal | Valore | Senza valore | Valore ponderato (stima) |",
        "|---|---|---|---|---|",
    ]
    for row in board.pipeline:
        lines.append(
            f"| {row.stage_nome} | {row.numero} | {_euro(row.valore_totale)} "
            f"| {row.senza_valore} | {_euro(row.valore_ponderato)} |"
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
        f"- Vinti: {board.chiusure.vinti}",
        f"- Persi: {board.chiusure.persi}",
        f"- Tasso di conversione: {_percent(board.chiusure.tasso_conversione)}",
        f"- Valore vinto (dichiarato dai deal, **non** fatturato): "
        f"{_euro(board.chiusure.valore_vinto)}",
        f"- Chiusure attese nei 30 giorni successivi al periodo: "
        f"{board.chiusure_previste_30_giorni}",
    ]
    if board.chiusure_non_attribuibili:
        lines.append(
            f"- {board.chiusure_non_attribuibili} deal chiusi prima dell'introduzione di "
            "questa misura non sono attribuibili a un periodo e non sono nei numeri sopra."
        )
    lines += ["", "## Offerte inviate in attesa di risposta", ""]
    offers = board.offerte_in_attesa[:PENDING_OFFERS_SHOWN]
    if not offers:
        lines.append("Nessuna offerta in attesa.")
    else:
        for offer in offers:
            # `None` days is not zero days: an offer whose `stato_dal` was never recorded is
            # of unknown age, and "ferma da 0 giorni" would read as "sent today".
            age = "data ignota" if offer.giorni is None else f"{offer.giorni} giorni"
            lines.append(f"- {offer.titolo} — ferma da {age}")
        lines += _and_the_rest(len(offers), board.offerte_in_attesa_totale)
    lines += [
        "",
        f"Segnale: {board.offerte_accettate_deal_non_vinto} offerte accettate il cui deal "
        "non è vinto.",
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


def chiusura_mese(context: McpContext, anno: int, mese: int) -> list[dict[str, Any]]:
    """The list of things to do before closing a month. **No fiscal figure** (§10.1).

    `month_bounds` refuses a month outside 1-12 with a `ValidationFailed` naming `mese`,
    which the server's guard renders as guidance. Validating it here as well would be a
    second rule that agrees with the first until one of them is edited.
    """
    da, a = month_bounds(anno, mese)
    service = DashboardService(context.session)
    economic = service.get_economic_dashboard(PeriodoQuery(da=da, a=a), context.actor)
    backlog = AnalyticsService(context.session).unbilled_backlog(context.actor)

    pnl = economic.pnl
    lines = [
        f"# Chiusura mese — {anno}-{mese:02d}",
        "",
        "## Conto economico del periodo",
        "",
        "| Voce | Deal chiusi | Deal in corso |",
        "|---|---|---|",
        f"| Ricavi (imponibile, emesso) | {_euro(pnl.chiusi.ricavi)} "
        f"| {_euro(pnl.in_corso.ricavi)} |",
        f"| Costi diretti | {_euro(pnl.chiusi.costi_diretti)} "
        f"| {_euro(pnl.in_corso.costi_diretti)} |",
        f"| Costo del lavoro | {_euro(pnl.chiusi.costo_lavoro)} "
        f"| {_euro(pnl.in_corso.costo_lavoro)} |",
        f"| Margine lordo | {_euro(pnl.chiusi.margine_lordo)} "
        f"| {_euro(pnl.in_corso.margine_lordo)} |",
        f"| Margine % | {_percent(pnl.chiusi.margine_percentuale)} "
        f"| {_percent(pnl.in_corso.margine_percentuale)} |",
        "",
        "La cifra riportabile è la colonna **deal chiusi**. Le due colonne non si sommano: "
        "il margine di un lavoro finito e quello di uno a metà non sono la stessa cosa.",
        "",
        f"Spese generali del periodo: {_euro(pnl.spese_generali)} — **non ripartite** "
        "su nessun deal.",
        "",
        "## Da chiudere prima della chiusura",
        "",
        f"- Ore fatturabili non fatturate **nel periodo**: "
        f"{pnl.ore_fatturabili_non_fatturate} ore, valore maturato "
        f"{_euro(pnl.valore_maturato)}",
        f"- Voci senza tariffa nel periodo: {pnl.ore_senza_tariffa}",
        f"- Arretrato **in totale** (senza periodo): "
        f"{backlog.ore_fatturabili_non_fatturate} ore, "
        f"{_euro(backlog.valore_maturato)}, di cui {backlog.voci_senza_tariffa} voci senza "
        "tariffa",
        f"- Fatture emesse nel periodo: {economic.fatture_emesse}",
        f"- Da incassare (totale con IVA, senza periodo): {_euro(economic.da_incassare)}",
        f"- Di cui **scaduto**: {_euro(economic.scaduto)}",
        "",
        f"Periodo chiuso: {'sì' if pnl.periodo_chiuso else 'no'}. "
        f"Voci scritte in ritardo: {pnl.voci_scritte_in_ritardo}.",
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


def ore_da_registrare(context: McpContext, settimana: str | None = None) -> list[dict[str, Any]]:
    """The most useful prompt in the product (§10): it attacks "I never entered Tuesday".

    `settimana` is accepted and refused for anything but the current week rather than
    silently ignored: `get_operational_dashboard` is the current week by construction (§6),
    and an argument the prompt advertised and answered with a different week's figures would
    be worse than one it does not have -- the caller would believe it worked.

    `giorni_senza_ore` is the field, not a set difference computed here. The service already
    knows which days have no *entry*, which is not the same statement as "these hours sum to
    zero", and re-deriving it from `giorni` would silently pick the second reading.
    """
    board = DashboardService(context.session).get_operational_dashboard(context.actor)
    week = board.settimana
    if settimana is not None and settimana != week.da.isoformat():
        return [
            _user(
                f"La dashboard operativa copre solo la settimana corrente "
                f"({week.da} → {week.a}); la settimana richiesta ({settimana}) non è "
                "disponibile. Per le ore di una settimana passata usa il rapporto ore."
            )
        ]

    lines = [
        f"# Ore da registrare — settimana {week.da} → {week.a}",
        "",
        "| Giorno | Ore |",
        "|---|---|",
    ]
    for day in week.giorni:
        lines.append(f"| {day.giorno} | {day.ore} |")
    lines += ["", f"Totale settimana: {week.ore_totali} ore.", ""]
    if week.giorni_senza_ore:
        lines.append("**Giorni senza nessuna ora registrata:**")
        lines += [f"- {day}" for day in week.giorni_senza_ore]
    else:
        lines.append("Nessun giorno scoperto: la settimana è completa.")

    lines += ["", "## Deal su cui si è lavorato di recente", ""]
    # Names, not identifiers. A list of UUIDs is a section nobody can answer from, so it
    # would be tax with no context in it -- which is the whole failure §10 is about. The
    # feed carries fifty rows (fifteen for an agent) and can name the same deal many times,
    # so it is deduplicated
    # in order and capped before any row is resolved: the number of primary-key lookups is
    # bounded by the constant, not by the length of the feed.
    seen: list[str] = []
    deals = DealRepository(context.session)
    for activity in board.attivita_recenti:
        if activity.entity_type != "deal":
            continue
        deal = deals.get(activity.entity_id)
        if deal is None or deal.nome in seen:
            continue
        seen.append(deal.nome)
        if len(seen) == RECENT_DEALS_SHOWN:
            break
    if not seen:
        lines.append("Nessuna attività recente su un deal.")
    else:
        lines += [f"- {nome}" for nome in seen]
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
