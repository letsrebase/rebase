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
PROPOSAL_TARGET_TYPES: tuple[str, ...] = ("contract", "work_unit")
# mastro's `excerptKind` (`proposal.ts:21-43`): `quoted` is a span the application
# located in the document's own text; `transcribed` exists only for a scanned page with
# no text layer, and is never rendered as a quotation for the same reason mastro's own
# comment gives -- nothing here can verify a string against a page image.
PROPOSAL_TIPI_EXCERPT: tuple[str, ...] = ("quoted", "transcribed")
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

    # The archived original the extraction read -- never optional, mastro invariant 4.
    # No explicit `ondelete`: RESTRICT is the schema-wide default, and a document a
    # proposal still points at cannot be deleted out from under it, the same reasoning
    # `Approval.document_id` (REB-359) already carries.
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id"), nullable=False, index=True
    )
    # Required by the CHECK below unless target_type = 'contratto' (first intake has
    # no contract row to point at yet, mirroring `proposal.ts:167-172`'s own CHECK).
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
        String(12), nullable=False, default="quoted", server_default=text("'citato'")
    )
    # The producer's own declared confidence, 0 to 1, never computed after the fact
    # (`proposal.ts:90-91`).
    confidence: Mapped[Any] = mapped_column(
        Numeric(CONFIDENCE_MAX_DIGITS, CONFIDENCE_DECIMAL_PLACES), nullable=False
    )
    confidence_reason: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="pending", server_default=text("'in_attesa'")
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
            f"tipo_estratto IN {PROPOSAL_TIPI_EXCERPT!r}", name="ck_proposals_excerpt_type"
        ),
        CheckConstraint(f"stato IN {PROPOSAL_STATUSES!r}", name="ck_proposals_status"),
        CheckConstraint(
            "target_type = 'contratto' OR contract_id IS NOT NULL",
            name="ck_proposals_contract_id_required_unless_contract",
        ),
        # The review queue's own access path: "every pending proposal, oldest first" --
        # see `ProposalRepository.list`.
        Index("ix_proposals_status_created_at", "status", "created_at"),
    )
