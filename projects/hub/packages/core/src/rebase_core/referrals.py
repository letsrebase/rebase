"""Referrals: an existing member's own link, who it brought in, and the reward that
matures once at the referred entity's first signed letter (P-REB-44).

A member is only ever a referrer through their own code (`code_for`, issued lazily,
never on signup): there is no anonymous link. `link_signup` is the public, mute door
every signup already has (`FreelancerService.apply`, `CompanyService.request`) --
an unknown code, an empty one, or a member naming themselves is never an error, only a
referral that quietly does not happen. `record_reward_if_signed` is the one paid door,
called once from `EngagementNoticeService` when the CRM's notice says an engagement's letter
is signed (REB-1106: the letters and their signature live in the CRM, and the hub keeps a read
model of its engagements, `HubEngagement`): each side of the engagement that was referred,
freelancer and company independently, gets a reward inserted once
and never again, on the `INSERT ... ON CONFLICT DO NOTHING` idiom
`rebase_core.framework.next_letter_number` already uses for "exactly once, even under a
race" -- the one-time maturity design record 2026-09-26 asks for, immune to a later
renewal, another match, or a rate an admin edits afterward.

A referral counts only once the referred person is verified (REB-658, design record
2026-10-05). The public routes do not verify the address a `ref` code is posted with,
so `link_signup` records it `to_verify` and nothing is owed on it. It becomes
`verified` by the first magic-link login after it was made (`verify_on_login`, called
from `UserService.enter`) or, for a freelancer, their own signature of a letter
(`record_reward_if_signed`); a company never signs, so only the login verifies one.
The reward is written at the later of the two moments, the first signed letter and the
verification. Replacing an attribution with the code the person presents is not here.

The reward's base is rebase's own margin on that letter, the company's daily budget
(snapshotted on the engagement's row when the hub opened it) against the letter's `fee`, never
the freelancer's fee alone: `reward_base` below is where the day-rate question the P-REB-44 spike
raised is answered, grounded in the engagement's `expected_days`, an admin's own estimate of its
billable days.
"""

import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import utcnow
from rebase_core.contract_schemas import DAY_RATE, LUMP_SUM
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.match_words import PAY_MODE_WORDS
from rebase_core.models import (
    REFERRAL_CODE_LENGTH,
    REFERRAL_STATES,
    Company,
    ContractDocument,
    Freelancer,
    HubEngagement,
    Match,
    Referral,
    ReferralReward,
    TeamRequest,
    User,
)
from rebase_core.referral_schemas import (
    ALREADY_ACCRUED,
    EXPECTED,
    MatchReferral,
    MemberReferral,
    MemberReferralItem,
)
from rebase_core.referral_settings import ReferralSettingsService

ENTITY = "referral"
# No 0/O/1/I: a code is read aloud or typed from a screenshot as often as it is clicked.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CENT = Decimal("0.01")


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))


def reward_base(
    daily_budget: Decimal, fee: Decimal, mode: str, expected_days: Decimal | int | None
) -> Decimal | None:
    """Rebase's own margin on a letter, the base a referral reward is a rate of.

    A day-rate letter (`modalita == DAY_RATE`, both `daily_budget` and
    `fee` already daily) has a well-defined margin per day,
    `budget_giornaliero - compenso`, projected over `expected_days` when an admin
    estimated one, else one day's margin -- the conservative floor a reward never
    guesses past. An `lump_sum` letter's `fee` is already a lump sum for the whole
    engagement, so it is compared against the client's own projected total,
    `budget_giornaliero * giorni_previsti`; with no estimate there is no total to
    project, and this returns `None` for an admin to price by hand on the ledger, the
    same figure `ReferralReward.base_amount` leaves null for exactly that case. Never
    negative: a fee above the client's budget is rebase's own loss on that letter, not
    a negative reward for whoever made the referral."""
    if mode == DAY_RATE:
        margin_per_day = daily_budget - fee
        days = expected_days if expected_days is not None else 1
        return max(Decimal("0"), margin_per_day * days)
    if expected_days is None:
        return None
    return max(Decimal("0"), daily_budget * expected_days - fee)


# An engagement whose letter is not signed yet: the only states in which a first signature can
# still land and mature a reward. `active` and `concluded` already had one (a reward is
# written in the transaction of the notice that says the letter is signed), `cancelled` never
# will, and a `handoff` row is read by nothing.
EARNING_ENGAGEMENT_STATES = ("draft", "awaiting_signature")
# The hours an hourly letter's day is worth: the CRM's own rule (`engagements.schemas.
# HOURS_PER_DAY`), the one place hours become days.
HOURS_PER_DAY = 8
TO_VERIFY, VERIFIED = REFERRAL_STATES


def can_mature(kind: str, status: str) -> bool:
    """Whether a first signed letter would write this referral's reward: a verified
    referral, and a pending freelancer's, whose own signature is what verifies them
    (`record_reward_if_signed`). A pending company's is not -- the company never signs,
    so only its referente's login can verify it, and until then no letter earns anything."""
    return status == VERIFIED or kind == "freelancer"


def letter_unit(data: Mapping[str, Any]) -> str:
    """How a letter prices its fee, `to_work_unit` or `lump_sum` (`PAY_MODES`): its printed
    `modalita`, read back as the mode it was written from, which the reward's base is
    decided on by equality. The one reading of that field, shared by the reward at
    signing, its projection, and the lists that show the letter's fee. A letter without one
    (none is written without it) reads as `""`, which `reward_base` prices as a lump sum."""
    printed = data.get("modalita")
    return next((mode for mode, word in PAY_MODE_WORDS.items() if word == printed), "")


def engagement_reward_base(engagement: HubEngagement) -> Decimal | None:
    """`reward_base` on an engagement's own figures (its fee and mode, the budget snapshot and
    the expected days); `None` when the fee, the mode or the snapshot is missing. An hourly
    fee is a day's worth of hours before it is set against the daily budget."""
    fee, budget, mode = engagement.fee, engagement.company_daily_budget, engagement.mode
    if fee is None or budget is None or mode is None:
        return None
    if mode == "flat":
        return reward_base(budget, fee, LUMP_SUM, engagement.expected_days)
    daily_fee = fee * HOURS_PER_DAY if mode == "hour" else fee
    return reward_base(budget, daily_fee, DAY_RATE, engagement.expected_days)


def match_reward_base(match: Match, mode: str) -> Decimal | None:
    """`reward_base` on a match's own snapshots (`letter_fee`,
    `company_daily_budget`, `expected_days`); `None` when either snapshot is
    missing, exactly as the reward at signing leaves its figure blank."""
    fee, budget = match.letter_fee, match.company_daily_budget
    if fee is None or budget is None:
        return None
    return reward_base(budget, fee, mode, match.expected_days)


class ReferralService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._settings = ReferralSettingsService(session)

    # ---- a member's own code -------------------------------------------------------

    def code_for(self, user_id: UUID) -> str:
        """The member's own code, issued the first time anyone asks for one. Flushed,
        never committed, like every other write in this service: the caller's commit
        persists it and its rollback drops it. A clash with another member's
        freshly-issued code is the same race `FreelancerService.apply` already
        recovers from: retry under a new code, inside a savepoint so the caller's own
        staged work survives, rather than fail the request over a coincidence this
        rare."""
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        if user.referral_code is not None:
            return user.referral_code
        for _ in range(5):
            code = _generate_code()
            try:
                with self.session.begin_nested():
                    user.referral_code = code
                    self.session.flush()
                return code
            except IntegrityError:
                self.session.refresh(user)
                if user.referral_code is not None:
                    return user.referral_code
        raise ValidationFailed(ENTITY, "code", "non riesco a generare un codice libero, riprova")

    def resolve_referrer(self, code: str | None) -> User | None:
        """The member behind a code, or `None` for an empty or unknown one -- the
        wizard is public and mute, so an invalid `ref` is never an error, only a
        referral that does not happen."""
        if not code:
            return None
        owner = self.session.scalar(select(User).where(User.referral_code == code.strip().upper()))
        # A code of a person an admin merged into another (REB-800) credits the survivor:
        # the absorbed row is a husk that nobody reads a ledger from.
        if owner is not None and owner.merged_into_id is not None:
            owner = self.session.get(User, owner.merged_into_id) or owner
        return owner

    def link_signup(
        self, kind: str, entity_id: UUID, code: str | None, *, new_user_id: UUID
    ) -> None:
        """Records that `code`'s owner referred whoever just signed up as `kind`
        (`"freelancer"` or `"company"`), once per entity
        (`uq_referrals_kind_entity`): a repeat freelancer application
        (`FreelancerService.apply`'s own "nothing overwritten" rule) or a company's
        later, additional request (`create_additional_request`) never calls this a
        second time for the same row, but the `ON CONFLICT DO NOTHING` is the real
        guard, the same belt-and-braces every unique-key insert in this package keeps.
        Silent on every refusal -- an unknown code, a member referring themselves --
        the same muteness the public routes that call this already keep.

        Staged into the caller's transaction and never committed here, the way
        `record_reward_if_signed` is: the caller writes the card and this row in one
        commit, so a failure between the two cannot leave a card whose referral is
        lost for good (a retried signup finds the card already there and never calls
        this again)."""
        referrer = self.resolve_referrer(code)
        if referrer is None or referrer.id == new_user_id:
            return
        self.session.execute(
            pg_insert(Referral)
            .values(
                referrer_user_id=referrer.id,
                kind=kind,
                entity_id=entity_id,
                code=referrer.referral_code,
            )
            .on_conflict_do_nothing(index_elements=[Referral.kind, Referral.entity_id])
        )

    def link_team_request(
        self, company_id: UUID, user: User, *, own_code: str | None = None
    ) -> None:
        """A `Company` row now exists for `user` (the company wizard, or an admin opening
        the talent cloud), so the code a public team request of the same contact carried
        (REB-600) can become a referral. The request is found by the referente's
        lowercased email, newest first, among the public ones that kept a code; the
        wizard's own `own_code` wins when it names a member, the request's is the
        fallback. Silent like `link_signup`, staged and never committed, and a no-op
        once any company of this user already carries a referral: a company's attribution
        is made once, by its first request. Pending until the person's first magic-link
        login (REB-658), since the email on a public request was never proved."""
        # Two admins opening the cloud for two companies of one contact at once would both
        # find no referral and each write one: the contact's `users` row is the lock that
        # makes the second check after the first commit. `FOR NO KEY UPDATE`, not `FOR
        # UPDATE`: the company or grant row staged before this call holds `FOR KEY SHARE`
        # on the user through its foreign key, which `FOR UPDATE` would wait on while the
        # other session does the same.
        self.session.execute(
            select(User.id).where(User.id == user.id).with_for_update(key_share=True)
        )
        already = self.session.scalar(
            select(Referral.id)
            .where(
                Referral.kind == "company",
                Referral.entity_id.in_(select(Company.id).where(Company.user_id == user.id)),
            )
            .limit(1)
        )
        if already is not None:
            return
        own = self.resolve_referrer(own_code)
        code = own_code if own is not None and own.id != user.id else None
        if code is None:
            code = self.session.scalar(
                select(TeamRequest.ref)
                .where(
                    TeamRequest.origin == "public",
                    TeamRequest.ref.is_not(None),
                    func.lower(TeamRequest.email) == user.email.strip().lower(),
                )
                .order_by(TeamRequest.created_at.desc(), TeamRequest.id.desc())
                .limit(1)
            )
        self.link_signup("company", company_id, code, new_user_id=user.id)

    def referrer_name(self, kind: str, entity_id: UUID) -> str | None:
        """Who referred this freelancer or this company, as a contract prints a name
        (`rebase_core.matches`'s `_framework_date`/`_letter_date`): `None` prints as a
        blank line, the same convention every other optional field of these templates
        already uses. Only a `verified` referral is named (REB-664): a pending one may
        be squatted (REB-646), and a signed document must not state an attribution nobody
        has proved, so it reads as no referrer at all."""
        row = self.session.execute(
            select(User.first_name, User.last_name)
            .select_from(Referral)
            .join(User, User.id == Referral.referrer_user_id)
            .where(
                Referral.kind == kind,
                Referral.entity_id == entity_id,
                Referral.status == VERIFIED,
            )
        ).first()
        return None if row is None else f"{row.first_name} {row.last_name}".strip()

    # ---- the referral's proof, and the reward at the first signed letter ---------------

    def verify_on_login(self, user_id: UUID, at: datetime) -> None:
        """Called by `UserService.enter` in the commit that opens the session: the
        person just spent a link mailed to their address, so every referral naming them
        (the freelancer card or the company requests they hold) that was made at or
        before `at` becomes `verified`, `verified_via` `accesso`. Never one made after:
        a login only proves the address was theirs at that moment, and a referral posted
        later (their address, an accomplice's code) has not been proved by it. Staged,
        never committed. A referral whose letter was signed while it was pending (a
        company's, which never signs) earns its reward now.

        The `UPDATE` takes the referral's row lock, which `record_reward_if_signed`
        takes too, so a login and a signature racing on one referral cannot both miss
        each other: whichever commits second sees the other's work."""
        owned = or_(
            and_(
                Referral.kind == "freelancer",
                Referral.entity_id.in_(select(Freelancer.id).where(Freelancer.user_id == user_id)),
            ),
            and_(
                Referral.kind == "company",
                Referral.entity_id.in_(select(Company.id).where(Company.user_id == user_id)),
            ),
        )
        verified = self.session.execute(
            update(Referral)
            .where(Referral.status == TO_VERIFY, Referral.created_at <= at, owned)
            .values(status=VERIFIED, verified_at=at, verified_via="login")
            .returning(Referral.id, Referral.kind, Referral.entity_id)
        ).all()
        for referral_id, kind, entity_id in verified:
            self._mature_first_signed_letter(referral_id, kind, entity_id)

    def _mature_first_signed_letter(self, referral_id: UUID, kind: str, entity_id: UUID) -> None:
        """The reward of a referral that became `verified` after its referred side
        already had a letter signed: written from that first letter, the one
        `record_reward_if_signed` would have used, with the rate in force now. Read from the
        hub's read model of the CRM's engagements."""
        side = HubEngagement.freelancer_id if kind == "freelancer" else HubEngagement.company_id
        first = self.session.scalars(
            select(HubEngagement)
            .where(
                HubEngagement.signed_at.is_not(None),
                HubEngagement.state != "handoff",
                side == entity_id,
            )
            .order_by(HubEngagement.signed_at.asc(), HubEngagement.id)
            .limit(1)
        ).first()
        if first is not None:
            self._write_reward(referral_id, kind, first)

    def record_reward_if_signed(self, engagement: HubEngagement) -> None:
        """Called by `EngagementNoticeService` for an engagement whose letter is signed, staged
        into the transaction that upserts the engagement and never committed here: the notice
        and the reward land together or not at all. It runs on every notice of a signed
        engagement, which is safe since each referral's reward is inserted once. The figures
        are the engagement's own (`engagement_reward_base`), read here and not re-derived, so a
        reward matches the letter the day it was signed; the budget is the snapshot taken when
        the hub opened the engagement, never a live read of `Company.daily_budget`: a company's
        own request can be edited weeks later, and a reward already promised must never move
        because of it.

        Only a `verified` referral earns (REB-658). The freelancer is the letter's one
        signer -- Documenso mails the envelope to their address -- so their signature
        verifies a pending referral of theirs, `letter`, in this same transaction. The
        company is not a signer: a pending company referral earns nothing here and
        waits for its referente's login (`verify_on_login`), which writes the reward
        from this letter."""
        for kind, entity_id in (
            ("freelancer", engagement.freelancer_id),
            ("company", engagement.company_id),
        ):
            if entity_id is None:
                continue
            # The row lock pairs with `verify_on_login`'s `UPDATE`; `populate_existing`
            # because a login in another transaction may have verified it since this
            # session loaded it.
            referral = self.session.scalar(
                select(Referral)
                .where(Referral.kind == kind, Referral.entity_id == entity_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if referral is None:
                continue
            if referral.status == TO_VERIFY:
                if kind != "freelancer":
                    continue
                referral.status = VERIFIED
                referral.verified_at = engagement.signed_at or utcnow()
                referral.verified_via = "letter"
            self._write_reward(referral.id, kind, engagement)

    def _write_reward(self, referral_id: UUID, kind: str, engagement: HubEngagement) -> None:
        """The one insert of a referral's reward, once per referral and never again
        (`ON CONFLICT DO NOTHING` on `uq_referral_rewards_referral_id`), at the rate of
        the referred side's kind in force now."""
        settings = self._settings.get_settings()
        rate = settings.rate_freelancer if kind == "freelancer" else settings.rate_company
        base = engagement_reward_base(engagement)
        self.session.execute(
            pg_insert(ReferralReward)
            .values(
                referral_id=referral_id,
                engagement_id=engagement.id,
                rate=rate,
                base_amount=base,
                reward_amount=None if base is None else (base * rate).quantize(_CENT),
            )
            .on_conflict_do_nothing(index_elements=[ReferralReward.referral_id])
        )

    # ---- the member's own view -------------------------------------------------------

    def for_user(self, user_id: UUID) -> MemberReferral:
        freelancer_rows = self.session.execute(
            select(Referral.created_at, User.first_name, User.last_name, Freelancer.status)
            .select_from(Referral)
            .join(Freelancer, Freelancer.id == Referral.entity_id)
            .join(User, User.id == Freelancer.user_id)
            .where(Referral.referrer_user_id == user_id, Referral.kind == "freelancer")
        ).all()
        company_rows = self.session.execute(
            select(Referral.created_at, Company.company_name, Company.status)
            .select_from(Referral)
            .join(Company, Company.id == Referral.entity_id)
            .where(Referral.referrer_user_id == user_id, Referral.kind == "company")
        ).all()
        referred = [
            MemberReferralItem(
                kind="freelancer",
                first_name=f"{row.first_name} {row.last_name}".strip(),
                created_at=row.created_at,
                status=row.status,
            )
            for row in freelancer_rows
        ] + [
            MemberReferralItem(
                kind="company",
                first_name=row.company_name,
                created_at=row.created_at,
                status=row.status,
            )
            for row in company_rows
        ]
        referred.sort(key=lambda item: item.created_at, reverse=True)
        settings = self._settings.get_settings()
        return MemberReferral(
            code=self.code_for(user_id),
            rate_freelancer=settings.rate_freelancer,
            rate_company=settings.rate_company,
            referred=referred,
        )

    # ---- what a match would earn -----------------------------------------------------

    @staticmethod
    def project_reward(engagement: HubEngagement, rate: Decimal) -> Decimal | None:
        """What a referral would earn on this engagement if its letter were signed now: the
        same `engagement_reward_base` the reward at signing uses, times `rate`, quantized the
        same way. `None` when there is nothing to project -- the engagement is past the point a
        first signature can land (`EARNING_ENGAGEMENT_STATES`), or the base is unknown (a
        `lump_sum` letter with no expected days, or a snapshot never taken). Read by the ledger."""
        if engagement.state not in EARNING_ENGAGEMENT_STATES:
            return None
        base = engagement_reward_base(engagement)
        return None if base is None else (base * rate).quantize(_CENT)

    @staticmethod
    def project_match_reward(match: Match, mode: str, rate: Decimal) -> Decimal | None:
        """`project_reward` for the hub's own match, the one «Match» list still shows until
        the match side is removed."""
        if match.cancelled_at is not None or match.status not in EARNING_ENGAGEMENT_STATES:
            return None
        base = match_reward_base(match, mode)
        return None if base is None else (base * rate).quantize(_CENT)

    def for_matches(
        self, rows: Sequence[tuple[Match, ContractDocument | None]]
    ) -> dict[UUID, list[MatchReferral]]:
        """The referred sides of every match of a page, freelancer first then company,
        keyed by match id (a match with none is absent): three queries for the whole
        page whatever its size -- referrals with their referrer and his card, their
        rewards with the match each one's letter belongs to, and the rates. `rows` pairs
        each match with its current letter, the one whose `mode` a projection reads.
        A reward on this match shows its real figures and state; one already earned on
        another match is `already_accrued`, since a referral pays once; none yet is
        `expected`, projected at the current rate."""
        if not rows:
            return {}
        freelancer_ids = {match.freelancer_id for match, _ in rows}
        company_ids = {match.company_id for match, _ in rows}
        referrer = aliased(User)
        card = aliased(Freelancer)
        referrals = {
            (row.Referral.kind, row.Referral.entity_id): row
            for row in self.session.execute(
                select(
                    Referral,
                    referrer.first_name.label("first_name"),
                    referrer.last_name.label("last_name"),
                    card.id.label("card_id"),
                )
                .join(referrer, referrer.id == Referral.referrer_user_id)
                .outerjoin(card, and_(card.user_id == referrer.id, card.deleted_at.is_(None)))
                .where(
                    or_(
                        and_(Referral.kind == "freelancer", Referral.entity_id.in_(freelancer_ids)),
                        and_(Referral.kind == "company", Referral.entity_id.in_(company_ids)),
                    )
                )
            )
        }
        if not referrals:
            return {}
        # A reward belongs to an engagement, and the engagement of a match the hub holds is keyed
        # by the match's own id (a handed-off or migrated match keeps it), so this is the match
        # the reward came from until the match side is removed.
        rewards = {
            reward.referral_id: (reward, reward.engagement_id)
            for reward in self.session.scalars(
                select(ReferralReward).where(
                    ReferralReward.referral_id.in_([r.Referral.id for r in referrals.values()])
                )
            )
        }
        settings = self._settings.get_settings()
        out: dict[UUID, list[MatchReferral]] = {}
        for match, letter in rows:
            sides = (
                ("freelancer", match.freelancer_id, settings.rate_freelancer),
                ("company", match.company_id, settings.rate_company),
            )
            for kind, entity_id, rate in sides:
                found = referrals.get((kind, entity_id))
                if found is None:
                    continue
                earned = rewards.get(found.Referral.id)
                rate_shown: Decimal | None = None
                amount: Decimal | None = None
                reward_id: UUID | None = None
                if earned is None and not can_mature(kind, found.Referral.status):
                    status = TO_VERIFY
                elif earned is None:
                    status = EXPECTED
                    rate_shown = rate
                    unit = letter_unit(letter.data) if letter is not None else ""
                    amount = self.project_match_reward(match, unit, rate)
                elif earned[1] == match.id:
                    reward = earned[0]
                    status, rate_shown, amount = reward.status, reward.rate, reward.reward_amount
                    reward_id = reward.id
                else:
                    status = ALREADY_ACCRUED
                out.setdefault(match.id, []).append(
                    MatchReferral(
                        kind=kind,
                        referrer_name=f"{found.first_name} {found.last_name}".strip(),
                        referrer_freelancer_id=found.card_id,
                        rate=rate_shown,
                        amount=amount,
                        status=status,
                        reward_id=reward_id,
                    )
                )
        return out
