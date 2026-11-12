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
        # Organizations, people, deals.
        "create_organization",
        "update_organization",
        "delete_organization",
        "restore_organization",
        "change_customer_owner",
        "list_duplicate_organizations",
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
        # Time and purchases.
        "log_time",
        "update_time_entry",
        "delete_time_entry",
        "restore_time_entry",
        "start_timer",
        "stop_timer",
        "update_timer",
        "discard_timer",
        "create_purchase",
        "update_purchase",
        "cancel_purchase",
        "delete_purchase",
        "restore_purchase",
        # The recurrence rule of money going out (due items design, § 1.2): a purchase's
        # sibling, so the same class.
        "create_recurring_expense",
        "stop_recurring_expense",
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
        # What an item of the inbox is, and putting one aside (design 2026-11-09, § 6,
        # «classify»): nothing is filed or written as a record, and a proposal waiting on the
        # item is a person's to decide, so a call on such an item is refused for an agent.
        "set_inbox_item_kind",
        "discard_inbox_item",
        "mark_inbox_item_duplicate",
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
        # Due items (spec 2026-11-09, due items, § 5): reading the ordinary kinds, and a
        # client's payment recorded or undone, as `set_payment_state` is. The outgoing and
        # fiscal halves are below.
        "list_due_items",
        "get_due_item",
        "settle_due_item",
        "reverse_settlement",
        # The cash schedule, the payables mirror and the phone's money figures (REB-1025): the
        # ordinary kinds are any member's; what the schedule discloses of the fiscal kinds and
        # of the bank accounts is gated by the two keys in the full-access set below.
        "read_cash_schedule",
        # Engagements with freelancers (collaborators design § 6): the administrator's token reads
        # them, updates a draft and asks when a freelancer is free. Creating one, ending one and
        # the tax data are in the full-access set below.
        "read_engagements",
        "update_engagement",
        "get_freelancer_availability",
        # An expense note (spec 2026-11-09, staff, § 3 and § 4): an agent submits for the staff
        # member its owner is, from an inbox item the owner uploaded; the service refuses any
        # other staff member. Deciding and reading everyone's are the full-access set below.
        "submit_expense_note",
        # What a letter or framework agreement would say now: a read, with the free text of the
        # texts and of the engagement in it.
        "preview_engagement",
        # The live report of an engagement's days and what is payable: a read.
        "read_engagement_report",
        # Asking Documenso where a document stands and applying its answer: the same thing the
        # webhook does, so it only reads Documenso's state (collaborators design § 6).
        "refresh_document_signature",
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
        # The accountant's period bundle: every fiscal figure of an entity in one file
        # (accountant export design § 5).
        "build_accountant_export",
        "get_export_download_url",
        # What would make a period's export incomplete (§ 2): counts and the first few names of
        # open points across the books, an administrator's like the export itself.
        "get_export_checklist",
        # A time-limited link to one stored export, opened with no account: the address is the
        # credential, so only an administrator's token with `full_access` makes one (§ 5).
        "create_export_share_link",
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
        # and the annual estimate. A contract's recurring fee is the same bridge (REB-1032):
        # the draft it makes names the rate card and the period, so issuing it replaces the
        # forecast of that occurrence.
        "bind_time_to_invoice",
        "bill_contract_days",
        "bill_contract_fee",
        "get_fiscal_estimate",
        # The dates of the taxes a legal entity owes (REB-1004): its tax position, as the estimate.
        "get_tax_calendar",
        # The accountant's figures of a tax return, typed beside the estimate (REB-1006, taxes
        # design § 5): the most sensitive data the product holds, so an administrator's token
        # with `full_access`, nothing less. Accepting one read from a document stays a person's.
        "set_declared_tax_figure",
        # The losses of earlier years the estimate carries forward (REB-1088, taxes design § 1
        # and § 5): typed from the return like a declared figure and moving the same tax due
        # items, so the same position.
        "set_tax_loss",
        "delete_tax_loss",
        # The advance method of a tax year (REB-1074, taxes design § 5): reversible, but it moves
        # the tax due items, so the same position as the figures it reads.
        "set_tax_advance_method",
        # The instalment plan of the June payments and the plan of a notice from the tax office
        # (REB-1075, taxes design § 5): they move the tax due items and say what is owed to the
        # tax office, so the same position as the figures; reading them is the same.
        "set_tax_instalment_plan",
        "create_tax_notice_plan",
        "update_tax_notice_plan",
        "delete_tax_notice_plan",
        "list_tax_plans",
        # Grouping due items into an F24, reading the groups and ungrouping (REB-1076, taxes
        # design § 5): the fiscal position and a payment's shape, so an administrator's token with
        # `full_access`. Settling one goes through the bank matching; linking a receipt (a file in
        # the inbox) stays a person's step, with no agent action.
        "group_f24",
        "ungroup_f24",
        "list_f24_groups",
        # The IVA settlement of a period and the due item it writes (REB-1021): the legal
        # entity's fiscal position, so an administrator's token with `full_access`.
        "settle_iva_period",
        "list_iva_settlements",
        # The IVA periods and the registers behind them (REB-1024): the same position.
        "list_iva_periods",
        "get_iva_register",
        # Closing an IVA period against writes to its registers, and reopening it (REB-1036):
        # as `close_period` is, an act on the whole space, so an administrator's token with
        # `full_access`; the service asks for an unscoped administrator.
        "close_iva_period",
        "reopen_iva_period",
        # The space's profile and which modules are on (spec 2026-11-09, B1): what the whole
        # team sees changes. An administrator's token with `full_access`, nothing less; the
        # generic `update_space_settings` stays person-only below.
        "set_space_profile",
        "set_space_modules",
        "skip_space_onboarding",
        # The text that leaves in the owner's name, and the draft that carries an invoice
        # and its register (REB-753). Sending has no MCP counterpart by structure.
        "create_contract_email_template",
        "update_contract_email_template",
        "prepare_contract_email",
        # Gmail reads that spend the owner's quota under the owner's consent, and the text
        # of an attachment, which is a file written by an outside sender.
        "discover_gmail_correspondents",
        "suggest_organizations_from_gmail",
        "read_gmail_attachment",
        # Slice 9 §3.6: writing a numbered row straight into the fiscal register, declaring
        # the numbers it will never carry, and the review and confirm steps (REB-365/366)
        # that converge on the same write.
        "import_issued_invoice",
        "declare_invoice_register_gaps",
        "review_invoice_import",
        "confirm_invoice_import",
        # Making an organization shared across the legal entities (REB-970): what an invoice
        # import offers when a customer is billed by two of them.
        "share_organization",
        # Folding two rows of one company into one (REB-995): it rewrites what every deal,
        # invoice and timeline points at and archives a row, so an administrator's token with
        # `full_access`, nothing less. The preview is the same action, with `dry_run`.
        "merge_organizations",
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
        # Reading an inbox item (REB-972): it spends the space's model, money the owner capped
        # (a default token proposes with `propose_inbox_item` and reads nothing itself).
        "classify_inbox_item",
        # Due items: money going out marked paid or unmarked, and every item whose source is
        # a tax, IVA, withholding or payroll (the fiscal position, C3 and C6), whatever the
        # direction. An administrator's token with `full_access`, nothing less.
        "settle_outgoing_due_item",
        "reverse_outgoing_settlement",
        "read_fiscal_due_items",
        # The bank accounts' balances: the schedule leaves them out of its answer for a token
        # that lacks `full_access`, and the account reads need the same.
        "read_bank_accounts",
        # Staff and directors with their pay (spec 2026-11-09, staff, payroll and expenses,
        # § 4): the most personal data the product holds. Reading it and writing it are an
        # administrator's act and, for an agent, a `full_access` token's, nothing less; the
        # four are the design's tool table. The legal entity's pay day has no tool and is
        # person-only below.
        "create_staff_member",
        "update_staff_member",
        "get_staff_member",
        "list_staff",
        # The month's estimate per staff member (REB-1008): cost and cash, so pay.
        "get_payroll_month",
        # Deciding on an expense note and paying it back, and reading every note in scope (staff
        # design § 4): an administrator's token with `full_access`, and never on its own note.
        "approve_expense_note",
        "reject_expense_note",
        "reimburse_expense_note",
        "read_all_expense_notes",
        # Closing a payroll month locks its payslips and its estimates (staff design § 4): an
        # administrator's act and, for an agent, a `full_access` token's.
        "close_payroll_month",
        # Engagements: creating one draws a letter number a cancellation never gives back,
        # ending or sending one acts on a legal document, and the tax data is personal data
        # printed on contracts (collaborators design § 6). The hub id is a freelancer's identity.
        "create_engagement",
        "cancel_engagement",
        "close_engagement",
        "send_engagement_for_signature",
        # Sending the mail with the link again, cancelling a framework agreement and recording a
        # notice each act on a legal document or mail a freelancer (collaborators design § 6).
        "resend_signing_mail",
        "cancel_framework_agreement",
        "record_framework_notice",
        "set_freelancer_tax_data",
        "set_organization_hub_freelancer",
        # Bank accounts, their statements and movements (REB-1025, cash design): the company's
        # position. An administrator's token with `full_access`, nothing less. A statement file
        # and its revert travel over REST only (bytes never cross MCP): a token with `full_access`
        # reaches them for a script that feeds the account.
        "list_bank_accounts",
        "create_bank_account",
        "update_bank_account",
        "archive_bank_account",
        "list_statements",
        "list_bank_movements",
        "get_bank_account_coverage",
        "import_statement",
        "revert_statement",
        # Matching a bank movement to the due items it pays (REB-1042): an agent reads a movement
        # with its ranked candidates and proposes a match, and a person confirms or undoes it
        # (the two person-only actions below). The same position as the movements themselves.
        "get_bank_movement",
        "propose_movement_match",
    }
)

PERSON_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        # Linking an F24's receipt, a file in the inbox, to its group, and taking it off (REB-1076).
        "attach_f24_receipt",
        "detach_f24_receipt",
        # Saying an F24 with nothing to pay was filed settles its lines as a movement's match
        # does, and taking that back (REB-1093): the same position as confirming a match.
        "file_f24",
        "reopen_f24",
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
        # Settling a due item with a bank movement as its evidence, and taking that back: a
        # person's act, like accepting any other proposal (REB-1042, brief « The matching panel »).
        "confirm_movement_match",
        # The difference an advance already paid left on a tax item asks a person to confirm it
        # (REB-1079, REB-1074): its legal treatment is «to verify», so no credential decides it.
        "confirm_tax_carry",
        "undo_movement_match",
        # The freelancer's signature makes an engagement active: a person records it, never the
        # credential that read the letter's text (collaborators design § 6).
        "activate_engagement",
        # Writing a letter or a framework agreement as a document: a person reviews the text first,
        # and the agent's way to a legal document is `send_engagement_for_signature` with
        # `full_access` (REB-1019). Previews are reads and sit in the allowed set above.
        "create_engagement_letter",
        "create_framework_agreement",
        # A signature no signing site witnessed (a letter signed by hand, its PDF uploaded) is said
        # by a person: an agent that read the letter's text must not be able to record that it was
        # signed (collaborators design § 2).
        "upload_signed_document",
        # A month's «Rendiconto» is recorded as received and approved by a person: the client's
        # agreement relayed, or its objection, and the tacit date passing, never an agent's act
        # (collaborators design § 4, frame A4). Approval is what makes a day payable.
        "record_monthly_report",
        "approve_monthly_report",
        # The freelancer's own card on their contract: what rebase's space says of their
        # engagement, read across spaces. A person's read: it is rebase's data in another
        # space, and no tool exists for it (brief 2026-11-10, REB-1035).
        "read_rebase_engagement",
        # Connections to third parties, and mail that leaves in the owner's name.
        "connect_gmail",
        "disconnect_gmail",
        "update_gmail_settings",
        # The model PigroCRM reads documents with, its key and its spend cap (REB-972, design
        # 2026-11-09 § 3.2): an administrator's act, like every credential.
        "read_ai_model_settings",
        "update_ai_model_settings",
        "test_ai_model",
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
        # An IMAP or PEC mailbox and its password (REB-1015): a credential to a third-party
        # account, typed by a person.
        "connect_mailbox",
        "change_mailbox_password",
        "disconnect_mailbox",
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
        # The places of business of a legal entity beside its registered office, which split the
        # IRAP base between regions (REB-1092): a tax fact of the company, a person's to state.
        "update_legal_entity_sites",
        # The day net pay leaves, for every staff member of a legal entity without an
        # override of their own (staff design § 4: its tools are the four of `FULL_ACCESS`).
        "set_payroll_pay_day",
        # The figures of an accepted payslip: accepting is a person's act (frame § A4), so a
        # person types them (REB-1008); the inbox's proposal is the other way in (REB-1045).
        "record_staff_payslip",
        # Accepting what the inbox read of a payslip (REB-1045): the same act as typing its
        # figures, so the same door. `accept_proposal` is person-only already; this is the
        # staff service's own check on the way in.
        "accept_payslip_proposal",
        # Correcting one already recorded (REB-1030): the same act, so the same door.
        "correct_staff_payslip",
        # What an hour costs, from what a person costs, and its adoption as the linked
        # user's hourly cost (REB-1009): a person reads it on the staff page and a person
        # confirms it. Adoption writes through `update_user_rates`, which stays `full_access`.
        "propose_staff_hourly_cost",
        "adopt_staff_hourly_cost",
        # Reads of the configuration's own history, and the register's PDF (it archives a
        # document).
        "read_user_timeline",
        "read_field_definition_timeline",
        "read_pipeline_stage_timeline",
        "read_slack_timeline",
        "read_reminder_defaults_timeline",
        "render_contract_register",
        # What happens to an export once it is handed over (REB-1047, accountant export § 5): who
        # holds a link and when it was opened is read and ended by a person, and writing a copy
        # to the space's Drive is a person's act. The tool that makes a link is `full_access`'s.
        "list_export_share_links",
        "revoke_export_share_link",
        "write_export_to_drive",
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
    "connect_mailbox": "collegare una casella email",
    "change_mailbox_password": "cambiare la password di una casella email",
    "disconnect_mailbox": "scollegare una casella email",
    "convert_deal_to_contract": "trasformare un deal in contratto",
    "set_space_profile": "cambiare il profilo dello spazio",
    "set_space_modules": "accendere o spegnere i moduli dello spazio",
    "upload_inbox_file": "caricare un documento in arrivo",
    "classify_inbox_item": "leggere un documento in arrivo",
    "set_inbox_item_kind": "dire che cos'e' un documento in arrivo",
    "discard_inbox_item": "scartare un documento in arrivo",
    "mark_inbox_item_duplicate": "segnare un documento in arrivo come doppione",
    "read_ai_model_settings": "leggere le impostazioni del modello",
    "update_ai_model_settings": "cambiare le impostazioni del modello",
    "test_ai_model": "provare il modello",
    "settle_outgoing_due_item": "segnare come pagata una scadenza in uscita o fiscale",
    "reverse_outgoing_settlement": "annullare il pagamento di una scadenza in uscita o fiscale",
    "read_fiscal_due_items": "leggere le scadenze di imposte, IVA, ritenute e stipendi",
    "read_cash_schedule": "leggere lo scadenziario di cassa",
    "read_bank_accounts": "leggere i conti bancari e i loro saldi",
    "share_organization": "condividere un'anagrafica fra le aziende",
    "merge_organizations": "unire due anagrafiche della stessa azienda",
    "list_duplicate_organizations": "elencare le anagrafiche con la stessa partita IVA",
    "read_engagements": "leggere gli incarichi con i freelance",
    "update_engagement": "modificare un incarico con un freelance",
    "get_freelancer_availability": "vedere quando un freelance e' libero",
    "create_engagement": "creare un incarico con un freelance",
    "cancel_engagement": "annullare un incarico con un freelance",
    "close_engagement": "chiudere un incarico con un freelance",
    "send_engagement_for_signature": "mandare un incarico in firma",
    "resend_signing_mail": "rimandare la mail con il link per firmare",
    "refresh_document_signature": "aggiornare lo stato della firma di un documento",
    "cancel_framework_agreement": "annullare un contratto quadro non ancora firmato",
    "record_framework_notice": "registrare la disdetta di un contratto quadro",
    "upload_signed_document": "caricare il PDF firmato a mano di un documento",
    "activate_engagement": "registrare la firma di un incarico",
    "read_engagement_report": "leggere i giorni di un incarico e quali si pagano",
    "record_monthly_report": "registrare l'arrivo del rendiconto mensile di un incarico",
    "approve_monthly_report": "approvare il rendiconto mensile di un incarico",
    "read_rebase_engagement": "leggere lo stato del tuo incarico con rebase",
    "set_freelancer_tax_data": "salvare i dati fiscali di un freelance",
    "set_organization_hub_freelancer": "collegare un'anagrafica a un freelance dell'hub",
    "skip_space_onboarding": "rimandare le domande su come lavora lo spazio",
    "create_staff_member": "registrare una persona dell'azienda con il suo compenso",
    "update_staff_member": "modificare una persona dell'azienda o il suo compenso",
    "get_staff_member": "leggere il compenso di una persona dell'azienda",
    "build_accountant_export": "preparare l'esportazione per il commercialista",
    "get_export_download_url": "scaricare l'esportazione per il commercialista",
    "get_export_checklist": "vedere se il periodo è pronto per l'esportazione",
    "create_export_share_link": "creare un link per condividere l'esportazione",
    "list_export_share_links": "vedere i link di condivisione delle esportazioni",
    "revoke_export_share_link": "revocare un link di condivisione",
    "write_export_to_drive": "scrivere l'esportazione su Google Drive",
    "list_staff": "leggere l'elenco delle persone dell'azienda e i loro compensi",
    "set_declared_tax_figure": "registrare le cifre della dichiarazione dei redditi",
    "set_tax_loss": "registrare una perdita fiscale da riportare agli anni dopo",
    "delete_tax_loss": "togliere una perdita fiscale registrata",
    "set_tax_advance_method": "scegliere come calcolare gli acconti d'imposta di un anno",
    "confirm_tax_carry": "confermare la differenza riportata su un'imposta",
    "set_tax_instalment_plan": "rateizzare il saldo e il primo acconto delle imposte di un anno",
    "create_tax_notice_plan": "registrare il piano di rate di un avviso dell'erario",
    "update_tax_notice_plan": "cambiare il piano di rate di un avviso dell'erario",
    "delete_tax_notice_plan": "eliminare il piano di rate di un avviso dell'erario",
    "list_tax_plans": "leggere le rateazioni delle imposte e dei piani degli avvisi",
    "group_f24": "raggruppare le scadenze di un giorno in un F24",
    "ungroup_f24": "sciogliere un F24",
    "list_f24_groups": "leggere gli F24 raggruppati",
    "attach_f24_receipt": "collegare la ricevuta di un F24",
    "detach_f24_receipt": "scollegare la ricevuta di un F24",
    "file_f24": "segnare come presentato un F24 senza importo da pagare",
    "reopen_f24": "riaprire un F24 segnato come presentato",
    "create_recurring_expense": "registrare una spesa ricorrente",
    "stop_recurring_expense": "terminare una spesa ricorrente",
    "settle_iva_period": "liquidare l'IVA di un periodo",
    "list_iva_settlements": "leggere le liquidazioni IVA",
    "list_iva_periods": "leggere i periodi IVA con la loro liquidazione",
    "get_iva_register": "leggere il registro IVA vendite o acquisti di un periodo",
    "close_iva_period": "chiudere un periodo IVA",
    "reopen_iva_period": "riaprire un periodo IVA",
    "set_payroll_pay_day": "impostare il giorno di paga dell'azienda",
    "update_legal_entity_sites": "modificare le sedi dell'azienda",
    "record_staff_payslip": "registrare le cifre di un cedolino",
    "correct_staff_payslip": "correggere il cedolino di una persona",
    "propose_staff_hourly_cost": "proporre il costo orario di una persona",
    "adopt_staff_hourly_cost": "adottare il costo orario di una persona",
    "get_payroll_month": "leggere la stima del mese per le persone dell'azienda",
    "close_payroll_month": "chiudere un mese di paga",
    "accept_payslip_proposal": "accettare un cedolino letto da «In arrivo»",
    "list_bank_accounts": "leggere i conti bancari",
    "get_bank_account_coverage": "leggere gli estratti conto che mancano a un conto",
    "list_statements": "leggere gli estratti conto di un conto",
    "list_bank_movements": "leggere i movimenti di un conto",
    "get_bank_movement": "leggere un movimento con le scadenze che potrebbe pagare",
    "propose_movement_match": "proporre a quali scadenze corrisponde un movimento",
    "confirm_movement_match": "confermare l'abbinamento di un movimento a delle scadenze",
    "undo_movement_match": "annullare l'abbinamento di un movimento",
    "create_bank_account": "aggiungere un conto bancario",
    "update_bank_account": "modificare un conto bancario",
    "archive_bank_account": "archiviare un conto bancario",
    "import_statement": "caricare un estratto conto",
    "revert_statement": "annullare l'importazione di un estratto conto",
    "bill_contract_fee": "fatturare il canone di un contratto",
    "submit_expense_note": "inviare una nota spese",
    "approve_expense_note": "approvare una nota spese",
    "reject_expense_note": "rifiutare una nota spese",
    "reimburse_expense_note": "rimborsare una nota spese",
    "read_all_expense_notes": "leggere le note spese di tutti",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)
