# Matches and signed contracts: an admin pairs a freelancer with a company, the hub writes both contracts, Documenso collects the signature

Date: 2026-09-23. Status: design approved in conversation by Ivan on 2026-09-23, written
spec awaiting his review. Tracker: REB-387 in `Manage matches and the freelancer
contract in the hub admin` (P-REB-21). Builds on REB-386 (PR #333), which holds the two
contract texts and the pandoc and Typst build that typesets them.

## 0. Why

Ivan, 2026-09-23: «l'idea è che l'admin vada nella sezione dei talenti, effettui il match
con una delle aziende iscritte, inserisca i dettagli necessari per la creazione di
contratto quadro se non già attivo (1 ogni 12 mesi), inserisca i dettagli necessari per
la creazione della lettera di incarico, questi documenti vengono generati, viene inviata
una email al freelance indicando che sono disponibili e che devono essere firmati (una
email a documento) il freelance clicca su pulsante, apre sito dedicato per firma [...]
i documenti firmati vengono inviati via email a tutte le parti (freelance e rebase)».

Today a match lives in two heads and a mailbox. The hub has freelancer cards
(`packages/core/src/rebase_core/models.py:202-256`), company requests (`models.py:259-333`)
and an admin area, but no match, no contract and no signature anywhere in the models,
the migrations (0001 to 0016) or the web app. The contract texts exist since REB-386
as Markdown in `content/contratti/` with a CLI that typesets them; nothing runs them at
request time, and the API image carries neither pandoc nor Typst.

## 1. The decisions, one paragraph each

**(a) Documenso, self-hosted, is the signing site.** Chosen by Ivan over DocuSeal (its
per-document PDF API is a paid Pro feature), a European SaaS (not open source) and a
signature built into the hub (legal weight and PDF sealing on us). Documenso is AGPL,
runs as containers in our stack, and its API v2 covers the whole loop: create an
envelope from a PDF (`POST /envelope/create`, multipart) with the recipient and its
`SIGNATURE` fields placed as percentages of the page, distribute it with
`distributionMethod: NONE` so it sends no mail and returns each recipient's `signingUrl`
(`POST /envelope/distribute`), and call a webhook with `DOCUMENT_COMPLETED` once every
recipient has signed, authenticated by the `X-Documenso-Secret` header. Phase 1 proved all
of it on a self-hosted `documenso/documenso:v2.18.0`, the sealed PDF's download included
(`GET /api/v2/envelope/item/{itemId}/download?version=signed`); what it found is in
`2026-09-23-documenso-probe.md` beside this file, and § 6 and § 7 below follow it.

**(b) The hub orchestrates; Documenso only signs.** The hub generates the PDFs, sends
its own mails through Resend with the rebase look, receives the webhook, stores the
signed PDF and mails it to both parties. Ivan chose this over letting Documenso mail the
freelancer: the mails and the record stay ours, and Documenso stays replaceable.

**(c) Only the freelancer signs.** The documents leave rebase already carrying its data
and the name of whoever signs for it; the contract closes when the freelancer accepts
rebase's proposal (article 1326 of the civil code) and approves the onerous clauses
(articles 1341 and 1342). The hub records which admin generated and sent each document.

**(d) The framework agreement lasts twelve months and renews itself.** Article 9.1 of the
text says so since commit `58f6ece` on PR #333, and the tacit renewal joined the clauses
approved specifically. For the hub, a framework agreement is active from its signature
until someone records a notice or a withdrawal; a new one is generated only when the
freelancer has none active. A newer version of the text does not end the active one
(article 17.3): the page shows that a newer version exists.

**(e) The framework agreement goes first.** When a match needs a new framework agreement,
the letter waits and is generated and sent automatically the moment the framework
agreement is signed, so it can print the framework's real signature date, which it
cites. It is still one mail per document.

**(f) No draft goes out for signature.** The hub may generate a preview of a document
whose text says `status: draft`, with the BOZZA watermark, but refuses to send it to
Documenso. Until Ivan and Lorenzo mark the texts final, the whole flow can be exercised
and nothing can be signed.

**(g) The freelancer sees their contracts.** The member area gets a read-only
«Contratti» section: documents waiting for a signature with a «Firma» button, signed
ones to download, and the framework agreement's state. Added by Ivan on 2026-09-23.

**(h) The client's price never reaches the freelancer's documents.** The letter carries
the fee agreed with the freelancer, prefilled from their day rate (`tariffa_giornaliera`);
the company request's `budget_giornaliero` is never copied into a letter (Ivan,
2026-09-23, REB-386).

## 2. Data model

One migration, `0017`, written defensively like the others (`IF NOT EXISTS`), four new
tables: the three below and `contract_letter_counters` (one row per year, `year` and
`last`, bumped under a row lock so two admins never get the same letter number and a
rolled-back transaction leaves no gap). None of them is synced to the PostHog warehouse, so
`test_warehouse_contract.py` does not change; that is also why the freelancer's tax data
is its own table rather than columns on `freelancers`, which the warehouse does sync.

**`freelancer_fiscal`**, one row per freelancer (`freelancer_id` unique, FK with cascade):
`codice_fiscale`, `partita_iva`, `domicilio`, `pec` (nullable), `updated_by` (admin
user id). Filled by an admin in the match flow the first time and reused afterwards;
editable from the «Match e contratti» page.

**`matches`**: `id` (UUIDv7), `freelancer_id`, `company_id` (the request), the client's
legal data as the letter prints it (`cliente_ragione_sociale`, `cliente_piva`,
`cliente_sede`, prefilled from the latest match with a request of the same company
user), `stato`, `created_by`, `cancelled_at`, timestamps. States:

| `stato` | Means | Enters when |
|---|---|---|
| `bozza` | documents generated, nothing sent | the admin completes the flow's last step |
| `in_firma` | at least one document sent, the letter not yet signed | «Invia per la firma» |
| `attivo` | the letter is signed and the framework agreement is active | the webhook for the letter |
| `concluso` | the engagement ended | an admin closes it |
| `annullato` | abandoned before or during signing | an admin cancels it |

**`contract_documents`**: `id`, `kind` (`quadro` or `lettera`), `freelancer_id`,
`match_id` (null for a framework agreement, which belongs to the freelancer, not to a
match), `numero` (letters only: `YYYY-NNN`, a per-year counter taken at generation),
`text_version` (the `version` of the Markdown's front matter), `data` (JSONB: every
field value the PDF printed, so a document can be regenerated identically), `pdf`
(bytea), `testo_bozza` (true when the Markdown said `status: draft`, so § 1f can refuse to
send it), `stato`, `documenso_id`, `signing_url`, `sent_at`, `signed_at`, `signed_pdf`
(bytea), `notice_at` (framework agreements: a notice or withdrawal recorded), `created_by`,
`sent_by`, timestamps. States: `generato`, `in_attesa` (a letter waiting for its
framework agreement), `inviato`, `firmato`, `annullato`, `disdetto` (framework only).

A framework agreement is **active** when `stato = firmato` and `notice_at` is null. Its
next renewal is the next anniversary of `signed_at`; the last day for a notice is thirty
days before it. The pages compute both; nothing is stored.

PDFs live in Postgres as the CV does (`models.py:203-206`: one place to delete from),
and are served by the same kind of response as `downloads.py`'s `cv_response`.

Every admin action lands in `AdminAction` (`audit.py`) with new kinds for a match created,
documents sent, a document cancelled, a mail resent and a notice recorded, entity type
`matches`.

## 3. Admin screens

**The talent list** (`apps/web/src/pages/admin/lists.tsx:235-456`) gets a row menu,
`DropdownMenu` from `@rebase/ui`, on card rows only (`stato !== 'lead'`), with two
entries: «Crea match» and «Match e contratti». The name keeps linking to the card.

**«Crea match»** is a page, `/admin/freelance/$id/match/new`, in five steps:

1. **Azienda.** Pick one of the company requests (`Company`), searchable by company name
   and contact. A request that is `chiuso` is shown but greyed.
2. **Freelance.** The tax data from `freelancer_fiscal`, prefilled when saved; saving
   here saves them for next time.
3. **Cliente.** The client's legal data, prefilled from the previous match of the same
   company user.
4. **Lettera di incarico.** Every field of `lettera-di-incarico.md`, prefilled where the
   hub knows it: `ruolo` from `figura_richiesta`, `attivita` from `progetto`,
   `data-inizio` from `periodo_da`, `impegno` from `durata`, `luogo` from `remoto` and
   `giorni_presenza`, `compenso` from the freelancer's `tariffa_giornaliera`, the rest
   from `rebase.json`'s defaults (payment at 30 days from the end of the month). The
   company's `budget_giornaliero` is not shown on this step.
5. **Anteprima.** The generated PDFs to open: the letter, and the framework agreement
   when the freelancer has none active. Two buttons: «Salva come bozza» and «Invia per
   la firma». The second says which document leaves now and which waits (1e).

**«Match e contratti»** is a page, `/admin/freelance/$id/contracts`, linked from the row
menu and from the card's header. At the top, the framework agreement: state, signed on,
next renewal and last day for a notice, text version (and «nuova versione disponibile»
when there is one), the original and the signed PDF, and actions «Reinvia email»,
«Aggiorna stato», «Annulla» (before signature), «Registra disdetta» (after). Below, the
matches, newest first: company, dates, state, the letter with its number, state, PDFs and
the same actions, plus «Chiudi match» on an active one. The tax data are editable here.

**The admin MCP server** gets two read-only tools, `list_matches` and `get_match`
(documents included, PDFs as download links rather than bytes). Creating and sending stay
in the UI.

## 4. Member screen

The member area (`/me`) gets «Contratti», visible to a signed-in person who has a
freelancer card: the framework agreement's state and dates, then each letter with its
company and dates. A document in `inviato` shows «Firma il documento», which opens its
`signing_url`; a signed one offers the signed PDF. The routes depend on `MeDep` and only
ever return the caller's own documents; a document belonging to someone else is a 404,
never a 403.

## 5. Generation

The logic of `tools/build_contract_pdf.py` (fields, proposals, the fee and payment-term
checks, the marker count across pandoc, pandoc and Typst) moves into
`rebase_core.contracts`, with the Markdown, the Typst template and `rebase.json` as its
package data. The CLI becomes a thin wrapper over it and keeps `--data`, `--public` and
`rebase.local.json` for previews on a laptop. In the API the signer's real data come from
settings, not from a file (§ 8).

The API image installs the same pins as PigroCRM's (`projects/pigrocrm/Dockerfile.api:12-26`,
`flake.nix:73-83`): pandoc 3.8.2.1 and Typst 0.14.2, plus fontTools as a runtime
dependency of `rebase_core` for the Outfit instances, and it copies the brand's font and
palette. Generation is synchronous (about a second) and happens at step 5 and when a
waiting letter is released by the webhook.

**Signature positions.** The template marks every signature blank with Typst `metadata`;
after compiling, `typst query` returns each one's page and box, which the hub turns into
Documenso's percentages. The framework agreement carries two signature fields for the
freelancer, the contract's and the specific approval's; a letter carries one. The date
of signature is a Documenso `DATE` field on the `data-firma` blank, and `luogo-firma`
prints «firmato elettronicamente». Since rebase does not sign (1c), its signature blank
prints «Documento emesso da rebase il <date of sending>» and the name from
`REBASE_SIGNER_JSON`, so the signed PDF has no empty line where a signature seems to be
missing. The freelancer's fields come from `users` (name, email) and `freelancer_fiscal`;
a letter's `data-contratto-quadro` is the active framework agreement's `signed_at`.

## 6. Signing

**Sending** (`POST /api/hub/matches/{id}/send`, under `/api/hub/` like every admin route): for each document that leaves now,
the hub creates the Documenso envelope with the PDF, the freelancer as the one `SIGNER`
and the fields from § 5, distributes it with `distributionMethod: NONE`, stores
`documenso_id` and `signing_url`, and mails the freelancer through Resend: subject «Da
firmare: contratto quadro rebase» or «Da firmare: lettera di incarico n. 2026-001», one
paragraph, one «Firma il documento» button, the same frame as `magic_link_mail`
(`mail.py:197-244`). The document becomes `inviato`, the match `in_firma`. If Documenso
refuses or times out, nothing is marked as sent and the admin reads why; if only the mail
fails, the document is `inviato` and «Reinvia email» sends it again.

**Completion** (`POST /api/hub/documenso/webhook`, no cookie, `X-Documenso-Secret`
compared in constant time with the setting; an empty or missing header is refused, since
a Documenso webhook saved without a secret sends the header empty): on
`DOCUMENT_COMPLETED` the hub finds the
document by `documenso_id`, downloads the sealed PDF, stores it in `signed_pdf`, sets
`firmato` and `signed_at`, and mails the signed PDF as an attachment to the freelancer
and to the contracts address (`ciao@letsrebase.com`). Then: a signed framework agreement
releases the letters waiting for it (generated now, with its signature date, and sent);
a signed letter turns its match `attivo`. `DOCUMENT_REJECTED` and `DOCUMENT_CANCELLED`
turn the document `annullato` and are shown on the page. An event for a document already
`firmato` is acknowledged and ignored, so a retried webhook does nothing twice. Documenso
retries a failed delivery three times within about 160 ms and never again, and sends a
second event while a slow first one is still being handled, so the transition takes a
row lock (`SELECT ... FOR UPDATE` on the document) and answers fast: the download and
the mails run after the commit, and a delivery the hub missed entirely is recovered only
by «Aggiorna stato». The sealed PDF has one more page than the original, Documenso's
audit certificate.

**Recovery.** «Aggiorna stato» asks Documenso for the envelope's state and applies the
same transition as the webhook, for the day a webhook is lost. «Annulla» cancels the
envelope in Documenso and the document in the hub.

**Mail attachments.** `Mail` (`mail.py:27-34`) and `ResendSender` gain optional
attachments (file name and bytes, sent base64 as Resend's API expects); nothing else
changes for the mails that exist.

## 7. Deployment

Documenso joins the `rebase` compose project (`projects/hub/docker-compose.yml`) as
`documenso` with its own `documenso-db` (postgres:17-alpine), a pinned image tag, its
data under a `${REBASE_DOCUMENSO_DATA_DIR:?}` bind, and a loopback port from the table in
`docs/adding-a-project.md`. The public name is `firma.letsrebase.com` (Cloudflare DNS,
a vhost next to the hub's in `projects/website/deploy/letsrebase.conf`). Its own settings
live in the host `.env`: the auth secret and encryption keys, the public URL, SMTP
through Resend (for its own account mails only, since the hub sends the signing mails),
and the signing certificate as base64 with its passphrase. The certificate starts
self-signed: the seal is valid, but PDF readers mark it as not trusted; a certificate on
Adobe's trust list is a later purchase. The preview uses the same instance through a
separate Documenso user and organisation, with its own API token and webhook: phase 1
showed that two teams under one user do not isolate each other (the production token read
and cancelled a preview envelope). Documenso needs `NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS`
to call the hub by an internal name, the owner's «Signing Complete!» mail switched off
through the envelope's `emailSettings`, and public sign-up disabled.

## 8. Settings

New `REBASE_*` variables, each in `config.py`, `.env.example` and the compose
`x-api-environment` list, as the hub's other variables are:

| Variable | Holds | Empty means |
|---|---|---|
| `REBASE_DOCUMENSO_URL` | the instance's base URL | signing is off: «Invia per la firma» answers 503 with a sentence |
| `REBASE_DOCUMENSO_API_TOKEN` | the team's API token | same |
| `REBASE_DOCUMENSO_WEBHOOK_SECRET` | the webhook secret | the webhook answers 503 |
| `REBASE_CONTRACTS_MAIL` | where rebase's signed copies go | `ciao@letsrebase.com` |
| `REBASE_SIGNER_JSON` | the data of whoever signs for rebase (the `rebase-*` fields) | documents print those fields blank, and sending refuses |

## 9. Testing

Core services are tested against a fake Documenso through the `HttpCall` seam (the root
`conftest.py` blocks the network) and a `RecordingSender`, on the Postgres testcontainer
brought to `head`. The API tests cover each route, the webhook's secret, a repeated event
and the letter released by its framework agreement; new tables join the fixtures'
`DELETE FROM` lists. The web gets vitest cases for the row menu, the five steps, both
pages and the member section. The real render stays in the `contract-pdf` preflight check,
extended to the core module. Phase 3 ends with one document signed end to end on the
preview. Each PR carries the before and after pictures and the video the repository asks
for.

## 10. Phases

Each phase is a Linear milestone in P-REB-21 with its own cards, and lands on main before
the next starts.

1. **Prove Documenso in our stack.** Run it locally and on the preview host; confirm the
   envelope, the fields, `distributionMethod: NONE` with `signingUrl`, the webhook and
   its secret, the sealed PDF's download, a self-signed certificate, and one team per
   environment. Output: a short note in this folder and the compose fragment, not yet
   deployed. Gate for phases 3 and 4.
2. **Match and generate, no signature.** Migration 0017, the core service, the runtime
   renderer with the image changes, the admin routes and MCP read tools, the row menu,
   the five-step flow, the «Match e contratti» page, PDFs downloadable. Needs REB-386
   merged.
3. **Sign.** Mail attachments, the Documenso client, sending, the webhook, the letter
   released by its framework agreement, recovery actions, the member «Contratti»
   section.
4. **Run Documenso for real.** The compose services, the vhost and DNS, the certificate,
   the settings on production and preview, the deploy notes.

## 11. Not here

The company's own contract with rebase (roadmap #286). Reminders to a freelancer who
has not signed (Documenso's reminders are a paid feature; a mail from the hub can come
later). Signing on behalf of rebase (1c). An advanced electronic signature with a
one-time code, which would strengthen the specific approval of the onerous clauses:
worth weighing once Documenso's own options are known (phase 1). REB-339 is superseded
by this design and REB-340 is answered by § 2.
