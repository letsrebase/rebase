# AGENTS.md: working on the rebase hub

The root [`AGENTS.md`](../../AGENTS.md) covers the monorepo. This file is only about
this project.

## What it is

rebase, the freelance community, as a product of its own: the signup list the
community site collects, the freelancer profiles and the company requests the hub's
wizards will collect, and the admin area that reads them. Its design record is
`docs/superpowers/specs/`, English, one document per step; read the 2026-09-09 spec
before changing the shape of anything. Since 2026-09-10 a freelancer can get back in
with a magic link by mail (`/hub/login`, `/hub/me`): spec
`docs/superpowers/specs/2026-09-10-member-area-design.md`.

## The one rule

**Nothing here imports PigroCRM, and PigroCRM imports nothing from here.** The hub was
split out of the CRM on 2026-09-09 precisely so the two can change independently: its
own settings (`REBASE_*`), its own Postgres, its own Alembic history, its own API and
MCP server. `ruff.toml` bans the three `pigrocrm*` module roots in every package. Two
products that need to agree on something agree through `shared/`.

## Layout

```
packages/core/   rebase_core: models, migrations, services, the ad conversion, the perk files, the contracts and their texts
apps/api/        rebase_api: FastAPI, one process, its own database
apps/mcp/        rebase_mcp: the same services over stdio or Streamable HTTP, for an admin with a token
apps/web/        pnpm package `hub`: the SPA at letsrebase.com/hub/ (wizards, the member area, admin)
content/         the prose a perk is made of, reviewed as prose, and the contracts' example data
tools/           the scripts that typeset that prose into PDFs
db-backup/       the nightly encrypted backups of the hub's and Documenso's databases: one image, the script, the drill
```

`packages/core` may import neither adapter, and neither adapter may import the other:
each directory's `ruff.toml` says so.

**Response models say exactly what the JSON holds.** Every model an API route returns derives
from `schema_common.ResponseModel`, which makes the OpenAPI *response* schema list every field
as required (`json_schema_serialization_defaults_required`): the services build each model
whole, so `note`, `links` or `next_cursor` are always present, and the generated web client
types them as nullable, never as possibly missing. A new response model that derives from
plain `BaseModel` fails `test_openapi_contract.py`. Request bodies stay plain `BaseModel`.

**A closed set of values is an `enum` in the response schema, taken from the models' tuple.**
A response field whose values are a fixed set (`status`, `remote`, `kind`, `origin`, ...) is typed
with an alias from `rebase_core.vocabulary` (`FreelancerStatus`, `RemoteOption`, ...), built from the
constant in `rebase_core.models` (`FREELANCER_STATES`, `REMOTE_OPTIONS`, ...), so the generated web
client types it as a union of those values. The alias is a `str` for Python and for validation, on
purpose: a value outside the set (a row from before a `CHECK`, a kind another change adds) must
reach the page, not turn into a 500 on the response. `test_openapi_contract.py` holds each
alias to its constant. Left as plain `str` because no constant says what is allowed:
`FreelancerRead.provenance`, a document's `kind`, the CRM invoice's `kind`/`status`.

**A JSON field has a type, not `dict[str, Any]`.** `TeamProposalRead.location` and `.economics` are
`TeamPlace` and `TeamEconomics`, a campaign's `filters` is its `Filtri` union, and an
`AdminActionRead.payload` is `AdminActionPayload` (`audit.py`), a `TypedDict` of the keys the admin
pages read (the diff, the merge, the AI audit's counts and costs) that keeps any other key a writer
adds (`extra="allow"`) and is not validated on the way out. Its keys are all optional because
which ones are there depends on the kind: each is `Annotated[..., _MAY_BE_ABSENT]`
(`exclude_if`, which is what keeps a key out of `required` under `ResponseModel`). A writer that
starts putting a new key in a payload adds it to that `TypedDict`.

**The UI comes from `@rebase/ui`, and this application writes no primitive at all.**
The seven hand-written ones it used to carry were deleted when the package took over
(REB-300), so `apps/web/src/components/` holds only what is genuinely the hub's
(`Shell`, `SidebarNav`, `TeamBuilder`) and everything else is imported from the package.
`src/styles/tokens.css` is the entry file that fixes the import order, plus the one
thing that is really local: the `.site` scope, where the chooser, the two wizards and
the thanks page keep the landing's own 2px line, 8px step and 7% grid, the step by
repointing `--shadow-app-*` and the line and the grid in that same block. There is no `components.json` here: a new primitive is generated in
`shared/ui` (`pnpm --filter @rebase/ui exec shadcn add <name>`).

## The MCP server is an admin's, by token

Since REB-213 every transport resolves a personal token (`rebase_core.admin_tokens`,
minted from «Agenti» or with `rebase createtoken`) to the admin behind it before a tool
runs, and `build_server` takes a callable answering who that is: the admin signs what the
tools write. Over HTTP the `mcp` compose service serves `rebase_mcp.http:app` on 8088
(preview 8089, `REBASE_MCP_PORT`), and the host vhost proxies `/api/hub/mcp` there; it
is a process of its own because `apps/api` may not import `apps/mcp`. Over stdio the token
is `REBASE_MCP_TOKEN`, resolved again every minute (`rebase_mcp.actor.CachedAdmin`), so a
revoked or expired token or a demoted owner stops writing within it and not at the next
restart. Design record: `docs/superpowers/specs/2026-09-15-mcp-for-admins-design.md`.

**The server is one module per tool family** (REB-805, REB-892): `tools_freelancers`,
`tools_talents`, `tools_cards`, `tools_companies`, `tools_campaigns`, `tools_team`,
`tools_admin`, `tools_matches` and `tools_fiscal`, each with `register(mcp, deps)`, and
`build_server` (`server.py`) only builds `Deps` and calls them. What they share is
`tools_common.py`: `Deps` (the session factory, the admin, the settings and the transports'
collaborators, `call`/`run`/`run_list` opening one session per tool call and mapping
`DomainError` only) and the parsers every id and number goes through (`to_uuid`, `number`,
`integer`, `day`, `moment`, `as_remote`): a malformed parameter is a `ToolError` in Italian
naming the parameter, never a bare `ValueError`. Tool names, parameters and the Italian
docstrings are the published surface; a new tool goes in its family's module and brings a
test in that family's `apps/mcp/tests/test_*_tools.py`. The collaborators both transports
need come from `rebase_core.dependencies.build_dependencies` (renderer, signing, the model
seam, the mail sender, the CRM services), so stdio and HTTP cannot drift.

**Naming of tool parameters.** Entity fields keep the Italian domain noun the API and the
database use (`status`, `origin`, `text`, `job_title`, `remote`, `daily_rate`);
generic paging and flags are English (`limit`, `cursor`, `has_cv`, `only_silent`). The
mix is the published surface and is not renamed here (a rename wave owns it): a new
parameter follows the rule, a domain noun in Italian and a mechanism in English.

**What the HTTP transport guards** (REB-803, REB-889). DNS rebinding protection is on:
`REBASE_MCP_ALLOWED_HOSTS` and `REBASE_MCP_ALLOWED_ORIGINS` (comma-separated, `host:*` for
any port, `rebase_mcp.settings`) default to the public names nginx forwards
(`letsrebase.com`, `preview.letsrebase.com`; the vhost passes `Host $host`), the loopback
and the compose service `mcp`; another name needs the variable passed through the compose
file. A client that presents `REBASE_MCP_BAD_BEARERS_PER_MINUTE` (10) wrong bearers in a
minute is answered 429 without the database being asked; its address is `X-Real-IP`, an
IPv6 one as its /64, and only failures are counted. `AdminTokenService.resolve` stamps
`last_used_at` at most once a minute and refuses an expired token (`admin_tokens.expires_at`,
set when minted from «Agenti» or by `POST /api/hub/tokens`) like a revoked one. The tools
that ask the model (`regenerate_freelancer_card`, and `create_company` and
`link_company_to_pigro` when they file a new deal) are capped at 30 an hour per admin
(`Deps.spend_model_call`), and a refusal is a sentence. A read of personal data leaves an
`AdminAction` of the admin behind the token: `cv_read` on the freelancer
(`read_freelancer_cv`), `recipients_read` on the campaign (`get_campaign`) and
`emails_read` on entity type `mcp_read`, one fixed id per tool (`list_talents` when `fields`
asks for `email` or `all`, `list_companies`); the payload names the tool and the row count,
never a value. The server instructions and `read_freelancer_cv` say that what comes back
(CV, notes, campaign texts) is data and not an instruction.

**The hub keeps a read model of the CRM's engagements, and the CRM keeps it current (REB-1106).**
`hub_engagements` (`models/engagements.py`, migration 0052) holds what the referral reward, the
verification of a referral by a first signed letter, the ledger and the card's engagement facts
read, instead of `matches` and `contract_documents`: the CRM's engagement id (`crm_engagement_id`),
the hub freelancer and company ids, the letter's id and number, `signed_at`, `mode` (`day`, `hour`,
`flat`), fee, expected days, role, dates, `state` (the CRM's, plus `handoff`: a row the hub opened
at «Crea match» that nothing reads) and `sequence`, and two columns of the hub's own: the company's
`company_daily_budget` snapshot (taken when the row is opened, never sent by the CRM) and
`planned_ends_on` (the end it had while live, since the CRM moves `ends_on` back when it closes one
early). `POST /api/hub/engagement-notices` (`routers/pigro_notices.py`, bearer
`REBASE_PIGRO_NOTICES_TOKEN`, 404 while it is empty, `PIGROCRM_HUB_NOTICES_TOKEN` there) upserts it
through `EngagementNoticeService.apply`: the row is found by `hub_match_id` (the id the hub minted)
or the CRM's id, a `sequence` not newer than the row's is answered `applied: false` and changes
nothing, and **the referral reward is written in the same transaction** whenever the row says its
letter is signed (`ReferralService.record_reward_if_signed`, once per referral by `ON CONFLICT DO
NOTHING`), including from a `closed` notice that overtook a lost `signed` one. The base is
`engagement_reward_base`: a day fee against the daily budget, an hourly fee at 8 hours a day, a
lump sum against the budget times the expected days. `POST /api/hub/referrer-name` answers each
side's referrer with `ReferralService.referrer_name`, only for a verified referral. The ledger's
`engagement_*` fields replace its `match_*` ones. **The match side still works until its removal
card**: the old signing path no longer writes a reward, and `ReferralService.for_matches` reads a
reward's engagement as the match with that id (true of a handed-off or migrated match), which is
the one overlap limit the «Match» list has.

**«Crea match» hands the engagement to the CRM (REB-1105).** `POST /api/hub/freelancers/{id}/engagement-handoff`
(`routers/matches.py`, body `{company_id}`, `EngagementHandoff` in `engagement_handoff.py`) asks the CRM's root
door for the freelancer's organization (`PUT /api/rebase/freelancers/{hub id}/organization`,
`FreelancerDoor.ensure_organization`: the card's name, login address, contact points and fiscal VAT number and tax
code; by hub id, never by name), keeps one `hub_engagements` row in state `handoff` with the company's daily budget
snapshot (opening the same pair again reuses the row and its id; a pair whose engagement the CRM already noticed gets
a new one) and answers `{state: "ready", url, engagement_id}`: the CRM's form at
`{space}/app/engagements?hub_match_id=<minted id>&freelancer_id=<organization>&deal_id=<request's deal>&client_id=<its
customer>`. The space is read off the request's `crm_deal_url` (`{origin}/{slug}/app/deal/{id}`), so no setting names
the leads space twice. A request not linked yet answers `{state: "no_deal", message}` and writes nothing; the page
(`OpenEngagement.tsx`, the route of the old wizard) offers «Riprova» (`/crm/link`) and asks again. 409 carries the
CRM's refusal (an archived organization, a VAT number that is another freelancer's), 502 a CRM that did not answer,
503 no engagements token. The old wizard (`CreateMatch.tsx` and its routes) is unreachable from «Crea match» and
stays until its removal card.

Every match and contract action is a tool too (REB-478): each runs the guard the admin
API runs, then the same core service, with the calling admin as the actor, and answers
`pdf_url`, never the PDF bytes, the tax identifiers or the client's budget. The guards
are the core's `require_live_match` and `require_live_document`, and `SigningService`
comes from the core's `signing_from_settings`, the one builder the API's `SigningDep`,
the MCP transports and `rebase contracts-sweep` share; the tests hand `FakeRenderer` and
a `SigningService` over `FakeDocumenso`.

- `preview_match`: step 3's sentences for the hub's proposal with the given fields and letter type (`regime`, the proposal's when left out); writes nothing.
- `create_match`: the draft match, «Salva senza inviare»; `match_id` makes a retry idempotent; `regime` as in `preview_match`.
- `send_match_for_signature`: «Invia per la firma»; `message` is the page's sentence.
- `resend_signing_mail`: «Reinvia email», for a document still waiting.
- `refresh_contract`: «Aggiorna stato», Documenso's word applied as the webhook would.
- `cancel_contract`: «Annulla» on a framework agreement not signed yet.
- `record_notice`: «Registra disdetta» on an active framework agreement.
- `cancel_match`: «Annulla» on a match in draft or in signature.
- `close_match`: «Chiudi match», the engagement ends.
- `link_company_to_pigro`: «Riprova» on a company request (REB-869), the way `link_match_to_pigro` is for matches: files it in the CRM now as the calling admin and answers the request with `crm_status`, `crm_situation` and `crm_deal_url`; with `deal_id` it is «Collega a un altro deal». `get_company` and `list_companies` carry the same three fields.
- `create_company`: opens a company request for a company met directly, optionally linked to the CRM deal it already has (REB-870); sends no mail.
- `set_freelancer_tax_data`: saves the tax data, the partita IVA optional; answers that they are saved, never the values.

The team builder and the talent cloud are thirteen tools more (REB-520, spec § 8 of
`docs/superpowers/specs/2026-09-25-team-builder-and-talent-cloud-design.md`), each
running the core service the admin area runs, with the calling admin as the actor:

- `list_team_requests`, `get_team_request`: «Richieste team» and a request's page.
- `list_team_proposals`: «Proposte», every «Proponi il team» asked, filed or not.
- `list_ai_config`: «Modelli AI», read only: providers (the hint, never a key), tasks, prices, the month's spend, and `trail`, the last 50 entries of the configuration's trail (keys, switches, prices and every regeneration of the cards, start and end). No tool writes a key.
- `set_team_request_summary`: «Salva il riassunto», refused while it names the company.
- `contact_team_talents`: «Contatta i talenti», or with `only_silent` «Rimanda a chi non ha risposto», on a request filed from the site or the cloud.
- `set_team_request_status`: «Segna come contattata», «Chiudi», with an optional note.
- `get_freelancer_card`, `regenerate_freelancer_card`: the anonymous card, and «Rigenera scheda». The card's `seniority` and `experience_years` are the admin's when `override_freelancer` set them (`seniority`, `experience_years`), Claude's otherwise; `experience_years` is `null` and `seniority_uncertain` true when the CV dates no role (REB-799).
- `set_freelancer_vetted`: «Segna come verificato» and «Togli la verifica».
- `grant_talent_cloud`, `revoke_talent_cloud`, `list_talent_cloud_grants`: the cloud's grants.

**No team is proposed over MCP, and a search there writes to nobody** (REB-876, Lorenzo
2026-11-02). `propose_team` was a tool until then and is gone on purpose: an agent finds
the people for a request with `list_talents`, `get_talent` and the cards, which spends
nothing on the team builder's model, and a person writes to the candidates by hand, from
drafts the agent can prepare. `contact_team_talents` went with it and came back the same
day, at Lorenzo's word: it is the one availability mail the server sends, to the talents
of a request a company filed from the site or the cloud, and only when an admin asks.
«Proponi il team» stays on the public page and in the talent cloud (the admin area only
reads «Proposte»), and the server's instructions say all of this. Rows with `origin`
`admin` are older than this and stay readable.

«Modelli AI» (`/hub/admin/ai-models`, REB-833, `routers/ai.py` under `/api/hub/admin/ai`) is
where an admin replaces a provider's key (write-only: the answer is «configurata, finisce con
…abcd»), picks each task's provider, model, effort, token and daily euro cap, edits the price
table and reads the month's spend. «Prova» sends one fixed non-personal prompt through the
task's own seam, recorded as `purpose="test"`, so it spends the task's cap. The tasks are
`card`, `team_proposal`, since REB-862 `anonymous_cv` and, since REB-867, `crm_lead`. «Prova» of
`crm_lead` sends an invented request and shows the draft as the deal would take it.

There is no «Valutazione» page: the evaluation is the command `rebase cards-eval` (REB-842, below),
which an operator runs and reads, and the admin API has no `card-eval` route.

`get_talent` and `list_talents` carry `vetted_at` and whether an anonymous card exists.
Since REB-778 each `list_talents` row also carries the card's `job_title` and the
anonymous card's `role`, `seniority`, `experience_years`, `skills` and `location`, and the tool
filters by `skills` (every term, in the position or the card's role, skills or
summary) and by `location`, so an agent matches a request in one call instead of one
`get_talent` per profile.

**`origin` is the value a row shows** (REB-798): `form` (a bare sign-up), `wizard` (the
person filled the card) or `admin` (drafted from research), derived from
`compilata_da`. The filter `origin` takes exactly those three and refuses anything
else (a value that selects nobody read like «no such people» before). The marketing page
a person started from (`home`, `pigrocrm`, `Freelancer.origin`) is `origin_page`, in
the list service, `GET /api/hub/talent`, the admin Talenti filter, and as its own key in
`get_talent`. A stored campaign filter still calls that page `origin` (no data
migration): `campaigns/audience.py` maps it on replay. `get_talent` is the list's row
for that person (`TalentsService.row_for_freelancer`, one builder `talents.card_fields`
for both) laid over the admin detail, so a card has `industries`, `languages`, `profile_summary` and
`band` there without `get_freelancer_card`; a test compares every `TalentRead` field
of `get_talent` with the list row.

**`list_talents` is for ranking a request** (REB-801). Beside `skills` and `location` it
filters by `seniority` (any of junior/mid/senior/lead), `min_years`/`max_years`, `industries`
and `languages` (every term, a substring of one element, like `skills`) and `card`
(`present`; `failed` when Claude recorded a failure and there is no live card;
`absent` for neither); a value outside the allowed ones is an error, not an empty list.
`sort` is `recent`, `rate_asc|desc` or `experience_years_asc|desc`: the cursor then carries
that value and the id under the whole `sort` as its key (`SortSpec("rate_asc",
"float")`, so a cursor cannot be replayed in the other direction), and a row with nothing to
rank by (every lead, a card with no rate or no years) is left out of the list **and of
`total`/`by_status`**, so the order stays one sorted query and not a merge with a
nullable key. A cursor from another order or another `q` is refused. `experience_years` ranks and
filters only a whole JSON number, the one the row shows.
The tool answers a **short row** (`id`, `first_name`, `last_name`, `status`, `role`, `seniority`,
`experience_years`, `daily_rate`, `location`, five `skills`, `profile_summary` cut to 160
characters; empty keys left out) and no address. `fields` names the fields wanted, whole
(`["email", "industries"]`), and `["all"]` is the whole `TalentRead` row. The page is
compact JSON text with no structured copy: the server's usual dict return sends the page
twice, once indented by two spaces. Fifty short rows are about 25 KB where the old full
row was about 55 KB, and a call now answers 50 rows by default (it was 100) and 200 at
most (it was 500; the service itself still allows 500 for the campaign audiences). The
shape lives in `apps/mcp/src/rebase_mcp/talents_view.py`. The admin Talenti screen and
`GET /api/hub/talent` change in `origin`/`origin_page`, and their rows gain
`industries`, `languages`, `profile_summary` and `band`.

The campaigns are read-only over MCP (P-REB-41 phase 2), so an agent reports on a
campaign without a terminal:

- `list_campaigns`: every campaign, newest first, with its `counts`, clicks, entries and
  actions included. Paged (REB-804): `limit` (20, at most 100) and `cursor`; `next_cursor`
  is `None` on the last page.
- `get_campaign`: one campaign, with each person's outcome, and `signed_in_from_mail` /
  `action_from_mail` when the mail's own link was the door. The recipients, by address, are
  paged the same way (`limit` 50, at most 200, `cursor`); `counts` count the whole campaign.
  `CampaignService.list_all` and `detail` take the same `limit`/`cursor`, `None` for the
  admin screen, which reads everything.

## The anonymous CV as a PDF (REB-862)

An admin's «Scarica CV anonimo» sends a talent's CV to a client company without the person.
Spec: `docs/superpowers/specs/2026-10-30-anonymous-cv-pdf-design.md`.

- **The task.** `anonymous_cv` in «Modelli AI» (`ai_tasks`, seeded by 0044 from the `card` row,
  `max_output_tokens` 16000): its own call, never part of the card's. `deps.get_cv_llm` builds
  the seam; the writer names the talent on each request (`LlmRequest.freelancer_id`, as the
  card's calls do since REB-861), so `ai_usage` names the person.
- **The table.** `anonymous_cvs` (`AnonymousCvRow`) keeps the last body per freelancer, keyed
  by the SHA-256 of the CV file and `CV_PROMPT_VERSION`; it goes with the person. **Only
  `AnonymousCvWriter` and `clear_cv` touch it; the merge leaves it in place** (`LEFT_IN_PLACE`:
  a body is served only for the exact CV bytes it was written from). The card, the cloud, the team
  builder, the member area, the MCP and the lists never read it: the body is an
  admin-requested file, not profile data.
- **The route.** `POST /api/hub/freelancers/{id}/anonymous-cv` (`routers/admin.py`, a plain
  `def`: a first press waits on the model and the print, in the threadpool) answers `application/pdf` as an attachment named
  `rebase-cv-<slug>-<ref>.pdf`, where `ref` (the footer's «Rif.») is the last 8 characters
  of the id: the first 8 of a UUIDv7 are its creation time, shared by talents created
  together and telling when the person signed up. 404 no talent or no CV, 422 `AnonymousCvFailed` (the guard
  or the model failed; nothing stored), 502 when the task is off and nothing kept can be
  served (a kept, still guard-clean body is served with the task off) or the provider failed,
  503 `CvPrintFailed`. The page's head takes seniority and years from the card as the admin
  corrected them, the city from the card, the work mode from the profile; nothing from a
  card of another CV file, and no seniority the card marks uncertain unless an admin set
  one.
- **The audit.** One `anon_cv_downloaded` entry on the freelancer per 200, payload
  `{"reused": bool}`. The name is short on purpose: `admin_actions.kind` holds 20 characters and `AdminActionService.record` refuses a longer one.
- **The wait.** The task's calls wait 150 seconds and are never retried (`llm.py`,
  `_TASK_HTTP_LIMITS`); the host vhost gives the route 330 seconds, a by-hand change on the
  installed copies (below, after the team builder's 90 seconds).
- **Logs.** Nothing here logs the CV, the answer or a name.

## The CRM draft task (REB-867)

`crm_lead` (`rebase_core.crm_lead`, migration 0045, seeded from the `team_proposal` row, effort
`low`, 1500 tokens, 1 euro a day, **off**) drafts a CRM deal from a company request. Spec:
`docs/superpowers/specs/2026-10-30-company-requests-into-the-rebase-crm-design.md` § 3. The model
reads `requested_role`, `project`, `duration` and `period_from` and nothing else: never the
company's name, the referente or the budget (`draft_lead` takes no such argument). Its answer's
schema is lenient on purpose and `LeadDraft.from_output` holds each field to its bound (ruolo 80
characters, days 0.5 to 5, weeks 1 to 104 whole, ten skills of 40, sintesi 800): a value outside
is dropped, never clamped. A call that cannot answer gives `None` and the filing goes ahead with
no draft. Switch it on in «Modelli AI» only once `privacy.html` names what it sends.

## A request filed in the CRM (REB-868)

Every request written by `CompanyService.request` (the wizard) or
`MemberService.create_additional_request` (a member's second one) is `crm_stato =
da_collegare` in the transaction of its row; a row written before migration 0046 has `NULL`
and the sweep never picks it up (no backfill: it reaches the CRM only by an admin's «Riprova»
or «Collega a un altro deal»). `rebase_core.crm_link.CrmLeadService` is `EngagementService`'s
twin: `link(company_id, admin_id, deal_id)` locks the row to read what is sent and stamp the
attempt, **releases it before the AI call and the HTTP call**, and locks again to write the
answer; the draft of `crm_lead` is made once and kept in `crm_draft`, so a CRM that is down
is retried without spending the cap again. The call is `PUT {pigro_api_url}/api/rebase/leads/
{id}` under `REBASE_PIGRO_ENGAGEMENTS_TOKEN` (no token: the request waits as `da_collegare`);
`201`/`200` is `linked`, `422` is `rejected` (only a person asks again), anything else
`error` (the sweep retries). The deal is built in code and every value is cut to the CRM's
limits; `hourly_rate` and `budgeted_hours` are never sent. The first attempt is a
`BackgroundTasks` job (`deps.get_crm_filing`); the sweep is `crm_sweep` in `rebase
contracts-sweep`, its own round and its own line, outside `SigningService`, and leaves a row
alone for five minutes after it was written or attempted so it never races that job.
`POST /api/hub/companies/{id}/crm/link` is «Riprova»; with `{"deal_id"}` it is «Collega a un
altro deal»: no draft, and a refusal is a 409 that changes nothing stored (the sweep would
otherwise file the request by its address and make the second customer being corrected).
The trail entry is `crm_link`. The admin company page shows the state, the CRM's sentence and
the deal's link in `pages/admin/CrmLink.tsx`, with «Riprova» and «Collega a un altro deal»;
`link_company_to_pigro` is the same for agents (REB-869).

**An admin opens a request for a company met directly (REB-870).** `POST /api/hub/admin/companies`
(`CompanyAdminCreate`: the wizard's fields, validated the same way, plus `deal_id`) writes the
wizard's row through `CompanyService.create_by_admin` (the referente found or created by email,
**no mail to anybody**, the trail entry `company_created` with the admin, `source` and the deal).
Without `deal_id` it is a request like any other: `to_link`, first attempt in the background,
a new deal in the CRM. With it the row is written with `crm_deal_id` set and the CRM is asked at
once: the door links that deal and creates nothing, so the answer is `linked`, or `rejected`
with the CRM's sentence when it does not know the deal. **The named deal stays on the row until it
is linked** and `CrmLeadService.link` asks for it again whoever repeats the attempt («Riprova», the
sweep): a transient `error` must not end in the request being filed by its address, which is the
second customer the deal was named to avoid; only a refusal of a deal *named in the call* is raised
instead of written. The admin screen is `pages/admin/CreateCompany.tsx` at `/admin/companies/new`
(«Nuova richiesta» on «Aziende»), with a field that takes the deal's address or id (`lib/crm.ts`).
`create_company` is the same for agents.

## MCP surface

The tools this step adds to the hub's MCP server (the rest are listed under «The MCP server is an
admin's, by token»):

- `create_company`: opens a company request on the referente's behalf with the wizard's fields
  (`company_name`, `contact_first_name`, `contact_last_name`, `email`, `phone`, `requested_role`,
  `project`, `period_from`, `duration`, `daily_budget`, `remote`, `headcount`,
  `onsite_days`) and, optionally, `deal_id` (the id of a deal the CRM has, or its
  `.../app/deal/<id>` address). Sends no mail. It files the request in the CRM before answering
  when the engagements token is configured (it has no background task) and answers the request with
  `crm_status`, `crm_situation` and `crm_deal_url`; without the token it stays `to_link` for
  the sweep.

## One person, once (REB-800)

A person is their address **or** their LinkedIn profile. `rebase_core.linkedin` is the one place a
profile is checked and given its stored shape, `https://www.linkedin.com/in/<name>` in lower case with
no query, slash or locale, and `linkedin_key` is what two profiles are compared by; `User` and
`Signup` normalise on write (`@validates`), and `uq_users_linkedin_lower` and
`uq_signups_linkedin_lower` hold the rule in the database. `UserService.find_person(email,
linkedin_url)` is the lookup every form goes through: the person who answers to the address (an
address merged away answers for the survivor, `User.survivor`), else the one holding the profile.
So the sign-up form attaches a second address with a known profile to the row already there, the
wizard to the card already there (and mails the **address on file**, never the one typed, with
`ALREADY_HAS_CARD_BY_LINKEDIN_NOTE`), and an admin's draft or an edit that names a profile already on
a card is refused with `LINKEDIN_HAS_CARD` / `LINKEDIN_TAKEN`. A sign-up whose profile is on a card is no
lead (`rebase_core.leads.card_by_linkedin`, in the three lead lists).

Two live cards that share a profile are a *unibile* pair (`rebase_core.duplicates.find_duplicates`);
the same name or telephone number only flags (`duplicate_possible_of` on a Talenti row and in
`get_talent`, `to_review`), never merged on its own. A Talenti page reads only its own cards
and the cards sharing a key with them (`duplicates.duplicates_of`, two statements however many
people there are); the whole-table read is `find_duplicates`, for the report and the command.
`PersonMergeService` (`rebase_core.merge`;
the relations table is `merge_relations.py`, the CV/profile/identity parts `merge_steps.py`)
merges one card into another: admin «Unisci» (`POST /api/hub/freelancers/{id}/merge`), MCP
`merge_freelancers` (`list_duplicates` lists the pairs, without addresses), and
`rebase talent-duplicates` (dry run unless `--apply`). Every relation moves by id (`merge_relations.RELATIONS`, with
`merge_relations.LEFT_IN_PLACE` naming what stays and why; `test_every_foreign_key_to_a_person_is_moved_or_left_on_purpose`
fails on a table added later that points at a person), the newest CV (`freelancers.cv_uploaded_at`)
stays with its card and the card is flagged for `cards-refresh`, the absorbed card is soft-deleted and
its `users` row points at the survivor (`merged_into_id`), and the `merged` entry in the trail names
every moved row so `--undo` can put it back. Production runbook, order of steps, the dump taken first
and the undo: `docs/merge-duplicate-talents.md`.

## Several ways to reach a person (REB-943, REB-944)

Spec: `projects/pigrocrm/docs/superpowers/specs/2026-11-04-contact-points-design.md` § 4.
`user_contact_points` (`UserContactPoint`, migration 0050) is owned by `users`, which is both a
freelancer's and a company contact's record: `kind` (`email`, `phone`, `linkedin`), `value`, `label`
(`work`, `personal`, `billing`, `other`), `note` (200 characters), `is_primary`, `position`.
`rebase_core.contact_points` is the one writer (`ContactPointService.replace` takes the whole list,
the way a deal's lines are replaced in the CRM) and the one place a value is checked and compared:
an address by its lower case, a number by its ASCII digits, a profile by `linkedin.normalise_linkedin`.
The unique indexes say the same in SQL (one primary per person and kind, one value per person and
kind through the generated `value_key`, one owner per address).

- **The login address is not a row.** `users.email` stays the identity, receives the magic link and is
  what campaigns write to (`campaigns/service.py` freezes `candidate.email`, a `users.email`, and the
  `request_link` path asks `by_email` only). Every read answers it first as the primary email, without
  an `id` (`ContactPointService.present`); it is never in the list a write sends. Sending a list read
  back as it is works: the entry that is the login address and primary is left out; any other point
  equal to it is refused. A stored `email` point is always extra, never primary.
- **An address is one person's.** An extra address that is another person's login address or extra
  address is refused with a sentence that names it (`ValidationFailed`, 422 over the API, a `ToolError`
  over the MCP). The login addresses of people merged into this one are this person's own.
- **Extra addresses never decide who somebody is.** They are unverified, so `find_person`,
  `get_or_create`, the wizards, the admin's draft and `MemberService.lookup` read login addresses
  only. A sign-up or request under somebody's extra address makes its own person, and
  `duplicates` flags the pair with the signal `email` (`to_review`, never mergeable on its own;
  `duplicate_possible_of` on a Talenti row): the admin merges with the usual flow. An extra
  address is never a way in, and `promote`/`set_role` refuse to create a login under one.
- **`phone` is the primary phone point**: `User.phone` is a read-only property over the `contact_points`
  relationship (`viewonly`, lazy; the two admin lists load it with `selectinload`), so `FreelancerRead`,
  `CompanyRead`, `MeRead`, the CRM payload (`crm_link`, whose shape does not change), the cloud caller and
  the CV comparison read what they always read. `duplicates` compares the primary phone only.
- **A merge carries the points.** `ContactPointService.absorb` moves the absorbed person's points to the
  survivor (a value the survivor has stays theirs, a primary they have is kept) and adds the absorbed
  login address as an extra one; the undo puts back what is still there (`left_behind.contact_points`
  counts what the survivor changed since).
- **Who writes.** An admin: `contact_points` on `FreelancerOverride` and `CompanyOverride` (the contact is
  the owner user), over the API (`PATCH .../override`) and the MCP (`override_freelancer`,
  `override_company`); the delta is one `overridden` entry with the lists before and after, and a revert
  sends them back. `null` is refused, `[]` clears. The person: `PUT /api/hub/me/contact-points`
  (`MemberService.update_contact_points`), which leaves a note of the person's own. The web editor is
  `components/ContactPointsEditor.tsx`, used by both override dialogs and by «I tuoi recapiti»
  (`pages/member/Contacts.tsx`).
- **Adding points at creation (REB-948).** `create_company` (`CompanyAdminCreate.contact_points`, so the admin
  API takes it too) and `create_freelancer_from_signup` (`FreelancerDraft.contact_points`) take a list that
  `ContactPointService.add` merges into what the person already keeps: their own points stay, a point they
  already have (same kind, same compared value) is left as it is, a new one is never primary on its own
  (`is_primary` is accepted and ignored; the first phone or profile becomes primary when there is none, and
  for a request the flat `phone` stays the primary number), and the whole list goes through `replace`, so
  every rule of a write applies. `check_added` runs before anything is written, so a refused point (another
  person's login or extra address) leaves no contact and no request behind; the check counts the flat `phone`
  the same write stores, so the cap and the duplicates cannot pass it and fail after. The trail keeps what
  was added: `contact_points_added` in the `company_created` entry (only the new points), and for a draft an
  `overridden` entry with the lists before and after plus a line in its note. The CRM gets the points with the
  request, as for any stored point.
- **Search reads the points (REB-948).** `q` of the admin Talenti and Aziende lists, and so of the MCP
  `list_talents` and `list_companies`, also matches a person's extra points
  (`contact_points.has_point_matching`): the text of any point, or a phone by its digits (at least three, the
  CRM's rule). There is no trigram index on `user_contact_points.value`: the table is small and the match is
  a correlated `EXISTS`. A person whose point is the term (same text, or a phone with the same digits) ranks like an exact
  address (1.0); one whose point only contains it ranks at 0.5 (`point_match_score`), below a name or address
  that equals the term. The sign-up leads
  have no user, so their search is unchanged.
- **The CRM gets them (REB-945).** `crm_link.py` sends the contact's stored points as
  `contact.contact_points` on the leads door payload, never the login address (that is `contact.email`),
  leaving out a point the CRM would refuse; `contact.phone` is still the primary phone point.

## One thread of notes (REB-856)

Everything said about a freelancer or a company is a row of `notes` (`rebase_core.notes`,
`NoteService`); the `note` column of both tables and the append-only `comments` of ORB-59
are gone (migration 0042 moved every old `note` in as a note signed «Nota precedente», and
renamed `comments`). A note has `author_id` and `author` (the user and their name as it read
then; a caller never supplies the name), `created_at`, and `source`: `web` (the admin area),
`mcp` (an agent over a token), `person` (the person's own change of profile, CV or request,
written by `MemberService`) or `system` (the merge's line); NULL is a note from before the
tracking. So **the text carries no date and no author**: the MCP tool descriptions tell the
agent so. An admin edits and deletes the notes whose `source` is `web`, `mcp` or NULL
(`schemas.note_is_editable`); `person` and `system` are events and are refused in a
sentence. An edit stamps `edited_at` and `edited_by_id`; a delete is soft
(`deleted_*`), and lists and `get_freelancer`/`get_company` leave deleted notes out.
Every admin add, edit and delete is an `AdminAction` on the parent (`note_added`,
`note_edited`, `note_deleted`: `note_id`, `source`, the text, or `before`/`after`), and
`set_status` on both services takes `admin_id` and `source` and writes `status_changed`
(`before`, `after`, `source`) when the state moved: the door of an action is always in the
trail. `StatusChange` is the state alone (a stale client sending `note` gets a 422).

- API (`routers/admin.py`, source `web`): `GET/POST /api/hub/freelancers/{id}/notes`,
  `GET/POST /api/hub/companies/{id}/notes`, `PATCH /api/hub/notes/{note_id}`,
  `DELETE /api/hub/notes/{note_id}` (204).
- MCP (source `mcp`, the actor is the token's admin): `add_freelancer_note`, `add_company_note`,
  `update_note(note_id, text)`, `delete_note(note_id)`; `set_freelancer_status` and
  `set_company_status` take the state alone, and none of them has an `author` argument.
- A revert of an old `overridden` entry on the field `note` is refused with a sentence: a note
  is edited in the thread. A merge moves the notes by id (`RELATIONS["notes"]`; an undo of a
  merge made before the rename still finds its rows under the old key `comments`).
- The web page is `pages/admin/Notes.tsx`; `AuditTrail.tsx` reads the new kinds.
- PostHog's warehouse read `comments`: it reads `notes` once its schema is refreshed (the
  runbook, `docs/adding-a-project.md` § 7). `TeamRequest.note` is another thing and stays.

## The guide is a generated file, committed, and easy to leave stale

`content/guide-first-steps-freelance.md` is typeset by `tools/build_guide_pdf.py`, with
pandoc and Typst, into `packages/core/src/rebase_core/perks/`, and the result is
**committed**. It is the one build output in git here, and the script's docstring says
why: the alternative puts those two binaries plus fontTools inside `Dockerfile.api` for
one document.

It is served by `GET /api/hub/me/guide`, which depends on `MeDep` and nothing else, so
the perk of being signed in is that the route answers at all (`MemberDep` until
REB-278 unified member and admin sign-in). There is no public URL for the file, and
the website links to the wizard instead (ORB-70).

What that costs is a file that can fall behind its sources, so after editing the
Markdown, the template, the palette or the typeface run
`uv run python projects/hub/tools/build_guide_pdf.py` and commit both the PDF and
`tools/guide-pdf.lock.json`. Forgetting is a failing test rather than a stale download:
`packages/core/tests/test_guide_pdf.py` compares every source's hash with that lock,
`apps/web/src/lib/perks.test.ts` compares the page count and size the member area shows,
and the `guide-pdf` preflight check rebuilds the bytes. Comparing bytes is meaningful
only because the build is reproducible on purpose: `--creation-timestamp 0` for Typst,
`recalcTimestamp=False` for the font instances.

## The contracts are typeset at request time

Since REB-387 the API writes the framework agreement and the letter of engagement itself,
with `rebase_core.contracts`: pandoc and Typst over the Markdown in
`packages/core/src/rebase_core/contracts/texts/`, the template beside it, and the
palette, the typeface and the echo logo read from `shared/brand/` at the paths the image
mirrors. So
`Dockerfile.api` carries PigroCRM's pandoc and Typst (`test_api_image.py` holds the two
images to one pair) and fontTools is a dependency of `rebase_core`. `rebase
contracts-check` typesets every text from fiction and says whether a machine can; the
`hub-image` preflight check and CI's image job run it inside the built image. Who signs
for rebase comes from `REBASE_SIGNER_JSON` in the host `.env`, never from the repository.

Since REB-687 there are two families of the same two documents, named by (kind, regime)
in `rebase_core.regimes`: `framework-agreement.md` and `engagement-letter.md` for a member
who works with a partita IVA, `framework-agreement-occasional.md` and
`engagement-letter-occasional.md` for a member paid as a prestazione occasionale.
Every `contract_documents` row carries its `regime`, and a match's regime is its letter's:
«Tipo di lettera» on step 1 of «Crea match», proposed from the tax data, whose partita IVA
may be missing; ticking «Senza partita IVA» there also switches «Tipo di lettera» to
«Prestazione occasionale». Every lookup of a freelancer's framework agreement takes a
regime, so a freelancer may hold one of each, a signature releases only the letters of its
own regime, and «Match e contratti» shows one framework card per regime. An ordinary
letter needs a saved partita IVA; the occasional texts never print one, and an occasional
letter's payment term counts from the receipt. Design record:
`docs/superpowers/specs/2026-10-12-prestazione-occasionale-design.md`.

Since REB-979 a letter may pay «a incasso»: the optional `collection_days` (`giorni-incasso`,
1 to 60) on `LetterDraft`, «Condizioni» and the MCP `terms`. `checked` then writes the
payment term as that many days after the client has paid, and in any case within 60 days of
receiving the invoice (the receipt, on the occasional letter), and the letters' «Pagamento a
incasso» paragraph, between `{{se giorni-incasso}}` and `{{fine se}}`, prints; without the
field a letter is what it was. Both framework agreements were raised to 1.1 for it; an
agreement signed at 1.0 keeps its text (article 17.3), so the letter itself carries the
consent. The wording awaits the overall legal review of the contracts
(`docs/design/DECISIONS.md`, 2026-11-09).

## Contracts are signed on Documenso

Since REB-387 phase 3 «Invia per la firma» sends a match's documents through Documenso
(`rebase_core.signing`, `rebase_core.documenso`), and the hub mails the signing link
itself: Documenso sends no mail of its own. Documenso calls back
`POST /api/hub/documenso/webhook` with `X-Documenso-Secret` equal to
`REBASE_DOCUMENSO_WEBHOOK_SECRET`, which must be long and random (`openssl rand -hex
32`): the route sits public behind `/api/hub/` with no rate limit, and for a rejection
or a cancellation the secret is the only guard. A completion is not moved on the secret
alone (REB-431): the webhook only marks which document to confirm, and `finish` reads
the envelope back with the hub's own API token before the document counts as signed, so
a secret leaked to somebody who never held the token still cannot forge a signature.
Production's webhook points at
`http://api:8000/api/hub/documenso/webhook` inside the compose network, preview's at
`https://preview.letsrebase.com/api/hub/documenso/webhook`. Documenso retries a failed
delivery only at once, so an event lost while the API restarts is not lost for good: the
sweep (below) reads the envelope and applies it on its own, every ten minutes, and
«Aggiorna stato» on «Match e contratti» does the same thing at once, for an admin who
does not want to wait for the next sweep. Without
`REBASE_DOCUMENSO_URL` and `REBASE_DOCUMENSO_API_TOKEN` signing answers 503, and a text
whose front matter says `status: draft` never leaves unless `REBASE_CONTRACTS_ALLOW_DRAFT`
is true; only the preview's `.env` sets it, and even there it stays false while both
texts are `status: final`, ready again for the next draft. Documenso reaches `api` only if
`NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS` lists it (probe § 5); the secret travels in
clear, so the webhook URL stays on the compose network or is HTTPS.

The webhook's own follow-up starts with that confirmation, then the sealed copy's
download and mails, a framework agreement's waiting letters, all in the background,
after the response: a restart caught mid-confirmation leaves a signed document plain
`sent`, not even `signed` yet, until the next sweep tries it again; caught later, it
leaves whichever step ran undone. `rebase contracts-sweep` redoes anything a restart, or
a mail the provider refused, left behind, and runs every ten minutes on production and
the preview alike, from the `sweep` service in `docker-compose.yml` (REB-393). Read what
it did with `docker logs rebase-sweep-1` (production) or `docker logs
rebase-preview-sweep-1` (preview): each run prints «N documenti ripresi», and, when
Documenso itself refused a confirmation or could not be reached (an expired, revoked or
wrong token among them, REB-431), «, M non confermati» on the same line. Then it links
every active match still waiting for its deal on Pigro, or failed last time (REB-499),
and adds «, N match collegati a Pigro», and «, M non collegati» when any stayed unlinked
(the reason is on each match, `pigro_error`); without `REBASE_PIGRO_ENGAGEMENTS_TOKEN`
it links nothing and says 0.

**How the contracts code is laid out (REB-892).** No module passes 800 lines. A match's
code is `rebase_core.matches.MatchService` (`create`, `cancel`, `close`) built from
`reads` (`match_reads`: `get`, `list_all`, `for_freelancer`, `document_pdf`), `drafts`
(`match_drafts`: `prefill`, `proposal`, `preview`, `check`, which write nothing) and
`documents` (`match_documents`: what the documents print, `write_framework`); the shared
rows and the **global lock order** (the freelancer's row, then the match, then its
documents) are the functions of `match_rows`. `SigningService` (`signing`: `send_match`,
`apply`, the plumbing) holds `recovery` (`signing_recovery`: `finish`, `sweep`) and `admin`
(`signing_admin`: `refresh`, `resend_mail`, `cancel_document`, `cancel_match`,
`record_notice`); the sentences they store are `signing_words`. The signing `dispatch`
calls Documenso under the freelancer's row lock on purpose: it is one admin's click and
the document must roll back when Documenso refuses, so the latency is the price. The CRM
door is one module, `pigro_door` (`PigroDoor.put` never raises and maps the status to the
state the row is left in, `PigroDoor.get`, `write`, `committed`, `sweep_round`): the
registry (`pigro`), the engagements link (`engagements`, its report maths in
`engagement_report`) and the leads link (`crm_link`) pass it a path, a token and the row's
column names, and the rule that **no database lock is held across the CRM's call** is written out in
its docstring.

## The `rebase` command (REB-892)

`rebase_core.cli` is a package, one module per group of commands (`members`, `cards`, `campaigns`,
`signing`, `ai`) and a `REGISTRY` of names and help in `cli/__init__.py`; `main` imports a
group's module only when one of its commands is chosen, and a command imports what its own work needs
inside its handler, so `rebase campaigns-tick` does not load the model providers' libraries. A command
is a `Command` (`cli/common.py`) in its module's `COMMANDS`. **The three compose loops stay shell
loops that start `uv run rebase <command>` each tick** (REB-892, HMCP-10): a long-lived Python process
per loop would hold about 100 MB each on both stacks, on an origin with little memory to spare
(measured 2026-11-02, the idle loop containers are about 6 MB), so the cost per tick is cut by the lazy
imports instead. `rebase createtoken` prints the token once on
stdout, or with `--token-file PATH` writes it there alone (mode 0600, never over an existing file),
and `--expires-in-days N` sets its expiry; `createtoken` and `setrole` prompt only at a terminal and
otherwise refuse with exit code 2.

## Campaigns

Since P-REB-41 «Invia una campagna» the `campaigns` service in `docker-compose.yml` runs
`rebase campaigns-tick` on a loop, every minute, on both stacks. Each pass is one call
of `run_tick` (`rebase_core.campaigns.tick`), inside a session advisory lock so two
passes never overlap: it takes every campaign whose `scheduled_for` has come, and
every campaign an earlier pass left `sending`, and sends their queued recipients
through `campaign_sender_from_settings` (`rebase_core.campaigns.sender`), one mail at a
time and one mail a second, leaving Resend's other request a second to the magic link.
A loop, never a one-shot, for the same reason as `sweep`: `_deploy-compose.yml` fails a
deploy on any container that is not `running`. Without `REBASE_RESEND_API_KEY` the
command prints why and exits 0, so the loop keeps running and sends nothing. The
preview's API has `REBASE_RESEND_API_KEY` set, production's key, on purpose, but no
`REBASE_RESEND_WEBHOOK_SECRET`, so `_ready_to_send` refuses «Mandami una prova» and
«Invia» there (`NO_WEBHOOK`): the preview's loop runs, it just never has a sent row to
send. Read what it did with `docker logs rebase-campaigns-1`
(production) or `docker logs rebase-preview-campaigns-1` (preview): each run prints one
line, «N campagne, M inviate, S saltate, F fallite». `campagne` is how many due
campaigns this pass touched, and `sent` and `skipped` are recipients this pass
actually sent to or skipped. `failed` are rows this pass marked `failed`: a mail
Resend refused outright (a 422, say), a row whose third attempt failed too (a timeout,
a bare 429 or a 5xx leaves the row `in_coda` for the next pass, with the same
`Idempotency-Key`), and a row whose checks or render raised, marked rather than let it
wedge every campaign after it (controller ruling R14). A 401 or 403 (a revoked or
restricted key, a domain Resend no longer sends for) marks no row: the pass stops that
campaign with its rows still `in_coda` and logs «campaign <id> stopped this tick:
Resend 401», and the send resumes on the first pass after the key is fixed. The pass
also stores the stop on the campaign (`stalled_at`, `stalled_reason`, REB-524), and so does
a campaign whose own list raised, so «Campagne» and the campaign's page read «Invio
fermo: Resend rifiuta l'invio: controlla la chiave e il dominio del mittente» (or «la
lista non si legge») instead of «Parte il…» until the first mail that leaves clears it.
The one sentence covers both causes of a 401/403, since only the status is kept. No
address and no key ever appears in these lines or anywhere else in the logs.

**A daily ceiling holds the rest of a long list for the next day** (REB-779). Resend's
free plan sends `REBASE_RESEND_DAILY_QUOTA` (default 100) mails a day, and the magic
link and the member mails use the same key. A campaign may send that less
`REBASE_CAMPAIGN_DAILY_RESERVE` (default 25, kept for the magic links, the member
mails and the campaign test mails, which are not rows and are not counted), so 75.
`campaign_room` (`rebase_core.campaigns.tick`) counts the `campaign_recipients`
sent since midnight in Rome by `sent_at`, across every campaign; right before
each mail, at zero the pass stops that campaign with its rows still `in_coda` and no
attempt counted, and stores «limite di N mail al giorno raggiunto: il resto parte il
DD/MM dalle 00:00 (ora di Roma)» as the stop (`stalled_reason`), so «Campagne», the
campaign's page, `list_campaigns` and `get_campaign` say when the rest leaves. The
first pass after midnight in Rome sends it and clears the note. A 429 whose body
names `daily_quota_exceeded` or `monthly_quota_exceeded` (a sender outcome of its own,
`quota`) is the same stop: no attempt is spent and the row is tried again next pass,
since Resend's day is a UTC day, which starts one or two hours after Rome's midnight:
in that window the ceiling has reset while Resend's count has not, so a campaign may
spend the reserve until the 429 stops it. A bare 429 is still a retry that counts.

**The same pass stamps what each mail led to** (P-REB-41 phase 2, `rebase_core.campaigns.outcome`).
For every row sent in the last 30 days and still missing a stamp, it writes `signed_in_at`
(the first login after the mail) and `action_at` (the campaign's action, from the table
that records it: the CV note, the card's `created_at`, the request's `updated_at`,
the tick itself for a card that became complete, or the first click for «Un link»,
whose page the hub never sees, REB-530). A stamp is written once. The log line
ends with `N esiti registrati`. The preview's `campaigns` loop runs, since it has a
Resend key; it is the missing webhook secret that keeps «Mandami una prova» and «Invia»
refused there, so the preview never has a sent row to stamp.

**«Riscrivi a chi non ha fatto niente»** (`POST /api/hub/campaigns/{id}/follow-up`) makes a
`draft` with `source = list` and `follows_id`. It keeps the earlier campaign's action and a
copy of its mail, and only the mail can change (`LIST_IS_FIXED`). Its list is the earlier
campaign's sent rows with no action, each checked live with `done_at_many` (one statement per kind of fact for the whole list), less the rows
whose mail bounced or drew a complaint, which the page's «Riscrivi (N)» leaves out too. The gap rule
applies, so a follow-up drafted within `REBASE_CAMPAIGN_GAP_DAYS` of the send lists
everyone as excluded, with the date.

**Scripts written for one campaign wave never live under `/opt/hub`.** The deploy syncs
the whole repository there with `rsync -az --delete` (`.github/workflows/
_deploy-compose.yml`), so anything dropped into the checkout by hand that is not in the
repository is gone on the next deploy: a one-off `outreach-r2` directory of scripts for
the September waves, placed under `/opt/hub` rather than committed or kept outside the
checkout, was wiped this way on 24/09. A wave's own scripts belong in the repository (if
they are worth keeping) or outside `/opt/hub` entirely, the same rule `REBASE_DATA_DIR`
follows for Postgres's own files.

**Six rules keep an address out of a campaign, and only one of them is an opt-out.**
`exclusions()` (`rebase_core.campaigns.audience`) runs when the list is shown, when it
is frozen and again right before each mail, and leaves out, with the reason on screen:
an admin, by `User.role == "admin"` (`REASON_ADMIN`, no row needed); a person whose card
an admin marked «scartato» (`REASON_DISCARDED`, DECISIONS.md 2026-09-28); an address with a
row in `campaign_optouts` -- `source='link'` for the recipient's own unsubscribe,
`'complaint'` for a spam complaint Resend reports, `'admin'` for «Non scrivere mai»,
which is how the team goes in rather than relying on the role check; an address that
hard-bounced on any earlier campaign (`campaign_recipients.bounced_at`, no optout
row); and an address another campaign reached in the last `REBASE_CAMPAIGN_GAP_DAYS`
(3), the gap rule that also turns several campaigns scheduled for the same minute into
one mail per person. The sixth is REB-911's: a person with a `users` row whose address nobody
proved (no `logins` row ever, no `users.email_confirmed_at`) is left out with
«indirizzo non confermato» (`REASON_UNCONFIRMED`), since the wizard takes any address
(finding HCORE-13, Lorenzo's option C of 2026-11-10). A signup with no card has no `users` row
and is not held back. `CampaignService.schedule` asks for the one confirmation mail for whoever
it left out for that reason (`confirmation.request`, `users.confirmation_requested_at`; the
preview asks for nothing), and `campaigns-tick` sends it (`confirmation.send_pending`, from
`REBASE_MAIL_FROM`, at most `REBASE_CONFIRMATION_MAIL_DAILY_BUDGET` (5) attempts a Rome day,
inside the campaign reserve, which the two unasked budgets must leave 5 of free): once per
person for good, since `confirmation_sent_at` is set on any answer the provider may have
accepted (a timeout, a 5xx); a definite refusal is tried again the next day, three times in
all. Whoever logged in, opted out or bounced since stops being owed and never holds a place.
Its link, `{hub_url}/confirm?t=`, is 256 random bits kept as a sha256 with a deadline
(`REBASE_CONFIRMATION_LINK_DAYS`, 30); the page posts it (`POST /api/hub/campaigns/confirm`), a
GET confirms nothing, and a forged or expired one is a 410. A confirmed address joins the
campaigns scheduled from then on. Before each mail the tick also checks whether the action is
already done (`done_at`) and, for a list built from a state or from filters, whether
the person is still on it. That list is rebuilt once per pass, since it is a query over
every card (the state is filtered in SQL, `states._journey_state`, held to `card_state` by a
test); an opt-out or a bounce recorded mid-pass keeps a queued mail from leaving.
Optouts are campaigns-only: the magic link, the welcome mail and the contracts flow
keep reaching an opted-out address, since none of those is a campaign. The unsubscribe
route (`POST /api/hub/campaigns/unsubscribe`) has no rate limit on purpose: Gmail and
Yahoo send RFC 8058's one-click POST from a few server addresses, the token is 256
random bits and the write is idempotent.

`POST /api/hub/webhooks/resend` is that webhook: Resend signs every call with Svix, and
the route verifies the raw body against `REBASE_RESEND_WEBHOOK_SECRET` before parsing
it as JSON, then turns `email.delivered`, `email.bounced`, `email.clicked` and
`email.complained` into a row on `campaign_recipients`
(`rebase_core.campaigns.webhook`). Ivan sets it up once per environment:

1. In Resend, go to Webhooks, then «Add endpoint».
   - Production: `https://letsrebase.com/api/hub/webhooks/resend`.
   - Preview: none. The preview shares production's Resend key and must never get a
     webhook endpoint or `REBASE_RESEND_WEBHOOK_SECRET` on that team; see below.
2. Select the events `email.delivered`, `email.bounced`, `email.clicked` and
   `email.complained`.
3. Copy the `whsec_…` value into that environment's `${DEPLOY_PATH}/.env` as
   `REBASE_RESEND_WEBHOOK_SECRET`.
4. Recreate the api container with the next deploy, or with
   `docker compose -p rebase --env-file ... up -d api` from `projects/hub`.
5. Verify the webhook: send yourself a test from «Nuova campagna», then open the
   endpoint in Resend's Webhooks page. Its delivery log must show `200` for the
   `email.delivered` of that test. A `401` means the secret in `.env` is not this
   endpoint's.

A key without the secret is refused on purpose: with `REBASE_RESEND_API_KEY` set and
`REBASE_RESEND_WEBHOOK_SECRET` empty, «Mandami una prova», «Invia» and «Programma»
answer 503 «Manca il webhook di Resend: configuralo prima di inviare, vedi AGENTS.md.»
before they touch the campaign, since a campaign sent that way would never learn about
its deliveries, bounces or complaints.

Resend's webhook covers every mail the team sends, magic links included. Those arrive
untagged and are acknowledged without effect. A tagged event whose recipient row is not
in this database answers 503, so that Resend retries it (for about a day) while the tick
commits the row. Two environments that both receive webhooks on one Resend team would
503 each other's tagged events, and Resend would retry every one of them for a day, so
only production has the endpoint and the secret. **The preview keeps production's key
without the secret on purpose**: with no `REBASE_RESEND_WEBHOOK_SECRET`,
`_ready_to_send` refuses «Mandami una prova» and «Invia» there, so the preview never
sends a mail Resend would need to notify it about. The preview gets its own Resend team
before it ever gets a webhook secret.

## The team builder

Since milestone C of `docs/superpowers/specs/2026-09-25-team-builder-and-talent-cloud-design.md`
(REB-508 to REB-516) Claude reads every freelancer's CV once and writes an anonymous
card of it (`rebase_core.card_writer`), and `/hub/team` turns a project's description and
those cards into a team (`rebase_core.team_builder`, with `team_catalogue`, `team_prompt` and
`team_public` beside it, REB-892), which a company files with
«Assumi team» and an admin works in «Richieste team». The landing's hero
(`projects/website`, REB-676) asks the same `POST /api/hub/team/proposals` and sends the
visitor to `/hub/team?proposal=<id>` for the whole team, which the page reads back with
`GET /api/hub/team/proposals/{id}` (REB-675, spec § 3.2 addendum). Both calls go through one seam,
`rebase_core.llm` (`AiCall`, on Pydantic AI; REB-831): which provider, model, effort and
output ceiling answer a task, and the provider's key, are read from the database on every
call («Modelli AI», `ai_tasks`), never from the environment, and every call writes its
usage (`ai_usage`). It refuses before any network when the task's daily euro cap is spent,
its model has no price, or its provider is off or has no key. **The cap is held, not just
read (REB-889):** before the network a call places a hold, an `ai_usage` row with
`reserved_until` set and its worst case in `cost_eur` (input at 3 characters per token, the
output ceiling at the dearer of the output and thinking prices), under an advisory lock on the
task, so concurrent calls cannot all pass the same sum (`ai_spend.py`,
`AiConfigService.reserve`); the answer settles that row to the real tokens and cost
(`record_usage(hold_id=...)`), a call that fails releases it, and a hold nobody settles stops
counting after five minutes. A call is refused when what is spent and held plus its worst case
passes the cap, so the last worst case of a cap stays unspent. A hold is not a call: lists,
the month's spend and the card cost readers keep to `ai_spend.settled()`; only the cap's sum
counts it. Code that records after the fact (`record_usage` with no hold) is unchanged. The Anthropic path keeps what
the hub always sent: the `server-side-fallback-2026-07-01` beta with `fallbacks="default"`,
inference global (`inference_geo`; the API offers `global` or `us`, EU residency would take
Vertex AI or Bedrock in an EU region, not decided), adaptive thinking at the task's effort,
`cache_control` on the team builder's catalogue block, and the schema through the SDK's
`transform_schema`; `anthropic_model.py` teaches Pydantic AI the `fallback` block such a
response carries (`test_llm.py` drives it over a mocked wire). The tests hand a
`RecordingCall` and never reach a provider.

**The privacy page goes out before the hub sends a CV anywhere.** From the first card on,
CVs go to a model provider (today Anthropic), and it is letsrebase.com/privacy, in «La tua
scheda e il team builder», that tells the people they belong to. The key is no longer in
`.env` but in the database, so the gate is no longer «the key lands after the page»: the
`website-v*` tag that carries the section goes first, and **`rebase ai-seed` (the moment a
provider has a key and is on) and `REBASE_CARDS_LOOP=on` both come after it**. The other
way round, CVs reach a provider while the privacy page still says nothing of it. Nor
enable OpenAI or Google in «Modelli AI» before the notice is updated to name them as
processors (REB-841: «La tua scheda e il team builder» names Anthropic only, with the SCCs of
its DPA, and `privacy.test.ts` holds that no other provider is named), a data-processing
agreement exists with each, and no-training and retention terms are checked: the page lets an
admin do it today, and doing it sends CVs and source text to a processor the notice does not
name.

**The talent cloud** (milestone D, REB-517 to REB-521) shows the same cards by name to a
company rebase admitted. «Apri il talent cloud» on a company request's page in «Aziende»
grants its referente `/hub/me/cloud` and mails them the news, «Revoca» closes it, and
`cloud_visible` decides who is in it: live, not `discarded`, with a card. There the
builder names each person, «Assumi team» files the request with no form, and a card's
«Richiedi» asks for that talent alone. «Contatta i talenti» on a team request mails each
talent the availability question, answered with a click that posts on
`/hub/team/answer`, and «Segna come verificato» in «Talenti» puts «Verificato da
rebase» on their card. **On production the talents' mail of spec § 4.4 goes out before
the first grant**: every freelancer with a card is told, by a campaign from «Campagne»,
what their card says, that the public builder shows it without their name, and that the
companies rebase admits see their profile by name with the CV. **The `mcp` service uses
«Modelli AI» and the Resend key** as `api` does: `regenerate_freelancer_card` calls the
configured model (asked on each call, so a model configured while it runs applies at
once), and `contact_team_talents` and `grant_talent_cloud` send mail. Recreate it beside
`api` when the Resend key changes; without a model or a mail key those tools answer as
the admin area does (the card refused with the builder's sentence, sending not active
here, the cloud opened with no mail).

**The key** is saved in «Modelli AI», encrypted, never in the repository or the
environment (see «Model provider keys» below); the old `REBASE_ANTHROPIC_API_KEY` and
`REBASE_TEAM_BUILDER_MODEL` are read once, by `rebase ai-seed`, and are no longer settings
nor passed to the containers. Nothing needs recreating when a key or a model changes: the
next call reads it. With no model configured for a task (task or provider off, no key) no
card is written and `POST /api/hub/team/proposals` answers 503 «Il team builder è
spento.»; a task whose daily cap is spent answers the visitor 503 «Troppe richieste in
questo momento»; nothing else of the hub changes. `REBASE_TEAM_BUILDER_ENABLED=false` is
the switch that turns the proposals off with the model still there: the same 503, while
cards go on being written and requests already filed stay in «Richieste team».

**The backlog is `rebase cards-refresh`.** A card is written after the wizard or a CV
upload, so the CVs already on file need it too: since REB-552 the `cards` service runs
it once an hour, up to 50 cards. The loop itself is gated by `REBASE_CARDS_LOOP`
(`.env`, default `off`, read by the `cards` service's shell loop): production keeps it `off` until the talents'
mail of spec § 4.4 has gone out, so a deploy that already has a model configured never sends a
CV to a provider before that mail, then sets it `on` and runs `docker compose up -d
cards` to recreate the service. The first run after the key lands is still by hand,
before the loop is switched on:
`docker compose -p rebase --env-file /opt/hub/.env exec api uv run --no-sync rebase
cards-refresh --limit 50` (`-p rebase-preview --env-file /opt/hub-preview/.env` on the
preview) until it prints «0 schede scritte, 0 non riuscite», and a retired card comes
back on the next run. Nothing in code stops two passes from paying for the same card
twice, so a hand-run catch-up and the hourly loop must never overlap: keep
`REBASE_CARDS_LOOP=off` for the whole catch-up, only set it `on` and `docker compose -p rebase --env-file /opt/hub/.env up -d cards`
(`-p rebase-preview --env-file /opt/hub-preview/.env` on the preview) once it is done, and never run the command by hand while the loop is `on`.
Each run takes 50 CVs, the oldest first (`--limit`), and never a turned-down person's. A
CV that failed on its own account (a refusal, a scan with no text, a card that names the
person) is not tried again until it changes, so the runs end; an outage stops the batch,
counts under «non riuscite» and is the next run's, so a run that keeps printing «0
schede scritte, 1 non riuscite, fermato: modello non disponibile» is the model not
answering (or its daily cap spent, or no model configured: the card then carries that reason,
«Tetto giornaliero raggiunto», and nothing is retired), not a CV. «Rigenera scheda» on
the talent's page asks again for one. A card whose Italian gives the person's gender away
(`gendered_words` in `card_person.py`, the gate under the prompt's rule) is asked for once more, and
one still gendered after it is not written: the card read shows the error «La scheda
rivela il genere della persona anche dopo la riscrittura», the run counts it under «non
riuscite», and the CV is not asked again until it changes or an admin asks with «Rigenera
scheda»; an outage on the rewrite is an outage like any other (REB-574, REB-802).

**The card's level is banded, and an admin can overrule it** (REB-799). The prompt gives
`experience_years` from dated roles only, `null` when the CV dates none, and the bands: junior under
3 years, mid 3 to 5, senior 6 and up, lead only from 8 years with leadership the CV
states. `card_level.banded` enforces the same numbers on the answer (the seniority is the
band's, `lead` kept only with `LEAD_FROM` years, and never without years), so the two are
held together by `test_the_prompt_states_the_bands_the_parser_enforces`. A card with
`experience_years` null keeps Claude's reading of the responsibility and is `seniority_uncertain`. The
admin's seniority and years are `freelancers.seniority_admin` and `experience_years_admin` (migration
0034), set by `override_freelancer` or «Correggi il livello» on the card, audited and
revertable like every override, kept by a regenerate, and applied wherever the card is
read through `card_level.card_read` (the card read, the cloud and its seniority filter,
the team builder's catalogue and proposals) or `card_level.level` for a reader that has
the card's JSON only. Never read `Card.seniority` or `Card.experience_years` off a stored card
without them.

**A provider, a model or a prompt change is measured before it goes live, and the measure is
automatic** (REB-835, REB-842, design 2026-10-27 «Evaluation before a change goes live», as Lorenzo
changed it the same day: nobody writes an expected card or rates anything). `rebase cards-eval`
writes the card of each person of a fixed sample with the provider, model, effort and prompt it is
asked for (`CardWriter.compose`, the production writer with nothing stored), then cross-checks every
card against the sources it was written from: the CV, GitHub, the links and the hub's own facts. The
sample is about fifteen real profiles chosen to cover the failures seen (an undated CV, a duplicate
person, a GitHub-heavy profile, a designer with a portfolio, a very senior lead), stored **by id**
(`card_eval_profiles`: the person and the failure they cover, never a card; migration 0039 dropped
the expected cards the manual flow had written and kept the ids), so two runs measure the same
people. The runs and their per-person results are `card_eval_runs` and `card_eval_results`
(migration 0037, 0039). Everything a run stores (the card, the checks, the judge's verdicts and
reasons) is derived from a CV and the sources, so it lives in the hub's database only, never in the
repository, a log, Linear or a PR, and every row cascades with the person. None of the three tables
is in `WAREHOUSE_TABLES`. The admin area has no page for it and the API no route: «Valutazione»
(REB-836) is gone.

`rebase cards-eval [--sample N | --freelancer ID ...] [--provider P --model M [--effort E]]
[--prompt-version V] [--judge-provider P --judge-model M] [--baseline RUN_ID] [--allow-disabled]`:

- **Who.** With neither `--sample` nor `--freelancer`, the whole sample. `--sample N` takes N of it, one
  person of each failure in turn (labels and ids sorted, so the same N is always the same people).
  `--freelancer` names people, in the sample or not («fuori campione»). An empty sample is an error.
- **What writes.** Provider, model and effort default to the `card` task's own (`ai_tasks`); the calls
  are `AiCall(override=TaskOverride(...))`, the seam as the `card` task answered by that provider and
  model, so each is recorded in `ai_usage` under task `card` with `purpose="eval"` and **counts
  against the task's daily euro cap**, which can end the run early (below). It never writes a live
  card and never touches `ai_tasks`. `--prompt-version 1` writes with the CV-alone prompt of before
  the sources (`cards.card_prompt_v1`, the answer a bare `Card`, version 1's guards), which is how
  the baseline of the sources is written; the default is the current `PROMPT_VERSION`.
- **The rules** (`card_eval_checks.py`), where a rule exists: the card validates and no guard of
  `card_writer.py` refused it (`guardia`); `identifying` read again against everything the person's data
  holds (their name, the GitHub login, the owned repositories, the links' hostnames, the hub's
  clients), **for every prompt version**, so a v1 card is held to the rule the v2 card is; and the
  years. (The seniority is no check of its own: `compose` brings it inside the years' bands for
  every prompt.) The years are recomputed from the CV's dated periods only when the parse is
  **reliable**: every year the CV writes belongs to a period that was read («Python (2019)», an
  open «2019 -» and «dal 2016» are not, and go to the judge), and the periods counted at their
  shortest (a period written with bare years may have lasted from the difference of the years to
  that plus one) and without those beside a study («laurea», «università»…) are within a year of
  the periods counted at their longest and with them. Then the card's `experience_years` must be between the
  first, less a year, and the second plus the hub's completed years and a year; a CV with no year at
  all dates nothing, so the card has none. An admin's years (`experience_years_admin`) are a fact. Anything else
  is left to the judge. A field a rule settled keeps the rule's verdict whatever the judge said.
- **The judge** (`card_judge.py`): a call apart from the writing one, given the card and the sources
  as data (the same blocks the writer reads, whichever prompt wrote the card, so a card of the CV
  alone is judged against everything we hold), which marks each of the eight fields `supported`,
  `unsupported` or `contradicted` with a line of reason, lists what the card **invented** and every
  **identifier** of the person it finds in it. It is answered by `--judge-provider` and `--judge-model` (both or neither) or,
  left out, by the first of `DEFAULT_JUDGES` (`claude-opus-5-5`, then `claude-sonnet-5-5`) that is not the
  writer, with no reasoning effort set (REB-847): **the same model judging its own cards tends to find them right**, so a judge equal to the
  writer is refused (exit 2). The reasons are stored with the result and never printed.
  **The judge has its own output ceiling**, `JUDGE_MAX_OUTPUT_TOKENS` (8000, `TaskOverride.max_output_tokens`),
  not the `card` task's 2000: its longest legal answer is about 2,900 tokens and it thinks before
  answering. It is still a call of task `card`, `purpose="eval"`, recorded and capped as one; the writing
  calls keep the task's ceiling, so the cards are written as production writes them.
- **Safety.** A provider that is off, the writer's or the judge's, is refused («Il fornitore è
  spento») unless `--allow-disabled` says this evaluation may send CVs to it; the report then
  carries that warning. The processors gate of «OpenAI and Google» below is about production use;
  the flag is the explicit, printed exception for evaluating. Any outage, a spent cap or anything
  else that ends a run early leaves it `INCOMPLETA` (exit 1, never a baseline); every person's
  result is stored as it comes, so what was paid for is kept. Run it from the `api` container like
  `cards-refresh` (`docker compose -p rebase --env-file /opt/hub/.env exec api uv run --no-sync
  rebase cards-eval …`, `-p rebase-preview --env-file /opt/hub-preview/.env` on the preview).

Reading the report: one line per person (label and the first characters of the id, never a name)
with the fields the sources support out of the eight, the contradicted ones, what was invented,
whether the years recomputed from the CV agree (`-` when the judge decided), `guardia` and
`anonima` as `ok`/`no`, latency, and the euro of the writing and of the judging **apart**; then the
totals (supported, unsupported and contradicted fields, invented claims, years, guard refusals,
identifiers found by the rules and by the judge, latency, cost), who judged, and the comparison with
the baseline on the people both runs wrote, a measure at a time «meglio/uguale/peggio». **A change
goes live when it is no worse than the baseline on supported fields, contradictions, years,
guard refusals and identifiers.** The baseline is `--baseline RUN_ID`, or by default the newest
finished run of the automatic evaluation, of the whole sample, whose provider, model and effort are
the `card` task's now and whose prompt version is the current `PROMPT_VERSION`: production as it
runs, never the last candidate
tried. With none the report says «Nessun riferimento»: run the live configuration first and that run
is the baseline. To measure the sources, write the baseline first with `--prompt-version 1`, then run
the current prompt with `--baseline <that run>`. Record the ids in the PR that changes the prompt
(`PROMPT_VERSION` in `card_prompt.py`).

**The card is written from more than the CV, and every switch is off in production** (REB-837 to
REB-839, design 2026-10-27, «Sources» and «Provenance, versions and regeneration»). The prompt is
`PROMPT_VERSION` 3 in `card_prompt.py` (REB-839 wrote version 2, REB-855 version 3): version 1's words, then the version line and the description of
a private answer (`card_draft.CardDraft`): the `card` as before, `sources` (which sources the model
says supported each field) and `employers` (every employer the CV names). `Card` did not change,
and only `card` is ever the card: the cloud, the talent list and every public read serve `Card`'s
fields and the level, never `sources`, `source_hashes`, `conflicts`, `prompt_version` or `provider`;
only `GET /api/hub/freelancers/{id}/card` (`FreelancerCardAdminRead.provenance`) carries them,
for «Scheda anonima», which shows `sources` as «secondo il modello».

**The CV's identity and contacts are read by the same call, and only an admin sees them** (REB-855,
`PROMPT_VERSION` 3). `CardDraft.person` (`card_draft.CardPerson`: `nome`, `last_name`, `email`,
`phone`, `city`, `linkedin_url`, `other_links`, each null or empty unless a document states it
literally) is answered beside the card, so a person costs one call, never two. `clean_person`
normalises it (email lower case and valid by `EmailStr`, LinkedIn through `normalise_linkedin`, the
phone as written and trimmed up to `PHONE_MAX_LENGTH` with at least six digits, only `http(s)`
links, at most five) and **drops what does not pass: a bad contact never fails a card**. With the
`employers` the model already answers (as `companies`) it is stored as
`freelancer_cards.person` (JSONB, migration 0043), tagged with the hash of the CV it was read
from, and **never written into `users`**. It is stored whenever the draft parsed, also when the
anonymity guard refused the card, so the contacts show regardless; a failure that read none keeps
the persona of the same CV and retires one of another CV (`_save`), and the row's delete (a CV
removed) takes it. Who reads it: `GET /api/hub/freelancers/{id}/card` (`FreelancerCardAdminRead`
`person` and `differences`, the fields where the CV and `users` both say something and disagree:
names and email without case or spacing, the phone by its digits, LinkedIn by its stored shape),
which feeds «Scheda con i dati» and the «Telefono» and «Email nel CV» rows of the talent page;
and MCP `get_freelancer` (`phone`, `cv_contacts`, `cv_differences`). `FreelancerRead.phone` is
the person's primary phone point (`users.phone` until migration 0050). **Nothing else reads the CV's contacts**: not `get_freelancer_card`, `list_talents`,
`get_talent`, `cloud.py`, `team_builder.py`, the evaluation (`card_eval` compares the anonymous
card only) or the member area (`test_card_person.py`, the cloud, admin API and MCP tests assert
it). **Version 3 changes no card by itself**: every card of version 2 reads as stale (`prompt`)
and waits in «Schede da aggiornare» for an admin's «Avvia rigenerazione» or a «Rigenera
scheda»; the hourly `refresh_stale` only writes a card whose CV is new.

- **Sources** are GitHub (`github_source.py`, the account their links name, public data, the
  optional `REBASE_GITHUB_TOKEN` only lifts the rate limit), their other links (`web_fetch.py`:
  HTTPS, no private address on any hop, `robots.txt`, size and time limits, no browser, a
  JavaScript-only page is `unreadable`) and the hub's own facts (`hub_facts.py`: the admin's
  override and the engagements by role, months and outcome, **never a client's name**, budget or
  note). **LinkedIn is never read**: dropped before any fetch. They are stored per person in
  `freelancer_sources` with the time read and a hash that leaves out what moves by itself (stars,
  last push, page dates); a removed link's text goes at the next refetch, a person's soft delete,
  merge or erasure removes all of it at once (`sources.py`).
- **Every source is off by default, on `GET /api/hub/admin/ai` and in «Modelli AI», «Fonti»:**
  `github` and `links` (`ai_sources`, `PUT /api/hub/admin/ai/sources/{kind}/enabled`, an
  `ai_switch` entry; turning one off deletes its stored text at once). **They may be
  switched on in production once the `website-v*` tag that carries the sources paragraph of
  «La tua scheda e il team builder» is live (REB-841: GitHub and the pages of the links are read,
  sent to Anthropic, stored and refetched weekly)**: the text of a GitHub profile and of a page is personal data the person did not hand us
  directly. Step 6 of the plan, once it does: switch `github` on in the preview, run
  `rebase sources-refetch`, then `rebase cards-eval` with the same provider and model as the
  Phase 2 baseline, `--baseline` the id of that last version 1 run (the default baseline is a run
  of the current prompt version, and there is none until one is made), and read the report as
  above; only then production.
- **What the model is told, and what the code checks.** Each source is a `<source key="...">`
  block with its header, wrapped as data (it cannot close its own block), and the system text says
  no identifier of a source may reach the card, repositories are described by what they do, and
  years come only from dated professional periods: the CV's dated roles and the hub's completed
  engagements. GitHub, a page and the wizard **never set `experience_years`**: when a source beside the CV was
  read and the model's `sources["experience_years"]` names neither `cv` nor `hub`, the code nulls the years and
  stores a conflict `discarded_experience_years` (the seniority is then the uncertain reading of REB-799). The
  card follows the CV's dates when it and the hub disagree, and the code (not the model) records
  `different_experience_years` when the hub's completed months exceed the card's years by more than one.
  `verify_sources` drops every key that is not a source of this person.
- **The guard** (`card_guard.identifying`, called by `compose`) reads the card against the name and
  surname as before (a hit fails the card at once), and against the GitHub login, the owned
  non-fork repository names, every link's hostname, the employers the model reported and, since
  REB-855, what the CV itself says of the person (`person`: the name as the CV spells it, the
  email address and its local part, the phone number by its last nine digits, the hostnames and
  GitHub account of its links, `KnownIdentifiers.cv_*`, `emails`, `phones`). A hit on
  those asks for **one rewrite, then fails the card** with the sentence «La scheda cita un account,
  un repository, un sito, un datore di lavoro, un nome o un recapito della persona...»
  (`source_identifying`), keeping the card of the same CV if there is one. The rewrite message
  names the card's fields, never the words. The employers are stored only as `companies` of the
  persona (below), never in another column, a log, an `ai_usage` record or a conflict
  (`test_employers_are_stored_only_with_the_persona`).
- **A CV is parsed in a child process** (`cv_text.extract_text` runs `cv_text_worker`, REB-889): the
  file is a stranger's upload and `pypdf` has no bound of its own, so the parent kills the child after
  20 seconds, the child has a 2 GiB address-space limit, and the 20 page and 200,000 character
  ceilings are applied while it reads. A parse that overruns, dies or answers badly is the same answer
  as a scan, empty text (the card fails `no_text`), never an exception.
- **A card is stale when its inputs are not the ones it was written from**, and that never spends
  by itself. `freelancer_cards` keeps `prompt_version` (`NULL` is version 1), `provider`, `sources`,
  `source_hashes` (`wizard`: the position; `hub`: the hub's facts without the months of a running
  engagement; `github`, `link:<n>`), `conflicts` and `thinking_tokens` (migration 0038).
  `CardWriter.stale_of` says `cv` (the CV is compared by `freelancers.cv_sha256`, the digest the
  database generates, never hashed in Python; the hourly loop writes a card for a new CV, and for nothing
  else), `prompt` (another version) or `source` (the hub's facts, the position or a source
  changed; the rate and the work mode are not read by the prompt). The last two are listed in
  «Modelli AI», «Schede da aggiornare» (`GET /api/hub/admin/ai/cards/stale`), with the estimated
  cost, the average of the recorded `card` calls times the count, «stima non disponibile» while
  none is recorded; the list reads 200 cards at a time and their inputs by `IN (...)`
  (`input_hashes_many`, `SourceService.current_many`, `hub_facts_many`), and a start reads only
  as far as its `limit` (`stale_cards(limit)`). `CardWriter` is assembled from `card_base`,
  `card_regen` (the trail and the locks), `card_staleness`, `card_store` and `card_writer`; the
  prompt is `card_prompt`, the persona and the gender guard `card_persona`, the hashes `card_provenance`.
  **«Avvia rigenerazione»** (`POST .../cards/regenerate-stale`, `{limit}` 1 to
  50, a signed `AdminAction` `cards_regenerate` and, at its end, `cards_regenerated`: see «Every
  regeneration a person starts is traced in full» below) writes the oldest `limit` after the response, each
  re-checked **under the person's claim** just before its write, so a double click pays once, and
  stops at the first outage or spent cap (REB-853: with `REBASE_CARDS_WORKERS` at once, or as a
  Message Batch from `REBASE_CARDS_BATCH_THRESHOLD` cards up, below). A bump of `PROMPT_VERSION`
  therefore lists every card of the old version and spends nothing: **deploying version 2 puts all
  of them in the list**, and so did version 3. The persona (REB-855) travels the same way through
  `CardWriter.finish` and `CardWriter.store`, so a card applied from a Message Batch result stores
  it, and guards the anonymous card with it, exactly as a synchronous write does.
- **Bulk writing is parallel, and a bulk regeneration is a Message Batch** (REB-853). `rebase
  cards-refresh` and an admin's regeneration write with `REBASE_CARDS_WORKERS` workers (default 5,
  1 to 8; `.env`, `docker-compose.yml`): each is a thread with a session and a `CardWriter` of its
  own, and each person is **claimed** by the Postgres advisory lock `card:<freelancer id>` that
  `CardWriter._write` always took, so no card is paid for twice even when two passes overlap. The
  first `unavailable` (an outage, a spent daily cap, no model) stops the pool: the people not
  started are left as they were (still stale, no failure recorded), the ones already in a call
  finish. Counts, `ai_usage` rows and the `AdminAction` are unchanged in shape. Each worker takes
  two connections for the length of a write (its session and the lock), so
  `create_engine_from_settings` raises the pool's overflow with it, and the setting stops at 8. **From `REBASE_CARDS_BATCH_THRESHOLD` cards (default 10) an admin's start, or
  `rebase cards-regenerate --admin EMAIL [--limit N] [--batch]` (`--batch` whatever the threshold), goes through
  Anthropic's Message Batches API instead**: half the price, answered within the hour (24 at
  most), and only for the Anthropic provider (any other falls back to the workers, and the CLI
  says so). A single «Rigenera scheda», `cards-refresh` and the wizard's write stay synchronous.
  `card_batch.CardBatches.submit` builds each person's request with `CardWriter.prepare`, the same
  prompt and answer shape the synchronous write sends, and `llm_batch.AiBatch` encodes it with Pydantic
  AI's own batch encoder (`build_batch_model`): same model, effort, adaptive thinking, schema,
  output ceiling and `inference_geo` as the `card` task, less what a batch refuses (the
  server-side fallback and the per-request timeout and retries; extended output is a separate
  beta, not needed here). `custom_id` is the freelancer id. The batch and one `card_batch_items`
  row per card (the hash of the CV and of every input, no text; migration 0041) are written
  **before** the provider is called, and the batch's state is `submitting`, `in_progress`,
  `ended`, `applied` or `failed`. **One batch is active at a time**: a start while one is not
  applied is refused with a 409 («Una rigenerazione delle schede è già in corso»); the advisory lock
  `regenerate_stale` holds for the whole run is the one guard, and a start that loses the race to
  it still queues and spends nothing.
  `rebase cards-batch-poll` carries it on (the `cards` service runs it every five minutes,
  whatever `REBASE_CARDS_LOOP` says; the hourly `sources-refetch` and `cards-refresh` are every
  twelfth tick): when the provider says the batch ended, each result is applied through the
  synchronous write's own steps, `CardWriter.finish` (draft, guards, the one synchronous rewrite
  a gendered or identifying card is asked for) and `CardWriter.store` (the one save), under the
  person's claim. A result is applied only to the card it was asked for: a CV, an input or the
  `PROMPT_VERSION` that changed meanwhile makes the item `superseded` (the answer is paid for,
  recorded and dropped, the card is still stale); a card that raises is settled `errored` and
  never holds the rest; one that needs a rewrite while the model is down waits for the next pass
  while the others are applied; a refusal is written again as a synchronous call, which has the fallback a
  batch has not; a request the provider did not answer (errored, expired, canceled) leaves its card
  stale for the next start. Usage is recorded when the result is read, at the **batch price**
  (`ai_usage.batch`, cost = 50% of the price row's input, output, thinking and cache reads, as
  Anthropic bills a batch), once per card (`usage_recorded`, committed in the same transaction as
  the row), so the cap counts it the day it lands, and a batch whose estimated cost (half the
  synchronous estimate) would pass what is left of the daily cap is refused at submission, and
  one that fits holds that estimate (a batch hold, 25 hours, its id in the stored handle) so a
  call and a batch see each other; the first answer recorded deletes the hold in its own
  transaction, a batch that ends with nothing to record releases it, and a crash leaves it to
  its TTL; the
  cost estimate of «Schede da aggiornare» (synchronous calls only) is not halved by it.
  **To resume after a restart, nothing is needed**: the state is in the tables and the results at
  the provider for 29 days, so the next poll goes on, or run `docker compose -p rebase --env-file
  /opt/hub/.env exec api uv run --no-sync rebase cards-batch-poll` by hand; a model that does not
  answer a rewrite leaves the rest for the next pass. A batch left `submitting` for 15 minutes
  without a provider id is marked `failed` (it was never sent, or its answer was lost with the
  process: never sent a second time, which could pay twice; an orphan at the provider shows in the
  Console), and an admin starts again. «Schede da aggiornare» shows the running batch (cards sent,
  when, how many applied) and refreshes itself until it is applied.
- **Every regeneration a person starts is traced in full** (REB-861, `cards.Regeneration`,
  `CardWriter.regenerate_stale`, `record_start`, `card_done`, `end_regeneration`). Three entries of
  the admin trail, none with personal data (counts, costs, reasons and people's ids; never a name
  or a card's text):
  - **`cards_regenerate`** on `ai_config`, the start, always signed: `{limit, queued, source, batch,
    batch_id?}` with `source` `web` («Avvia rigenerazione») or `cli` (`rebase cards-regenerate
    --admin EMAIL`, which is **required**: a missing, unknown or non-admin address refuses with an
    Italian message and exit 2, and nothing starts). A batch's start is recorded once the batch has
    an id (before the provider is called), and `batch_id` is how the poll finds it again, with no
    column of its own: a batch sent before REB-861 has no such start and records nothing.
  - **`card_regenerated`** on the freelancer, one per card the regeneration rewrote or failed to:
    `{source (web|mcp|cli), bulk, start_action_id (bulk only), prompt_version, model, outcome
    (written|not_written), reason (not_written only), cost_eur, unpriced?}`, signed by the admin. The
    reasons are `cards.Failure` (`refusal`, `shape`, `identifying`, `source_identifying`,
    `gendered`, `max_tokens`, `no_text`, `unavailable`), `cap_reached`, `not_configured`,
    `superseded` (the batch's answer was dropped because the CV or an input changed) and, for a
    batch, `errored` (no answer, or one that could not be applied), `expired`, `not_sent`. The cost
    is the person's `ai_usage` rows since the write began (since the batch was sent, for one):
    `LlmRequest.freelancer_id` puts the person on the usage row of a call, which is how it is read
    back. A card that was no longer stale, or taken by another writer, is not this regeneration's
    and records nothing. The single «Rigenera scheda» (the web route, `source` `web`; MCP
    `regenerate_freelancer_card`, `source` `mcp`, as the token's admin) records it too, with `bulk`
    false. **The hourly `cards-refresh` for a new CV and the wizard's own write are started by no
    person and stay unrecorded.** It shows in the talent's «Registro delle modifiche»
    (`AuditTrail.tsx`: «Scheda rigenerata» or «Scheda non rigenerata: motivo», «in blocco» or
    «singola», the door, the cost).
  - **`cards_regenerated`** on `ai_config`, the end, written once, signed by the admin who started
    it: when the worker pool returns, when a batch is applied, or when one is given up or was never
    sent. `{source, start_action_id, batch_id?, batch_status? (applied|failed), written, failed,
    superseded, skipped (no longer stale, or the person or CV gone), errored, cost_eur, unpriced?,
    failures: [{freelancer_id, reason}], stopped?, left?}`, counted from the cards' own entries (the
    latest per person), so the end can never disagree with them. A card whose rewrite finds the model
    down waits for the next pass and is recorded when it is settled.

  **Where to read it:** `GET /api/hub/admin/ai/audit?limit=50` (admin only, newest first, the
  `AdminActionRead` shape with the admin's name; `AiConfigService.trail`) feeds the «Registro»
  section of «Modelli AI», and MCP `list_ai_config` answers the same last 50 as `trail`. A
  regeneration with a start and no end is on, and the page keeps reading until it ends.
- **The weekly refetch never calls a model.** `rebase sources-refetch [--limit N]` runs
  `SourceService.refetch_due` (people whose sources are a week old, missing or no longer match
  their links; only people a card is written for: live, not turned down, with a CV; the text of
  anyone else is deleted; one person's failure is logged and the pass goes on) and **the `cards`
  service runs it every hour before `cards-refresh`, whatever `REBASE_CARDS_LOOP` says**, since it
  sends nothing to a provider; with every switch off it reads nothing. A removed link's text goes
  at the next pass. Fetching a page or GitHub never writes a card. A network failure or a
  refusal keeps the text read before; only a page that is gone (404, 410, 451) replaces it.
  **A failed read is retried on a backoff, not in a week** (REB-844): each source row counts its
  failures in a row (`freelancer_sources.attempts`, migration 0040; `fetched_at` is the last
  attempt), the next try is due an hour after it, then 2, 4, 8, 16 and 24 hours (`retry_delay`),
  and the first read that works sets the count to 0. **GitHub's rate limit belongs to the pass,
  not to the person**: without `REBASE_GITHUB_TOKEN` it is 60 requests an hour and a person costs
  up to seven. `GithubSource` reads `x-ratelimit-remaining` and `x-ratelimit-reset` off every
  answer (the seam is `HttpHeadersCall`, `urllib_headers_call`) and `retry-after` on a 403 or
  429; when the budget is spent it answers `limited` for the rest of the pass without calling
  GitHub, and `SourceService` writes **nothing** for a source that was `limited`: no `error` row,
  no count, the old text stays. A person due only for GitHub is not visited while it is spent
  (the log says «github rate limit spent: N people wait for the next pass»), so the next pass,
  after the window turns, takes them in the order they were due: with no token about eight
  people an hour. A bare 403 with budget left is a refusal (`error`, backed off). A card written (the wizard, a CV upload, «Rigenera scheda», an admin's start)
  refetches that person's sources first, with every switch off it reads nothing, and an
  evaluation (`compose`) reads only what is stored.
- **Evaluating version 2** is `rebase cards-eval` as above: `CardWriter.compose` builds the prompt
  from the sources stored for each person and the hub's facts, so a person is measured with what
  the production writer would use. `source_identifying` counts as a guard failure in the report.

**Every «Proponi il team» is a row, answered or not** (migration 0028, Ivan 2026-09-29:
«salvare tutte le richieste dopo il click proponi team anche se non ancora
finalizzate/inviate così raccogliamo metriche di uso»). `team_proposals` keeps the
description, the headcount picked (`people`) and the note of every ask, and an ask
Claude did not answer or the caps refused is a row with `error` set
(`llm_unavailable`, `team_builder_busy`), an empty summary and nobody in it: never
handed out, never regenerated or filed, never counted by the daily cap. «Proposte»
(`/hub/admin/team/proposals`, linked from «Richieste team») and `list_team_proposals`
over MCP read them all, newest first, each with the «Assumi team» filed on it when
there is one and, since REB-607, each talent it held by name, linked to their admin
page (`team` on the row, one name query per page; a talent removed with «Elimina»
since stays by id, unnamed, since their page no longer opens). That is where the usage of the builder is read; PostHog's
`team_proposal_generated` counts only the proposals that answered. One bound: a
refusal's row holds no proposal slot, so `rebase_core.team_slots` (which both the public
and the cloud router call; routers never import each other) writes at most
`REFUSAL_WRITES` (2) of them at once, and at most `REFUSAL_WAITERS` (8) more wait
`REFUSAL_WAIT_SECONDS` (0.5) for a turn; past that, which only a flood reaches, the 503 is answered, the row is
not written and the log says «refused ask not kept».

**Three caps**, all in `rebase_core.team_slots` and `team_caps`, in the order a proposal meets
them (the switch comes first: off says «Il team builder è spento.», not «Troppe richieste»).
`REBASE_TEAM_BUILDER_CLIENT_DAILY_CAP` (30) is one public client's share of the day: every
public ask, refused ones included, counts against its own client (`rebase_core.ratelimit`,
a rolling 24 hours, IPv6 on its /64, per process like every limiter here), so one address at
the speed bump's five a minute cannot spend the daily cap for everybody (REB-889); the cloud
is grant-gated and has no such share. `REBASE_TEAM_BUILDER_CONCURRENCY` (4) is how many proposals run at once
in the API process: the next one answers 503 «Troppe richieste in questo momento:
riprova tra un minuto.» at once rather than wait on the threads the member area and the
webhooks share. `REBASE_TEAM_BUILDER_DAILY_CAP` (300) is how many proposals a day, the
public page and the cloud together, counted on `team_proposals` since midnight in Rome
(`rebase_core.team_caps`); once reached, the same 503 until the next day. It counts
proposals written: an admin's own (only older rows, from the MCP tool REB-876 removed)
and one with an empty catalogue (no call made) do not count, and a call that failed is
bounded by `spend_one` and the concurrency cap, not by this one.

**`/api/hub/team/proposals` needs 90 seconds on the host vhost, and so does
`/api/hub/me/cloud/proposals`**, the same builder inside the talent cloud (REB-519). A
proposal holds the request while Claude writes, and the seam gives up after two
attempts of 40 seconds (`llm.py`, `_TIMEOUT_SECONDS` and `_MAX_RETRIES`), near 81
seconds; nginx's default of 60 would answer the visitor 504 while the proposal goes
on. The repository's copies of the host vhosts, `projects/website/deploy/letsrebase.conf`
and `preview.letsrebase.conf`, carry both locations beside `location ^~ /api/hub/`,
which an exact match outranks; the installed vhost in `/etc/nginx/sites-available/`
gets them by hand, as the MCP location did. That MCP location,
`location ^~ /api/hub/mcp`, waits on the same seam for `regenerate_freelancer_card`
(REB-520); the `proxy_read_timeout 120s;` after its include,
there since the hours report's Pigro link (REB-499), covers it too: in both copies here,
and by hand in the installed vhosts (8088 on production, 8089 on the preview, which has
it since #428), with the same `nginx -t` and reload. Production's:

```
location = /api/hub/team/proposals {
    client_max_body_size 6M;
    proxy_pass http://127.0.0.1:8084;
    include /etc/nginx/snippets/rebase-proxy.conf;
    proxy_read_timeout 90s;
}
location = /api/hub/me/cloud/proposals {
    client_max_body_size 6M;
    proxy_pass http://127.0.0.1:8084;
    include /etc/nginx/snippets/rebase-proxy.conf;
    proxy_read_timeout 90s;
}
```

and the preview's the same on 127.0.0.1:8086; `nginx -t`, then `systemctl reload
nginx`.

**`POST /api/hub/freelancers/{id}/anonymous-cv` needs 330 seconds on the host vhost**
(REB-862), and the installed vhosts get it by hand before «Scarica CV anonimo» is used. A
first press waits while the model writes a two-page CV, one answer of 3-5k tokens that is
not streamed: the `anonymous_cv` task has its own limits in `llm.py` (`_TASK_HTTP_LIMITS`,
150 seconds a call and no retry, where every other task keeps 40 and one), and a CV that
names the person is asked once more. Two calls of 150 seconds plus the print is 330;
nginx's 60 would answer 504 while the model went on writing a body the admin then pays
for again. A regex location is never read once `^~ /api/hub/` matches, so both copies here
carry a longer prefix, `location ^~ /api/hub/freelancers`, that mirrors `/api/hub/`'s
directives and holds the route in a nested regex; no trailing slash, as
`/api/hub/matches`, or nginx answers the wizard's `POST /api/hub/freelancers` and the
admin list with a 301. Production's:

```
location ^~ /api/hub/freelancers {
    client_max_body_size 6M;
    proxy_pass http://127.0.0.1:8084;
    include /etc/nginx/snippets/rebase-proxy.conf;
    location ~ ^/api/hub/freelancers/[^/]+/anonymous-cv$ {
        proxy_pass http://127.0.0.1:8084;
        include /etc/nginx/snippets/rebase-proxy.conf;
        proxy_read_timeout 330s;
    }
}
```

and the preview's the same on 127.0.0.1:8086, pasted into the installed copies' existing
server blocks beside `location ^~ /api/hub/`; `nginx -t`, then `systemctl reload nginx`.

## The public doors, and the real cap in front of them (REB-889)

Four routes take a stranger's input with no login: `POST /api/hub/freelancers` (up to 5 MB
of CV, and a model call for the card), `POST /api/hub/auth/link` and `/auth/enter`, the
company and signup forms, and the team builder. The API's own limits are the floor, not
the cap, and all of them are per process:

- **Per client**: `rebase_core.ratelimit.RateLimiter`, a sliding window (`SIGNUPS_PER_MINUTE`,
  5 a minute, shared by every public write through `rebase_api.ratelimit.spend_one`). The
  client is `X-Real-IP` as nginx saw it, an IPv6 address as its /64 and an IPv4-mapped one
  as the IPv4 it wraps; the table is bounded and forgets the least recently seen client, so
  it can only become too lenient, never lock anybody out.
- **Applications with a CV, the whole world** (`REBASE_APPLICATIONS_WITH_CV_PER_HOUR`, 30):
  the CVs people themselves stored in the last hour (`Freelancer.cv_uploaded_at`,
  `compilata_da = 'persona'`, so a resubmission that replaces a card's CV counts like a new
  card); past it `POST /api/hub/freelancers` answers 429 «Stiamo ricevendo molte
  candidature…» with `Retry-After: 3600`, for a post that carries a CV only. The card's
  model write after the answer needs no extra gate: the seam refuses before any network call
  once the task's daily cap is spent (`AiConfigService.can_call`), and `cards-refresh` writes
  whatever the seam turned away.
- **Sign-in is never closed by a budget.** A login link is asked for by its owner, so it is
  capped **per person**, not per world: `REBASE_MAGIC_LINK_DAILY_PER_USER` (5) links per
  Rome day, counted on `magic_link_tokens.asked_via` (`login`, `wizard`; `NULL` for an
  admin's promotion or the command line, which are not counted). Past it `request_link`
  sends no mail and answers like any other request (it must not say whether the address
  exists); the log says «a person is past the daily cap» and never the address. An admin is
  never refused. Only the mail nobody asked for, the wizard's «you already have a card»
  (`asked_via = 'wizard'`), has a global circuit breaker: `REBASE_PUBLIC_MAIL_DAILY_BUDGET`
  (10, never above `REBASE_CAMPAIGN_DAILY_RESERVE`) a day; spent, that mail is not sent and
  the log says «public mail budget spent». The budget plus the campaigns' ceiling stay inside
  Resend's 100 a day. `ResendSender.send` logs the provider's status (401/403 key or domain,
  429 quota, 5xx outage) and never the address or the key.
- **The server half of the ad conversion** is sent only when the signup body says
  `pixel_consent: true`, the same «granted» decision (`localStorage["rebase.consent"]`)
  that loads the browser pixel; absent means no.

**The real cap is nginx's `limit_req`, committed here because nothing else holds it.** Once
in the `http` context of the host (the repository's vhost copies include nothing for it,
so it is a file of its own, `/etc/nginx/conf.d/rebase-limits.conf`), then a `limit_req`
line in each location below, in both the production and the preview vhost:

```
# One key per client: an IPv4 address, an IPv6 one as its /64 (the groups written out,
# or the ones before a `::`), an IPv4-mapped one as the IPv4.
map $remote_addr $rebase_client {
    default                                          $remote_addr;
    "~^::ffff:(?<v4>[0-9.]+)$"                       $v4;
    "~^(?<net>[0-9a-f]{1,4}(:[0-9a-f]{1,4}){3}):"    $net;
    "~^(?<net>[0-9a-f]{1,4}(:[0-9a-f]{1,4}){0,2})::" $net;
}
limit_req_zone $rebase_client zone=rebase_write:10m  rate=6r/m;    # the public writes
limit_req_zone $rebase_client zone=rebase_auth:10m   rate=10r/m;   # the magic link
limit_req_zone $rebase_client zone=rebase_door:10m   rate=60r/m;   # bearer-secret doors
limit_req_zone $rebase_client zone=rebase_mcp:10m    rate=120r/m;  # the MCP server
limit_req_status 429;
```

| Location | Zone | Line to add |
|---|---|---|
| `= /api/hub/freelancers` (the wizard's POST; not the admin routes under `^~ /api/hub/freelancers/`) | `rebase_write` | `limit_req zone=rebase_write burst=3 nodelay;` |
| `= /api/community/signups` | `rebase_write` | `limit_req zone=rebase_write burst=3 nodelay;` |
| `^~ /api/hub/auth/` | `rebase_auth` | `limit_req zone=rebase_auth burst=5 nodelay;` |
| `= /api/hub/members/lookup` (PigroCRM's server, one address) | `rebase_door` | `limit_req zone=rebase_door burst=30 nodelay;` |
| `= /api/hub/documenso/webhook` (Documenso retries three times in 160 ms) | `rebase_door` | `limit_req zone=rebase_door burst=30 nodelay;` |
| `= /api/hub/webhooks/resend` | `rebase_door` | `limit_req zone=rebase_door burst=30 nodelay;` |
| `^~ /api/hub/mcp` | `rebase_mcp` | `limit_req zone=rebase_mcp burst=60 nodelay;` |

The bearer-secret doors (`/members/lookup`, the Documenso webhook, the MCP bearer) compare
in constant time against a long secret, so brute force is impractical; the zone is for
the noise, and the MCP transport also refuses a client after
`REBASE_MCP_BAD_BEARERS_PER_MINUTE` wrong bearers. Their `detail` strings are English
codes (`invalid_token`, `invalid_secret`, `invalid_signature`, `invalid_body`, `retry`,
`webhook_disabled`, `not_found`) because their only reader is the caller's log; everything
a person reads is Italian. After a change, `nginx -t`, then `systemctl reload nginx`.

**The session cookie is `rebase_user`**, read from that name only (`deps.session_token`); the name it had before REB-668 and the browser's `orbiters.*` storage keys were dropped with REB-697, once the 30-day session window had passed.

**Sessions slide at most once an hour** (`users.SESSION_SLIDE_AFTER`): a request that finds
its deadline less than an hour behind the one it would set writes nothing, so a `/me` poll
is a read.

**Who is an admin is audited in the transaction that changes it.** `promote` and `demote`
write `admin_promoted` and `admin_demoted` (entity type `user`) in the same commit as the
role; `demote` refuses the last remaining admin with a 409 and an address merged into
somebody with a 422. An admin may demote themselves while another admin exists; the rule that
protects the area is the last-admin one, and `rebase setrole` (the operator's recovery
tool, no actor) is not guarded. The trail has two modes, `AdminActionService.record`:
best effort by default (its own commit after the change, a failure logged), and
`commit=False` for the sensitive kinds, which the caller commits with the change and which
fail the change when they cannot be written: `overridden` (card and company identity and
profile), `status_changed`, `fiscal_updated`, `admin_promoted`, `admin_demoted`. A kind or
entity type over its 20-character column raises `ValueError` instead of being cut, and
`test_admin_actions.py` checks every kind in `ADMIN_ACTION_KINDS` fits.

## Running it

From the repository root:

```
uv sync --frozen
uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests
uv run --env-file projects/hub/.env uvicorn rebase_api.main:app --port 8010
```

The tests bring a template database to `head` with this package's migrations, once per
xdist worker on the worker's own PostgreSQL (`projects/hub/conftest.py` over the
repository's root `conftest.py`, REB-579), and each test root clones it; never with
`create_all`: a table the model declares and the migration forgets fails here rather
than on the server. A migration test asks the same fixture for an empty database.

**A migration that renames or drops a table is also a change outside this repository.**
Six of these tables are read by PostHog's warehouse as `posthog_ro`, and a `GRANT`
follows a rename while a sync does not: `member_logins` became `logins` in migration
0012 and PostHog paused that sync nine days later, in an email.
`packages/core/tests/test_warehouse_contract.py` now fails on the pull request instead,
and names what to do in PostHog; the runbook is `docs/adding-a-project.md` § 7 and the
order of operations is the `posthog-analytics` skill.

**Its `vite preview` serves under `/hub/`, not `/`.** The web app is built with
`base: '/hub/'`, so the preview's root path 404s and the wizard pages are at `/hub/`,
`/hub/freelance` and `/hub/companies`. A blank page at `/` is that, not a broken build.
Unlike the website's, this preview has no `strictPort`, so a second checkout does not
collide with the first: it takes the next free port and logs it, confirmed live as `4174`
while another agent held 4173 on 2026-09-10. Read the port off its own output rather than
assuming 4173.

## Testing on the preview

Written from the 2026-09-29 end-to-end run. The container list and the `setrole` flags
were re-checked read-only on 2026-10-05; the login, the match and the signing were not
repeated, because they write data or send mail.

- **The host is the origin.** `preview.letsrebase.com` shares its host with the hub's
  production (`docs/migrations/2026-09-14-letsrebase.md`): the Hetzner origin, one of
  rebase's two servers (`docs/architecture.md` § Where it runs). The `pigrocrm` ssh alias
  used below is a local ssh alias for it and is not defined in this repository. Touch only the
  `rebase-preview-*` containers (`api-1`, `web-1`, `db-1`, `mcp-1`, `sweep-1`,
  `campaigns-1`, `cards-1`), nothing else on that host. The compose flags for the preview
  are in § "The team builder".
- **Become admin.** `ssh pigrocrm docker exec rebase-preview-api-1 uv run --no-sync
  rebase setrole --email <address> --role admin --first-name <first name> --last-name <last name>`.
  `--name` and `--last-name` are required when the address has no row yet (without them it
  prompts on a tty and fails in `docker exec`, and `UserService.set_role` rejects empty
  names); `--role member` demotes.
- **Log in.** There is no password: a login is a magic link mailed through Resend, so a
  `+alias` of an inbox you own (`you+hubtest@example.com`) works.
- **Create a match** from the admin area, or with the `create_match` MCP tool, then send
  it for signature.
- **Sign on Documenso** with a headless browser at the `/sign/<token>` link from the
  mail. The fields are a canvas, not form inputs: click the pad, type the name, «Next»,
  then «Next Field», click the field by its coordinates, and repeat «Next Field» and the
  click until none is left (the framework agreement has two signature fields for the
  freelancer), «Complete», «Sign». The journey is in
  `docs/superpowers/specs/2026-09-23-documenso-probe.md` § 6, written for a letter with
  one field. Repeat for each document and each signer. A freelancer with no active
  framework agreement gets the framework agreement first and the letter only once it is
  signed, with a new mail; to test both, use a test freelancer without one. The preview
  signs on production's Documenso
  (`REBASE_DOCUMENSO_URL=https://firma.letsrebase.com`, the preview's own Documenso user,
  § "Documenso, the signing site"), so the envelopes are created there;
  `ssh pigrocrm docker exec rebase-preview-api-1 uv run --no-sync rebase documenso-check`
  says whether it reaches it.
- **A match sent for signature mails real addresses.** Give the freelancer and the
  company only addresses you own.
- **The preview keeps what earlier runs left**: test members, matches and contract rows
  stay in its database, while the envelopes stay on production's Documenso. Expect rows
  and envelopes you did not make in both places and do not clean them up unasked.

## The database was inherited

`signups` was created by PigroCRM's sidecar in production and holds real rows.
Migration 0001 adopts it as it stands (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`, the unique index `IF NOT EXISTS`) so the first `alembic upgrade head` on the
copied database changes nothing and records 0001. Keep every migration that may run on
that database conditional in the same way until the copy is confirmed.

## Deploying

Through CI only, as every project here (`docs/adding-a-project.md` §7): preview on a
push to `main` that touched the hub, production on a tag `hub-v<semver>`, both by
`.github/workflows/deploy-hub.yml` calling `_deploy-compose.yml`. The production compose
project is `rebase`; the preview's is `rebase-preview`. Both carried the platform's
former name until 2026-09-15, and each moved the day its environment was
migrated: the stack stopped, `/srv/<project>-data` moved, the database and role
renamed with `ALTER`. Pass `-p` to every `docker compose` you ever run against either
by hand, or compose names a second stack after the directory.

Each environment's `.env` is `${DEPLOY_PATH}/.env`, the root of that environment's
checkout and two levels above the compose file: the deploy passes
`--env-file "${DEPLOY_PATH}/.env"`, never rsyncs a `.env`, and reads nothing beside
the compose file. It holds the `REBASE_*` and `POSTGRES_*` values and is never in the
repository. `REBASE_DATA_DIR` has no default in the compose file, so a `.env` that
forgets it fails the stack instead of mounting an empty directory.

Ports, loopback only, from the table in `docs/adding-a-project.md` §7: production api
8084, web 8085, mcp 8088, Postgres 55435, Documenso 8090; preview 8086, 8087, 8089,
55436. The public paths are `/hub/` (web) and `/api/hub/` + `/api/community/signups`
(api): `projects/website/deploy/letsrebase.conf` proxies them to production on the host
vhost, and `projects/website/deploy/preview.letsrebase.conf` proxies the same paths to
the preview stack at `preview.letsrebase.com` (`location ^~ /api/hub/`,
`preview.letsrebase.conf`, 127.0.0.1:8086 for its api) -- which is why the
preview's Documenso webhook, at `https://preview.letsrebase.com/api/hub/documenso/webhook`,
depends on that vhost rather than reaching the preview api directly. The member area's
mail needs `REBASE_RESEND_API_KEY` and `REBASE_MAIL_FROM` in the host `.env`; without
the key `/hub/login` answers 503 with a sentence. A preview stack that gets a key must
also set `REBASE_HUB_URL` to its own address, or every link it mints points at
production.

«Istanze Pigro» in the admin area (ORB-142) reads PigroCRM's registry of spaces through
the CRM's API, never its database: `REBASE_PIGRO_API_URL` (the CRM's public origin,
also where each space is linked) and `REBASE_PIGRO_REGISTRY_TOKEN`, which must equal
the `PIGROCRM_REGISTRY_TOKEN` in the CRM's own host `.env`. One value, set by hand in
both files, generated once; without it the page answers 503 with a sentence and the CRM
side does not even have the route. The call goes through `rebase_core.http`, the seam
the mail uses, so the tests hand a fake and never reach a CRM.

### Model provider keys (`REBASE_SECRETS_KEY`, `ai-seed`, rotation)

Provider API keys live encrypted in the database (`ai_providers`, AES-256-GCM,
`rebase_core/secrets.py`); the key that opens them, `REBASE_SECRETS_KEY` (32 bytes in
base64, `openssl rand -base64 32`), lives only in the host `.env`, root-only (`chmod
600`), never in the database or a backup. Without it the app refuses to save or read a
provider key and «Modelli AI» says so. The provider's name is the GCM associated data
(REB-889), so a sealed key copied to another provider's row does not open; a row sealed before
that opens through a fallback with no associated data and is sealed again by the next «save»
(`save_key`, `ai-rotate-secrets`): reading never writes.

**Order on production, once (REB-834): one deploy, then `ai-seed` at once.** The two-tag
path (tag the `ai-seed` commit, then the head) does not work: that commit still numbers
its migration 0035, which collides with `0035_one_person_one_linkedin` (REB-800) already
applied on production, and deploying it would mis-stamp alembic. The workflow rsyncs the
sources, builds and runs `up -d` in one step (`_deploy-compose.yml`), so there is no point
between the new image and the new containers to run `ai-seed` in: the public builder
answers 503 «Il team builder è spento.» (the old variables are no longer passed to the
containers) from `up -d` until the command is run. Measured on 2026-10-27, about 30
seconds between the first refused connection and the builder answering again. So:

1. Add `REBASE_SECRETS_KEY=<openssl rand -base64 32>` to the host `.env` (root-only,
   `chmod 600`), next to the existing `REBASE_ANTHROPIC_API_KEY` (and
   `REBASE_TEAM_BUILDER_MODEL` when set; the command defaults to `claude-opus-5`). Keep a
   copy only Lorenzo can read (a root-only file in `/root/`, the password manager): losing
   it loses every stored provider key. The preview first, the same way, because the
   trunk deploys it before any tag.
2. Tag `main` (`hub-v<next>`) and wait for the tag CI and the deploy run to end.
3. The moment `up -d` ends, run the command with the old key handed in through the
   environment, never on the command line (the containers no longer carry it):
   `REBASE_ANTHROPIC_API_KEY="$(grep '^REBASE_ANTHROPIC_API_KEY=' /opt/hub/.env | cut
   -d= -f2-)"; export REBASE_ANTHROPIC_API_KEY; docker compose -p rebase --env-file
   /opt/hub/.env exec -e REBASE_ANTHROPIC_API_KEY api uv run --no-sync rebase ai-seed
   --eur-per-usd <rate>` (`-p rebase-preview --env-file /opt/hub-preview/.env` on the
   preview; there is no `rebase` on the container's `PATH`, hence `uv run --no-sync`). It
   moves the old key and model into the tables (key sealed, `anthropic` on, both tasks on
   the model) and writes the Claude prices in euros from claude.com/pricing (USD per
   million tokens, read 2026-10-27: opus-5 5/25/0.50, opus-5-5 4/20/0.20, thinking billed
   as output). `<rate>` is **euros per one dollar**, the inverse of the ECB's quote (ECB
   1.0870 USD per EUR means `--eur-per-usd 0.9199`), of the day. No rate lives in the
   code. It prints «chiave salvata, finisce con …abcd», never the key, and refuses to run
   twice.
4. Verify with «Prova» on each task in «Modelli AI», and the public builder
   (`POST /api/hub/team/proposals`).
5. Remove `REBASE_ANTHROPIC_API_KEY` and `REBASE_TEAM_BUILDER_MODEL` from the `.env` (the
   key now lives encrypted in the database) and recreate `api`, `mcp` and `cards`.

**Rotation:** generate a new value, then hand both to a one-off container through the
environment, never the command line (`ps` and the shell history would show what opens every
stored key): `read -rs REBASE_SECRETS_KEY_OLD; read -rs REBASE_SECRETS_KEY_NEW; export
REBASE_SECRETS_KEY_OLD REBASE_SECRETS_KEY_NEW; docker compose -p rebase --env-file
/opt/hub/.env run --rm -e REBASE_SECRETS_KEY_OLD -e REBASE_SECRETS_KEY_NEW api rebase
ai-rotate-secrets` (`-p rebase-preview --env-file /opt/hub-preview/.env` on the preview).
It re-seals every stored key in one transaction and prints the count; a wrong old value
changes nothing. Then put the new value in `.env` and recreate **api, mcp and cards**
(`docker compose -p rebase --env-file /opt/hub/.env up -d api mcp cards`): all three read
`REBASE_SECRETS_KEY`, and a `cards` loop on the old value would fail every hourly run
with a sentence naming `REBASE_SECRETS_KEY` and the card would carry it. Between the command and the recreate the
old value no longer opens the keys, so keep `REBASE_CARDS_LOOP=off` for the gap.

### OpenAI and Google (REB-840): prices, verification, the processors gate

The seam supports all three providers; only Anthropic has run against the real API. OpenAI
and Google **stay `enabled=false` in production** until the processors check of the design
holds for each: the privacy notice lists the provider, a data processing agreement exists
with it, and its no-training and retention terms were read. Lorenzo and Ivan decide that;
nothing in the code or a deploy turns a provider on, and a task pointed at a provider that
is off is refused with «Il fornitore è spento» (an evaluation run needs the explicit
`--allow-disabled`, because it sends CVs; see «A provider, a model or a prompt change is
measured»).

- `rebase ai-prices-seed --eur-per-usd <rate>`: writes the reviewed price table
  (`SEED_USD_PRICES` in `ai_config.py`: USD per million tokens, Standard tier, short
  context, from developers.openai.com/api/docs/pricing and
  ai.google.dev/gemini-api/docs/pricing, read 2026-10-27) in euros, `<rate>` being euros per
  one dollar as for `ai-seed`. A row that exists, an admin's edit included, is never
  overwritten; no provider is turned on. Google's page announces dated steps (`gemini-4-argon`
  doubles on 2026-12-01, `gemini-3.8-flash` on 2027-01-01): edit those rows in «Modelli AI»
  when the date comes, and re-read both pages before trusting the table after that.
- `rebase ai-verify --provider openai|google --model <model>`: with the key saved in «Modelli
  AI» (the provider may still be off), one call with a fixed prompt that carries no personal
  data and the seam's native structured output, then the same with the effort setting. It
  prints `key_injected= native_output_ok= thinking_ok= usage_present=` and exits 0 only when
  the first three hold; it prints an exception class and a status, never the key nor the
  provider's text. Tests use stub models. **The live run has not happened:** no OpenAI or
  Gemini key exists yet. When one is saved on the preview, run it for each provider with the
  model chosen; if `key_injected` or `native_output_ok` is `False`, stop and fix `build_model`
  before enabling that provider, and record the verified model and date here.
- Evaluation per provider (`cards-eval --allow-disabled`, the report saved) is the
  evaluation set of the Phase 2 milestone (REB-835): run it once the profiles are accepted.

### Database backups

Both of production's databases are backed up off the origin every night (REB-715,
REB-717): the hub's own (`rebase-db-1`) by the `hub-backup` service at 03:15 UTC, under
`hub/`, and Documenso's (`rebase-documenso-db-1`, every uploaded and sealed PDF) by
`documenso-backup` at 03:45, under `documenso/`. They are one definition in
`docker-compose.documenso.yml`, production's overlay (the preview never loads it, so it
never needs a backup's secrets), and one image and one script in `db-backup/`; a service
differs from the other only in `BACKUP_NAME` (the prefix, the file stems), `BACKUP_HEAD_SQL`
(the query whose answer goes in the `MANIFEST` as the schema version: Alembic's revision
for the hub, Prisma's newest migration for Documenso), the database it reads and its
monitor. Each run is `pg_dump` into a tmpfs, a `MANIFEST`, a CMS envelope to the public
certificate in `HUB_BACKUP_CERT_B64` (Vaultwarden's; the private key is on no server), an
upload to the private R2 bucket `rebase-hub-backup`, a size check against the bucket, and a
ping to that service's healthchecks.io check («Hub database backup», «Documenso backup»;
`/fail` after a failed run, and a check mails when a day plus two hours pass with no
success). R2 expires both prefixes after ninety days (`infra/cloudflare/r2.tf`).

- **Settings.** In the host `.env`, by hand: `HUB_BACKUP_CERT_B64`, `HUB_BACKUP_R2_ACCOUNT_ID`,
  `HUB_BACKUP_BUCKET`, and the optional `HUB_BACKUP_AT` and `DOCUMENSO_BACKUP_AT`. Written
  by the deploy from the `hub-production` Environment (`env-secrets` in `deploy-hub.yml`):
  `HUB_BACKUP_ACCESS_KEY_ID` and `HUB_BACKUP_SECRET_ACCESS_KEY` (one key pair, Object Read
  and Write on that one bucket), `HUB_BACKUP_PING_URL` and `DOCUMENSO_BACKUP_PING_URL`. The
  compose file refuses to start without any of them.
- **The databases are never recreated by the backups.** The image is built `FROM` the
  databases' own `postgres:17-alpine`, pinned by digest in `db-backup/Dockerfile`, because
  the deploy's `build --pull` would otherwise move the host's tag and the next `up -d`
  would recreate every database container on a Postgres nobody chose (REB-715).
  `test_documenso_compose.py` fails when the two databases' tag and the Dockerfile's drift.
  After a deploy, `docker ps` must show the databases' "Up" age unchanged.
- **Tests.** `db-backup/tests/backup-restore.sh` is the drill, with the real compose files
  and scripts against throwaway Postgres servers, an S3 gateway and a stand-in monitor: it
  backs up, proves the object is an envelope no other key opens, proves each monitor is
  pinged (and only its own), and restores both databases into a scratch Postgres, comparing
  every table's row count (`scripts/row-counts.sql`) and the schema version. CI runs it as
  `hub · database backups drill`, preflight as `hub-db-backup-drill`. A change to
  `db-backup/` is done when it passes.
- **Restore** is [`docs/restore-from-backup.md`](docs/restore-from-backup.md), and a real
  object is restored into a scratch Postgres by hand after a change to the backup, never
  only in CI. The dumps hold neither application's secrets (the runbook says where those
  are).

### Documenso, the signing site

Since REB-393 Documenso runs beside production in the `rebase` compose project, from
`docker-compose.documenso.yml`, which only production's `.env` loads with
`COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`. Compose interpolates
every service of every file it reads, profiles or not, so the Documenso services in
`docker-compose.yml` would fail the preview's deploy and CI's build on their secrets.
Anything you run by hand against production passes `-p rebase --env-file
"${DEPLOY_PATH}/.env"` from `projects/hub`, or compose sees neither file nor stack. The
webhook, its SSRF bypass and `REBASE_DOCUMENSO_*` are described above (§ "Contracts are
signed on Documenso"); this section is the container's own.

- **Image**: `documenso/documenso:v2.21.1`, pinned by digest (phase 1 probed v2.18.0; REB-712
  moved it after the v2.19 and v2.20 release notes, REB-907 after v2.21.0 and v2.21.1). An
  upgrade is a pull request that moves the pin, after reading the release notes.
- **Rolling back past this release**: `COMPOSE_FILE=docker-compose.yml:docker-compose.documenso.yml`
  lives in `/opt/hub/.env` on the server, not in the checkout, so redeploying an older
  tag rsyncs `docker-compose.documenso.yml` away while `.env` still names it, and compose
  refuses to come up. Remove the `COMPOSE_FILE` line from `/opt/hub/.env` before rolling
  back past this release. `documenso`, `documenso-db` and the two backup services are not
  stopped by that: they keep running as orphans, unmanaged by the older compose project,
  until stopped by hand (`docker stop rebase-documenso-1 rebase-documenso-db-1
  rebase-documenso-backup-1 rebase-hub-backup-1`).
- **Name and port**: `https://firma.letsrebase.com`, the vhost `deploy/firma.letsrebase.conf`
  (its own certificate) in front of 127.0.0.1:8090.
- **Data**: its own Postgres, `documenso-db`, on `REBASE_DOCUMENSO_DATA_DIR`
  (`/srv/rebase-data/documenso-postgres`), with every uploaded and sealed PDF in it.
  About 500 to 630 MiB of memory once warm, 45 to 80 MiB for its Postgres; the origin has
  3.8 GB and 4 GB of swap since 2026-09-25.
- **Backup** (REB-715): the `documenso-backup` service, one of the two database backups
  described under "Database backups" below; it writes under `documenso/` at 03:45 UTC. The
  dump holds neither Documenso's encryption keys nor its seal certificate: the runbook
  ([`docs/restore-from-backup.md`](docs/restore-from-backup.md)) says where those are and
  what that costs.
- **Certificate**: self-signed, made on the host with OpenSSL, in the `.env` as the
  `.p12` on one line of base64 with its passphrase. The seal is valid and PDF readers say
  its issuer is not trusted; a certificate on Adobe's trust list is a later purchase.
- **Mail**: Documenso sends only its own account mails, through Resend with the hub's
  key; every signing mail is the hub's.
- **Accounts**: public sign-up is off (`DOCUMENSO_DISABLE_SIGNUP` defaults to true). One
  Documenso user per environment, each with its own organisation, team, API token and
  webhook, because a token reads and cancels every envelope of its user's teams
  (probe § 8):

  | Environment | Documenso user | Team | Webhook URL |
  |---|---|---|---|
  | production | `ciao+firma-is@letsrebase.com` | its Personal Team | `http://api:8000/api/hub/documenso/webhook` |
  | preview | `ciao+firma-preview@letsrebase.com` | its Personal Team | `https://preview.letsrebase.com/api/hub/documenso/webhook` |

  Made on 2026-09-25 (REB-408). Each user's own Personal Team holds its API token («prod»
  and «demo», no expiry: revoke and replace one in Documenso, then in that hub's `.env`,
  then redeploy) and its webhook. The two webhooks were written straight into Documenso's
  `Webhook` table (URL, the three events, the secret, the user's own team) rather than
  typed into its form, which is equivalent; the form is the way to edit them.

  Both webhooks send `document.completed`, `document.rejected` and `document.cancelled`,
  each with its own secret, which is that hub's `REBASE_DOCUMENSO_WEBHOOK_SECRET`.
  Production's hub reaches Documenso as `REBASE_DOCUMENSO_URL=http://documenso:3000`,
  the preview's as `https://firma.letsrebase.com`. `docker exec rebase-api-1 uv run
  --no-sync rebase documenso-check` (and `rebase-preview-api-1`) says whether each hub
  reaches Documenso with its token.

  Step 5 of the rollout opens `DOCUMENSO_DISABLE_SIGNUP` for the tens of minutes the two
  environments' users take to be made, on a name already in certificate transparency
  logs, so `NEXT_PRIVATE_ALLOWED_SIGNUP_DOMAINS` (`DOCUMENSO_SIGNUP_DOMAINS` in `.env`,
  defaulting to `letsrebase.com`) refuses any other domain on the server itself, whether
  or not the window is open, so an account made in that window cannot outlive it. Check
  nobody else got in before closing the window: `docker exec rebase-documenso-db-1 psql
  -U documenso -d documenso -tAc 'SELECT email FROM "User"'` must list exactly the two
  `@letsrebase.com` addresses above, beside Documenso's own two internal accounts,
  `serviceaccount@firma.letsrebase.com` and `deleted-account@firma.letsrebase.com`.
- **Who signs for rebase**: the `rebase-*` fields of `REBASE_SIGNER_JSON`, in the host's
  `.env`, never in the repository. With every text `status: final`, production signs
  with whoever's data is there, which until the SRL exists (roadmap #284) is a person's,
  not the company's yet. The value is one line of compact JSON. Compose's `.env` parser
  refuses shell-style quoting (`'...'"'"'...'`), which an apostrophe in an address
  produces (the patch tag of 2026-09-25 failed on it once). Unquoted JSON works as long as
  it holds no ` #` (the rest of the line would become a comment) and no `$` (compose would
  interpolate it); otherwise wrap the value in double quotes and escape every inner `"`,
  `\` and `$` as `\"`, `\\` and `$$`. Check with `docker compose -p rebase --env-file
  /opt/hub/.env config | grep REBASE_SIGNER_JSON` before any tag.
- **The preview sends real mail**: its `.env` carries `REBASE_RESEND_API_KEY` since
  2026-09-25, because «Invia per la firma» refuses without a mail sender; its signer is
  fiction, and its contracts mail is `ciao+firma-preview@letsrebase.com`.
- **Branding** (REB-474): both organisations show rebase, set in Documenso under
  Organisation settings → Preferenze → Branding: the logo is the echo,
  `shared/brand/echo/echo-ink-watermelon-outlines.png` (Ivan, 2026-09-25: the signing
  surfaces and the documents carry the echo; the site's and the hub's headers keep the
  lockup), the brand URL `https://letsrebase.com`, and the colours
  background `#f1f2f3`, foreground `#011936`, primary `#c50d33` with `#ffffff` on it,
  border `#465362`, ring `#ed254e`, radius `0rem`; no custom CSS. With billing off the
  branding needs no plan or licence. The preview's organisation took the same settings by
  copying production's `OrganisationGlobalSettings` branding columns in SQL.
