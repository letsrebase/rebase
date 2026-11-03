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
from rebase_core.match_words import LETTERA
from rebase_core.models import (
    Company,
    ContractDocument,
    Freelancer,
    Match,
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
    EARNING_MATCH_STATES,
    ENTITY,
    ReferralService,
    can_mature,
    letter_unit,
)

# One row of `_ledger_query`: the referral, its reward and the referrer's card are outer
# joins, so the second and the last column are `None` for a referral with no reward yet
# or a referrer with no card. SQLAlchemy types the select without the `None` (a join does
# not narrow a column's type); the rows are read under this alias, which carries it.
_LedgerRow = Row[Referral, ReferralReward | None, str, str, str, UUID | None]
_SORT = SortSpec("created_at", "datetime")
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
STATE_ORDER = ("da_confermare", "confermato", "pagato")


class _MatchFacts(NamedTuple):
    match: Match
    nome_azienda: str
    figura_richiesta: str
    freelancer_id: UUID
    freelancer_nome: str
    freelancer_deleted: bool
    letter: ContractDocument | None


class ReferralLedgerService:
    def __init__(self, session: Session) -> None:
        self.session = session

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
                    .ext(distinct_on(column))
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

    def _ledger_items(self, rows: Sequence[_LedgerRow]) -> list[ReferralLedgerItem]:
        """The page's rows as ledger items, every lookup batched over the whole page:
        the referred names (two queries at most), the match each reward's letter belongs
        to, the newest live match of each referral without a reward, those matches'
        facts, and the rates -- a bounded handful of queries, never one per row."""
        if not rows:
            return []
        settings = ReferralSettingsService(self.session).get_settings()
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
            {
                (r.Referral.kind, r.Referral.entity_id)
                for r in rows
                if r.ReferralReward is None and can_mature(r.Referral.kind, r.Referral.stato)
            }
        )
        facts = self._match_facts(
            {m for m in reward_match.values() if m is not None} | set(live.values())
        )
        evidence = referral_evidence(self.session, [(r.Referral, r.referrer_email) for r in rows])
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
                projected_amount = ReferralService.project_reward(found.match, unit, projected_rate)
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
                    referral_stato=referral.stato,
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
        """The reward, locked `FOR UPDATE` until the caller's commit: `set_state` and
        `set_price` both read `stato`, decide, then write, and two admins (or a double
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
