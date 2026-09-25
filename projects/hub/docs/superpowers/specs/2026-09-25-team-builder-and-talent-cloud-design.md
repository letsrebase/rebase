# The team builder and the talent cloud: a project description becomes an anonymous team in public, and a named one for the companies rebase admits

Date: 2026-09-25. Status: approach, both faces and every decision below taken in
conversation by Ivan on 2026-09-25 (two rounds of questions); this record is written for
his review, with two texts he reads before they ship (§ 3.6 the availability mail, § 6
the privacy wording). Tracker: REB-506 in `Propose a team from a description, and open
the talent cloud to companies` (P-REB-43). Beside it, the same day, the hours report per
match (REB-489, P-REB-42), which is independent: a request from the cloud ends in
matches created by hand in «Crea match», where that project takes over.

## 0. Why

Ivan, 2026-09-25: «piattaforma pubblica per creazione di un team di progetto, mi immagino
un box vuoto conversazionale dove è possibile scrivere la descrizione di un progetto (con
magari degli esempi etc) e poi con AI viene proposto un team anonimizzato e pulsante
assumi team, questo scatena una richiesta verso l'admin e la salva in una nuova tabella
dedicata, per inviare assumi team bisogna inserire email e numero di telefono e nome
della azienda dopo aver visto il team». And: «talent private cloud vetted pool per clienti
pronto all'uso». Then, on the second round: «il cloud privato mostra dati non
anonimizzati, questa sarà la grande differenza».

The hub already holds what both faces need: a freelancer's card with the CV, its text
(`rebase_core.cv_text`), the daily rate, the work mode; a company user who signs in
with a magic link and reads their own request; the admin area with «Talenti» and
«Aziende»; the mail seam; the MCP server for admins. What is missing is a description of
each talent that can be shown to a stranger, a model that turns a project into a team,
the request that follows, and a door for companies.

## 1. The decisions, one paragraph each

**One feature, two faces.** The public team builder and the private talent cloud share
the anonymous cards, the proposal engine, the request table and the admin screens. The
public face shows cards with no name and takes contacts at the end; the private face
shows the same talents by name to a company rebase admitted, with filters, the builder
inside, and requests without a form. A visitor never sees a name; a company in the cloud
never sees less than the admin does about a talent's profile, except the rate rebase
pays (§ 4.2).

**Every freelancer with a CV gets an anonymous card, written by Claude once per CV.**
The card is the ontology Ivan asked for: role, seniority, years, skills, sectors,
languages, work mode, and a client price band. It is generated from the CV's text and
the profile's own fields, stored on the freelancer, regenerated when the CV changes, and
readable by an admin on the talent's page. No name, no link, no employer's name from
the CV: the card describes, it does not identify. «Vetted» is a manual flag on the
freelancer, an action in «Talenti» and over MCP, shown as a badge on the card in the
cloud; the public face does not distinguish.

**The public builder works on everyone with a CV, one shot, with «Rigenera».** Ivan:
«lavora su tutti i freelancer con cv», «colpo solo con rigenera», «ok anche chi ha già
match». The page is the hub's, public, no login (`/hub/team`); the static site will
carry a button to it in its own card. The description, plus a note on «Rigenera», goes to
Claude with the whole catalogue of cards; the answer is a team whose size the description
decides («può essere uno come più persone»), one role and one reason per person, an
anonymous summary of the project, and what the description said about place. The
economics are the hub's arithmetic, not the model's: a price band per person and for
the team, per day and per month at 22 days, from the freelancer's rate plus 40%.

**Remote or local, never a city on the card.** The card says `remoto`, `ibrido` or
`in_sede` as the freelancer declared, and keeps the place the CV names for the engine
only. A description that asks for a team on site gets no remote talent; one that names
where the client is gets people the CV places there or nearby, or none, and the summary
says so. Ivan: «se il need dice che serve un team locale non proporre remoti e se
indicano dove sono comportati di conseguenza».

**Contacts at «Assumi team», nothing before, nothing verified.** Ivan: «dopo che è
stata generata e l'utente preme assumi il team, chiedi le sue info per il ricontatto,
no magic link o altro». Email, phone and company name make the request; it lands in a
table of its own, mails ciao@, and the admin works it by hand.

**The admin contacts the talents with one button, and each talent answers with one
click.** «Contatta i talenti» sends every person of the team a mail with the anonymous
summary of the project, the role proposed for them, their own daily rate (never the
client's), and two buttons, one green «Sono disponibile» and one red «Non sono
disponibile», each a link with a token; the click records the answer on the request,
and the admin reads who said what. Nothing else is automatic: contacting the company
and closing the request are the admin's.

**The cloud is a company's, admitted from its request, and shows names.** An admin
opens the cloud to an existing company request; the referente signs in as they already
do and finds «Talent cloud»: every card with the person's name, surname, links and CV
beside the anonymous description, a vetted badge where it applies, filters by role,
seniority, skill, work mode and price band, the same builder box, «Assumi team» with no
form, and «Richiedi» on a single card. Requests from the cloud carry the company and its
user and land in the same table.

**The cloud is a service sold offline, and the public page points at it.** Ivan:
«acquisto gestito offline a parte». The public page ends with a box: in beta the
builder has no limits, and a company that wants the whole cloud, by name and without
limits, asks for access through the company wizard, opened with origin `team-builder`;
the wizard shows a box saying that this request is for the talent cloud, and the origin
is saved on the request so the admin knows who came from there and calls them.

**Claude through the API, no limits in beta, one switch.** `claude-opus-5` through the
official SDK behind a seam of the hub's own (§ 5), with adaptive thinking, structured
output, the catalogue cached as a prompt prefix, and the server-side refusal fallback.
No throttle and no quota now, by Ivan's word; a setting turns the public page off, every
proposal row keeps the tokens it cost, and PostHog counts generations, requests and
answers. The privacy page says what goes to Anthropic (§ 6).

**Everything over MCP.** Ivan: «chiaramente per tutto anche mcp». Requests, the talents'
answers, the contact action, the states, the vetted flag, the cloud grants, the cards
and their regeneration, and the proposal itself, for an admin with a token.

**Not the existing company requests.** Ivan: «non mi interessano le richieste già
esistenti, è un nuovo servizio». The builder does not run on a wizard request, and a
wizard request does not become a team request.

## 2. Data model

New tables, one migration:

- `freelancer_cards`: `freelancer_id` (PK, FK), `cv_sha256` (varchar(64)), `card`
  (JSONB, § 2.1), `model` (varchar(60)), `input_tokens`, `output_tokens` (integer),
  `generated_at`, `error` (text, nullable: the last failure, for a card that could not
  be written).
- `team_proposals`: `id`, `descrizione` (text), `nota` (text, nullable: the «Rigenera»
  note), `previous_id` (FK self, nullable), `riassunto` (text: the anonymous summary),
  `luogo` (JSONB: `{"locale": bool, "dove": str | null}` as the engine read it), `team`
  (JSONB: a list of `{"freelancer_id", "ruolo", "motivazione", "giorni_settimana"}`),
  `economia` (JSONB, § 3.3), `model`, `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `origine` (varchar(10): `pubblico` | `cloud`), `user_id` (FK,
  nullable: the cloud user), `client_hash` (varchar(64): SHA-256 of the client key the
  rate limiter already computes, for reading abuse after the fact, never an address),
  `created_at`.
- `team_requests`: `id`, `proposal_id` (FK), `origine` (`pubblico` | `cloud`),
  `azienda` (varchar(200)), `email` (varchar(320)), `telefono` (varchar(40)),
  `user_id` (FK, nullable), `company_id` (FK, nullable: the cloud company's request
  row), `stato` (`nuova` | `contattata` | `chiusa`), `note` (text, nullable, the
  admin's), `contacted_at`, `closed_at`, `created_at`, `updated_at`; `talenti`
  through `team_request_talents`: `request_id`, `freelancer_id`, `ruolo`,
  `token_hash` (varchar(64)), `mail_sent_at`, `risposta` (`si` | `no`, nullable),
  `risposta_at`.
- `talent_cloud_grants`: `id`, `company_id` (FK `companies`), `user_id` (FK: the
  referente's user), `granted_by` (FK users), `granted_at`, `revoked_by`,
  `revoked_at`. One live grant per user.
- On `freelancers`: `vetted_at` (timestamptz, nullable), `vetted_by` (FK users,
  nullable).

### 2.1 The card

```json
{
  "ruolo": "Backend developer",
  "seniority": "senior",
  "anni": 9,
  "competenze": ["Python", "FastAPI", "PostgreSQL", "AWS"],
  "settori": ["fintech", "e-commerce"],
  "lingue": ["italiano", "inglese"],
  "modalita": "remoto",
  "luogo": "Torino",
  "sintesi": "Backend developer senior, nove anni fra fintech ed e-commerce, API in Python e infrastruttura AWS."
}
```

`seniority` is one of `junior`, `mid`, `senior`, `lead`; `modalita` is the profile's
`remoto` field, not the model's guess; `luogo` is for the engine and never rendered
outside the admin's talent page; `sintesi` is two sentences at most, Italian, with no
name, no company name and no link. The price band is not on the card: it is computed
from `tariffa_giornaliera` at read time (§ 3.3), so a rate the freelancer edits is right
the next time.

## 3. The public face

### 3.1 The page

`/hub/team`, under the public layout with the wizards' chrome: a heading, one
paragraph, three example descriptions as buttons that fill the box, the box
(«Descrivi il progetto: cosa va fatto, per quanto tempo, dove, con che tecnologie»), and
«Proponi il team». While it runs, the button says «Sto leggendo i profili…»; a
generation takes seconds to tens of seconds. The result: the anonymous summary, one
card per person (role in the team, the reason, the anonymous description, the price
band per day), the team's band per day and per month, a box for a note and
«Rigenera», and «Assumi team». Under it all, the beta box of § 1 with «Chiedi l'accesso
al talent cloud», a link to `/hub/companies?origine=team-builder`.

«Assumi team» opens the contact form (company name, email, phone, all required, the
same rules as the company wizard's fields), and «Invia la richiesta» ends on a thanks
paragraph: «Grazie: ti scriviamo entro due giorni lavorativi.» No account, no mail to
the visitor.

### 3.2 The routes

- `POST /api/hub/team/proposals` (public): `{descrizione, nota?, previous_id?}` →
  `TeamProposalRead` (§ 3.3). `503` with «Il team builder è spento.» when
  `REBASE_TEAM_BUILDER_ENABLED` is false or no API key is configured; `422` for a
  description under 40 or over 4000 characters; `502` «Non riesco a proporre un team
  adesso: riprova tra poco.» when Claude does not answer or answers a refusal (the
  refusal is logged with its category); `200` with an empty `team` and the summary's
  sentence when no talent fits (a local need with nobody there, say).
- `POST /api/hub/team/requests` (public): `{proposal_id, azienda, email, telefono}` →
  `201` `{id}`; refuses a proposal already requested (`409`) and a proposal older than a
  day (`422`).
- `GET /api/hub/team/availability?t=<token>&r=si|no` (public): records the talent's
  answer and redirects to `/hub/team/risposta?esito=si|no`, a public page that says
  «Grazie, abbiamo registrato la tua disponibilità» or «…che non sei disponibile»; a
  spent or unknown token lands on the same page with «Questo link non è più valido».

### 3.3 The proposal

`TeamProposalRead`: `id`, `riassunto`, `luogo`, `team: [{freelancer_id, ruolo,
motivazione, giorni_settimana, scheda: {ruolo, seniority, anni, competenze, settori,
lingue, modalita, sintesi}, fascia: {min, max}}]`, `economia: {giorno: {min, max}, mese:
{min, max}, giorni_mese: 22}`, `previous_id`, `created_at`. The band per person is the
freelancer's `tariffa_giornaliera` times 1.4, placed in one of `[0, 300)`, `[300, 400)`,
`[400, 500)`, `[500, 650)`, `[650, 800)`, `[800, ∞)` euro per day and shown as its
bounds («400–500 € al giorno», «oltre 800 € al giorno»); the team's band per day is the
sum of the bounds, per month the same times 22. A freelancer with no rate is in the
catalogue with no band, and the page says «tariffa da definire» for them. Ivan:
«meglio fascia», «mensile a 22 giorni», «tariffa del cliente che di base sarà quella
del freelancer +40%».

### 3.4 The engine

`rebase_core/team_builder.py`: `TeamBuilder(session, llm, settings, now)` with
`propose(descrizione, nota, previous, origine, user_id, client_hash) -> TeamProposalRead`.
The catalogue is every live freelancer (`deleted_at IS NULL`, `stato != 'scartato'`)
with a card, rendered as one JSON line each: `{"id": "<short id>", ...card without
sintesi's name-free check, "fascia": "400–500"}` where `<short id>` is the first eight
characters of the freelancer's id (the model never sees a full id, and the answer is
mapped back through a dict the service holds). The system prompt: who rebase is, what
a team proposal is, the rules (size from the description; one role per person; a
person once; skills before sectors; remote and local as § 1 says; the summary must
name no company, product or person from the description; answer in Italian), then the
catalogue as a second system block with `cache_control` (the catalogue changes when a
card does, so the prefix stays warm across visitors for the cache's five minutes and is
rewritten rarely). The user turn: the description, and on «Rigenera» the previous
summary, the previous team's short ids and the note («togli il designer»). Output
through `output_config.format` with the JSON schema of `{riassunto, luogo: {locale,
dove}, team: [{id, ruolo, motivazione, giorni_settimana}]}`, `additionalProperties:
false`, validated again by a Pydantic model on the way in; a short id the catalogue
does not hold drops that person and logs it. Adaptive thinking, effort `medium`,
`max_tokens` 8000, the server-side fallback `default` with its beta header. The
proposal row keeps `model` and the three token counts from `usage`.

### 3.5 The request, in the admin

«Richieste team» (`/admin/team`), a nav entry between «Match» and «Aziende»: the list
(company, origin, date, state, how many talents answered yes of how many asked), and
the request page: the description as typed, the summary, the place read, the team with
the real names linking to each talent's page, their own rates and the client bands, the
company's contacts, the state, the admin's note; «Contatta i talenti» (once; then
«Rimanda a chi non ha risposto»), the answers per talent with their time, «Segna come
contattata», «Chiudi». `GET/POST /api/hub/team/requests...` (admin) behind `AdminDep`.
A request arriving mails `REBASE_CONTRACTS_MAIL` (ciao@) «Nuova richiesta team da
Acme S.r.l.» with the summary and a link to the page.

### 3.6 The availability mail

`team_availability_mail(to, *, nome, ruolo, riassunto, tariffa, yes_url, no_url)`:
subject «Un progetto per te: sei disponibile?»; the body says rebase has a project the
person fits, the anonymous summary, the role proposed, that the fee would be their own
daily rate as on their card, and the two buttons, «Sono disponibile» in the site's green
and «Non sono disponibile» in the watermelon red, each a link with a one-use token
(SHA-256 stored, `token_urlsafe(32)` sent, the magic link's own shape), valid thirty
days; then that rebase will write back with the details. Ivan reads the wording on the
PR before it ships. The link's page (§ 3.2) needs no login.

## 4. The private face

### 4.1 Access

On a company request's page in «Aziende», «Apri il talent cloud» writes a
`talent_cloud_grants` row for the request's user and mails the referente «Il talent
cloud di rebase è aperto per Acme S.r.l.» with a link to `/hub/me/cloud` (the magic link
flow they already have); «Revoca» closes it. `MeRead` gains `talent_cloud: bool`, and the
member area's nav gains «Talent cloud» when true. Ivan: «accesso al cloud privato da
azienda esistente».

### 4.2 The cloud

`/hub/me/cloud`, for a signed-in user with a live grant (`403` otherwise): the same
builder box on top, then the talents as cards with the name, surname, links, the CV to
open (`GET /api/hub/me/cloud/talents/{id}/cv`, the member's own CV route reused with the
grant as the guard), the anonymous description, the vetted badge («Verificato da
rebase»), the client band; filters by role (the card's `ruolo`, as typed values
grouped), seniority, one skill (a text search over `competenze`), work mode and price
band; sorted vetted first, then by name. A card has «Richiedi»: a request with that one
talent, no form. The builder's «Assumi team» files the request with the company's own
data (`origine = cloud`, `user_id`, `company_id`), no form. What the cloud never shows:
the freelancer's own rate, their email, their phone, their notes, their state.
`GET /api/hub/me/cloud/talents` (cursor-paginated like `talenti`, the same
`pagination` module) and `POST /api/hub/me/cloud/requests`.

### 4.3 What the public page says about it

The beta box: «Il team builder è in beta e senza limiti. Le aziende che entrano nel
talent cloud vedono i profili per nome, sfogliano tutto il cloud e chiedono i talenti
direttamente.» and the button to the wizard with `origine=team-builder`. The company
wizard, when the origin is `team-builder`, shows a box above the form: «Stai chiedendo
l'accesso al talent cloud: compila la richiesta e ti ricontattiamo noi.» The origin
lands in `companies.origine` as every origin does, and the «Aziende» list shows «da
team builder» on the row.

## 5. The Claude seam

`rebase_core/llm.py`: a `LlmCall` protocol, `complete(request: LlmRequest) ->
LlmResponse`, with `LlmRequest(system: list[dict], messages: list[dict], schema: dict,
max_tokens: int)` and `LlmResponse(text: str | None, stop_reason: str, refusal_category:
str | None, model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int)`;
`AnthropicCall(settings)` implements it with the official `anthropic` SDK (a dependency
of `rebase_core`), `client.beta.messages.create(model=settings.team_builder_model,
max_tokens, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
thinking={"type": "adaptive"}, output_config={"effort": "medium", "format": {"type":
"json_schema", "schema": ...}}, system=[...with cache_control...], messages=[...])`,
reading the first text block; `RecordingCall` in the tests answers scripted responses,
the way `RecordingSender` does for mail. Two callers: the card writer (§ 5.1) and the
engine (§ 3.4). The key is `REBASE_ANTHROPIC_API_KEY`; the model
`REBASE_TEAM_BUILDER_MODEL`, default `claude-opus-5`; the switch
`REBASE_TEAM_BUILDER_ENABLED`, default true. Without a key, cards are not written and the
builder answers `503`; nothing else of the hub changes.

### 5.1 The card writer

`rebase_core/cards.py`: `CardWriter(session, llm, settings, now)` with
`write(freelancer_id) -> FreelancerCardRead` and `refresh_stale(limit) -> int`. `write`
takes the CV's text (`FreelancerService.cv_text`, the same page and character ceilings
as `read_freelancer_cv`), `posizione`, `remoto` and `tariffa_giornaliera`, asks for the
card of § 2.1 through the schema, stores it with the CV's SHA-256 and the tokens; a
refusal or an error leaves the previous card and writes `error`. It runs when a CV
arrives or changes (`MemberService.replace_cv` and the admin's `create_freelancer_from
signup` path, in the background after the response) and from `rebase cards-refresh`,
which writes every freelancer whose CV hash differs from the card's, `limit` at a time,
for the backlog and for a model change; the admin's talent page has «Rigenera scheda».
A freelancer who deletes their CV keeps no card: the row goes with it.

## 6. Privacy, settings, deployment

The site's privacy page says today «Non trasferiamo nulla a terzi». It gains a
paragraph: the CV's text is read once by Anthropic's API, in the European Union where
the model runs, to write an anonymous description of the profile, and the description a
visitor types on the team builder is sent to the same API to propose a team; Anthropic
does not train on it; the sentence names Anthropic's privacy page. Ivan reads it before
the merge. Talents who signed up before this ships get one mail from the admin, as a
campaign, telling them their card exists and what it says.

Settings: `REBASE_ANTHROPIC_API_KEY`, `REBASE_TEAM_BUILDER_MODEL`,
`REBASE_TEAM_BUILDER_ENABLED`, in `config.py`, `.env.example` and the compose
`x-api-environment` list. PostHog events through the existing `Tracker`:
`team_proposta_generata` (origine, persone, tokens), `team_richiesta_inviata` (origine),
`team_talento_risposta` (risposta). Deployment: the migration on the hub's boot; the key
in production's and preview's `.env` before the tag; `rebase cards-refresh` once after
the deploy, for the backlog.

## 7. Testing

- **Core**: the band arithmetic (every boundary, no rate, 22 days); the catalogue
  rendering (short ids, no name, no link, the vetted flag absent); the engine with a
  `RecordingCall` (a scripted answer mapped back to freelancers, an unknown short id
  dropped, a refusal → the error, a local need with an empty team, the «Rigenera»
  turn carrying the previous ids and the note, the token counts stored); the card
  writer (the hash, the refresh of a changed CV only, the error kept, the deletion);
  the request (states, the one-use tokens, `si`/`no` recorded once, the second click
  refused, the mail per talent once, «Rimanda» only to the silent); the grants (one
  live per user, the guard); the anonymous summary refused when it repeats the
  company's name from the description (a test on the schema validator's own check: the
  summary must not contain any word of the `azienda` field of the request that follows;
  the engine's prompt asks it, the request's writer checks it and logs).
- **API**: the three public routes and their codes, the admin routes behind `AdminDep`,
  the cloud routes behind the grant, the `503` without a key.
- **MCP**: the tools of § 8 in `test_tools.py`.
- **Web**: the public page (examples, the run, the result, «Rigenera», the form, the
  thanks, the beta box), the answer page, «Richieste team» and the request page, the
  talent page's card and «Rigenera scheda», «Talenti»'s vetted action and badge,
  «Aziende»'s grant action and «da team builder», the wizard's box, the member nav and
  the cloud page with its filters and «Richiedi».
- **Preview**: a real generation with the preview's key and its catalogue, a request,
  the mail to a test talent, the click on both buttons, a grant to a test company, the
  cloud page; the video.

## 8. Over MCP

| Tool | Does |
|---|---|
| `list_team_requests(stato=None, origine=None, limit)`, `get_team_request(id)` | the admin list and page |
| `contact_team_talents(id, only_silent=False)` | «Contatta i talenti» / «Rimanda» |
| `set_team_request_status(id, stato, note=None)` | `contattata`, `chiusa` |
| `propose_team(descrizione, nota=None, previous_id=None)` | the engine, as the admin (origine `admin`, not saved as public) |
| `get_freelancer_card(freelancer_id)`, `regenerate_freelancer_card(freelancer_id)` | the card |
| `set_freelancer_vetted(freelancer_id, vetted: bool)` | the flag |
| `grant_talent_cloud(company_id)`, `revoke_talent_cloud(company_id)`, `list_talent_cloud_grants()` | the grants |

`get_talento` and `list_talenti` (REB-282) carry `vetted_at` and whether a card exists.

## 9. Phases

Two milestones of P-REB-43, one draft PR each:

**Describe every talent anonymously and propose a team in public**: the seam, the
settings, the migration, the card writer and its CLI and hooks, the band arithmetic,
the engine, the public routes, the public page and the answer page, the request table
and the admin's «Richieste team», the PostHog events, the privacy paragraph.

**Work a team request with the talents, and open the cloud to a company**: the
availability mail and the answer route, the vetted flag and its badge, the grants and
their mail, the member's cloud page with its filters and requests, the wizard's box and
«da team builder», the MCP tools, the talents' card on the admin talent page.

The plan, one task per card, is
`projects/hub/docs/superpowers/plans/2026-09-25-team-builder-and-talent-cloud.md`.

## 10. Not here

- A conversation with the model (questions back to the visitor): one shot and
  «Rigenera», by Ivan's word.
- Limits, captchas or quotas on the public page: none in beta; the switch and the
  token counts are the only guard, and a later card adds a quota when the numbers say
  so.
- Payment for the cloud: sold offline.
- Availability as a field on the card, or a calendar: the mail's two buttons are the
  availability, per request.
- The static site's button to `/hub/team` and the campaign mail to existing talents:
  their own cards, after the page exists.
- Running the builder on existing company requests, or turning a team request into
  matches automatically: the admin creates matches in «Crea match» by hand.
