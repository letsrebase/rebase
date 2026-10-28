# Talent cards from more sources, written by any of three providers

Status: approved by Lorenzo on 2026-10-27. Card: REB-829. Project: «Write talent cards
from every source with any AI provider» (P-REB-68).

## Why

The anonymous card (`rebase_core/cards.py`, spec 2026-09-25 § 2.1) is what every talent
search reads: `list_talenti`, the team builder, the talent cloud. Today it is one Claude
Opus 5 call over the CV text alone, with the key in the server's `.env`. On 2026-10-27
the first real searches showed cards Lorenzo knows are wrong (a senior read as «junior,
2 years»), because the only source was a CV with no dates on its roles. REB-799 fixed
the rules (year bands, `anni` null when undated, an admin override); this design widens
what the card is written from and who writes it.

Lorenzo's decisions, 2026-10-27:

- A provider-agnostic library that calls each provider **directly**, never through a
  gateway.
- Anthropic, OpenAI and Gemini supported from the start; the existing Anthropic key
  stays the first provider. Keys and models are configurable **by admins in the hub**.
- The card is written from **everything we have**: the CV, GitHub, the other links the
  person gave, and what the hub already knows about them.
- **LinkedIn is out of card generation entirely**: no profile visit, no scraping, no
  LinkedIn PDF export, nothing read from LinkedIn. The `linkedin_url` stays a contact
  and identity field (REB-800 uses it to find duplicates), never an input to the card.

## Goals

1. One seam for every model call in the hub (cards and team builder), provider and model
   chosen per task by an admin.
2. A card written from the CV plus GitHub plus the person's other public links plus the
   hub's own data, with the source of each field recorded.
3. The card stays anonymous with more sources, which bring handles, repository names,
   employers and bios: the guard covers them before any new source is added.
4. Quality and cost are measured before a provider or prompt change goes live, on a
   fixed evaluation set.
5. Only the cards that need it are regenerated, by version, never all of them blindly.

## Non-goals

- LinkedIn in any form (above).
- A gateway, proxy or router service (OpenRouter, LiteLLM proxy, a hosted gateway).
- Automatic provider fallback on failure: one provider per task, chosen by an admin. A
  failure leaves the previous card in place, as today.
- Changing the card's public shape (`Card` in `team_schemas.py`) beyond the provenance
  and version fields below.
- Scraping behind a login, or any source the person did not give us.

## Anti-goals (a failure even if it works)

- A card that lets a reader identify the person (a GitHub handle, a repository name, an
  employer, a personal domain).
- A key that leaves the server in any readable form after it is saved: not in an API
  response, a log, an MCP answer, an error message or a screenshot.
- A spend nobody chose: a change of rules or provider that silently regenerates 100 cards.

## The library: Pydantic AI

AI SDK is TypeScript and the hub is Python; using it would mean a Node service beside
the API. The choice is **Pydantic AI** (`pydantic-ai-slim` with the `anthropic`,
`openai` and `google` extras), checked on its current documentation on 2026-10-27:

- each provider class takes an injected `api_key` (`AnthropicProvider(api_key=...)`,
  `OpenAIProvider(api_key=...)`, `GoogleProvider(api_key=...)`), so keys come from the
  database at call time, never from environment variables;
- each model talks to its provider's own SDK, no intermediary;
- `output_type=NativeOutput(Card)` asks the provider's native structured output against
  a Pydantic model, which `Card` already is;
- usage (input, output, thinking tokens) comes back on the result for cost tracking;
- `pydantic_evals` covers the evaluation set below;
- one `Thinking(effort=...)` setting maps to each provider's own knob (Anthropic
  adaptive thinking with effort, OpenAI `reasoning_effort`, Gemini 3 `thinking_level`),
  so the task's effort level is one admin field for all three.

Checked in the documentation on 2026-10-27: key injection for Anthropic and OpenAI,
native structured output, the thinking mapping for all three. Gemini's key injection
(`GoogleProvider(api_key=...)`) and native output on the exact Gemini model chosen are
verified in step 1 with a live call before Gemini can be switched on.

The seam stays `rebase_core/llm.py`: its `LlmCall` protocol and `RecordingCall` test
double keep their role, with an implementation over Pydantic AI replacing
`AnthropicCall`. `cards.py` and `team_builder.py` keep calling the seam and never import
a provider.

## Configuration in the admin area

A new admin page, «Modelli AI», backed by two tables:

- **`ai_providers`**: one row per provider (`anthropic`, `openai`, `google`): the API
  key encrypted at rest, its last four characters, who saved it and when, enabled or
  not. The key is write-only: the page and every API answer show «configurata, finisce
  con …abcd», never the key. Encryption is AES-256-GCM with a key from the server
  environment (`REBASE_SECRETS_KEY`), the scheme PigroCRM already uses for Google refresh
  tokens (`pigrocrm/core/gmail/crypto.py`); since the hub may not import PigroCRM, the
  small helper moves to `shared/` or is rewritten in the hub (the builder's call).
- **`ai_tasks`**: one row per task (`card`, `team_proposal`): provider, model name (free
  text with the provider's known models suggested), effort or reasoning level where the
  provider has one, max output tokens, a daily spend cap in euros, enabled or not.

Every change to either table is an `AdminAction` (who, when, before and after; the key
itself never stored in the action, only «chiave sostituita»). A «Prova» button sends one
fixed, non-personal prompt and shows the answer, the latency and the cost, so an admin
knows a key works before a real CV goes out.

Migration: the current `REBASE_ANTHROPIC_API_KEY` and `REBASE_TEAM_BUILDER_MODEL` seed
the two tables once; afterwards the database is the source and the variables are
removed from `config.py`, `.env.example` and the compose files.

MCP: `list_ai_config` (read-only, no keys) and nothing that writes keys: a key is typed
by a person in the admin page only.

## Sources

Each source is fetched, normalised to text with a clear header, and stored per person
with the time it was read and a hash, so a card can say what it was written from and a
source change marks the card stale.

| Source | How | Notes |
|---|---|---|
| CV | as today (`cv_text.py`) | primary for dates and roles |
| Wizard answers | from the row | position, day rate, remote option: context, never overriding the CV's dates |
| GitHub | official REST API with a hub token (`REBASE_GITHUB_TOKEN`, optional, raises the rate limit) | for the user in the person's links: public profile bio, public repositories they own (name, description, languages, stars, last push), language totals, contribution recency. Forks and archived repos down-weighted |
| Other links | plain HTTPS GET, no browser | personal site, portfolio, Behance, Dribbble, Hugging Face, Kaggle, Stack Overflow, GitLab, Medium, talk pages: page text only, size and time limits, no redirects to private addresses, `robots.txt` respected |
| Hub data | from the database | the admin's seniority and years override (REB-799) as hard facts; past matches on the hub (role, months, completed or not) as evidence of real work; never admin notes or comments verbatim |

Not sources: LinkedIn (Lorenzo's decision), anything behind a login, search engines, data
brokers, anything the person did not put in their own profile.

A headless browser is **not** needed: every source above is an API or a static page.
A page that only renders with JavaScript is skipped and recorded as unreadable.

## Anonymity with more sources

The new sources carry exactly what the card must not: handles, repository names,
employers, personal domains, photos' alt text, bios in the first person. Before any
source is added:

- The prompt states the source list and that none of their identifiers may reach the
  card; repositories are described by what they do («una libreria Python per …»), never
  by name.
- The guard in `cards.py` (`_identifying`) extends to: the GitHub login, every
  repository name the person owns, every hostname of their links, and every employer
  name the CV states (extracted by the same call into a private field that is never
  stored on the card, or by a separate pass: the builder's call). A hit asks for one
  rewrite, then fails the card, as the gender guard does since REB-802.
- Every source's text is wrapped as data, not instructions, as the CV is today.

## Provenance, versions and regeneration

- Each card stores, per field, which sources supported it (`fonti`: `cv`, `github`,
  `link:<n>`, `hub`), the provider and model, the prompt version and the source hashes.
  The admin card view shows them; the public and cloud reads do not.
- When CV and another source disagree (years, role), the card follows the CV's dates
  and records the conflict for the admin view rather than averaging.
- `PROMPT_VERSION` is a constant in `cards.py`. A card is stale when its CV hash, any
  source hash or its prompt version differs. The hourly loop (`cards-refresh`) only
  regenerates cards stale by **CV or source hash**, as today; cards stale only by prompt
  version are listed in the admin area with the estimated cost, and an admin starts
  their regeneration. No version bump ever spends on its own.

*Added 2026-10-28 (REB-853, Lorenzo).* The first regeneration of 87 cards took about 18
minutes, one card at a time, about 11 seconds each, almost all of it waiting on the model.
Two changes, neither touching what a card is:

- **Parallel.** `cards-refresh` and an admin's regeneration write with a small pool of
  workers (`REBASE_CARDS_WORKERS`, default 5), each on a session of its own. A person is
  claimed by an advisory lock for the whole write, and a regeneration re-checks that the card
  is still stale under that claim, so no card is paid for twice. The first outage, spent cap
  or missing model stops the pool and leaves the rest as it was.
- **Bulk goes through the Message Batches API.** From `REBASE_CARDS_BATCH_THRESHOLD` cards (10)
  a regeneration is one Anthropic Message Batch: half the price of a synchronous call and
  answered within the hour, which suits work nobody is waiting for. Each request is built by
  the code that builds the synchronous one (same prompt, answer shape, model, effort and
  output ceiling), less the server-side fallback and per-request timeout a batch refuses.
  The batch and a row per card (the CV's hash and the inputs' hashes, never text) are kept
  before the provider is called, the `cards` service polls it, and each result is applied
  through the same draft parsing, guards, single synchronous rewrite and save as a
  synchronous write, so a batch can never produce a card a synchronous write would have
  refused. A result is applied only to the card it was asked for (a changed CV or input
  drops it, paid for, and leaves the card stale), a refusal is asked again synchronously
  (the fallback is not available to a batch), and a request the provider did not answer
  leaves its card stale. Usage is recorded at the batch price (50%), flagged `batch`. One
  batch is active at a time, a single «Rigenera scheda» stays synchronous, and any provider
  but Anthropic falls back to the workers.

## Evaluation before a change goes live

A fixed sample of about 15 real profiles, chosen to cover the failures seen (undated CV,
duplicate person, GitHub-heavy profile, designer with a portfolio, very senior lead),
stored by id in the hub's database, not in the repository (what a run writes holds
personal data). A command (`rebase cards-eval`) writes the cards of the sample with a
provider, model, effort and prompt version, then cross-checks each against the sources it
was written from, with no admin step: rules where one exists (the schema and the guards,
anonymity, the years recomputed from the CV's dated periods when the text allows a
reliable parse) and a separate judging call that marks each field supported, unsupported
or contradicted against the CV, GitHub, the links and the hub's facts, and flags what was
invented. It reports supported fields, contradictions, guard failures, identifiers left in
the card, latency and cost (writing and judging apart) and the comparison with a baseline
run. A provider or prompt change goes live after its numbers are read.

*Changed 2026-10-27 (REB-842, Lorenzo).* The first version of this section had an admin
write each expected card by hand and rate the role, skills and summary of every run; it
was built (REB-835, REB-836) and removed the same day, with its page, its routes and the
expected cards it stored.

## Cost

Every call records provider, model, task, input, output and thinking tokens and the cost
in euros from a price table kept in the hub (per provider and model, editable by admins
on the same page, because prices change). The task's daily cap stops calls for the day
when reached, with the reason shown in the admin area. The «Modelli AI» page shows the
month's spend per task and provider.

## Privacy and legal (Lorenzo and Ivan's decision)

Two separate checks, each gating one rollout step:

- **New data categories (before step 3).** GitHub data and the text of third-party
  pages are personal data the freelancer did not hand us directly, even if they linked
  it. The privacy notice they accepted must cover reading the links they gave to write
  the card; if it does not, the notice is updated and the people already registered are
  told before step 3 starts.
- **New processors (before step 4).** Sending CVs and profile text to OpenAI and Google
  adds two processors. The notice lists them, a data processing agreement exists with
  each, and the provider's no-training and retention terms are checked on the account
  used. Until then only Anthropic is enabled; the code supports all three.

## Rollout

1. Seam on Pydantic AI with Anthropic only, configuration tables seeded from the
   current env, «Modelli AI» page, cost recording. No behaviour change.
2. Evaluation set and command; baseline numbers for the current prompt.
3. GitHub and links fetchers, provenance, extended guard, prompt version 2; evaluate;
   regenerate on an admin's start. After the new-categories check.
4. OpenAI and Gemini enabled after the processors check; evaluate each.

## Left to the builder

- Where the AES helper lives (`shared/` or a hub copy).
- How employer names reach the guard without being stored.
- Exact table and column names, the price table's shape, the admin page layout (an
  existing admin page's pattern, no new UI primitive).
- Fetch limits (bytes, timeouts, how many links per person).

## Lorenzo's answers (2026-10-27)

- Privacy: Lorenzo and Ivan update the privacy notice to cover reading the links people
  give and the new processors. Steps 3 and 4 are built but stay switched off in
  production until they confirm the notice is updated; each source and each provider has
  its own on/off switch on the «Modelli AI» page, off by default.
- Evaluation set: the builder drafts the 15 expected cards from each CV and its links;
  an admin corrects them before they become the reference. Superseded the same day by
  REB-842 (Lorenzo): no expected card, the evaluation is automatic.
- Status: approved.

## Calls made after the builder check (mine, for Lorenzo to check)

- **Years come only from dates.** `anni` is summed from dated professional periods in
  the CV and from the hub's own completed matches (dated, real work). GitHub, links and
  the wizard never set `anni`: an account's age or a repository's first commit is not
  professional experience. They may inform the seniority reading when `anni` is null,
  which is then still shown as uncertain (REB-799). The admin override stays the way to
  fix a card a CV cannot fix.
- **Provenance is the model's claim, checked by code.** The model returns, beside the
  card, the sources it used for each field in a private field that is never part of
  `Card` and never public. Code keeps only sources that exist for that person and stores
  the result on the card row (`fonti`), shown in the admin view as «secondo il
  modello». Conflicts (CV years against hub matches) are computed by code, not asked.
- **Source hashes ignore what moves on its own.** A source's hash covers its normalised
  content minus volatile fields (stars, last push, contribution counts, page dates). Sources
  are refetched when a card is written and once a week; only a change of the hashed
  content makes a card stale. The weekly refetch never calls a model by itself.
- **No client names reach the model.** Hub matches are passed as role, months and
  outcome, never the client's name. The guard also keeps the person's name and surname
  (as today), the GitHub login, every repository name they own, every link hostname and
  every employer the CV names.
- **Keys.** The app refuses to save or use a provider key when `REBASE_SECRETS_KEY` is
  missing, and says so on the «Modelli AI» page; it never falls back to plain text. The
  one-time seed from the old environment variables is a command, not a migration. A
  rotation command re-encrypts every stored key under a new secrets key.
- **Fetched text is deleted with its link.** Removing a link deletes its stored text at
  the next refetch; deleting a person (or an erasure request) deletes all their source
  text at once, like the CV.
- **Evaluation metric.** Superseded by REB-842, see «Evaluation before a change goes live»:
  the checks are automatic and nobody rates a card.
