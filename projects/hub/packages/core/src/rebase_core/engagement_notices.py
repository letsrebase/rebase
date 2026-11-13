"""The hub's read model of the CRM's engagements, kept current by the CRM's notices (REB-1106,
collaborators design § 5, «The hub's read model»).

The CRM writes a notice to its outbox in the transaction of every change of an engagement and
delivers it here until the hub answers 2xx, so a notice may arrive twice, late or after a newer
one. `EngagementNoticeService.apply` makes that harmless: the row is found by the match id the
hub minted for it (`hub_match_id`, a handed-off row's key) or else by the CRM's engagement id, and
a notice whose `sequence` is not newer than the one the row holds changes nothing. A newer one
overwrites every field the CRM owns and leaves what is the hub's alone: the company's daily budget
snapshot, as long as the notice names the same company the row has. A notice that names another
company re-snapshots from it, and one that names none clears the company and the snapshot: the
engagement no longer serves the company it was handed off for.

**The referral reward is written in the same transaction as the upsert**, when the row says its
letter is signed (`ReferralService.record_reward_if_signed`, which inserts each referral's reward
once and never again), so a reward and the signature that earned it land together or not at all.
It runs on every applied notice of a signed engagement and not only on one of kind `signed`: a
`closed` notice that overtook a lost `signed` one still carries the signature.

Nothing here commits for the caller's transaction but `apply` itself, once, at the end.
"""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rebase_core.engagement_schemas import EngagementNotice
from rebase_core.errors import ValidationFailed
from rebase_core.models import Company, Freelancer, HubEngagement
from rebase_core.referrals import ReferralService

ENTITY = "engagement"


class EngagementNoticeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def apply(self, notice: EngagementNotice) -> bool:
        """Upserts the engagement from `notice`, writes the reward its signature earns and
        commits. Whether the notice changed anything: `False` for one the row already held."""
        if self.session.get(Freelancer, notice.hub_freelancer_id) is None:
            raise ValidationFailed(ENTITY, "hub_freelancer_id", "scheda freelance sconosciuta")
        row = self._locked(notice)
        if notice.sequence <= row.sequence:
            self.session.rollback()
            return False
        company_id = notice.hub_company_id
        if company_id is not None and self.session.get(Company, company_id) is None:
            # A request the hub no longer knows is not a reason to refuse the engagement.
            company_id = None
        row.crm_engagement_id = notice.engagement_id
        row.freelancer_id = notice.hub_freelancer_id
        if company_id != row.company_id:
            # The budget snapshot belongs to the company it was taken from: another company takes
            # it again from the new one, none drops it with the company. A row that had no company
            # (born of a notice, never handed off) was never snapshotted and stays so.
            had_company = row.company_id is not None
            row.company_id = company_id
            if company_id is None:
                row.company_daily_budget = None
            elif had_company:
                company = self.session.get(Company, company_id)
                row.company_daily_budget = company.daily_budget if company is not None else None
        row.client_name = notice.client_name
        row.letter_id = notice.letter_id
        row.letter_number = notice.letter_number
        row.signed_at = notice.signed_at
        row.mode = notice.mode
        row.fee = notice.fee
        row.expected_days = notice.expected_days
        row.role = notice.role
        row.starts_on = notice.starts_on
        row.ends_on = notice.ends_on
        if notice.state != "concluded":
            row.planned_ends_on = notice.ends_on
        row.state = notice.state
        row.sequence = notice.sequence
        self.session.flush()
        if row.signed_at is not None:
            ReferralService(self.session).record_reward_if_signed(row)
        self.session.commit()
        return True

    def _locked(self, notice: EngagementNotice) -> HubEngagement:
        """The engagement's row, locked until the commit, created empty (`handoff`, sequence 0)
        when the hub has none: `INSERT ... ON CONFLICT DO NOTHING` then a locking read, so two
        first notices of one engagement cannot both insert."""
        key = notice.hub_match_id or notice.engagement_id
        found = self._find(key, notice.engagement_id)
        if found is None:
            self.session.execute(
                pg_insert(HubEngagement)
                .values(
                    id=key,
                    freelancer_id=notice.hub_freelancer_id,
                    state="handoff",
                    sequence=0,
                )
                .on_conflict_do_nothing()
            )
            found = self._find(key, notice.engagement_id)
        if found is None:
            # The id is free but the CRM's engagement id is another row's: two rows for one
            # engagement, which only a different `hub_match_id` for it can make.
            raise ValidationFailed(ENTITY, "hub_match_id", "incarico già noto con un altro id")
        return found

    def _find(self, key: UUID, crm_engagement_id: UUID) -> HubEngagement | None:
        return self.session.scalars(
            select(HubEngagement)
            .where(
                or_(
                    HubEngagement.crm_engagement_id == crm_engagement_id,
                    HubEngagement.id == key,
                )
            )
            .order_by(HubEngagement.crm_engagement_id.is_(None))
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
