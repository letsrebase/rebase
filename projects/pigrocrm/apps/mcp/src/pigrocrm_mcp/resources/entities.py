from uuid import UUID

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealListQuery, DealRead, DealValueLineRead
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.labels import ACTIVITY_KIND, NOTE_SOURCE, label
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE

_DEAL_HOURS_STATUS = {"in_progress": "in progress", "to_invoice": "to invoice", "closed": "closed"}


def _timeline_lines(context: McpContext, entity: str, entity_id: UUID) -> list[str]:
    entries = ActivityService(context.session).timeline(entity, entity_id, limit=10)
    if not entries:
        return ["_No activity recorded._"]
    return [
        f"- {e.occurred_at:%Y-%m-%d %H:%M} · **{label(ACTIVITY_KIND, e.kind)}** · by {e.actor_type}"
        for e in entries
    ]


def _custom_lines(custom_fields: dict[str, object]) -> list[str]:
    if not custom_fields:
        return []
    return ["", "## Custom fields", ""] + [
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
        who = note.author or (
            "Earlier note" if note.source is None else label(NOTE_SOURCE, note.source)
        )
        header = f"**{note.created_at:%Y-%m-%d %H:%M}** · {who}"
        if note.source is not None:
            header += f" · source: {label(NOTE_SOURCE, note.source)}"
        if note.edited_at is not None:
            header += f" · edited on {note.edited_at:%Y-%m-%d %H:%M}"
            if note.edited_by:
                header += f" by {note.edited_by}"
        lines += ["", header, "", note.text]
    return lines


def _figures(deal: DealRead) -> str:
    """The two figures of a deal's lines, beside its total: what is paid once and what
    arrives every month. Empty for a deal with no lines."""
    if deal.one_off_value is None or deal.monthly_recurring_value is None:
        return ""
    return (
        f" (one-off {deal.one_off_value}, monthly recurring {deal.monthly_recurring_value};"
        " an open-ended fee counts 12 months)"
    )


def _value_line(line: DealValueLineRead) -> str:
    quantity = "" if line.unit == "flat" else f" × {line.quantity}"
    start = f" from {line.start}" if line.start else ""
    if line.duration_months:
        duration = f" for {line.duration_months} months"
    else:
        duration = " open-ended" if line.delivery_period != "one_off" else ""
    label = f"{line.description}: " if line.description else ""
    return (
        f"- {label}{line.unit_price}{quantity} per {line.unit}, {line.delivery_period}"
        f"{start}{duration}"
    )


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

    A customer with no street/postal code/city/province used to render as
    `—,   () IT` -- a literal dash, a comma, two blank spaces where postal code and
    city would go, and empty parentheses -- noise a model has to read past
    before it can act. With nothing but a country, this renders as `IT` alone.
    """
    locality = " ".join(part for part in (customer.postal_code, customer.city) if part)
    if customer.province:
        locality = f"{locality} ({customer.province})".strip()
    if customer.country:
        locality = f"{locality} {customer.country}".strip()
    segments = [part for part in (customer.address, locality) if part]
    return ", ".join(segments) if segments else "—"


def _provenance_line() -> str:
    """First line of every card: the names, notes and custom fields below were written by
    somebody else (a person, or an agent that was reading a mail)."""
    return f"_Provenance: {PROVENANCE_TEXT_FREE}_"


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
        "## Tax data",
        "",
        f"- VAT number: {customer.vat_number or '—'}",
        f"- Tax code: {customer.tax_code or '—'}",
        f"- Recipient code (SdI): {customer.sdi_code or '—'}",
        f"- PEC: {customer.certified_email or '—'}",
        f"- Address: {_address_line(customer)}",
        "",
        "## Contacts",
        "",
    ]
    lines += [
        f"- {p.first_name} {p.last_name or ''} — {p.role or 'role not given'} — {p.email or '—'}"
        for p in people.items
    ] or ["_No contacts._"]
    lines += ["", "## Deal", ""]
    lines += [
        f"- {d.name} — expected value {_or_dash(d.expected_value)}{_figures(d)}"
        f" — probability {d.probability}%"
        for d in deals.items
    ] or ["_No deals._"]
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
        f"- Role: {person.role or '—'}",
        f"- Email: {person.email or '—'}",
        f"- Phone: {person.phone or '—'}",
        f"- LinkedIn: {person.linkedin or '—'}",
    ]
    if person.customer_id:
        customer = CustomerService(context.session).get(person.customer_id, context.actor)
        lines.append(f"- Customer: {customer.legal_name} (`customer://{customer.id}`)")
    else:
        lines.append("- Customer: not linked")
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
        f"- Customer: {customer.legal_name} (`customer://{customer.id}`)",
        f"- Stage: {stage.name} ({stage.type})",
        f"- Expected value: {_or_dash(deal.expected_value)}",
        f"- Probability: {deal.probability}%",
        f"- Expected close: {deal.expected_close_date or '—'}",
        f"- Budgeted hours: {_or_dash(deal.budgeted_hours)}",
        f"- Budgeted value: {_or_dash(deal.budgeted_value)}",
    ]
    if deal.value_lines:
        lines += ["", "## Value lines", ""]
        lines += [_value_line(line) for line in deal.value_lines]
        lines += [
            f"- One-off value: {_or_dash(deal.one_off_value)}",
            f"- Monthly recurring value: {_or_dash(deal.monthly_recurring_value)}",
            "- The expected value counts an open-ended fee at 12 months.",
        ]
    lines += _custom_lines(deal.custom_fields)

    # Reading before acting (slice 1 §8.4), applied to the one thing an agent wants to
    # know before logging an hour. Still no revenue figure in *this* block: revenue is
    # the invoice (§3, decision 2), and the hours half has to be readable on a deal that
    # has none. The economics live in their own section below.
    summary = TimeEntryService(context.session).deal_summary(deal.id, context.actor)
    lines += [
        "",
        "## Hours",
        f"- Status: **{_DEAL_HOURS_STATUS.get(summary.status, summary.status)}**",
        f"- Hours logged: {summary.total_hours}",
        f"- Billable hours not yet invoiced: {summary.billable_uninvoiced_hours}",
        f"- Value of uninvoiced hours (estimate): {summary.unbilled_hours_value} EUR",
    ]
    if summary.hours_without_rate:
        lines.append(
            f"- Entries without a rate: {summary.hours_without_rate} "
            "(excluded from the accrued value and the margin)"
        )

    # 4A deliberately printed no revenue here, because there were no invoices to read and
    # a zero would have looked like a real figure. There are now: `deal_pnl` reads the
    # issued ones and this block is the payoff -- an agent asked "how is this project
    # going" sees the economics in the same read as the hours, instead of inferring them.
    pnl = AnalyticsService(context.session).deal_pnl(deal.id, context.actor)
    lines += [
        "",
        "## Economics",
        f"- Invoiced revenue: {pnl.revenue} EUR ({pnl.issued_invoices} invoices issued)",
        f"- Direct costs: {pnl.direct_costs} EUR",
        f"- Labor cost: {pnl.work_cost} EUR",
    ]
    if pnl.status == "closed":
        lines.append(f"- Gross margin: {pnl.gross_margin} EUR (final)")
    else:
        # Never a margin without the qualifier on an unfinished deal: the figure is
        # provisional, and an agent handed a bare number will quote it as final.
        lines.append(f"- Gross margin: {pnl.gross_margin} EUR — **provisional**")
        lines.append(f"- Accrued value (estimate, not revenue): {pnl.accrued_value} EUR")
    if pnl.percentage_margin is None:
        # `null` is not zero per cent: nothing has been invoiced yet, which is a
        # different sentence from "every euro earned went out in costs".
        lines.append("- Margin %: not computable, no revenue invoiced")
    else:
        lines.append(f"- Margin %: {pnl.percentage_margin}")

    lines += ["", "## Timeline", ""] + _timeline_lines(context, "deal", deal_id)
    lines += _note_lines(context, "deal", deal_id)
    return "\n".join(lines)
