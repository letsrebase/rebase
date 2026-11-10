# The accountant export

Status: approved by Lorenzo on 2026-11-09. Card: REB-966. Project: «Hand the accountant a
clean period export» (P-REB-87). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A2, A5, A6,
A8 and C8. Reads what C2 to C7 produce. Fiscal and labour facts below are «to verify» with the
accountant and are collected in frame § A5.

## Why

The accountant keeps the books (frame § A6): «il commercialista resta e pigro serve a noi per
avere un'idea in tempo reale di tutto». What PigroCRM gives the accountant today is the year
register CSV for issued invoices (`analytics/year_csv.py`, `GET /api/analytics/year/csv`,
REB-757), a contract's day register CSV and a deal's time report XLSX. Once supplier invoices,
IVA, bank movements, taxes and payroll live in PigroCRM, the accountant needs them too, for a
period, in one place, without anyone collecting files by hand.

## 1. What a period export contains

For one legal entity and one period (a month, a quarter, a year). An «all entities» export is
described below.

| Part | From | Format |
|---|---|---|
| sales register | C3's IVA sales register (C3 § 6): issued invoices and credit notes as such (C3 § 3), annulled numbers and declared gaps listed | CSV |
| purchase register | C3's IVA purchase register (C3 § 6): registered purchases | CSV |
| IVA settlements of the period | C3 § 6 | CSV |
| integration documents (TD16 to TD19, «to verify») | C3 § 2 and § 7 | their XML, and a CSV list |
| every invoice, issued and received | the stored FatturaPA files and their PDFs | the original files |
| receipts and expenses | purchases of kind `receipt` and `expense`, expense notes (C7, which become purchases of kind `expense_note`) | the original files and a CSV list |
| bank movements with what each settled | C4 § 3 | CSV |
| open items at the period's end | due items not settled (C4 § 1) | CSV |
| taxes paid and F24s | C6 § 2 | CSV, and the uploaded F24 receipts |
| withholdings paid for others | C3 § 2 and § 5, C5 § 4 | CSV, the figures each recipient's certification needs (to verify) |
| payroll parts (opt-in, § 4): payslips, payroll F24s, payroll withholdings | C7 § 2 | the original files and CSV |

The sales and purchase registers carry the VAT columns: rate, nature, taxable amount, VAT and,
for purchases, the deductible share. They are by `iva_period`. The year register CSV stays as it
is, for its own use.

Every CSV follows the conventions the year register already uses (`year_csv.py`): byte order
mark, semicolon, CRLF, Italian amounts, formula triggers defused. Identifiers stay text cells
written as `="..."`, which spreadsheets read as text; this is kept as is, and the formats of
specific accounting software remain separate cards when an accountant asks for them, reading
the same data.

The bundle is one ZIP with a short index file naming each part and its row counts.

### Which date places a row in the period

- IVA parts (the two registers, the settlements, integration documents): by `iva_period`.
- Files (invoices, receipts, F24 receipts): by document date.
- Bank movements: by settlement date.

A monthly export of a quarterly IVA entity carries the registers of the month, and the
settlement only when the quarter closes in that month.

### Which parts exist

Parts follow modules and packs (frame § A2). A part whose module is off, or that does not apply
to the entity's pack (no IVA settlement for a flat-rate entity, no IVA register for `non-it`),
is omitted, and the index says so for each omitted part.

### Shared purchases

An entity's export leaves out shared purchases (those with no legal entity). An «all entities»
export, available only to admins whose scope is not limited to one entity, carries them in a
section of their own.

## 2. Is the period ready?

Before export, a checklist says what would make the export incomplete, and the export carries
a snapshot of it in its index:

- inbox items of the period still waiting (C2);
- purchases without their document, or proposals not accepted;
- invoices with no stored file (listed by number, also in the index);
- bank accounts with a statement missing or a gap (C4 § 2), movements not matched;
- payslips or F24s of the period not received (C7), when payroll parts are requested;
- an IVA period not closed (C3 § 6, the IVA period lock).

An export with open points is allowed; it says so.

## 3. Delivery, storage and retention

Every export route is `require_admin`: an admin whose scope covers the legal entity. There is
no accountant role and no invitation path for the accountant: a `readonly` member sees the whole
entity's CRM, which is more than an export.

- **Download** from «Esporta per il commercialista» under «Amministrazione», by an admin.
- **A share link** for one export: created by an admin, opened without an account, so the link
  is the only credential and its controls are part of the design: a 256-bit random token,
  stored only as its hash; a default lifetime of 7 days (at most 30); a download count and an
  audit row per opening (when, from which address), shown to the admin who made it;
  one-click revoke; served with `Referrer-Policy: no-referrer` and `Cache-Control: no-store`,
  and the token kept out of the access logs (the nginx location logs the path without it).
  This is how the file reaches the accountant.
- **Never payroll on a plain link.** An export with payroll parts, and the «variazioni paghe»
  export, are delivered through the Drive folder or a link that also asks for a one-time code
  sent to the recipient's address the admin named; a plain link for them is refused.
- **The space's Drive folder**: «write to Drive» means the space's storage when its backend is
  Google Drive (`packages/core/src/pigrocrm/core/storage/factory.py:70-94`), under
  `commercialista/<entity-slug>/<period>/<timestamp>/`, with lowercase keys. With local storage
  there is no Drive delivery, and the page says so.

An export is a stored artefact. It is a row (legal entity, period, parts, built by, built at,
checklist snapshot) with its files under a storage prefix of its own, not `documents`. File
names carry a timestamp, so a rebuild never overwrites the earlier export. When a bundle would
exceed the size a single `put` takes, it is split into several ZIP parts; the limit is taken as
100 MiB, the cap the upload path already uses (`invoices/import_upload.py:55`), «to verify»
against the storage backends. Exports are kept for 13 months, then deleted, leaving a trace.

## 4. Payroll parts and the payroll consultant

Payroll parts (payslips, payroll F24s, payroll withholdings) are opt-in per export and only
for an admin. A separate export, «variazioni paghe», is for the payroll consultant: staff
changes, expense notes to reimburse in the payslip and, if C7 adds them, absences. It is also
admin only, and is delivered as the previous paragraph says for payroll parts.

## 5. Permissions and MCP

An export contains every fiscal figure of a legal entity.

| Action | Who |
|---|---|
| build an export, read the checklist, create a share link, get a download path | admin whose scope covers the entity; agent with `full_access` |
| write it to Drive | admin person |
| open a share link | anyone holding it, until it expires or is revoked |

Tools, all admin and agents with `full_access`, in `privileged.py`:

| Tool | Does |
|---|---|
| `build_accountant_export` | builds an export (or the «variazioni paghe» export) and stores it |
| `get_export_checklist` | the readiness checklist for an entity and period |
| `get_export_download_url` | a relative REST path to download the export, like `get_invoice_xml_url`; never bytes over MCP |
| `create_export_share_link` | a time-limited link for one export |

Their service methods go in `_FORBIDDEN` and in `FORBIDDEN_SERVICE_CALLS`, and the
byte-returning one in the `_BYTE` block of `test_mcp_surface_coverage.py`, as the rule there
requires.

## 6. What is tested

The bundle's parts and index for a quarter with every kind of document; the CSV conventions
shared with the year register; the VAT columns, annulled numbers and gaps in the registers;
the date rules (a monthly export of a quarterly entity); omitted parts for a flat-rate and a
`non-it` entity; shared purchases only in the «all entities» export; invoices with no file in
the checklist; split into parts; the 13-month deletion; a rebuild not overwriting; the
checklist listing each open point; every route refusing a non-admin and an admin outside the
entity's scope; a revoked and an expired share link; the MCP tools refusing a token without
`full_access` and returning a path, never bytes.

## 7. Not in this design

A double-entry journal (frame § A6); importers for specific accounting software; sending the
export by mail.

## Cards

Filed once the design is approved: the purchase register and settlement CSVs; the bundle and
its index; storage, parts and retention; the readiness checklist; delivery to Drive; the share
link; payroll parts and the «variazioni paghe» export; the MCP tools; the brief for the export
page; that page.

### Built (REB-1011)

The bundle and its index exist as `core/accountant_export/`, behind `GET
/api/accountant-export` (an admin; `all_entities` for an unscoped one). Parts are
`PartDefinition`s in `registry.py`; a project that ships a source adds its definition and
removes its row from `UNAVAILABLE`, and the ZIP and the index carry it. Today: the sales
register with VAT columns, the annulled and unused numbers, the issued invoices' files, the
received invoices' files, the receipts and expenses with their files, the open due items at
the period's end and the payments booked in it. Everything else in § 1 is listed in the index
as «non disponibile». A sale is placed by its issue date, which is not called an IVA period
until the frame's A5 row on the IVA period date for sales is verified; a declared gap in
the period that holds the next number after it. Storage, retention, the checklist, the share
link and the MCP tools stay their own cards. In a space with a single legal entity the shared
purchases are that entity's (`PurchaseService` reads «shared» that way there), so its export
carries them; with several, only the all-entities export does.
