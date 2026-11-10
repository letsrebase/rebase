"""`proposals` -- the review-before-write table spec §10 gives both of the project's
"agents propose, humans confirm" questions: a contract's first draft of fields, and a
day's proposal from an approval email. One table, trimmed from mastro's own four
target types (`proposal.ts:64`) down to the two REB-344 asks for: `'contract'` and
`'work_unit'`. `'invoice'` is REB-351's own concern; `'rinnovo_contratto'` is the
renewal-automation feature `contracts.previous_contract_id`'s own note (REB-358
`models.py`) already scopes out.

No trigger here, unlike `work_units`/`approvals` (§12): every invariant this table
needs -- the two closed sets, and `contract_id`'s conditional requirement -- is a
plain `CHECK`, exactly like every invariant this schema enforced before REB-359
introduced procedural SQL for the one case a `CHECK` genuinely cannot express (a
cross-table subquery). Nothing here needs one.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# mastro's `proposalTargetType` (`proposal.ts:64`), trimmed to the two this project
# needs -- see the module docstring for the two it leaves out and why.
PROPOSAL_TARGET_TYPES: tuple[str, ...] = (
    "contract",
    "work_unit",
    "supplier_invoice",
    "receipt",
    "bank_statement",
)
# What a proposal of each target type reads from (design 2026-11-09, incoming documents inbox,
# § 3.5): a contract or a day from an archived document, which `accept` re-points at the row it
# creates; a document read out of the inbox from the inbox item, which has no customer, deal
# or contract to own a `Document` and so is no `Document` until the module that files it exists.
DOCUMENT_TARGET_TYPES: tuple[str, ...] = ("contract", "work_unit")
INBOX_TARGET_TYPES: tuple[str, ...] = ("supplier_invoice", "receipt", "bank_statement")
# mastro's `excerptKind` (`proposal.ts:21-43`): `quoted` is a span the application
# located in the document's own text; `transcribed` exists only for a scanned page with
# no text layer, and is never rendered as a quotation for the same reason mastro's own
# comment gives -- nothing here can verify a string against a page image.
PROPOSAL_EXCERPT_TYPES: tuple[str, ...] = ("quoted", "transcribed")
# mastro's `proposalStatus` (`proposal.ts:18`).
PROPOSAL_STATUSES: tuple[str, ...] = ("pending", "accepted", "rejected")

CONFIDENCE_MAX_DIGITS = 3
CONFIDENCE_DECIMAL_PLACES = 2


class Proposal(Base, PrimaryKeyMixin, TimestampMixin):
    """A candidate set of fields an agent read out of a document, sitting between the
    document and the row it would become until a human decides.

    No `SoftDeleteMixin`: a proposal is never removed, only decided -- `status` is the
    permanent record of what happened to it, exactly as an `Approval` (REB-359) is
    never removed either.
    """

    __tablename__ = "proposals"

    # The archived original the extraction read (mastro invariant 4): required for a contract or
    # a day, and absent for a proposal read out of the inbox, which points at its item instead
    # (`ck_proposals_source`). No explicit `ondelete`: RESTRICT is the schema-wide default, and
    # a document a proposal still points at cannot be deleted out from under it, the same
    # reasoning `Approval.document_id` (REB-359) already carries.
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id"), default=None, index=True
    )
    # The inbox item a supplier invoice, a receipt or a statement was read from (REB-972).
    inbox_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None, index=True
    )
    # Whose alone the proposal is, when it is one person's (a task read from a personal
    # mailbox, design § 3.5 and § 4): not even an administrator sees it. `NULL` for the rest,
    # whose visibility is their source's (`proposals/access.py`).
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )
    # Required by the CHECK below for a day (a contract's first intake has no contract row to
    # point at yet, mirroring `proposal.ts:167-172`'s own CHECK).
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts.id"), default=None, index=True
    )
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Shape depends on target_type -- see `proposals/schemas.py`'s
    # `ContractProposalFields`/`WorkUnitProposalFields` for the two shapes this
    # column actually carries, validated at the Pydantic layer before either ever
    # reaches this JSONB column.
    proposed_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The verbatim span the proposed fields rest on -- shown next to them at review,
    # exactly as `approval.excerpt` (REB-359) sits next to the days it covers.
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_type: Mapped[str] = mapped_column(
        String(12), nullable=False, default="quoted", server_default=text("'quoted'")
    )
    # The producer's own declared confidence, 0 to 1, never computed after the fact
    # (`proposal.ts:90-91`).
    confidence: Mapped[Any] = mapped_column(
        Numeric(CONFIDENCE_MAX_DIGITS, CONFIDENCE_DECIMAL_PLACES), nullable=False
    )
    confidence_reason: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="pending", server_default=text("'pending'")
    )
    # Set once, alongside stato, never edited afterwards -- kept separately from
    # campi_proposti rather than as a computed diff: "the diff between the two is the
    # whole point... it stays correct forever only if neither side is ever
    # overwritten" (`proposal.ts:114-122`).
    accepted_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # The row the accepted proposal produced (a contracts.id or a work_units.id) --
    # not a foreign key, the same polymorphic-reference reasoning `documents`' own
    # ownership columns already carry elsewhere in this schema, and only ever set by
    # the accept path itself in the same transaction as the row it points at.
    result_id: Mapped[UUID | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(Text, default=None)
    decided_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            f"target_type IN {PROPOSAL_TARGET_TYPES!r}", name="ck_proposals_target_type"
        ),
        CheckConstraint(
            f"excerpt_type IN {PROPOSAL_EXCERPT_TYPES!r}", name="ck_proposals_excerpt_type"
        ),
        CheckConstraint(f"status IN {PROPOSAL_STATUSES!r}", name="ck_proposals_status"),
        CheckConstraint(
            "target_type <> 'work_unit' OR contract_id IS NOT NULL",
            name="ck_proposals_contract_id_required_for_work_unit",
        ),
        # Exactly one source, the one the target type reads from.
        CheckConstraint(
            f"(target_type IN {DOCUMENT_TARGET_TYPES!r} AND document_id IS NOT NULL "
            "AND inbox_item_id IS NULL) "
            f"OR (target_type IN {INBOX_TARGET_TYPES!r} AND inbox_item_id IS NOT NULL "
            "AND document_id IS NULL)",
            name="ck_proposals_source",
        ),
        # The review queue's own access path: "every pending proposal, oldest first" --
        # see `ProposalRepository.list`.
        Index("ix_proposals_status_created_at", "status", "created_at"),
        # One proposal waits for a person per inbox item: a second would ask the same question
        # twice, and the item's «proposed» state would point at two answers.
        Index(
            "uq_proposals_pending_inbox_item",
            "inbox_item_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND inbox_item_id IS NOT NULL"),
        ),
    )
