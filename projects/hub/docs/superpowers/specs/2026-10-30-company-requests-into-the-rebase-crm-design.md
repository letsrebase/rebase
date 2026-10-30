# Company requests into the rebase CRM

Status: draft, waiting for Lorenzo's approval. Card: REB-865. Project: «File every
completed hub company request in the rebase CRM» (P-REB-71).

## Why

rebase keeps its commercial relationships in its own PigroCRM space: customer, contact,
deal, activities, offers, invoices. The hub keeps the other half: freelancers, company
requests, matches, letters and signatures. Today nothing connects the two. A company that
completes the hub wizard never appears in the CRM, so its follow-up lives in nobody's
pipeline; a company met directly starts in the CRM and, when a match is near, enters the
hub with nothing pointing back at the deal. The same company ends up typed twice, and the
same event gets written in both places.

Lorenzo's decisions, 2026-10-30:

- A company met directly is recorded in the CRM first. It enters the hub when there is a
  match or a real negotiation, linked to what the CRM already has.
- A company that registers on the hub appears in the CRM **automatically**, and only when
  its registration is **complete, with every field**.
- The deal is filled as completely as possible by **AI**, from what the company wrote, to be
  reviewed by a person afterwards. The AI runs on the hub's existing provider system
  (`ai_tasks`, «Modelli AI»), not on anything new.
- Operational status is not kept in agent memory: it goes in the CRM or the hub.

## Who holds what

| What | Where | Why |
|---|---|---|
| Customer, contact, deal, pipeline stage, activities, offers, invoices | rebase's CRM space | It is the commercial relationship, and the CRM is where reminders, dashboards and invoices already run |
| Freelancer card, company request, match, letter, signature, hours report | hub | It is the supply side and the contract machinery; a match needs a hub `company_id` (`create_match`) |
| An event about the negotiation (a call fixed, an offer sent, a reply) | an activity on the CRM deal | One place for the client's story |
| An event about a freelancer (contacted, interested, proposed, discarded) | a note on the hub card | One place for the person's story |

Creation flows one way, from the hub to the CRM, and the hub keeps the link. States and
notes are never synchronised in either direction: a two-way sync is the expensive, fragile
part, and the table above already removes the reason to want one. This is the
`docs/design/DECISIONS.md` row of 2026-10-30.

## 1. What triggers a filing

A **completed request** is a `companies` row committed by the server. The wizard keeps its
progress in the browser only (`CompanyWizard.tsx:329-344`, `saveDraft` under
`COMPANY_DRAFT_KEY`) and posts once, at the end, with every field `CompanyCreate` requires
(`rebase_core/schemas.py`); a half-filled wizard never reaches the server. So "complete
with every field" needs no new check: it is every row written by one of three paths:

- `CompanyService.request` (`POST /api/hub/companies`, the public wizard);
- `MemberService.create_additional_request` (`POST /api/hub/me/company`, a member's second
  request, REB-381);
- the admin create of § 6.

Each sets `crm_stato` to `da_collegare` in the transaction that writes the row.

A **team request** from the team builder's public page (`TeamRequest`, `models.py:1256`)
carries only company name, email and phone. It is not a complete request and is not
filed; the same contact is filed when they complete the wizard or an admin opens a request
for them (§ 6).

The referente's email is not confirmed by the wizard, so a request typed with somebody
else's address is filed too. That is accepted because each request gets a deal of its own
(§ 3): the worst case is a stray deal in the first stage, archived in one click.

## 2. The CRM door

A new route on the engagements door, as the 2026-09-25 row asks of any new capability the
hub needs from a space:

`PUT /api/rebase/leads/{request_id}`, under the same root-only prefix as
`/api/rebase/engagements` and not inside it, so it cannot be read as a `{match_id}`.

- **Token.** The engagements token, checked by the door's existing dependency
  (`_require_root_door`, `require_service_token`). No new credential.
- **Space.** `PIGROCRM_LEADS_SPACE`, the slug of rebase's own space. The route never takes
  a slug from the caller, so the token reaches no space through it that it could not
  already reach. Empty means 503 with «Spazio dei lead non configurato».
- **Serialised.** A session advisory lock keyed by the referente's lowercased email for the
  whole call, as `EngagementService` locks by address; a lock held past the timeout is 503,
  which the hub retries. Two requests from one person, or the wizard's job racing the
  sweep, cannot both create a customer.
- **Idempotent.** The deal carries the marker `rebase:request=<request_id>` in its note, and
  the route looks for it first (`DealRepository.find_by_marker`, the engagements pattern).
  A retry after a crash, or a second `PUT`, finds the deal and its customer and person and
  writes nothing.
- **Which customer.**
  1. `deal_id` given (§ 6 only): the deal must exist and be live in that space, otherwise
     422. The route writes the marker and an activity «Collegata la richiesta dall'hub»
     with the hub link on that deal, and creates nothing else.
  2. Otherwise, the oldest live person in the space with the referente's email
     (case-insensitive; `people.email` is not unique) whose customer is live: that person
     and customer are reused. A person whose customer is archived, or who has none, counts
     as found for the person and not for the customer: a new customer is created and the
     person attached to it.
  3. Nobody with that email: a new customer and person.
- **Always a new deal.** Outside case 1, every request gets a deal of its own, even when
  the customer already has an open one. Two requests a week apart are two things to answer
  (the rule `CompanyService.request` already follows), and a request typed with a real
  client's address must not write onto that client's live deal. When the typed company
  name differs from the reused customer's `ragione_sociale`, the deal note says so.
- **Never by name.** A customer is matched only by the referente's email or an explicit
  deal. «Rossi» and «Rossi S.r.l.» are the same company to a person and two strings to a
  query; a wrong merge is worse than a duplicate a person archives.
- **What it writes.** Customer: `ragione_sociale` = the typed company name, a note saying it
  came from the hub on that date. Person: the referente's fields, attached to the customer.
  Deal: the fields of § 3, in the pipeline's first open stage, with no owner (an admin
  assigns it). Every write is recorded with actor «rebase» (`Actor.rebase()`), so the
  timeline says the hub did it and not a person.
- **Answer.** `{customer_id, person_id, deal_id, deal_url, creato}`: 201 when it created a
  deal, 200 when it found one by marker or linked an explicit one.
- **Errors.** As `routers/engagements.py` maps them: 401 wrong token, 404 token not
  configured, 422 a body that does not validate or a `deal_id` not in the space, 503 space
  not configured, unreachable or locked.

## 3. What the hub sends, and the AI draft

A fourth task in the hub's AI system, `crm_lead`, beside `card`, `team_proposal` and
`cv_anonimo`.

- **Configuration.** `AI_TASKS` (`models.py:1495`) gains `crm_lead`; the hub's next
  migration widens both `ck_ai_tasks_task` and `ck_ai_usage_task`, and seeds the task's
  row with the provider and model `team_proposal` uses, effort `low`, a `max_output_tokens`
  and a small daily cap, switched off. «Modelli AI» (`TASK_LABELS`) and `list_ai_config`
  show it, with «Prova», and an admin switches it on once the privacy line below is live.
- **What the model sees.** `figura_richiesta`, `progetto`, `durata` and `periodo_da`.
  Never the company name, the referente or the budget. This is a new kind of data leaving
  the hub, so the 2026-10-30 `DECISIONS.md` row names it, and the privacy page says it
  before the task is switched on (REB-867). The wording of that page is Ivan's and
  Lorenzo's to approve.
- **The call.** `call_for_task(session_factory, settings, "crm_lead")` (`llm.py:531`), one
  request, a structured output model:

  | field | meaning | bound |
  |---|---|---|
  | `ruolo` | the role, normalised (`figura_richiesta` is free text) | 80 characters |
  | `giorni_settimana` | days per week, when the text says it | 0.5 to 5 |
  | `durata_settimane` | the engagement's length, when `durata` or the text says it | 1 to 104 |
  | `competenze` | skills or tools named in the text | ten, 40 characters each |
  | `sintesi` | three or four sentences in Italian on what the company needs | 800 characters |

  Every field is empty when the text does not say it. A value out of its bound is dropped,
  not clamped.
- **The deal, built in code.**
  - `nome`: «`nome_azienda`: `ruolo`», with `figura_richiesta` when there is no `ruolo`, cut
    to the CRM's limit.
  - `valore_previsto`: `budget_giornaliero` × `giorni_settimana` × `durata_settimane` ×
    `numero_risorse`, only when every input is present, dropped when it does not fit the
    column.
  - `data_chiusura_prevista`: `periodo_da` when it is in the future (the company wants to
    start then, so the deal has to be closed by then).
  - `tariffa_oraria` and `ore_preventivate` stay empty. In the CRM they are what hours
    logged on the deal are valued at, and a prospect's typed budget is not that.
  - `note`, in Italian, in two blocks: what the company wrote (every request field, the
    `progetto` text verbatim, the hub link, the typed company name when it differs) and,
    under «Bozza AI, da rivedere», `ruolo`, `competenze` and `sintesi`. The model's text
    reaches the deal's name only through `ruolo`, bounded, and nowhere else outside that
    block.
- **The body.** `azienda`, `referente` (`nome`, `cognome`, `email`, `telefono`,
  `linkedin_url`), `deal` (the fields above), `deal_id` (§ 6 only). Every string is cut or
  dropped to the CRM's limits before sending (person `nome` 120, `linkedin` 255,
  `telefono` 40, deal `nome` its maximum), as the engagements client already does, so a
  long value never turns into a permanent 422.
- **When the AI cannot answer** (task off, provider off, cap reached, an error): the
  filing goes ahead without a draft. The name falls back to `figura_richiesta`,
  `valore_previsto` stays empty, and the note says no draft was made. The AI improves a
  filing; it never blocks one.
- **Untrusted text.** `progetto` is written by a stranger. The output is a fixed schema with
  bounds, its fields land only in the note and in a bounded name, and the CRM already marks
  free text as data, not instructions (its `provenienza`).

## 4. The link on the hub

- **Columns on `companies`**, a migration of their own (REB-868): `crm_stato` (`da_collegare`,
  `collegato`, `errore`, `rifiutato`, under a named check; null for rows written before
  this ships), `crm_customer_id`, `crm_deal_id`, `crm_deal_url`, `crm_errore`,
  `crm_attempted_at`, and `crm_bozza` (JSONB, the draft of the first successful AI call).
  The states mean what `pigro_stato` means on a match (`engagements.py:135`): `errore` is
  retried by the sweep, `rifiutato` (a 422 from the door) only by a person.
- **`CrmLeadService`** in `rebase_core`, shaped like `EngagementService`:
  - `link(company_id, admin_id=None)`: under a row lock, set `da_collegare` if the state is
    null (an old row an admin retries), stamp `crm_attempted_at`, commit and release; build
    the body, calling the AI only when `crm_bozza` is empty and storing its draft; send it
    through the `HttpCall` seam with no lock held; lock again and write the answer or the
    error sentence; record an admin action when an admin asked.
  - `link_pending()`: every live request in `da_collegare` or `errore`, each linked on its
    own, a failure logged without stopping the rest.
- **When it runs.** The first attempt is a `BackgroundTasks` job right after the response,
  on all three paths (`routers/members.py:165` gains the parameter). The sweep is recovery:
  `rebase contracts-sweep` calls `link_pending()` in a round of its own, wired outside
  `SigningService` (whose round returns early without the engagement service,
  `signing.py:703-704`), and prints its own line. No new timer.
- **The draft is made once.** A CRM that is down for a day is retried every sweep, but the
  stored draft is reused, so the AI cap is not spent again.
- **Without the token or the space** nothing is sent and requests wait in `da_collegare`.
  When the CRM is configured, the next sweep files them all; the AI cap bounds the burst
  and the ones past it are filed without a draft.
- **A request changed after linking** (a member's `PATCH /me/company`, an admin's
  override) changes nothing in the CRM. A soft-deleted request is not linked.
- **Admin page and MCP.** `AdminCompanyDetail` shows the state, the error sentence and a
  link to the deal; «Riprova» calls `POST /api/hub/companies/{id}/crm/link`. «Collega a un
  altro deal» sends a `deal_id` (§ 2, case 1) and replaces the link, for when the
  automatic filing made a second customer for a company the CRM already had. `get_company`
  and `list_aziende` carry the state; `link_company_to_pigro` is the «Riprova» for agents,
  as `link_match_to_pigro` is for matches.

## 5. Writing to the CRM's timeline

The new activity kinds (the lead filed, the request linked to an existing deal) get their
labels in the CRM's `activityLabels.ts` and an entry in `test_activity_coverage.py`, and the
new route is in the regenerated `api-types.ts`.

## 6. An admin opens a request for a company already in the CRM

Today a request is created only by the public wizard or a member (`routers/companies.py:16`,
`routers/members.py:165`). An admin route, a form on «Aziende» and a `create_company` MCP
tool create one on the referente's behalf:

- The same fields as the wizard, with the same validation, plus an optional **CRM deal**
  (its URL or id). The referente's `users` row is found or created by email, as the wizard
  does; no mail is sent to anybody.
- The row is written with `crm_stato` `da_collegare` and linked through the door. With a
  CRM deal given, the door links to it and creates nothing (§ 2, case 1). Without one, the
  email lookup applies. Giving the deal is what keeps a company met directly from being
  created twice when its contact in the CRM has no email, or a different one.
- The admin action is recorded with its `fonte`, as every admin write is.

## 7. Configuration and deploy

| Where | Variable | Value |
|---|---|---|
| CRM | `PIGROCRM_LEADS_SPACE` | the slug of rebase's space on production, a test space on the preview; in `.env.example` and the `x-api-environment` list of `docker-compose.yml` |

The token is the engagements one, already set on both hosts, and `REBASE_PIGRO_API_URL`
already has its default. The CRM ships first (milestone 2), then the hub.

## 8. Tests

- CRM door: token answers; space not configured; the marker found on a retry; the lock
  held (503); the person found by email, case-insensitive, the oldest when several; a
  person whose customer is archived; a new deal even with an open one; the typed name
  noted when it differs; a `deal_id` outside the space refused, one inside linked with its
  activity; nothing matched by name; actor «rebase» on the timeline.
- AI task: the draft parsed into the output model; a value out of bounds dropped; the deal
  name with and without `ruolo`; `valore_previsto` with each input missing and when it
  overflows; the model never sees the company name or the referente; a call that raises.
- Link: the state set in the transaction of each path; no lock held during the call; the
  draft stored once and reused; a CRM that refuses, times out or answers 503; the sweep's
  counts and its own line; `rifiutato` skipped by the sweep and retried by «Riprova»; an
  old row with a null state; a soft-deleted request skipped; values cut to the CRM's
  limits; no network in any test (`HttpCall` fake).

## Not in this design

- Updating the CRM when a hub request changes, or the hub when a deal moves.
- Creating attività or reminders for the new deal; the CRM's own rules can do that.
- Choosing the deal's owner automatically.
- Filing team requests that never become a `companies` row.

## Open question for Lorenzo

The requests already on the hub: file none (the default here, since nobody asked), or file
those still `nuovo`, `contattato` or `in_corso` once, with a `rebase crm-backfill --dry-run`
first?

## Cards

REB-865 this record · REB-866 the CRM route · REB-867 the AI task and its privacy line ·
REB-868 the link and its sweep · REB-869 the admin page and MCP · REB-870 the admin create.
