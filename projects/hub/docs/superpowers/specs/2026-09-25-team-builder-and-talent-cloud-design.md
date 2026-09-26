# The team builder and the talent cloud: a project description becomes an anonymous team in public, and a named one for the companies rebase admits

Date: 2026-09-25. Status: approach, both faces and every decision below taken in
conversation by Ivan on 2026-09-25 (two rounds of questions); amended the same day after
an independent review of the text against the code (where a CV arrives, how the talent's
answer is recorded, the short ids, the migration numbering, the wizard's parameter, the
privacy wording, the errors, the public read's ids). Written for his review, with four
things he reads before they ship: § 3.6 the availability mail and its two colours, § 4.4
what the cloud tells the talents, § 6 the two privacy paragraphs, and the question of an
opt-out in § 4.4. Tracker: REB-506 in `Propose a team from a description, and open the
talent cloud to companies` (P-REB-43). Beside it, the same day, the hours report per
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
public face shows cards with no name and no id, and takes contacts at the end; the
private face shows the same talents by name to a company rebase admitted, with filters,
the builder inside, and requests without a form. What the cloud never shows is one
list, § 4.2: the freelancer's own rate, their state, the admin's notes; the CV it does
show carries whatever contact data the freelancer wrote in it, and the privacy text says
so (§ 6).

**Every freelancer with a CV gets an anonymous card, written by Claude once per CV
version.** The card is the ontology Ivan asked for: role, seniority, years, skills,
sectors, languages, and a client price band. It is generated from the CV's text and the
profile's own fields, stored on the freelancer with the hash of the CV it came from,
regenerated when the CV changes, and readable by an admin on the talent's page. No name,
no link, no employer's name from the CV: the card describes, it does not identify. The
work mode is not on the card: it is the profile's `remoto` field, read when the card is
shown, so a freelancer who changes it is right the next time. «Vetted» is a manual flag
on the freelancer, an action in «Talenti» and over MCP, shown as a badge on the card in
the cloud; the public face does not distinguish.

**The public builder works on everyone with a CV, one shot, with «Rigenera».** Ivan:
«lavora su tutti i freelancer con cv», «colpo solo con rigenera», «ok anche chi ha già
match». The page is the hub's, public, no login (`/hub/team`); the static site will
carry a button to it in its own card. The description, plus a note on «Rigenera», goes to
Claude with the whole catalogue of cards; the answer is a team whose size the description
decides («può essere uno come più persone»), one role and one reason per person, an
anonymous summary of the project, and what the description said about place. The
economics are the hub's arithmetic, not the model's: a price band per person and for
the team, per day and per month at 22 days, from the freelancer's rate plus 40%.

**Remote or local, never a city on the card.** The card keeps the place the CV names
for the engine only; the work mode shown is the profile's. A description that asks for
a team on site gets no remote talent; one that names where the client is gets people
the CV places there or nearby, or none, and the summary says so. The prompt forbids a
place in a person's `motivazione`. Ivan: «se il need dice che serve un team locale non
proporre remoti e se indicano dove sono comportati di conseguenza».

**Contacts at «Assumi team», nothing before, nothing verified.** Ivan: «dopo che è
stata generata e l'utente preme assumi il team, chiedi le sue info per il ricontatto,
no magic link o altro». Email, phone and company name make the request; it lands in a
table of its own, mails ciao@, and the admin works it by hand. A proposal is requested
once: a second «Assumi team» on it, a double click included, answers `409`.

**The admin contacts the talents with one button, and each talent answers with one
click on a page, never on a GET.** «Contatta i talenti» sends every person of the team a
mail with the anonymous summary of the project, the role proposed for them, their own
daily rate (never the client's), and two buttons, «Sono disponibile» and «Non sono
disponibile», each a link to a page of the hub carrying a one-use token and the answer;
the page shows the answer and one confirm button that posts it, so a mail scanner that
fetches every link records nothing. The click records the answer on the request, and the
admin reads who said what. Before the first send the admin can edit the summary, and the
send refuses a summary that names the company. Nothing else is automatic: contacting the
company and closing the request are the admin's.

**The cloud is a company's, admitted from its request, and shows names.** An admin
opens the cloud from a company request's page; the grant is the referente's for that
company, one live grant per person and company: a second request of the same referente
for the same company finds the one that exists, a person behind two companies holds
two grants and opens the cloud through either, and what they propose there is
attributed to the newest live grant's company.
The referente signs in as they already do and finds «Talent cloud»: every card with the
person's name, surname, links and CV beside the anonymous description, a vetted badge
where it applies, filters by role, seniority, skill, work mode and price band, the same
builder box, «Assumi team» with no form, and «Richiedi» on a single card. Requests from
the cloud carry the company and its user and land in the same table.

**The cloud is a service sold offline, and the public page points at it.** Ivan:
«acquisto gestito offline a parte». The public page ends with a box: in beta the
builder has no limits, and a company that wants the whole cloud, by name and without
limits, asks for access through the company wizard, opened with `?da=team-builder`
(the parameter the wizard already reads as the origin); the wizard shows a box saying
that this request is for the talent cloud, and the origin is saved on the request so the
admin knows who came from there and calls them.

**Claude through the API, no quota in beta, a cap and a switch.** `claude-opus-5`
through the official SDK behind a seam of the hub's own (§ 5), with adaptive thinking,
structured output, the catalogue cached as a prompt prefix, inference kept in the
European Union, and the server-side refusal fallback. No throttle and no quota on
proposals, by Ivan's word; what there is: a setting that turns the public page off, a
cap on how many proposals run at once so the one API process keeps answering the
member area and the webhooks, a timeout under nginx's, the usual speed bump on the
request route (it mails ciao@), every proposal row keeping the tokens it cost, and
PostHog counting generations, requests and answers. The privacy page says what goes to
Anthropic and what the cloud shows (§ 6).

**Everything over MCP.** Ivan: «chiaramente per tutto anche mcp». Requests, the talents'
answers, the contact action, the states, the vetted flag, the cloud grants, the cards
and their regeneration, and the proposal itself, for an admin with a token.

**Not the existing company requests.** Ivan: «non mi interessano le richieste già
esistenti, è un nuovo servizio». The builder does not run on a wizard request, and a
wizard request does not become a team request.

## 2. Data model

New tables, one migration, numbered when it is written as the next free number after
every open branch's (`0020` on the campaigns branch, `0021` on the hours-report branch,
so `0022` today) and re-pointed at `main`'s head before the merge, since three
migrations revising `0019` would be three heads and `alembic upgrade head` refuses
those at the API's boot:

- `freelancer_cards`: `freelancer_id` (PK, FK, cascade), `cv_sha256` (varchar(64): the
  CV the card came from), `card` (JSONB, § 2.1, nullable while only an error exists),
  `model` (varchar(60)), `input_tokens`, `output_tokens` (integer), `generated_at`,
  `error` (text, nullable: the last failure), `error_cv_sha256` (varchar(64), nullable:
  the CV that failed on a refusal or a bad shape, so it is not retried until the CV
  changes; a provider outage leaves it null, so the next run retries).
- `team_proposals`: `id`, `descrizione` (text), `nota` (text, nullable: the «Rigenera»
  note), `previous_id` (FK self, nullable), `riassunto` (text: the anonymous summary,
  editable by the admin), `luogo` (JSONB: `{"locale": bool, "dove": str | null}` as
  the engine read it), `team` (JSONB: a list of `{"posizione", "freelancer_id",
  "ruolo", "motivazione", "giorni_settimana"}`), `economia` (JSONB, § 3.3), `model`,
  `input_tokens`, `output_tokens`, `cache_read_tokens`, `origine` (varchar(10):
  `pubblico` | `cloud` | `admin`), `user_id` (FK, nullable: the cloud user or the
  admin), `created_at`.
- `team_requests`: `id`, `proposal_id` (FK, nullable: a single-talent request has
  none; unique where not null, which is what makes a double click a `409`), `origine`
  (`pubblico` | `cloud`), `azienda` (varchar(200)), `email` (varchar(320)), `telefono`
  (varchar(40), nullable: a cloud user may have none on file), `user_id` (FK,
  nullable), `company_id` (FK, nullable: the cloud company's request row), `stato`
  (`nuova` | `contattata` | `chiusa`), `note` (text, nullable, the admin's),
  `contacted_at`, `closed_at`, `created_at`, `updated_at`; `talenti` through
  `team_request_talents`: `request_id`, `freelancer_id`, `ruolo`, `token_hash`
  (varchar(64), nullable until the first send), `mail_sent_at`, `risposta` (`si` |
  `no`, nullable), `risposta_at`; unique `(request_id, freelancer_id)`.
- `talent_cloud_grants`: `id`, `user_id` (FK: the referente's user), `company_id` (FK
  `companies`: the request the grant was opened from), `granted_by` (FK users),
  `granted_at`, `revoked_by`, `revoked_at`. One live grant per user and company (a
  partial unique index on `(user_id, company_id)` where `revoked_at IS NULL`).
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
  "luogo": "Torino",
  "sintesi": "Backend developer senior, nove anni fra fintech ed e-commerce, API in Python e infrastruttura AWS."
}
```

`seniority` is one of `junior`, `mid`, `senior`, `lead`; `luogo` is the city or region
the CV names, or null, required in the schema (null, not absent) and never rendered
outside the admin's talent page; `sintesi` is two sentences at most, Italian, with no
name, no company name and no link; the writer checks that last rule itself, and a
card that carries the freelancer's surname as a word in `ruolo`, `sintesi`, `competenze`
or `settori` (not in `luogo` or `lingue`: Messina, Russo and Tedesco are a city and two
languages), or `http://`, `https://`, `www.` or `@` in any field (the bare word «HTTP»
is a skill), is a shape failure, not a card; so is
the card of a CV that fails (`max_tokens` included): a card never outlives its CV. Not on the card, read when it is shown: the work
mode (`Freelancer.remoto`: `remoto`, `ibrido`, `in_sede`, or unknown when the profile
has none) and the price band, computed from `tariffa_giornaliera` (§ 3.3), so a rate or
a mode the freelancer edits is right the next time.

## 3. The public face

### 3.1 The page

`/hub/team`, under the public layout with the wizards' chrome: a heading, one
paragraph, three example descriptions as buttons that fill the box, the box
(«Descrivi il progetto: cosa va fatto, per quanto tempo, dove, con che tecnologie»), and
«Proponi il team». While it runs, the button says «Sto leggendo i profili…»; a
generation takes seconds to tens of seconds. The result: the anonymous summary, one
card per person (role in the team, the reason, the anonymous description, the work
mode, the price band per day), the team's band per day and per month, a box for a note
and «Rigenera», and «Assumi team». Under it all, the beta box of § 1 with «Chiedi
l'accesso al talent cloud», a link to `/hub/companies?da=team-builder`.

«Assumi team» opens the contact form (company name, email, phone, all required, the
same rules as the company wizard's fields), and «Invia la richiesta» ends on a thanks
paragraph: «Grazie: ti scriviamo entro due giorni lavorativi.» No account, no mail to
the visitor.

### 3.2 The routes

- `POST /api/hub/team/proposals` (public, behind the wizards' `spend_one` speed bump):
  `{descrizione, nota?, previous_id?}` → `TeamProposalRead` (§ 3.3). `previous_id`
  must name a proposal of the caller's origin (`public` here, `cloud` on the cloud's
  route, where it must also be the caller's own) younger than a day, else `422`; the
  proposal's id, a UUID with random bits the caller received, is its capability. `503` with «Il team builder è spento.» when
  `REBASE_TEAM_BUILDER_ENABLED` is false or no API key is configured
  (`TeamBuilderOff`, a domain error the API maps to `503`); `503` with «Troppe
  richieste in questo momento: riprova tra un minuto.» when the cap of § 5 is full;
  `422` for a description under 40 or over 4000 characters; `502` «Non riesco a
  proporre un team adesso: riprova tra poco.» when Claude does not answer, answers a
  refusal, runs out of tokens, or answers something that is not the shape
  (`LlmUnavailable`, a domain error mapped to `502`; the refusal is logged with its
  category); `200` with an empty `team` and the summary's sentence when no talent
  fits (a local need with nobody there, say).
- `POST /api/hub/team/requests` (public, behind the same `spend_one` speed bump as the
  wizards: it mails ciao@ on every call): `{proposal_id, azienda, email, telefono}` →
  `201` `{id}`; a proposal already requested is `409` (the unique index decides, so a
  double click is one request), a proposal older than a day or not a public one `422`.
- `POST /api/hub/team/availability` (public): `{t: <token>, risposta: "si" | "no"}`,
  posted by the answer page, records the talent's answer and answers `{esito: "si" |
  "no" | "invalid"}`; the page is `/hub/team/risposta?t=…&r=si|no`, which shows «Vuoi
  confermare che sei disponibile?» (or «…che non sei disponibile?») with one button
  «Conferma», and after the post «Grazie, abbiamo registrato la tua disponibilità»,
  «…che non sei disponibile», or «Questo link non è più valido» for a spent, unknown or
  expired token. A GET records nothing, which is what keeps a mail scanner from
  answering for the talent.

### 3.3 The proposal

`TeamProposalRead`: `id`, `riassunto`, `luogo`, `team: [{posizione, ruolo, motivazione,
giorni_settimana, scheda: {ruolo, seniority, anni, competenze, settori, lingue,
sintesi}, modalita, fascia: {min, max}}]`, `economia: {giorno: {min, max}, mese: {min,
max}, giorni_mese: 22}`, `previous_id`, `created_at`. `posizione` is the person's
index in this proposal (1, 2, 3); the public read carries no freelancer id at all, so a
visitor cannot follow one talent across proposals or rebuild the catalogue; the admin's
and the cloud's reads carry `freelancer_id` beside it. The band per person is the
freelancer's `tariffa_giornaliera` times 1.4, placed in one of `[0, 300)`, `[300, 400)`,
`[400, 500)`, `[500, 650)`, `[650, 800)`, `[800, ∞)` euro per day and shown as its
bounds («400–500 € al giorno», «oltre 800 € al giorno»); the team's band per day is the
sum of the bounds, per month the same times 22. A freelancer with no rate is in the
catalogue with no band, and the page says «tariffa da definire» for them. Ivan:
«meglio fascia», «mensile a 22 giorni», «tariffa del cliente che di base sarà quella
del freelancer +40%».

### 3.4 The engine

`rebase_core/team_builder.py`: `TeamBuilder(session, llm, settings, *, tracker=None,
now=utcnow)` with `propose(data, *, origine, user_id) -> TeamProposalRead` and
`get(proposal_id, *, public: bool)`. The catalogue is every live freelancer
(`deleted_at IS NULL`, `stato != 'scartato'`) with a card, sorted by the freelancer's
id so the text is the same for every visitor (the cache prefix), rendered as one JSON
line each: `{"id": "t1", "ruolo", "seniority", "anni", "competenze", "settori",
"lingue", "luogo", "modalita", "fascia": "400–500"}`, where `t1`, `t2`… is the line's
position in that order (a slice of the UUIDv7 would collide within a minute of
signups) and the service keeps the map from position to freelancer for this call;
`sintesi` is left out of the catalogue, the structured fields are what the engine
reads. The system prompt: who rebase is, what a team proposal is, the rules (size from
the description; one role per person; a person once; skills before sectors; remote and
local as § 1 says; the summary must name no company, product or person from the
description; a `motivazione` names no place; answer in Italian), then the catalogue as
a second system block with `cache_control` (the catalogue changes when a card does, so
the prefix stays warm across visitors for the cache's five minutes and is rewritten
rarely). The user turn: the description, and on «Rigenera» the previous summary, the
previous team's positions and the note («togli il designer»). Output through
`output_config.format` with the JSON schema of `{riassunto, luogo: {locale, dove},
team: [{id, ruolo, motivazione, giorni_settimana}]}`, `additionalProperties: false`,
validated again by a Pydantic model on the way in; an id the catalogue does not hold
or repeats drops that line and logs it, and so does, when `luogo.locale` is true, a
member whose `modalita` is `remoto` or unknown (the place itself stays the model's
judgement: the catalogue carries `luogo` and `modalita` for it); a `max_tokens` stop, a body that is not JSON
or does not validate is `LlmUnavailable`. Adaptive thinking, effort `medium`,
`max_tokens` 8000, the server-side fallback `default` with its beta header. The
proposal row keeps `model` and the three token counts from `usage`. The seam passes
every schema through the SDK's `transform_schema` before sending it, so the length and
range keywords the API refuses never reach it, and Pydantic enforces them on the way in.

### 3.5 The request, in the admin

«Richieste team» (`/admin/team`), a nav entry between «Match» and «Aziende»: the list,
paginated by cursor like every hub list (company, origin, date, state, how many talents
answered yes of how many asked), and the request page: the description as typed, the
summary in a box the admin can edit («Salva il riassunto»: it is what the talents will
read), the place read, the team with the real names linking to each talent's page,
their own rates and the client bands, the company's contacts, the state, the admin's
note; «Contatta i talenti» (the first time: everyone; then «Rimanda a chi non ha
risposto»: only the silent), the answers per talent with their time, «Segna come
contattata», «Chiudi». The routes under `/api/hub/team/requests...` behind `AdminDep`,
in `routers/admin.py`'s neighbourhood as every admin route is. A request arriving mails
`REBASE_CONTRACTS_MAIL` (ciao@) «Nuova richiesta team da Acme S.r.l.» with the summary
(or the talent's name for a single-card request) and a link to the page; at creation
the summary is also checked against the company's name and a hit is logged; the check
that refuses is the send's (§ 3.6).

### 3.6 The availability mail

`team_availability_mail(to, *, nome, ruolo, riassunto, tariffa, yes_url, no_url)`:
subject «Un progetto per te: sei disponibile?»; the body says rebase has a project the
person fits, the anonymous summary, the role proposed, that the fee would be their own
daily rate as on their card, and the two buttons, «Sono disponibile» and «Non sono
disponibile», each a link to the answer page (§ 3.2) with a one-use token (SHA-256
stored, `token_urlsafe(32)` sent, the magic link's own shape), valid thirty days; then
that rebase will write back with the details. The colours: the palette has no green,
and white on Watermelon fails the contrast floor, which is why every mail button is
`CTA`; the proposal is «Sono disponibile» on `#2b8a3e` (a green whose contrast with
white text is 4.7:1) and «Non sono disponibile» on `CTA`, and `_button` gains a colour
parameter. Ivan reads the wording and picks the colours on the PR before it ships.
`contact_talents` refuses, with «Il riassunto nomina l'azienda: correggilo prima di
scrivere ai talenti.» (a `409`), a summary that contains any word of three letters or
more of the request's `azienda`, case-insensitively.

## 4. The private face

### 4.1 Access

On a company request's page in «Aziende» (`AdminCompanyDetail`), «Apri il talent
cloud» writes a `talent_cloud_grants` row for the request's user and that company (a
live grant of that user for that company already there is answered, not doubled) and mails the referente «Il talent cloud
di rebase è aperto per Acme S.r.l.» with a link to `/hub/me/cloud` (the magic link
flow they already have); «Revoca» closes the user's live grant for that company, and
the cloud stays open while any grant of theirs is live. `MeRead` gains
`talent_cloud: bool`, and the member area's nav gains «Talent cloud» when true. Ivan:
«accesso al cloud privato da azienda esistente».

### 4.2 The cloud

`/hub/me/cloud`, for a signed-in user with a live grant (`403` «Il talent cloud non è
aperto per questo account.» otherwise): the same builder box on top, then the talents
as cards with the name, surname, links, the CV to open, the anonymous description, the
work mode, the vetted badge («Verificato da rebase»), the client band; filters by role
(the distinct `ruolo` values of the cards, sorted), seniority, one skill (a text search
over `competenze`), work mode and price band; sorted vetted first, then by name; at
most 200 cards, the community's size for a while, with no pagination (the hub's cursor
module encodes one time or number key and would need extending for this order; a
later card when the count asks for it). One filter, `cloud_visible`, decides who is in
the cloud for the list, the CV route and «Richiedi» alike: live, not `scartato`, with a
card. A card has «Richiedi»: a request with that one talent, no form, no proposal. The
builder's «Assumi team» files the request with the company's own data (`origine =
cloud`, `user_id`, `company_id`, the user's phone when they have one), no form. What
the cloud never shows: the freelancer's own rate, their state, the admin's notes and
comments. The CV carries whatever the freelancer wrote in it, an email and a phone
usually, and the privacy text says so. `GET /api/hub/me/cloud/talents`,
`GET /api/hub/me/cloud/talents/{id}/cv` (a route of its own, behind the grant and the
filter), `POST /api/hub/me/cloud/proposals` and `POST /api/hub/me/cloud/requests` with
`{proposal_id}` or `{freelancer_id}`.

### 4.3 What the public page says about it

The beta box: «Il team builder è in beta e senza limiti. Le aziende che entrano nel
talent cloud vedono i profili per nome, sfogliano tutto il cloud e chiedono i talenti
direttamente.» and the button to the wizard with `?da=team-builder`. The company
wizard, when the origin is `team-builder`, shows a box above the form: «Stai chiedendo
l'accesso al talent cloud: compila la richiesta e ti ricontattiamo noi.» The origin
lands in `companies.origine` as every origin does, and the «Aziende» list shows «da
team builder» on the row.

### 4.4 What the talents are told

Before the first grant in production, every freelancer with a card gets one mail from
the admin (a campaign, or the outreach recipe until campaigns ship): their anonymous
card exists and what it says, that the public builder shows it without their name, and
that companies rebase admits to the talent cloud see their profile by name with the CV.
Ivan decides whether that mail also offers an opt-out («non mostrarmi alle aziende»),
which would be one flag on the freelancer, one line in the member area and one clause
in `cloud_visible`; this record assumes no opt-out, as he said on the first round
(«non servono ulteriori consensi»), and the question stays open until he reads § 6.

## 5. The Claude seam

`rebase_core/llm.py`: a `LlmCall` protocol, `complete(request: LlmRequest) ->
LlmResponse`, with `LlmRequest(system: list[dict], messages: list[dict], schema: dict,
max_tokens: int)` and `LlmResponse(text: str | None, stop_reason: str, refusal_category:
str | None, model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int)`;
`LlmUnavailable(DomainError)`, mapped to `502` by the API and answered as a sentence by
the MCP server; `AnthropicCall(api_key, model, *, client=None)` implements the protocol
with the official `anthropic` SDK (a pinned dependency of `rebase_core`):
`client.beta.messages.create(model, max_tokens, betas=["server-side-fallback-2026-07-01"],
fallbacks="default", thinking={"type": "adaptive"}, output_config={"effort": "medium",
"format": {"type": "json_schema", "schema": ...}}, inference_geo="eu", system=[...with
cache_control...], messages=[...])`, a request timeout of fifty seconds (under nginx's
sixty), reading the first text block and passing `stop_reason` through;
`RecordingCall(responses)` in the same module answers scripted responses and keeps the
requests, the way `RecordingSender` does for mail. Two callers: the card writer
(§ 5.1) and the engine (§ 3.4). The key is `REBASE_ANTHROPIC_API_KEY`; the model
`REBASE_TEAM_BUILDER_MODEL`, default `claude-opus-5`; the switch
`REBASE_TEAM_BUILDER_ENABLED`, default true; the cap `REBASE_TEAM_BUILDER_CONCURRENCY`,
default 4, a semaphore in the API process: the fifth proposal at once answers `503`
rather than queue on the thread pool the member area and the Documenso webhook share.
The semaphore bounds load, not spend: the public proposals route also sits behind the
wizards' `spend_one` speed bump, and `REBASE_TEAM_BUILDER_DAILY_CAP` (default 300:
proposals a day across both origins, counted on `team_proposals`) answers the same
«Troppe richieste» `503` once reached, so a flood cannot run up the bill; beta stays
unlimited for a person, not for a script.
Without a key, cards are not written and the builder answers `503`; nothing else of the
hub changes.

### 5.1 The card writer

`rebase_core/cards.py`: `CardWriter(session, llm, *, now=utcnow)` with
`write(freelancer_id) -> FreelancerCardRead`, `refresh_stale(limit) -> CardsRefreshed`
and `delete(freelancer_id)`. `write` takes the CV's text (`FreelancerService.cv_text`,
the same page and character ceilings as `read_freelancer_cv`) and `posizione` (never
the rate: the bands are computed in core), asks for the card of § 2.1 through the schema, stores it with the
CV's SHA-256 and the tokens; an empty text (a scanned CV) makes no call and writes
`error` «Il CV non ha testo leggibile.»; a refusal, a `max_tokens` stop or a body that is
not the shape retires a previous card written from another CV (`card` set to null, so
the catalogue never shows a card of a CV that is gone) and writes `error` with the
failed CV's hash, so the same CV is not retried and paid for until it changes; a
provider error leaves the previous card and writes `error` without the hash, so the
next run retries. It runs
after the response, in a session of its own (`SessionOpenerDep`, as the Documenso
webhook's follow-up does), where a CV arrives or changes: the public wizard
(`FreelancerService.apply`) and the member's `replace_cv`; `FreelancerService.clear_cv`
deletes the card in core, so the admin route and the MCP tool both drop it. `rebase
cards-refresh --limit N` writes every freelancer whose CV hash differs from the card's
and from the failed one, `limit` at a time, oldest first, and prints how many were
written and how many failed; it stops at the first provider outage, since those CVs
are not marked failed and the next run retries them, so the backlog is done when a run
prints «0 schede scritte, 0 non riuscite»; the admin's talent page has «Rigenera
scheda», which ignores the failed hash once.

## 6. Privacy, settings, deployment

The site's privacy page has no section about the hub's card; its «Il tuo spazio
PigroCRM» section says «Non trasferiamo nulla a terzi» about the CRM and stays as it
is. A new section, «La tua scheda e il team builder», between «L'iscrizione a rebase»
and «Il tuo spazio PigroCRM», for Ivan's review: the text of the CV is sent to
Anthropic's API, with inference in the European Union, to write an anonymous
description of the profile (role, seniority, skills, sectors, languages), every time the
CV changes or an admin asks for it again; the description a visitor types on the team
builder is sent to the same API, with the anonymous descriptions of every profile, to
propose a team; Anthropic processes the data on rebase's behalf and does not train on
it (a link to Anthropic's privacy page, whose host is added to the site's allowlist of
external links in the same commit); a visitor of the public page sees the description
without the name; a company rebase admits to the talent cloud sees the profile by name
with the CV, links included, and the CV carries what the freelancer wrote in it; how to
ask for the description to be deleted, at the address the page already gives. The
existing talents' mail is § 4.4.

Settings: `REBASE_ANTHROPIC_API_KEY`, `REBASE_TEAM_BUILDER_MODEL`,
`REBASE_TEAM_BUILDER_ENABLED` (compose default `true`), `REBASE_TEAM_BUILDER_CONCURRENCY`,
in `config.py`, `.env.example` and the compose `x-api-environment` list. PostHog events
through the existing `Tracker`: `team_proposta_generata` (origine, persone, tokens),
`team_richiesta_inviata` (origine), `team_talento_risposta` (risposta). Deployment: the
migration on the hub's boot; the key in production's and preview's `.env` before the
tag; the host vhost gets `proxy_read_timeout 90s` on `/api/hub/team/proposals` (a
by-hand change on the host, as the MCP location was); the talents' mail of § 4.4 goes
out, then `rebase cards-refresh` runs once after the deploy, for the backlog. Two rows
in the monorepo's `docs/design/DECISIONS.md`, added with this record: the client price
is the freelancer's rate plus 40%, shown as a band; a CV's text and a visitor's
description go to Anthropic's API and nowhere else.

## 7. Testing

- **Core**: the band arithmetic (every boundary, no rate, 22 days); the catalogue
  rendering (positional ids, no name, no link, no `sintesi`, the vetted flag absent,
  stable order); the engine with a `RecordingCall` (a scripted answer mapped back to
  freelancers, an unknown or repeated id dropped, a refusal, a `max_tokens` stop, a
  non-JSON body and a non-validating body each → `LlmUnavailable`, a local need with an
  empty team, the «Rigenera» turn carrying the previous positions and the note, the
  token counts stored, the public read without ids); the card writer (the hash, the
  refresh of a changed CV only, the failed hash skipped, the empty text, the deletion
  through `clear_cv`); the request (states, the unique proposal, the one-use tokens,
  `si`/`no` recorded once through the post, the second post refused, the thirty-day
  expiry, the mail per talent once, «Rimanda» only to the silent, the send refused on a
  summary that names the company, the edited summary sent); the grants (one live per
  user, a second grant answered, the guard, `cloud_visible` on the list, the CV route
  and «Richiedi»).
- **API**: the public routes and their codes (`503` off, `503` cap, `502`, `422`, `409`,
  `201`), the admin routes behind `AdminDep`, the cloud routes behind the grant, the
  answer post.
- **MCP**: the tools of § 8 in `test_tools.py`.
- **Web**: the public page (examples, the run, the result, «Rigenera», the form, the
  thanks, the beta box), the answer page and its confirm, «Richieste team» and the
  request page with the editable summary, the talent page's card and «Rigenera scheda»,
  «Talenti»'s vetted action and badge, `AdminCompanyDetail`'s grant action and «da team
  builder» on the list, the wizard's box, the member nav and the cloud page with its
  filters and «Richiedi».
- **Preview**: a real generation with the preview's key and its catalogue, a request,
  the mail to a test talent, the confirm on both answers, a grant to a test company,
  the cloud page; the video.

## 8. Over MCP

| Tool | Does |
|---|---|
| `list_team_requests(stato=None, origine=None, limit)`, `get_team_request(id)` | the admin list and page |
| `set_team_request_summary(id, riassunto)` | the editable summary |
| `contact_team_talents(id, only_silent=False)` | «Contatta i talenti» / «Rimanda» |
| `set_team_request_status(id, stato, note=None)` | `contattata`, `chiusa` |
| `propose_team(descrizione, nota=None, previous_id=None)` | the engine, as the admin (`origine` `admin`) |
| `get_freelancer_card(freelancer_id)`, `regenerate_freelancer_card(freelancer_id)` | the card |
| `set_freelancer_vetted(freelancer_id, vetted: bool)` | the flag |
| `grant_talent_cloud(company_id)`, `revoke_talent_cloud(company_id)`, `list_talent_cloud_grants()` | the grants |

`get_talento` and `list_talenti` (REB-282) carry `vetted_at` and whether a card exists.
The words the screens use for states and origins live in core beside `match_words`, so
the tools, the API and the web say the same thing and `test_web_labels.py` holds them.

## 9. Phases

Two milestones of P-REB-43, one draft PR each:

**Describe every talent anonymously and propose a team in public**: the seam, the
settings, the migration, the card writer with its hooks and CLI, the band arithmetic,
the engine, the public routes, the public page, the request table and the admin's
«Richieste team» with the editable summary, the talent page's card and «Rigenera
scheda», the PostHog events for proposals and requests, the privacy section.

**Work a team request with the talents, and open the cloud to a company**: the
availability mail, the answer page and its post, the response event, the vetted flag
and its badge, the grants and their mail, the member's cloud page with its filters and
requests, the wizard's box and «da team builder», the MCP tools, the talents' mail.

The plan, one task per card, is
`projects/hub/docs/superpowers/plans/2026-09-25-team-builder-and-talent-cloud.md`.

## 10. Not here

- A conversation with the model (questions back to the visitor): one shot and
  «Rigenera», by Ivan's word.
- A quota or a captcha on the public page: none in beta; the switch, the cap and the
  token counts are the only guard, and a later card adds a quota when the numbers say
  so.
- Payment for the cloud: sold offline.
- Availability as a field on the card, or a calendar: the mail's two buttons are the
  availability, per request.
- Pagination of the cloud past 200 cards: when the community is that big.
- The static site's button to `/hub/team`: its own card, after the page exists.
- An opt-out from the cloud: open for Ivan (§ 4.4), one small card if he wants it.
- Running the builder on existing company requests, or turning a team request into
  matches automatically: the admin creates matches in «Crea match» by hand.
