"""What a merge moves (REB-800): the `RELATIONS` table, one row per kind of thing that
belongs to a person, the predicates that keep a move from colliding with a unique index
the survivor already occupies, and the three operations on them -- find the rows that
would move, move them, put the recorded ones back. `LEFT_IN_PLACE` is the list of
foreign keys to a person that a merge deliberately leaves where they are.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from rebase_core.db import PrimaryKeyMixin
from rebase_core.models import (
    AdminAction,
    CampaignRecipient,
    Company,
    GuideDownload,
    HubEngagement,
    Login,
    MagicLinkToken,
    Note,
    Referral,
    TalentCloudGrant,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    UserSession,
)

USER = "user"
FREELANCER = "freelancer"


@dataclass(frozen=True)
class Relation:
    """One thing that belongs to a person: the column that names them, whose id it is
    (`user` or `freelancer`), a filter that narrows the rows, and, for a relation with a
    unique index the survivor may already occupy, which of the rows may move."""

    key: str
    column: InstrumentedAttribute[Any]
    scope: str
    where: tuple[Any, ...] = ()
    movable: Callable[[Session, list[UUID], "Context"], list[UUID]] | None = None

    @property
    def model(self) -> type[PrimaryKeyMixin]:
        return cast("type[PrimaryKeyMixin]", self.column.class_)

    def ids(self, session: Session, source: UUID) -> list[UUID]:
        """Every row of this relation that names `source`."""
        return list(
            session.scalars(select(self.model.id).where(self.column == source, *self.where))
        )


@dataclass(frozen=True)
class Context:
    survivor_user: UUID
    absorbed_user: UUID
    survivor_card: UUID
    absorbed_card: UUID

    def survivor(self, relation: Relation) -> UUID:
        return self.survivor_user if relation.scope == USER else self.survivor_card

    def absorbed(self, relation: Relation) -> UUID:
        return self.absorbed_user if relation.scope == USER else self.absorbed_card


def _no_second_referral(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    """A card has one referral (`uq_referrals_kind_entity`): the survivor's own stays."""
    if session.scalar(
        select(Referral.id).where(
            Referral.kind == "freelancer", Referral.entity_id == ctx.survivor_card
        )
    ):
        return []
    return ids


def _no_self_referral(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    """A referral the absorbed person made of the survivor's card would become the
    survivor referring themselves: it stays with the card it was made for."""
    own = set(
        session.scalars(
            select(Referral.id).where(
                Referral.id.in_(ids),
                Referral.kind == "freelancer",
                Referral.entity_id == ctx.survivor_card,
            )
        )
    )
    return [i for i in ids if i not in own]


def _no_referrer_that_is_the_referred(
    session: Session, ids: list[UUID], ctx: Context
) -> list[UUID]:
    """The mirror: a referral the survivor made of the absorbed card stays as it was."""
    own = set(
        session.scalars(
            select(Referral.id).where(
                Referral.id.in_(ids),
                Referral.kind == "freelancer",
                Referral.referrer_user_id == ctx.survivor_user,
            )
        )
    )
    return [i for i in ids if i not in own]


def _not_a_second_or_a_self_referral(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    return _no_referrer_that_is_the_referred(session, _no_second_referral(session, ids, ctx), ctx)


def _not_on_the_same_request(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    taken = set(
        session.scalars(
            select(TeamRequestTalent.request_id).where(
                TeamRequestTalent.freelancer_id == ctx.survivor_card
            )
        )
    )
    rows = session.execute(
        select(TeamRequestTalent.id, TeamRequestTalent.request_id).where(
            TeamRequestTalent.id.in_(ids)
        )
    ).all()
    return [row_id for row_id, request_id in rows if request_id not in taken]


def _no_second_live_grant(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    live = set(
        session.scalars(
            select(TalentCloudGrant.company_id).where(
                TalentCloudGrant.user_id == ctx.survivor_user, TalentCloudGrant.revoked_at.is_(None)
            )
        )
    )
    rows = session.execute(
        select(TalentCloudGrant.id, TalentCloudGrant.company_id, TalentCloudGrant.revoked_at).where(
            TalentCloudGrant.id.in_(ids)
        )
    ).all()
    return [
        row_id for row_id, company, revoked in rows if revoked is not None or company not in live
    ]


RELATIONS: tuple[Relation, ...] = (
    Relation("companies", Company.user_id, USER),
    Relation("magic_links", MagicLinkToken.user_id, USER),
    Relation("logins", Login.user_id, USER),
    Relation("guide_downloads", GuideDownload.user_id, USER),
    Relation("sessions", UserSession.user_id, USER),
    Relation("campaign_recipients_user", CampaignRecipient.user_id, USER),
    Relation("team_proposals", TeamProposal.user_id, USER),
    Relation("team_requests", TeamRequest.user_id, USER),
    Relation("cloud_grants", TalentCloudGrant.user_id, USER, movable=_no_second_live_grant),
    Relation("referrals_made", Referral.referrer_user_id, USER, movable=_no_self_referral),
    Relation("hub_engagements", HubEngagement.freelancer_id, FREELANCER),
    Relation("campaign_recipients_card", CampaignRecipient.freelancer_id, FREELANCER),
    Relation(
        "team_request_talents",
        TeamRequestTalent.freelancer_id,
        FREELANCER,
        movable=_not_on_the_same_request,
    ),
    Relation(
        "referral_received",
        Referral.entity_id,
        FREELANCER,
        where=(Referral.kind == "freelancer",),
        movable=_not_a_second_or_a_self_referral,
    ),
    Relation(
        "notes",
        Note.entity_id,
        FREELANCER,
        where=(Note.entity_type == "freelancer",),
    ),
    Relation(
        "audit",
        AdminAction.entity_id,
        FREELANCER,
        where=(AdminAction.entity_type.in_(("freelancer", "freelancer_fiscal")),),
    ),
)

# What a relation was called in the entries written before it was renamed: `undo` reads the
# old key too, so a merge made while the thread was `comments` can still be put back.
_LEGACY_KEYS = {"notes": "comments"}

# Every foreign key to `users.id` or `freelancers.id` that the merge deliberately leaves
# where it is, and why. `test_merge` holds `RELATIONS` plus this list against the schema,
# so a table added later that points at a person fails there instead of staying behind.
LEFT_IN_PLACE: dict[str, str] = {
    "users.merged_into_id": "the pointer a merge writes, repointed by the merge itself",
    "freelancers.user_id": "the card stays on its own user row, soft-deleted",
    "user_contact_points.user_id": (
        "moved by the identity step with the absorbed login address (`ContactPointService."
        "absorb`), where a collision with the survivor's own points is decided"
    ),
    "freelancers.vetted_by": "an admin's name on a verification, never a talent's",
    "freelancer_cards.freelancer_id": "the CV's card, swapped with the CV, not moved",
    "admin_actions.admin_id": "the admin who acted",
    "notes.author_id": "who wrote the note, as written; the note itself moves with the card",
    "notes.edited_by_id": "the admin who edited the note",
    "notes.deleted_by_id": "the admin who deleted the note",
    "admin_tokens.user_id": "an admin's token; an admin is never merged away",
    "campaigns.created_by": "the admin who made it",
    "talent_cloud_grants.granted_by": "the admin who granted it",
    "talent_cloud_grants.revoked_by": "the admin who revoked it",
    "referral_rewards.confirmed_by": "the admin who confirmed it",
    "referral_settings.updated_by": "the admin who set it",
    "ai_providers.saved_by": "the admin who saved the key",
    "ai_usage.freelancer_id": "the cost of a call as made, about the card it was written for",
    "anonymous_cvs.freelancer_id": (
        "keyed by the CV's SHA-256: a moved CV no longer matches and is written again on "
        "the next download"
    ),
    "ai_sources.updated_by": "the admin who switched a source on or off",
    "freelancer_sources.freelancer_id": (
        "what pages said about the absorbed card, deleted by the merge; the survivor reads "
        "its own links"
    ),
    "card_eval_profiles.freelancer_id": "a CV's place in the evaluation sample stays with it",
    "card_eval_runs.created_by": "the admin who ran the evaluation",
    "card_eval_results.freelancer_id": "what a run wrote for one CV, about that card as it was",
    "card_batch_items.freelancer_id": (
        "a card's place in a regeneration batch, with hashes only: the result is applied "
        "only if the card's inputs are still those"
    ),
    "card_batches.started_by": "the admin who started the regeneration",
}


def _movable(session: Session, relation: Relation, ctx: Context) -> tuple[list[UUID], list[UUID]]:
    """The absorbed side's rows of a relation, and the ones of them that may move."""
    ids = relation.ids(session, ctx.absorbed(relation))
    return ids, relation.movable(session, ids, ctx) if relation.movable else ids


def move_relations(session: Session, ctx: Context) -> tuple[dict[str, list[UUID]], dict[str, int]]:
    """Moves every movable row to the survivor. Answers the ids moved per relation, and
    the count left in place per relation (`left_behind`)."""
    moved: dict[str, list[UUID]] = {}
    left_behind: dict[str, int] = {}
    for relation in RELATIONS:
        ids, allowed = _movable(session, relation, ctx)
        if allowed:
            session.execute(
                update(relation.model)
                .where(relation.model.id.in_(allowed))
                .values({relation.column.key: ctx.survivor(relation)})
            )
            moved[relation.key] = allowed
        if len(ids) != len(allowed):
            left_behind[relation.key] = len(ids) - len(allowed)
    return moved, left_behind


def count_movable(session: Session, ctx: Context) -> dict[str, int]:
    """What `move_relations` would move, as counts, writing nothing."""
    counts: dict[str, int] = {}
    for relation in RELATIONS:
        _, allowed = _movable(session, relation, ctx)
        if allowed:
            counts[relation.key] = len(allowed)
    return counts


def put_back_relations(
    session: Session, ctx: Context, recorded: dict[str, list[str]]
) -> dict[str, int]:
    """Moves back the rows a `merged` entry names (`recorded`: relation key to ids), only
    those still held by the survivor, so anything written since stays. Answers the count
    put back per relation."""
    put_back: dict[str, int] = {}
    for relation in RELATIONS:
        moved_ids = recorded.get(relation.key)
        if moved_ids is None and relation.key in _LEGACY_KEYS:
            moved_ids = recorded.get(_LEGACY_KEYS[relation.key])
        ids = [UUID(i) for i in moved_ids or []]
        if not ids:
            continue
        result = session.execute(
            update(relation.model)
            .where(relation.model.id.in_(ids), relation.column == ctx.survivor(relation))
            .values({relation.column.key: ctx.absorbed(relation)})
        )
        put_back[relation.key] = result.rowcount  # type: ignore[attr-defined]
    return put_back
