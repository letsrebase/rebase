"""An agent must not be able to issue a fiscal document, nor touch the handful of
slice-4 operations that are closer to configuration or to rewriting the past than to
recording an entity.

The guarantee is *structural*: the tool is not registered. It is deliberately not a
permission check, because a personal access token inherits its owner's full role and
never expires (residuo R10) -- so a check inside a registered tool is a check an
administrator's token passes, and "give a token to Claude" would mean "let Claude issue
invoices in your name", or recalculate what a quarter's work was worth.

A comment saying so protects nothing. These tests read every module under `tools/` and
fail if any forbidden operation ever appears as a tool, or is called under another name,
so the guarantee survives someone adding one without reading the comment. Slice 4 and
slice 6 extend the same two lists rather than writing a parallel mechanism -- which is
why `FORBIDDEN`/`FORBIDDEN_SERVICE_CALLS` are module-level constants with one name per
line rather than inline literals, and why the source scan below covers every file in
`tools/`, not only `tools/__init__.py`: slice 4's tool call-throughs live in their own
module (`tools/timetracking.py`, mirroring `tools/invoices.py`), and a registered tool
that called a same-named wrapper in that module which itself called a forbidden service
method would leave no trace in `tools/__init__.py` alone.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "pigrocrm_mcp" / "tools"

# The modules the scans below skip, and the only ones allowed to reach a forbidden
# operation. Their whole existence is conditional: `server.py` imports and registers each
# only when `Settings.mcp_full_access` is true, so on a default installation their calls
# are as unreachable as if the files were not there. The scans therefore ask a narrower
# and truer question than "does this call appear anywhere" -- they ask whether it appears
# anywhere that runs unconditionally.
#
# Two files and not one since slice 9C. `drive_privileged.py` holds the three Drive reads
# and needs a *second* condition on top of the switch -- a configured Google client, since
# there is no credential to read Drive with otherwise -- and expressing that as a separate
# module makes the condition visible in `server.py`'s import instead of as an `if` nested
# inside a `register` that anybody could widen. The exemption stays enumerated here, in
# one tuple, rather than becoming "any file whose name contains privileged".
#
# `test_the_privileged_module_is_the_only_place_they_appear` closes the obvious hole in
# that exemption: a third file quietly added to the skip list, or either of these two
# imported outside the guard, all fail there.
PRIVILEGED_MODULES = (TOOLS_DIR / "privileged.py", TOOLS_DIR / "drive_privileged.py")

# The banned operations this package does have a tool for -- the count is deliberately
# not written down here, because it has already gone stale twice: the fiscal ones, which
# turn a draft into a fiscal fact or change what one says after the fact, then the ones
# closer to configuration or to rewriting the past than to recording an entity, then the
# groups the comments below introduce. Each is reachable over REST by a
# human with the right role; none is reachable by an agent at all. `bind_time_to_invoice`
# and `get_fiscal_estimate` belong to `AnalyticsService`, which does not exist until plan
# 4B -- declared here regardless, because the ban is a decision made now and plan 4B must
# not have to touch this list to honour it.
#
# Two of them were added after an audit found the repo stating this policy twice
# and the two statements disagreeing:
#
#   * `update_fiscal_profile` was one of the *four* names slice 3 §11 actually banned
#     (`issue_invoice`, `annul_invoice`, `mark_transmitted_externally`,
#     `update_fiscal_profile`); this list had dropped it and put `export_invoice_xml` --
#     a real, correct addition -- in its place, while `tools/invoices.py` went on
#     declaring it forbidden in a table nothing imported. The audit restored it. It left
#     again with ORB-188 (2026-09-12), this time on purpose and in both halves at once
#     (`FULL_ACCESS_ACTIONS` and here): the profile is one rewritable row, an
#     issued invoice keeps its own copy of it, and in a space born empty setting it is
#     the first thing a person asks their assistant. It is on the default surface with
#     the emitter's write, admin-only through the service.
#   * `unarchive_cost_category` is `archive_cost_category` in the other direction, and
#     the same decision about which categories the CRM offers. Its absence from slice 4
#     §11's list of ten is an omission rather than a distinction -- there is no reading
#     under which creating, renaming and archiving a category are configuration and
#     bringing one back is not.
FORBIDDEN = (
    "issue_invoice",
    "annul_invoice",
    "mark_invoice_transmitted",
    "export_invoice_xml",
    # The accountant's export (REB-1012): every fiscal figure of an entity, stored. The tools
    # answer a download path or a link (REB-1047), never bytes.
    "build_accountant_export",
    "get_export_download_url",
    "get_export_checklist",
    "create_export_share_link",
    "recalculate_rates",
    "update_user_rates",
    "update_deal_rate",
    "create_cost_category",
    "update_cost_category",
    "archive_cost_category",
    "unarchive_cost_category",
    "close_period",
    "reopen_period",
    "bind_time_to_invoice",
    "get_fiscal_estimate",
    "get_tax_calendar",
    "list_iva_settlements",
    "list_iva_periods",
    "get_iva_register",
    # Closing and reopening an IVA period (REB-1036), as `close_period` is.
    "close_iva_period",
    "reopen_iva_period",
    # The accountant's declared tax figures (REB-1006): an administrator's token with
    # `full_access` and no other.
    "set_declared_tax_figure",
    # The advance method of a tax year (REB-1074): it moves the tax due items, so the same.
    "set_tax_advance_method",
    # The instalment plan of the June payments and the plans of a tax office notice (REB-1075),
    # which move the tax due items, and the read of them: the same.
    "set_tax_instalment_plan",
    "create_tax_notice_plan",
    "update_tax_notice_plan",
    "delete_tax_notice_plan",
    "list_tax_plans",
    # F24 groups (REB-1076): the fiscal position and the shape of a payment, so the same.
    "group_f24",
    "ungroup_f24",
    "list_f24_groups",
    # What the whole team sees (spec 2026-11-09, B1): an administrator's token with
    # `full_access`, and no other.
    "set_space_profile",
    "set_space_modules",
    "skip_space_onboarding",
    # Folding two organizations into one (REB-995): an administrator's token with `full_access`.
    "merge_organizations",
    # The draft that bills one occurrence of a contract's recurring fee (REB-1032): the bridge from
    # a contract to an invoice line, as `bind_time_to_invoice`, so a `full_access` token's.
    "bill_contract_fee",
    # Staff and directors with their pay (spec 2026-11-09, staff, payroll and expenses, § 4):
    # the most personal data the product holds, an administrator's token with `full_access`
    # and no other. The pay day of a legal entity has no tool and is person-only.
    "create_staff_member",
    "update_staff_member",
    "get_staff_member",
    "list_staff",
    "get_payroll_month",
    # Deciding on an expense note and paying it back (REB-1046): an administrator's token with
    # `full_access`, and never on its own note.
    "approve_expense_note",
    "reject_expense_note",
    "reimburse_expense_note",
    "close_payroll_month",
    # The first that is not fiscal: asking Gmail who at a customer's
    # domain the owner has corresponded with. Not irreversible -- it stores nothing --
    # but it spends the owner's Gmail quota under the owner's OAuth consent, which is
    # the exact reason `sync` and `backfill` are refused to agents outright. It is on
    # this list rather than on `FORBIDDEN_GMAIL` because, unlike those two, the
    # installation *can* opt in: the same switch that hands an agent the fiscal acts.
    "discover_gmail_correspondents",
    # The organizations the connected mailbox proposes (REB-223): discovery's question over a
    # year of sent mail, so the same quota, the same consent and the same switch.
    "suggest_organizations_from_gmail",
    # Not fiscal either: the text of an attachment of an archived mail. The CRM keeps an
    # attachment's name, type and size and never its bytes (spec 5.4), so the tool fetches
    # it from Google at call time -- the owner's quota and consent, content written by an
    # outside sender -- and stores nothing. Same door as `discover_gmail_correspondents`,
    # for the same reason.
    "read_gmail_attachment",
    # Fiscal again: writing a numbered, issued
    # row straight into the register (slice 9 §3), and declaring the numbers it will
    # never carry (slice 9 §3.2).
    "import_issued_invoice",
    "declare_invoice_register_gaps",
    # REB-365: reads a document's own stored bytes back and reports what would happen
    # to it, gated the same way even though it writes nothing itself -- it exposes the
    # same incoming-supplier and register-conflict facts as the two writes above.
    "review_invoice_import",
    # REB-366: converges onto `import_issued`'s own write for the fatture that
    # classify `"ready"` -- the actual register write the review step above only
    # reports, gated the same way as `import_issued_invoice` itself.
    "confirm_invoice_import",
    # None of these three fiscal: reading
    # the owner's Google Drive (slice 9 §4.2). Like `discover_gmail_correspondents`
    # they spend the owner's quota under the owner's OAuth consent, and unlike
    # every other entry here what they *return* is the content of a personal Drive --
    # where the folder of another job, the rent contract and the photos of somebody's
    # children live next to the client's contract. The confinement to
    # `root_folder_ids` (see `core/drive/reader.py`) is what makes them offerable at
    # all; this list is what says the installation has to ask for them.
    "list_drive_files",
    "read_drive_file",
    "import_drive_file",
    # REB-976: engagements with freelancers (collaborators design § 6). Creating one draws a
    # letter number a cancellation never gives back, cancelling or closing one ends a legal
    # document, and the tax data is personal data printed on contracts. They also need the
    # `collaborators` module on, which `TOOL_MODULES` prunes.
    "create_engagement",
    "cancel_engagement",
    "close_engagement",
    "set_freelancer_tax_data",
    # REB-1025: the bank accounts of the space, their statements and movements (cash design):
    # the company's position, an administrator's token with `full_access` and no other. The
    # `cash` module has to be on as well, which `TOOL_MODULES` prunes.
    "list_bank_accounts",
    "create_bank_account",
    "update_bank_account",
    "archive_bank_account",
    "list_statements",
    "list_bank_movements",
    # REB-1042: reading a movement with its ranked candidates and proposing a match; confirming
    # and undoing are person-only and have no tool at all.
    "get_bank_movement",
    "propose_movement_match",
    # REB-1019: sending a document for signature mails the freelancer a link to a legal document,
    # and the other three act on one already sent: resending its mail, cancelling a framework
    # agreement (its envelope too) and recording a notice that ends it. Their tools live in
    # `privileged.py`; `refresh_document_signature` only reads Documenso's state and is a default
    # tool of the `collaborators` module.
    "send_engagement_for_signature",
    "resend_signing_mail",
    "cancel_framework_agreement",
    "record_framework_notice",
)

# The other half of `FULL_ACCESS_ACTIONS`: operations that are banned to an agent
# credential and have **no MCP tool at all** -- not "not registered by default", but no
# tool anywhere in `apps/mcp`, now or planned.
#
# They exist because the ban list is keyed on the operation rather than on a transport,
# so it can hold an operation this package never offers. The first three are Drive
# *configuration* (spec 9 §5.2): naming the root folders the reader may see and the
# folder the document storage writes into (`account.py`'s `_ROOTS_ACTION`), and
# connecting or disconnecting the grant itself (`oauth.py`'s
# `_CONNECT_ACTION`/`_DISCONNECT_ACTION`). A settings panel is the only caller; there is
# nothing here for an agent to be handed even with the switch on.
#
# The reason they are banned anyway is the reason this file's own subject exists: a
# personal access token is accepted on every REST route, and `PATCH
# /api/drive/account/roots` is a REST route. Without the ban an agent token could
# repoint the roots at any folder of the owner's Drive -- making every confinement
# `core/drive/reader.py` enforces true of a folder list the agent chose -- or disconnect
# the account and take the CRM's own document store offline. `apps/api/tests/
# test_drive_api.py` pins all three at the route.
#
# Enumerated here, and this is why the equality above is a union rather than a plain
# `==`: the two lists still have to be one list, but "in `FULL_ACCESS_ACTIONS` and
# not in `FORBIDDEN`" has only the admissible answers below, written down with their
# justification instead of left as slack in the assertion. One added to the ban list
# without a tool -- or one of these growing a tool and staying here -- fails. The day
# ledger's writes (REB-745, REB-749) are a person's acts, reached by the web app over
# REST, which an agent token could reach too without the ban.
#
# Spelled as the action keys the services pass to `require_write`: the key *is* the
# identity of the operation for `_refuse_if_agent`, so a copy re-spelled here would be a
# ban that silently stopped matching.
REST_ONLY_FORBIDDEN = frozenset(
    {
        "set_drive_roots",
        # REB-1079: confirming the difference an advance already paid left on a tax item. It
        # has no tool: an administrator confirms it from «Tasse», over REST, which a
        # `full_access` token could too.
        "confirm_tax_carry",
        "set_drive_storage_folder",
        "connect_drive",
        "disconnect_drive",
        # REB-745..747: the day ledger's writes. They have no tool and never will
        # (`test_mcp_surface_coverage.py`, `_COPERTE_O_UMANE`): an agent proposes, a person
        # confirms, and the web app reaches these over REST, which a token could too.
        "create_work_unit",
        "transition_work_unit",
        "create_approval",
        # REB-749: the request for approval, prepared from the contract page.
        "request_approval",
        # REB-751: a draft invoice from a contract's days. The rung `bind_time_to_invoice`
        # sits on, for days, and like it closed to an agent; unlike it, with no tool at all,
        # since an agent has no business choosing what an issue will freeze.
        "bill_contract_days",
        # REB-753: the wording a contract's emails go out with, and the draft that attaches
        # the invoice and the register. The MCP reads templates and previews; writing them
        # and preparing the draft are the person's, as the send they lead to is.
        "create_contract_email_template",
        "update_contract_email_template",
        "prepare_contract_email",
        # REB-971: the inbox's upload. Bytes never travel over MCP (design 2026-11-09, § 6), so
        # there is no tool; a token with `full_access` reaches it over REST for a script that
        # feeds the inbox, and a default token does not.
        "upload_inbox_file",
        # REB-1021: the IVA settlement of a period and the due item it writes; the tool arrives
        # with the IVA page's card, and until then only a service call reaches it.
        "settle_iva_period",
        # REB-972: reading an item spends the space's model; an agent proposes with
        # `propose_inbox_item` and reads nothing itself, and a full-access token may over REST.
        "classify_inbox_item",
        # REB-1025: a statement file and the undoing of its import, and the coverage of an
        # account's months. Bytes never travel over MCP, so there is no tool for the first two;
        # a token with `full_access` reaches all three over REST, a default token none.
        "import_statement",
        "revert_statement",
        "get_bank_account_coverage",
    }
)

# The third term: `full_access` operations that **do** have a tool, and a tool every token is
# shown. What a due item's settlement needs depends on the item, which only the service knows
# (spec 2026-11-09, due items, § 5): a client's payment is any writing token's, money going
# out and every tax, IVA, withholding or payroll item is a `full_access` administrator's. So
# `settle_due_item`, `reverse_settlement`, `list_due_items` and `get_due_item` are registered
# by default and the service refuses an outgoing or fiscal item to a token without the flag
# (`apps/mcp/tests/test_due_items_tools.py`, `apps/api/tests/test_due_items_api.py`).
# Hiding the tool would hide the incoming half too. The equality below still holds: these
# are named here, with the tools that reach them, and `test_a_per_record_full_access_action
# _is_reached_by_a_default_tool` asserts each tool is on the default surface.
FULL_ACCESS_BY_THE_RECORD: dict[str, tuple[str, ...]] = {
    "settle_outgoing_due_item": ("settle_due_item",),
    "reverse_outgoing_settlement": ("reverse_settlement",),
    "read_fiscal_due_items": ("list_due_items", "get_due_item", "get_cash_schedule"),
    # REB-1025: the schedule is every token's; its balances, accounts and alerts are a
    # `full_access` administrator's, which the service decides.
    "read_bank_accounts": ("get_cash_schedule",),
    # REB-970: `update_organization` with a null `legal_entity_id` shares the organization across
    # the legal entities, which shows a company to people who could not see it: an
    # administrator's token with `full_access`, nothing less, as in the invoice import.
    "share_organization": ("update_organization",),
    # REB-976: the hub's id of a freelancer is their identity, set through the same tool by an
    # administrator's token with `full_access`.
    "set_organization_hub_freelancer": ("update_organization",),
    # REB-1046: the expense notes are every token's own, and an administrator's token with
    # `full_access` lists every note in scope: the service decides, so the tool stays on the
    # default surface.
    "read_all_expense_notes": ("list_expense_notes",),
}

# The tools above that exist only on an installation where Google is configured as well:
# on one without it there is no mailbox to ask and no credential to read Drive with, so
# the switch alone does not make them appear.
# `test_they_are_registered_exactly_when_the_installation_opted_in` accounts for them
# by building both kinds of installation.
FORBIDDEN_NEEDING_GMAIL = frozenset(
    {
        "discover_gmail_correspondents",
        "suggest_organizations_from_gmail",
        "read_gmail_attachment",
        "list_drive_files",
        "read_drive_file",
        "import_drive_file",
    }
)

# The service methods behind them. Listed separately because a future tool could call one
# under an innocuous name -- `finalise_invoice` registering a tool that calls `issue`
# would pass a name check and defeat the point. A ban a bare name cannot express goes
# in `FORBIDDEN_QUALIFIED_CALLS` below instead.
FORBIDDEN_SERVICE_CALLS = (
    "issue_and_render",
    "annul",
    "mark_transmitted_externally",
    "export_xml",
    # `AccountantExportService`'s three and `ExportShareService`'s one, named uniquely here.
    "build_accountant_export",
    "get_export_download_url",
    "get_export_checklist",
    "create_export_share_link",
    "recalculate_rates",
    "update_user_rates",
    "update_deal_rate",
    "create_cost_category",
    "update_cost_category",
    "archive_cost_category",
    "unarchive_cost_category",
    "close_period",
    "reopen_period",
    # `IvaPeriodLockService`'s two (REB-1036), behind the tools of the same name.
    "close_iva_period",
    "reopen_iva_period",
    "bind_to_invoice",
    "get_fiscal_estimate",
    "get_tax_calendar",
    "list_iva_settlements",
    # `DeclaredTaxFigureService.set_declared_tax_figure` (REB-1006), behind the tool of the
    # same name.
    "set_declared_tax_figure",
    # `TaxAdvanceMethodService.set_tax_advance_method` (REB-1074), behind the tool of the same
    # name.
    "set_tax_advance_method",
    # `TaxInstalmentPlanService` and `TaxNoticePlanService` (REB-1075), behind the tools of the
    # same names.
    "set_tax_instalment_plan",
    "create_tax_notice_plan",
    "update_tax_notice_plan",
    "delete_tax_notice_plan",
    "list_tax_plans",
    # `F24GroupService`'s three behind a tool (REB-1076), named uniquely for this scan.
    "group_f24",
    "ungroup_f24",
    "list_f24_groups",
    # `StaffService`'s four, named uniquely for this scan: a bare `get` or `list` would match
    # every other service.
    "create_staff_member",
    "update_staff_member",
    "get_staff_member",
    "list_staff",
    "get_payroll_month",
    # `ExpenseNoteService`'s three decisions, behind the tools of the same name (REB-1046).
    "approve_expense_note",
    "reject_expense_note",
    "reimburse_expense_note",
    "close_payroll_month",
    # `BankAccountService`'s four and the two reads of `BankStatementReadService`, named uniquely
    # for this scan (REB-1025).
    "list_bank_accounts",
    "create_bank_account",
    "update_bank_account",
    "archive_bank_account",
    "list_statements",
    "list_movements",
    # REB-1042: `MovementMatchService.get_movement` and `.propose`, behind `get_bank_movement` and
    # `propose_movement_match`; `confirm` and `undo` have no tool and are person-only.
    "get_movement",
    "propose_match",
    "discover",
    # `GmailSyncService.suggest_organizations`, behind `suggest_organizations_from_gmail`
    # (REB-223): the same family as `discover`.
    "suggest_organizations",
    # `GmailAttachmentService.attachment_text`: fetches from Gmail, at call time, the file
    # that sync never saved (spec 5.4 keeps name, type and size and nothing else). Same
    # family as `discover` -- the owner's quota and consent, bytes written by an outside
    # sender -- so the same door: it exists only where the installation opened
    # `mcp_full_access`. The name is unique in this code, and on purpose: see
    # `documents/service.py::extract_text`.
    "attachment_text",
    "import_issued",
    "declare_gaps",
    "review_import",
    "confirm_import",
    # `DriveReader`'s three reads and the two calls the Drive tools make around them
    # (slice 9 §4.2). Not a `*Service` receiver, which is why they are here as bare
    # names and why `test_mcp_surface_coverage.py` cannot carry them in `_VIETATE`
    # (its taxonomy sweeps classes whose name ends in `Service`, and `DriveReader` is
    # not one) -- see `_OUTSIDE_FROM_SETACCIO` there, which records the reason for each.
    #
    # Each name is unique in this codebase, which is what makes the substring scan the
    # right instrument: no service has a `list_children`, a `read_text` or a
    # `describe_roots`, and `import_bytes` belongs to `DocumentService` alone.
    #
    # `DriveReader.describe` is deliberately **absent**, and its absence is the `upsert`
    # problem in a form this file's two instruments cannot solve: `TemplateService.
    # describe` and `FiscalProfileService.describe` are both on the MCP surface, so a
    # bare-name ban would fail the build with a message about Drive, and the qualified
    # scan cannot help either -- `_receivers_of` recognises a service by the `Service`
    # suffix, and `DriveReader` has none. What covers it instead is stronger than a name:
    # `test_no_unconditional_module_can_even_obtain_a_drive_reader` bans the only
    # constructor, so an unconditional module has nothing to call `describe` *on*.
    "list_children",
    "read_text",
    "read_bytes",
    "describe_roots",
    "import_bytes",
)

# The bans a bare method name cannot express, because the name is not the operation.
# Empty since ORB-188, and kept with its instrument (`_receivers_of`) because the lesson
# that filled it still holds: its one entry was `("FiscalProfileService", "upsert")`,
# and `upsert` is exactly the kind of name a second service carries too --
# `LegalEntityService.upsert` -- so putting "upsert" in the tuple above would have
# banned the substring, and any call spelled that way, on any service, would have failed
# this file with a message about the fiscal profile. A ban on a name is not a ban on an
# operation, and the bare names above are safe only because each happens to be unique.
# The next qualified ban goes here as a `(service, method)` pair and is resolved to its
# receiver; the tests below iterate the tuple so that an empty one asserts nothing
# rather than parametrising into a skip.
# `InvoiceService.issue` is the bare half of `issue_and_render`, the one name a tool calls: a
# tool reaching `issue` directly would skip the render step, so the pair is banned too.
FORBIDDEN_QUALIFIED_CALLS: tuple[tuple[str, str], ...] = (
    ("InvoiceService", "issue"),
    # REB-1024: the IVA registers are the legal entity's fiscal position, so only a `full_access`
    # token reads them, from `tools/privileged.py`. `list_periods` and the two registers are
    # names other services may use, which is why they are qualified here.
    ("IvaRegisterService", "list_periods"),
    ("IvaRegisterService", "read_sales_register"),
    ("IvaRegisterService", "read_purchase_register"),
    # REB-1032: the draft of a contract's recurring fee, a `full_access` token's from
    # `tools/privileged.py`. `bill` is a name other services may use, which is why it is qualified.
    ("FeeBillingService", "bill"),
)

_REASON = (
    "e' un'operazione esclusa dalla superficie MCP per costruzione (slice 3 §11 per "
    "gli atti fiscali, slice 4 §11 per le tariffe, le "
    "categorie di costo e le chiusure di periodo), non per controllo di permessi: un "
    "PAT eredita il ruolo pieno del proprietario e non scade (residuo R10), quindi "
    "l'assenza del tool e' l'unico meccanismo che regge."
)


# Gmail (slice 5B). Tool names, not service methods, for two different reasons.
#
# The first two have no method behind them yet: sending arrives in 5B-2, and the ban is a
# decision taken now -- like `bind_time_to_invoice`, declared here before `AnalyticsService`
# existed. An email sent from your mailbox cannot be recalled and the customer reads it as
# your words; and an agent that holds the *reading* of mail and the *sending* on the same
# belt has the injection source and the exfiltration channel on one channel. The body of an
# email that reaches an agent is text written by somebody else: what keeps that from being
# a hole is that there is nothing to send anything out with. The person presses Send.
#
# The other four have a method behind them, already excluded in
# `test_mcp_surface_coverage.py`, and are repeated here as *names* because the two lists
# answer different questions: that one is "is this method reachable", this one is "does
# this tool exist". A tool called `sync_gmail` that called something else would pass the
# first and fail this one.
#
# There is no `FORBIDDEN_GMAIL_SERVICE_CALLS`: the matching methods are already covered by
# the other file's ban by construction, and adding them here would break the equality
# between `_VIETATE` and these tuples that
# `test_the_forbidden_block_names_exactly_what_the_ban_test_bans` checks -- two lists that
# repeat each other diverge, which is exactly the defect that test exists to catch.
FORBIDDEN_GMAIL = (
    "send_email",
    "send_payment_reminder",
    "sync_gmail",
    "backfill_gmail",
    "connect_google_account",
    "disconnect_google_account",
    "set_gmail_settings",
    "search_gmail",
)

# The substring no tool name may contain. Stronger than the list above, which only knows the
# spellings somebody thought of: the guarantee is that no tool sends, not that four
# particular names are absent.
FORBIDDEN_TOOL_NAME_FRAGMENTS = ("send", "invia")

_REASON_GMAIL = (
    "e' escluso dalla superficie MCP di slice 5B per costruzione. La riga non e' "
    "lettura contro scrittura ma di chi e' la risorsa e di chi la decisione: l'agente "
    "legge la copia gia' archiviata in `gmail_messages` e lo stato della credenziale, "
    "mentre andare a prendere la posta (quota e consenso della persona), collegare o "
    "scollegare una casella, decidere se il CRM conserva i corpi, e inviare, "
    "appartengono alla persona. Come per gli atti fiscali, non e' un controllo di "
    "permessi: un PAT eredita il ruolo pieno del proprietario e non scade (residuo "
    "R10), quindi l'assenza del tool e' l'unico meccanismo che regge."
)


def _unconditional_paths(base: Path) -> list[Path]:
    """Every `.py` file under `base` that runs on a default installation, `prompts/`
    included when `base` is the real `tools/`.

    One helper for the three scans below (`_modules`, `_registered_tool_names`,
    `_tools_source`) so that widening the sweep is one edit rather than three -- three
    was how `prompts/` came to be missing from two of them while `server.py` registered
    it unconditionally.

    The `base == TOOLS_DIR` guard is for the scans' own tests, which point `base` at a
    `tmp_path`: a temporary directory has no `prompts/` sibling to find.
    """
    paths = list(base.rglob("*.py"))
    if base == TOOLS_DIR:
        paths += list((TOOLS_DIR.parent / "prompts").rglob("*.py"))
    return sorted(path for path in paths if path not in PRIVILEGED_MODULES)


def _modules(base: Path = TOOLS_DIR) -> list[ast.Module]:
    return [
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in _unconditional_paths(base)
    ]


def _registered_tool_names(base: Path = TOOLS_DIR) -> set[str]:
    """Every function carrying an `@mcp.tool()` decorator, at any nesting depth, in any
    module under `tools/`.

    Walks the whole tree rather than the top level: the registrations live inside
    `build_server`'s body and inside each `register(...)` function, so a top-level-only
    scan would report nothing and pass vacuously -- which is the failure mode this file
    exists to avoid in the code it guards.

    It used to be scoped to `tools/__init__.py`, on the true-at-the-time observation
    that every `@mcp.tool()` decorator lived there. Slice 5B's `tools/gmail.py` carries
    its own, and that is exactly the shape a future forbidden tool would take: a new
    domain module registering its own tools, invisible to a scan of one file. A ban that
    only reads the file the last author happened to use is not a ban.
    """
    names: set[str] = set()
    for module in _modules(base):
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    names.add(node.name)
    return names


def _tools_source(base: Path = TOOLS_DIR) -> str:
    """Every `.py` file under `tools/` **and under `prompts/`**, concatenated.

    Whole-package, like `_registered_tool_names` above and for a second reason of its
    own: the actual call into a service lives one file over, in the
    domain-specific module a registered tool calls through (`tools/invoices.py`,
    `tools/timetracking.py`, ...). A forbidden method reached only from there --
    directly, or through a same-named wrapper function called under a different tool
    name -- must fail exactly as if it were called inline in `tools/__init__.py`
    itself; scanning the whole package is what makes that true regardless of which
    file the call physically sits in.

    `prompts/` is in for the same reason and was missing: `server.py` calls
    `register_prompts` **unconditionally**, so §10's four prompts are as reachable on a
    default installation as any tool in `tools/__init__.py`, and every ban below read
    right past them. A prompt is a function with a `context` -- it can call
    `InvoiceService.issue` exactly as a tool can -- and «un prompt» is a plausible place
    for somebody to put a convenience that ends in a fiscal write, precisely because it
    does not look like a tool.

    Which files those are is `_unconditional_paths`' answer, shared with the two scans
    above so the sweep is widened in one place.
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in _unconditional_paths(base))


def _receivers_of(method: str, base: Path = TOOLS_DIR) -> list[str | None]:
    """For every call spelled `.method(` anywhere under `base`, the service class it
    lands on -- or `None` where this cannot tell.

    Exists for `FORBIDDEN_QUALIFIED_CALLS`, and only for it: the substring scan below is
    the right instrument for a method name that belongs to exactly one service, and the
    wrong one for `upsert`. Resolves the three call shapes this package actually uses --
    `ServiceClass(context.session).m(...)`, `svc = ServiceClass(...)` then `svc.m(...)`,
    and the module-private factories (`_invoices(context) -> InvoiceService`) that
    `tools/invoices.py` and `tools/documents.py` use -- recognising a service by this
    codebase's own naming rule, a class whose name ends in `Service`, so that this file
    stays readable without importing anything from `packages/core`.

    An unresolved receiver is reported as `None` and counted as an offence by the caller.
    That is the whole point of returning `None` rather than dropping the call: "we could
    not work out whose `upsert` this is" must not read as "it is not the forbidden one".
    A ban that fails open is not a ban.
    """
    receivers: list[str | None] = []
    for path in sorted(base.rglob("*.py")):
        if path in PRIVILEGED_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        factories: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                returns = node.returns
                if isinstance(returns, ast.Name) and returns.id.endswith("Service"):
                    factories[node.name] = returns.id

        def produced(func: ast.expr, factories: dict[str, str] = factories) -> str | None:
            if not isinstance(func, ast.Name):
                return None
            return func.id if func.id.endswith("Service") else factories.get(func.id)

        bindings: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                owner = produced(node.value.func)
                if owner is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = owner

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != method:
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Call):
                receivers.append(produced(receiver.func))
            elif isinstance(receiver, ast.Name):
                receivers.append(bindings.get(receiver.id))
            else:
                receivers.append(None)
    return receivers


def test_the_ast_walk_actually_finds_the_registered_tools() -> None:
    """Guards the guard. If `_registered_tool_names` returned an empty set -- because the
    decorator spelling changed, or because the registrations moved -- every ban test
    below would pass while checking nothing. Anchored on tools that exist today and on a
    plausible count, so a silent break is a failure and not a green run."""
    names = _registered_tool_names()
    assert "list_invoices" in names
    assert "get_invoice" in names
    assert "create_organization" in names
    assert len(names) > 20


def test_the_source_scan_actually_reads_more_than_one_file() -> None:
    """Guards the *other* guard. If `_tools_source` only ever returned
    `tools/__init__.py`'s own text -- because `rglob` found nothing, or `TOOLS_DIR` was
    pointed at the wrong directory -- `test_no_registered_tool_calls_a_fiscal_write_
    under_another_name` would silently stop seeing `tools/invoices.py` and
    `tools/timetracking.py` at all, which is exactly the gap the whole-package scan
    exists to close."""
    source = _tools_source()
    assert "def search(" in source  # tools/invoices.py
    assert "def log_time(" in source  # tools/timetracking.py
    # And `prompts/`, which `server.py` registers unconditionally and which this sweep
    # read right past until the 9C final review. Anchored on two of the four so that a
    # sweep silently narrowing back to `tools/` fails here instead of leaving every ban
    # below green over a directory it no longer looks at.
    assert "def status_organization(" in source  # prompts/customer.py
    assert "def closing_month(" in source  # prompts/dashboards.py


def test_the_registration_scan_reads_every_module_and_not_only_the_first() -> None:
    """Guards the widening. `tools/gmail.py` is the first module outside
    `tools/__init__.py` to register tools of its own, and it is the shape every future
    domain module will take. If the scan silently narrowed back to one file, the Gmail
    bans below would pass while checking a file that contains none of them."""
    names = _registered_tool_names()
    assert "list_gmail_messages" in names  # tools/gmail.py
    assert "create_organization" in names  # tools/families/organizations.py


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_no_tool_is_registered_for_a_forbidden_operation(forbidden: str) -> None:
    assert forbidden not in _registered_tool_names(), (
        f"'{forbidden}' e' registrato come tool MCP, ma {_REASON}"
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN_GMAIL)
def test_no_tool_is_registered_for_a_forbidden_gmail_operation(forbidden: str) -> None:
    assert forbidden not in _registered_tool_names(), (
        f"'{forbidden}' e' registrato come tool MCP, ma {_REASON_GMAIL}"
    )


@pytest.mark.parametrize("fragment", FORBIDDEN_TOOL_NAME_FRAGMENTS)
def test_no_registered_tool_name_can_even_suggest_sending(fragment: str) -> None:
    """The stronger form of the two send bans above. A list of names knows only the
    spellings somebody thought of; this knows that nothing on this surface sends."""
    offenders = sorted(name for name in _registered_tool_names() if fragment in name)
    assert not offenders, f"{offenders} contengono '{fragment}', ma l'invio {_REASON_GMAIL}"


@pytest.mark.parametrize("method", FORBIDDEN_SERVICE_CALLS)
def test_no_tool_reaches_a_forbidden_operation_under_another_name(method: str) -> None:
    """The name check alone is not enough: a tool called `finalise_paperwork` that calls
    `.issue(...)`, or one called `tidy_up_rates` whose own call-through wrapper in
    `tools/timetracking.py` calls `.recalculate_rates(...)`, would both pass it. This
    looks at what the whole `tools/` package actually calls, not at any one tool's own
    name."""
    offenders_in_source = f".{method}(" in _tools_source()
    assert not offenders_in_source, (
        f"'.{method}(' compare in qualche modulo sotto tools/, ma quell'operazione "
        f"{_REASON} Il divieto vale sulla chiamata, non sul name del tool che vi "
        "arriva: rinominare il tool o spostare la chiamata in un altro modulo non la "
        "rende ammissibile."
    )


def test_the_qualified_scan_tells_two_services_with_the_same_method_apart(tmp_path: Path) -> None:
    """Guards the guard, on the one property that makes it worth having. If
    `_receivers_of` collapsed to "some `.upsert(` exists", a qualified ban declared on
    one service's `upsert` would also ban every other service's -- `FiscalProfileService`
    and `LegalEntityService` both have one, and both are tools today -- and the build
    would fail with a message about the wrong operation, which is how a policy stops
    being believed."""
    (tmp_path / "m.py").write_text(
        "def a(context):\n"
        "    return LegalEntityService(context.session).upsert(data, context.actor)\n"
        "\n"
        "def b(context):\n"
        "    return FiscalProfileService(context.session).upsert(data, context.actor)\n",
        encoding="utf-8",
    )
    assert sorted(str(r) for r in _receivers_of("upsert", tmp_path)) == [
        "FiscalProfileService",
        "LegalEntityService",
    ]


def test_the_qualified_scan_fails_closed_on_a_receiver_it_cannot_resolve(tmp_path: Path) -> None:
    """The other half, and the one that decides whether this mechanism is a ban or a
    suggestion: a call whose receiver the walk cannot attribute to a class is reported as
    unresolved, and the ban test below treats unresolved as forbidden. Anything else
    would let `some_container.fiscal.upsert(...)` through on the grounds that nobody
    could prove what it was."""
    (tmp_path / "m.py").write_text(
        "def a(context):\n    return context.services.fiscal.upsert(data)\n", encoding="utf-8"
    )
    assert _receivers_of("upsert", tmp_path) == [None]


def test_no_tool_reaches_a_forbidden_operation_on_the_service_that_owns_it() -> None:
    """The qualified half of `test_no_tool_reaches_a_forbidden_operation_under_another_
    name`. Same guarantee, expressed against the receiver instead of the bare name,
    because the bare name would either miss the operation or ban an unrelated one.
    A loop and not a parametrisation, so that the tuple being empty (as it is since
    ORB-188) asserts nothing instead of producing a skipped test."""
    for service, method in FORBIDDEN_QUALIFIED_CALLS:
        offenders = [
            owner or "un ricevitore non risolvibile"
            for owner in _receivers_of(method)
            if owner is None or owner == service
        ]
        assert not offenders, (
            f"'.{method}(' e' chiamato su {offenders} da qualche modulo sotto tools/, ma "
            f"'{service}.{method}' {_REASON} Il divieto vale su questa coppia "
            "servizio/metodo: un ricevitore che questo controllo non sa attribuire conta "
            "come violazione, perche' un divieto che si arrende all'ambiguita' non e' un "
            "divieto."
        )


def test_the_structural_ban_has_a_second_line_on_the_credential_itself() -> None:
    """The tool being absent is not, on its own, the guarantee this file claims.

    Everything above proves these operations are unreachable over *this transport*. A
    personal access token is not confined to it: `PatService.resolve` answers with
    `Actor(type="mcp", role=<the owner's role>)`, and `apps/api`'s `get_actor` accepts
    the same `Bearer pgc_...` header on every REST route. Until `FULL_ACCESS_ACTIONS`
    existed nothing in that package read `actor.type`, so a token handed to an agent on
    the written promise that it "may prepare but may not emit" could issue an invoice
    with one curl -- spending a register number and producing a FatturaPA.

    So the two lists have to stay one list. This test is the seam: `FORBIDDEN` names the
    tools that must not be registered, `FULL_ACCESS_ACTIONS` names the operations
    that must refuse an agent whatever transport it arrives on, and a name added to one
    and forgotten in the other is that gap coming back. The mapping is identity except
    for one pair, where the tool's advertised name and the action string the service
    passes to `require_admin` were chosen separately -- mapped here rather than renamed,
    because the tool name is the agent-facing contract and the action string is the
    audited one.

    `REST_ONLY_FORBIDDEN` is the union's other term, and it is what keeps this a
    statement rather than a loose fit: the ban list is keyed on the *operation*, so it
    can legitimately hold one this package offers no tool for -- Drive's three
    configuration operations and the day ledger's writes, reachable only over REST and only
    by a person. Those are enumerated there with their justification, so "banned but
    toolless" has named answers instead of being slack in this assertion. One more, or one of
    these growing a tool and staying in that set, fails here.
    """
    from pigrocrm.core.agent_actions import AGENT_ALLOWED_ACTIONS, FULL_ACCESS_ACTIONS

    tool_name_to_action = {"mark_invoice_transmitted": "mark_transmitted_externally"}
    expected = {tool_name_to_action.get(name, name) for name in FORBIDDEN}

    # The union would still balance if one of the three grew a tool and stayed in both
    # sets, so the disjointness is asserted on its own: `REST_ONLY_FORBIDDEN` claims
    # "no tool", and a claim nothing checks is a comment.
    assert not (REST_ONLY_FORBIDDEN & expected), (
        "un'operazione elencata in REST_ONLY_FORBIDDEN ha ora un tool MCP: togliela da "
        f"quel set, la sua motivazione non vale piu' -- {sorted(REST_ONLY_FORBIDDEN & expected)}"
    )
    assert not (expected & AGENT_ALLOWED_ACTIONS), (
        "an operation with a banned tool is in AGENT_ALLOWED_ACTIONS: "
        f"{sorted(expected & AGENT_ALLOWED_ACTIONS)}"
    )
    assert not (set(FULL_ACCESS_BY_THE_RECORD) & expected), (
        "an operation listed in FULL_ACCESS_BY_THE_RECORD has a banned tool: it is decided "
        "per record, not by the tool's absence -- "
        f"{sorted(set(FULL_ACCESS_BY_THE_RECORD) & expected)}"
    )
    by_the_record = set(FULL_ACCESS_BY_THE_RECORD)
    assert expected | REST_ONLY_FORBIDDEN | by_the_record == set(FULL_ACCESS_ACTIONS), (
        "le due meta' del divieto sono divergenti: FORBIDDEN vieta il tool, "
        "FULL_ACCESS_ACTIONS vieta l'operazione a qualunque credenziale di tipo "
        "agente. Un name aggiunto a una sola delle due lascia proprio il buco che la "
        "seconda e' stata scritta per chiudere -- l'assenza del tool non impedisce la "
        "stessa chiamata via REST con lo stesso token. Un'operazione vietata che non ha "
        "nessun tool va elencata e motivata in REST_ONLY_FORBIDDEN, non lasciata come "
        "gioco in questa asserzione.\n"
        f"solo in FORBIDDEN: {sorted(expected - set(FULL_ACCESS_ACTIONS))}\n"
        "solo in FULL_ACCESS_ACTIONS: "
        f"{sorted(set(FULL_ACCESS_ACTIONS) - expected - REST_ONLY_FORBIDDEN - by_the_record)}\n"
        "in REST_ONLY_FORBIDDEN ma non vietate: "
        f"{sorted(REST_ONLY_FORBIDDEN - set(FULL_ACCESS_ACTIONS))}"
    )


async def test_a_per_record_full_access_action_is_reached_by_a_default_tool(
    tmp_path: Path,
) -> None:
    """The third term of the union above is honest only while each of its tools is on the
    default surface: an action decided per record that no default tool reaches would be a
    ban with a name nobody can call."""
    server = build_server(
        lambda: None,  # type: ignore[arg-type, return-value]
        lambda: Actor(id=None, type="mcp", role="admin"),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )
    default = {tool.name for tool in await server.list_tools()}
    for action, tools in FULL_ACCESS_BY_THE_RECORD.items():
        assert set(tools) <= default, (action, sorted(set(tools) - default))


async def test_they_are_registered_exactly_when_the_installation_opted_in(
    mcp_session: Session, tmp_path: Path
) -> None:
    """Both halves of the switch, in one assertion, because them disagreeing is the
    failure worth catching.

    Everything above proves the tools are absent on a default installation -- the
    guarantee for everyone who never touches the setting. This adds the other direction:
    with `mcp_full_access` on, every one of them is there.

    A half-open switch is worse than either honest state. Registered tools that all
    refuse waste an agent's turns and read as a broken product; open capabilities with no
    tool to reach them are a setting that does nothing. Worst is all but one: the
    operator believes the switch is on and the one refusal arrives at the moment
    somebody is issuing an invoice.

    This test asks the built server rather than reading the source, which is why it sits
    apart from its neighbours: registration is conditional at *runtime*, on a value no
    AST walk can see.

    `Settings(_env_file=None, ...)` and never the ambient settings -- this repository's
    own `.env` has the switch **on**, so a test that inherited it would assert the
    opposite of what it claims and pass anyway.
    """

    async def tool_names(full_access: bool, *, gmail: bool) -> set[str]:
        # The token carries the flag too (REB-810): a token that has not been given it is
        # shown the closed surface even on an open installation, which
        # `test_token_full_access.py` owns. Here both halves agree, as they do for a space
        # whose members have all opened their tokens.
        actor = Actor(id=None, type="mcp", role="admin", full_access=full_access)
        google = (
            {
                "google_client_id": "cid.apps.googleusercontent.com",
                "google_client_secret": "the-secret",
                "google_token_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
                "public_url": "https://crm.example.it",
            }
            if gmail
            else {}
        )
        server = build_server(
            lambda: mcp_session,
            lambda: actor,
            LocalFileStorage(str(tmp_path)),
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                mcp_full_access=full_access,
                module_collaborators=True,
                **google,
            ),
        )
        return {tool.name for tool in await server.list_tools()}

    # Without Google, the switch adds every forbidden tool except the ones that need a
    # mailbox to exist at all: those stay absent, not broken, exactly as `tools/gmail.py`
    # does on the same installation.
    closed = await tool_names(False, gmail=False)
    open = await tool_names(True, gmail=False)

    assert not (closed & set(FORBIDDEN)), "un'installazione chiusa non registra nessuno dei divieti"
    without_google = set(FORBIDDEN) - FORBIDDEN_NEEDING_GMAIL
    missing = without_google - open
    assert not missing, f"interruttore aperto ma questi tool non esistono: {sorted(missing)}"
    # And nothing else moved: opting in means exactly these more tools, not a different server.
    assert open - closed == without_google

    # With Google configured as well, the switch adds all of them -- and still nothing
    # else.
    closed_google = await tool_names(False, gmail=True)
    open_google = await tool_names(True, gmail=True)

    assert not (closed_google & set(FORBIDDEN))
    assert open_google - closed_google == set(FORBIDDEN)


def test_the_privileged_module_is_the_only_place_they_appear() -> None:
    """Guards the exemption.

    The three scans above skip `PRIVILEGED_MODULES`, which is sound only while those
    files are genuinely the whole exemption and their calls are genuinely conditional.
    Both halves are checked here: every forbidden service call that appears anywhere
    under `tools/` must appear in one of them, and `server.py` must reach **each** of
    them only behind `mcp_full_access`.

    Without this, the exemption is a hole with a comment on it: a third file added to
    the skip list, or either module imported unconditionally, would leave every test
    above green while the forbidden operations became reachable on an installation that
    never opted in.

    Note what the second half does *not* claim. It proves each privileged import is
    *nested inside* the `if resolved_settings.mcp_full_access:` statement, which is what
    a structural check can prove about a nested import; that the three Drive tools
    additionally need Google is proved at runtime instead, by
    `test_drive_privileged_tools.py`'s four-way sweep over built servers -- the only
    place a *runtime* condition can be observed at all.
    """
    unconditional = _tools_source()
    privileged = "\n".join(path.read_text(encoding="utf-8") for path in PRIVILEGED_MODULES)

    for method in FORBIDDEN_SERVICE_CALLS:
        assert f".{method}(" not in unconditional, (
            f"'.{method}(' compare in un modulo che viene registrato sempre"
        )
        assert f".{method}(" in privileged, (
            f"'.{method}(' non compare in nessun modulo privilegiato: o il tool non "
            "esiste, o e' altrove, e in entrambi i casi l'esenzione qui sopra sta "
            "coprendo la cosa sbagliata"
        )

    inside, outside = _privileged_imports_of_server()
    assert inside == {module.stem for module in PRIVILEGED_MODULES}, (
        "un modulo privilegiato non e' importato dentro `if resolved_settings."
        f"mcp_full_access:`: dentro la guardia ci sono {sorted(inside)}"
    )
    assert not outside, (
        f"{sorted(outside)} sono importati fuori dalla guardia: verrebbero registrati "
        "sempre e l'esenzione delle scansioni diventerebbe un buco"
    )


def _privileged_imports_of_server() -> tuple[set[str], set[str]]:
    """The privileged modules `server.py` imports, split into those nested inside the
    `mcp_full_access` guard and those outside it.

    Structural rather than textual, because the textual version answered a weaker
    question than it looked like it did: "the import appears later in the file than the
    guard does" is also true of an import in a *sibling* block below the guard, or after
    it at module level -- both of which run unconditionally. This walks
    `build_server`'s body for the `ast.If` whose test is `resolved_settings.
    mcp_full_access` and asks whether each `ImportFrom` is a descendant of it.

    Two sets and not a boolean so the failure can say which module is on the wrong side;
    a module imported both inside and outside appears in both, and `fuori` being
    non-empty is what fails.
    """
    tree = ast.parse((TOOLS_DIR.parent / "server.py").read_text(encoding="utf-8"))

    def is_the_guard(node: ast.AST) -> bool:
        if not isinstance(node, ast.If):
            return False
        test = node.test
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "mcp_full_access"
            and isinstance(test.value, ast.Name)
            and test.value.id == "resolved_settings"
        )

    guards = [node for node in ast.walk(tree) if is_the_guard(node)]
    assert guards, (
        "`if resolved_settings.mcp_full_access:` non esiste piu' in server.py: questo "
        "controllo non sta piu' guardando niente"
    )

    def imported_names(nodes: list[ast.AST]) -> set[str]:
        found: set[str] = set()
        for root in nodes:
            for node in ast.walk(root):
                if isinstance(node, ast.ImportFrom):
                    found |= {alias.name for alias in node.names}
        return found

    names = {module.stem for module in PRIVILEGED_MODULES}
    inside = imported_names(list(guards)) & names
    all = imported_names([tree]) & names
    return inside, all - inside


def _agent_reachable_source() -> str:
    """Every line of `apps/mcp` an agent can reach on a default installation.

    Wider than `_tools_source()` on purpose, and the width is the point: agent-reachable
    code is not only `tools/`. `resources/entities.py` renders the `organization://`,
    `person://` and `deal://` templates, `prompts/` holds §10's four prompts, and
    `server.py` itself carries three inline `@mcp.resource` bodies plus
    `describe_schema` -- all registered unconditionally, none of them
    scanned by a tools-only sweep. A `drive_reader_for` built inside a resource render or
    a prompt body would be exactly as reachable as one built in a tool, while leaving no
    trace under `tools/` at all.

    `prompts/` was the one missing, and it was missing from `_tools_source` too: nothing
    about it is conditional (`server.py` calls `register_prompts` outside every `if`), and
    a prompt takes the same `McpContext` a tool does, so «un prompt che riassume e poi
    emette» would have passed every ban in this file. It is now in both sweeps, through
    `_unconditional_paths`.

    `PRIVILEGED_MODULES` are subtracted, as everywhere else in this file: their whole
    existence is conditional.
    """
    paths = [
        path
        for base in (TOOLS_DIR, TOOLS_DIR.parent / "resources")
        for path in _unconditional_paths(base)
    ]
    paths.append(TOOLS_DIR.parent / "server.py")
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_the_wide_scan_really_reads_the_resources_the_prompts_and_the_server() -> None:
    """Guards the widening, the way this file guards every other scan. An
    `_agent_reachable_source` that silently collapsed back to `tools/` would leave the
    Drive-reader ban below green while no longer looking at the three places it was
    widened for."""
    source = _agent_reachable_source()
    assert "def render_organization(" in source  # resources/entities.py
    assert "def organization_resource(" in source  # server.py's inline @mcp.resource
    assert "def status_organization(" in source  # prompts/customer.py
    assert "def review_pipeline(" in source  # prompts/dashboards.py
    assert "def search_everything(" in source  # tools/families/shared.py, i.e. the old width


def test_no_unconditional_module_can_even_obtain_a_drive_reader() -> None:
    """The complete form of the Drive half of the ban, and the reason
    `DriveReader.describe` needs no entry in `FORBIDDEN_SERVICE_CALLS`.

    A bare-name ban covers the methods whose names happen to be unique. This covers the
    *class*: `drive_reader_for` is the only way to obtain a `DriveReader` at all -- it
    is what runs `GoogleDriveAccountService.usable`, unseals the refresh token and reads
    `root_folder_ids` off the row, and a reader built any other way would be one whose
    roots nobody had checked. So a module that cannot name it has nothing to call
    `list_children`, `read_text` or `describe` *on*, whatever those methods are called
    next year.

    Stated as its own test rather than folded into the loop above because it is a
    different kind of statement: the ban list forbids calls, this forbids a capability.
    """
    unconditional = _agent_reachable_source()
    privileged = "\n".join(path.read_text(encoding="utf-8") for path in PRIVILEGED_MODULES)

    assert "drive_reader_for" not in unconditional, (
        "un modulo registrato sempre puo' costruire un DriveReader: da li' ogni "
        "metodo di lettura di Drive e' raggiungibile, anche quelli il cui name non "
        "compare in FORBIDDEN_SERVICE_CALLS"
    )
    assert "drive_reader_for" in privileged, (
        "nessun modulo privilegiato costruisce un DriveReader: o i tool di Drive non "
        "esistono piu', o sono altrove, e in entrambi i casi questo divieto sta "
        "coprendo la cosa sbagliata"
    )
