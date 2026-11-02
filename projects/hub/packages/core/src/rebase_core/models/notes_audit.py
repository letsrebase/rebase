from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.identity import NAME_MAX_LENGTH

# ---- notes: what is said about a row, over time ---------------------------------------

NOTE_ENTITY_TYPES = ("freelancer", "company")
NOTE_MAX_LENGTH = 4000
AUTORE_MAX_LENGTH = NAME_MAX_LENGTH
# Where a note came from (REB-856). `web` and `mcp` are an admin writing, through the admin
# area or an agent's token; `persona` is the person's own change of their profile, `sistema`
# what the hub itself recorded (a merge). The last two are events of the row's life, not
# remarks: they are never edited or deleted. A row written before the source was tracked has
# `fonte` NULL and is an admin's, so it may be edited.
NOTE_SOURCES = ("web", "mcp", "persona", "sistema")
NOTE_ADMIN_SOURCES = ("web", "mcp")


class Note(Base, PrimaryKeyMixin):
    """One note about a freelancer or a company: the one place an admin, an agent or the
    person's own change leaves a remark (REB-856; it replaced the append-only `comments`
    of ORB-59 and the `note` column of both rows, at Lorenzo's request of 2026-10-29).

    The text carries no date or author: `created_at`, `autore` (the display name as it
    read when it was written), `autore_id` and `fonte` say who and when and by which door,
    and `modificata_*` who changed it last. An admin may edit or delete a note of their own
    kind (`NOTE_ADMIN_SOURCES`, or an old row with no source); the delete is soft
    (`eliminata_*`), so the trail (`AdminAction` `note_*`) can still say what was there.

    `entity_type` plus `entity_id` rather than two nullable foreign keys: the service
    checks the row exists before writing, and one table with one index is what a thread on
    a third kind of row would reuse without a migration on this table."""

    __tablename__ = "notes"

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    autore: Mapped[str] = mapped_column(String(AUTORE_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    autore_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    fonte: Mapped[str | None] = mapped_column(String(10), default=None)
    modificata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    modificata_da_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    eliminata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    eliminata_da_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (
        Index("ix_notes_entity", "entity_type", "entity_id", "created_at"),
        CheckConstraint(
            "fonte IS NULL OR fonte IN ('web', 'mcp', 'persona', 'sistema')", name="ck_notes_fonte"
        ),
    )


# ---- what an admin overrode, cleared, deleted or restored, and when --------------------

ADMIN_ACTION_ENTITY_TYPES = (
    "freelancer",
    "company",
    "match",
    "freelancer_fiscal",
    "ai_config",
    "card_eval",
)
# `overridden` covers both "set" and "clear" -- clearing is setting a field to its empty
# value, and `rebase_core.audit.field_changes` records the same `changed`/`before`/`after`
# shape either way. `cleared` is only for the one thing that shape must never carry: the
# freelancer's CV, whose bytes are personal data that must not be duplicated into an audit
# row (`FreelancerService.clear_cv`). `deleted`/`restored` are the whole story on their own,
# with an empty payload.
# REB-387 adds the matches' own kinds, on entity type `match` (phase 3 writes the last
# four), and `fiscal_updated` on `freelancer_fiscal`, whose payload names the fields that
# changed and never their values: a tax identifier is not copied into this table.
# REB-509 adds `vetted`, on entity type `freelancer`: «Vetted» flips `Freelancer.vetted_at`
# on or off, not a field an "overridden"/"cleared" pair already describes.
# REB-830 adds the model providers' trail, on entity type `ai_config` (one fixed id:
# the entity is the configuration, not a row): `ai_key` (its payload says «chiave
# sostituita» and the last four characters, never the key), `ai_task`, `ai_price` and
# `ai_switch` (a provider turned on or off), the last three with the before and after.
# REB-868 adds `crm_link` on `company`: an admin's «Riprova» or «Collega a un altro deal»,
# its payload the outcome (`esito`), the CRM's sentence (`errore`) and, for the second,
# `deal_id`. REB-498 adds `pigro_link` on `match`: an admin's «Riprova su Pigro», its payload the
# outcome (`esito`) and the CRM's sentence (`errore`). REB-517 adds `talents_contacted`,
# on entity type `team_request`: «Contatta i talenti» and «Rimanda», with how many talents
# were mailed. No `CHECK` holds this list: it is the record of what the code writes.
# REB-800 adds `merged` (on the card that was absorbed: whose it went into, and every row
# that moved, by id, so the merge can be undone), `absorbed` (on the survivor: what it
# took in) and `unmerged` (the undo, on both). REB-835 adds the evaluation set's trail,
# on entity type `card_eval` (the freelancer's id): `eval_add`, `eval_remove`,
# `eval_expected` (its payload names the fields that changed, never their values: an
# expected card is personal data), `eval_accept` and `eval_rate`. REB-839 adds
# `cards_regenerate` on `ai_config`: an admin's start of the regeneration of the cards
# that are stale by prompt version or by their inputs (its payload: the limit, how many
# were queued, `fonte`, `batch` and its `batch_id`). REB-861 signs and sources every start
# (the command line too: `--admin`), records the end of each bulk regeneration as
# `cards_regenerated` on `ai_config` (counts, cost, failures by reason, no personal data)
# and each card a regeneration rewrites, or fails to, as `card_regenerated` on the
# freelancer. The hourly `cards-refresh` and the wizard's write are started by no person
# and record nothing.
ADMIN_ACTION_KINDS = (
    "overridden",
    "cleared",
    "deleted",
    "restored",
    "match_created",
    "match_cancelled",
    "match_closed",
    "fiscal_updated",
    "documents_sent",
    "document_cancelled",
    "mail_resent",
    "notice_recorded",
    "vetted",
    "pigro_link",
    "talents_contacted",
    "ai_key",
    "ai_task",
    "ai_price",
    "ai_switch",
    "merged",
    "absorbed",
    "unmerged",
    "eval_add",
    "eval_remove",
    "eval_expected",
    "eval_accept",
    "eval_rate",
    "cards_regenerate",
    "cards_regenerated",
    "card_regenerated",
    "note_added",
    "note_edited",
    "note_deleted",
    "status_changed",
    "anon_cv_downloaded",
    "crm_link",
    "company_created",
)


class AdminAction(Base, PrimaryKeyMixin):
    """One row per admin-driven change to a `Freelancer`/`Company` record
    (REB-347; since REB-856 also its status and its notes): who, when, and for
    an `overridden`/`cleared` entry, the field's value before and after
    (`rebase_core.audit.field_changes`). Append-only -- an admin reverses
    an action by writing a new one from this row's own `payload`, never by editing it.

    `entity_type` plus `entity_id` rather than a foreign key per entity, the same
    reasoning `Note` gives: the service checks the row exists before writing, and one
    table with one index is what a third entity's own trail would reuse without a
    migration here."""

    __tablename__ = "admin_actions"

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_admin_actions_entity", "entity_type", "entity_id", "created_at"),)


# ---- an admin's own tokens, for agents --------------------------------------------------

ADMIN_SESSION_TOKEN_HASH_LENGTH = 64  # sha256, hex
ADMIN_TOKEN_PREFIX_LENGTH = 20


class AdminToken(Base, PrimaryKeyMixin, TimestampMixin):
    """A personal token of an admin, for an agent (REB-213): the credential the MCP
    server takes as a bearer. Only the sha256 of the value is stored; the value itself
    is shown once, at creation, and never again. `prefix` is the visible head of it, so a
    list can tell two tokens apart. `revoked_at` is the end of a token, `expires_at` an optional
    one set when it is minted, and a revoked or expired one is refused like an unknown one.

    Since REB-278 `user_id` (`users.id`, `role == 'admin'`) is the owner; migration B
    (REB-281) drops the `admin_id` column it replaced, once `admin_users` itself is
    gone and nothing points at it any more."""

    __tablename__ = "admin_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(ADMIN_SESSION_TOKEN_HASH_LENGTH), nullable=False, unique=True
    )
    prefix: Mapped[str] = mapped_column(String(ADMIN_TOKEN_PREFIX_LENGTH), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The token's end, when it was minted with one (REB-803): `NULL` never expires. A token
    # past it is refused like a revoked one.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        # Trigram search (REB-313): Agenti's list matches a token by its own `nome`,
        # the name the admin gave it -- there is no linked `users` row to search
        # instead, unlike Talenti and Aziende.
        Index(
            "ix_admin_tokens_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
    )
