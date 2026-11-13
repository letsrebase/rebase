"""Merging one person into another, and undoing it (REB-800).

`PersonMergeService.merge(into, absorbed, admin)` is for two cards of one person: the
survivor keeps its card and takes in everything the absorbed one had. **Nothing is
deleted.** The absorbed card is soft-deleted (`deleted_at`), its `users` row keeps its
address and points at the survivor (`merged_into_id`), and the one `merged` entry in the
trail names every row that moved, by id, which is also what `undo` reads to move them
back. The survivor's own trail gets an `absorbed` entry with the counts.

**What moves** is `merge_relations.RELATIONS`, each by id: the logins, sessions and magic links,
the company requests, the team asks and talent-cloud grants, the campaign rows, the
referrals the person made or came through, the hub's engagements, the
notes, the audit entries, the talent's asks on team requests. A
row that would collide with one the survivor already has (a referral for the same card,
a talent already on the same request, a live cloud grant for the same company) stays where
it was and is counted as `left_behind`.

**The CV is the newest of the two.** `freelancers.cv_uploaded_at` says which; the CV and
its anonymous card travel together, so if the absorbed card holds the newer one the two
swap and the older file and card stay, reachable, on the soft-deleted card. A survivor
left with a CV and no card, or only a failure for that CV, is flagged
(`card_to_regenerate`) and its failure is cleared, so the hourly `cards-refresh` writes
it: the merge never calls Claude.

**Profile fields** the survivor leaves empty are filled from the absorbed card; the first
attribution (the UTM set and the page) stays, the older card's; the
further `status` wins over `new`. Every changed value is kept in the entry, as it was.

**What stops it**, each with a sentence: the same card, an admin as the absorbed side, two
different tax codes (the CRM's), a live framework agreement of the same type
on both sides, an engagement in progress with the same client on both, or a CRM that cannot be
asked. The contracts and engagements are the CRM's since the relationship with a freelancer
moved there (collaborators design § 5, REB-1107): the merge asks it through the door
(`FreelancerDoor.merge_check`), before it takes any lock, and **refuses when it cannot be
asked**, since a merge it did not check could join two live agreements.
"""

import hashlib
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.contact_points import ContactPointService
from rebase_core.door_schemas import CrmMergeCheck
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.merge_relations import (
    USER,
    Context,
    count_movable,
    move_relations,
    put_back_relations,
)
from rebase_core.merge_steps import (
    CV_KEPT,
    CV_OUTCOMES,
    CV_RESTORED,
    MergeSteps,
    audit_json,
    plain,
    restore,
)
from rebase_core.models import (
    AUTHOR_MAX_LENGTH,
    AdminAction,
    CampaignOptout,
    Freelancer,
    Note,
    TeamProposal,
    User,
)
from rebase_core.pigro_door import FreelancerDoor, PigroUnavailable
from rebase_core.schema_common import ResponseModel
from rebase_core.sources import delete_sources
from rebase_core.users import UserService
from rebase_core.vocabulary import enum_of

ENTITY = "freelancer"

# The CRM's letter types as the sentence names them (the hub's own regime names).
_REGIME_NAMES = {"ordinary": "vat_registered", "occasional": "occasional"}


class MergeRead(ResponseModel):
    """What a merge did, for the admin who ran it. Counts, never the rows."""

    survivor_freelancer_id: UUID
    absorbed_freelancer_id: UUID
    action_id: UUID
    moved: dict[str, int]
    left_behind: dict[str, int]
    cv: Annotated[str, Field(json_schema_extra=enum_of(CV_OUTCOMES))]
    card_to_regenerate: bool


class MergeRequest(BaseModel):
    """The body of the admin merge: the card to absorb, by freelancer id."""

    model_config = ConfigDict(extra="forbid")

    da: UUID


class MergeRefused(InvalidState):
    """A merge that must not run, with the sentence an admin reads."""


def _name(user: User) -> str:
    return f"{user.first_name} {user.last_name}".strip()


class PersonMergeService:
    def __init__(self, session: Session, door: FreelancerDoor) -> None:
        self.session = session
        self.door = door
        self.steps = MergeSteps(session)

    # ---- merge ------------------------------------------------------------------------

    def merge(self, into_id: UUID, absorbed_id: UUID, admin_id: UUID) -> MergeRead:
        """Merges the card `absorbed_id` into `into_id`, in one transaction, as above."""
        if into_id == absorbed_id:
            raise ValidationFailed(ENTITY, "da", "servono due schede diverse")
        survivor = self._live(into_id)
        absorbed = self._live(absorbed_id)
        s_user, a_user = self._user(survivor), self._user(absorbed)
        # Before any lock: no row lock is held across a call to the CRM.
        crm = self._crm_check(survivor.id, absorbed.id)
        self._lock(s_user, a_user, survivor, absorbed)
        self._still_live(survivor, absorbed, s_user, a_user)
        self._check(survivor, absorbed, s_user, a_user, crm)
        admin = self.session.get(User, admin_id)
        if admin is None:
            raise NotFound(USER, admin_id)
        ctx = Context(s_user.id, a_user.id, survivor.id, absorbed.id)

        moved, left_behind = move_relations(self.session, ctx)
        undo: dict[str, Any] = {}
        undo["filled"] = self.steps.fill_profile(survivor, absorbed)
        undo["cv_swapped"], cv = self.steps.keep_newest_cv(survivor, absorbed)
        undo["cv_sha_after"] = (
            hashlib.sha256(survivor.cv_bytes).hexdigest() if survivor.cv_bytes else None
        )
        undo["card_error_cleared"] = self.steps.clear_failed_card(survivor)
        undo["filled_after"] = {field: plain(getattr(survivor, field)) for field in undo["filled"]}
        undo["user"] = self.steps.merge_identity(s_user, a_user)
        undo["optout_copied"] = self.steps.carry_optout(a_user, s_user)
        undo["team_refs"] = self.steps.retarget_proposals(absorbed.id, survivor.id)
        absorbed.deleted_at = utcnow()
        # What other pages said about a card that no longer exists is not kept (REB-837);
        # the survivor reads its own links, and an undo reads the absorbed card's again.
        delete_sources(self.session, absorbed.id)
        self.session.flush()
        flagged = self.steps.needs_card(survivor)

        author = _name(admin)[:AUTHOR_MAX_LENGTH] or "admin"
        self.session.add(
            Note(
                entity_type="freelancer",
                entity_id=survivor.id,
                text=(
                    f"Scheda unita con quella dell'indirizzo {a_user.email}: "
                    "una sola persona, il CV più recente resta qui."
                ),
                author=author,
                author_id=admin_id,
                source="system",
            )
        )
        payload = {
            "into_freelancer_id": survivor.id,
            "into_user_id": s_user.id,
            "moved": {key: [str(i) for i in ids] for key, ids in moved.items()},
            "left_behind": left_behind,
            "cv": cv,
            "card_to_regenerate": flagged,
            **undo,
        }
        merged = AdminAction(
            entity_type="freelancer",
            entity_id=absorbed.id,
            kind="merged",
            admin_id=admin_id,
            payload=audit_json(payload),
        )
        self.session.add(merged)
        self.session.add(
            AdminAction(
                entity_type="freelancer",
                entity_id=survivor.id,
                kind="absorbed",
                admin_id=admin_id,
                payload=audit_json(
                    {
                        "from_freelancer_id": absorbed.id,
                        "from_user_id": a_user.id,
                        "moved": {key: len(ids) for key, ids in moved.items()},
                        "left_behind": left_behind,
                        "cv": cv,
                        "card_to_regenerate": flagged,
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
            left_behind=left_behind,
            cv=cv,
            card_to_regenerate=flagged,
        )

    def plan(self, into_id: UUID, absorbed_id: UUID) -> dict[str, int]:
        """What `merge` would move, as counts, writing nothing: what the dry run prints.
        Raises the same refusals `merge` does."""
        survivor, absorbed = self._live(into_id), self._live(absorbed_id)
        s_user, a_user = self._user(survivor), self._user(absorbed)
        self._check(survivor, absorbed, s_user, a_user, self._crm_check(survivor.id, absorbed.id))
        ctx = Context(s_user.id, a_user.id, survivor.id, absorbed.id)
        return count_movable(self.session, ctx)

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
        left_behind: dict[str, int] = {}
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
                left_behind["surviving_linkedin"] = 1
        elif user_undo["survivor_linkedin_before"] != s_user.linkedin_url:
            left_behind["surviving_linkedin"] = 1
        # A profile both cards held before the merge cannot be held by both again once
        # the unique index exists: the survivor keeps it, and the card that comes back
        # has none until an admin gives it one.
        profile = user_undo["absorbed_linkedin_before"]
        if profile is not None and users.by_linkedin(profile) is not None:
            profile = None
            left_behind["linkedin"] = 1
        a_user.linkedin_url = profile
        a_user.merged_into_id = None
        # A merge made before REB-943 carries no points: its phone was a column, which the
        # migration turned into a point on both people, and nothing there is moved back.
        if "contact_points" in user_undo:
            changed = ContactPointService(self.session).put_back(
                s_user, a_user, user_undo["contact_points"]
            )
            if changed:
                left_behind["contact_points"] = changed
        for alias in user_undo["aliases"]:
            self.session.execute(
                update(User).where(User.id == UUID(alias)).values(merged_into_id=a_user.id)
            )
        # The failure was cleared on the card the survivor held *after* the swap, so it
        # goes back before the swap is undone.
        self.steps.restore_card_error(survivor, payload["card_error_cleared"])
        if payload["cv_swapped"]:
            self.steps.swap_cv_and_card(survivor, absorbed)
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
            if plain(getattr(survivor, field)) == payload["filled_after"][field]:
                setattr(survivor, field, restore(Freelancer, field, before))
            else:
                changed_since += 1
        if changed_since:
            left_behind["fields"] = changed_since
        if payload["optout_copied"]:
            self.session.execute(
                delete(CampaignOptout).where(
                    CampaignOptout.email == s_user.email.lower(),
                    CampaignOptout.created_at <= action.created_at,
                )
            )

        put_back = put_back_relations(self.session, ctx, payload["moved"])
        absorbed.deleted_at = None
        self.session.flush()
        for entity_id in (absorbed.id, survivor.id):
            self.session.add(
                AdminAction(
                    entity_type="freelancer",
                    entity_id=entity_id,
                    kind="unmerged",
                    admin_id=admin_id,
                    payload=audit_json({"action_id": action.id, "put_back": put_back}),
                )
            )
        self.session.commit()
        return MergeRead(
            survivor_freelancer_id=survivor.id,
            absorbed_freelancer_id=absorbed.id,
            action_id=action.id,
            moved=put_back,
            left_behind=left_behind,
            cv=CV_RESTORED if payload["cv_swapped"] else CV_KEPT,
            card_to_regenerate=False,
        )

    # ---- the checks -------------------------------------------------------------------

    def _crm_check(self, survivor_id: UUID, absorbed_id: UUID) -> CrmMergeCheck:
        """What the CRM holds that stops these two cards becoming one. A CRM that cannot be
        asked refuses the merge, in the sentence the admin reads, and writes nothing."""
        try:
            return self.door.merge_check(survivor_id, absorbed_id)
        except PigroUnavailable as exc:
            raise MergeRefused(
                f"{exc} I contratti e gli incarichi sono nel CRM e senza il suo controllo non "
                "unisco le schede: riprova tra poco."
            ) from exc

    def _check(
        self,
        survivor: Freelancer,
        absorbed: Freelancer,
        s_user: User,
        a_user: User,
        crm: CrmMergeCheck,
    ) -> None:
        if a_user.role == "admin":
            raise MergeRefused(
                "La scheda da assorbire è di un amministratore: unisci l'altra nella sua.",
            )
        if crm.tax_code_conflict:
            raise MergeRefused(
                "Le due schede hanno codici fiscali diversi: non sono la stessa persona.",
            )
        if crm.other_organization:
            # A hub merge does not re-key the CRM's organization: it would stay on the hidden
            # card, and the survivor would lose its contracts and fiscal identifiers.
            raise MergeRefused(
                "Entrambe le schede hanno un'anagrafica nel CRM: unisci prima il freelance "
                "nel CRM, poi le due schede."
                if crm.organization
                else "Nel CRM c'è l'anagrafica della scheda da assorbire e non quella della "
                "scheda che resta: unisci nell'altro verso (tieni la scheda che ha "
                "l'anagrafica) oppure sposta l'id dell'hub sull'anagrafica nel CRM."
            )
        if crm.framework_regimes:
            regimes = {_REGIME_NAMES.get(regime, regime) for regime in crm.framework_regimes}
            raise MergeRefused(
                "Entrambe le schede hanno un contratto quadro in corso o firmato "
                f"({', '.join(sorted(regimes))}): annulla o disdici uno dei due prima di unirle.",
            )
        if crm.shared_clients:
            raise MergeRefused(
                "Entrambe le schede hanno un incarico in corso con la stessa azienda: "
                "chiudi o annulla uno dei due prima di unirle.",
            )

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

    def _live(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        return row

    def _user(self, row: Freelancer) -> User:
        user = self.session.get(User, row.user_id)
        if user is None:
            raise NotFound(USER, row.user_id)
        return user
