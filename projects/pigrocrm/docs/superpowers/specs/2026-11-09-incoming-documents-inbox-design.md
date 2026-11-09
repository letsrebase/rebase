# The incoming documents inbox

Status: approved by Lorenzo on 2026-11-09. Card: REB-960. Project: «Collect every
incoming document in one inbox» (P-REB-81). Frame: `2026-11-09-space-profiles-modules-and-the-money-picture-design.md`
(the «frame» below), § A2, A3, A4, A5, A8 and C2.

## Why

The money picture of the frame needs documents PigroCRM does not receive today. A supplier
invoice, a receipt, a bank statement or a signed contract reaches it only one way: a person
uploads FatturaPA XML files, or a ZIP of them, into the issued-invoice import
(`invoices/import_upload.py`, `import_review.py`, `import_confirm.py`), which parses the
supplier's invoices and then drops them, since there is nowhere to put them
(`import_direction.py`, «incoming»). Mail is one Gmail account per user
(`gmail/models.py:58`, `unique=True` on `user_id`) read only for the addresses already in the
address book (`gmail/query.py`). There is no IMAP, no PEC, no signed `.p7m`, no ZIP from mail,
and no inbox that keeps what arrives.

Lorenzo, 2026-11-09, on how documents arrive today, for him and for rebase: through an
intermediary that holds the SdI recipient code (Fiscozen for him; not chosen yet for rebase),
on a PEC mailbox, as a PDF in an ordinary email, and from the «Cassetto fiscale» of the
«Agenzia delle Entrate». All four. The banks to cover first: Banco BPM, Revolut Business and
Qonto.

Lorenzo, 2026-11-09, on who reads free text: «entrambe le cose», PigroCRM's own model and the
user's own agent. This supersedes in part the 2026-10-26 row of `docs/design/DECISIONS.md`
(REB-754), which kept server-side extraction and IMAP polling out; both are now in, and a
superseding row is added in the same change.

## Decisions taken

1. Every road ends in **one inbox** («In arrivo»), and nothing that arrives is lost or filed
   silently: an item is either certain data entered with its trace, or a proposal a person
   accepts (frame § A4).
2. Several mailboxes per space, **not only Gmail**, each owned by a person or by the space
   (frame decision 5).
3. Bank statements arrive by email or upload first; banking APIs come later (frame decision
   6). This project stores and tags statement files; reading them, and the mailbox purpose
   that fetches them, are C4.
4. Everything is over MCP and REST with the user's permissions (frame § A8), except bytes,
   which never travel over MCP (§ 6).
5. **Two readers, one proposal.** Free text is read either by PigroCRM's own model or by the
   user's agent over MCP; both end in the same proposal (§ 3.2).

The inbox and mailboxes are core, always on, and extend the frame's A2 core list. The SdI
road of frame C2 is, here, the PEC mailbox; the accredited channel stays later (§ 2.3).

## 1. The inbox

An **inbox item** is one file that arrived, before it becomes anything:

- the bytes, stored through the existing storage (`storage/factory.py`), under a key of the
  inbox's own built from a slugified name (§ 2.1), so an item needs no customer, deal or
  contract as its owner (a `Document` must have exactly one, `documents/models.py:23`, and a
  supplier invoice has none of those);
- where it came from: an upload by a person (who), a space mailbox (which, the message id, the
  sender, the subject, the date; a personal mailbox is never a source, § 2.2), a parent item
  it was unpacked from (a ZIP, a PEC envelope), later the SdI;
- its SHA-256, and once read its **natural key** (§ 3.3);
- its **legal entity**, once classified (§ 3.4);
- its **kind**, decided by the classifier of § 3: sales invoice, supplier invoice, credit
  note, receipt, bank statement, contract, tax document (an F24 receipt, a tax return, a
  notice from the tax office; filed by C6), payslip (filed by C7), other;
- its **state**: `new`, `proposed` (a proposal waits for a person), `filed` (it became a
  record, which it points at), `duplicate` (of which item), `discarded` (by whom, why).

**Filing** copies the bytes into the record that owns it (a supplier invoice, C3; a
statement, C4; a contract or a work unit, through proposals) as a `Document` version or the
record's own attachment, then deletes the item's own copy of the bytes. The item row stays
with its link, its hashes and its reason: a filed or discarded item is never deleted, and a
discarded one stays readable.

What an item can be filed as depends on which modules are on (frame § A2): with `purchases`
off, a supplier invoice waits as an item and says which module would file it.

**Visibility.** Before an item has a legal entity it is visible to admins without an entity
scope and to the person who uploaded it (the uploader's view is a rule of this design). A child
item takes its parent's uploader; an item from a space mailbox has none, so only those admins
see it. After classification the usual entity scope applies (spec 2026-10-03 § 4). A space
mailbox syncs as a system actor with all-entity scope, like the other jobs.

## 2. The roads in

### 2.1 Upload

A person uploads one or more files, on the web (drag and drop, and the phone's «+»: a photo
or a PDF) or over REST. MCP never uploads (§ 6). The inbox has **its own allowlist and
signature checks**, because the documents allowlist (`documents/schemas.py:36-45`) refuses
p7m, ZIP, CSV, XLS and CAMT, and `documents/uploads.py:32-38` sniffs magic bytes only for pdf,
png, jpeg, docx and xlsx. Accepted: xml, p7m (DER and base64), zip, pdf, png, jpeg, csv, xls,
xlsx, camt.053, each with a signature check where the format has one. Storage keys are built
from slugified names in the `storage_key_for` style (`documents/context.py:122`), since the
key rule (`storage/base.py:39`) allows lowercase `[a-z0-9._-]` only. The size ceiling is the
documents one (`SIZE_MAX`, 100 MiB, `documents/schemas.py:50`).

- **FatturaPA XML** (the `FPR12` format the importer reads, `invoices/fatturapa_import.py`).
- **Signed FatturaPA, `.p7m`**: the CMS envelope is opened and the XML inside is read.
  Whether the signature is also verified is the builder's call; the XML is read either way.
  A file whose envelope cannot be opened becomes an item of kind «other» with the reason.
  That `.p7m` is CAdES, and that the SdI delivers it Base64-wrapped, are **to verify**
  (frame § A5). No code in the repo reads p7m and `cryptography` does not return the signed
  content, so the reader is a new dependency declared in `pyproject.toml` (builder's choice,
  since `test_architecture.py` builds its import allowlist from it).
- **ZIP**: unpacked with `invoices/import_upload.py::expand_uploads` and its limits (2000
  files, 10 MiB per file, 100 MiB in total, no nested ZIP, no password), one item per file
  inside. The ZIP itself is kept as the **parent item**, and each child points at it.
  Statement PDFs above 10 MiB therefore arrive as a loose upload, not inside a ZIP.
- **PDF and images** (a receipt photo, a supplier PDF, a bank statement PDF).
- **CSV, XLS, XLSX and CAMT.053** for bank statements: stored and tagged by file type only.
  Telling one bank's CSV from another's, and reading any of them, is C4. Binary XLS needs a
  reader (`xlrd` or similar), a C4 dependency.
- **SdI metadata files** (names containing `_MT_`) and **SdI receipts** are discarded
  automatically with that reason; they stay as discarded items.

A new upload route needs its own nginx location with `client_max_body_size 105m` in
`deploy/nginx/pigro.letsrebase.conf`, `preview.pigro.letsrebase.conf` and `spa.conf`
(the existing ones are at `pigro.letsrebase.conf:171-182`); the host file is applied by hand.
Without it the default 1m answers 413.

### 2.2 Mailboxes

A **mailbox** is a connected account PigroCRM reads. A new `mailboxes` table holds it, with:

- an **owner**: a person (their own mailbox, visible to them alone: it yields their synced
  messages and the task proposals of § 4, which only they see, when they switch them on, and
  never an inbox item, since
  `billing` is not allowed on it) or the space (a shared address such as fatture@ or a PEC,
  connected by an admin, and the only kind that sources items);
- a **provider**: Gmail through Google OAuth, as today (`gmail/oauth.py`), or IMAP with host,
  port, user and password, which covers PEC, Aruba, generic hosting, and a Google Workspace
  box with an app password. Microsoft 365 OAuth is later, since Exchange Online refuses a
  plain password for IMAP (to verify, frame § A5);
- one or more **purposes**, which decide what PigroCRM looks for and what it keeps:

| Purpose | Reads | Keeps | Allowed on |
|---|---|---|---|
| `correspondence` | the mail of addresses already in the address book, as Gmail sync does today (the old `activities` purpose, renamed: `activities` is the timeline package and a proposed item here is a `task`) | messages and links to customers, people and deals; with the owner's switch, task proposals from bodies (§ 4) | personal and space mailboxes |
| `billing` | mail with an attachment of an accepted type (§ 2.1), from any sender | the attachments, as inbox items, with sender, subject and date; never the body | space-owned mailboxes only |

The frame's C2 names `activities` and `billing`; the rename and the restriction of `billing`
to space-owned mailboxes are the only changes, and
`statements` is C4's, since it needs bank accounts and their senders.

**Gmail wrapped, not replaced.** A personal Gmail is the existing `google_accounts` row
wrapped 1:1 by a `mailboxes` row with the purpose `correspondence`, its owner the person.
The unique-per-user rule (`gmail/models.py:58`) and the Drive-sibling rule
(`gmail/oauth.py:83-103`) stay, so the existing Gmail tests stay valid. A person has one
Gmail through OAuth; any further mailbox they own is IMAP. **Space-owned mailboxes in this
project are IMAP only**; space-owned Gmail OAuth is later, which avoids an admin's personal
Gmail and Drive colliding with a space Gmail.

**Query guards.**

- `correspondence` on Gmail keeps `messages_list_url` exactly as it is: no list query without
  an address clause (`gmail/query.py:55, 288-295`), tested by AST walk
  (`test_gmail_query.py:263`) and by request (`test_gmail_sync.py:150`).
- `billing` on Gmail, when a Gmail mailbox can be space-owned, gets a **sibling builder**, not
  a change to `messages_list_url`: it refuses a list query without a `has:attachment` plus a
  filename clause, tested the same two ways.
- IMAP has no attachment search. The reader fetches **headers and BODYSTRUCTURE only**, since
  the watermark, never bodies, and fetches an attachment part only when its type is accepted.
  This is why `billing` is for dedicated space mailboxes: the server is still asked about
  every message since the watermark, which a personal mailbox must never allow.
- A `billing` read skips inline parts (Content-ID or inline disposition), images under a size
  floor (the builder's value) and the mailbox's own sent mail.
- **One watermark per mailbox and purpose.** A first connection backfills 90 days by default;
  a purpose added later to an established mailbox backfills from its own watermark.

**Storing bytes is a deliberate reversal.** Sync today keeps «no attachment bytes, ever»
(`gmail/parse.py:12`, `gmail/attachment_text.py`). `billing` stores them, for billing
mailboxes only; `correspondence` is unchanged.

**Credentials.**

- Gmail OAuth refresh tokens stay sealed as today.
- IMAP passwords are sealed with a new root key, `PIGROCRM_MAILBOX_TOKEN_KEY`, derived per
  registry space the way `space_slack_token_key` is (`tenants/google.py:84-88`), and used
  undivided by the root installation as the Google key is; never with `PIGROCRM_JWT_SECRET`,
  whose rotation makes sealed settings unreadable (`space_settings/sealing.py`).
- **IMAP security**: TLS required (993, or STARTTLS on 143); the host is resolved once, the
  connect address pinned, and only global addresses accepted, as `reminders/push.py` does
  (`is_global`, `:167`).
- Connecting a mailbox and typing its password is a **person's act**, like every credential
  (REB-880).

**Health**, for every mailbox: `active`; `failing` (a transient failure, retried);
`auth_failed` (a person must act: a changed password, a revoked grant); `disconnected`.
Gmail's four statuses (`active`, `expired`, `revoked`, `disconnected`, `gmail/models.py:25-40`)
map onto it: `expired` and `revoked` to `auth_failed`. This replaces the earlier claim of a
three-state banner; the banner today has four reasons (`revoked`, `expiring`, `expired`,
`scope_missing`, `gmail/schemas.py:112`) and they stay for Gmail.

Sync runs in the existing 15-minute cron. The `gmail-sync` command becomes `mail-sync`; the
`SCHEDULED_JOBS` entry (`reminders/jobs.py:75`), the hosts' crontabs and the runbooks change
in the same change, and an IMAP-only space must not read «not configured»
(`reminders/jobs.py:234`) nor exit 1 with no Google mailbox (`cli/gmail.py`). It never
deletes or marks mail on the server.

**Disconnecting** a personal mailbox keeps what was received, and offers its owner the
deletion of its synced messages, as today's offer does (`gmail/oauth.py:48-76`); it holds no
inbox items and no attachment bytes. The items of a space mailbox belong to the space and
stay when the mailbox is disconnected.

### 2.3 Intermediaries, the «Cassetto fiscale» and the SdI

- **An intermediary** that holds the SdI recipient code (Fiscozen, Aruba, Fatture in Cloud
  and the like) usually notifies by email and lets you download the XML or a ZIP. PigroCRM
  takes what it sends to a `billing` mailbox, and the ZIP or XML a person downloads from it.
  A connector to an intermediary's own API is not designed here.
- **The «Cassetto fiscale»** is read by uploading the ZIP of XML or `.p7m` files exported
  from it (the export format is **to verify**, A5). No automatic access: it needs the
  taxpayer's own credentials.
- **A PEC mailbox** is the road from the SdI in this project. That a PEC with recipient code
  0000000 receives the SdI's own mail, with the signed invoice attached, is **to verify**
  (A5). The SdI's envelope is expected to hold `daticert.xml`, `postacert.eml`
  (`message/rfc822`) and `smime.p7s` (structure **to verify**): the reader unwraps
  `postacert.eml`, reads the invoice inside it, and ignores `daticert.xml` and `smime.p7s`;
  the envelope is kept as the parent item.
- **The SdI as an accredited channel**, or an intermediary's API, is the later step the frame
  names; when it comes it is one more road into the same inbox.

## 3. Classification

Each new item is read once, in this order:

1. **Structured and checkable → certain data, not necessarily written alone.** A FatturaPA
   (plain or `.p7m`) is parsed with the existing adapter (`invoices/import_adapter.py`).
   Its **legal entity** comes first (§ 3.4); then its direction, from
   `import_direction.classify_direction`:
   - **our legal entity as supplier**: a sales invoice. It goes to the existing issued-invoice
     review (`import_review.py`) and **a person confirms** it, since importing an invoice is
     DESTRUCTIVE (DECISIONS, 2026-10-27, REB-813) and the confirmation may create customers
     and consumes a register number. No cron and no agent confirms it;
   - **our legal entity as buyer** (exactly one matches): a supplier invoice or credit note
     (TD01 to TD06 today, TD17 to TD19 added in C3), which C3 registers automatically; a
     person can cancel it, and C3 owns that;
   - **a statement file** goes to C4, which reads it.
2. **Free text → proposal.** A PDF, an image or a mail body is read by one of two readers
   (§ 3.2). What it reads is a proposal of the kind the module will file (supplier invoice,
   receipt, statement, contract, worked day, task), accepted or rejected by a person
   (`ProposalService`, § 3.5). The text read is data, never instructions (`provenance.py`).
3. **Nothing recognised → `other`**, kept, with a sentence saying why.

### 3.2 The two readers of free text

- **PigroCRM's own model**, off by default, switched on per space by an admin and configured
  the way the hub's «Modelli AI» are (`projects/hub`, tables `ai_providers`, `ai_tasks`,
  `ai_prices`, `ai_usage`, migration `0036`): provider, sealed key, model, daily spend cap,
  on or off, a «Prova» button. Its tasks: reading a PDF or a receipt (kind, issuer, tax id,
  number, dates, amounts with VAT split, IBAN, due date) and proposing tasks from mail
  (§ 4). The privacy page names the model and what it receives before the switch can be
  turned on. The model settings are an admin person's act.
- **The user's own agent over MCP**, which reads an item's extracted text and creates the
  proposal through tools, as agents do today for contracts and days.

Both end in the same proposal, and the second needs nothing switched on.

### 3.3 Duplicates

Keys differ by kind. **Sales invoices** use the importer's own key and hash
(`invoices/import_dedup.py`, `natural_key`: legal entity, year, number). **Everything else**
uses issuer tax id, number, date, document type and hash (this adds the type to the frame's
A4 key of issuer VAT number, number and date). The same invoice arriving by PEC, by the
intermediary's ZIP and as a PDF by email is one item and two duplicates of it. Further:

- an **XML arriving after a PDF-derived record** updates that record, the XML wins, and the
  PDF stays its printable copy; a PDF arriving after the XML attaches to it the same way;
- a file whose hash matches a **discarded** item is a duplicate of it and stays discarded.

### 3.4 Legal entity

An item gets its legal entity at classification. For an XML it is the buyer
(`CessionarioCommittente`) or the seller matched against the space's legal entities, and it
is **certain only if exactly one matches**; otherwise the item waits for a person. A supplier
invoice addressed to nobody the space knows is held, not certain.

### 3.5 Proposals

`proposals` is extended: `document_id` becomes nullable and `inbox_item_id` is added; the
closed `ck_proposals_target_type` check and `ck_proposals_contract_id_required_unless_contract`
are widened for the new target types; `ProposalService.create` and `.accept` dispatch
explicitly per `target_type`, with no `else` routing to work units; an `owner_user_id`
column carries visibility, so a task proposal from a personal mailbox is visible and
notified to its owner only. Such a proposal has no source item or document: it carries neither
`document_id` nor `inbox_item_id`, and the widened check allows that for its target type. «Proposte» (`/app/proposals`) stays the one place a proposal is accepted; an
«In arrivo» item in state `proposed` links to its proposal.

Before C3 and C4 ship, a supplier invoice or a statement is classified and waits in the
inbox with its parsed fields shown; nothing is lost while the modules that file them do not
exist.

## 4. Tasks from personal mailboxes

With the purpose `correspondence` on a personal mailbox, **only when its owner switches it on**, a reader
of § 3.2 reads the bodies of threads already inside the address-filtered sync scope (never
more than the address filter lets in) and proposes **tasks**: a to-do with a due date when a
mail asks for something («mi mandi l'offerta entro venerdì»), and a done task on the customer
or deal when a mail records something that happened. Each is a proposal **only the mailbox's
owner sees** and accepts; nothing is created by itself.

This **breaks the header-only precedent** of REB-223 (DECISIONS, 2026-09-23: proposing
customers from the mail the mailbox sent reads headers only, `format=metadata`, stored
nowhere, `gmail/query.py:338-375`), for this purpose only. `gmail_store_bodies`
(`gmail/models.py:83`) keeps its meaning: bodies are read for the reader and stored only as
that setting already allows.

## 5. Alerts and jobs

Through the reminders catalogue (`reminders/catalogue.py`; detectors are grouped by file and
registered in `reminders/detectors/__init__.py`):

- **`proposal_pending`** is extended: its audience is the owner for a task proposal from a
  personal mailbox, admins as today for the rest;
- **`connection_failing`** is extended to cover IMAP mailboxes (`failing` past its ladder,
  `auth_failed`), to the owner of a personal mailbox and to admins for a space one;
- **`inbox_item_waiting`** is new: an item waiting for a person for more than N days
  (default 3), to its uploader, or to the admins who see it when it has none;
- a duplicate that disagrees with its original (same key, different amount).

The statement that should have arrived and did not is C4's alert.

## 6. Surfaces

- **Web**: «In arrivo» in the «Da fare» section of the sidebar, with the number of items
  waiting; a list by state and kind, and the item's panel showing the file, where it came
  from, what was read, the link to its proposal and the action that files it. On the phone,
  the «+» gains «Carica un documento» (camera or file). Settings: «Caselle email» under
  «Collegamenti», and the model's settings next to the hub-style «Modelli AI». A new surface:
  brief and mockups before markup (frame § A7).
- **REST and MCP**: **bytes never travel over MCP** (`apps/mcp/.../tools/families/documents.py:27-37`):
  upload is web and REST only. MCP lists and reads items (metadata and extracted text, the
  text in `FREE_TEXT_TOOLS`, `provenance.py:28`) and files them. The new tools stay within
  the tool list ceiling (DECISIONS, 2026-10-27, REB-813).

Frame A8 classification, tool by tool:

| Act | Classification |
|---|---|
| list and read items and mailboxes | read only |
| upload | web and REST only, no MCP tool |
| classify: file an item as a kind, discard, mark duplicate | collaborator acts an agent may do (additive; filing creates a proposal or hands to a module) |
| accept or reject a proposal | person only (A4, REB-806) |
| confirm a sales invoice import | behind `mcp_full_access` and `full_access` (DESTRUCTIVE) |
| connect or disconnect a mailbox, set purposes, pause, resume, backfill, the task-from-mail switch | person only, like `update_gmail_settings`, `sync_gmail`, `backfill_gmail` (`agent_actions.py:211-215`) |
| model settings | an admin person's act |

## 7. What is tested

The query guards per purpose (no Gmail list query without its clause, by AST and by request;
IMAP fetching headers and BODYSTRUCTURE only, never a body, an attachment only of an
accepted type); a `.p7m` opened, DER and base64; a ZIP unpacked into items pointing at it,
with the limits of `expand_uploads`; a PEC envelope unwrapped and its `daticert.xml` ignored;
`_MT_` files discarded with a reason; the same invoice by three roads ending as one item and
two duplicates, an XML after a PDF-derived record updating it, and a discarded hash staying
discarded; a sales XML routed to the issued review and not confirmed without a person, a
supplier XML with one matching buyer certain and with none held; a PDF producing a proposal
and nothing else, by either reader, and the model off by default; a personal mailbox's
task proposals visible and notified to its owner only, not even to an admin; an IMAP mailbox
that fails authentication showing `auth_failed` and keeping what it had; a host that resolves to a non-global address
refused and a plaintext port refused; a token refused when connecting a mailbox; the purpose
`billing` refused on a personal mailbox, which yields no inbox item even from a message with an
accepted attachment; an unclassified item visible to its uploader and to an unscoped admin and
invisible to a collaborator who did not upload it; a space mailbox's items kept when it is
disconnected; an IMAP-only space not reading
«not configured»; the existing Gmail tests unchanged.

## 8. Not in this design

Reading statements, matching movements and the `statements` purpose (C4); the supplier
entity, the passive invoice record and the IVA registers (C3); Microsoft 365 OAuth and
space-owned Gmail OAuth; connectors to intermediaries' APIs; the SdI as an accredited
channel; automatic access to the «Cassetto fiscale»; sending mail from a space mailbox.

## Cards

Filed once the design is approved: the inbox model and the upload road (XML, `.p7m`, ZIP,
PDF) with deduplication and the nginx locations; classification and the proposal changes
(§ 3.5); the brief for «In arrivo» and «Caselle email»; the inbox page; the `mailboxes`
model with owners and purposes, wrapping today's Gmail accounts, and the `mail-sync`
rename; the IMAP and PEC connector with the new key and its health states; the `billing`
intake; PigroCRM's own model and its settings; tasks proposed from personal mail; the alerts.
