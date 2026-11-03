"""One week of a space, as one object with no HTML in it (spec 2026-09-16 §3.2).

Pydantic and nothing else: the mail renders this (§3.5), the command prints it (§3.4),
and the tests read it. A schema that carried a fragment of markup would make the tests
assertions about a rendering, and the first change to the mail's telaio would break every
one of them.

**Every figure here arrives already computed.** Not one field is derived from another,
and the two properties at the bottom are predicates over fields rather than sums of them:
`DigestService` copies each number from the repository or the service that owns it, so
there is no second place a total of this report can be worked out and disagree. The same
rule `core/dashboard/` is held to by an AST scan, applied here by construction --
`won_to_invoice` is the dashboard's own count, `accrued_value` is
`AnalyticsService`'s, and the two month totals are two calls to one register sum with two
windows.

Italian field names, like every other schema in this package: the reader of the mail and
the reader of this file are the same person.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from pigrocrm.core.dashboard.schemas import WeekHours


class DigestInvoice(BaseModel):
    """One invoice line, whichever of the four sections it appears in.

    One model for «scadute», «in scadenza», «emesse» and «incassate» rather than four,
    because the mail prints the same five things about each -- number, client, amount,
    a date and a state -- and four near-identical models would be four places to forget
    the client's name. What differs is only *which* date `data` carries, and that is
    named on the field.

    `number` is `invoices.naming.numero_completo(anno, numero)`, the string printed on the
    document the client is holding, never the bare progressive: two invoices from
    different years are indistinguishable by progressive alone, which is the defect that
    function exists to have fixed once.
    """

    invoice_id: UUID
    number: str
    customer: str
    amount: Decimal = Field(max_digits=12, decimal_places=2)
    # Emission, collection or due date, depending on the list this row is in: the date the
    # section is *about*. An invoice issued in July and collected this week belongs to the
    # week by its `payment_date`, and printing its emission date beside it would read as
    # the wrong week.
    date: dt.date
    status: str
    payment_status: str
    # `trasmessa_esternamente_il is not None`: the document has been deposited with the
    # user's own intermediary. Carried as a bool and not as the date, because the only
    # thing the report does with it is print the word «trasmessa» (§3.1 item 3) -- and
    # `status` cannot say it, holding as it does the register's own two state machines.
    transmitted: bool = False
    # Only «scadute» fills this in. `None` and not `0` everywhere else: zero days late is
    # a thing an invoice can be (due today), and it is not what an issued invoice is.
    days_of_delay: int | None = None


class DigestDealMove(BaseModel):
    """A deal that changed stage during the week (§3.1, section 6).

    `stage_name` is where it *arrived*. The stage it left is in the activity's payload too
    and is deliberately not carried: the mail's line is «X è passato a Y», and a reader
    who wants the whole path has the deal's own timeline.
    """

    deal_id: UUID
    title: str
    stage_name: str
    when: dt.date


class DigestOffer(BaseModel):
    """An offer sent and still unanswered, with its age in days (§3.1, section 6)."""

    document_id: UUID
    title: str
    days: int


class DigestStage(BaseModel):
    """One row of «In pipeline»: a stage that holds at least one open deal.

    Three fields of `PipelineStageSummary` and not the whole of it. The weighted estimate
    is on the commercial dashboard, labelled «stima» beside the caveat that explains it; a
    mail has no room for that caveat, and an estimate without one is a number that gets
    quoted as revenue.
    """

    stage_name: str
    number: int
    total_value: Decimal = Field(max_digits=12, decimal_places=2)


class DigestSignal(BaseModel):
    """One of the operational dashboard's inconsistency counts, carried verbatim.

    Only those with `conteggio > 0` reach the report (§3.1, section 7): a mail that says
    «scaduto e non incassato: 0» every Monday is a mail people stop opening.
    """

    code: str
    label: str
    count: int
    link: str


class DigestTaskLine(BaseModel):
    """One commitment line of «Da fare»: what it is, when it falls due, how late it is."""

    task_id: UUID
    title: str
    due_date: dt.date
    # Only the overdue fill this in, as `DigestInvoice.days_of_delay` does: `None` and
    # not `0`, since a commitment due today is a fact about the day and not a delay.
    days_of_delay: int | None = None


class DigestTask(BaseModel):
    """The recipient's own commitments of the week (REB-726): overdue, then the next seven
    days, today included.

    **Per recipient, not per scope.** Every other section is the space's (or the
    scope's) and is built once; this one is the person's, so `DigestRun` fills it in on
    a copy of the built report for each address. It is the week's view and nothing
    more: the daily summary the reminders spec adds (2026-10-26 §6.2) is a different mail
    about what has fallen due, and this section neither duplicates it nor reads its
    ledger.

    `*_totale` is how many there are; the lists are the first few, so a mail never
    grows with the backlog. Open commitments only, and none without a date: an undated
    to-do is on the Oggi page, not in a mail about the week.
    """

    overdue: list[DigestTaskLine] = Field(default_factory=list)
    overdue_total: int = 0
    in_week: list[DigestTaskLine] = Field(default_factory=list)
    in_week_total: int = 0

    @property
    def empty(self) -> bool:
        return not self.overdue_total and not self.in_week_total


class WeeklyDigest(BaseModel):
    """The week, section by section, in the order §3.1 prints them, and «da fare» (REB-726)
    after them: the one section that is a person's and not the space's.

    The field order *is* the mail's order: «da incassare», «da emettere», «emesse»,
    «incassate», «le ore», «in pipeline», «da sistemare». A renderer that walks this model
    top to bottom produces the agreed page, and a section moved in the mail has to be
    moved here first -- which is where it is visible to every test.

    Every list may be empty and every one is present: the renderer decides what to print,
    not the builder. `None` appears exactly once, on `hours`, because a week with no hours is
    a different statement from a week of zeros -- §3.1 drops the section entirely rather
    than printing a flat line.
    """

    week: str
    date_from: dt.date
    date_to: dt.date

    # 1. Da incassare.
    overdue: list[DigestInvoice]
    in_due_date: list[DigestInvoice]

    # 2. Da emettere.
    won_to_invoice: int
    hours_uninvoiced: Decimal = Field(max_digits=8, decimal_places=2)
    # `AnalyticsService`'s accrued value. Never revenue, never added to one: the revenue
    # is the invoice, and this is what has not been invoiced yet.
    accrued_value: Decimal = Field(max_digits=12, decimal_places=2)

    # 3. Issued this week, with the running month beside the one before it.
    issued: list[DigestInvoice]
    total_month_current: Decimal = Field(max_digits=12, decimal_places=2)
    total_month_previous: Decimal = Field(max_digits=12, decimal_places=2)

    # 4. Collected this week.
    paid: list[DigestInvoice]

    # 5. The hours. `None` when the week has none at all.
    hours: WeekHours | None

    # 6. In pipeline.
    pipeline: list[DigestStage]
    deal_moves: list[DigestDealMove]
    offers_pending: list[DigestOffer]

    # 7. Da sistemare.
    signals: list[DigestSignal]

    # 8. Da fare. The recipient's, so it is empty on a freshly built report and set per
    # address by `DigestRun` (see `DigestTask`).
    tasks: DigestTask = Field(default_factory=DigestTask)

    @property
    def week_stalled(self) -> bool:
        """Nothing was issued, collected, logged or moved.

        The four facts a person recognises as «I did something that week», and deliberately
        not the same test as `empty`: a space can owe money, hold a pipeline and have an
        offer out, and still have had a week in which nothing moved. That is the week §3.1
        closes with the «settimana ferma» line and the ready-made prompt, and it is the
        week the report is most worth sending.
        """
        return not self.issued and not self.paid and not self.deal_moves and self.hours is None

    @property
    def empty(self) -> bool:
        """Every list empty, every number zero, no hours: a space with nothing in it.

        §2: «silenzio per gli spazi vuoti». The command asks this and sends nothing, which
        is why it is a property of the report rather than a separate count of rows -- the
        thing that decides whether there is anything to say is the thing that would have
        said it.

        Written out field by field rather than over `model_dump()`: a field added to this
        model must be considered here on purpose, and a generic walk would silently
        absorb it and go on answering `True` for a space that had just acquired something
        to report.
        """
        return (
            not self.overdue
            and not self.in_due_date
            and not self.issued
            and not self.paid
            and not self.pipeline
            and not self.deal_moves
            and not self.offers_pending
            and not self.signals
            and self.tasks.empty
            and self.hours is None
            and self.won_to_invoice == 0
            and self.hours_uninvoiced == 0
            and self.accrued_value == 0
            and self.total_month_current == 0
            and self.total_month_previous == 0
        )
