from uuid import UUID

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.provenance import PROVENIENZA_TESTO_LIBERO


def _timeline_lines(context: McpContext, entity: str, entity_id: UUID) -> list[str]:
    entries = ActivityService(context.session).timeline(entity, entity_id, limit=10)
    if not entries:
        return ["_Nessuna attività registrata._"]
    return [f"- {e.occurred_at:%Y-%m-%d %H:%M} · **{e.kind}** · da {e.actor_type}" for e in entries]


def _custom_lines(custom_fields: dict[str, object]) -> list[str]:
    if not custom_fields:
        return []
    return ["", "## Campi personalizzati", "", f"_Provenienza: {PROVENIENZA_TESTO_LIBERO}_", ""] + [
        f"- **{key}**: {value}" for key, value in sorted(custom_fields.items())
    ]


def _or_dash(value: object) -> str:
    """`0`/`Decimal("0")`/`False` are real values, not "unset" -- a bare
    `value or '—'` ternary treats them identically to `None` because all three
    are falsy in Python. A deal worth exactly zero (pro bono, a full discount)
    would then render indistinguishably from a deal where nobody ever filled
    the field in, and a model reading this card before acting would draw the
    wrong conclusion from the dash. Mirrors `fields.validator.is_blank`'s
    reasoning for custom fields, applied here to the native numeric ones: only
    `None` means "nothing was provided". Not used for text fields -- an empty
    string and "not provided" are the same thing to show a reader there, so the
    plain `or '—'` ternary stays correct for those.
    """
    return "—" if value is None else str(value)


def _address_line(customer: CustomerRead) -> str:
    """Joins only the parts that are actually present.

    A customer with no street/CAP/comune/provincia used to render as
    `—,   () IT` -- a literal dash, a comma, two blank spaces where CAP and
    comune would go, and empty parentheses -- noise a model has to read past
    before it can act. With nothing but a country, this renders as `IT` alone.
    """
    locality = " ".join(part for part in (customer.cap, customer.comune) if part)
    if customer.provincia:
        locality = f"{locality} ({customer.provincia})".strip()
    if customer.nazione:
        locality = f"{locality} {customer.nazione}".strip()
    segments = [part for part in (customer.indirizzo, locality) if part]
    return ", ".join(segments) if segments else "—"


def render_customer(context: McpContext, customer_id: UUID) -> str:
    customer = CustomerService(context.session).get(customer_id, context.actor)
    people = PersonService(context.session).list(
        PersonListQuery(customer_id=customer_id, limit=50), context.actor
    )
    from pigrocrm.core.deals.schemas import DealListQuery

    deals = DealService(context.session).list(
        DealListQuery(customer_id=customer_id, limit=50), context.actor
    )

    lines = [
        f"# {customer.ragione_sociale}",
        "",
        "## Dati fiscali",
        "",
        f"- P.IVA: {customer.partita_iva or '—'}",
        f"- Codice fiscale: {customer.codice_fiscale or '—'}",
        f"- Codice SDI: {customer.codice_sdi or '—'}",
        f"- PEC: {customer.pec or '—'}",
        f"- Indirizzo: {_address_line(customer)}",
        "",
        "## Contatti",
        "",
    ]
    lines += [
        f"- {p.nome} {p.cognome or ''} — {p.ruolo or 'ruolo non indicato'} — {p.email or '—'}"
        for p in people.items
    ] or ["_Nessun contatto._"]
    lines += ["", "## Deal", ""]
    lines += [
        f"- {d.nome} — valore previsto {_or_dash(d.valore_previsto)} — probabilità {d.probabilita}%"
        for d in deals.items
    ] or ["_Nessun deal._"]
    lines += _custom_lines(customer.custom_fields)
    lines += ["", "## Timeline", ""] + _timeline_lines(context, "customer", customer_id)
    if customer.note:
        lines += [
            "",
            "## Note",
            "",
            f"_Provenienza: {PROVENIENZA_TESTO_LIBERO}_",
            "",
            customer.note,
        ]
    return "\n".join(lines)


def render_person(context: McpContext, person_id: UUID) -> str:
    person = PersonService(context.session).get(person_id, context.actor)
    lines = [
        f"# {person.nome} {person.cognome or ''}".strip(),
        "",
        f"- Ruolo: {person.ruolo or '—'}",
        f"- Email: {person.email or '—'}",
        f"- Telefono: {person.telefono or '—'}",
        f"- LinkedIn: {person.linkedin or '—'}",
    ]
    if person.customer_id:
        customer = CustomerService(context.session).get(person.customer_id, context.actor)
        lines.append(f"- Cliente: {customer.ragione_sociale} (`customer://{customer.id}`)")
    else:
        lines.append("- Cliente: non associato")
    lines += _custom_lines(person.custom_fields)
    lines += ["", "## Timeline", ""] + _timeline_lines(context, "person", person_id)
    if person.note:
        lines += ["", "## Note", "", f"_Provenienza: {PROVENIENZA_TESTO_LIBERO}_", "", person.note]
    return "\n".join(lines)


def render_deal(context: McpContext, deal_id: UUID) -> str:
    deal = DealService(context.session).get(deal_id, context.actor)
    customer = CustomerService(context.session).get(deal.customer_id, context.actor)

    from pigrocrm.core.pipeline.service import PipelineService

    stage = PipelineService(context.session).get(deal.pipeline_stage_id)

    lines = [
        f"# {deal.nome}",
        "",
        f"- Cliente: {customer.ragione_sociale} (`customer://{customer.id}`)",
        f"- Stato: {stage.nome} ({stage.tipo})",
        f"- Valore previsto: {_or_dash(deal.valore_previsto)}",
        f"- Probabilità: {deal.probabilita}%",
        f"- Chiusura prevista: {deal.data_chiusura_prevista or '—'}",
        f"- Ore preventivate: {_or_dash(deal.ore_preventivate)}",
        f"- Valore preventivato: {_or_dash(deal.valore_preventivato)}",
    ]
    lines += _custom_lines(deal.custom_fields)

    # Reading before acting (slice 1 §8.4), applied to the one thing an agent wants to
    # know before logging an hour. Still no revenue figure in *this* block: revenue is
    # the invoice (§3, decision 2), and the hours half has to be readable on a deal that
    # has none. The economics live in their own section below.
    summary = TimeEntryService(context.session).deal_summary(deal.id, context.actor)
    lines += [
        "",
        "## Ore",
        f"- Stato: **{summary.stato}**",
        f"- Ore consuntivate: {summary.ore_totali}",
        f"- Ore fatturabili non ancora fatturate: {summary.ore_fatturabili_non_fatturate}",
        f"- Valore delle ore non fatturate (stima): {summary.valore_ore_non_fatturate} EUR",
    ]
    if summary.ore_senza_tariffa:
        lines.append(
            f"- Voci senza tariffa: {summary.ore_senza_tariffa} "
            "(escluse dal valore maturato e dal margine)"
        )

    # 4A deliberately printed no revenue here, because there were no invoices to read and
    # a zero would have looked like a real figure. There are now: `deal_pnl` reads the
    # issued ones and this block is the payoff -- an agent asked "how is this project
    # going" sees the economics in the same read as the hours, instead of inferring them.
    pnl = AnalyticsService(context.session).deal_pnl(deal.id, context.actor)
    lines += [
        "",
        "## Economia",
        f"- Ricavi fatturati: {pnl.ricavi} EUR ({pnl.fatture_emesse} fatture emesse)",
        f"- Costi diretti: {pnl.costi_diretti} EUR",
        f"- Costo del lavoro: {pnl.costo_lavoro} EUR",
    ]
    if pnl.stato == "chiuso":
        lines.append(f"- Margine lordo: {pnl.margine_lordo} EUR (definitivo)")
    else:
        # Never a margin without the qualifier on an unfinished deal: the figure is
        # provisional, and an agent handed a bare number will quote it as final.
        lines.append(f"- Margine lordo: {pnl.margine_lordo} EUR — **provvisorio**")
        lines.append(f"- Valore maturato (stima, non un ricavo): {pnl.valore_maturato} EUR")
    if pnl.margine_percentuale is None:
        # `null` is not zero per cent: nothing has been invoiced yet, which is a
        # different sentence from "every euro earned went out in costs".
        lines.append("- Margine %: non calcolabile, nessun ricavo fatturato")
    else:
        lines.append(f"- Margine %: {pnl.margine_percentuale}")

    lines += ["", "## Timeline", ""] + _timeline_lines(context, "deal", deal_id)
    if deal.note:
        lines += ["", "## Note", "", f"_Provenienza: {PROVENIENZA_TESTO_LIBERO}_", "", deal.note]
    return "\n".join(lines)
