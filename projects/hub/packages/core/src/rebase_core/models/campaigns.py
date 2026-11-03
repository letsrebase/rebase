from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.identity import NAME_MAX_LENGTH

# ---- campaigns: a list, a mail, and what it led to (P-REB-41) ---------------------------

CAMPAIGN_FONTI = ("stato", "filtri", "lista")
CAMPAIGN_STATES = ("bozza", "programmata", "in_invio", "inviata", "annullata")
CAMPAIGN_ACTIONS = (
    "entrato",
    "cv",
    "scheda_completa",
    "profilo_creato",
    "richiesta_aggiornata",
    "pigro_cliente",
    "clic",
)
CAMPAIGN_DESTINATIONS = ("area", "wizard", "pigro", "richiesta", "link")
RECIPIENT_STATES = ("in_coda", "inviata", "saltata", "fallita")
RECIPIENT_KINDS = ("freelancer", "lead", "azienda", "proprietario")
OPTOUT_SOURCES = ("link", "reclamo", "admin")
CAMPAIGN_NAME_MAX_LENGTH = 120
CAMPAIGN_SLUG_MAX_LENGTH = 80
CAMPAIGN_SUBJECT_MAX_LENGTH = 200
CAMPAIGN_BUTTON_MAX_LENGTH = 60
CAMPAIGN_BUTTON_URL_MAX_LENGTH = 500
CAMPAIGN_TEXT_MAX_LENGTH = 5000
RECIPIENT_REASON_MAX_LENGTH = 200
CAMPAIGN_STALL_MAX_LENGTH = 200


class Campaign(Base, PrimaryKeyMixin, TimestampMixin):
    """One list and one mail (spec § 1). `fonte` says where the list comes from: a
    journey state (`stato_percorso`), the Talenti or company filters (`filtri`), or the
    recipients of an earlier campaign who did nothing (`segue_id`, phase 2), and the
    constraints keep each source with its own field. `contenuto_at` moves only when the
    admin edits the list or the mail, never on the writes that record a test or a
    send, because «Invia» compares it with `prova_inviata_at`: `updated_at` would move
    on the test's own write and lock the button for good."""

    __tablename__ = "campaigns"

    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    nome: Mapped[str] = mapped_column(String(CAMPAIGN_NAME_MAX_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(CAMPAIGN_SLUG_MAX_LENGTH), nullable=False)
    fonte: Mapped[str] = mapped_column(String(10), nullable=False)
    stato_percorso: Mapped[str | None] = mapped_column(String(30), default=None)
    # `none_as_null=True`: an explicit `None` on this attribute must bind as a true SQL
    # NULL, not a JSON `null` (SQLAlchemy's JSONB default), so a service can clear
    # `filtri` on an edit without tripping `ck_campaigns_filtri`. Bind-side only, no
    # migration needed.
    filtri: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    segue_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), default=None)
    oggetto: Mapped[str] = mapped_column(String(CAMPAIGN_SUBJECT_MAX_LENGTH), nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    bottone_testo: Mapped[str] = mapped_column(String(CAMPAIGN_BUTTON_MAX_LENGTH), nullable=False)
    bottone_meta: Mapped[str] = mapped_column(String(10), nullable=False)
    # «Un link» (REB-530): where the button leads when it leaves the hub, and only then.
    bottone_url: Mapped[str | None] = mapped_column(
        String(CAMPAIGN_BUTTON_URL_MAX_LENGTH), default=None
    )
    azione: Mapped[str] = mapped_column(String(25), nullable=False)
    stato: Mapped[str] = mapped_column(String(12), nullable=False, default="bozza")
    contenuto_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    programmata_per: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    prova_inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Phase 3 (spec § 6.3): the last good and the last failed read of the CRM's usage.
    pigro_letto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_errore_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # REB-524: the last time a pass stopped on something that will not fix itself (a
    # key Resend refuses, a list it can no longer read), and why, in the admin's words.
    # Both `None` again once a mail leaves or the send ends (`tick.py`).
    fermo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    fermo_motivo: Mapped[str | None] = mapped_column(
        String(CAMPAIGN_STALL_MAX_LENGTH), default=None
    )

    __table_args__ = (
        Index("uq_campaigns_slug", "slug", unique=True),
        Index("ix_campaigns_stato_programmata_per", "stato", "programmata_per"),
        CheckConstraint("fonte IN ('stato', 'filtri', 'lista')", name="ck_campaigns_fonte"),
        CheckConstraint(
            "stato IN ('bozza', 'programmata', 'in_invio', 'inviata', 'annullata')",
            name="ck_campaigns_stato",
        ),
        CheckConstraint(
            "azione IN ('entrato', 'cv', 'scheda_completa', 'profilo_creato', "
            "'richiesta_aggiornata', 'pigro_cliente', 'clic')",
            name="ck_campaigns_azione",
        ),
        CheckConstraint(
            "bottone_meta IN ('area', 'wizard', 'pigro', 'richiesta', 'link')",
            name="ck_campaigns_bottone_meta",
        ),
        CheckConstraint(
            "(fonte = 'stato') = (stato_percorso IS NOT NULL)", name="ck_campaigns_stato_percorso"
        ),
        CheckConstraint("(fonte = 'filtri') = (filtri IS NOT NULL)", name="ck_campaigns_filtri"),
        CheckConstraint("(fonte = 'lista') = (segue_id IS NOT NULL)", name="ck_campaigns_segue"),
        CheckConstraint(
            "(bottone_meta = 'link') = (bottone_url IS NOT NULL)", name="ck_campaigns_bottone_url"
        ),
        CheckConstraint(
            "(bottone_meta = 'link') = (azione = 'clic')", name="ck_campaigns_link_clic"
        ),
    )


class CampaignRecipient(Base, PrimaryKeyMixin):
    """One person of a frozen list (spec § 3). `email` is lowercase and unique within a
    campaign. `prima` is the state the action is measured against, taken when the list
    was frozen. `disiscrizione_token` is kept as it was minted, not hashed: Resend's
    `Idempotency-Key` wants the byte-identical request on a retry (409
    `invalid_idempotent_request` otherwise), so a retried mail must render the same
    link, and what a leaked token can do is only unsubscribe that one address."""

    __tablename__ = "campaign_recipients"

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nome: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    freelancer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("freelancers.id", ondelete="SET NULL"), default=None
    )
    signup_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("signups.id", ondelete="SET NULL"), default=None
    )
    pigro_slugs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    codice: Mapped[str] = mapped_column(String(8), nullable=False)
    prima: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stato: Mapped[str] = mapped_column(String(10), nullable=False, default="in_coda")
    motivo: Mapped[str | None] = mapped_column(String(RECIPIENT_REASON_MAX_LENGTH), default=None)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resend_id: Mapped[str | None] = mapped_column(String(64), default=None)
    disiscrizione_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    consegnata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rimbalzata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    primo_clic_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reclamo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    entrato_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    azione_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_campaign_recipients_campaign_email", "campaign_id", "email", unique=True),
        Index("uq_campaign_recipients_resend_id", "resend_id", unique=True),
        Index("uq_campaign_recipients_token", "disiscrizione_token", unique=True),
        Index("ix_campaign_recipients_email", "email"),
        Index("ix_campaign_recipients_campaign_stato", "campaign_id", "stato"),
        CheckConstraint(
            "stato IN ('in_coda', 'inviata', 'saltata', 'fallita')",
            name="ck_campaign_recipients_stato",
        ),
        CheckConstraint(
            "tipo IN ('freelancer', 'lead', 'azienda', 'proprietario')",
            name="ck_campaign_recipients_tipo",
        ),
    )


class CampaignOptout(Base):
    """An address no campaign may reach (spec § 7): by its own link, by a spam complaint
    Resend reported, or by an admin's «Non scrivere mai». Campaigns only: the magic link,
    the welcome mail and the contracts keep going."""

    __tablename__ = "campaign_optouts"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fonte: Mapped[str] = mapped_column(String(10), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        CheckConstraint("fonte IN ('link', 'reclamo', 'admin')", name="ck_campaign_optouts_fonte"),
    )
