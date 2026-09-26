"""campaigns, campaign_recipients, campaign_optouts: a list, a mail, and who may not get one

Revision ID: 0020
Revises: 0019

P-REB-41 phase 1 (spec 2026-09-25-admin-campaigns-design.md § 3). Three new tables and
nothing touched elsewhere. Conditional like every migration of this package, so a
retried deploy passes over what the first attempt created. None of the three is read by
PostHog's warehouse (`test_warehouse_contract.py`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_TABLES = (
    "CREATE TABLE IF NOT EXISTS campaigns ("
    "id UUID PRIMARY KEY, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "nome VARCHAR(120) NOT NULL, "
    "slug VARCHAR(80) NOT NULL, "
    "fonte VARCHAR(10) NOT NULL, "
    "stato_percorso VARCHAR(30), "
    "filtri JSONB, "
    "segue_id UUID REFERENCES campaigns (id), "
    "oggetto VARCHAR(200) NOT NULL, "
    "testo TEXT NOT NULL, "
    "bottone_testo VARCHAR(60) NOT NULL, "
    "bottone_meta VARCHAR(10) NOT NULL, "
    "azione VARCHAR(25) NOT NULL, "
    "stato VARCHAR(12) NOT NULL, "
    f"contenuto_at {_TS} NOT NULL, "
    f"programmata_per {_TS}, "
    f"prova_inviata_at {_TS}, "
    f"inviata_at {_TS}, "
    f"pigro_letto_at {_TS}, "
    f"pigro_errore_at {_TS}, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_campaigns_fonte CHECK (fonte IN ('stato', 'filtri', 'lista')), "
    "CONSTRAINT ck_campaigns_stato CHECK "
    "(stato IN ('bozza', 'programmata', 'in_invio', 'inviata', 'annullata')), "
    "CONSTRAINT ck_campaigns_azione CHECK (azione IN ('entrato', 'cv', 'scheda_completa', "
    "'profilo_creato', 'richiesta_aggiornata', 'pigro_cliente')), "
    "CONSTRAINT ck_campaigns_bottone_meta CHECK "
    "(bottone_meta IN ('area', 'wizard', 'pigro', 'richiesta')), "
    "CONSTRAINT ck_campaigns_stato_percorso CHECK "
    "((fonte = 'stato') = (stato_percorso IS NOT NULL)), "
    "CONSTRAINT ck_campaigns_filtri CHECK ((fonte = 'filtri') = (filtri IS NOT NULL)), "
    "CONSTRAINT ck_campaigns_segue CHECK ((fonte = 'lista') = (segue_id IS NOT NULL)))",
    "CREATE TABLE IF NOT EXISTS campaign_recipients ("
    "id UUID PRIMARY KEY, "
    "campaign_id UUID NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE, "
    "email VARCHAR(320) NOT NULL, "
    "nome VARCHAR(120), "
    "tipo VARCHAR(12) NOT NULL, "
    "user_id UUID REFERENCES users (id) ON DELETE SET NULL, "
    "freelancer_id UUID REFERENCES freelancers (id) ON DELETE SET NULL, "
    "signup_id UUID REFERENCES signups (id) ON DELETE SET NULL, "
    "pigro_slugs JSONB NOT NULL, "
    "codice VARCHAR(8) NOT NULL, "
    "prima JSONB NOT NULL, "
    "stato VARCHAR(10) NOT NULL, "
    "motivo VARCHAR(200), "
    "tentativi INTEGER NOT NULL, "
    "resend_id VARCHAR(64), "
    "disiscrizione_token VARCHAR(64) NOT NULL, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"inviata_at {_TS}, "
    f"consegnata_at {_TS}, "
    f"rimbalzata_at {_TS}, "
    f"primo_clic_at {_TS}, "
    f"reclamo_at {_TS}, "
    f"entrato_at {_TS}, "
    f"azione_at {_TS}, "
    "CONSTRAINT ck_campaign_recipients_stato CHECK "
    "(stato IN ('in_coda', 'inviata', 'saltata', 'fallita')), "
    "CONSTRAINT ck_campaign_recipients_tipo CHECK "
    "(tipo IN ('freelancer', 'lead', 'azienda', 'proprietario')))",
    "CREATE TABLE IF NOT EXISTS campaign_optouts ("
    "email VARCHAR(320) PRIMARY KEY, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    "fonte VARCHAR(10) NOT NULL, "
    "campaign_id UUID REFERENCES campaigns (id) ON DELETE SET NULL, "
    "CONSTRAINT ck_campaign_optouts_fonte CHECK (fonte IN ('link', 'reclamo', 'admin')))",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaigns_slug ON campaigns (slug)",
    "CREATE INDEX IF NOT EXISTS ix_campaigns_stato_programmata_per "
    "ON campaigns (stato, programmata_per)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_campaign_email "
    "ON campaign_recipients (campaign_id, email)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_resend_id "
    "ON campaign_recipients (resend_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_token "
    "ON campaign_recipients (disiscrizione_token)",
    "CREATE INDEX IF NOT EXISTS ix_campaign_recipients_email ON campaign_recipients (email)",
    "CREATE INDEX IF NOT EXISTS ix_campaign_recipients_campaign_stato "
    "ON campaign_recipients (campaign_id, stato)",
)


def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES):
        op.execute(statement)


def downgrade() -> None:
    for table in ("campaign_optouts", "campaign_recipients", "campaigns"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
