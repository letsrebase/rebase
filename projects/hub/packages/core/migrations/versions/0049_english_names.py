"""English names for every column, closed value, JSON key, constraint and index

Revision ID: 0049
Revises: 0048

The hub's rename to English (REB-902, Lorenzo's decision of 2026-11-02). The tables were
English already; this revision renames what was left inside them:

* every Italian column (`stato` -> `status`, `nome` -> `first_name` or `name`, ...),
* the closed value sets the code and the CHECKs spell (`bozza` -> `draft`, `in_firma` ->
  `awaiting_signature`, `quadro` -> `framework`, `partita_iva` -> `vat_registered`, ...),
* the names and the predicates of the CHECK constraints, and the names of the indexes,
  `uq_orbiters_signups_email_lower` among them,
* the keys of the JSON documents the application reads back: the anonymous card and its
  sources, conflicts and person, a proposal's place, team and economics, the anonymous CV, an
  evaluation's results, a campaign's filters and its recipients' snapshots, a company's CRM
  draft and the payload of an admin action.

`contract_documents.regime` goes from 12 to 16 characters: `vat_registered` is 14.

The contract documents' own `data` is not touched: its keys are the template variables of the
contract texts, which stay Italian. Column renames follow into the indexes, the partial index
and the foreign keys on their own, so only the indexes whose name carries an Italian word are
renamed.

The release is breaking: the old code does not read the new names, so the api, the mcp, the
sweeps and the campaign and card loops are stopped before `alembic upgrade` and started on the
new image. The downgrade restores every old name and value, so the previous image can run
again; JSON documents written by the new code in between are converted back too.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---- columns: table -> (old, new) ---------------------------------------------------------

COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "admin_tokens": (("nome", "name"),),
    "campaign_optouts": (("fonte", "source"),),
    "campaign_recipients": (
        ("azione_at", "action_at"),
        ("codice", "code"),
        ("consegnata_at", "delivered_at"),
        ("disiscrizione_token", "unsubscribe_token"),
        ("entrato_at", "signed_in_at"),
        ("inviata_at", "sent_at"),
        ("motivo", "reason"),
        ("nome", "name"),
        ("prima", "before"),
        ("primo_clic_at", "first_click_at"),
        ("reclamo_at", "complaint_at"),
        ("rimbalzata_at", "bounced_at"),
        ("stato", "status"),
        ("tentativi", "attempts"),
        ("tipo", "kind"),
    ),
    "campaigns": (
        ("azione", "action"),
        ("bottone_meta", "button_meta"),
        ("bottone_testo", "button_text"),
        ("bottone_url", "button_url"),
        ("contenuto_at", "content_at"),
        ("fermo_at", "stalled_at"),
        ("fermo_motivo", "stalled_reason"),
        ("filtri", "filters"),
        ("fonte", "source"),
        ("inviata_at", "sent_at"),
        ("nome", "name"),
        ("oggetto", "subject"),
        ("pigro_errore_at", "pigro_error_at"),
        ("pigro_letto_at", "pigro_read_at"),
        ("programmata_per", "scheduled_for"),
        ("prova_inviata_at", "test_sent_at"),
        ("segue_id", "follows_id"),
        ("stato", "status"),
        ("stato_percorso", "journey_status"),
        ("testo", "text"),
    ),
    "card_eval_results": (("anni_ok", "experience_years_ok"),),
    "companies": (
        ("budget_giornaliero", "daily_budget"),
        ("crm_bozza", "crm_draft"),
        ("crm_errore", "crm_error"),
        ("crm_stato", "crm_status"),
        ("durata", "duration"),
        ("figura_richiesta", "requested_role"),
        ("giorni_presenza", "onsite_days"),
        ("nome_azienda", "company_name"),
        ("numero_risorse", "headcount"),
        ("origine", "origin"),
        ("periodo_da", "period_from"),
        ("progetto", "project"),
        ("remoto", "remote"),
        ("stato", "status"),
    ),
    "contract_documents": (
        ("numero", "number"),
        ("stato", "status"),
        ("testo_bozza", "draft_text"),
    ),
    "contract_letter_counters": (("anno", "year"), ("ultimo", "last")),
    "freelancer_cards": (("fonti", "sources"), ("persona", "person")),
    "freelancer_fiscal": (
        ("codice_fiscale", "tax_code"),
        ("domicilio", "domicile"),
        ("partita_iva", "vat_number"),
        ("pec", "certified_email"),
    ),
    "freelancers": (
        ("anni_admin", "experience_years_admin"),
        ("compilata_da", "filled_by"),
        ("origine", "origin"),
        ("posizione", "job_title"),
        ("remoto", "remote"),
        ("stato", "status"),
        ("tariffa_giornaliera", "daily_rate"),
    ),
    "logins": (("origine", "origin"),),
    "magic_link_tokens": (("origine", "origin"),),
    "matches": (
        ("cliente_piva", "customer_vat_number"),
        ("cliente_ragione_sociale", "customer_legal_name"),
        ("cliente_sede", "customer_registered_office"),
        ("company_budget_giornaliero", "company_daily_budget"),
        ("giorni_previsti", "expected_days"),
        ("lettera_compenso", "letter_fee"),
        ("lettera_data_fine", "letter_end_date"),
        ("lettera_data_inizio", "letter_start_date"),
        ("pigro_errore", "pigro_error"),
        ("pigro_stato", "pigro_status"),
        ("stato", "status"),
    ),
    "notes": (
        ("autore", "author"),
        ("autore_id", "author_id"),
        ("eliminata_at", "deleted_at"),
        ("eliminata_da_id", "deleted_by_id"),
        ("fonte", "source"),
        ("modificata_at", "edited_at"),
        ("modificata_da_id", "edited_by_id"),
        ("testo", "text"),
    ),
    "referral_rewards": (("stato", "status"),),
    "referrals": (("stato", "status"),),
    "signups": (("cognome", "last_name"), ("nome", "first_name"), ("rif", "ref")),
    "team_proposals": (
        ("descrizione", "description"),
        ("economia", "economics"),
        ("errore", "error"),
        ("luogo", "location"),
        ("nota", "note"),
        ("origine", "origin"),
        ("persone", "people"),
        ("riassunto", "summary"),
    ),
    "team_request_talents": (
        ("risposta", "answer"),
        ("risposta_at", "answered_at"),
        ("ruolo", "role"),
    ),
    "team_requests": (
        ("azienda", "company"),
        ("origine", "origin"),
        ("rif", "ref"),
        ("stato", "status"),
        ("telefono", "phone"),
    ),
    "users": (
        ("attivo", "active"),
        ("cognome", "last_name"),
        ("nome", "first_name"),
        ("telefono", "phone"),
    ),
}

# ---- closed values: (table, NEW column) -> (old, new) ----------------------------------------

_PIGRO = (
    ("da_collegare", "to_link"),
    ("collegato", "linked"),
    ("errore", "error"),
    ("rifiutato", "rejected"),
)
_REMOTE = (("remoto", "remote"), ("ibrido", "hybrid"), ("in_sede", "on_site"))
_PERSON_STATUS = (
    ("nuovo", "new"),
    ("contattato", "contacted"),
    ("attivo", "active"),
    ("scartato", "discarded"),
)
_COMPANY_STATUS = (
    ("nuovo", "new"),
    ("contattato", "contacted"),
    ("in_corso", "in_progress"),
    ("chiuso", "closed"),
)
_NOTE_SOURCE = (("persona", "person"), ("sistema", "system"))

VALUES: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("campaign_optouts", "source"): (("reclamo", "complaint"),),
    ("campaign_recipients", "status"): (
        ("in_coda", "queued"),
        ("inviata", "sent"),
        ("saltata", "skipped"),
        ("fallita", "failed"),
    ),
    ("campaign_recipients", "kind"): (("azienda", "company"), ("proprietario", "owner")),
    ("campaigns", "source"): (("stato", "status"), ("filtri", "filters"), ("lista", "list")),
    ("campaigns", "status"): (
        ("bozza", "draft"),
        ("programmata", "scheduled"),
        ("in_invio", "sending"),
        ("inviata", "sent"),
        ("annullata", "cancelled"),
    ),
    ("campaigns", "action"): (
        ("entrato", "signed_in"),
        ("scheda_completa", "card_complete"),
        ("profilo_creato", "profile_created"),
        ("richiesta_aggiornata", "request_updated"),
        ("pigro_cliente", "pigro_customer"),
        ("clic", "click"),
    ),
    ("campaigns", "button_meta"): (("richiesta", "request"),),
    ("campaigns", "journey_status"): (
        ("scheda_vuota_nuovi", "card_empty_new"),
        ("scheda_vuota_entrati", "card_empty_signed_in"),
        ("manca_cv", "missing_cv"),
        ("completo", "complete"),
        ("pigro_vuoto", "pigro_empty"),
        ("azienda_aperta", "company_open"),
    ),
    ("companies", "status"): _COMPANY_STATUS,
    ("companies", "remote"): _REMOTE,
    ("companies", "crm_status"): _PIGRO,
    ("contract_documents", "kind"): (("quadro", "framework"), ("lettera", "letter")),
    ("contract_documents", "status"): (
        ("generato", "generated"),
        ("in_attesa", "pending"),
        ("inviato", "sent"),
        ("firmato", "signed"),
        ("annullato", "cancelled"),
        ("disdetto", "notice_served"),
    ),
    ("contract_documents", "regime"): (
        ("partita_iva", "vat_registered"),
        ("occasionale", "occasional"),
    ),
    ("freelancers", "status"): _PERSON_STATUS,
    ("freelancers", "filled_by"): (("persona", "person"),),
    ("freelancers", "remote"): _REMOTE,
    ("matches", "status"): (
        ("bozza", "draft"),
        ("in_firma", "awaiting_signature"),
        ("attivo", "active"),
        ("concluso", "concluded"),
        ("annullato", "cancelled"),
    ),
    ("matches", "pigro_status"): _PIGRO,
    ("notes", "source"): _NOTE_SOURCE,
    ("referral_rewards", "status"): (
        ("da_confermare", "to_confirm"),
        ("confermato", "confirmed"),
        ("pagato", "paid"),
    ),
    ("referrals", "status"): (("da_verificare", "to_verify"), ("verificato", "verified")),
    ("referrals", "verified_via"): (
        ("accesso", "login"),
        ("lettera", "letter"),
        ("storico", "history"),
    ),
    ("team_proposals", "origin"): (("pubblico", "public"),),
    ("team_requests", "origin"): (("pubblico", "public"),),
    ("team_requests", "status"): (
        ("nuova", "new"),
        ("contattata", "contacted"),
        ("chiusa", "closed"),
    ),
    ("team_request_talents", "answer"): (("si", "yes"),),
    ("ai_tasks", "task"): (("cv_anonimo", "anonymous_cv"),),
    ("ai_usage", "task"): (("cv_anonimo", "anonymous_cv"),),
    ("ai_usage", "purpose"): (("prova", "test"),),
}

# ---- CHECK constraints: dropped before, added after; (table, name, predicate) ------------------

OLD_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("campaign_optouts", "ck_campaign_optouts_fonte", "fonte IN ('link', 'reclamo', 'admin')"),
    (
        "campaign_recipients",
        "ck_campaign_recipients_stato",
        "stato IN ('in_coda', 'inviata', 'saltata', 'fallita')",
    ),
    (
        "campaign_recipients",
        "ck_campaign_recipients_tipo",
        "tipo IN ('freelancer', 'lead', 'azienda', 'proprietario')",
    ),
    (
        "campaigns",
        "ck_campaigns_azione",
        "azione IN ('entrato', 'cv', 'scheda_completa', 'profilo_creato', "
        "'richiesta_aggiornata', 'pigro_cliente', 'clic')",
    ),
    (
        "campaigns",
        "ck_campaigns_bottone_meta",
        "bottone_meta IN ('area', 'wizard', 'pigro', 'richiesta', 'link')",
    ),
    (
        "campaigns",
        "ck_campaigns_bottone_url",
        "(bottone_meta = 'link') = (bottone_url IS NOT NULL)",
    ),
    ("campaigns", "ck_campaigns_filtri", "(fonte = 'filtri') = (filtri IS NOT NULL)"),
    ("campaigns", "ck_campaigns_fonte", "fonte IN ('stato', 'filtri', 'lista')"),
    ("campaigns", "ck_campaigns_link_clic", "(bottone_meta = 'link') = (azione = 'clic')"),
    ("campaigns", "ck_campaigns_segue", "(fonte = 'lista') = (segue_id IS NOT NULL)"),
    (
        "campaigns",
        "ck_campaigns_stato",
        "stato IN ('bozza', 'programmata', 'in_invio', 'inviata', 'annullata')",
    ),
    (
        "campaigns",
        "ck_campaigns_stato_percorso",
        "(fonte = 'stato') = (stato_percorso IS NOT NULL)",
    ),
    (
        "companies",
        "ck_companies_crm_stato",
        "crm_stato IS NULL OR crm_stato IN ('da_collegare', 'collegato', 'errore', 'rifiutato')",
    ),
    (
        "companies",
        "ck_companies_giorni_presenza_range",
        "giorni_presenza IS NULL OR (giorni_presenza >= 1 AND giorni_presenza <= 4)",
    ),
    (
        "companies",
        "ck_companies_giorni_presenza_together",
        "(remoto = 'ibrido') = (giorni_presenza IS NOT NULL)",
    ),
    ("companies", "ck_companies_numero_risorse_positive", "numero_risorse >= 1"),
    ("companies", "ck_companies_remoto", "remoto IN ('remoto', 'ibrido', 'in_sede')"),
    ("companies", "ck_companies_stato", "stato IN ('nuovo', 'contattato', 'in_corso', 'chiuso')"),
    (
        "contract_documents",
        "ck_contract_documents_kind",
        "kind IN ('quadro', 'lettera')",
    ),
    (
        "contract_documents",
        "ck_contract_documents_match_for_letters",
        "(kind = 'quadro') = (match_id IS NULL)",
    ),
    (
        "contract_documents",
        "ck_contract_documents_notice_for_quadro",
        "stato <> 'disdetto' OR kind = 'quadro'",
    ),
    (
        "contract_documents",
        "ck_contract_documents_numero_for_letters",
        "(kind = 'lettera') = (numero IS NOT NULL)",
    ),
    (
        "contract_documents",
        "ck_contract_documents_regime",
        "regime IN ('partita_iva', 'occasionale')",
    ),
    (
        "contract_documents",
        "ck_contract_documents_stato",
        "stato IN ('generato', 'in_attesa', 'inviato', 'firmato', 'annullato', 'disdetto')",
    ),
    ("contract_letter_counters", "ck_contract_letter_counters_positive", "ultimo >= 1"),
    (
        "freelancers",
        "ck_freelancers_anni_admin_range",
        "anni_admin IS NULL OR (anni_admin >= 0 AND anni_admin <= 60)",
    ),
    ("freelancers", "ck_freelancers_compilata_da", "compilata_da IN ('persona', 'admin')"),
    (
        "freelancers",
        "ck_freelancers_remoto",
        "remoto IS NULL OR remoto IN ('remoto', 'ibrido', 'in_sede')",
    ),
    (
        "freelancers",
        "ck_freelancers_stato",
        "stato IN ('nuovo', 'contattato', 'attivo', 'scartato')",
    ),
    (
        "matches",
        "ck_matches_giorni_previsti",
        "giorni_previsti IS NULL OR giorni_previsti BETWEEN 1 AND 366",
    ),
    (
        "matches",
        "ck_matches_pigro_stato",
        "pigro_stato IS NULL OR pigro_stato IN "
        "('da_collegare', 'collegato', 'errore', 'rifiutato')",
    ),
    (
        "matches",
        "ck_matches_stato",
        "stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')",
    ),
    ("notes", "ck_notes_fonte", "fonte IS NULL OR fonte IN ('web', 'mcp', 'persona', 'sistema')"),
    (
        "referral_rewards",
        "ck_referral_rewards_stato",
        "stato IN ('da_confermare', 'confermato', 'pagato')",
    ),
    (
        "referrals",
        "ck_referrals_verification",
        "(stato = 'da_verificare' AND verified_at IS NULL AND verified_via IS NULL) "
        "OR (stato = 'verificato' AND verified_at IS NOT NULL AND verified_via IS NOT NULL "
        "AND verified_via IN ('accesso', 'lettera', 'storico'))",
    ),
    (
        "team_proposals",
        "ck_team_proposals_errore",
        "errore IN ('llm_unavailable', 'team_builder_busy')",
    ),
    (
        "team_proposals",
        "ck_team_proposals_origine",
        "origine IN ('pubblico', 'cloud', 'admin')",
    ),
    (
        "team_proposals",
        "ck_team_proposals_persone",
        "persone IS NULL OR (persone >= 1 AND persone <= 10)",
    ),
    ("team_request_talents", "ck_team_request_talents_risposta", "risposta IN ('si', 'no')"),
    ("team_requests", "ck_team_requests_origine", "origine IN ('pubblico', 'cloud')"),
    ("team_requests", "ck_team_requests_stato", "stato IN ('nuova', 'contattata', 'chiusa')"),
    (
        "ai_tasks",
        "ck_ai_tasks_task",
        "task IN ('card', 'team_proposal', 'cv_anonimo', 'crm_lead')",
    ),
    (
        "ai_usage",
        "ck_ai_usage_task",
        "task IN ('card', 'team_proposal', 'cv_anonimo', 'crm_lead')",
    ),
    ("ai_usage", "ck_ai_usage_purpose", "purpose IN ('run', 'prova', 'eval')"),
)

NEW_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("campaign_optouts", "ck_campaign_optouts_source", "source IN ('link', 'complaint', 'admin')"),
    (
        "campaign_recipients",
        "ck_campaign_recipients_status",
        "status IN ('queued', 'sent', 'skipped', 'failed')",
    ),
    (
        "campaign_recipients",
        "ck_campaign_recipients_kind",
        "kind IN ('freelancer', 'lead', 'company', 'owner')",
    ),
    (
        "campaigns",
        "ck_campaigns_action",
        "action IN ('signed_in', 'cv', 'card_complete', 'profile_created', "
        "'request_updated', 'pigro_customer', 'click')",
    ),
    (
        "campaigns",
        "ck_campaigns_button_meta",
        "button_meta IN ('area', 'wizard', 'pigro', 'request', 'link')",
    ),
    ("campaigns", "ck_campaigns_button_url", "(button_meta = 'link') = (button_url IS NOT NULL)"),
    ("campaigns", "ck_campaigns_filters", "(source = 'filters') = (filters IS NOT NULL)"),
    ("campaigns", "ck_campaigns_source", "source IN ('status', 'filters', 'list')"),
    ("campaigns", "ck_campaigns_link_click", "(button_meta = 'link') = (action = 'click')"),
    ("campaigns", "ck_campaigns_follows", "(source = 'list') = (follows_id IS NOT NULL)"),
    (
        "campaigns",
        "ck_campaigns_status",
        "status IN ('draft', 'scheduled', 'sending', 'sent', 'cancelled')",
    ),
    (
        "campaigns",
        "ck_campaigns_journey_status",
        "(source = 'status') = (journey_status IS NOT NULL)",
    ),
    (
        "companies",
        "ck_companies_crm_status",
        "crm_status IS NULL OR crm_status IN ('to_link', 'linked', 'error', 'rejected')",
    ),
    (
        "companies",
        "ck_companies_onsite_days_range",
        "onsite_days IS NULL OR (onsite_days >= 1 AND onsite_days <= 4)",
    ),
    (
        "companies",
        "ck_companies_onsite_days_together",
        "(remote = 'hybrid') = (onsite_days IS NOT NULL)",
    ),
    ("companies", "ck_companies_headcount_positive", "headcount >= 1"),
    ("companies", "ck_companies_remote", "remote IN ('remote', 'hybrid', 'on_site')"),
    (
        "companies",
        "ck_companies_status",
        "status IN ('new', 'contacted', 'in_progress', 'closed')",
    ),
    ("contract_documents", "ck_contract_documents_kind", "kind IN ('framework', 'letter')"),
    (
        "contract_documents",
        "ck_contract_documents_match_for_letters",
        "(kind = 'framework') = (match_id IS NULL)",
    ),
    (
        "contract_documents",
        "ck_contract_documents_notice_for_framework",
        "status <> 'notice_served' OR kind = 'framework'",
    ),
    (
        "contract_documents",
        "ck_contract_documents_number_for_letters",
        "(kind = 'letter') = (number IS NOT NULL)",
    ),
    (
        "contract_documents",
        "ck_contract_documents_regime",
        "regime IN ('vat_registered', 'occasional')",
    ),
    (
        "contract_documents",
        "ck_contract_documents_status",
        "status IN ('generated', 'pending', 'sent', 'signed', 'cancelled', 'notice_served')",
    ),
    ("contract_letter_counters", "ck_contract_letter_counters_positive", "last >= 1"),
    (
        "freelancers",
        "ck_freelancers_experience_years_admin_range",
        "experience_years_admin IS NULL OR "
        "(experience_years_admin >= 0 AND experience_years_admin <= 60)",
    ),
    ("freelancers", "ck_freelancers_filled_by", "filled_by IN ('person', 'admin')"),
    (
        "freelancers",
        "ck_freelancers_remote",
        "remote IS NULL OR remote IN ('remote', 'hybrid', 'on_site')",
    ),
    (
        "freelancers",
        "ck_freelancers_status",
        "status IN ('new', 'contacted', 'active', 'discarded')",
    ),
    (
        "matches",
        "ck_matches_expected_days",
        "expected_days IS NULL OR expected_days BETWEEN 1 AND 366",
    ),
    (
        "matches",
        "ck_matches_pigro_status",
        "pigro_status IS NULL OR pigro_status IN ('to_link', 'linked', 'error', 'rejected')",
    ),
    (
        "matches",
        "ck_matches_status",
        "status IN ('draft', 'awaiting_signature', 'active', 'concluded', 'cancelled')",
    ),
    ("notes", "ck_notes_source", "source IS NULL OR source IN ('web', 'mcp', 'person', 'system')"),
    (
        "referral_rewards",
        "ck_referral_rewards_status",
        "status IN ('to_confirm', 'confirmed', 'paid')",
    ),
    (
        "referrals",
        "ck_referrals_verification",
        "(status = 'to_verify' AND verified_at IS NULL AND verified_via IS NULL) "
        "OR (status = 'verified' AND verified_at IS NOT NULL AND verified_via IS NOT NULL "
        "AND verified_via IN ('login', 'letter', 'history'))",
    ),
    (
        "team_proposals",
        "ck_team_proposals_error",
        "error IN ('llm_unavailable', 'team_builder_busy')",
    ),
    ("team_proposals", "ck_team_proposals_origin", "origin IN ('public', 'cloud', 'admin')"),
    (
        "team_proposals",
        "ck_team_proposals_people",
        "people IS NULL OR (people >= 1 AND people <= 10)",
    ),
    ("team_request_talents", "ck_team_request_talents_answer", "answer IN ('yes', 'no')"),
    ("team_requests", "ck_team_requests_origin", "origin IN ('public', 'cloud')"),
    ("team_requests", "ck_team_requests_status", "status IN ('new', 'contacted', 'closed')"),
    (
        "ai_tasks",
        "ck_ai_tasks_task",
        "task IN ('card', 'team_proposal', 'anonymous_cv', 'crm_lead')",
    ),
    (
        "ai_usage",
        "ck_ai_usage_task",
        "task IN ('card', 'team_proposal', 'anonymous_cv', 'crm_lead')",
    ),
    ("ai_usage", "ck_ai_usage_purpose", "purpose IN ('run', 'test', 'eval')"),
)

# ---- indexes: old -> new --------------------------------------------------------------------

INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_admin_tokens_nome_trgm", "ix_admin_tokens_name_trgm"),
    ("ix_campaign_recipients_campaign_stato", "ix_campaign_recipients_campaign_status"),
    ("ix_campaigns_stato_programmata_per", "ix_campaigns_status_scheduled_for"),
    ("ix_companies_figura_richiesta_trgm", "ix_companies_requested_role_trgm"),
    ("ix_companies_nome_azienda_trgm", "ix_companies_company_name_trgm"),
    ("ix_companies_progetto_trgm", "ix_companies_project_trgm"),
    ("ix_freelancers_posizione_trgm", "ix_freelancers_job_title_trgm"),
    ("ix_signups_cognome_trgm", "ix_signups_last_name_trgm"),
    ("ix_signups_nome_trgm", "ix_signups_first_name_trgm"),
    ("ix_team_proposals_errore_created_at", "ix_team_proposals_error_created_at"),
    ("ix_team_requests_stato_created_at", "ix_team_requests_status_created_at"),
    ("ix_users_cognome_trgm", "ix_users_last_name_trgm"),
    ("ix_users_nome_trgm", "ix_users_first_name_trgm"),
    ("uq_contract_documents_numero", "uq_contract_documents_number"),
    ("uq_orbiters_signups_email_lower", "uq_signups_email_lower"),
)

# ---- JSON documents: key maps (old -> new) ---------------------------------------------------

# The anonymous card's fields: the card itself, the card's `sources` (keyed by field), an
# evaluation's verdicts and its card.
CARD_KEYS = {
    "ruolo": "role",
    "anni": "experience_years",
    "competenze": "skills",
    "settori": "industries",
    "lingue": "languages",
    "luogo": "location",
    "sintesi": "profile_summary",
}
CONFLICT_KEYS = {"anni_scheda": "experience_years_card", "anni_hub": "experience_years_hub"}
CONFLICT_KINDS = {
    "anni_diversi": "different_experience_years",
    "anni_scartata": "discarded_experience_years",
}
PERSON_KEYS = {
    "nome": "first_name",
    "cognome": "last_name",
    "telefono": "phone",
    "citta": "city",
    "altri_link": "other_links",
    "aziende": "companies",
}
EVAL_KEYS = {
    **CARD_KEYS,
    "anni_ok": "experience_years_ok",
    "anni_checked": "experience_years_checked",
    "anni_ok_rate": "experience_years_ok_rate",
}
CV_KEYS = {
    "lingua": "language",
    "titolo": "title",
    "sommario": "summary",
    "esperienze": "experience",
    "ruolo": "role",
    "azienda": "company",
    "luogo": "location",
    "periodo": "period",
    "punti": "points",
    "testo": "text",
    "competenze": "skills",
    "voci": "entries",
    "formazione": "education",
    "istituto": "institution",
    "certificazioni": "certifications",
    "lingue": "languages",
    "livello": "level",
    "altre_sezioni": "other_sections",
}
PLACE_KEYS = {"locale": "on_site", "dove": "place"}
TEAM_KEYS = {
    "posizione": "position",
    "ruolo": "role",
    "motivazione": "reasoning",
    "giorni_settimana": "week_days",
}
ECONOMICS_KEYS = {"giorno": "day", "mese": "month", "giorni_mese": "days_month"}
BEFORE_KEYS = {
    "ha_scheda": "has_card",
    "ha_cv": "has_cv",
    "completa": "complete",
    "richieste": "requests",
}
CRM_DRAFT_KEYS = {
    "ruolo": "role",
    "giorni_settimana": "week_days",
    "durata_settimane": "duration_weeks",
    "competenze": "skills",
    "sintesi": "profile_summary",
}
TALENT_FILTER_KEYS = {
    "lista": "list",
    "stato": "status",
    "posizione": "job_title",
    "remoto": "remote",
    "tariffa_min": "min_rate",
    "tariffa_max": "max_rate",
    "origine": "origin_page",
    "con_accessi": "with_logins",
    "creato_da": "created_from",
    "creato_a": "created_to",
}
COMPANY_FILTER_KEYS = {
    "lista": "list",
    "stato": "status",
    "periodo_da": "period_from",
    "origine": "origin",
    "creato_da": "created_from",
    "creato_a": "created_to",
}
FILTER_LIST = {"talenti": "talents", "aziende": "companies"}
# What an admin action's payload names: the keys, and the fields of a field override.
PAYLOAD_KEYS = {
    "esito": "outcome",
    "errore": "error",
    "ricostruita": "rebuilt",
    "scheda_da_rigenerare": "card_to_regenerate",
    "documenti": "documents",
    "nota_id": "note_id",
    "riusato": "reused",
    "testo": "text",
    "quadri_annullati": "frameworks_cancelled",
    "documento": "document",
    "fonte": "source",
    "lasciati": "left_behind",
    "numero": "number",
    "azione": "action",
    "rimesse": "put_back",
}
PAYLOAD_OUTCOMES = {
    "scritta": "written",
    "non_scritta": "not_written",
    "da_collegare": "to_link",
    "collegato": "linked",
    "errore": "error",
    "rifiutato": "rejected",
}
PAYLOAD_KINDS = {"quadro": "framework", "lettera": "letter"}
FIELD_KEYS = {
    "posizione": "job_title",
    "remoto": "remote",
    "tariffa_giornaliera": "daily_rate",
    "stato": "status",
    "anni_admin": "experience_years_admin",
    "compilata_da": "filled_by",
    "nome_azienda": "company_name",
    "figura_richiesta": "requested_role",
    "progetto": "project",
    "periodo_da": "period_from",
    "durata": "duration",
    "budget_giornaliero": "daily_budget",
    "giorni_presenza": "onsite_days",
    "numero_risorse": "headcount",
    "codice_fiscale": "tax_code",
    "partita_iva": "vat_number",
    "pec": "certified_email",
    "domicilio": "domicile",
    "nome": "first_name",
    "cognome": "last_name",
    "telefono": "phone",
}
# The value sets of the fields a field override can move, by the entity it is recorded on.
FIELD_VALUES = {
    ("freelancer", "status"): dict(_PERSON_STATUS),
    ("company", "status"): dict(_COMPANY_STATUS),
    ("freelancer", "remote"): dict(_REMOTE),
    ("company", "remote"): dict(_REMOTE),
    ("freelancer", "filled_by"): {"persona": "person"},
}


def _invert(mapping: dict[str, str]) -> dict[str, str]:
    return {new: old for old, new in mapping.items()}


def _deep(value: Any, keys: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {keys.get(key, key): _deep(inner, keys) for key, inner in value.items()}
    if isinstance(value, list):
        return [_deep(inner, keys) for inner in value]
    return value


def _shallow(value: Any, keys: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {keys.get(key, key): inner for key, inner in value.items()}
    return value


def _conflicts(value: Any, keys: dict[str, str], kinds: dict[str, str]) -> Any:
    if not isinstance(value, list):
        return value
    out = []
    for item in value:
        item = _shallow(item, keys)
        if isinstance(item, dict) and item.get("kind") in kinds:
            item = {**item, "kind": kinds[item["kind"]]}
        out.append(item)
    return out


def _filters(value: Any, forward: bool) -> Any:
    """A campaign's stored list filters: the list's name, the keys and the closed values."""
    if not isinstance(value, dict):
        return value
    talents_old, companies_old = "talenti", "aziende"
    listed = value.get("lista" if forward else "list")
    names = FILTER_LIST if forward else _invert(FILTER_LIST)
    is_talents = listed == (talents_old if forward else "talents")
    is_companies = listed == (companies_old if forward else "companies")
    keys = TALENT_FILTER_KEYS if is_talents else COMPANY_FILTER_KEYS if is_companies else {}
    keys = keys if forward else _invert(keys)
    out = _shallow(value, keys)
    list_key = "list" if forward else "lista"
    if list_key in out:
        out[list_key] = names.get(out[list_key], out[list_key])
    status_key = "status" if forward else "stato"
    remote_key = "remote" if forward else "remoto"
    statuses = dict(_PERSON_STATUS if is_talents else _COMPANY_STATUS)
    remotes = dict(_REMOTE)
    if not forward:
        statuses, remotes = _invert(statuses), _invert(remotes)
    if isinstance(out.get(status_key), str):
        out[status_key] = statuses.get(out[status_key], out[status_key])
    if isinstance(out.get(remote_key), str):
        out[remote_key] = remotes.get(out[remote_key], out[remote_key])
    return out


# Keys an old note entry (`note_added`, `note_edited`, `note_deleted`) spelled in Italian and
# every other entry already spelled in English: going back, only a note's are translated.
_NOTE_ONLY = ("source", "text")


def _payload(entity: str, kind: str, payload: Any, forward: bool) -> Any:
    if not isinstance(payload, dict):
        return payload
    keys = PAYLOAD_KEYS if forward else _invert(PAYLOAD_KEYS)
    fields = FIELD_KEYS if forward else _invert(FIELD_KEYS)
    outcomes = PAYLOAD_OUTCOMES if forward else _invert(PAYLOAD_OUTCOMES)
    kinds = PAYLOAD_KINDS if forward else _invert(PAYLOAD_KINDS)
    is_note = kind.startswith("note_")
    out = {}
    for key, inner in payload.items():
        if not forward and key in _NOTE_ONLY and not is_note:
            out[key] = inner
        else:
            out[keys.get(key, key)] = inner
    if isinstance(out.get("outcome" if forward else "esito"), str):
        name = "outcome" if forward else "esito"
        out[name] = outcomes.get(out[name], out[name])
    if isinstance(out.get("kind"), str):
        out["kind"] = kinds.get(out["kind"], out["kind"])
    if isinstance(out.get("changed"), list):
        out["changed"] = [
            fields.get(name, name) if isinstance(name, str) else name for name in out["changed"]
        ]
    for side in ("before", "after"):
        snapshot = out.get(side)
        if not isinstance(snapshot, dict):
            continue
        moved = {}
        for name, inner in snapshot.items():
            new_name = fields.get(name, name)
            values = FIELD_VALUES.get((entity, new_name if forward else name), {})
            values = values if forward else _invert(values)
            moved[new_name] = values.get(inner, inner) if isinstance(inner, str) else inner
        out[side] = moved
    return out


# table, column, forward, backward (each takes the stored value)
def _json_columns() -> list[tuple[str, str, Callable[[Any], Any], Callable[[Any], Any]]]:
    def keyed(
        keys: dict[str, str], deep: bool = True
    ) -> tuple[Callable[[Any], Any], Callable[[Any], Any]]:
        step = _deep if deep else _shallow
        return (lambda v: step(v, keys)), (lambda v: step(v, _invert(keys)))

    card = keyed(CARD_KEYS)
    person = keyed(PERSON_KEYS)
    evaluation = keyed(EVAL_KEYS)
    cv = keyed(CV_KEYS)
    place = keyed(PLACE_KEYS)
    team = keyed(TEAM_KEYS)
    economics = keyed(ECONOMICS_KEYS)
    before = keyed(BEFORE_KEYS, deep=False)
    draft = keyed(CRM_DRAFT_KEYS, deep=False)
    return [
        ("freelancer_cards", "card", *card),
        ("freelancer_cards", "sources", *card),
        (
            "freelancer_cards",
            "conflicts",
            lambda v: _conflicts(v, CONFLICT_KEYS, CONFLICT_KINDS),
            lambda v: _conflicts(v, _invert(CONFLICT_KEYS), _invert(CONFLICT_KINDS)),
        ),
        ("freelancer_cards", "person", *person),
        ("card_eval_results", "card", *evaluation),
        ("card_eval_results", "checks", *evaluation),
        ("card_eval_results", "judgement", *evaluation),
        ("card_eval_runs", "summary", *evaluation),
        ("anonymous_cvs", "body", *cv),
        ("team_proposals", "location", *place),
        ("team_proposals", "team", *team),
        ("team_proposals", "economics", *economics),
        ("campaign_recipients", "before", *before),
        ("companies", "crm_draft", *draft),
        (
            "campaigns",
            "filters",
            lambda v: _filters(v, True),
            lambda v: _filters(v, False),
        ),
    ]


def _rewrite(conn: sa.Connection, table: str, column: str, step: Callable[[Any], Any]) -> None:
    """Applies `step` to every stored document of the column. The row is found again by its
    `ctid`: a table here may have a composite key, and nothing else writes inside the
    migration's own transaction."""
    rows = conn.execute(
        sa.text(
            f"SELECT ctid::text AS row_id, {column} AS doc FROM {table} WHERE {column} IS NOT NULL"
        )
    ).all()
    for row in rows:
        moved = step(row.doc)
        if moved != row.doc:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = CAST(:doc AS jsonb) "
                    "WHERE ctid = CAST(:row_id AS tid)"
                ),
                {"doc": json.dumps(moved), "row_id": row.row_id},
            )


def _rewrite_payloads(conn: sa.Connection, forward: bool) -> None:
    rows = conn.execute(sa.text("SELECT id, entity_type, kind, payload FROM admin_actions")).all()
    for row in rows:
        moved = _payload(row.entity_type, row.kind, row.payload, forward)
        if moved != row.payload:
            conn.execute(
                sa.text("UPDATE admin_actions SET payload = CAST(:doc AS jsonb) WHERE id = :pk"),
                {"doc": json.dumps(moved), "pk": row.id},
            )


def _drop_checks(checks: Sequence[tuple[str, str, str]]) -> None:
    for table, name, _ in checks:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def _add_checks(checks: Sequence[tuple[str, str, str]]) -> None:
    for table, name, predicate in checks:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})")


def _rename_columns(forward: bool) -> None:
    for table, pairs in COLUMNS.items():
        for old, new in pairs:
            source, target = (old, new) if forward else (new, old)
            op.execute(f"ALTER TABLE {table} RENAME COLUMN {source} TO {target}")


def _rewrite_values(forward: bool) -> None:
    for (table, column), pairs in VALUES.items():
        for old, new in pairs:
            source, target = (old, new) if forward else (new, old)
            op.execute(
                sa.text(
                    f"UPDATE {table} SET {column} = :target WHERE {column} = :source"
                ).bindparams(source=source, target=target)
            )


def _rename_indexes(forward: bool) -> None:
    for old, new in INDEXES:
        source, target = (old, new) if forward else (new, old)
        op.execute(f"ALTER INDEX IF EXISTS {source} RENAME TO {target}")


def upgrade() -> None:
    conn = op.get_bind()
    _drop_checks(OLD_CHECKS)
    _rename_columns(forward=True)
    op.execute("ALTER TABLE contract_documents ALTER COLUMN regime TYPE VARCHAR(16)")
    _rewrite_values(forward=True)
    for table, column, forward, _ in _json_columns():
        _rewrite(conn, table, column, forward)
    _rewrite_payloads(conn, forward=True)
    _add_checks(NEW_CHECKS)
    _rename_indexes(forward=True)


def downgrade() -> None:
    conn = op.get_bind()
    _drop_checks(NEW_CHECKS)
    _rename_indexes(forward=False)
    _rewrite_payloads(conn, forward=False)
    for table, column, _, backward in reversed(_json_columns()):
        _rewrite(conn, table, column, backward)
    _rewrite_values(forward=False)
    op.execute("ALTER TABLE contract_documents ALTER COLUMN regime TYPE VARCHAR(12)")
    _rename_columns(forward=False)
    _add_checks(OLD_CHECKS)
