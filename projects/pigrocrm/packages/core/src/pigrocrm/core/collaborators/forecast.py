"""The due items an engagement writes for what it pays the freelancer (collaborators design § 4,
«Due items» and «The payment rule»), kept in step with the engagement.

An `active` or `concluded` engagement has one **forecast out** (source kind `engagement`) per
billing point of its letter (`payments.billings`), dated by its payment rule
(`payments.due_terms`). Anything else (a draft, an engagement out for signature, a cancelled one)
has none. `sync` is idempotent: it finds the forecasts it wrote by their key, drops the ones the
engagement no longer says, rewrites the ones whose amount or date moved and adds the missing
ones, only for billing points whose legal backstop (60 days) has not passed yet: an elapsed one
is left to whatever settles it.

An `on_collection` forecast is **paired** with the client's due item: the sales invoice of the
engagement's deal or contract whose accrual period covers the billing period, or one issued in
the month after it when the invoice states no period. The freelancer's item then follows that
item's settlement (`attach_dependency`); with none yet it waits and its backstop dates it, and
issuing the client's invoice pairs it (`pair_invoice`). A fixed term depends on nothing.

The purchase that replaces a forecast, the withholding and the net wait for the purchases
cards. None of this commits: the caller's transaction is the engagement's, or the invoice's.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators import payments
from pigrocrm.core.collaborators.models import PAYMENT_DAYS_LIMIT, Engagement
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import today_local
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.sales import join_sales_item
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.work_units.models import WorkUnit

# The states in which an engagement owes the freelancer money.
PAYING_STATES: tuple[str, ...] = ("active", "concluded")
log = logging.getLogger(__name__)
DROPPED_REASON = "L'incarico non prevede piu' questo compenso"


@dataclass(frozen=True)
class SyncResult:
    created: int
    dropped: int
    paired: int
    # Engagements `refresh_all` could not sync; the others were.
    failed: int = 0

    def __bool__(self) -> bool:
        return bool(self.created or self.dropped or self.paired or self.failed)


class EngagementForecastService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.items = DueItemService(session)
        self.repo = DueItemRepository(session)

    def sync(self, engagement: Engagement, *, today: dt.date | None = None) -> SyncResult:
        """Brings the engagement's forecasts in step with it, then pairs the waiting ones."""
        today = today or today_local()
        points = {
            payments.forecast_key(engagement.id, b.billing_date): b
            for b in self._billings(engagement, today)
        }
        window = dt.timedelta(days=PAYMENT_DAYS_LIMIT)
        existing = self.repo.forecasts_of(payments.SOURCE_KIND, engagement.id)
        if not points and not existing:
            return SyncResult(0, 0, 0)
        legal_entity_id = self._legal_entity(engagement)
        created = dropped = 0
        live: set[UUID] = set()
        for item in existing:
            key = item.forecast_key
            billing = points.get(key) if key is not None else None
            if billing is None or (
                billing.billing_date + window >= today
                and not self._is_current(item, engagement, billing, legal_entity_id)
            ):
                self.items.drop(item.id, DROPPED_REASON)
                dropped += 1
            elif key is not None:
                live.add(key)
        for key, billing in points.items():
            if key in live or billing.billing_date + window < today:
                continue
            terms = payments.due_terms(engagement, billing.billing_date)
            self.items.create(
                DueItemCreate(
                    legal_entity_id=legal_entity_id,
                    direction="out",
                    organization_id=engagement.freelancer_id,
                    amount=billing.amount,
                    description=self._description(engagement, billing),
                    state="forecast",
                    source_kind=payments.SOURCE_KIND,
                    source_id=engagement.id,
                    source_rule=payments.SOURCE_RULE,
                    forecast_key=key,
                    own_date=terms.own_date,
                    rule=terms.rule,
                )
            )
            created += 1
        return SyncResult(created, dropped, self.pair(engagement, today=today))

    def pair(self, engagement: Engagement, *, today: dt.date | None = None) -> int:
        """Points each `on_collection` forecast of the engagement at the client's due item it
        follows, where there is one: a waiting one gets its first, one that follows another
        moves when a later invoice now covers its period. Answers how many it moved."""
        if engagement.payment_rule != "on_collection" or engagement.state not in PAYING_STATES:
            return 0
        if engagement.client_organization_id is None or (
            engagement.deal_id is None and engagement.contract_id is None
        ):
            return 0
        forecasts = self.repo.forecasts_of(payments.SOURCE_KIND, engagement.id)
        if not forecasts:
            return 0
        today = today or today_local()
        periods = {
            payments.forecast_key(engagement.id, b.billing_date): b
            for b in self._billings(engagement, today)
        }
        legal_entity_id = self._legal_entity(engagement)
        paired = 0
        for item in forecasts:
            billing = periods.get(item.forecast_key) if item.forecast_key else None
            if billing is None:
                continue
            client = self._client_item(engagement, legal_entity_id, billing)
            if client is None or client.id == item.depends_on_id:
                continue
            if item.depends_on_id is None:
                self.items.attach_dependency(item.id, client.id)
            else:
                self.items.repoint_dependency(item.id, client.id)
            paired += 1
        return paired

    def pair_invoice(self, invoice: Invoice) -> int:
        """An issued client invoice may be what a forecast follows: pairs the engagements of its
        customer that serve its deal or a contract. Answers how many items it moved.

        A side effect of issuing or importing the invoice, which must never stop it: an
        engagement that cannot be paired is logged and left for the next `refresh_all`."""
        stmt = (
            select(Engagement.id)
            .where(
                Engagement.client_organization_id == invoice.customer_id,
                Engagement.state.in_(PAYING_STATES),
                Engagement.payment_rule == "on_collection",
                or_(Engagement.contract_id.is_not(None), Engagement.deal_id == invoice.deal_id),
            )
            .order_by(Engagement.id)
        )
        paired = 0
        for engagement_id in self.session.scalars(stmt).all():
            try:
                with self.session.begin_nested():
                    paired += self.pair(self._locked(engagement_id))
            except Exception as exc:  # noqa: BLE001 - never stops the invoice
                log.warning("engagement %s not paired: %s", engagement_id, type(exc).__name__)
        return paired

    def refresh_all(self, *, today: dt.date | None = None) -> SyncResult:
        """`sync` for every paying engagement: the cron's step, which moves the horizon of an
        open-ended one forward and pairs what a client invoice written elsewhere left waiting.
        One engagement that fails is counted and logged, and the others are still synced."""
        created = dropped = paired = failed = 0
        stmt = (
            select(Engagement.id).where(Engagement.state.in_(PAYING_STATES)).order_by(Engagement.id)
        )
        for engagement_id in self.session.scalars(stmt).all():
            try:
                with self.session.begin_nested():
                    result = self.sync(self._locked(engagement_id), today=today)
            except Exception as exc:  # noqa: BLE001 - one engagement must not stop the others
                log.warning("engagement %s not synced: %s", engagement_id, type(exc).__name__)
                failed += 1
                continue
            created += result.created
            dropped += result.dropped
            paired += result.paired
        return SyncResult(created, dropped, paired, failed)

    # ---- internals ------------------------------------------------------------------------

    def _locked(self, engagement_id: UUID) -> Engagement:
        """The engagement under `FOR UPDATE`, as `EngagementService` takes it: a sync from here
        and one from an update queue instead of both writing the same forecast."""
        return self.session.execute(
            select(Engagement)
            .where(Engagement.id == engagement_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()

    @staticmethod
    def _billings(engagement: Engagement, today: dt.date) -> list[payments.Billing]:
        if engagement.state not in PAYING_STATES:
            return []
        return payments.billings(engagement, today)

    def _legal_entity(self, engagement: Engagement) -> UUID:
        """The legal entity that pays: the freelancer's own, else the contract's, else the
        client's, else the space's default."""
        freelancer = self.session.get(Organization, engagement.freelancer_id)
        if freelancer is not None and freelancer.legal_entity_id is not None:
            return freelancer.legal_entity_id
        if engagement.contract_id is not None:
            contract = self.session.get(Contract, engagement.contract_id)
            if contract is not None:
                return contract.legal_entity_id
        if engagement.client_organization_id is not None:
            client = self.session.get(Organization, engagement.client_organization_id)
            if client is not None and client.legal_entity_id is not None:
                return client.legal_entity_id
        default = self.session.execute(
            select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
        ).scalar_one_or_none()
        if default is None:
            raise LookupError("the space has no default legal entity")
        return default

    def _is_current(
        self,
        item: DueItem,
        engagement: Engagement,
        billing: payments.Billing,
        legal_entity_id: UUID,
    ) -> bool:
        terms = payments.due_terms(engagement, billing.billing_date)
        if item.depends_on_id is not None:
            # An item that follows an invoice of a client the engagement no longer serves.
            dependency = self.repo.get(item.depends_on_id)
            if (
                dependency is None
                or dependency.organization_id != engagement.client_organization_id
            ):
                return False
        return (
            item.amount == billing.amount
            and item.legal_entity_id == legal_entity_id
            and item.own_date == terms.own_date
            and item.rule_kind == terms.rule.kind
            and item.rule_offset_days == terms.rule.offset_days
            and item.rule_not_after == terms.rule.not_after
        )

    @staticmethod
    def _description(engagement: Engagement, billing: payments.Billing) -> str:
        if engagement.fee_unit == "flat":
            return "Compenso a corpo"
        return f"Compenso {billing.billing_date:%Y-%m}"

    def _client_item(
        self, engagement: Engagement, legal_entity_id: UUID, billing: payments.Billing
    ) -> DueItem | None:
        """The client's due item the billing period is invoiced on, `None` when there is none.

        Several invoices can cover one period; the latest one is the one the freelancer waits
        for, since the client has paid for the work only once the last of them is settled."""
        served = []
        if engagement.deal_id is not None:
            served.append(Invoice.deal_id == engagement.deal_id)
        if engagement.contract_id is not None:
            served.append(
                exists().where(
                    WorkUnit.contract_id == engagement.contract_id,
                    WorkUnit.invoice_line_id == InvoiceLine.id,
                    # The lines a proforma was built on stay its own when it is issued.
                    or_(
                        InvoiceLine.invoice_id == Invoice.id,
                        InvoiceLine.invoice_id == Invoice.origin_proforma_id,
                    ),
                )
            )
        # An invoice with no accrual period belongs to the period that ended last before it was
        # issued: from the period's last day up to, not including, the next month's last day,
        # so two consecutive periods never claim the same invoice.
        year, month = billing.period_end.year, billing.period_end.month
        following = month_bounds(year + month // 12, month % 12 + 1)[1]
        covers = or_(
            and_(
                Invoice.accrual_from <= billing.period_end,
                Invoice.accrual_to >= billing.period_start,
            ),
            and_(
                Invoice.accrual_from.is_(None),
                Invoice.issue_date >= billing.period_end,
                Invoice.issue_date < following,
            ),
        )
        stmt = (
            join_sales_item(select(DueItem))
            .where(
                Invoice.customer_id == engagement.client_organization_id,
                Invoice.legal_entity_id == legal_entity_id,
                Invoice.type == "invoice",
                Invoice.status == "issued",
                Invoice.deleted_at.is_(None),
                DueItem.state.in_(("expected", "settled")),
                or_(*served),
                covers,
            )
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()
