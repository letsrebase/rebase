"""What an agent credential may do: the three classes an operation belongs to, and the
display names the refusals use.

An operation is named by the key its service passes to `Actor.require_write`,
`require_admin` or `require_unscoped_admin` (an English snake_case key; the sentence a person
reads comes from `ACTION_LABELS`). An actor of type `mcp` (a personal access token, over MCP
or over REST: `PatService.resolve` answers `Actor(type="mcp")` for both) is checked against
these sets, and **an action in none of them is refused**: a new operation is closed to agents
until somebody classifies it here. The previous design was a denylist, so every operation
nobody remembered to name was open, and that is how a token could create administrators,
rewrite the space settings or send mail from the owner's mailbox over REST while no tool
existed for it (PAPI-01).

* `AGENT_ALLOWED_ACTIONS`: what a token does with the default settings. It mirrors the MCP
  tool surface: the operations that have a tool (or that a tool runs inside of) and nothing
  else. `apps/mcp/tests/test_mcp_surface_coverage.py` is the inventory of what has no tool.
* `FULL_ACCESS_ACTIONS`: open only to a token that carries `full_access`, which needs the
  space's `mcp_full_access` ceiling as well (REB-810). They are not merely privileged, they
  are irreversible in a way the rest of the product is not: issuing consumes a number from
  a gap-free fiscal register, annulling and `mark_transmitted_externally` change what an
  immutable document says afterwards, the rate and period-lock operations rewrite what a
  quarter's work was worth, and the Drive and Gmail reads spend the owner's quota under the
  owner's consent and return content written by outsiders. The check keys on the credential,
  never on the role, so an administrator's token does not pass by being an administrator.
* `PERSON_ONLY_ACTIONS`: no agent credential, whatever it carries. Accounts and credentials,
  the space's own settings (including the `mcp_full_access` ceiling itself), the connections
  to Google and Slack, mail that leaves in the owner's name, and the configuration that gives
  the CRM its shape. Listed rather than left implicit so a test can prove every action a
  service names was classified on purpose.

The real cure for all three remains scopes on a token (residuo R10): let a token say what it
is for, instead of inheriting its owner's role and being told no operation by operation.
"""

AGENT_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        # Activities and notes.
        "create_task",
        "update_task",
        "complete_task",
        "cancel_task",
        "reopen_task",
        "archive_task",
        "restore_task",
        "add_note",
        "update_note",
        "delete_note",
        # Customers, people, deals.
        "create_customer",
        "update_customer",
        "delete_customer",
        "restore_customer",
        "change_customer_owner",
        "create_person",
        "update_person",
        "delete_person",
        "restore_person",
        "create_deal",
        "update_deal",
        "move_deal",
        "delete_deal",
        "restore_deal",
        # Documents (`create_document` and `add_document_version` run inside
        # `create_document_from_template`; `import_document` inside invoice artefacts).
        "create_document",
        "create_document_from_template",
        "add_document_version",
        "import_document",
        "regenerate_document",
        "delete_document",
        "restore_document",
        "set_offer_state",
        # Contracts.
        "create_contract",
        "convert_deal_to_contract",
        "update_contract",
        "set_contract_state",
        "create_rate_card",
        "update_rate_card",
        "create_contract_expense",
        "update_contract_expense",
        "create_clause_note",
        "update_clause_note",
        "set_renewal_assumption",
        "renew_contract",
        # Time and costs.
        "log_time",
        "update_time_entry",
        "delete_time_entry",
        "restore_time_entry",
        "start_timer",
        "stop_timer",
        "update_timer",
        "discard_timer",
        "create_cost",
        "update_cost",
        "delete_cost",
        "restore_cost",
        # Invoices up to, and not including, the fiscal acts.
        "create_invoice",
        "update_invoice",
        "delete_invoice",
        "replace_invoice_lines",
        "confirm_proforma",
        "set_payment_state",
        "produce_invoice_artifacts",
        # An agent proposes; a person decides (`accept_proposal` is person-only).
        "create_proposal",
        # The emitter and its fiscal profile: setup, not history (ORB-188). The profile
        # write is a total replacement that can be replaced again and an issued invoice
        # keeps its own copy.
        "create_legal_entity",
        "upsert_legal_entities",
        "update_fiscal_profile",
        # Drafts an agent prepares for a person to send.
        "create_email_draft",
        # Reads that have a tool and a gate for the sake of the role.
        "cash_overview",
        "read_scheduled_jobs",
        "read_slack_health",
        "set_reminder_defaults",
    }
)

FULL_ACCESS_ACTIONS: frozenset[str] = frozenset(
    {
        # Fiscal acts: they consume a register number or change what an immutable
        # document says after the fact (slice 3 §11).
        "issue_invoice",
        "annul_invoice",
        "mark_transmitted_externally",
        "export_invoice_xml",
        # Rates, cost categories and period locks: configuration, or rewriting what a
        # quarter's work was worth (slice 4 §11).
        "recalculate_rates",
        "update_user_rates",
        "update_deal_rate",
        "create_cost_category",
        "update_cost_category",
        "archive_cost_category",
        "unarchive_cost_category",
        "close_period",
        "reopen_period",
        # The bridge from hours (and from a contract's days, REB-751) to an invoice line,
        # and the annual estimate.
        "bind_time_to_invoice",
        "bill_contract_days",
        "get_fiscal_estimate",
        # The space's profile and which modules are on (spec 2026-11-09, B1): what the whole
        # team sees changes. An administrator's token with `full_access`, nothing less; the
        # generic `update_space_settings` stays person-only below.
        "set_space_profile",
        "set_space_modules",
        # The text that leaves in the owner's name, and the draft that carries an invoice
        # and its register (REB-753). Sending has no MCP counterpart by structure.
        "create_contract_email_template",
        "update_contract_email_template",
        "prepare_contract_email",
        # Gmail reads that spend the owner's quota under the owner's consent, and the text
        # of an attachment, which is a file written by an outside sender.
        "discover_gmail_correspondents",
        "suggest_customers_from_gmail",
        "read_gmail_attachment",
        # Slice 9 §3.6: writing a numbered row straight into the fiscal register, declaring
        # the numbers it will never carry, and the review and confirm steps (REB-365/366)
        # that converge on the same write.
        "import_issued_invoice",
        "declare_invoice_register_gaps",
        "review_invoice_import",
        "confirm_invoice_import",
        # Slice 9 §4.2: reading the owner's Google Drive and copying a file from it, and
        # configuring the grant (which folders the CRM may read and write, connecting and
        # disconnecting the account). Settings operations have no MCP tool and never will;
        # a PAT reaches them over REST unless they are here.
        "list_drive_files",
        "read_drive_file",
        "import_drive_file",
        "set_drive_roots",
        "set_drive_storage_folder",
        "connect_drive",
        "disconnect_drive",
        # REB-745..749: the day ledger's writes. An agent proposes a day and a person
        # confirms it (`accept_proposal`), which writes the approval and the day itself.
        "create_work_unit",
        "transition_work_unit",
        "create_approval",
        "request_approval",
        # The inbox's upload road (REB-971): bytes enter over the web and REST only, never over
        # MCP (design 2026-11-09, § 6), so no tool runs it. A token with `full_access` reaches
        # it over REST, for a script that feeds the inbox; a default token does not.
        "upload_inbox_file",
    }
)

PERSON_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        # Accounts and credentials: a token that minted tokens or administrators would
        # outlive its own revocation.
        "create_user",
        "update_user",
        "list_users",
        "reset_password",
        "invite_user",
        "resend_invite",
        "revoke_invite",
        "list_invites",
        "create_token",
        "revoke_token",
        "list_tokens",
        "list_team_tokens",
        "create_full_access_token",
        "set_token_full_access",
        # The space's own settings, including the ceiling that decides what a token may do.
        "read_space_settings",
        "update_space_settings",
        # Decisions a person takes about what an agent proposed (REB-806).
        "accept_proposal",
        "reject_proposal",
        # Connections to third parties, and mail that leaves in the owner's name.
        "connect_gmail",
        "disconnect_gmail",
        "update_gmail_settings",
        "sync_gmail",
        "backfill_gmail",
        "send_email",
        "reconcile_email",
        "update_email_draft",
        "delete_email_draft",
        "prepare_payment_reminder",
        "connect_slack",
        "disconnect_slack",
        "manage_slack",
        # What the CRM looks like: the shape of the pipeline, the custom fields, the
        # templates, the automations, the emitters' assets and defaults.
        "create_pipeline_stage",
        "update_pipeline_stage",
        "delete_pipeline_stage",
        "seed_pipeline",
        "create_field_definition",
        "update_field_definition",
        "archive_field_definition",
        "unarchive_field_definition",
        "create_template",
        "update_template",
        "activate_template",
        "deactivate_template",
        "seed_templates",
        "seed_cost_categories",
        "update_automation_config",
        "set_default_legal_entity",
        "deactivate_legal_entity",
        "update_azienda_assets",
        "update_document",
        # Reads of the configuration's own history, and the register's PDF (it archives a
        # document).
        "read_user_timeline",
        "read_field_definition_timeline",
        "read_pipeline_stage_timeline",
        "render_contract_register",
    }
)

# The sentence a person reads where the key would be jargon. Keys with no entry are shown
# as they are: they were already readable tool names.
ACTION_LABELS: dict[str, str] = {
    "send_email": "inviare un'email",
    "reconcile_email": "verificare l'esito di un'email",
    "prepare_payment_reminder": "preparare un sollecito",
    "connect_gmail": "collegare un account Google",
    "disconnect_gmail": "scollegare un account Google",
    "update_gmail_settings": "modificare le impostazioni Gmail",
    "sync_gmail": "sincronizzare Gmail",
    "backfill_gmail": "sincronizzare lo storico Gmail",
    "set_drive_roots": "impostare le cartelle Drive",
    "set_drive_storage_folder": "scegliere la cartella di scrittura Drive",
    "connect_drive": "collegare Google Drive",
    "disconnect_drive": "scollegare Google Drive",
    "connect_slack": "collegare Slack",
    "disconnect_slack": "scollegare Slack",
    "manage_slack": "gestire Slack",
    "convert_deal_to_contract": "trasformare un deal in contratto",
    "set_space_profile": "cambiare il profilo dello spazio",
    "set_space_modules": "accendere o spegnere i moduli dello spazio",
    "upload_inbox_file": "caricare un documento in arrivo",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)
