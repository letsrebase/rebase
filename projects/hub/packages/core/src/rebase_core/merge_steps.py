"""The parts of a merge that are not a relation move (REB-800): the profile fields the
survivor fills, the newest-CV swap with its anonymous card, the `users` row, the saved
team proposals and the campaign opt-out. Each answers what the `merged` entry records so
`undo` can put it back; `plain`, `restore` and `audit_json` are how those values are
written to and read from that entry.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.orm import Session

from rebase_core.audit import coerce_stored_value, sanitize_payload
from rebase_core.models import (
    ATTRIBUTION_COLUMNS,
    CampaignOptout,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    User,
)

# What the `cv` of a merge answers: the survivor ends with no CV, its own, the absorbed
# card's, or (an undo) the older one put back.
CV_NONE, CV_KEPT, CV_ABSORBED, CV_RESTORED = "nessuno", "invariato", "assorbita", "ripristinato"
CV_OUTCOMES = (CV_NONE, CV_KEPT, CV_ABSORBED, CV_RESTORED)
# Fields of the card the survivor takes from the absorbed one when it has none.
_FILLED_FIELDS = (
    "posizione",
    "daily_rate",
    "remote",
    "seniority_admin",
    "experience_years_admin",
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
    "sources",
    "source_hashes",
    "conflicts",
    "thinking_tokens",
    "person",
    "created_at",
    "updated_at",
)
_CV_COLUMNS = ("cv_bytes", "cv_filename", "cv_mime", "cv_size", "cv_uploaded_at")


def _stamp(row: Freelancer) -> datetime:
    return row.cv_uploaded_at or row.created_at


def plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "quantize"):
        return str(value)
    return value


def restore(model: type[Any], field: str, value: Any) -> Any:
    if field in ("vetted_at",) or field.endswith("_at"):
        return datetime.fromisoformat(value) if value is not None else None
    if field == "vetted_by":
        return UUID(value) if value is not None else None
    return coerce_stored_value(model, field, value)


def audit_json(payload: dict[str, Any]) -> dict[str, Any]:
    return sanitize_payload(payload)


class MergeSteps:
    def __init__(self, session: Session) -> None:
        self.session = session

    def fill_profile(self, survivor: Freelancer, absorbed: Freelancer) -> dict[str, Any]:
        """Fills what the survivor leaves empty; answers the survivor's values before."""
        before: dict[str, Any] = {}
        for field in _FILLED_FIELDS:
            if getattr(survivor, field) is None and getattr(absorbed, field) is not None:
                before[field] = None
                setattr(survivor, field, getattr(absorbed, field))
        if not survivor.links and absorbed.links:
            before["links"] = list(survivor.links)
            survivor.links = list(absorbed.links)
        if survivor.status == "new" and absorbed.status in ("contacted", "active"):
            before["status"] = survivor.status
            survivor.status = absorbed.status
        older = absorbed if absorbed.created_at < survivor.created_at else survivor
        has_own = any(getattr(survivor, c) for c in ATTRIBUTION_COLUMNS)
        if (older is absorbed or not has_own) and any(
            getattr(absorbed, c) for c in ATTRIBUTION_COLUMNS
        ):
            for column in ATTRIBUTION_COLUMNS:
                before[column] = getattr(survivor, column)
                setattr(survivor, column, getattr(absorbed, column))
        return {key: plain(value) for key, value in before.items()}

    def keep_newest_cv(self, survivor: Freelancer, absorbed: Freelancer) -> tuple[bool, str]:
        """The survivor ends with the newest CV and its card; the older stays on the
        absorbed card. Answers whether the two swapped, and which CV the survivor holds."""
        if absorbed.cv_bytes is None:
            return False, CV_KEPT if survivor.cv_bytes is not None else CV_NONE
        newer = survivor.cv_bytes is None or _stamp(absorbed) > _stamp(survivor)
        if not newer:
            return False, CV_KEPT
        self.swap_cv_and_card(survivor, absorbed)
        return True, CV_ABSORBED

    def swap_cv_and_card(self, first: Freelancer, second: Freelancer) -> None:
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

    def clear_failed_card(self, survivor: Freelancer) -> dict[str, Any] | None:
        """A failure recorded for the CV the survivor now holds would keep `cards-refresh`
        from trying it again: cleared, with its old value kept for the undo."""
        card = self.session.get(FreelancerCard, survivor.id)
        if card is None or card.error is None:
            return None
        before = {"error": card.error, "error_cv_sha256": card.error_cv_sha256}
        card.error, card.error_cv_sha256 = None, None
        return before

    def restore_card_error(self, survivor: Freelancer, before: dict[str, Any] | None) -> None:
        if before is None:
            return
        card = self.session.get(FreelancerCard, survivor.id)
        if card is not None:
            card.error, card.error_cv_sha256 = before["error"], before["error_cv_sha256"]

    def needs_card(self, survivor: Freelancer) -> bool:
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

    def merge_identity(self, survivor: User, absorbed: User) -> dict[str, Any]:
        """The `users` rows: the absorbed address points at the survivor and gives up its
        LinkedIn profile (one holder per profile), a missing profile or phone number on the
        survivor is filled from it. Answers what the undo needs."""
        undo: dict[str, Any] = {
            "survivor_linkedin_before": survivor.linkedin_url,
            "absorbed_linkedin_before": absorbed.linkedin_url,
            "survivor_telefono_before": survivor.phone,
            "aliases": [],
        }
        absorbed_profile = absorbed.linkedin_url
        absorbed.linkedin_url = None
        self.session.flush()
        if survivor.linkedin_url is None:
            survivor.linkedin_url = absorbed_profile
        if survivor.phone is None:
            survivor.phone = absorbed.phone
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
        undo["survivor_telefono_after"] = survivor.phone
        return undo

    def retarget_proposals(self, source: UUID, target: UUID) -> list[str]:
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

    def carry_optout(self, absorbed: User, survivor: User) -> bool:
        """An address that asked for no campaign mail asked it for the person: the
        survivor's address gets the same refusal, and the undo takes it away again."""
        theirs = self.session.get(CampaignOptout, absorbed.email.lower())
        if theirs is None or self.session.get(CampaignOptout, survivor.email.lower()) is not None:
            return False
        self.session.add(
            CampaignOptout(
                email=survivor.email.lower(), source=theirs.source, campaign_id=theirs.campaign_id
            )
        )
        return True
