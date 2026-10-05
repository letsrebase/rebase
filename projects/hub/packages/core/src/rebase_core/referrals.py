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

import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import Row, Select, and_, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import utcnow
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.match_words import LETTERA
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
    MatchReferral,
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


# `modalita` is free text end to end (`LetteraFields.modalita` is a bounded `SafeStr`, the
# admin form only offers «a giornata» and «a corpo» but the API takes any string, and the
# letter is also edited by hand), so the day-rate branch is a reading of the text, not an
# equality. How the mode OPENS decides: an optional noun that names the fee («tariffa»,
# «pagamento»), an optional connective (`a`, `per`, `al`, `su base`), then the day word.
# «a giornata (8 ore)», «tariffa giornaliera», «tariffa: giornaliera» and «compenso su
# base giornaliera» are day rates; «forfait 20 giornate», «mezza giornata» or «a ore
# (giornata da 8 ore)» merely mention a day. A mode that also says `a corpo`, the other
# mode the admin form offers, is a lump sum whatever else it says: «pagamento giornaliero
# (compenso a corpo)» is paid daily, but its fee is not a daily one.
_DAY_RATE_START = re.compile(
    r"(?:(?:tariffa|pagamento|compenso|corrispettivo|prezzo|costo|importo)[\s:-]+)?"
    r"(?:(?:a|per|al|su base|in base a)\s+)?"
    r"(?:giornat\w*|giornalier\w*|giorn[oi]\b)"
)
_LUMP_SUM = re.compile(r"\ba corpo\b")


def is_day_rate(modalita: str) -> bool:
    """Whether a letter's `modalita` prices the fee per day, whatever its spelling: case
    and surrounding or repeated whitespace are ignored, and the mode must open with
    `giornata`, `giornaliera`, `giorno` or `giorni`, optionally after `a `, `per `, `al `,
    `su base ` or `in base a ` and optionally after a noun naming the fee (`tariffa`,
    `pagamento`, `compenso`, `corrispettivo`, `prezzo`, `costo`, `importo`, with a colon
    or dash allowed after it), with anything after it (a note such as «(8 ore)»)
    ignored. A mode that only mentions a day further on (`forfait 20 giornate`, `mezza
    giornata`), or that says `a corpo` anywhere, is not one."""
    mode = " ".join(modalita.casefold().split())
    return _DAY_RATE_START.match(mode) is not None and _LUMP_SUM.search(mode) is None


def reward_base(
    budget_giornaliero: Decimal, compenso: Decimal, modalita: str, giorni_previsti: int | None
) -> Decimal | None:
    """Rebase's own margin on a letter, the base a referral reward is a rate of.

    A day-rate letter (`is_day_rate(modalita)`, both `budget_giornaliero` and
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
    if is_day_rate(modalita):
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
PREVISTO = "previsto"
GIA_MATURATO = "gia_maturato"


def letter_unit(data: Mapping[str, Any]) -> str:
    """How a letter prices its fee, `a giornata` or `a corpo`, as it printed it: the
    `modalita` the reward's base is decided on. The one reading of that field, shared by
    the reward at signing, its projection, and the lists that show the letter's fee."""
    return str(data.get("modalita") or data.get("unita") or "")


def match_reward_base(match: Match, modalita: str) -> Decimal | None:
    """`reward_base` on a match's own snapshots (`lettera_compenso`,
    `company_budget_giornaliero`, `giorni_previsti`); `None` when either snapshot is
    missing, exactly as the reward at signing leaves its figure blank."""
    compenso, budget = match.lettera_compenso, match.company_budget_giornaliero
    if compenso is None or budget is None:
        return None
    return reward_base(budget, compenso, modalita, match.giorni_previsti)


class _MatchFacts(NamedTuple):
    match: Match
    nome_azienda: str
    figura_richiesta: str
    freelancer_id: UUID
    freelancer_nome: str
    freelancer_deleted: bool
    letter: ContractDocument | None


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
        modalita = letter_unit(document.data)
        base = match_reward_base(match, modalita)
        for kind, entity_id, rate in (
            ("freelancer", match.freelancer_id, settings.rate_freelancer),
            ("company", match.company_id, settings.rate_company),
        ):
            referral_id = self.session.scalar(
                select(Referral.id).where(Referral.kind == kind, Referral.entity_id == entity_id)
            )
            if referral_id is None:
                continue
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
        settings = self.get_settings()
        return MemberReferral(
            code=self.code_for(user_id),
            rate_freelancer=settings.rate_freelancer,
            rate_company=settings.rate_company,
            referred=referred,
        )

    # ---- the admin's two rates ---------------------------------------------------------

    def get_settings(self) -> ReferralSettings:
        """The one settings row, seeded by migration 0024; a second call in the same
        process never inserts again, `ReferralSettings.__doc__`'s own promise. Flushed,
        never committed: `record_reward_if_signed` calls this inside the row locks
        `SigningService._confirm_completion` already holds, and a commit here would
        release them and close that transaction early, ahead of the reward and the
        signature it belongs to (CodeRabbit) -- the same reason `write_framework`
        never commits either."""
        row = self.session.scalar(
            select(ReferralSettings).order_by(ReferralSettings.created_at).limit(1)
        )
        if row is not None:
            return row
        row = ReferralSettings()
        self.session.add(row)
        self.session.flush()
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
        settings = self.get_settings()
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
                if earned is None:
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

    # ---- the admin's ledger ----------------------------------------------------------

    def _live_matches(self, pending: set[tuple[str, UUID]]) -> dict[tuple[str, UUID], UUID]:
        """For each referred side without a reward (`kind`, `entity_id`), its newest
        match that can still earn (not cancelled, letter not signed yet): the one the
        first signature would mature the reward on. One query for all of them, one row
        per side (`DISTINCT ON`), never a row per match the side ever had."""
        freelancer_ids = {entity for kind, entity in pending if kind == "freelancer"}
        company_ids = {entity for kind, entity in pending if kind == "company"}
        newest = []
        for kind, column, ids in (
            ("freelancer", Match.freelancer_id, freelancer_ids),
            ("company", Match.company_id, company_ids),
        ):
            if ids:
                newest.append(
                    select(literal(kind).label("kind"), column.label("entity_id"), Match.id)
                    .where(
                        Match.cancelled_at.is_(None),
                        Match.stato.in_(EARNING_MATCH_STATES),
                        column.in_(ids),
                    )
                    .distinct(column)
                    .order_by(column, Match.created_at.desc(), Match.id.desc())
                    .subquery()
                )
        if not newest:
            return {}
        rows = self.session.execute(union_all(*(select(sub) for sub in newest)))
        return {(kind, entity_id): match_id for kind, entity_id, match_id in rows}

    def _match_facts(self, match_ids: Iterable[UUID]) -> dict[UUID, _MatchFacts]:
        ids = set(match_ids)
        if not ids:
            return {}
        freelancer_user = aliased(User)
        newer = aliased(ContractDocument)
        current_letter = (
            select(newer.id)
            .where(newer.match_id == Match.id, newer.kind == LETTERA)
            .order_by(newer.created_at.desc(), newer.id.desc())
            .limit(1)
            .correlate(Match)
            .scalar_subquery()
        )
        rows = self.session.execute(
            select(Match, Company, freelancer_user, Freelancer.deleted_at, ContractDocument)
            .join(Company, Company.id == Match.company_id)
            .join(Freelancer, Freelancer.id == Match.freelancer_id)
            .join(freelancer_user, freelancer_user.id == Freelancer.user_id)
            .outerjoin(ContractDocument, ContractDocument.id == current_letter)
            .where(Match.id.in_(ids))
        ).all()
        return {
            match.id: _MatchFacts(
                match,
                company.nome_azienda,
                company.figura_richiesta,
                match.freelancer_id,
                f"{user.nome} {user.cognome}".strip(),
                freelancer_deleted_at is not None,
                letter,
            )
            for match, company, user, freelancer_deleted_at, letter in rows
        }

    def _ledger_items(self, rows: Sequence[Row[Any]]) -> list[ReferralLedgerItem]:
        """The page's rows as ledger items, every lookup batched over the whole page:
        the referred names (two queries at most), the match each reward's letter belongs
        to, the newest live match of each referral without a reward, those matches'
        facts, and the rates -- a bounded handful of queries, never one per row."""
        if not rows:
            return []
        settings = self.get_settings()
        names: dict[tuple[str, UUID], str] = {}
        deleted: set[tuple[str, UUID]] = set()
        freelancer_ids = {r.Referral.entity_id for r in rows if r.Referral.kind == "freelancer"}
        company_ids = {r.Referral.entity_id for r in rows if r.Referral.kind == "company"}
        if freelancer_ids:
            for entity_id, nome, cognome, deleted_at in self.session.execute(
                select(Freelancer.id, User.nome, User.cognome, Freelancer.deleted_at)
                .join(User, User.id == Freelancer.user_id)
                .where(Freelancer.id.in_(freelancer_ids))
            ):
                names["freelancer", entity_id] = f"{nome} {cognome}".strip()
                if deleted_at is not None:
                    deleted.add(("freelancer", entity_id))
        if company_ids:
            for entity_id, nome_azienda, deleted_at in self.session.execute(
                select(Company.id, Company.nome_azienda, Company.deleted_at).where(
                    Company.id.in_(company_ids)
                )
            ):
                names["company", entity_id] = nome_azienda
                if deleted_at is not None:
                    deleted.add(("company", entity_id))
        reward_ids = [r.ReferralReward.id for r in rows if r.ReferralReward is not None]
        reward_match: dict[UUID, UUID | None] = (
            {
                reward_id: match_id
                for reward_id, match_id in self.session.execute(
                    select(ReferralReward.id, ContractDocument.match_id)
                    .join(ContractDocument, ContractDocument.id == ReferralReward.document_id)
                    .where(ReferralReward.id.in_(reward_ids))
                )
            }
            if reward_ids
            else {}
        )
        live = self._live_matches(
            {(r.Referral.kind, r.Referral.entity_id) for r in rows if r.ReferralReward is None}
        )
        facts = self._match_facts(
            {m for m in reward_match.values() if m is not None} | set(live.values())
        )
        items = []
        for row in rows:
            referral, reward = row.Referral, row.ReferralReward
            key = (referral.kind, referral.entity_id)
            match_id = reward_match.get(reward.id) if reward is not None else live.get(key)
            found = facts.get(match_id) if match_id is not None else None
            projected_rate = projected_amount = None
            if reward is None and found is not None:
                projected_rate = (
                    settings.rate_freelancer
                    if referral.kind == "freelancer"
                    else settings.rate_company
                )
                unit = letter_unit(found.letter.data) if found.letter is not None else ""
                projected_amount = self.project_reward(found.match, unit, projected_rate)
            items.append(
                ReferralLedgerItem(
                    referral_id=referral.id,
                    reward_id=reward.id if reward is not None else None,
                    kind=referral.kind,
                    referred_id=referral.entity_id,
                    referrer_nome=f"{row.referrer_nome} {row.referrer_cognome}".strip(),
                    referrer_email=row.referrer_email,
                    referrer_freelancer_id=row.referrer_freelancer_id,
                    referred_nome=names.get(key, "-"),
                    referred_deleted=key in deleted,
                    match_id=match_id if found is not None else None,
                    match_freelancer_id=found.freelancer_id if found is not None else None,
                    match_freelancer_nome=found.freelancer_nome if found is not None else None,
                    match_freelancer_deleted=found.freelancer_deleted
                    if found is not None
                    else False,
                    match_nome_azienda=found.nome_azienda if found is not None else None,
                    match_figura_richiesta=found.figura_richiesta if found is not None else None,
                    projected_rate=projected_rate,
                    projected_amount=projected_amount,
                    rate=reward.rate if reward is not None else None,
                    base_amount=reward.base_amount if reward is not None else None,
                    reward_amount=reward.reward_amount if reward is not None else None,
                    stato=reward.stato if reward is not None else None,
                    note=reward.note if reward is not None else None,
                    created_at=referral.created_at,
                    confirmed_at=reward.confirmed_at if reward is not None else None,
                    paid_at=reward.paid_at if reward is not None else None,
                )
            )
        return items

    def _ledger_query(self) -> Select[Any]:
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
                referrer.nome.label("referrer_nome"),
                referrer.cognome.label("referrer_cognome"),
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
        return ReferralLedgerList(items=self._ledger_items(page_rows), next_cursor=next_cursor)

    def get_ledger_item(self, reward_id: UUID) -> ReferralLedgerItem:
        row = self.session.execute(
            self._ledger_query().where(ReferralReward.id == reward_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, reward_id)
        return self._ledger_items([row])[0]

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
