"""Referrals: an existing member's own link, who it brought in, and the reward that
matures once at the referred entity's first signed letter (P-REB-44).

A member is only ever a referrer through their own code (`code_for`, issued lazily,
never on signup): there is no anonymous link. `link_signup` is the public, mute door
every signup already has (`FreelancerService.apply`, `CompanyService.request`) --
an unknown code, an empty one, or a member naming themselves is never an error, only a
referral that quietly does not happen. `record_reward_if_signed` is the one paid door,
called once from `SigningService._confirm_completion` the moment a letter (never a
framework agreement, which carries no match) turns `firmato`: each side of the match
that was referred, freelancer and company independently, gets a reward inserted once
and never again, on the `INSERT ... ON CONFLICT DO NOTHING` idiom
`rebase_core.framework.next_letter_number` already uses for "exactly once, even under a
race" -- the one-time maturity design record 2026-09-26 asks for, immune to a later
renewal, another match, or a rate an admin edits afterward.

A referral counts only once the referred person is verified (REB-658, design record
2026-10-05). The public routes do not verify the address a `rif` code is posted with,
so `link_signup` records it `da_verificare` and nothing is owed on it. It becomes
`verificato` by the first magic-link login after it was made (`verify_on_login`, called
from `UserService.enter`) or, for a freelancer, their own signature of a letter
(`record_reward_if_signed`); a company never signs, so only the login verifies one.
The reward is written at the later of the two moments, the first signed letter and the
verification. Replacing an attribution with the code the person presents is not here.

The reward's base is rebase's own margin on that letter, `Company.budget_giornaliero`
against the letter's `compenso`, never the freelancer's fee alone (the contract flow's
own rule, `matches.py`'s module docstring): `reward_base` below is where the day-rate
question the P-REB-44 spike raised is answered, grounded in `Match.giorni_previsti`
(REB-497), an admin's own estimate of the engagement's billable days.
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
from rebase_core.contract_schemas import DAY_RATE
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.match_words import LETTERA
from rebase_core.models import (
    REFERRAL_CODE_LENGTH,
    REFERRAL_STATES,
    Company,
    ContractDocument,
    Freelancer,
    Match,
    Referral,
    ReferralReward,
    TeamRequest,
    User,
)
from rebase_core.referral_schemas import (
    GIA_MATURATO,
    PREVISTO,
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
    budget_giornaliero: Decimal, compenso: Decimal, modalita: str, giorni_previsti: int | None
) -> Decimal | None:
    """Rebase's own margin on a letter, the base a referral reward is a rate of.

    A day-rate letter (`modalita == DAY_RATE`, both `budget_giornaliero` and
    `compenso` already daily) has a well-defined margin per day,
    `budget_giornaliero - compenso`, projected over `giorni_previsti` when an admin
    estimated one, else one day's margin -- the conservative floor a reward never
    guesses past. An `a corpo` letter's `compenso` is already a lump sum for the whole
    engagement, so it is compared against the client's own projected total,
    `budget_giornaliero * giorni_previsti`; with no estimate there is no total to
    project, and this returns `None` for an admin to price by hand on the ledger, the
    same figure `ReferralReward.base_amount` leaves null for exactly that case. Never
    negative: a fee above the client's budget is rebase's own loss on that letter, not
    a negative reward for whoever made the referral."""
    if modalita == DAY_RATE:
        margin_per_day = budget_giornaliero - compenso
        days = giorni_previsti if giorni_previsti is not None else 1
        return max(Decimal("0"), margin_per_day * days)
    if giorni_previsti is None:
        return None
    return max(Decimal("0"), budget_giornaliero * giorni_previsti - compenso)


# A match whose letter is not signed yet: the only states in which a first signature can
# still land and mature a reward. `attivo` and `concluso` already had one (a reward is
# written in the transaction that turns the letter `firmato`), `annullato` never will.
EARNING_MATCH_STATES = ("bozza", "in_firma")
DA_VERIFICARE, VERIFICATO = REFERRAL_STATES


def can_mature(kind: str, stato: str) -> bool:
    """Whether a first signed letter would write this referral's reward: a verified
    referral, and a pending freelancer's, whose own signature is what verifies them
    (`record_reward_if_signed`). A pending company's is not -- the company never signs,
    so only its referente's login can verify it, and until then no letter earns anything."""
    return stato == VERIFICATO or kind == "freelancer"


def letter_unit(data: Mapping[str, Any]) -> str:
    """How a letter prices its fee, `a giornata` or `a corpo` (`PAY_MODES`): its stored
    `modalita`, which the reward's base is decided on by equality. The one reading of
    that field, shared by the reward at signing, its projection, and the lists that show
    the letter's fee. A letter without one (none is written without it) reads as `""`,
    which `reward_base` prices as a lump sum."""
    return str(data.get("modalita") or "")


def match_reward_base(match: Match, modalita: str) -> Decimal | None:
    """`reward_base` on a match's own snapshots (`lettera_compenso`,
    `company_budget_giornaliero`, `giorni_previsti`); `None` when either snapshot is
    missing, exactly as the reward at signing leaves its figure blank."""
    compenso, budget = match.lettera_compenso, match.company_budget_giornaliero
    if compenso is None or budget is None:
        return None
    return reward_base(budget, compenso, modalita, match.giorni_previsti)


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
        wizard is public and mute, so an invalid `rif` is never an error, only a
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
                select(TeamRequest.rif)
                .where(
                    TeamRequest.origine == "pubblico",
                    TeamRequest.rif.is_not(None),
                    func.lower(TeamRequest.email) == user.email.strip().lower(),
                )
                .order_by(TeamRequest.created_at.desc(), TeamRequest.id.desc())
                .limit(1)
            )
        self.link_signup("company", company_id, code, new_user_id=user.id)

    def referrer_name(self, kind: str, entity_id: UUID) -> str | None:
        """Who referred this freelancer or this company, as a contract prints a name
        (`rebase_core.matches`'s `_quadro_data`/`_lettera_data`): `None` prints as a
        blank line, the same convention every other optional field of these templates
        already uses. Only a `verificato` referral is named (REB-664): a pending one may
        be squatted (REB-646), and a signed document must not state an attribution nobody
        has proved, so it reads as no referrer at all."""
        row = self.session.execute(
            select(User.nome, User.cognome)
            .select_from(Referral)
            .join(User, User.id == Referral.referrer_user_id)
            .where(
                Referral.kind == kind,
                Referral.entity_id == entity_id,
                Referral.stato == VERIFICATO,
            )
        ).first()
        return None if row is None else f"{row.nome} {row.cognome}".strip()

    # ---- the referral's proof, and the reward at the first signed letter ---------------

    def verify_on_login(self, user_id: UUID, at: datetime) -> None:
        """Called by `UserService.enter` in the commit that opens the session: the
        person just spent a link mailed to their address, so every referral naming them
        (the freelancer card or the company requests they hold) that was made at or
        before `at` becomes `verificato`, `verified_via` `accesso`. Never one made after:
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
            .where(Referral.stato == DA_VERIFICARE, Referral.created_at <= at, owned)
            .values(stato=VERIFICATO, verified_at=at, verified_via="accesso")
            .returning(Referral.id, Referral.kind, Referral.entity_id)
        ).all()
        for referral_id, kind, entity_id in verified:
            self._mature_first_signed_letter(referral_id, kind, entity_id)

    def _mature_first_signed_letter(self, referral_id: UUID, kind: str, entity_id: UUID) -> None:
        """The reward of a referral that became `verificato` after its referred side
        already had a letter signed: written from that first letter, the one
        `record_reward_if_signed` would have used, with the rate in force now."""
        side = Match.freelancer_id if kind == "freelancer" else Match.company_id
        first = self.session.execute(
            select(ContractDocument, Match)
            .join(Match, Match.id == ContractDocument.match_id)
            .where(
                ContractDocument.kind == LETTERA,
                ContractDocument.stato == "firmato",
                side == entity_id,
            )
            .order_by(ContractDocument.signed_at.asc().nulls_last(), ContractDocument.id)
            .limit(1)
        ).first()
        if first is not None:
            self._write_reward(referral_id, kind, first[0], first[1])

    def record_reward_if_signed(self, document: ContractDocument, match: Match) -> None:
        """Called once, from `SigningService._confirm_completion`, under the row locks
        that call already holds, staged into that same transaction and never committed
        here -- `write_framework`'s own convention, one commit at the end of the
        caller's unit of work, so a reward and the signature that earned it land
        together or not at all. `document.data` is that letter's own printed fields
        (`_lettera_data`), read here rather than re-derived, so a reward matches
        exactly what the letter said the day it was signed. The budget itself comes
        from `match.company_budget_giornaliero`, snapshotted at match creation, never
        a live read of `Company.budget_giornaliero`: a company's own request can be
        edited weeks after the match, and the reward this match already promised must
        never move because of it.

        Only a `verificato` referral earns (REB-658). The freelancer is the letter's one
        signer -- Documenso mails the envelope to their address -- so their signature
        verifies a pending referral of theirs, `lettera`, in this same transaction. The
        company is not a signer: a pending company referral earns nothing here and
        waits for its referente's login (`verify_on_login`), which writes the reward
        from this letter."""
        for kind, entity_id in (("freelancer", match.freelancer_id), ("company", match.company_id)):
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
            if referral.stato == DA_VERIFICARE:
                if kind != "freelancer":
                    continue
                referral.stato = VERIFICATO
                referral.verified_at = document.signed_at or utcnow()
                referral.verified_via = "lettera"
            self._write_reward(referral.id, kind, document, match)

    def _write_reward(
        self, referral_id: UUID, kind: str, document: ContractDocument, match: Match
    ) -> None:
        """The one insert of a referral's reward, once per referral and never again
        (`ON CONFLICT DO NOTHING` on `uq_referral_rewards_referral_id`), at the rate of
        the referred side's kind in force now."""
        settings = self._settings.get_settings()
        rate = settings.rate_freelancer if kind == "freelancer" else settings.rate_company
        base = match_reward_base(match, letter_unit(document.data))
        self.session.execute(
            pg_insert(ReferralReward)
            .values(
                referral_id=referral_id,
                document_id=document.id,
                rate=rate,
                base_amount=base,
                reward_amount=None if base is None else (base * rate).quantize(_CENT),
            )
            .on_conflict_do_nothing(index_elements=[ReferralReward.referral_id])
        )

    # ---- the member's own view -------------------------------------------------------

    def for_user(self, user_id: UUID) -> MemberReferral:
        freelancer_rows = self.session.execute(
            select(Referral.created_at, User.nome, User.cognome, Freelancer.stato)
            .select_from(Referral)
            .join(Freelancer, Freelancer.id == Referral.entity_id)
            .join(User, User.id == Freelancer.user_id)
            .where(Referral.referrer_user_id == user_id, Referral.kind == "freelancer")
        ).all()
        company_rows = self.session.execute(
            select(Referral.created_at, Company.nome_azienda, Company.stato)
            .select_from(Referral)
            .join(Company, Company.id == Referral.entity_id)
            .where(Referral.referrer_user_id == user_id, Referral.kind == "company")
        ).all()
        referred = [
            MemberReferralItem(
                kind="freelancer",
                nome=f"{row.nome} {row.cognome}".strip(),
                created_at=row.created_at,
                stato=row.stato,
            )
            for row in freelancer_rows
        ] + [
            MemberReferralItem(
                kind="company", nome=row.nome_azienda, created_at=row.created_at, stato=row.stato
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
    def project_reward(match: Match, modalita: str, rate: Decimal) -> Decimal | None:
        """What a referral would earn on this match if its letter were signed now: the
        same `match_reward_base` the reward at signing uses, times `rate`, quantized the
        same way. `None` when there is nothing to project -- the match is cancelled or
        past the point a first signature can land (`EARNING_MATCH_STATES`), or the base
        is unknown (an `a corpo` letter with no `giorni_previsti`, or a snapshot never
        taken). The one projection, read by the «Match» list and by the ledger."""
        if match.cancelled_at is not None or match.stato not in EARNING_MATCH_STATES:
            return None
        base = match_reward_base(match, modalita)
        return None if base is None else (base * rate).quantize(_CENT)

    def for_matches(
        self, rows: Sequence[tuple[Match, ContractDocument | None]]
    ) -> dict[UUID, list[MatchReferral]]:
        """The referred sides of every match of a page, freelancer first then company,
        keyed by match id (a match with none is absent): three queries for the whole
        page whatever its size -- referrals with their referrer and his card, their
        rewards with the match each one's letter belongs to, and the rates. `rows` pairs
        each match with its current letter, the one whose `modalita` a projection reads.
        A reward on this match shows its real figures and state; one already earned on
        another match is `gia_maturato`, since a referral pays once; none yet is
        `previsto`, projected at the current rate."""
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
                    referrer.nome.label("nome"),
                    referrer.cognome.label("cognome"),
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
        rewards = {
            reward.referral_id: (reward, match_id)
            for reward, match_id in self.session.execute(
                select(ReferralReward, ContractDocument.match_id)
                .join(ContractDocument, ContractDocument.id == ReferralReward.document_id)
                .where(ReferralReward.referral_id.in_([r.Referral.id for r in referrals.values()]))
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
                if earned is None and not can_mature(kind, found.Referral.stato):
                    stato = DA_VERIFICARE
                elif earned is None:
                    stato = PREVISTO
                    rate_shown = rate
                    unit = letter_unit(letter.data) if letter is not None else ""
                    amount = self.project_reward(match, unit, rate)
                elif earned[1] == match.id:
                    reward = earned[0]
                    stato, rate_shown, amount = reward.stato, reward.rate, reward.reward_amount
                    reward_id = reward.id
                else:
                    stato = GIA_MATURATO
                out.setdefault(match.id, []).append(
                    MatchReferral(
                        kind=kind,
                        referrer_nome=f"{found.nome} {found.cognome}".strip(),
                        referrer_freelancer_id=found.card_id,
                        rate=rate_shown,
                        amount=amount,
                        stato=stato,
                        reward_id=reward_id,
                    )
                )
        return out
