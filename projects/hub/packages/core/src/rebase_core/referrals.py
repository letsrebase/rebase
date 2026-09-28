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

The reward's base is rebase's own margin on that letter, `Company.budget_giornaliero`
against the letter's `compenso`, never the freelancer's fee alone (the contract flow's
own rule, `matches.py`'s module docstring): `reward_base` below is where the day-rate
question the P-REB-44 spike raised is answered, grounded in `Match.giorni_previsti`
(REB-497), an admin's own estimate of the engagement's billable days.
"""

import secrets
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import (
    REFERRAL_CODE_LENGTH,
    Company,
    ContractDocument,
    Freelancer,
    Match,
    Referral,
    ReferralReward,
    ReferralSettings,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.referral_schemas import (
    MemberReferral,
    MemberReferralItem,
    ReferralLedgerItem,
    ReferralLedgerList,
)

ENTITY = "referral"
# No 0/O/1/I: a code is read aloud or typed from a screenshot as often as it is clicked.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CENT = Decimal("0.01")
_SORT = SortSpec("created_at", "datetime")
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
STATE_ORDER = ("da_confermare", "confermato", "pagato")


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))


def reward_base(
    budget_giornaliero: Decimal, compenso: Decimal, modalita: str, giorni_previsti: int | None
) -> Decimal | None:
    """Rebase's own margin on a letter, the base a referral reward is a rate of.

    A day-rate letter (`modalita == 'a giornata'`, both `budget_giornaliero` and
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
    if modalita.strip() == "a giornata":
        margin_per_day = budget_giornaliero - compenso
        days = giorni_previsti if giorni_previsti is not None else 1
        return max(Decimal("0"), margin_per_day * days)
    if giorni_previsti is None:
        return None
    return max(Decimal("0"), budget_giornaliero * giorni_previsti - compenso)


class ReferralService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- a member's own code -------------------------------------------------------

    def code_for(self, user_id: UUID) -> str:
        """The member's own code, issued the first time anyone asks for one. A clash
        with another member's freshly-issued code is the same race `FreelancerService.
        apply` already recovers from: retry under a new code rather than fail the
        request over a coincidence this rare."""
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        if user.referral_code is not None:
            return user.referral_code
        for _ in range(5):
            user.referral_code = _generate_code()
            try:
                self.session.commit()
                return user.referral_code
            except IntegrityError:
                self.session.rollback()
                user = self.session.get(User, user_id)
                assert user is not None
                if user.referral_code is not None:
                    return user.referral_code
        raise ValidationFailed(ENTITY, "code", "non riesco a generare un codice libero, riprova")

    def resolve_referrer(self, code: str | None) -> User | None:
        """The member behind a code, or `None` for an empty or unknown one -- the
        wizard is public and mute, so an invalid `rif` is never an error, only a
        referral that does not happen."""
        if not code:
            return None
        return self.session.scalar(select(User).where(User.referral_code == code.strip().upper()))

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
        the same muteness the public routes that call this already keep."""
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
        self.session.commit()

    def referrer_name(self, kind: str, entity_id: UUID) -> str | None:
        """Who referred this freelancer or this company, as a contract prints a name
        (`rebase_core.matches`'s `_quadro_data`/`_lettera_data`): `None` prints as a
        blank line, the same convention every other optional field of these templates
        already uses."""
        row = self.session.execute(
            select(User.nome, User.cognome)
            .select_from(Referral)
            .join(User, User.id == Referral.referrer_user_id)
            .where(Referral.kind == kind, Referral.entity_id == entity_id)
        ).first()
        return None if row is None else f"{row.nome} {row.cognome}".strip()

    # ---- the reward, at the first signed letter --------------------------------------

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
        never move because of it."""
        settings = self.get_settings()
        modalita = str(document.data.get("modalita") or document.data.get("unita") or "")
        compenso = match.lettera_compenso
        budget = match.company_budget_giornaliero
        for kind, entity_id, rate in (
            ("freelancer", match.freelancer_id, settings.rate_freelancer),
            ("company", match.company_id, settings.rate_company),
        ):
            referral_id = self.session.scalar(
                select(Referral.id).where(Referral.kind == kind, Referral.entity_id == entity_id)
            )
            if referral_id is None:
                continue
            base = (
                None
                if compenso is None or budget is None
                else reward_base(budget, compenso, modalita, match.giorni_previsti)
            )
            reward = None if base is None else (base * rate).quantize(_CENT)
            self.session.execute(
                pg_insert(ReferralReward)
                .values(
                    referral_id=referral_id,
                    document_id=document.id,
                    rate=rate,
                    base_amount=base,
                    reward_amount=reward,
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
        return MemberReferral(code=self.code_for(user_id), referred=referred)

    # ---- the admin's two rates ---------------------------------------------------------

    def get_settings(self) -> ReferralSettings:
        """The one settings row, seeded by migration 0022; a second call in the same
        process never inserts again, `ReferralSettings.__doc__`'s own promise."""
        row = self.session.scalar(
            select(ReferralSettings).order_by(ReferralSettings.created_at).limit(1)
        )
        if row is not None:
            return row
        row = ReferralSettings()
        self.session.add(row)
        self.session.commit()
        return row

    def save_settings(
        self, rate_freelancer: Decimal, rate_company: Decimal, admin_id: UUID
    ) -> ReferralSettings:
        row = self.get_settings()
        row.rate_freelancer = rate_freelancer
        row.rate_company = rate_company
        row.updated_by = admin_id
        self.session.commit()
        return row

    # ---- the admin's ledger ----------------------------------------------------------

    def _to_ledger_item(
        self,
        referral: Referral,
        reward: ReferralReward | None,
        referrer_nome: str,
        referrer_email: str,
    ) -> ReferralLedgerItem:
        if referral.kind == "freelancer":
            fname = self.session.execute(
                select(User.nome, User.cognome)
                .select_from(Freelancer)
                .join(User, User.id == Freelancer.user_id)
                .where(Freelancer.id == referral.entity_id)
            ).first()
            referred_nome = f"{fname.nome} {fname.cognome}".strip() if fname else "-"
        else:
            company = self.session.get(Company, referral.entity_id)
            referred_nome = company.nome_azienda if company is not None else "-"
        match_id = None
        if reward is not None:
            document = self.session.get(ContractDocument, reward.document_id)
            match_id = document.match_id if document is not None else None
        return ReferralLedgerItem(
            referral_id=referral.id,
            reward_id=reward.id if reward is not None else None,
            kind=referral.kind,
            referrer_nome=referrer_nome,
            referrer_email=referrer_email,
            referred_nome=referred_nome,
            match_id=match_id,
            rate=reward.rate if reward is not None else None,
            base_amount=reward.base_amount if reward is not None else None,
            reward_amount=reward.reward_amount if reward is not None else None,
            stato=reward.stato if reward is not None else None,
            note=reward.note if reward is not None else None,
            created_at=referral.created_at,
            confirmed_at=reward.confirmed_at if reward is not None else None,
            paid_at=reward.paid_at if reward is not None else None,
        )

    def _ledger_query(self) -> Select[Any]:
        """Starts from `Referral`, outer-joined to its reward, so a referral with no
        reward yet (the referred party has not signed a first letter) still has a row
        on the ledger (P-REB-44) instead of being invisible until one exists."""
        referrer = User.__table__.alias("referrer")
        return (
            select(
                Referral,
                ReferralReward,
                referrer.c.nome.label("referrer_nome"),
                referrer.c.email.label("referrer_email"),
            )
            .select_from(Referral)
            .join(referrer, referrer.c.id == Referral.referrer_user_id)
            .outerjoin(ReferralReward, ReferralReward.referral_id == Referral.id)
        )

    def list_rewards(
        self,
        limit: int = LIST_LIMIT_DEFAULT,
        *,
        stato: str | None = None,
        cursor: str | None = None,
    ) -> ReferralLedgerList:
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        stmt = self._ledger_query()
        if stato is not None:
            stmt = stmt.where(ReferralReward.stato == stato)
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
        items = [
            self._to_ledger_item(
                row.Referral, row.ReferralReward, row.referrer_nome, row.referrer_email
            )
            for row in page_rows
        ]
        return ReferralLedgerList(items=items, next_cursor=next_cursor)

    def get_ledger_item(self, reward_id: UUID) -> ReferralLedgerItem:
        row = self.session.execute(
            self._ledger_query().where(ReferralReward.id == reward_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, reward_id)
        return self._to_ledger_item(
            row.Referral, row.ReferralReward, row.referrer_nome, row.referrer_email
        )

    def _require_reward(self, reward_id: UUID) -> ReferralReward:
        row = self.session.get(ReferralReward, reward_id)
        if row is None:
            raise NotFound(ENTITY, reward_id)
        return row

    def set_state(self, reward_id: UUID, stato: str, admin_id: UUID) -> ReferralReward:
        """`da_confermare` to `confermato` to `pagato`, one step at a time, never
        backwards: a wrong click is undone by an admin, from the ledger, not by this
        service quietly accepting any state from any state. Tracking only -- moving a
        reward to `pagato` records that the transfer happened outside the hub; nothing
        here moves money. Refuses to confirm a reward with no figure yet -- the same
        rule `set_price` already enforces from the other side, so a fixed-price letter
        with no estimate cannot be confirmed unpriced and then left unpriceable
        forever, whether the call comes from the ledger's own UI or straight at this
        endpoint."""
        row = self._require_reward(reward_id)
        if stato not in STATE_ORDER:
            raise ValidationFailed(ENTITY, "stato", "stato non valido")
        index = STATE_ORDER.index(row.stato)
        next_state = STATE_ORDER[index + 1] if row.stato != "pagato" else "niente"
        if STATE_ORDER.index(stato) != index + 1:
            raise ValidationFailed(ENTITY, "stato", f"da {row.stato} si passa solo a {next_state}")
        if stato == "confermato" and row.reward_amount is None:
            raise ValidationFailed(ENTITY, "stato", "il reward va prezzato prima di confermarlo")
        row.stato = stato
        if stato == "confermato":
            row.confirmed_by, row.confirmed_at = admin_id, utcnow()
        elif stato == "pagato":
            row.paid_at = utcnow()
        self.session.commit()
        return row

    def set_price(
        self, reward_id: UUID, base_amount: Decimal, reward_amount: Decimal
    ) -> ReferralReward:
        """What an admin fills in by hand for a reward `record_reward_if_signed` left
        with no figure (an `a corpo` letter with no `giorni_previsti` estimate).
        Refused once a state beyond `da_confermare` was already recorded against the
        figure it replaces -- price it, then move it, never the other way."""
        row = self._require_reward(reward_id)
        if row.stato != "da_confermare":
            raise ValidationFailed(
                ENTITY, "stato", "il prezzo si corregge solo prima della conferma"
            )
        row.base_amount, row.reward_amount = base_amount, reward_amount
        self.session.commit()
        return row
