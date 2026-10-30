"""Merging one person into another, and undoing it (REB-800).

`PersonMergeService.merge(into, absorbed, admin)` is for two cards of one person: the
survivor keeps its card and takes in everything the absorbed one had. **Nothing is
deleted.** The absorbed card is soft-deleted (`deleted_at`), its `users` row keeps its
address and points at the survivor (`merged_into_id`), and the one `merged` entry in the
trail names every row that moved, by id, which is also what `undo` reads to move them
back. The survivor's own trail gets an `absorbed` entry with the counts.

**What moves** is the `RELATIONS` below, each by id: the logins, sessions and magic links,
the company requests, the team asks and talent-cloud grants, the campaign rows, the
referrals the person made or came through, the matches, the contract documents, the
notes, the audit entries, the talent's asks on team requests, the tax data. A
row that would collide with one the survivor already has (a referral for the same card,
a talent already on the same request, a live cloud grant for the same company, a second
tax record) stays where it was and is counted as `lasciati`.

**The CV is the newest of the two.** `freelancers.cv_uploaded_at` says which; the CV and
its anonymous card travel together, so if the absorbed card holds the newer one the two
swap and the older file and card stay, reachable, on the soft-deleted card. A survivor
left with a CV and no card, or only a failure for that CV, is flagged
(`scheda_da_rigenerare`) and its failure is cleared, so the hourly `cards-refresh` writes
it: the merge never calls Claude.

**Profile fields** the survivor leaves empty are filled from the absorbed card; the first
attribution (the UTM set and the page) stays, the older card's; the
further `stato` wins over `nuovo`. Every changed value is kept in the entry, as it was.

**What stops it**, each with a sentence: the same card, an admin as the absorbed side, two
different tax codes, a live framework agreement of the same regime on both sides, or an
active match with the same company on both.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Text, cast, delete, func, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from rebase_core.audit import utcnow
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import (
    ATTRIBUTION_COLUMNS,
    AUTORE_MAX_LENGTH,
    AdminAction,
    CampaignOptout,
    CampaignRecipient,
    Company,
    ContractDocument,
    Freelancer,
    FreelancerCard,
    FreelancerFiscal,
    GuideDownload,
    Login,
    MagicLinkToken,
    Match,
    Note,
    Referral,
    TalentCloudGrant,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
    UserSession,
)
from rebase_core.sources import delete_sources
from rebase_core.users import UserService

ENTITY = "freelancer"
USER = "user"
FREELANCER = "freelancer"

_LIVE_FRAMEWORK = ("inviato", "in_attesa", "firmato")
_LIVE_MATCH = ("in_firma", "attivo")
# Fields of the card the survivor takes from the absorbed one when it has none.
_FILLED_FIELDS = (
    "posizione",
    "tariffa_giornaliera",
    "remoto",
    "seniority_admin",
    "anni_admin",
    "vetted_at",
    "vetted_by",
)
_CARD_COLUMNS = (
    "cv_sha256",
    "card",
    "model",
    "input_tokens",
    "output_tokens",
    "generated_at",
    "error",
    "error_cv_sha256",
    "prompt_version",
    "provider",
    "fonti",
    "source_hashes",
    "conflicts",
    "thinking_tokens",
    "persona",
    "created_at",
    "updated_at",
)
_CV_COLUMNS = ("cv_bytes", "cv_filename", "cv_mime", "cv_size", "cv_uploaded_at")


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


@dataclass(frozen=True)
class Context:
    survivor_user: UUID
    absorbed_user: UUID
    survivor_card: UUID
    absorbed_card: UUID


def _model(relation: Relation) -> Any:
    return relation.column.class_


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


def _no_second_tax_record(session: Session, ids: list[UUID], ctx: Context) -> list[UUID]:
    if session.scalar(
        select(FreelancerFiscal.id).where(FreelancerFiscal.freelancer_id == ctx.survivor_card)
    ):
        return []
    return ids


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
    Relation("matches", Match.freelancer_id, FREELANCER),
    Relation("contract_documents", ContractDocument.freelancer_id, FREELANCER),
    Relation("campaign_recipients_card", CampaignRecipient.freelancer_id, FREELANCER),
    Relation(
        "team_request_talents",
        TeamRequestTalent.freelancer_id,
        FREELANCER,
        movable=_not_on_the_same_request,
    ),
    Relation("fiscal", FreelancerFiscal.freelancer_id, FREELANCER, movable=_no_second_tax_record),
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
    "freelancers.vetted_by": "an admin's name on a verification, never a talent's",
    "freelancer_cards.freelancer_id": "the CV's card, swapped with the CV, not moved",
    "freelancer_fiscal.updated_by": "the admin who saved it",
    "matches.created_by": "the admin who made it",
    "contract_documents.created_by": "the admin who made it",
    "contract_documents.sent_by": "the admin who sent it",
    "admin_actions.admin_id": "the admin who acted",
    "notes.autore_id": "who wrote the note, as written; the note itself moves with the card",
    "notes.modificata_da_id": "the admin who edited the note",
    "notes.eliminata_da_id": "the admin who deleted the note",
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


class MergeRead(BaseModel):
    """What a merge did, for the admin who ran it. Counts, never the rows."""

    survivor_freelancer_id: UUID
    absorbed_freelancer_id: UUID
    action_id: UUID
    moved: dict[str, int]
    lasciati: dict[str, int]
    cv: str
    scheda_da_rigenerare: bool


class MergeRequest(BaseModel):
    """The body of the admin merge: the card to absorb, by freelancer id."""

    model_config = ConfigDict(extra="forbid")

    da: UUID


class MergeRefused(InvalidState):
    """A merge that must not run, with the sentence an admin reads."""


def _name(user: User) -> str:
    return f"{user.nome} {user.cognome}".strip()


class PersonMergeService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- merge ------------------------------------------------------------------------

    def merge(self, into_id: UUID, absorbed_id: UUID, admin_id: UUID) -> MergeRead:
        """Merges the card `absorbed_id` into `into_id`, in one transaction, as above."""
        if into_id == absorbed_id:
            raise ValidationFailed(ENTITY, "da", "servono due schede diverse")
        survivor = self._live(into_id)
        absorbed = self._live(absorbed_id)
        s_user, a_user = self._user(survivor), self._user(absorbed)
        self._lock(s_user, a_user, survivor, absorbed)
        self._still_live(survivor, absorbed, s_user, a_user)
        self._check(survivor, absorbed, s_user, a_user)
        admin = self.session.get(User, admin_id)
        assert admin is not None
        ctx = Context(s_user.id, a_user.id, survivor.id, absorbed.id)

        moved: dict[str, list[UUID]] = {}
        left: dict[str, int] = {}
        for relation in RELATIONS:
            model = _model(relation)
            source = ctx.absorbed_user if relation.scope == USER else ctx.absorbed_card
            target = ctx.survivor_user if relation.scope == USER else ctx.survivor_card
            ids = self._ids(relation, source)
            allowed = relation.movable(self.session, ids, ctx) if relation.movable else ids
            if allowed:
                self.session.execute(
                    update(model).where(model.id.in_(allowed)).values({relation.column.key: target})
                )
                moved[relation.key] = allowed
            if len(ids) != len(allowed):
                left[relation.key] = len(ids) - len(allowed)
        undo: dict[str, Any] = {}
        undo["filled"] = self._fill_profile(survivor, absorbed)
        undo["cv_swapped"], cv = self._keep_newest_cv(survivor, absorbed)
        undo["cv_sha_after"] = (
            hashlib.sha256(survivor.cv_bytes).hexdigest() if survivor.cv_bytes else None
        )
        undo["card_error_cleared"] = self._clear_failed_card(survivor)
        undo["filled_after"] = {field: _plain(getattr(survivor, field)) for field in undo["filled"]}
        undo["user"] = self._merge_identity(s_user, a_user)
        undo["optout_copied"] = self._carry_optout(a_user, s_user)
        undo["team_refs"] = self._retarget_proposals(absorbed.id, survivor.id)
        absorbed.deleted_at = utcnow()
        # What other pages said about a card that no longer exists is not kept (REB-837);
        # the survivor reads its own links, and an undo reads the absorbed card's again.
        delete_sources(self.session, absorbed.id)
        self.session.flush()
        flagged = self._needs_card(survivor)

        author = _name(admin)[:AUTORE_MAX_LENGTH] or "admin"
        self.session.add(
            Note(
                entity_type="freelancer",
                entity_id=survivor.id,
                testo=(
                    f"Scheda unita con quella dell'indirizzo {a_user.email}: "
                    "una sola persona, il CV più recente resta qui."
                ),
                autore=author,
                autore_id=admin_id,
                fonte="sistema",
            )
        )
        payload = {
            "into_freelancer_id": survivor.id,
            "into_user_id": s_user.id,
            "moved": {key: [str(i) for i in ids] for key, ids in moved.items()},
            "lasciati": left,
            "cv": cv,
            "scheda_da_rigenerare": flagged,
            **undo,
        }
        merged = AdminAction(
            entity_type="freelancer",
            entity_id=absorbed.id,
            kind="merged",
            admin_id=admin_id,
            payload=_json(payload),
        )
        self.session.add(merged)
        self.session.add(
            AdminAction(
                entity_type="freelancer",
                entity_id=survivor.id,
                kind="absorbed",
                admin_id=admin_id,
                payload=_json(
                    {
                        "from_freelancer_id": absorbed.id,
                        "from_user_id": a_user.id,
                        "moved": {key: len(ids) for key, ids in moved.items()},
                        "lasciati": left,
                        "cv": cv,
                        "scheda_da_rigenerare": flagged,
                    }
                ),
            )
        )
        self.session.commit()
        return MergeRead(
            survivor_freelancer_id=survivor.id,
            absorbed_freelancer_id=absorbed.id,
            action_id=merged.id,
            moved={key: len(ids) for key, ids in moved.items()},
            lasciati=left,
            cv=cv,
            scheda_da_rigenerare=flagged,
        )

    def plan(self, into_id: UUID, absorbed_id: UUID) -> dict[str, int]:
        """What `merge` would move, as counts, writing nothing: what the dry run prints.
        Raises the same refusals `merge` does."""
        survivor, absorbed = self._live(into_id), self._live(absorbed_id)
        s_user, a_user = self._user(survivor), self._user(absorbed)
        self._check(survivor, absorbed, s_user, a_user)
        ctx = Context(s_user.id, a_user.id, survivor.id, absorbed.id)
        counts: dict[str, int] = {}
        for relation in RELATIONS:
            source = ctx.absorbed_user if relation.scope == USER else ctx.absorbed_card
            ids = self._ids(relation, source)
            allowed = relation.movable(self.session, ids, ctx) if relation.movable else ids
            if allowed:
                counts[relation.key] = len(allowed)
        return counts

    # ---- undo -------------------------------------------------------------------------

    def undo(self, action_id: UUID, admin_id: UUID) -> MergeRead:
        """Puts a merge back as it was: every row the `merged` entry names goes back to
        the absorbed card and person (only if it still sits with the survivor, anything
        written since stays), the survivor's filled fields and CV are restored, the
        absorbed card and address are live again. Once; a merge already undone is
        refused. Rows the survivor got *after* the merge (a new login, a note) stay
        with the survivor."""
        action = self.session.get(AdminAction, action_id)
        if action is None or action.kind != "merged":
            raise NotFound("merge", action_id)
        absorbed = self.session.get(Freelancer, action.entity_id)
        survivor = self.session.get(Freelancer, UUID(action.payload["into_freelancer_id"]))
        if absorbed is None or survivor is None:
            raise NotFound(ENTITY, action.entity_id)
        s_user, a_user = self._user(survivor), self._user(absorbed)
        self._lock(s_user, a_user, survivor, absorbed)
        done = self.session.scalar(
            select(AdminAction.id).where(
                AdminAction.kind == "unmerged",
                AdminAction.entity_id == action.entity_id,
                AdminAction.payload["action_id"].astext == str(action.id),
            )
        )
        if done is not None:
            raise ValidationFailed(ENTITY, "action_id", "questa unione è già stata annullata")
        if survivor.deleted_at is not None or s_user.merged_into_id is not None:
            raise MergeRefused(
                "La scheda che aveva assorbito è stata a sua volta unita o cancellata: "
                "annulla prima quella unione."
            )
        payload = action.payload
        if payload["cv_swapped"]:
            now = hashlib.sha256(survivor.cv_bytes).hexdigest() if survivor.cv_bytes else None
            if now != payload["cv_sha_after"]:
                raise MergeRefused(
                    "Dopo l'unione la scheda ha un CV diverso: annullare rimetterebbe quello "
                    "vecchio al suo posto. Rimetti a mano i CV, poi annulla."
                )
        ctx = Context(s_user.id, a_user.id, survivor.id, absorbed.id)

        # A field the survivor has changed since the merge keeps its new value: it is
        # restored only while it still holds what the merge wrote.
        user_undo = payload["user"]
        left: dict[str, int] = {}
        users = UserService(self.session)
        # The LinkedIn first, and the survivor's before the absorbed one's: the unique
        # index allows one holder of a profile at a time.
        if s_user.linkedin_url == user_undo["survivor_linkedin_after"]:
            before = user_undo["survivor_linkedin_before"]
            taken_by = users.by_linkedin(before)
            if taken_by is None or taken_by.id == s_user.id:
                s_user.linkedin_url = before
                self.session.flush()
            else:
                left["linkedin_sopravvissuto"] = 1
        elif user_undo["survivor_linkedin_before"] != s_user.linkedin_url:
            left["linkedin_sopravvissuto"] = 1
        # A profile both cards held before the merge cannot be held by both again once
        # the unique index exists: the survivor keeps it, and the card that comes back
        # has none until an admin gives it one.
        profile = user_undo["absorbed_linkedin_before"]
        if profile is not None and users.by_linkedin(profile) is not None:
            profile = None
            left["linkedin"] = 1
        a_user.linkedin_url = profile
        a_user.merged_into_id = None
        if s_user.telefono == user_undo["survivor_telefono_after"]:
            s_user.telefono = user_undo["survivor_telefono_before"]
        elif s_user.telefono != user_undo["survivor_telefono_before"]:
            left["telefono"] = 1
        for alias in user_undo["aliases"]:
            self.session.execute(
                update(User).where(User.id == UUID(alias)).values(merged_into_id=a_user.id)
            )
        # The failure was cleared on the card the survivor held *after* the swap, so it
        # goes back before the swap is undone.
        self._restore_card_error(survivor, payload["card_error_cleared"])
        if payload["cv_swapped"]:
            self._swap_cv_and_card(survivor, absorbed)
        for proposal_id in payload.get("team_refs", []):
            proposal = self.session.get(TeamProposal, UUID(proposal_id))
            if proposal is not None:
                proposal.team = [
                    {**member, "freelancer_id": str(absorbed.id)}
                    if member.get("freelancer_id") == str(survivor.id)
                    else member
                    for member in proposal.team
                ]
        changed_since = 0
        for field, before in payload["filled"].items():
            if _plain(getattr(survivor, field)) == payload["filled_after"][field]:
                setattr(survivor, field, _restore(Freelancer, field, before))
            else:
                changed_since += 1
        if changed_since:
            left["campi"] = changed_since
        if payload["optout_copied"]:
            self.session.execute(
                delete(CampaignOptout).where(
                    CampaignOptout.email == s_user.email.lower(),
                    CampaignOptout.created_at <= action.created_at,
                )
            )

        put_back: dict[str, int] = {}
        for relation in RELATIONS:
            moved_ids = payload["moved"].get(relation.key)
            if moved_ids is None and relation.key in _LEGACY_KEYS:
                moved_ids = payload["moved"].get(_LEGACY_KEYS[relation.key])
            ids = [UUID(i) for i in moved_ids or []]
            if not ids:
                continue
            model = _model(relation)
            holder = ctx.survivor_user if relation.scope == USER else ctx.survivor_card
            target = ctx.absorbed_user if relation.scope == USER else ctx.absorbed_card
            result = self.session.execute(
                update(model)
                .where(model.id.in_(ids), relation.column == holder)
                .values({relation.column.key: target})
            )
            put_back[relation.key] = result.rowcount  # type: ignore[attr-defined]
        absorbed.deleted_at = None
        self.session.flush()
        for entity_id in (absorbed.id, survivor.id):
            self.session.add(
                AdminAction(
                    entity_type="freelancer",
                    entity_id=entity_id,
                    kind="unmerged",
                    admin_id=admin_id,
                    payload=_json({"action_id": action.id, "rimesse": put_back}),
                )
            )
        self.session.commit()
        return MergeRead(
            survivor_freelancer_id=survivor.id,
            absorbed_freelancer_id=absorbed.id,
            action_id=action.id,
            moved=put_back,
            lasciati=left,
            cv="ripristinato" if payload["cv_swapped"] else "invariato",
            scheda_da_rigenerare=False,
        )

    # ---- the checks -------------------------------------------------------------------

    def _check(
        self, survivor: Freelancer, absorbed: Freelancer, s_user: User, a_user: User
    ) -> None:
        if a_user.role == "admin":
            raise MergeRefused(
                "La scheda da assorbire è di un amministratore: unisci l'altra nella sua.",
            )
        s_tax = self.session.scalar(
            select(FreelancerFiscal.codice_fiscale).where(
                FreelancerFiscal.freelancer_id == survivor.id
            )
        )
        a_tax = self.session.scalar(
            select(FreelancerFiscal.codice_fiscale).where(
                FreelancerFiscal.freelancer_id == absorbed.id
            )
        )
        if s_tax and a_tax and s_tax.strip().upper() != a_tax.strip().upper():
            raise MergeRefused(
                "Le due schede hanno codici fiscali diversi: non sono la stessa persona.",
            )
        regimes = self._framework_regimes(survivor.id) & self._framework_regimes(absorbed.id)
        if regimes:
            raise MergeRefused(
                "Entrambe le schede hanno un contratto quadro in corso o firmato "
                f"({', '.join(sorted(regimes))}): annulla o disdici uno dei due prima di unirle.",
            )
        shared = self._active_companies(survivor.id) & self._active_companies(absorbed.id)
        if shared:
            raise MergeRefused(
                "Entrambe le schede hanno un incarico in corso con la stessa azienda: "
                "chiudi o annulla uno dei due prima di unirle.",
            )

    def _framework_regimes(self, freelancer_id: UUID) -> set[str]:
        rows = self.session.execute(
            select(
                ContractDocument.regime, ContractDocument.stato, ContractDocument.notice_at
            ).where(
                ContractDocument.freelancer_id == freelancer_id,
                ContractDocument.kind == "quadro",
                ContractDocument.stato.in_(_LIVE_FRAMEWORK),
            )
        ).all()
        return {regime for regime, stato, notice in rows if stato != "firmato" or notice is None}

    def _active_companies(self, freelancer_id: UUID) -> set[UUID]:
        return set(
            self.session.scalars(
                select(Match.company_id).where(
                    Match.freelancer_id == freelancer_id, Match.stato.in_(_LIVE_MATCH)
                )
            )
        )

    # ---- the parts of a merge ---------------------------------------------------------

    def _fill_profile(self, survivor: Freelancer, absorbed: Freelancer) -> dict[str, Any]:
        """Fills what the survivor leaves empty; answers the survivor's values before."""
        before: dict[str, Any] = {}
        for field in _FILLED_FIELDS:
            if getattr(survivor, field) is None and getattr(absorbed, field) is not None:
                before[field] = None
                setattr(survivor, field, getattr(absorbed, field))
        if not survivor.links and absorbed.links:
            before["links"] = list(survivor.links)
            survivor.links = list(absorbed.links)
        if survivor.stato == "nuovo" and absorbed.stato in ("contattato", "attivo"):
            before["stato"] = survivor.stato
            survivor.stato = absorbed.stato
        older = absorbed if absorbed.created_at < survivor.created_at else survivor
        has_own = any(getattr(survivor, c) for c in ATTRIBUTION_COLUMNS)
        if (older is absorbed or not has_own) and any(
            getattr(absorbed, c) for c in ATTRIBUTION_COLUMNS
        ):
            for column in ATTRIBUTION_COLUMNS:
                before[column] = getattr(survivor, column)
                setattr(survivor, column, getattr(absorbed, column))
        return {key: _plain(value) for key, value in before.items()}

    def _keep_newest_cv(self, survivor: Freelancer, absorbed: Freelancer) -> tuple[bool, str]:
        """The survivor ends with the newest CV and its card; the older stays on the
        absorbed card. Answers whether the two swapped, and which CV the survivor holds."""
        if absorbed.cv_bytes is None:
            return False, "invariato" if survivor.cv_bytes is not None else "nessuno"
        newer = survivor.cv_bytes is None or _stamp(absorbed) > _stamp(survivor)
        if not newer:
            return False, "invariato"
        self._swap_cv_and_card(survivor, absorbed)
        return True, "assorbita"

    def _swap_cv_and_card(self, first: Freelancer, second: Freelancer) -> None:
        for column in _CV_COLUMNS:
            a, b = getattr(first, column), getattr(second, column)
            setattr(first, column, b)
            setattr(second, column, a)
        cards = {
            freelancer_id: self.session.get(FreelancerCard, freelancer_id)
            for freelancer_id in (first.id, second.id)
        }
        copies = {
            freelancer_id: {c: getattr(card, c) for c in _CARD_COLUMNS}
            for freelancer_id, card in cards.items()
            if card is not None
        }
        for card in cards.values():
            if card is not None:
                self.session.delete(card)
        self.session.flush()
        for source, target in ((first.id, second.id), (second.id, first.id)):
            if source in copies:
                self.session.add(FreelancerCard(freelancer_id=target, **copies[source]))
        self.session.flush()

    def _clear_failed_card(self, survivor: Freelancer) -> dict[str, Any] | None:
        """A failure recorded for the CV the survivor now holds would keep `cards-refresh`
        from trying it again: cleared, with its old value kept for the undo."""
        card = self.session.get(FreelancerCard, survivor.id)
        if card is None or card.error is None:
            return None
        before = {"error": card.error, "error_cv_sha256": card.error_cv_sha256}
        card.error, card.error_cv_sha256 = None, None
        return before

    def _restore_card_error(self, survivor: Freelancer, before: dict[str, Any] | None) -> None:
        if before is None:
            return
        card = self.session.get(FreelancerCard, survivor.id)
        if card is not None:
            card.error, card.error_cv_sha256 = before["error"], before["error_cv_sha256"]

    def _needs_card(self, survivor: Freelancer) -> bool:
        """Whether the survivor holds a CV the card was not written from."""
        if survivor.cv_bytes is None:
            return False
        digest = self.session.scalar(
            select(func.encode(func.sha256(Freelancer.cv_bytes), "hex")).where(
                Freelancer.id == survivor.id
            )
        )
        card = self.session.get(FreelancerCard, survivor.id)
        return card is None or card.card is None or card.cv_sha256 != digest

    def _merge_identity(self, survivor: User, absorbed: User) -> dict[str, Any]:
        """The `users` rows: the absorbed address points at the survivor and gives up its
        LinkedIn profile (one holder per profile), a missing profile or phone number on the
        survivor is filled from it. Answers what the undo needs."""
        undo: dict[str, Any] = {
            "survivor_linkedin_before": survivor.linkedin_url,
            "absorbed_linkedin_before": absorbed.linkedin_url,
            "survivor_telefono_before": survivor.telefono,
            "aliases": [],
        }
        absorbed_profile = absorbed.linkedin_url
        absorbed.linkedin_url = None
        self.session.flush()
        if survivor.linkedin_url is None:
            survivor.linkedin_url = absorbed_profile
        if survivor.telefono is None:
            survivor.telefono = absorbed.telefono
        aliases = list(
            self.session.scalars(select(User.id).where(User.merged_into_id == absorbed.id))
        )
        for alias in aliases:
            self.session.execute(
                update(User).where(User.id == alias).values(merged_into_id=survivor.id)
            )
        undo["aliases"] = [str(a) for a in aliases]
        absorbed.merged_into_id = survivor.id
        undo["survivor_linkedin_after"] = survivor.linkedin_url
        undo["survivor_telefono_after"] = survivor.telefono
        return undo

    # ---- helpers ----------------------------------------------------------------------

    def _lock(self, *rows: User | Freelancer) -> None:
        """Both people and both cards, locked in id order for the whole transaction, so two
        merges (or a merge and its undo) of the same people queue instead of interleaving
        and a login written meanwhile waits for the outcome. The rows are re-read: what the
        session held before the lock may be what the other transaction just changed."""
        for model in (User, Freelancer):
            ids = sorted(row.id for row in rows if isinstance(row, model))
            self.session.execute(
                select(model)
                .where(model.id.in_(ids))
                .order_by(model.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()

    @staticmethod
    def _still_live(survivor: Freelancer, absorbed: Freelancer, s_user: User, a_user: User) -> None:
        """After the lock the rows are what the other transaction left: if it merged either
        card away first, this one must not merge on top of that."""
        if (
            survivor.deleted_at is not None
            or absorbed.deleted_at is not None
            or s_user.merged_into_id is not None
            or a_user.merged_into_id is not None
        ):
            raise MergeRefused(
                "Una delle due schede è appena stata unita o cancellata: ricarica la pagina."
            )

    def _retarget_proposals(self, source: UUID, target: UUID) -> list[str]:
        """A saved team proposal names its talents by card id inside its JSON: the ones that
        name the absorbed card name the survivor now, or «Assumi team» would hire nobody.
        Answers the proposals touched, which is what the undo turns back."""
        rows = self.session.scalars(
            select(TeamProposal).where(cast(TeamProposal.team, Text).contains(str(source)))
        ).all()
        touched: list[str] = []
        for row in rows:
            row.team = [
                {**member, "freelancer_id": str(target)}
                if member.get("freelancer_id") == str(source)
                else member
                for member in row.team
            ]
            touched.append(str(row.id))
        return touched

    def _carry_optout(self, absorbed: User, survivor: User) -> bool:
        """An address that asked for no campaign mail asked it for the person: the
        survivor's address gets the same refusal, and the undo takes it away again."""
        theirs = self.session.get(CampaignOptout, absorbed.email.lower())
        if theirs is None or self.session.get(CampaignOptout, survivor.email.lower()) is not None:
            return False
        self.session.add(
            CampaignOptout(
                email=survivor.email.lower(), fonte=theirs.fonte, campaign_id=theirs.campaign_id
            )
        )
        return True

    def _ids(self, relation: Relation, source: UUID) -> list[UUID]:
        model = _model(relation)
        return list(
            self.session.scalars(select(model.id).where(relation.column == source, *relation.where))
        )

    def _live(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        return row

    def _user(self, row: Freelancer) -> User:
        user = self.session.get(User, row.user_id)
        assert user is not None
        return user


def _stamp(row: Freelancer) -> datetime:
    return row.cv_uploaded_at or row.created_at


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "quantize"):
        return str(value)
    return value


def _restore(model: type[Any], field: str, value: Any) -> Any:
    from rebase_core.audit import coerce_stored_value

    if field in ("vetted_at",) or field.endswith("_at"):
        return datetime.fromisoformat(value) if value is not None else None
    if field == "vetted_by":
        return UUID(value) if value is not None else None
    return coerce_stored_value(model, field, value)


def _json(payload: dict[str, Any]) -> dict[str, Any]:
    from rebase_core.audit import sanitize_payload

    return sanitize_payload(payload)
