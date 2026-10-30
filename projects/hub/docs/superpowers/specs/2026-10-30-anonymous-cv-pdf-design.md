# A talent's anonymous CV, as a PDF, from the admin page

Status: approved by Ivan on 2026-10-30 in chat. Card: REB-862.

## Why

Ivan proposes talents to companies. The only anonymous thing the hub has today is the
«Scheda anonima» on the talent's admin page (`SchedaAnonima.tsx`, spec 2026-09-25 § 2.1):
a few schematic rows (role, seniority, years, skills, sectors, languages, a summary) plus
two rows that are for the admin only («Fascia cliente», «Scritta»). It is what the team
builder reads, not something to send. Ivan's words: «sono poche info schematiche, deve
essere più CV». What he wants to send is a real CV, shaped like the ATS-style example he
gave (in his Downloads, `ATS_*_CV.pdf`, not in the repository: it carries a real person's
data): a summary, every role with company, place, dates and bullet points, skills grouped
by area, education, certifications, languages. Two pages.

## What Ivan decided (2026-10-30, in chat)

- A download from the talent's admin page (`/hub/admin/freelance/<id>`), a PDF.
- **Only the identity goes.** Name and surname, and the person's own links. Companies,
  clients, schools, cities and dates stay exactly as the CV has them: «quelle vanno bene».
- No «Fascia cliente», nothing about who wrote it (model, sources, date).
- The content is the person's CV, made into a clean, uniform CV: reordered and tidied,
  never invented.

## Goals

1. One button, «Scarica CV anonimo», on the talent's admin page, that downloads a PDF CV
   in rebase's style for any talent with a CV on file.
2. The PDF carries every role, every education entry, every certification and the
   languages of the CV, in the CV's own words and language.
3. No first name, surname, email, phone, postal address, date of birth or link of the
   person anywhere in the PDF. A text that still has one is never rendered.
4. Same CV, same PDF: the text is written once per CV file and reused until the CV
   changes, so a second download is instant, free and identical.

## Non-goals

- Anonymising employers, clients, schools, places or dates (Ivan, above).
- Editing the CV's text by hand before the download, or a «Rigenera» for it (v1: a new
  CV, or a prompt version bump, writes it again).
- An MCP tool, a member-area or cloud download, a company-side view, sending it by mail.
- Translating the CV. It stays in the language it was written in.
- Changing the anonymous card, its prompt, its guard or anything that reads it.

## Anti-goals (a failure even if it works)

- A PDF that carries the person's name, a contact or a personal link: the client could
  then reach the person without rebase.
- A PDF that says something the CV does not: an invented role, a date, a certification,
  a level.
- Rate, band or the admin's notes in the PDF.
- A spend nobody chose: nothing is written until an admin presses the button.

## The shape: `AnonymousCv`

One Pydantic model in a new package `rebase_core/anonymous_cv/` (`schema.py`, `guard.py`,
`writer.py`, `render.py` and the template `cv.typ`), the model's native
structured output (strict: every field required, nullable by a union, `extra="forbid"`,
the shape every provider in `llm.py` takes). Field names in Italian, like `Card`.

```
AnonymousCv
  lingua: "it" | "en"            the language the headings are printed in (below)
  titolo: str                    the professional headline, e.g. «Oracle DBA e Security Engineer»
  sommario: str                  the CV's own summary; when it has none, 2-3 sentences made
                                 only of facts the CV states
  esperienze: list[Esperienza]   newest first, every role the CV lists
    ruolo: str
    azienda: str | None          as written, legal form included
    luogo: str | None            «Rome, IT», «Remote»
    periodo: str | None          as written: «July 2022 – July 2024», «2019 – oggi»
    punti: list[Punto]
      titolo: str | None         a bold lead, for a CV that groups engagements under one
                                 role: «GRC Analyst (TIM S.p.A.)»
      testo: str
  competenze: list[Gruppo]       the CV's own grouping; one group «Competenze» when it has none
    area: str
    voci: list[str]
  formazione: list[Titolo]
    titolo: str                  «BSc in Computer Science»
    istituto: str | None
    luogo: str | None
    periodo: str | None
  certificazioni: list[str]
  lingue: list[Lingua]
    lingua: str
    livello: str | None          as written: «Native», «C2», «buono»
  altre_sezioni: list[Sezione]   projects, publications, talks, volunteering: anything else
    titolo: str                  the CV has that a CV reader would want, in its own words
    voci: list[str]
```

The model answers `AnonymousCvDraft`: `cv: AnonymousCv` plus a private `identita`
(`nome`, `cognome`, `email`, `telefono`, `link: list[str]`, each only as the CV states it,
`None` or empty otherwise). `identita` is the guard's input and nothing else: it is never
stored, logged, returned or printed. It makes the guard independent of the card: a talent
whose card was never written, was written before prompt version 3 or from another CV still
gets the CV's own spelling of the name and the CV's contacts checked.

Every list and string has a generous `max_length` (the card's style), so a model that
writes a novel is a shape failure, not a stored one. `lingua` is `it` when the CV is in
Italian and `en` for any other language: it only chooses the printed headings
(«Esperienze» / «Experience», …); the text itself is the CV's.

## Writing it: a new AI task, `cv_anonimo`

A call of its own, never part of the card's call: the card's prompt, version and guard
are Lorenzo's and stay as they are, and a CV is written only when an admin asks for it.

- **The task.** `AI_TASKS` gains `cv_anonimo`, configured in «Modelli AI» like the other
  two (provider, model, effort, output ceiling, daily cap) and recorded in `ai_usage`
  with `freelancer_id`. Migration 0044 widens `ck_ai_tasks_task` and `ck_ai_usage_task`
  and seeds the row from the `card` row as it stands (same provider, model and effort),
  with `max_output_tokens` 16000 (a two-page CV is about 3-5k tokens of JSON; the card's
  ceiling is far too small) and no daily cap. `ModelliAi.tsx` labels it «CV anonimo».
  `rebase ai-seed` and the «Prova» button treat it like the others. `models.py` changes
  with the migration: `AI_TASKS` and the two `CheckConstraint`s that repeat it.
- **Input: the CV's text alone** (`cv_text.extract_text`), not GitHub, links or the hub's
  data. The CV is what is reproduced; nothing else may add to it.
- **The prompt** (Italian, like the card's; `CV_PROMPT_VERSION = 1`): reproduce this CV
  faithfully in the shape, every role and entry, the CV's own words and language; reorder
  and tidy (consistent dates, bullets, grouping), never add a fact, a level or a skill;
  leave out the person's first name and surname everywhere (write around them, never a
  placeholder like «[nome]»), their email, phone, postal address, date and place of birth,
  nationality, marital status, photo caption, the privacy-consent line, the partita IVA,
  codice fiscale, PEC, IBAN and any document number, and **every link,
  URL, handle and username** (LinkedIn, GitHub, a personal site, a portfolio, a company
  site too: Ivan, «eventuali link specifici»). Company, client, school and city names stay.
  The CV text goes in the card's inert block (`cards._block`), so text inside the CV is
  read as data, not as instructions.
- **The guard** (`anonymous_cv/guard.py`, `identifying_fields`, reusing `card_guard`'s
  readers): every string of the `cv` is checked, against the union of three readings of
  who the person is (the profile: `users.nome`, `cognome`, `email`, `telefono` and its
  links; the card's stored persona when `cards.stored_persona` answers one; the call's own
  `identita`), for
  - the first name and surname, as `card_guard._names` reads a name (a whole word not
    written all lower case: «Rossi» and «ROSSI» are the person, «rossi» is not);
  - the email addresses, and the phone numbers by their digits
    (`card_draft.phone_digits`, so another spacing or a `+39` still matches);
  - any address at all (`card_guard._ADDRESS`: a scheme, `www.`, `@`);
  - the GitHub login and the hostnames of the person's links (`sources.github_login_of`,
    `sources.link_hostnames`);
  - a tax identifier, in every string, a place included: a codice fiscale or an Italian IBAN
    by its shape, any IBAN after the word «IBAN», eleven digits a few characters after a
    label («P.IVA», «partita IVA», «VAT», «IVA», «C.F.», «CF», «codice fiscale»), and the
    person's own partita IVA and codice fiscale however spaced (`freelancer_fiscal`, which
    also gives the PEC, read as an email). Eleven digits with no label that are not the
    person's pass: an order number has them too.

  Employers, repositories and clients are **not** checked, unlike the card's guard. It
  answers the names of the offending fields, never the matched word, like `identifying`.
  A hit is asked for **one rewrite** (the card's `rewrite_request` pattern: the answer as
  the assistant turn, the fields to fix as the next user turn); a second hit fails and no
  PDF is served. Nothing is cached, so the admin may press again and a new call may pass;
  there is no override that prints a text the guard refused.
- **Failures** are one sentence of ours, never the model's words: no CV, no readable text
  (`cards.NO_TEXT`), refusal, cut at the ceiling, not the shape, still identifying after
  the rewrite, the provider down, the task off or capped (the seam's own sentences). A
  failure stores nothing and is not cached: the next press asks again. (The card caches
  failures by hash because it is written automatically; this one is only ever written by a
  press, so a retry is the admin's choice.)

## Keeping it: `anonymous_cvs`

A table of its own, migration 0044 (with the task above):

```
anonymous_cvs
  freelancer_id  PK, FK freelancers.id ON DELETE CASCADE
  cv_sha256      the CV file it was written from: the hex SHA-256 of `freelancers.cv_bytes`,
                 the value `cards.stored_persona` computes
  prompt_version
  body           JSONB, the AnonymousCv as validated
  provider, model, input_tokens, output_tokens, thinking_tokens
  generated_at
```

A download reads the row; when its `cv_sha256` is the CV on file's and its
`prompt_version` is the current one, the body is reused and no call is made. Otherwise
the CV is written again and the row replaced. The write follows the card's discipline
(`cards.py` module docstring): no transaction held across the model call, and the answer
stored only after re-reading, under the freelancer's row lock, that the CV is still the one
it came from (a CV replaced meanwhile gets nothing stored, and the PDF is still not
served: the admin presses again). `FreelancerService.clear_cv` deletes the row in the same
transaction as the CV, beside the card's `delete`. `PersonMergeService` leaves it in
place on purpose (`LEFT_IN_PLACE`: it is keyed by the CV's hash, so a survivor whose CV
changed writes it again on the next download; when a merge swaps the CVs the hash no
longer matches the survivor's row, and a body is only ever served for the exact bytes it
was written from), so
`test_every_foreign_key_to_a_person_is_moved_or_left_on_purpose` passes. No public,
cloud, team-builder, member or MCP read touches the table.

## The PDF

Typst, through the contracts' toolchain and brand (`contracts/brand.py`: the palette,
Outfit at 300 and 500, the echo logo, `fonts_dir`), but **without pandoc**: the body is
written as a JSON file in the call's own directory and a new template,
`rebase_core/anonymous_cv/cv.typ`, reads it with `json(...)`. Every string reaches the page
as text, never as markup, so nothing in a CV can inject Typst. `--creation-timestamp 0`,
as the contracts, so the same body gives the same bytes. A `Renderer`-style protocol with
a fake, so the API tests need no binary.

The page, A4, the example CV's order and density, in rebase's colours:

- **Head**: the `titolo` large where the name was; one line under it with the seniority
  and years (the card's, with the admin's correction over it, `card_level.card_read`;
  left out when there is no card or the card was written from another CV file than the
  one on file, and the seniority alone left out when the card marks it uncertain and no
  admin set one), the city when that same card's `luogo` has one, and the work
  mode from the profile (`Freelancer.remoto`, printed «Da remoto», «Ibrido», «In sede»:
  the web's `REMOTO_LABELS`, which Python has no copy of, so `render.py` defines the three).
  The echo logo at the top right.
- **Sections**, headings in `lingua`, each left out when empty: Profilo / Profile summary,
  Esperienze / Experience (role bold, period right-aligned; company italic, place right;
  bullets, with the bold lead when `titolo` is set), Competenze / Skills (one line per
  area, the area bold), Formazione / Education, Certificazioni / Certifications, Lingue /
  Languages, then each of `altre_sezioni`.
- **Footer** on every page: «Profilo proposto da rebase · ciao@letsrebase.com ·
  letsrebase.com» and «Rif. <last 8 characters of the freelancer id>», so the company can
  name the profile when it answers. Page numbers. The last, not the first: the id is a
  UUIDv7, whose first characters are the moment the card was made, the same for talents
  created together and telling when the person signed up; the tail is random.
- No rate, band, notes, model, date of writing or source anywhere.

The filename is `rebase-cv-<titolo as an ASCII slug>-<ref>.pdf` (through
`downloads.pdf_response`, so the one header-safety rule holds).

## API and page

- `POST /api/hub/freelancers/{id}/cv-anonimo` (admin only; POST because it may call a
  model and spend): answers the PDF as an attachment. It may take as long as a «Rigenera
  scheda». Errors as sentences, in the admin API's own shapes: no CV (404 with «Nessun CV
  caricato»), the task off or no key (503, as `TeamBuilderOff`), a writing failure (422
  with the failure sentence), cap reached (the seam's error, as the card route answers it).
- Each successful download writes an `AdminAction` on the freelancer,
  `anon_cv_downloaded` (added to `ADMIN_ACTION_KINDS`; no `CHECK` holds that list),
  with `{"riusato": bool}`: a CV that leaves the hub for a
  company is in the person's trail with who sent it out and when. The trail's labels
  (`AuditTrail.tsx`) say «CV anonimo scaricato».
- `SchedaAnonima.tsx`: «Scarica CV anonimo» beside «Rigenera scheda», shown when the talent
  has a CV. While it works the label reads «Preparo il CV…» and the button is disabled;
  the PDF is fetched as a blob and saved with the server's filename; a failure shows the
  API's sentence under the heading, like the regenerate error.

## Tests (what proves it)

- Core: the guard catches the name (both spellings), email, phone in another format, a
  URL, `www.`, a handle, a link hostname, in any field including a bullet's `titolo`, and
  lets an employer, a city and a school through; a hit is rewritten once and a second hit
  fails; reuse on the same hash and version makes no call; a new CV or a version bump
  writes again; a CV replaced during the call stores nothing; `clear_cv` deletes the row;
  the merge test.
- Render: with `typst` on the machine (as `test_contract_pdf.py`), a fixture body renders,
  the PDF text has every role and no field of the fixture's identity; a string with Typst
  markup (`#`, `*`, `$`, `[`) prints literally.
- API: admin only (401/403), no CV, the task off, a guard failure, the happy path answers
  `application/pdf` with the filename, the second download reuses, the audit entry.
- Web: the button shows only with a CV, the pending label, the download call, the error.
- On the preview: a real talent's PDF, read page by page against their CV.

## Left to the builder

The exact prompt wording, the exact `max_length` values, the Typst layout details
(sizes, spacing, rules, how a long skills list wraps) within the brand, and where in
`cards.py`/`card_guard.py` a helper is shared rather than copied.

## Records to update in the same PR

`docs/design/DECISIONS.md` (the new task and table, why a separate call, why employers
stay), `projects/hub/AGENTS.md` (the task list, the table, the route), and the Linear card.
Migration numbers collide when two open PRs take the next one (REB-861 is open on the
cards): refetch `main` before the PR and renumber if 0044 is taken.
