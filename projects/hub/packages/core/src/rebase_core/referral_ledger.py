"""The admin's referral ledger (P-REB-44): every referral with its reward, the page's
figures batched in a bounded handful of queries, and the two admin steps on a reward
(`set_state`, `set_price`). The member's own code, the attribution and the reward at the
first signed letter stay in `rebase_core.referrals`."""

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Row, Select, and_, literal, select, union_all
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import utcnow
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import (
    Company,
    Freelancer,
    HubEngagement,
    Referral,
    ReferralReward,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.referral_evidence import referral_evidence
from rebase_core.referral_schemas import (
    ReferralLedgerItem,
    ReferralLedgerList,
)
from rebase_core.referral_settings import ReferralSettingsService
from rebase_core.referrals import (
    EARNING_ENGAGEMENT_STATES,
    ENTITY,
    ReferralService,
    can_mature,
)

# One row of `_ledger_query`: the referral, its reward and the referrer's card are outer
# joins, so the second and the last column are `None` for a referral with no reward yet
# or a referrer with no card. SQLAlchemy types the select without the `None` (a join does
# not narrow a column's type); the rows are read under this alias, which carries it.
_LedgerRow = Row[Referral, ReferralReward | None, str, str, str, UUID | None]
_SORT = SortSpec("created_at", "datetime")
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
STATE_ORDER = ("to_confirm", "confirmed", "paid")
# The word an admin reads for each state, in the 422 that refuses a step: the stored values
# are English, the sentence is Italian.
STATE_WORDS = {"to_confirm": "da_confermare", "confirmed": "confermato", "paid": "pagato"}


class _EngagementFacts(NamedTuple):
    engagement: HubEngagement
    company_name: str | None
    role: str | None
    freelancer_id: UUID
    freelancer_name: str
    freelancer_deleted: bool


class ReferralLedgerService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _live_engagements(self, pending: set[tuple[str, UUID]]) -> dict[tuple[str, UUID], UUID]:
        """For each referred side without a reward (`kind`, `entity_id`), its newest
        engagement that can still earn (its letter not signed yet and not cancelled): the one
        the first signature would mature the reward on. One query for all of them, one row
        per side (`DISTINCT ON`), never a row per engagement the side ever had. Read from the
        hub's read model of the CRM's engagements."""
        freelancer_ids = {entity for kind, entity in pending if kind == "freelancer"}
        company_ids = {entity for kind, entity in pending if kind == "company"}
        newest = []
        for kind, column, ids in (
            ("freelancer", HubEngagement.freelancer_id, freelancer_ids),
            ("company", HubEngagement.company_id, company_ids),
        ):
            if ids:
                newest.append(
                    select(literal(kind).label("kind"), column.label("entity_id"), HubEngagement.id)
                    .where(HubEngagement.state.in_(EARNING_ENGAGEMENT_STATES), column.in_(ids))
                    .ext(distinct_on(column))
                    .order_by(column, HubEngagement.created_at.desc(), HubEngagement.id.desc())
                    .subquery()
                )
        if not newest:
            return {}
        rows = self.session.execute(union_all(*(select(sub) for sub in newest)))
        return {(kind, entity_id): engagement_id for kind, entity_id, engagement_id in rows}

    def _engagement_facts(self, engagement_ids: Iterable[UUID]) -> dict[UUID, _EngagementFacts]:
        ids = set(engagement_ids)
        if not ids:
            return {}
        freelancer_user = aliased(User)
        rows = self.session.execute(
            select(HubEngagement, Company.company_name, freelancer_user, Freelancer.deleted_at)
            .outerjoin(Company, Company.id == HubEngagement.company_id)
            .join(Freelancer, Freelancer.id == HubEngagement.freelancer_id)
            .join(freelancer_user, freelancer_user.id == Freelancer.user_id)
            .where(HubEngagement.id.in_(ids))
        ).all()
        return {
            engagement.id: _EngagementFacts(
                engagement,
                company_name or engagement.client_name,
                engagement.role,
                engagement.freelancer_id,
                f"{user.first_name} {user.last_name}".strip(),
                freelancer_deleted_at is not None,
            )
            for engagement, company_name, user, freelancer_deleted_at in rows
        }

    def _ledger_items(self, rows: Sequence[_LedgerRow]) -> list[ReferralLedgerItem]:
        """The page's rows as ledger items, every lookup batched over the whole page:
        the referred names (two queries at most), the newest live engagement of each referral
        without a reward, those engagements' facts and the rewards' own, and the rates -- a
        bounded handful of queries, never one per row."""
        if not rows:
            return []
        settings = ReferralSettingsService(self.session).get_settings()
        names: dict[tuple[str, UUID], str] = {}
        deleted: set[tuple[str, UUID]] = set()
        freelancer_ids = {r.Referral.entity_id for r in rows if r.Referral.kind == "freelancer"}
        company_ids = {r.Referral.entity_id for r in rows if r.Referral.kind == "company"}
        if freelancer_ids:
            for entity_id, first_name, last_name, deleted_at in self.session.execute(
                select(Freelancer.id, User.first_name, User.last_name, Freelancer.deleted_at)
                .join(User, User.id == Freelancer.user_id)
                .where(Freelancer.id.in_(freelancer_ids))
            ):
                names["freelancer", entity_id] = f"{first_name} {last_name}".strip()
                if deleted_at is not None:
                    deleted.add(("freelancer", entity_id))
        if company_ids:
            for entity_id, company_name, deleted_at in self.session.execute(
                select(Company.id, Company.company_name, Company.deleted_at).where(
                    Company.id.in_(company_ids)
                )
            ):
                names["company", entity_id] = company_name
                if deleted_at is not None:
                    deleted.add(("company", entity_id))
        live = self._live_engagements(
            {
                (r.Referral.kind, r.Referral.entity_id)
                for r in rows
                if r.ReferralReward is None and can_mature(r.Referral.kind, r.Referral.status)
            }
        )
        facts = self._engagement_facts(
            {r.ReferralReward.engagement_id for r in rows if r.ReferralReward is not None}
            | set(live.values())
        )
        evidence = referral_evidence(self.session, [(r.Referral, r.referrer_email) for r in rows])
        items = []
        for row in rows:
            referral, reward = row.Referral, row.ReferralReward
            key = (referral.kind, referral.entity_id)
            engagement_id = reward.engagement_id if reward is not None else live.get(key)
            found = facts.get(engagement_id) if engagement_id is not None else None
            projected_rate = projected_amount = None
            if reward is None and found is not None:
                projected_rate = (
                    settings.rate_freelancer
                    if referral.kind == "freelancer"
                    else settings.rate_company
                )
                projected_amount = ReferralService.project_reward(found.engagement, projected_rate)
            items.append(
                ReferralLedgerItem(
                    referral_id=referral.id,
                    reward_id=reward.id if reward is not None else None,
                    kind=referral.kind,
                    referred_id=referral.entity_id,
                    referrer_name=f"{row.referrer_name} {row.referrer_last_name}".strip(),
                    referrer_email=row.referrer_email,
                    referrer_freelancer_id=row.referrer_freelancer_id,
                    referred_name=names.get(key, "-"),
                    referred_deleted=key in deleted,
                    engagement_id=engagement_id if found is not None else None,
                    engagement_freelancer_id=found.freelancer_id if found is not None else None,
                    engagement_freelancer_name=found.freelancer_name if found is not None else None,
                    engagement_freelancer_deleted=found.freelancer_deleted
                    if found is not None
                    else False,
                    engagement_company_name=found.company_name if found is not None else None,
                    engagement_role=found.role if found is not None else None,
                    projected_rate=projected_rate,
                    projected_amount=projected_amount,
                    rate=reward.rate if reward is not None else None,
                    base_amount=reward.base_amount if reward is not None else None,
                    reward_amount=reward.reward_amount if reward is not None else None,
                    status=reward.status if reward is not None else None,
                    referral_status=referral.status,
                    verified_at=referral.verified_at,
                    verified_via=referral.verified_via,
                    note=reward.note if reward is not None else None,
                    created_at=referral.created_at,
                    confirmed_at=reward.confirmed_at if reward is not None else None,
                    paid_at=reward.paid_at if reward is not None else None,
                    evidence=evidence[referral.id],
                )
            )
        return items

    def _ledger_query(self) -> Select[Referral, ReferralReward | None, str, str, str, UUID | None]:
        """Starts from `Referral`, outer-joined to its reward, so a referral with no
        reward yet (the referred party has not signed a first letter) still has a row
        on the ledger (P-REB-44) instead of being invisible until one exists. The
        referrer's own card, when he has one, comes in the same query (REB-609): a
        person has at most one (`uq_freelancers_user_id`), so it never doubles a row."""
        referrer = aliased(User)
        card = aliased(Freelancer)
        return (
            select(
                Referral,
                ReferralReward,
                referrer.first_name.label("referrer_name"),
                referrer.last_name.label("referrer_last_name"),
                referrer.email.label("referrer_email"),
                card.id.label("referrer_freelancer_id"),
            )
            .select_from(Referral)
            .join(referrer, referrer.id == Referral.referrer_user_id)
            .outerjoin(card, and_(card.user_id == referrer.id, card.deleted_at.is_(None)))
            .outerjoin(ReferralReward, ReferralReward.referral_id == Referral.id)
        )

    def list_rewards(
        self,
        limit: int = LIST_LIMIT_DEFAULT,
        *,
        status: str | None = None,
        cursor: str | None = None,
    ) -> ReferralLedgerList:
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        stmt = self._ledger_query()
        if status is not None:
            stmt = stmt.where(ReferralReward.status == status)
        if cursor:
            value, row_id = decode_cursor(_SORT, cursor)
            stmt = stmt.where(keyset_predicate(Referral.created_at, Referral.id, value, row_id))
        rows = self.session.execute(
            stmt.order_by(Referral.created_at.desc(), Referral.id.desc()).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            next_cursor = encode_cursor(_SORT, last.Referral.created_at, last.Referral.id)
        return ReferralLedgerList(items=self._ledger_items(page_rows), next_cursor=next_cursor)

    def get_ledger_item(self, reward_id: UUID) -> ReferralLedgerItem:
        row = self.session.execute(
            self._ledger_query().where(ReferralReward.id == reward_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, reward_id)
        return self._ledger_items([row])[0]

    def _require_reward(self, reward_id: UUID) -> ReferralReward:
        """The reward, locked `FOR UPDATE` until the caller's commit: `set_state` and
        `set_price` both read `status`, decide, then write, and two admins (or a double
        click) must not interleave those steps -- a price written over a reward the other
        request just confirmed would change a figure already recorded against. The
        second request waits here, then re-reads: `populate_existing`, because a locking
        read does not overwrite an object this session already holds, so without it the
        check would run on the stale state the lock was meant to refresh."""
        row = self.session.get(
            ReferralReward, reward_id, with_for_update=True, populate_existing=True
        )
        if row is None:
            raise NotFound(ENTITY, reward_id)
        return row

    def set_state(self, reward_id: UUID, status: str, admin_id: UUID) -> ReferralReward:
        """`to_confirm` to `confirmed` to `paid`, one step at a time, never
        backwards: a wrong click is undone by an admin, from the ledger, not by this
        service quietly accepting any state from any state. Tracking only -- moving a
        reward to `paid` records that the transfer happened outside the hub; nothing
        here moves money. Refuses to confirm a reward with no figure yet -- the same
        rule `set_price` already enforces from the other side, so a fixed-price letter
        with no estimate cannot be confirmed unpriced and then left unpriceable
        forever, whether the call comes from the ledger's own UI or straight at this
        endpoint."""
        row = self._require_reward(reward_id)
        if status not in STATE_ORDER:
            raise ValidationFailed(ENTITY, "status", "stato non valido")
        index = STATE_ORDER.index(row.status)
        next_state = STATE_WORDS[STATE_ORDER[index + 1]] if row.status != "paid" else "niente"
        if STATE_ORDER.index(status) != index + 1:
            raise ValidationFailed(
                ENTITY, "status", f"da {STATE_WORDS[row.status]} si passa solo a {next_state}"
            )
        if status == "confirmed" and row.reward_amount is None:
            raise ValidationFailed(ENTITY, "status", "il reward va prezzato prima di confermarlo")
        row.status = status
        if status == "confirmed":
            row.confirmed_by, row.confirmed_at = admin_id, utcnow()
        elif status == "paid":
            row.paid_at = utcnow()
        self.session.commit()
        return row

    def set_price(
        self, reward_id: UUID, base_amount: Decimal, reward_amount: Decimal
    ) -> ReferralReward:
        """What an admin fills in by hand for a reward `record_reward_if_signed` left
        with no figure (a `lump_sum` letter with no `expected_days` estimate).
        Refused once a state beyond `to_confirm` was already recorded against the
        figure it replaces -- price it, then move it, never the other way."""
        row = self._require_reward(reward_id)
        if row.status != "to_confirm":
            raise ValidationFailed(
                ENTITY, "status", "il prezzo si corregge solo prima della conferma"
            )
        row.base_amount, row.reward_amount = base_amount, reward_amount
        self.session.commit()
        return row
