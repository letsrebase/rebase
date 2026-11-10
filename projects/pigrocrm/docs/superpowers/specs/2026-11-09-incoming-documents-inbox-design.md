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

**As built (REB-971, the first milestone: store, hash, deduplicate and expose).** Choices the
design left to the builder, and what a reader of `core/inbox/` would otherwise have to infer:

- **Checks.** `filetypes.py` holds the allowlist: the extension names the type and the bytes
  must carry its signature (xml starts with `<`, p7m is DER or Base64 of a DER sequence, PDF
  `%PDF-` in the first KiB, PNG, JPEG, XLS (OLE2), XLSX (a ZIP with `[Content_Types].xml` and
  `xl/`), CSV (no NUL byte, or a UTF-16 mark)). `camt053` is an XML told by its namespace. The
  type and the content type of a download come from the allowlist, never from the client.
- **`.p7m`.** `p7m.py` reads it with `asn1crypto` (DER, BER with indefinite lengths, Base64 with
  or without PEM armour, a counter-signed envelope up to four deep); a file named `.p7m` must
  start like an envelope (a SEQUENCE whose length accounts for the file) or it is refused.
  Names containing `_MT_` are discarded as SdI metadata only for XML and `.p7m` files. The signature is **not
  verified**: the XML inside is read either way. An envelope that cannot be opened is kept
  as kind `other` with the reason.
- **Keys.** `content_sha256` is the hash of the document the file carries (the XML inside, for
  a `.p7m`), and `natural_key` is the digest of issuer tax id, number, date and document type
  of every invoice of a FatturaPA (a `lotto` is one key). One key for every FatturaPA: a sales
  invoice's `(legal entity, year, number)` needs the legal entity, which classification
  decides, and it names the same invoice. Neither is unique in the database, since a unique
  index would fail, with a 500, a person limited to some legal entities on the file of an item
  they cannot see; the service takes an advisory lock and looks the original up among what the
  caller can see, so two people sending one file each have an item, and an unscoped administrator
  sees both.
- **ZIP.** Opened with `expand_uploads` per archive. The ZIP is the parent item with state
  `filed` and kind `archive` (it became its entries and nobody has anything left to do with it,
  so it is never «waiting»). An entry the inbox does not take, or whose bytes disagree with its
  extension, is a discarded item with its reason and no bytes; a ZIP inside a ZIP is kept and
  discarded; a ZIP that is itself a duplicate of an opened one is one duplicate and is not opened
  again (the same ZIP sent loose after being nested is opened). A loose
  file is not held to the 10 MiB of an entry, only to `SIZE_MAX`.
- **All or nothing.** A request, or a loose file, the inbox does not take is a 422 with the
  reason and nothing written; once the files are accepted they are written in one transaction.
- **Bytes of a duplicate** are kept only when they differ from its original's: a signed `.p7m`
  is not the bare XML it signs, and which one a person wants is not for the inbox to say.
- **Access.** Row-level policy `legal_entity_scope` on `inbox_items` (migration 0074): the
  rows of the actor's legal entities and the ones the actor uploaded. The rule that an
  unclassified item is an unscoped administrator's and its uploader's alone is a role, which a
  policy cannot read, so `inbox/access.py` adds it to every read.
- **Surfaces.** `POST /api/inbox/upload` (multipart, field `file` repeated; the role is checked
  before the body is read; `upload_inbox_file` is a `FULL_ACCESS` action, so a token reaches it
  over REST only with `full_access`), `GET /api/inbox/items`, `/items/{id}` and `/items/{id}/file`.
  MCP has `list_inbox_items` and `get_inbox_item` and nothing else: metadata, never bytes, and
  never the storage key. Extracted text for the tools comes with the classifier.
- **Not here:** the classifier, the legal entity, proposals and the page (later cards).

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

**As built (REB-1014, the model and the rename).** Choices the design left to the builder:

- **Tables.** `mailboxes` (`core/mailboxes/models.py`, migration 0087) holds the owner
  (`owner_user_id`, null for the space), the provider (`gmail` or `imap`) and, for Gmail,
  `google_account_id`, unique: the account is wrapped 1:1 and keeps its credential, consent window,
  status, last error and the unique-per-user and Drive-sibling rules, so the Gmail tests did not
  change. A Gmail mailbox holds nothing its account holds; `status` is stored only for a mailbox
  with no account (IMAP, REB-1015), and a check keeps the two apart. A person has one Gmail
  mailbox (a partial unique index) and a Gmail mailbox is always personal.
- **Wrapping at the insert.** An `after_insert` listener on `GoogleAccount` makes the mailbox and
  its `correspondence` purpose, so no way of creating an account leaves one unwrapped; migration
  0087 wraps the existing ones. Rows go with the account (`ON DELETE CASCADE`).
- **One watermark per mailbox and purpose** is `mailbox_purposes.sync_watermark`, except that the
  `correspondence` watermark of a Gmail mailbox is still `GoogleAccount.sync_watermark`, which the
  Gmail sync reads and writes; its purpose row's own is null. `MailboxRepository.purpose_watermark`
  is the one place that knows it.
- **`billing` on a personal mailbox is impossible in the schema.** `mailboxes.space_owned` is
  generated from the owner and copied onto the purpose row through a composite foreign key, and a
  check refuses `billing` where the copy is false. `MailboxRepository.inbox_sources` is the
  mailboxes holding `billing`; no personal mailbox can be one (REB-960).
- **Health.** `mailboxes/health.py` maps Gmail's statuses onto `active`, `failing`, `auth_failed`
  and `disconnected` (`expired` and `revoked` to `auth_failed`); Gmail records no transient state, so
  a Gmail mailbox is never `failing`. The Gmail banner keeps its four reasons.
- **The job.** `gmail-sync` is `mail-sync`: `SCHEDULED_JOBS`, the `reminder_job_run` row (renamed by
  0087, so a space keeps its last run), the log lines, the panel and the runbooks. `pigrocrm
  gmail-sync` stays as an alias of the same command, writing the `mail-sync` clock, until the hosts'
  crontabs say `mail-sync`. A space with a mailbox that is not Gmail reads «configured» and a run
  that found only those exits 0. Reading anything but Gmail is REB-1015 and the `billing` read is
  REB-1016: this change creates no IMAP mailbox and reads none.

**As built (REB-1015, the IMAP and PEC connector).** Choices the design left to the builder:

- **Where it lives.** `mailboxes/imap_client.py` is the only module that knows an IMAP socket exists
  (`imaplib`, the BODYSTRUCTURE parser, the TLS and address rules); `imap_service.py` connects,
  re-keys and disconnects; `imap_sync.py` is one cycle of one mailbox and what it does to its health;
  `credential.py` seals and opens the password. A PEC is an IMAP mailbox: nothing in the connector
  tells them apart, and reading the SdI envelope is REB-1016's.
- **The columns.** The `mailboxes` row of an IMAP mailbox holds its address, host, port, security,
  user name, the sealed password (ciphertext and nonce) and, as health, `status`, `last_error`
  (Italian, never a server line), `last_error_at`, `failing_since` and `disconnected_at`; checks say an
  IMAP row holds its connection (and a password unless disconnected) and a Gmail row none of it, and one
  address is one mailbox per owner (`uq_mailboxes_imap_address`).
- **Key.** `PIGROCRM_MAILBOX_TOKEN_KEY`, derived per registry space with its own HKDF `info`
  (`space_mailbox_token_key`), the root installation undivided. Without it nothing is connected.
- **Security.** `security` is `tls` (implicit TLS) or `starttls`; there is no plaintext value. 143
  needs `starttls` and 993 needs `tls`; a server that does not offer STARTTLS is not logged into. The
  certificate and its name are always checked. The host is resolved once (`resolve_public`), every
  address must be global, and the socket goes to the addresses that were checked.
- **What is asked.** `UID SEARCH SINCE`, then `BODY.PEEK[HEADER.FIELDS (...)]` and BODYSTRUCTURE on a
  mailbox opened read-only. The reader hands `MessageHead`s (headers and the parts the structure
  names) to a sink per purpose; a purpose with no sink is not read and keeps its watermark, so the
  `billing` sink of REB-1016 starts from the mailbox's first day. The watermark is the newest
  INTERNALDATE handed over, the read is `>=` it, and a sink is idempotent.
- **Health.** A read that works is `active`; a transient failure is `failing` (`failing_since` is when
  the run began) and is retried; a refused password is `auth_failed` and is **not tried again** until
  a person types a new one (`change_password`, which tries it first). Disconnecting forgets the
  password and keeps the row and its purposes.
- **Who.** Connecting, re-keying and disconnecting are `require_person` (a token is refused whatever it
  carries; `PERSON_ONLY_ACTIONS`), then an unscoped administrator's for the space's mailbox and a
  writer's for their own. Someone else's personal mailbox is `NotFound`. REST: `GET /api/mailboxes`,
  `POST /api/mailboxes/imap`, `PUT /api/mailboxes/{id}/password`, `DELETE /api/mailboxes/{id}`. There
  is no MCP tool.
- **The job.** `mail-sync` reads IMAP mailboxes beside Gmail ones: a space with IMAP only is
  «configured» and is read. An `auth_failed` mailbox is a refusal line each cycle.

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

**As built (REB-972, classification and the proposals changes).** Choices the design left to the
builder:

- **Where it runs.** `inbox/classifier.py`, after the upload's own transaction (a classification
  never fails an upload, and a failure on one file leaves it unread and its claim released): every certain item is classified at once, and at most five PDFs or photos
  per request go to the model (`POST /api/inbox/classify` reads the oldest unread ones, and
  `POST /api/inbox/items/{id}/classify` one). An item is read once: one with a kind, or not `new`, is
  left as it is; one the model could not read keeps no kind and a sentence, and is read again by the
  next run, at most three times by automatic runs (`read_attempts`; the least tried first, and a
  rejected reading counts as tried out), and by a person's own request always. A paid reading is
  claimed first (`read_started_at`, a committed timestamp, abandoned after ten minutes), so two
  overlapping runs do not pay for one file. The classify routes have their own long-read locations
  in the three nginx files, since a request reads up to five files.
- **Certain data.** A FatturaPA's legal entity is the one that matches (`match_legal_entity`). The
  legal entity as the supplier of every invoice of the file is a `sales_invoice` with that legal
  entity; as the buyer of every invoice (and not the supplier) a `supplier_invoice`, or a
  `credit_note` when every document is TD04, with the legal entity only when exactly one matches;
  addressed to nobody the space knows it is a `supplier_invoice` with no legal entity; a mix, or an
  identifier no legal entity has, is left unclassified with the reason. `camt053`, CSV, XLS and XLSX
  are tagged `bank_statement` by their type. All of them stay `new`: **no classifier writes an
  invoice, a document, a purchase or a cost.** What a FatturaPA states is kept in
  `inbox_items.parsed_fields`, the text of a PDF in `inbox_items.extracted_text` (both deferred
  columns, shown by `get_inbox_item`, which `provenance.py` marks as free text).
- **The issued review.** A sales invoice of the inbox is reviewed with
  `GET /api/inbox/items/{id}/issued-review` (the importer's own `review_upload`, read only, on the
  item's bytes, a `.p7m` as the XML inside) and written with `POST .../issued-import` (its own
  `confirm_upload`, with the same decisions): that is the person's confirmation, behind the
  import's own gates (an administrator, and `full_access` for a token), and the item is
  then `filed`, its bytes deleted.
- **Proposals.** `proposals.document_id` is nullable; `inbox_item_id` and `owner_user_id` are
  added; the target types are `contract`, `work_unit` (which read from a document) and
  `supplier_invoice`, `receipt`, `bank_statement` (which read from an inbox item), each with one
  branch in `ProposalService.create` and `.accept` and none that falls through to the day
  (`ck_proposals_source` holds the two sources apart in the database). The three new kinds carry
  `ReadDocumentFields`; accepting one is refused with the sentence naming the module that does not
  exist yet (C3, C4) and the proposal stays pending: nothing is lost and nothing is invented.
  Rejecting one returns the item to `new` with no kind. A `task` target type and the notified owner
  of such a proposal are §4's card: `owner_user_id` and its visibility rule are here, with no
  producer yet. A contract or a day proposed from an inbox item (not an archived document) needs the
  filing that copies the bytes into a `Document`, which is the filing card's.
- **Visibility.** A proposal is visible as its source is: one with an owner is the owner's alone
  (not an administrator's), one read from an inbox item is visible to whoever sees the item
  (`proposals/access.py`, and in Postgres the `proposals` policy and the `proposal` branch of
  `entity_visible`, rewritten by 0080 to read the item's own policy), the rest as before. A
  proposal the actor cannot see is not found.
- **The agent.** With the model off a PDF stays `new` with no kind, listed by
  `list_inbox_items(classified=false)`; `get_inbox_item` carries its text; `propose_inbox_item`
  makes the same proposal the model would. A photo has no text an agent can read from here.

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

**As built (REB-972, the model).** PigroCRM had no model client, so the seam is the one its other
clients have: `http.HttpCall`, which a test replaces, with `ai/transport.py` (`ModelCall`,
`HttpModelCall` for Anthropic's Messages API and OpenAI's Chat Completions at their own fixed
addresses, no SDK, no host in a setting). Tests pass a fake transport and never a key.
`ai_model_config` (one row, the key sealed like the Google secrets, the daily cap and the two prices
a cap needs, `enabled` default false) and `ai_model_usage` (one row per call, the cost reserved
before the network under an advisory lock and settled to the real tokens, a failed call released)
are read and written only through `ai/gateway.py`; the settings are `GET/PUT /api/settings/ai-model`
and `POST .../test` («Prova»), an unscoped administrator's and never a token's
(`read_`/`update_ai_model_settings`, `test_ai_model` are person only). It cannot be switched on
without the provider, the model, the key, the cap and both prices, nor without
`privacy_acknowledged` in the same request, and the notice (`privacy_notice`) names the provider and
the model and says what is sent (the text of a PDF, the PDF itself when it has none, a photo);
changing the provider or the model switches it off. The model's answer is data: the kind is one of
a closed set, the fields go through `ReadDocumentFields`, and an unusable answer makes no proposal. A
web form for these settings is not built here.

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

**As built (REB-984, the page and the acts on an item).** `/app/inbox` (`features/inbox/`) is the brief's
page: a table with the open item in a 330 px panel from `lg`, cards and a full-screen sheet below, three
tabs (the states each lists are `TABS` in `labels.ts`), filters for the kind, the origin and the company, and
50 rows at a time. The count beside «In arrivo» (the sidebar and the phone's «Altro») and the numbers on the tabs are `total` of a one-item
page per state (REB-991: `GET /api/inbox/items` answers the number of items the filter matches, cursor and limit left
out), so they are exact and the reader's own; «Mostra altri» says how many there are in all. Each item carries its
`amount` and `currency` when known: the total a waiting proposal read, or the sum of a FatturaPA's invoices (the page's
amount column; an item with neither has none). «Leggi i documenti in attesa» is drawn for an unscoped administrator with the
model on, the only reader of `GET /api/settings/ai-model`; nobody else can know the model is on, so nobody else
is offered it (an item's own «Leggi con il modello» follows the same rule). Upload is one request per file, so a
refusal belongs to a file: a refused file stays as a red row until dismissed, a file over 100 MiB never leaves the
browser, and the phone's «Carica un documento» adds without navigating and toasts «Apri». There is no progress bar:
`fetch` cannot report an upload's progress, and the row says «Lo leggo…» while the request, which uploads and reads,
runs.

Three acts on an item that waits, which design § 6 classifies as collaborator acts an agent may do, are
`InboxService.discard`, `mark_duplicate` and `set_kind` (REST `POST /api/inbox/items/{id}/discard`, `/duplicate`
and `/kind`; MCP `discard_inbox_item`, `mark_inbox_item_duplicate` and `set_inbox_item_kind`). Each reads the
item under a row lock with the visibility of § 1, is idempotent for the same request, answers 409 for an item that
is no longer waiting, and records an activity entry (`discarded`, `marked_duplicate`, `kind_set`) on the item, whose
timeline is where «by whom» lives. Discarding needs a reason and keeps the file; marking a copy points at an item
the caller sees that is not a copy itself, and the copies the item had follow it to the new original; saying what an
item is takes one of the kinds of § 1 but `archive` (`sales_invoice` only for an XML or `.p7m`) and may carry the
legal entity, which makes the item visible by the usual entity scope, to a legal entity the caller sees. A
kind a FatturaPA decided is certain data and stays (409), and its company may still be chosen. A proposal waiting on
the item is rejected by a person's act and makes an agent's refuse (REB-806), so an agent never decides one. A file
uploaded again after a discard is a duplicate of the discarded item and stays discarded (§ 3.3; tested). Nothing is
filed: with no module that files a kind, the page words the third act «Indica il tipo…», and the bulk bar
(«Scarta…» and «Assegna all'azienda…») runs the same acts one item after the other.

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
