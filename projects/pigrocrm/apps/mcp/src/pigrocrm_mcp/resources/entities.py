from uuid import UUID

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE

_DEAL_HOURS_STATUS = {"in_progress": "in corso", "to_invoice": "da fatturare", "closed": "chiuso"}


def _timeline_lines(context: McpContext, entity: str, entity_id: UUID) -> list[str]:
    entries = ActivityService(context.session).timeline(entity, entity_id, limit=10)
    if not entries:
        return ["_Nessuna attività registrata._"]
    return [f"- {e.occurred_at:%Y-%m-%d %H:%M} · **{e.kind}** · da {e.actor_type}" for e in entries]


def _custom_lines(custom_fields: dict[str, object]) -> list[str]:
    if not custom_fields:
        return []
    return ["", "## Campi personalizzati", ""] + [
        f"- **{key}**: {value}" for key, value in sorted(custom_fields.items())
    ]


def _note_lines(context: McpContext, entity_type: str, entity_id: UUID) -> list[str]:
    """The whole thread, newest first (the tools carry the latest 50): who wrote each note,
    when, through which door and whether it was edited since."""
    notes = NoteService(context.session).list_for_entity(entity_type, entity_id, context.actor)
    if not notes:
        return []
    lines = ["", "## Note"]
    for note in notes:
        # A note from before the thread existed has no source and no author.
        who = note.author or ("Nota precedente" if note.source is None else note.source)
        header = f"**{note.created_at:%Y-%m-%d %H:%M}** · {who}"
        if note.source is not None:
            header += f" · fonte: {note.source}"
        if note.edited_at is not None:
            header += f" · modificata il {note.edited_at:%Y-%m-%d %H:%M}"
            if note.edited_by:
                header += f" da {note.edited_by}"
        lines += ["", header, "", note.text]
    return lines


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
    locality = " ".join(part for part in (customer.cap, customer.city) if part)
    if customer.province:
        locality = f"{locality} ({customer.province})".strip()
    if customer.country:
        locality = f"{locality} {customer.country}".strip()
    segments = [part for part in (customer.address, locality) if part]
    return ", ".join(segments) if segments else "—"


def _provenance_line() -> str:
    """First line of every card: the names, notes and custom fields below were written by
    somebody else (a person, or an agent that was reading a mail)."""
    return f"_Provenienza: {PROVENANCE_TEXT_FREE}_"


def render_customer(context: McpContext, customer_id: UUID) -> str:
    customer = CustomerService(context.session).get(customer_id, context.actor)
    people = PersonService(context.session).list(
        PersonListQuery(customer_id=customer_id, limit=50), context.actor
    )

    deals = DealService(context.session).list(
        DealListQuery(customer_id=customer_id, limit=50), context.actor
    )

    lines = [
        _provenance_line(),
        "",
        f"# {customer.legal_name}",
        "",
        "## Dati fiscali",
        "",
        f"- P.IVA: {customer.vat_number or '—'}",
        f"- Codice fiscale: {customer.tax_code or '—'}",
        f"- Codice SDI: {customer.sdi_code or '—'}",
        f"- PEC: {customer.certified_email or '—'}",
        f"- Indirizzo: {_address_line(customer)}",
        "",
        "## Contatti",
        "",
    ]
    lines += [
        f"- {p.first_name} {p.last_name or ''} — {p.role or 'ruolo non indicato'} — "
        f"{p.email or '—'}"
        for p in people.items
    ] or ["_Nessun contatto._"]
    lines += ["", "## Deal", ""]
    lines += [
        f"- {d.name} — valore previsto {_or_dash(d.expected_value)} — probabilità {d.probability}%"
        for d in deals.items
    ] or ["_Nessun deal._"]
    lines += _custom_lines(customer.custom_fields)
    lines += ["", "## Timeline", ""] + _timeline_lines(context, "customer", customer_id)
    lines += _note_lines(context, "customer", customer_id)
    return "\n".join(lines)


def render_person(context: McpContext, person_id: UUID) -> str:
    person = PersonService(context.session).get(person_id, context.actor)
    lines = [
        _provenance_line(),
        "",
        f"# {person.first_name} {person.last_name or ''}".strip(),
        "",
        f"- Ruolo: {person.role or '—'}",
        f"- Email: {person.email or '—'}",
        f"- Telefono: {person.phone or '—'}",
        f"- LinkedIn: {person.linkedin or '—'}",
    ]
    if person.customer_id:
        customer = CustomerService(context.session).get(person.customer_id, context.actor)
        lines.append(f"- Cliente: {customer.legal_name} (`customer://{customer.id}`)")
    else:
        lines.append("- Cliente: non associato")
    lines += _custom_lines(person.custom_fields)
    lines += ["", "## Timeline", ""] + _timeline_lines(context, "person", person_id)
    lines += _note_lines(context, "person", person_id)
    return "\n".join(lines)


def render_deal(context: McpContext, deal_id: UUID) -> str:
    deal = DealService(context.session).get(deal_id, context.actor)
    customer = CustomerService(context.session).get(deal.customer_id, context.actor)

    stage = PipelineService(context.session).get(deal.pipeline_stage_id)

    lines = [
        _provenance_line(),
        "",
        f"# {deal.name}",
        "",
        f"- Cliente: {customer.legal_name} (`customer://{customer.id}`)",
        f"- Stato: {stage.name} ({stage.type})",
        f"- Valore previsto: {_or_dash(deal.expected_value)}",
        f"- Probabilità: {deal.probability}%",
        f"- Chiusura prevista: {deal.expected_close_date or '—'}",
        f"- Ore preventivate: {_or_dash(deal.budgeted_hours)}",
        f"- Valore preventivato: {_or_dash(deal.budgeted_value)}",
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
        f"- Stato: **{_DEAL_HOURS_STATUS.get(summary.status, summary.status)}**",
        f"- Ore consuntivate: {summary.total_hours}",
        f"- Ore fatturabili non ancora fatturate: {summary.billable_uninvoiced_hours}",
        f"- Valore delle ore non fatturate (stima): {summary.unbilled_hours_value} EUR",
    ]
    if summary.hours_without_rate:
        lines.append(
            f"- Voci senza tariffa: {summary.hours_without_rate} "
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
        f"- Ricavi fatturati: {pnl.revenue} EUR ({pnl.issued_invoices} fatture emesse)",
        f"- Costi diretti: {pnl.direct_costs} EUR",
        f"- Costo del lavoro: {pnl.work_cost} EUR",
    ]
    if pnl.status == "closed":
        lines.append(f"- Margine lordo: {pnl.gross_margin} EUR (definitivo)")
    else:
        # Never a margin without the qualifier on an unfinished deal: the figure is
        # provisional, and an agent handed a bare number will quote it as final.
        lines.append(f"- Margine lordo: {pnl.gross_margin} EUR — **provvisorio**")
        lines.append(f"- Valore maturato (stima, non un ricavo): {pnl.accrued_value} EUR")
    if pnl.percentage_margin is None:
        # `null` is not zero per cent: nothing has been invoiced yet, which is a
        # different sentence from "every euro earned went out in costs".
        lines.append("- Margine %: non calcolabile, nessun ricavo fatturato")
    else:
        lines.append(f"- Margine %: {pnl.percentage_margin}")

    lines += ["", "## Timeline", ""] + _timeline_lines(context, "deal", deal_id)
    lines += _note_lines(context, "deal", deal_id)
    return "\n".join(lines)
