# Hours per match: the CRM opens a door for rebase's engagements, the hub reads the report

Date: 2026-09-25. Status: approach and every section below approved in conversation by
Ivan on 2026-09-25 (provisioning when the match turns active, rebase as the customer,
the minimum to log hours, a dedicated token, expected days on the match at eight hours a
day, the reporting clause in the letter, the report per day, week and month with the
invoices, and the same reads over MCP); this record is written for his review.
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
letter of engagement names the commitment as free text (`impegno`, `unita`), so a report
that compares hours with «the letter's days» needs a number that does not exist yet.

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
mail says where the hours go.

**Expected days are a number on the match.** `giorni_previsti`, an optional integer an
admin fills in at «Crea match» next to the fee, stored on the match and sent to the CRM
as `ore_preventivate` at eight hours a day. The letter's `impegno` stays free text; the
report says «N giorni previsti, 8 ore al giorno» and measures against it. A match without
it gets the hours and no progress bar.

**Transparency is written where the freelancer reads.** The deal's and the customer's
notes say rebase created them for letter n. X and reads the hours of that deal; the
space's timeline records «rebase» as the actor, not «sistema»; the member area shows
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

**A CRM that does not answer is a state on the match, retried by the sweep.** Linking
is attempted right after the match turns active; a refusal or a timeout leaves the match
`errore` with the sentence, the existing `contracts-sweep` (every ten minutes) retries
every `da_collegare` and `errore` match, and «Riprova» on the match does it at once.
Nothing blocks the signature: the document is `firmato` and the match `attivo` whether
or not the CRM answered.

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
the CRM's host and the hub's (`REBASE_PIGRO_ENGAGEMENTS_TOKEN`, § 4).

### 2.2 The registry table

`rebase_engagements`, in the registry database beside `tenants` and the identity tables,
on `TenantsBase` so `ensure_tenants_database`'s `create_all` creates it at the first boot
after the deploy (the module is imported in `tenants/database.py` the way
`identity.models` is, or `create_all` never sees it):

| column | type | notes |
|---|---|---|
| `match_id` | UUID, primary key | the hub's match id, the idempotency key |
| `tenant_id` | UUID, FK `tenants.id` | the space |
| `customer_id` | UUID, nullable | the customer «rebase» in that space |
| `deal_id` | UUID, nullable | the deal for the letter; `NULL` while step 2 of § 2.3 has not completed |
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
    "ragione_sociale": "rebase S.r.l.", "partita_iva": "...", "codice_fiscale": "...",
    "sede": "...", "pec": "...", "codice_destinatario": "..."
  }
}
```

`data_fine`, `giorni_previsti`, `pec` and `codice_destinatario` may be null. The answer,
`201` on the first call and `200` on every later one with the same `match_id`:

```json
{
  "slug": "ada-lovelace", "url": "https://pigro.letsrebase.com/ada-lovelace/app/",
  "customer_id": "...", "deal_id": "...",
  "deal_url": "https://pigro.letsrebase.com/ada-lovelace/app/deals/<id>",
  "spazio_creato": true, "creato": true
}
```

What the service (`pigrocrm.core.engagements.EngagementService`, in core, called by the
router and by nothing else yet) does, in order:

1. **Lock by address.** `pg_advisory_xact_lock(hashtext(lower(email)))` on the registry
   session, so two letters of the same freelancer activating together create one space
   and not two, and two retries of the same match wait for each other.
2. **An engagement row already there** answers its ids (`creato: false`), after checking
   the deal still exists in the space: a deal the freelancer soft-deleted answers `409`
   with «Il deal di questa lettera è stato eliminato nello spazio.», which the hub shows
   as the match's error. Nothing is recreated behind the freelancer's back.
3. **The space.** The registry row whose `owner_email` equals the address, case-insensitively,
   oldest first; none means `TenantService.provision` with `TenantSignup(slug, nome="Ada
   Lovelace", email, membro=True)`, the slug from `slugify("ada lovelace")` cut to the
   slug's maximum, with `-2`, `-3`... appended while `availability` says the name is
   taken or reserved. Then exactly what the signup route does after provisioning (the
   magic link and the welcome mail through `MagicLinkService.request` and `welcome_mail`),
   moved out of the router into a function both call, so a space born here and a space
   born at the signup are told the same way. The engagement row is written with the
   `tenant_id` and no deal yet, and committed: a failure from here on leaves a row that
   says «space found, deal missing», which the next call completes.
4. **The customer «rebase»**, in the space, as `Actor.rebase()` (§ 2.5): the customer
   whose `partita_iva` is rebase's, or, while `REBASE_SIGNER_JSON` carries no VAT number
   yet, whose `ragione_sociale` is rebase's; created if missing with the fiscal fields of
   the body and the note «Creato da rebase per la lettera n. 3/2026. rebase legge le ore
   dei deal di questo cliente per rendicontare gli incarichi.» An existing customer is
   left as it is: its fields are the freelancer's to edit.
5. **The deal**, in the space: name `Lettera n. 3/2026 · Backend developer per Acme
   S.r.l.`, on that customer, `tariffa_oraria = compenso / 8` (`Decimal`, the column's
   six places), `ore_preventivate = giorni_previsti * 8` or null, `data_chiusura_prevista
   = data_fine`, `owner_id` the space's admin whose email is the freelancer's, the
   default open stage, and the note «Creato da rebase per la lettera n. 3/2026 con Acme
   S.r.l. rebase legge le ore di questo deal per la rendicontazione al cliente.» Before
   creating, a deal with that exact name under that customer is reused: it is the deal a
   previous call created and failed to record (a failure between steps 5 and 6), and the
   name is deterministic on purpose.
6. **The row completed** with `customer_id` and `deal_id`, committed, answered.

`422` for a body that does not validate, `401` for a wrong or missing bearer, `404` when
the token is not configured, `503` with the sentence when the space's database cannot be
created or reached. The route is not throttled per client: the only caller holds the
token.

### 2.4 `GET /api/rebase/engagements/{match_id}/report`

Same bearer. Query `da` and `a` (`YYYY-MM-DD`), both optional: `da` defaults to the
deal's creation date, `a` to today, and a span over 400 days is a `422`. The answer:

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
type, year and number, state, payment state, date) in one query per report. `stato`
is `DealTimeSummary.stato` as the deal's own page shows it. A row whose deal is gone
(soft-deleted) answers `409` with the sentence of § 2.3 step 2; a `match_id` with no row
answers `404`. Nothing else of the space is readable through this door: not the
customers, not the other deals, not the invoices beyond the ones these hours sit on.

### 2.5 The actor «rebase»

`ActorType` gains `"rebase"` and `Actor.rebase()` (no id, role `admin`, `full_access`
false), used for everything § 2.3 writes in a space. The activities timeline, which
today names a `system` actor «sistema», names this one «rebase», so the deal's own
history reads «rebase ha creato il deal» and the freelancer knows who did what without
opening the note. `Actor.system()` stays what it is, for the CRM's own jobs.

### 2.6 What is not touched

No per-space migration: a space's schema is unchanged, which is what lets this ship
without the per-space `alembic_version` check a schema change needs (ORB-189). No
change to the MCP server of the CRM: an agent of the freelancer sees the customer and
the deal as any other. No change to the registry token's routes.

## 3. The hub side

### 3.1 Data model

New columns on `matches`, in the next free migration number when the branch lands
(`0020_campaigns.py` is taken by PR #407: refetch `main` and renumber before merging,
the lesson of two open PRs that both took `0006` on 2026-09-15). The migration also
backfills `pigro_stato = 'da_collegare'` on every match already `attivo`, so an
engagement signed before this ships is linked by the first sweep after the deploy:

| column | type | meaning |
|---|---|---|
| `giorni_previsti` | integer, nullable, `CHECK (giorni_previsti BETWEEN 1 AND 366)` | expected billable days, an admin's estimate |
| `pigro_stato` | varchar(20), nullable, `CHECK IN ('da_collegare', 'collegato', 'errore')` | `NULL` until the match turns active |
| `pigro_slug` | varchar(32), nullable | the space |
| `pigro_deal_id` | UUID, nullable | the deal |
| `pigro_url` | text, nullable | the deal's page, as the CRM answered it |
| `pigro_collegato_il` | timestamptz, nullable | when `collegato` was reached |
| `pigro_errore` | text, nullable | the sentence of the last failure, cleared on success |
| `pigro_tentato_il` | timestamptz, nullable | the last attempt, for the sweep's log and the admin's eye |

`MatchRead` and the list item carry `giorni_previsti` and the six `pigro_*` fields;
`MatchCreate` gains `giorni_previsti: int | None` beside `cliente` and `lettera`, and
the MCP `create_match` of PR #416 gains the same optional argument.

### 3.2 Settings

`REBASE_PIGRO_ENGAGEMENTS_TOKEN` (`Settings.pigro_engagements_token`), beside the
existing `pigro_api_url` and `pigro_registry_token`. Empty means the feature is off, as
signing is off without Documenso: a match that turns active gets `pigro_stato =
'da_collegare'` all the same, the link attempt is skipped and logged once at info level,
the match card says «Consuntivo non configurato su questo ambiente», and the report route
answers `503` with that sentence. Config, `.env.example` and the compose
`x-api-environment` list all gain the variable (a variable missing from the compose list
never reaches the container, REB-215).

### 3.3 The link: `rebase_core/engagements.py`

`EngagementService(session, settings, http, now, sender, signer)`, the hub's twin of the
registry client, through the same `HttpCall` seam (`rebase_core/http.py`: no redirects,
a bounded body, a timeout). Three methods:

- `payload(match)` builds § 2.3's body from the match, its company, its letter's data
  (`ContractDocument.data` of the match's letter: `numero`, `ruolo`, `data_inizio`,
  `data_fine`, `compenso`), the freelancer's user (email, nome, cognome), `giorni_previsti`,
  and rebase's own data from `REBASE_SIGNER_JSON` (`rebase-ragione-sociale`, `-piva`,
  `-cf`, `-sede`, `-pec`, `-codice-destinatario`). A match whose letter is not `firmato`
  is refused: the link exists only for an active match.
- `link(match_id, admin_id | None)` locks the match, refuses a match that is not
  `attivo` (`ValidationFailed`, «Si collega a Pigro solo un match attivo.»), puts the
  call, and writes the outcome: `collegato` with slug, deal id, url and date, or
  `errore` with the sentence (`PigroUnavailable`'s own sentences for a refused connection,
  a non-2xx, a body that is not the shape; the CRM's `detail` for a `409`). Sets
  `pigro_tentato_il` either way. On the first `collegato` it sends the freelancer's mail
  (§ 3.7) through the hub's sender, once: a retry that finds `collegato` already set sends
  nothing. Records an `AdminAction` of kind `pigro_link` with the outcome when an admin
  asked for it («Riprova», the MCP tool), so the audit trail of the match says who retried.
- `link_pending()` lists every `attivo` match with `pigro_stato` in (`da_collegare`,
  `errore`) and calls `link` on each, answering how many linked and how many failed, for
  the sweep.

Where it is called from: `SigningService._confirm_completion` sets `pigro_stato =
'da_collegare'` on the same commit that sets `attivo`, and the callers that run after a
completion (`finish`, `sweep`) call `link` once the commit is through, in the same
background step as the signed copy's download, so the webhook's response never waits on
the CRM. `rebase contracts-sweep` calls `link_pending()` after its own work and prints
«, N match collegati a Pigro, M non riusciti» on its line. `POST /api/hub/matches/{id}/pigro/link`
(admin) runs `link` now and answers the `MatchRead`, for «Riprova».

### 3.4 «Crea match»: the expected days

On the conditions step of PR #416's three-step wizard, an optional number field
«Giorni previsti» beside «Compenso», with the helper «Per il consuntivo: 8 ore al
giorno. Il testo della lettera resta quello di «Impegno».» The check step lists it with
the other conditions. Nothing changes in the letter's fields or its text from this
number.

### 3.5 The report

`GET /api/hub/matches/{id}/report?da&a` (admin): `EngagementService.report(match_id, da,
a)` asks § 2.4 for the whole engagement by default (`da` = the letter's `data_inizio`,
`a` = today, capped at 400 days) and answers:

```json
{
  "match_id": "...", "pigro_url": "...", "pigro_stato": "collegato",
  "giorni_previsti": 40, "ore_previste": "320.00",
  "totale_ore": "96.00", "giorni_equivalenti": "12.00", "avanzamento": "30.00",
  "ore_fatturate": "80.00", "ore_non_fatturate": "16.00",
  "per_giorno": [{"data": "2026-10-01", "ore": "8.00", "descrizioni": ["Setup"], "fattura": "12/2026"}],
  "per_settimana": [{"settimana": "2026-W40", "da": "2026-09-28", "a": "2026-10-04", "ore": "24.00"}],
  "per_mese": [{"mese": "2026-10", "ore": "96.00"}],
  "fatture": [{"numero": "12/2026", "tipo": "fattura", "data": "2026-10-31", "stato": "emessa",
               "stato_pagamento": "da_incassare", "ore": "80.00"}]
}
```

`giorni_equivalenti` is `totale_ore / 8`, `avanzamento` a percentage of `ore_previste`
with two places, null without `giorni_previsti`. A match not `collegato` answers `409`
with the state's sentence; a CRM that does not answer, `503` with `PigroUnavailable`'s
sentence. Nothing is stored.

**The screen.** A new admin route `/admin/matches/$id/report`, page title «Consuntivo»,
reached from the match card on «Match e contratti» (a «Consuntivo» action beside the
document links, shown for a `collegato` match) and from a «Pigro» column on the
«Match» list that reads «Collegato», «Da collegare» or «Errore». The page: the match's
title (company, role, letter number) and the link to the deal on Pigro; a period
selector (a month picker, default the current month, plus «Tutto l'incarico»); the
progress line «96 ore, 12 giorni su 40 previsti (30%)», or «96 ore, 12 giorni» without
an estimate; a table per day (date, hours, descriptions, the invoice number or «da
fatturare»); the totals per week and per month of the selected period; the invoices
these hours sit on (number, date, state, payment state, hours). The match card itself
shows the link state as a sentence in the `match_words` style (`situazione` gains one
sentence: «Le ore si consuntivano su Pigro.», «Pigro non ha ancora il deal: riprova o
aspetta lo sweep.», «Pigro non ha risposto: <sentence>.») and, for `errore` or
`da_collegare`, the «Riprova» action among `altre_azioni`.

### 3.6 The member area

Under the active letter on the member «Contratti» page: «Le tue ore su Pigro» as a
button to `pigro_url`, and the line «rebase legge le ore di questo progetto per la
rendicontazione al cliente.» Nothing for a match not `collegato`. `MemberContract` (the
member read model) carries `pigro_url` for the letter's match.

### 3.7 The mail

`engagement_ready_mail(to, nome, numero, azienda, deal_url, spazio_creato)` in
`rebase_core/mail.py`, sent once by `link` on the first `collegato`: subject «La tua
lettera n. 3/2026 è attiva: le ore si registrano su Pigro»; the body says the letter with
Acme is active, that the hours of this engagement are logged on Pigro in the deal the
button opens, that rebase reads the hours of that deal and nothing else of the space,
and, when `spazio_creato`, that a space was opened in their name and the mail from Pigro
carries the link that enters. The hub's frame and tone (`_frame`, `_button`).

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

On `apps/mcp` (`rebase_mcp/server.py`), beside PR #416's match tools:

| Tool | Does | Core |
|---|---|---|
| `get_match_report(match_id, da=None, a=None)` | the report of § 3.5 | `EngagementService.report` |
| `link_match_to_pigro(match_id)` | the «Riprova» of § 3.3, as the admin behind the token | `EngagementService.link` |

`get_match` and `list_matches` (PR #416) carry the `pigro_*` fields as the API does. Both
new tools are listed in `apps/mcp/tests/test_tools.py`.

### 3.10 Failure cases

- **CRM down at activation**: `errore` with «Pigro non risponde.», the sweep retries
  every ten minutes, «Riprova» at once; the signature is not affected.
- **Token missing on the hub**: skipped, `da_collegare`, the sentence of § 3.2, nothing
  retried until the token exists.
- **Two matches of one freelancer activating together**: § 2.3's lock; one space, two
  deals.
- **The freelancer deletes the deal**: `409` from the CRM, `errore` on the match with
  the CRM's sentence, the report says the same; an admin talks to the freelancer.
  Nothing is recreated.
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
boot. A row in `docs/design/DECISIONS.md`: the hub reaches into a space only through the
CRM's engagements door, with a token of its own, and the registry token stays
read-only.

## 5. Testing

- **CRM core** (`packages/core/tests/test_engagements.py`, on the tenants test
  infrastructure that provisions real databases): first call creates space, customer and
  deal; second call answers the same ids and creates nothing; an owned space is reused,
  the oldest of two; a slug collision takes `-2`; the deal's rate, estimate, note and
  actor; a failure between steps 5 and 6 recovered by name; the deleted-deal `409`; the
  report's rows, the invoice resolution, the 400-day cap, the defaults.
- **CRM API** (`apps/api/tests/test_engagements_api.py`): `404` without the token, `401`
  with a wrong bearer, `201` then `200`, `422` shapes, the report's JSON, and that the
  registry token is refused on both routes.
- **Hub core** (`packages/core/tests/test_engagements.py`, with a recorded `HttpCall`
  like `test_pigro.py`): the payload from a real match; `link`'s states from a `201`, a
  `200`, a `409`, a `503`, a refused connection; the mail sent once; `link_pending`'s
  counts; `report`'s grouping by ISO week and month, the progress maths, the null
  estimate; `_confirm_completion` setting `da_collegare`.
- **Hub API and MCP**: the three routes' status codes and shapes; the two tools in
  `test_tools.py`.
- **Hub web**: the wizard field and its check-step line; the «Consuntivo» page with a
  recorded report; the card's sentence and «Riprova»; the member line; the «Pigro» column.
- **Contracts**: `test_contract_render.py` typesets the new letter text;
  `test_web_labels.py` holds the new labels.
- **Preflight and preview**: a match activated on the preview (Documenso is on there)
  gets a deal on the preview CRM, the page renders it, the video shows the flow.

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
