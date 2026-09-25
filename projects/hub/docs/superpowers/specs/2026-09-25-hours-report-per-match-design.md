# Hours per match: the CRM opens a door for rebase's engagements, the hub reads the report

Date: 2026-09-25. Status: approach and every section below approved in conversation by
Ivan on 2026-09-25 (provisioning when the match turns active, rebase as the customer,
the minimum to log hours, a dedicated token, expected days on the match at eight hours a
day, the reporting clause in the letter, the report per day, week and month with the
invoices, and the same reads over MCP); this record is written for his review, amended
the same day after an independent review of the text against the code (the lock and the
commit order of § 2.3, where the letter's dates live, the timeout, the status codes) and
again after the PR's two reviewers (the deal recovered by a marker, the report fetched in
windows, the mail claimed under the lock, https only). One thing is not approved yet: the
wording of the clause in § 3.8, which Ivan reads on the PR that carries it, before that
PR is marked ready.
Tracker: REB-489 in `Report a match's hours from the freelancer's own CRM space`
(P-REB-42). Builds on the matches spec (`2026-09-23-matches-and-contract-signing-design.md`,
REB-387) and on the simpler-matches design (`2026-09-25-simpler-matches-and-mcp-design.md`,
PR #416), whose branch the hub half of this lands on.

## 0. Why

Ivan, 2026-09-25: «lato monitoraggio ore, voglio che associato ad ogni match si possa
andare a visualizzare report del consuntivato, per farlo, alla creazione del match deve
essere creato un ambiente pigro per il freelance (se non esiste) o nel suo ambiente deve
poi essere create le anagrafiche di riferimento etc e impostato tutto per consentire al
freelance di consuntivare le ore. quindi dentro all'hub admin dal match l'admin ne vede
report».

Three facts of the code shape the answer. The hub knows a PigroCRM space by slug, owner
and date and nothing else: the registry it reads (`rebase_core/pigro.py`, `GET
/api/tenants/`) is read-only by design and says nothing about what a space holds. Every
API of a space authenticates a user of that space, by session or by personal token, so
the hub is nobody inside one and a new door is needed whatever the approach. And the
letter of engagement names the commitment as free text (`impegno`, `unita`), and keeps
its dates and fee only as the page prints them (`ContractDocument.data` holds
`LetteraFields.to_fields()`: «1° ottobre 2026», a JSON number), so a report that
compares hours with «the letter's days» needs numbers that do not exist yet.

## 1. The decisions, one paragraph each

**The CRM opens a door of its own, and the hub keeps two ids.** Two routes on the
CRM's root installation, under a token that is not the registry's: one idempotent call
that gets a match its space, its customer and its deal, and one that answers the hours
on that deal. The CRM stays the owner of its invariants (a space per owner, a deal on a
customer, hours on a deal); the hub learns a slug and a deal id per match and never
opens a space's database, the rule of 2026-09-09. Two approaches were set aside: the hub
orchestrating the CRM's existing APIs (it would still need a cross-tenant credential, and
would learn the CRM's data model), and the CRM pushing hours to the hub (a mirror that
must follow every edit and deletion; not for a first version).

**Provisioning happens when the match turns active.** A match in `bozza` or
`in_firma` is often cancelled, and a Postgres database for an engagement that never
starts is noise on the CRM's server. The moment the letter is signed and the match
becomes `attivo` (`SigningService._confirm_completion`) is the trigger, and nothing
before it.

**The customer in the freelancer's space is rebase.** The freelancer invoices rebase,
never the end client, so the customer row is rebase S.r.l. with the fiscal data the hub
already holds for signing (`REBASE_SIGNER_JSON`), and the engagement is a deal under
it, named for the letter and the end client. Whoever opens their space finds one
customer and one deal per letter, and the numbers on their invoices add up.

**The minimum to log hours: a customer and a deal.** No contract row, no rate card, no
proposal: hours in PigroCRM hang on a deal (`time_entries.deal_id`), and a deal with an
hourly rate and estimated hours is everything the timer, the register and the deal's
own budget page need. The rate is the letter's daily fee over eight hours; the estimate
is the expected days times eight. The end client's rate never travels: the CRM sees the
freelancer's fee and nothing of what rebase bills (REB-386).

**The space is reused when the freelancer owns one, created when not.** «Owns» is the
registry's `owner_email`, matched case-insensitively; with several, the oldest. A
freelancer who is a collaborator in somebody else's space but owns none gets one of
their own. A new space is born exactly as the signup makes one (`TenantService.provision`:
no password, the freelancer its first admin, the defaults, the emitter with their name)
and the CRM's own welcome mail with the link that enters is what tells them; the hub's
mail says where the hours go. Creating a space takes seconds to tens of seconds (a
`CREATE DATABASE`, the whole Alembic history, the defaults): the hub's call allows for
it (§ 3.3), and nothing a person is waiting on runs it.

**Expected days are a number on the match, and so are the letter's dates and fee.**
`giorni_previsti`, an optional integer an admin fills in at «Crea match» next to the
fee, stored on the match and sent to the CRM as `ore_preventivate` at eight hours a day.
Beside it the match keeps `lettera_data_inizio`, `lettera_data_fine` and
`lettera_compenso` as a date, a date and a decimal, written at creation from the same
fields the letter is typeset from: the printed copy in `ContractDocument.data` is for
the page, these are for the CRM and the report. The letter's `impegno` stays free text;
the report says «N giorni previsti, 8 ore al giorno» and measures against it. A match
without an estimate gets the hours and no progress bar.

**Transparency is written where the freelancer reads.** The deal's and the customer's
notes say rebase created them for letter n. X and reads the hours of that deal; the
space's timeline records «rebase» as the actor, not «Sistema»; the member area shows
«Le tue ore su Pigro» under the active letter with the same sentence; the mail says it
again. The letter itself carries the obligation (below), so nobody learns it from a
note.

**The letter carries the reporting clause.** One new paragraph in
`lettera-di-incarico.md`, a bump of `text_version`, and a letter written before the
bump keeps the text it was signed with. The wording is § 3.8; Ivan reviews it before
the merge, since it is a contract.

**The report is answered live by the CRM and rendered by the hub.** Every opening of
the report asks the CRM for the deal's entries per day, with the invoice each entry sits
on, and the hub groups by ISO week and by month, sums, and compares with the expected
days. The hub caches nothing: an hour the freelancer deletes is gone from the next
opening, and there is no second copy of the register to keep honest.

**A CRM that does not answer is a state on the match, retried by the sweep; a CRM
that refuses is a state the sweep leaves alone.** Linking is attempted right after the
match turns active, in the background. A refusal of the connection, a timeout or an
answer that is not the shape leaves the match `errore` with the sentence, and the
existing `contracts-sweep` (every ten minutes) retries every `da_collegare` and
`errore` match. A `409` or a `422` from the CRM (the deal was deleted in the space, a
body the CRM will never accept) leaves the match `rifiutato` with the CRM's own
sentence: the sweep skips it, «Riprova» on the match tries again when an admin decides
to. Nothing blocks the signature: the document is `firmato` and the match `attivo`
whether or not the CRM answered.

**The same reads and the retry exist over the hub's MCP server.** `get_match_report`
and `link_match_to_pigro`, calling the same core service the admin API calls, for an
admin with a token, as every hub tool does since REB-213.

## 2. The CRM side: the engagements door

### 2.1 Settings

`PIGROCRM_ENGAGEMENTS_TOKEN` (`Settings.engagements_token`, `repr=False`), read from the
environment only. Empty, the default, means the two routes below do not exist (404,
exactly as `GET /api/tenants/` behaves without `registry_token`): a self-hosted
installation exposes nothing new. The registry token is not widened: it keeps listing
spaces and answering the member question, and a hub that holds only it cannot create a
space or read an hour. Both tokens are generated with `openssl rand -hex 32` and set on
the CRM's host and the hub's (`REBASE_PIGRO_ENGAGEMENTS_TOKEN`, § 4). The door also
needs `PIGROCRM_PUBLIC_URL`, which production and the preview already set for their
mails: without it the door answers `503` («PIGROCRM_PUBLIC_URL non configurato»)
rather than hand the hub a relative link.

### 2.2 The registry table

`rebase_engagements`, in the registry database beside `tenants` and the identity tables,
on `TenantsBase` so `ensure_tenants_database`'s `create_all` creates it at the first boot
after the deploy. `tenants/database.py` imports the models module by its full path
(`pigrocrm.core.engagements.models`), the way it imports `identity.models`, and the
`engagements` package's `__init__` exports nothing: its service imports
`tenants.service`, which imports `tenants.database`, and a re-export from `__init__`
would close that circle against a half-initialised module.

| column | type | notes |
|---|---|---|
| `id` | UUID, primary key | the registry's own key, as every `TenantsBase` row has (`PrimaryKeyMixin`) |
| `match_id` | UUID, unique, not null | the hub's match id, the idempotency key |
| `tenant_id` | UUID, FK `tenants.id`, indexed | the space |
| `customer_id` | UUID, nullable | the customer «rebase» in that space |
| `deal_id` | UUID, nullable | the deal for the letter; `NULL` while step 4 of § 2.3 has not completed |
| `created_at`, `updated_at` | timestamptz | |

The registry has no Alembic history (`create_all`, `tenants/database.py`); the table is
new, so nothing existing is altered.

### 2.3 `PUT /api/rebase/engagements/{match_id}`

Bearer `engagements_token`, on the root installation (no space prefix), like
`/api/tenants/`. The body:

```json
{
  "freelancer": {"email": "ada@example.com", "nome": "Ada", "cognome": "Lovelace"},
  "lettera": {
    "numero": "3/2026", "ruolo": "Backend developer", "azienda": "Acme S.r.l.",
    "data_inizio": "2026-10-01", "data_fine": "2026-12-31",
    "compenso": "400.00", "giorni_previsti": 40
  },
  "rebase": {
    "ragione_sociale": "rebase S.r.l.", "partita_iva": "01234567890", "codice_fiscale": "...",
    "indirizzo": "...", "pec": "...", "codice_sdi": "..."
  }
}
```

`data_fine`, `giorni_previsti`, `partita_iva`, `codice_fiscale`, `indirizzo`, `pec` and
`codice_sdi` may be null. `nome` and `cognome` take up to 120 characters (the hub's
`NAME_MAX_LENGTH`), `indirizzo` 255, `partita_iva` exactly eleven digits or null,
`codice_sdi` exactly seven characters or null: the CRM's own customer rules
(`CustomerService._check_fiscal`), applied at the door so a value the space would refuse
is a `422` with the field's name, never a customer that half exists. The answer, `201`
on the first call and `200` on every later one with the same `match_id`:

```json
{
  "slug": "ada-lovelace", "url": "https://pigro.letsrebase.com/ada-lovelace/app/",
  "customer_id": "...", "deal_id": "...",
  "deal_url": "https://pigro.letsrebase.com/ada-lovelace/app/deal/<id>",
  "spazio_creato": true, "creato": true
}
```

`url` and `deal_url` are built from `PIGROCRM_PUBLIC_URL`; the deal's page is the SPA's
`/<slug>/app/deal/<id>` route.

What the service (`pigrocrm.core.engagements.EngagementService`, in core, called by the
router and by nothing else yet) does, in order:

1. **Lock by address, for the whole call.** A session-level advisory lock,
   `pg_advisory_lock(hashtext(:email))` with the address lowercased and stripped, taken
   on a connection of the registry engine that the service keeps open until the end and
   releases with `pg_advisory_unlock` in a `finally`. Not a transaction-level lock: the
   registry session commits twice below (`TenantService.provision` commits on its own,
   and the row is committed before the space is touched), and `pg_advisory_xact_lock`
   would let go at the first of them. Two letters of the same freelancer activating
   together therefore create one space and not two, and two retries of the same match
   wait for each other through the whole of steps 2 to 6.
2. **An engagement row already there.** With `deal_id` set: the deal is read in the
   space; alive, the row's ids are answered (`creato: false`); gone (soft-deleted or
   missing), the answer is `409` with «Il deal di questa lettera è stato eliminato nello
   spazio.», and nothing is recreated behind the freelancer's back. With `deal_id`
   `NULL`, a previous call stopped between steps 3 and 6: the call resumes at step 4
   in that row's space.
3. **The space.** The registry row whose `owner_email` equals the address, case-insensitively,
   oldest first; none means `TenantService.provision` with `TenantSignup(slug, nome, email,
   membro=True)`, `nome` being `f"{nome} {cognome}"` cut to the signup's 200 characters,
   the slug from `slugify(f"{nome} {cognome}")` with `-2`, `-3`... in place of its tail
   while `availability` says the name is taken or reserved. Then exactly what the signup
   route does after provisioning (the magic link and the welcome mail,
   `MagicLinkService.request` and `welcome_mail`), moved out of the router into a
   function both call, so a space born here and a space born at the signup are told the
   same way. The engagement row is written with the `tenant_id` and no deal yet, and
   committed: a failure from here on leaves a row that says «space found, deal
   missing», which step 2 resumes.
4. **The customer «rebase»**, in the space, as `Actor.rebase()` (§ 2.5): found by
   `CustomerRepository.match_by_fiscal_id` on the VAT number when the body carries one,
   else by `CustomerRepository.find_by_name`, an exact query over the live customers
   (the list method is a paginated trigram search and is never used for this); created
   if missing with the fiscal fields of the body and the note «Creato da rebase
   per la lettera n. 3/2026. rebase legge le ore dei deal di questo cliente per
   rendicontare gli incarichi.» An existing customer is left as it is: its fields are
   the freelancer's to edit.
5. **The deal**, in the space: name `deal_name(numero, ruolo, azienda)`, that is
   `Lettera n. 3/2026 · Backend developer per Acme S.r.l.` with the role cut to 80
   characters and the company to 100, so the longest inputs stay under the column's 255;
   on that customer, `tariffa_oraria = compenso / 8` (`Decimal`, the column's six
   places), `ore_preventivate = giorni_previsti * 8` or null, `data_chiusura_prevista =
   data_fine`, `owner_id` the space's admin whose email is the freelancer's, the default
   open stage, and the note «Creato da rebase per la lettera n. 3/2026 con Acme S.r.l.
   rebase legge le ore di questo deal per la rendicontazione al cliente.» whose last
   line is the marker `rebase:match=<match_id>`. Before creating, the live deal under
   that customer whose note carries this match's marker is reused
   (`DealRepository.find_by_marker`, an exact query, never the paginated list): it is
   the deal a previous call created and failed to record (a failure between steps 5
   and 6). A deal with the same name and no marker is somebody else's and is left
   alone: the name is a label, the marker is the key.
6. **The row completed** with `customer_id` and `deal_id`, committed, answered.

`422` for a body that does not validate, `401` for a wrong or missing bearer, `404` when
the token is not configured, `503` when `PIGROCRM_PUBLIC_URL` is empty or the space's
database cannot be created or reached. The route is not throttled per client: the only
caller holds the token.

### 2.4 `GET /api/rebase/engagements/{match_id}/report`

Same bearer. Query `da` and `a` (`YYYY-MM-DD`), both optional: `da` defaults to the
deal's creation date, `a` to today (`pigrocrm.core.db.today_local()`, the CRM's own
clock), `a` before `da` or a span over 800 days is a `422`. The answer:

```json
{
  "slug": "ada-lovelace", "deal_url": "...",
  "deal": {"id": "...", "nome": "...", "tariffa_oraria": "50.000000",
           "ore_preventivate": "320.00", "stato": "in corso"},
  "giorni": [
    {"data": "2026-10-01", "ore": "8.00", "descrizione": "Setup", "fatturabile": true,
     "fattura": {"id": "...", "tipo": "fattura", "anno": 2026, "numero": 12,
                 "stato": "emessa", "stato_pagamento": "da_incassare", "data": "2026-10-31"}}
  ],
  "totale_ore": "96.00", "ore_fatturate": "80.00", "ore_non_fatturate": "16.00",
  "fatture": [{"id": "...", "tipo": "fattura", "anno": 2026, "numero": 12, "stato": "emessa",
               "stato_pagamento": "da_incassare", "data": "2026-10-31", "ore": "80.00"}]
}
```

One row per time entry (a day with two entries answers two rows; the hub sums), read
through `TimeEntryService.list` with `deal_id`, `da`, `a`, paged to the end, each entry's
`invoice_line_id` resolved to its invoice (`invoice_lines.invoice_id`, then `invoices`:
type, year and number, state, payment state, `data_emissione`) in one query per report.
`ore_fatturate` follows the CRM's own definition of billed (`billed_entry_ids`: on a
line of a `fattura` that is `emessa` and not deleted), so it agrees with the deal's own
summary; an entry on a draft, a proforma or an annulled invoice still shows that
invoice in `fattura`, and counts as not billed. `stato` is `DealTimeSummary.stato` as
the deal's own page shows it. A row whose deal is gone answers `409` with the sentence
of § 2.3 step 2; a `match_id` with no row answers `404`. Nothing else of the space is
readable through this door: not the customers, not the other deals, not the invoices
beyond the ones these hours sit on.

### 2.5 The actor «rebase»

`ActorType` gains `"rebase"` and `Actor.rebase()` (no id, role `admin`, `full_access`
false), used for everything § 2.3 writes in a space. The word a person reads lives in
the SPA: `apps/web/src/components/Timeline.tsx`'s `ACTOR_META` names `system`
«Sistema», and gains a `rebase` entry that names it «rebase», so the deal's own history
reads who did what without opening the note. Core's `Activity.actor_type` is a plain
`String(10)` with no check, so no space's schema changes. `Actor.system()` stays what it
is, for the CRM's own jobs.

### 2.6 What is not touched

No per-space migration: a space's schema is unchanged, which is what lets this ship
without the per-space `alembic_version` check a schema change needs (ORB-189). No
change to the MCP server of the CRM: an agent of the freelancer sees the customer and
the deal as any other. No change to the registry token's routes.

## 3. The hub side

### 3.1 Data model

New columns on `matches`, migration `0021_match_pigro_link.py` with `down_revision`
`0019` on the branch (`0020_campaigns.py` is PR #407's, on its own branch: whichever
lands second re-points its `down_revision` at the other before merging, the lesson of
two open PRs that both took `0006` on 2026-09-15). The migration also backfills
`pigro_stato = 'da_collegare'` on every match already `attivo`, so an engagement signed
before this ships is linked by the first sweep after the deploy:

| column | type | meaning |
|---|---|---|
| `giorni_previsti` | integer, nullable, `CHECK (giorni_previsti BETWEEN 1 AND 366)` | expected billable days, an admin's estimate |
| `lettera_data_inizio` | date, nullable | the letter's start, as `MatchCreate.lettera.data_inizio` came in |
| `lettera_data_fine` | date, nullable | the letter's end, when it has one |
| `lettera_compenso` | numeric(7, 2), nullable | the letter's daily fee |
| `pigro_stato` | varchar(20), nullable, `CHECK IN ('da_collegare', 'collegato', 'errore', 'rifiutato')` | `NULL` until the match turns active |
| `pigro_slug` | varchar(32), nullable | the space |
| `pigro_deal_id` | UUID, nullable | the deal |
| `pigro_url` | text, nullable | the deal's page, as the CRM answered it |
| `pigro_linked_at` | timestamptz, nullable | when `collegato` was reached |
| `pigro_attempted_at` | timestamptz, nullable | the last attempt, for the sweep's log and the admin's eye |
| `pigro_errore` | text, nullable | the sentence of the last failure or refusal, cleared on success |
| `pigro_mail_sent_at` | timestamptz, nullable | when the freelancer's mail (§ 3.7) was accepted by the provider |

The three `lettera_*` columns are written by `MatchService.create` from `data.lettera`,
where the ISO date and the `Decimal` are at hand; a match created before 0021 has them
`NULL`, and `payload` (§ 3.3) reads its letter's printed data instead. `MatchRead`
carries `giorni_previsti`, the three `lettera_*` values and the eight `pigro_*` fields;
`MatchListItem` carries `giorni_previsti`, `pigro_stato` and `pigro_url`; `MatchCreate`
gains `giorni_previsti: int | None` beside `cliente` and `lettera`, and the MCP
`create_match` of PR #416 gains the same optional argument.

### 3.2 Settings

`REBASE_PIGRO_ENGAGEMENTS_TOKEN` (`Settings.pigro_engagements_token`), beside the
existing `pigro_api_url` and `pigro_registry_token`. Empty means the feature is off, as
signing is off without Documenso: a match that turns active gets `pigro_stato =
'da_collegare'` all the same, the link attempt is skipped and logged once at info level,
the match card says «Consuntivo non configurato su questo ambiente», and the report and
link routes answer `503` with that sentence (the split `routers/pigro.py` already makes:
`503` for a CRM not configured, `502` for a CRM that did not answer). The token travels
only over TLS: a `pigro_api_url` that is not `https://` (except `localhost` and
`127.0.0.1`, for the tests and a developer's stack) makes every link and report fail
with «Pigro è raggiungibile solo su https.», the seam's sentence, before anything is
sent. Config,
`.env.example` and the compose `x-api-environment` list all gain the variable (a variable
missing from the compose list never reaches the container, REB-215).

### 3.3 The link: `rebase_core/engagements.py`

`EngagementService(session, settings, http, *, sender=None, now=utcnow, today=rome_today)`,
the hub's twin of the registry client, through the same `HttpCall` seam
(`rebase_core/http.py`: no redirects, a bounded body) with one difference: the seam's
`urllib_call` times out at ten seconds, and the first `PUT` for a new freelancer
provisions a database, so the service is handed `urllib_engagements_call`, the same
opener with a 90-second timeout, the way `urllib_download_call` varies the byte cap.
`PigroUnavailable` and its four sentences («Pigro non risponde.», «Pigro non ha
risposto (N).», «Pigro ha risposto qualcosa di troppo lungo.», «Pigro ha risposto
qualcosa che non è un elenco.») become module constants of `pigro.py` so both clients
say the same words. Three methods and a helper:

- `payload(match, letter, user, company)` builds § 2.3's body: the freelancer's user
  (email, nome, cognome), `giorni_previsti`, the letter's `numero` and `ruolo`
  (`ContractDocument.numero`, `data["ruolo"]`), the company's name, the dates and the
  fee from the match's `lettera_*` columns, or, when those are `NULL` (a match older
  than 0021), from the letter's printed data through `parse_italian_date` (the inverse
  of `contracts.fields.italian_date`, over the same `MONTHS`) and `amount(data, FEE)`;
  and rebase's own data from `REBASE_SIGNER_JSON` (`rebase-ragione-sociale`,
  `rebase-piva`, `rebase-cf`, `rebase-sede`, `rebase-pec`,
  `rebase-codice-destinatario`), normalised for the CRM: the VAT number compacted and
  stripped of a leading `IT`, sent only when it is eleven digits; `sede` as `indirizzo`
  cut to 255; `codice_destinatario` as `codice_sdi` only when it is seven characters.
  A match whose letter is not `firmato` is refused: the link exists only for an active
  match.
- `link(match_id, admin_id=None)`: under the match's lock (`MatchService.lock_match`),
  refuse a match that is not `attivo` (`InvalidState`, «Si collega a Pigro solo un
  match attivo.», a `409`); without a token, set `da_collegare` if unset, log once at
  info, commit and return; otherwise build the payload, set `pigro_attempted_at =
  now()`, commit, and release the lock. Then, with no row lock held (the house rule of
  `_confirm_completion`, a network call), `PUT
  {pigro_api_url}/api/rebase/engagements/{match_id}` with `Authorization: Bearer`,
  `Content-Type: application/json`. Then lock the match again, re-read it, and write the
  outcome only if it is not already `collegato`: `201` or `200` → `collegato` with slug,
  deal id, url, `pigro_linked_at = now()`, `pigro_errore = None`; `409` or `422` →
  `rifiutato` with the body's `detail`; any other status, an exception from `http`, a
  body that is not the shape → `errore` with the seam's sentence. In the same locked
  write, when the match is `collegato` and `pigro_mail_sent_at` is `NULL`, the column
  is stamped with `now()` as a claim; the commit releases the lock. Only the caller that
  claimed it sends the freelancer's mail (§ 3.7); a provider that refuses makes it
  re-lock and clear the claim, so the next `link` or the sweep sends again. Two callers
  racing on the same match (the webhook's `finish` and the sweep, say) therefore send
  one mail, the way the signed-copy mail is sent once. With `admin_id`,
  `AdminActionService.record(entity_type="match", entity_id, kind="pigro_link",
  admin_id, payload={"esito": stato, "errore": ...})`, so the audit trail of the match
  says who retried. Answer `MatchService.get`.
- `link_pending()` lists every `attivo` match with `pigro_stato` in (`da_collegare`,
  `errore`), or `collegato` with `pigro_mail_sent_at` `NULL`, and calls `link` on each,
  answering how many linked and how many failed, for the sweep. A `rifiutato` match is
  not on the list.
- `report(match_id, da=None, a=None)` (§ 3.5).

Where it is called from: `SigningService._confirm_completion` sets `pigro_stato =
'da_collegare'` on the same commit that sets `attivo`. `finish` (the webhook's
background step and «Aggiorna stato») and `sweep` then call `link` when, re-reading the
row after the confirmation, the match is `attivo` with `pigro_stato = 'da_collegare'`
(`_confirm_completion` answers `True` for a rejection as well, so the state, not the
return value, decides). `SigningService` takes the engagement service as one more
collaborator, and the two places that build one, `signing_from_settings` (the webhook's
background task, the CLI) and `deps.get_signing_factory` (which gains the API's
`HttpCallDep`), hand it over; a `SigningService` built without one links nothing, as one
without Documenso signs nothing. `rebase contracts-sweep` calls `link_pending()` after
its own work and prints «, N match collegati a Pigro, M non collegati» on its line.
`POST /api/hub/matches/{id}/pigro/link` (admin) runs `link` now and answers the
`MatchRead`, for «Riprova»: the admin's browser waits on it, up to the 90 seconds a new
space can take, and the button says so while it runs.

### 3.4 «Crea match»: the expected days

On the conditions step of PR #416's three-step wizard, an optional number field
«Giorni previsti» beside «Compenso», id `match-giorni_previsti`, with the helper «Per il
consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».» It
belongs to `MatchCreate`, not to the letter's form (`LetteraFields` forbids an unknown
key), and the check step lists it with the other conditions. Nothing changes in the
letter's fields or its text from this number.

### 3.5 The report

`GET /api/hub/matches/{id}/report?da&a` (admin): `EngagementService.report(match_id, da,
a)` asks § 2.4 for the whole engagement by default: `da` is `lettera_data_inizio`, or
the letter's printed start parsed for a match older than 0021, or the match's creation
date, never today; `a` is today; a span longer than the CRM's 800 days is fetched in
consecutive windows of at most 800 days and merged, so «Tutto l'incarico» is the whole
engagement and nothing is clipped. It answers:

```json
{
  "match_id": "...", "pigro_url": "...", "pigro_stato": "collegato",
  "giorni_previsti": 40, "ore_previste": "320.00",
  "totale_ore": "96.00", "giorni_equivalenti": "12.00", "avanzamento": "30.00",
  "ore_fatturate": "80.00", "ore_non_fatturate": "16.00",
  "per_giorno": [{"data": "2026-10-01", "ore": "8.00", "descrizioni": ["Setup"], "fatture": ["12/2026"]}],
  "per_settimana": [{"settimana": "2026-W40", "da": "2026-09-28", "a": "2026-10-04", "ore": "24.00"}],
  "per_mese": [{"mese": "2026-10", "ore": "96.00"}],
  "fatture": [{"numero": "12/2026", "tipo": "fattura", "data": "2026-10-31", "stato": "emessa",
               "stato_pagamento": "da_incassare", "ore": "80.00"}]
}
```

`giorni_equivalenti` is `totale_ore / 8`, `avanzamento` a percentage of `ore_previste`
with two places, null without `giorni_previsti`. A day carries every distinct invoice
its entries sit on (`fatture`), so two entries of one day on two invoices lose nothing.
A match not `collegato` answers `409`
(`InvalidState`, with the state's sentence); a CRM not configured `503`; a CRM that does
not answer `502` with the seam's sentence, the mapping `routers/pigro.py` already makes.
Nothing is stored.

**The screen.** A new admin route `/admin/matches/$id/report`, page title «Consuntivo»,
reached from the match card on «Match e contratti» (a «Consuntivo» action beside the
document links, shown for a `collegato` match) and from a «Pigro» column on the
«Match» list that reads «Collegato», «Da collegare», «Errore» or «Rifiutato». The page:
the match's title (company, role, letter number) and the link to the deal on Pigro; a
period selector (a month picker, default the current month, plus «Tutto l'incarico»);
the progress line «96 ore, 12 giorni su 40 previsti (30%)», or «96 ore, 12 giorni»
without an estimate; a table per day (date, hours, descriptions, the invoice number or
«da fatturare»); the totals per week and per month of the selected period; the
invoices the shown days sit on (number, date, state, payment state, and the hours of
the selected period on each: with a month selected, only the invoices of that month's
days and their hours in that month; with «Tutto l'incarico», every invoice with all its
hours). The match card
itself shows the link state as a sentence in the `match_words` style (`situazione`
gains one sentence: «Le ore si consuntivano su Pigro.», «Pigro non ha ancora il deal:
riprova o aspetta lo sweep.», «Pigro non ha risposto: <sentence>», «Pigro ha rifiutato
il collegamento: <sentence>») and, for `errore`, `da_collegare` and `rifiutato`, the
«Riprova su Pigro» action among `altre_azioni`.

### 3.6 The member area

Under the active letter on the member «Contratti» page: «Le tue ore su Pigro» as a
button to `pigro_url`, and the line «rebase legge le ore di questo progetto per la
rendicontazione al cliente.» Nothing for a match not `collegato`. `MemberContract` (the
member read model) carries `pigro_url` for the letter's match.

### 3.7 The mail

`engagement_ready_mail(to, *, nome, numero, azienda, deal_url, spazio_creato)` in
`rebase_core/mail.py`, sent by `link` for a `collegato` match until `pigro_mail_sent_at`
is stamped: subject «La tua lettera n. 3/2026 è attiva: le ore si registrano su Pigro»;
the body says the letter with Acme is active, that the hours of this engagement are
logged on Pigro in the deal the button opens, that rebase reads the hours of that deal
and nothing else of the space, and, when `spazio_creato`, that a space was opened in
their name and the mail from Pigro carries the link that enters. The hub's frame and
tone (`_frame`, `_button`).

### 3.8 The clause in the letter

A new paragraph in `contracts/texts/lettera-di-incarico.md`, after the one on
`scadenze-fatturazione`, and `text_version` bumped. Proposed wording, for Ivan's review
before the merge:

> **Consuntivazione.** Il Professionista registra le ore lavorate per l'incarico in
> PigroCRM, nel progetto che rebase predispone nel suo spazio all'avvio dell'incarico,
> con cadenza almeno settimanale e comunque prima di ogni fattura. rebase legge le ore
> di quel progetto per rendicontare l'incarico al Cliente e per riscontrare le fatture
> del Professionista, e non accede ad altro dello spazio.

A letter written before the bump keeps its `text_version` and its PDF; `rebase
contracts-check` and `test_contract_render.py` typeset the new text. The framework
agreement does not change.

### 3.9 Over MCP

On `apps/mcp` (`rebase_mcp/server.py`), beside PR #416's match tools, built the way
`build_server` takes its `signing` factory:

| Tool | Does | Core |
|---|---|---|
| `get_match_report(match_id, da=None, a=None)` | the report of § 3.5 | `EngagementService.report` |
| `link_match_to_pigro(match_id)` | the «Riprova» of § 3.3, as the admin behind the token | `EngagementService.link` |

Both turn `PigroUnavailable` into a `ToolError` with its sentence, as `list_pigro_spaces`
does. `get_match` and `list_matches` (PR #416) carry the `pigro_*` fields as the API
does. Both new tools are listed in `apps/mcp/tests/test_tools.py`.

### 3.10 Failure cases

- **CRM down at activation**: `errore` with «Pigro non risponde.», the sweep retries
  every ten minutes, «Riprova» at once; the signature is not affected.
- **Token missing on the hub**: skipped, `da_collegare`, the sentence of § 3.2, nothing
  retried until the token exists.
- **Two matches of one freelancer activating together**: § 2.3's lock; one space, two
  deals.
- **The freelancer deletes the deal**: `409` from the CRM, `rifiutato` on the match with
  the CRM's sentence, the report says the same, the sweep leaves it alone. The recovery
  is the freelancer restoring the deal in their space (a soft delete is reversible, and
  the registry row still points at it), after which «Riprova» finds it again. Nothing is
  recreated, and the match is never re-bound to another deal (§ 7).
- **A body the CRM refuses** (a `422`: a VAT number that is not eleven digits after
  normalising, say): `rifiutato` with the CRM's field and sentence, for an admin to fix
  the signer data and «Riprova».
- **The mail provider refuses the freelancer's mail**: the match is `collegato` all the
  same, `pigro_mail_sent_at` stays `NULL`, the sweep sends it at its next run.
- **The freelancer owns several spaces**: the oldest; the match shows which slug.
- **Match `concluso` or `annullato` after linking**: nothing happens in the CRM, the
  report stays readable, «Riprova» disappears.
- **`REBASE_SIGNER_JSON` without a VAT number** (the preview today): the customer is
  found by name; the note says so.

## 4. Settings and deployment

| Where | Variable | Value |
|---|---|---|
| CRM `.env` | `PIGROCRM_ENGAGEMENTS_TOKEN` | `openssl rand -hex 32` |
| hub `.env` | `REBASE_PIGRO_ENGAGEMENTS_TOKEN` | the same value |

Production and preview each get their own pair, in the host `.env` files (the CRM's
`x-api-environment` list and the hub's both gain the line). The CRM ships first
(`pigrocrm-v*`): the routes answer `404` until the token is set, and the hub's link
attempts fail with a sentence, not an exception, until the CRM is up. The hub follows
(`hub-v*`). No per-space migration; the registry table appears at the CRM's first
boot. The rule is a row in the monorepo's `docs/design/DECISIONS.md`, added with this
record: the hub reaches into a space only through the CRM's engagements door, with a
token of its own, and the registry token stays read-only.

## 5. Testing

- **CRM core** (`packages/core/tests/test_engagements.py`, on the container the tenants
  tests use, real databases): first call creates space, customer and deal; second call
  answers the same ids and creates nothing; an owned space is reused, the oldest of two;
  `Ada@Studio.it ` finds `ada@studio.it`; a slug collision takes `-2`; two threads with
  two match ids and one address end with one tenant row and two deals; a row with
  `deal_id` `NULL` is completed at the next call; a deal created and never recorded is
  reused by name; the longest role and company still make a valid name; the deleted-deal
  `409`; the report's rows, the invoice resolution and the billed definition, the
  800-day cap, the defaults; the actor «rebase» on the timeline.
- **CRM API** (`apps/api/tests/test_engagements_api.py`): `404` without the token, `401`
  with a wrong bearer, `201` then `200`, `422` shapes, `503` without a public URL, the
  report's JSON, and that the registry token is refused on both routes.
- **Hub core** (`packages/core/tests/test_engagements.py`, with a recorded `HttpCall` as
  `test_freelancers_companies.py`'s `fake_http` does): the payload from a match with the
  columns and from one without them (the printed data parsed); the fiscal
  normalisation; `link`'s states from a `201`, a `200`, a `409`, a `422`, a `502`, a
  refused connection, a plain-`http` URL, and that no row lock is held during the call;
  the mail claimed under the lock so that two racing `link` calls send one, and re-sent
  when the sender refused; `link_pending`'s counts and its silence on `rifiutato`;
  `report`'s default start for a match older than 0021, its windows over an engagement
  longer than 800 days, its grouping by ISO week and month across a year end, the
  progress maths, the null estimate; `_confirm_completion` setting `da_collegare`;
  `finish` and `sweep` linking a match that just turned active and not one whose letter
  was refused.
- **Hub API and MCP**: the three routes' status codes (`409`, `502`, `503`) and shapes;
  the two tools in `test_tools.py`.
- **Hub web**: the wizard field and its check-step line; the «Consuntivo» page with a
  recorded report; the card's sentences and «Riprova su Pigro»; the member line; the
  «Pigro» column; `test_web_labels.py` holding the action's labels.
- **Contracts**: `test_contract_render.py` typesets the new letter text.
- **Preflight and preview**: a match activated on the preview (Documenso is on there)
  gets a deal on the preview CRM (`preview.pigro.letsrebase.com`), the page renders it,
  the video shows the flow.

## 6. Phases

Two milestones of P-REB-42, each with one draft PR, the CRM's first:

**Open a door in the CRM for rebase's engagements** (`projects/pigrocrm`): the setting,
the registry table, the actor, the service, the two routes, the signup's post-provision
step shared with the router, the tests, `.env.example` and compose.

**Link an active match to its deal and read the hours in the admin**
(`projects/hub`, branched from PR #416's tip): the migration and the read models, the
setting, `EngagementService`, the trigger in signing and the sweep, the three routes,
the wizard field, the report page, the card sentence and «Riprova», the member line, the
mail, the letter clause, the two MCP tools, the DECISIONS row.

The implementation plan, one task per card, is
`projects/hub/docs/superpowers/plans/2026-09-25-hours-report-per-match.md`.

## 7. Not here

- The end client reading the report: admin only, by Ivan's word; a client-facing view
  is the day the company area learns about matches.
- Contracts, rate cards, ceilings or proposals in the freelancer's space: the deal is
  enough to log hours, and the rest is the freelancer's own CRM to run.
- A mirror of the hours in the hub, alerts on missing hours, or a weekly reminder to
  log: every read is live; reminders are a later card once the report has been used.
- Pushing the end client's rate, rebase's margin or the invoice to the CRM: the CRM
  sees the freelancer's fee and nothing else (REB-386).
- Creating a space for a freelancer with no active letter: only an active match earns
  a deal. A match already `attivo` when this ships is linked by the sweep (§ 3.1's
  backfill), not by hand.
- Provisioning in the background with a `202` and a poll: the 90-second call is enough
  for one space at a time, and nothing a person waits on runs it; if the host ever
  takes longer, that is the next step.
- Re-binding a match to another deal once the freelancer deleted the first: restoring
  the deal is the recovery (§ 3.10); an admin-only relink that rewrites the registry
  row is a later card, if a real case asks for it.
