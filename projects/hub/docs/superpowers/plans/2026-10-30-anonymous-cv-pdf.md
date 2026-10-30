# Anonymous CV PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** «Scarica CV anonimo» on a talent's admin page downloads a two-page PDF CV in rebase's style, written by a model from the CV on file, with no name, contact or link of the person.

**Architecture:** A new AI task `cv_anonimo` writes an `AnonymousCv` (structured output) from the CV text; a guard refuses any identity left in it (one rewrite); the body is kept in `anonymous_cvs` keyed by the CV's SHA-256 and reused; Typst prints it from a JSON file with the contracts' brand; an admin POST route answers the PDF and records an audit entry; the web page fetches it as a blob.

**Tech Stack:** Python 3.14, SQLAlchemy 2, Alembic, FastAPI, Pydantic AI seam (`rebase_core.llm`), Typst; React + TanStack Query + vitest.

**Spec:** `projects/hub/docs/superpowers/specs/2026-10-30-anonymous-cv-pdf-design.md`

## Global Constraints

- Repo commands run from the repository root: `uv run pytest -q <paths>`, `uv run ruff check projects/hub`, `uv run mypy`, `pnpm --filter hub test`, `pnpm --filter hub lint`.
- Tests that need Docker: run with `TESTCONTAINERS_RYUK_DISABLED=true`, in the foreground. Never `git stash`.
- Commits: `type(scope): subject`, scope one of `core`, `api`, `web`, `hub`; **no `Co-Authored-By` trailer**; end every body with `Refs REB-862.`
- Never edit `.claude/skills/`. Never touch `projects/pigrocrm`.
- Comment density and voice: match `cards.py` and `card_guard.py` (docstrings that say why, in plain English; Italian for every user-facing sentence).
- No log line, error, usage record or audit entry may carry CV text, the model's answer or an identifier of the person.
- Task name `cv_anonimo`; table `anonymous_cvs`; audit kind `anon_cv_downloaded` with `{"riusato": bool}`; route `POST /api/hub/freelancers/{freelancer_id}/cv-anonimo`; `CV_PROMPT_VERSION = 1`; the seeded task row: provider, model, effort copied from `card`, `max_output_tokens` 16000, `daily_cap_eur` NULL, `enabled` as `card`'s.
- Footer copy, verbatim: `Profilo proposto da rebase · ciao@letsrebase.com · letsrebase.com` and `Rif. <last 8 characters of the freelancer id>` (the UUIDv7's random tail; its first 8 are a timestamp, by the final review's ruling).
- Work modes printed: `remoto` → «Da remoto», `ibrido` → «Ibrido», `in_sede` → «In sede».
- Filename: `rebase-cv-<slug of titolo, ASCII, lower case, hyphens, at most 40 chars>-<ref>.pdf`.

## Review Focus

1. A CV whose surname is also a city or a company the person worked at (Messina, «Rossi S.r.l.»): the city in a `luogo` must pass, the company carrying the surname must be refused. Test in Task 2.
2. A name written in capitals in the CV header («MARIO ROSSI») or as a handle («mrossi», the email's local part): refused. Test in Task 2.
3. A phone written another way than the profile («+39 333 123 4567» vs «3331234567») inside a bullet: refused. Test in Task 2.
4. CV text that looks like Typst markup (`#set page(...)`, `*bold*`, `$x$`, `@ref`, `<label>`, `//`): printed literally, the build does not fail. Test in Task 4.
5. A CV replaced between the press and the model's answer: nothing stored, the admin gets a sentence, not the old person's PDF. Test in Task 3.

---

### Task 1: The task and the table (migration 0044)

**Files:**
- Create: `projects/hub/packages/core/migrations/versions/0044_anonymous_cvs.py`
- Modify: `projects/hub/packages/core/src/rebase_core/models.py` (`AI_TASKS`, `ck_ai_tasks_task`, `ck_ai_usage_task`, `ADMIN_ACTION_KINDS`, new `AnonymousCvRow`)
- Modify: `projects/hub/packages/core/src/rebase_core/llm.py` (`call_for_task` docstring names the three tasks)
- Modify: `projects/hub/packages/core/src/rebase_core/merge.py` (`LEFT_IN_PLACE`)
- Test: `projects/hub/packages/core/tests/test_migration_0044_anonymous_cvs.py`

**Interfaces:**
- Produces: `models.AnonymousCvRow` (`__tablename__ = "anonymous_cvs"`): `freelancer_id: UUID` PK FK `freelancers.id` `ondelete="CASCADE"`; `cv_sha256: str` (String(64), not null); `prompt_version: int` (not null); `body: dict` (JSONB, not null); `provider: str` (String(20)); `model: str` (String(CARD_MODEL_MAX_LENGTH)); `input_tokens`, `output_tokens`, `thinking_tokens: int` (not null, default 0); `generated_at: datetime` (tz, not null). `AI_TASKS == ("card", "team_proposal", "cv_anonimo")`. `"anon_cv_downloaded" in ADMIN_ACTION_KINDS`.

- [ ] **Step 1: Write the failing migration test**, modelled on `test_migration_0043_card_persona.py`:
  - `test_revision_follows_the_card_persona`: parent of `"0044"` is `"0043"`.
  - `test_upgrade_creates_the_table_and_the_task`: after upgrade, `anonymous_cvs` exists with the columns above; `ai_tasks` has a `cv_anonimo` row whose `provider`, `model`, `effort`, `enabled` equal the `card` row's, `max_output_tokens == 16000`, `daily_cap_eur IS NULL`; inserting an `ai_usage` row with `task='cv_anonimo'` succeeds; `task='altro'` still violates `ck_ai_usage_task`.
  - `test_upgrade_without_a_card_row_seeds_anthropic_defaults`: on a database where the `card` row is missing, the `cv_anonimo` row is still inserted (provider `anthropic`, the model the 0036 seed uses, enabled false).
  - `test_metadata_matches_the_schema`: `compare_metadata` is empty after upgrade.
  - `test_downgrade_drops_table_and_row`: downgrade to 0043 removes the table and the `cv_anonimo` rows of `ai_tasks` and `ai_usage`, and restores both CHECKs.
- [ ] **Step 2: Run it**: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/hub/packages/core/tests/test_migration_0044_anonymous_cvs.py` → FAIL (no revision 0044).
- [ ] **Step 3: Write the migration and the model changes.** Read 0036's seed for the default model name. In `merge.py` add `"anonymous_cvs.freelancer_id"` to `LEFT_IN_PLACE` with the reason «keyed by the CV's SHA-256: a moved CV no longer matches and is written again on the next download».
- [ ] **Step 4: Run** the new test, `test_merge.py` and `test_ai_config.py` → PASS.
- [ ] **Step 5: Commit** `feat(core): add the cv_anonimo task and the anonymous_cvs table`.

### Task 2: The shape and the guard

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/__init__.py` (module docstring only: what the package is, spec path)
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/schema.py`
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/guard.py`
- Test: `projects/hub/packages/core/tests/test_anonymous_cv_guard.py`

**Interfaces:**
- Produces (`schema.py`): Pydantic models, all `extra="forbid"`, every field required, nullable by `| None`, strings `SafeStr` with `max_length`, lists with `max_length`: `Punto(titolo: str | None, testo: str)`, `Esperienza(ruolo, azienda | None, luogo | None, periodo | None, punti: list[Punto])`, `Gruppo(area, voci: list[str])`, `Titolo(titolo, istituto | None, luogo | None, periodo | None)`, `Lingua(lingua, livello | None)`, `Sezione(titolo, voci: list[str])`, `AnonymousCv(lingua: Literal["it", "en"], titolo, sommario, esperienze, competenze: list[Gruppo], formazione: list[Titolo], certificazioni: list[str], lingue: list[Lingua], altre_sezioni: list[Sezione])`, `Identita(nome | None, cognome | None, email | None, telefono | None, link: list[str])`, `AnonymousCvDraft(cv: AnonymousCv, identita: Identita)`. And `def strings(cv: AnonymousCv) -> list[tuple[str, str]]`: every string of the CV as `(path, value)`, paths like `"titolo"`, `"esperienze[0].punti[2].testo"`, `"competenze[1].voci[0]"`, in document order.
- Produces (`guard.py`): `@dataclass(frozen=True) class Who(nomi: list[str], cognomi: list[str], emails: list[str], phones: list[str], github_login: str | None, hostnames: list[str])` with `def merged(self, other: "Who") -> "Who"`; `def who_of(*, nome: str | None, cognome: str | None, emails: Iterable[str | None], phones: Iterable[str | None], links: Iterable[str | None]) -> Who` (digits via `card_draft.phone_digits`, login via `sources.github_login_of`, hosts via `sources.link_hostnames`, emails lower case); `def identifying_fields(cv: AnonymousCv, who: Who) -> list[str]` answering offending paths, each once, in document order, never the matched word.

Rules for `identifying_fields` (reuse `card_guard`'s private readers `_names`, `_without_skills`, `_tokens`, `_phrase`, `_contains`, `_has_phone`, `_ADDRESS`, `PHONE_KEY_DIGITS` by import; do not copy them):
- **Every** string: `_ADDRESS`; email addresses whole; the email's local part and the GitHub login as phrases (`_phrase`, `_contains` over `_tokens`); each hostname as a whole host only (`_phrase(host)`, not its labels: an employer's name must pass); phones by their last `PHONE_KEY_DIGITS` digits through `_has_phone` (numbers of 7+ digits only).
- **Surname** (each of `cognomi`, `_names`): every string except `luogo` fields and `lingue[*]`, after `_without_skills` with every `competenze[*].voci[*]`.
- **First name** (each of `nomi`, plus each word of 3+ letters of a multi-word one): only `titolo`, `sommario`, `esperienze[*].ruolo`, `esperienze[*].punti[*].titolo|testo`, `altre_sezioni[*].voci[*]`, after `_without_skills`.

- [ ] **Step 1: Write the failing tests** (a module-level fixture `CV: AnonymousCv` with two roles, one with a bold-lead bullet, skills, education, certifications, languages; `WHO = who_of(nome="Mario", cognome="Messina", emails=["mario.messina@proton.me"], phones=["+39 333 123 4567"], links=["https://github.com/mmessina", "https://mariomessina.dev", "https://www.linkedin.com/in/mario-messina"])`):
  - `test_a_clean_cv_passes`: `identifying_fields(CV, WHO) == []`, with `esperienze[0].luogo == "Messina, IT"`, an employer «TIM S.p.A.», a school «I.I.S. Ettore Majorana» in it.
  - `test_surname_in_a_bullet`: `"Messina"` in `esperienze[1].punti[0].testo` → `["esperienze[1].punti[0].testo"]`.
  - `test_surname_in_capitals_in_the_title`: `titolo = "MARIO MESSINA – Backend developer"` → contains `"titolo"`.
  - `test_surname_in_a_company`: `esperienze[0].azienda = "Messina Consulting S.r.l."` → `["esperienze[0].azienda"]`.
  - `test_lowercase_word_is_not_the_surname`: surname «Porta», bullet «porta in produzione» → `[]`.
  - `test_first_name_that_is_a_skill`: `nomi=["Ada"]`, `competenze[0].voci = ["Ada"]`, sommario «Sviluppo in Ada» → `[]`; sommario «Ada ha guidato il team» with no such skill → `["sommario"]`.
  - `test_phone_in_another_format`: bullet «chiamare 333 1234567» → that path.
  - `test_email_handle_and_login`: `altre_sezioni[0].voci[0] = "mmessina"` → that path; `"mario.messina"` → that path.
  - `test_any_url_or_at`: a voce `"github.com/acme"` with `www.`/`https://` forms and `"@acme"` → each path.
  - `test_personal_domain_whole_host`: `"mariomessina.dev"` in a voce → that path.
  - `test_strings_paths_in_order`: `strings(CV)[:3]` paths are `["titolo", "sommario", "esperienze[0].ruolo"]`.
- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_anonymous_cv_guard.py` → FAIL (import error).
- [ ] **Step 3: Implement** `schema.py` and `guard.py` as above.
- [ ] **Step 4: Run** → PASS; `uv run ruff check projects/hub` and `uv run mypy` clean.
- [ ] **Step 5: Commit** `feat(core): the anonymous CV's shape and its identity guard`.

### Task 3: The writer

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/writer.py`
- Modify: `projects/hub/packages/core/src/rebase_core/errors.py` (`AnonymousCvFailed`)
- Modify: `projects/hub/packages/core/src/rebase_core/freelancers.py` (`clear_cv` deletes the row)
- Test: `projects/hub/packages/core/tests/test_anonymous_cv_writer.py` (reuse `fakes_cards.text_pdf`, `RecordingCall`)

**Interfaces:**
- Consumes: Task 1's `AnonymousCvRow`; Task 2's `AnonymousCvDraft`, `who_of`, `identifying_fields`; `cards._block` (the CV as an inert block), `cards.rewrite_request`'s pattern, `cards.stored_persona`, `cards.NO_TEXT`, `FreelancerService.cv_text`.
- Produces: `CV_PROMPT_VERSION = 1`; `class AnonymousCvFailed(DomainError)` with a user-facing Italian `message`, HTTP 422 in the API's handler; `@dataclass(frozen=True) class Written(cv: AnonymousCv, reused: bool)`; `class AnonymousCvWriter(session: Session, llm: LlmCall | None)` with `def get_or_write(self, freelancer_id: UUID) -> Written` and `def delete(self, freelancer_id: UUID) -> None`; `def cv_request(cv: CvText) -> LlmRequest` (`output_model=AnonymousCvDraft`).

Behaviour of `get_or_write`:
- Freelancer missing or deleted → `NotFound`; no CV → `NotFound("cv", ...)` whose message is «Nessun CV caricato».
- Row with `cv_sha256 == sha256(cv_bytes).hexdigest()` and `prompt_version == CV_PROMPT_VERSION` → `Written(body, reused=True)`, no call.
- Otherwise: empty CV text → `AnonymousCvFailed(NO_TEXT)`; `llm is None` → `LlmNotConfigured` with «Il compito «CV anonimo» non è configurato in «Modelli AI».»; commit, then call. `stop_reason` `refusal` / `max_tokens` / unparsed → `AnonymousCvFailed` with «Il modello ha rifiutato di scrivere il CV.», «Il CV è troppo lungo per il tetto di risposta del compito: alzalo in «Modelli AI».», «La risposta del modello non era un CV valido.». Guard over `who_of(profile)` ∪ `stored_persona` ∪ `identita` of the answer; a hit → one rewrite (the answer as assistant turn, then a user turn naming the paths); a second hit → `AnonymousCvFailed("Il CV contiene ancora il nome, un recapito o un link della persona anche dopo la riscrittura.")`. `LlmUnavailable` and its subclasses propagate untouched.
- Store: lock the freelancer row (`with_for_update`), re-hash `cv_bytes`; differs → `AnonymousCvFailed("Il CV è cambiato mentre lo scrivevo: riprova.")`, nothing stored; else upsert the row (`pg_insert ... on_conflict_do_update`) and commit. Usage is recorded by the seam with this `freelancer_id`, as the card writer's calls are.
- Nothing logged but the freelancer id and the failure kind.

- [ ] **Step 1: Write the failing tests** (a talent with `text_pdf("Mario Messina Backend developer TIM 2019-2024")` as CV, profile Mario Messina; `RecordingCall` answering an `AnonymousCvDraft` as JSON text and `parsed`):
  - `test_first_download_writes_and_stores`: one request, `output_model is AnonymousCvDraft`, system mentions every exclusion; row stored with the CV's hash, `CV_PROMPT_VERSION`, model, tokens; `reused is False`.
  - `test_second_download_reuses_without_a_call`: second `get_or_write` makes no request, `reused is True`, body equal.
  - `test_new_cv_writes_again` and `test_prompt_version_bump_writes_again` (monkeypatch `CV_PROMPT_VERSION = 2`).
  - `test_identifying_answer_is_rewritten_once`: first answer has «Mario Messina» in `sommario`, second is clean → two requests, the second's last user message names `sommario` and not «Messina»; stored body is the second.
  - `test_identifying_twice_fails_and_stores_nothing`.
  - `test_identity_from_the_answer_is_checked`: profile phone empty, `identita.telefono = "333 1234567"`, the number in a bullet → rewritten.
  - `test_refusal`, `test_max_tokens`, `test_no_text` (empty-text PDF, no request), `test_no_cv` → the sentences above.
  - `test_cv_replaced_during_the_call_stores_nothing`: a `RecordingCall` subclass whose `complete` replaces the CV through another session before returning → `AnonymousCvFailed` «Il CV è cambiato…», no row.
  - `test_clear_cv_deletes_the_row`: after `FreelancerService.clear_cv`, no `anonymous_cvs` row.
  - `test_unavailable_propagates`: the call raises `LlmUnavailable` → raised, no row.
- [ ] **Step 2: Run** `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/hub/packages/core/tests/test_anonymous_cv_writer.py` → FAIL.
- [ ] **Step 3: Implement** `writer.py` (the prompt: the spec's § «The prompt», in Italian, every exclusion named, the CV in `cards._block`), the error, and the `clear_cv` line beside `cards.delete`.
- [ ] **Step 4: Run** the new tests and `test_cards.py`, `test_merge.py`, `test_freelancers*.py` → PASS; ruff and mypy clean.
- [ ] **Step 5: Commit** `feat(core): write and keep a talent's anonymous CV`.

### Task 4: The PDF

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/render.py`
- Create: `projects/hub/packages/core/src/rebase_core/anonymous_cv/cv.typ`
- Modify: the core package's data-file list if `contracts/*.typ.template` is declared there (`pyproject.toml`), so `cv.typ` ships; `Dockerfile.api` needs nothing new (typst and the brand are already there; check).
- Test: `projects/hub/packages/core/tests/test_anonymous_cv_pdf.py`

**Interfaces:**
- Consumes: Task 2's `AnonymousCv`; `contracts.brand` (`palette()`, `fonts_dir()`, `ECHO`); `contracts.render`'s `_run` pattern and `TOOL_TIMEOUT_SECONDS`.
- Produces: `@dataclass(frozen=True) class CvHead(seniority: str | None, anni: int | None, luogo: str | None, modalita: str | None, ref: str)`; `class CvRenderer(Protocol): def render(self, cv: AnonymousCv, head: CvHead) -> bytes`; `class TypstCvRenderer` (production); `class FakeCvRenderer` (records calls, answers `b"%PDF-fake"`); `def filename(cv: AnonymousCv, ref: str) -> str`; `MODALITA_LABELS = {"remoto": "Da remoto", "ibrido": "Ibrido", "in_sede": "In sede"}`.

Rendering: a temporary directory per call; write `cv.json` (`{"cv": ..., "head": ..., "palette": ...}`) and copy `cv.typ` and the echo into it; `typst compile --root <dir> --font-path <fonts_dir> --creation-timestamp 0 cv.typ out.pdf`; read the bytes. The template reads `json("cv.json")` and inserts strings as text only (never `eval`). Layout per the spec's § «The PDF»; headings by `lingua` (`it`: Profilo, Esperienze, Competenze, Formazione, Certificazioni, Lingue; `en`: Profile, Experience, Skills, Education, Certifications, Languages); empty sections left out; the head's line joins seniority label («Junior», «Mid», «Senior», «Lead»), «N anni di esperienza» / «N years of experience», city, work mode with « · ».

- [ ] **Step 1: Write the failing tests** (skip the module when `shutil.which("typst") is None`, as `test_contract_pdf.py` does; read text back with `pypdf`):
  - `test_every_role_and_section_prints`: the fixture's roles, companies, a bullet, a skills area, a school, a certification, a language and the footer's `Rif. d41c7e2a` are in the text.
  - `test_headings_follow_lingua`: `lingua="en"` → «Experience» present, «Esperienze» absent.
  - `test_empty_sections_are_left_out`: no certifications → no heading for them.
  - `test_markup_prints_literally`: strings `#set page(width: 1cm)`, `*bold*`, `$x^2$`, `@ref`, `<label>`, `// comment`, `\` render, compile succeeds, each appears in the text.
  - `test_same_body_same_bytes`: two renders are byte-equal.
  - `test_filename`: `filename(cv_with_titolo("Oracle DBA & Security Engineer"), "d41c7e2a") == "rebase-cv-oracle-dba-security-engineer-d41c7e2a.pdf"`; accents folded, length capped.
- [ ] **Step 2: Run** `uv run pytest -q projects/hub/packages/core/tests/test_anonymous_cv_pdf.py` → FAIL.
- [ ] **Step 3: Implement** `render.py` and `cv.typ`.
- [ ] **Step 4: Run** → PASS. Render the fixture to `/tmp`-free scratch (`tempfile`) and look at both pages as images (`pdftoppm -png`), compare with the spec's layout; fix what reads badly.
- [ ] **Step 5: Commit** `feat(core): print the anonymous CV with Typst in rebase's style`.

### Task 5: The route, the audit entry and the records

**Files:**
- Modify: `projects/hub/apps/api/src/rebase_api/deps.py` (`get_cv_llm` → `call_for_task(opener, settings, "cv_anonimo")`, `CvLlmDep`; `get_cv_renderer` → `TypstCvRenderer()`, `CvRendererDep`)
- Modify: `projects/hub/apps/api/src/rebase_api/routers/admin.py` (the route)
- Modify: the API's exception handler map if `AnonymousCvFailed` needs a 422 entry (follow how `DomainError` subclasses map)
- Modify: `docs/design/DECISIONS.md` (one row, 2026-10-30, REB-862: question / decision / consequences, from the spec's «What Ivan decided» and the separate-call and employers-stay choices), `projects/hub/AGENTS.md` (a short section: the task, the table, the route, what never reads it)
- Test: `projects/hub/apps/api/tests/test_anonymous_cv_api.py`

**Interfaces:**
- Consumes: Task 3's `AnonymousCvWriter`, `Written`; Task 4's `CvRenderer`, `CvHead`, `filename`; `card_level.card_read` for seniority/anni over the admin's correction; `downloads.pdf_response`; `AdminActionService` as `FreelancerService` writes entries.
- Produces: `POST /api/hub/freelancers/{freelancer_id}/cv-anonimo` → `200 application/pdf`, `Content-Disposition` attachment with `filename(...)`; errors: 401/403 not admin, 404 no talent or no CV, 503 `TeamBuilderOff`-style when `CvLlmDep is None` and no reusable body exists, 422 `AnonymousCvFailed`, the seam's `LlmUnavailable` as the card route answers it. On 200, one `AdminAction` `anon_cv_downloaded` on the freelancer with `{"riusato": bool}`, actor the admin.

Note: with `CvLlmDep is None` a stored, current body is still served (pass `llm=None` to the writer; it raises only when a call is needed).

- [ ] **Step 1: Write the failing tests** (dependency overrides: `get_cv_llm` → `RecordingCall`, `get_cv_renderer` → `FakeCvRenderer`):
  - `test_requires_an_admin` (anonymous 401, member 403).
  - `test_no_cv_is_404_with_the_sentence`.
  - `test_task_off_is_503` (override returns `None`, no stored body).
  - `test_download_answers_the_pdf_and_records_it`: 200, `application/pdf`, filename `rebase-cv-…-<ref>.pdf`, `FakeCvRenderer` got `CvHead(ref=str(id)[-8:], …)` with the card's seniority and the admin's `anni_admin` when set; the audit trail has `anon_cv_downloaded` `{"riusato": false}` by this admin.
  - `test_second_download_reuses` → `{"riusato": true}`, one LLM request in total; served even with the task off.
  - `test_guard_failure_is_422_with_the_sentence` and no audit entry.
- [ ] **Step 2: Run** `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/hub/apps/api/tests/test_anonymous_cv_api.py` → FAIL.
- [ ] **Step 3: Implement** the deps and the route (docstring in the style of `regenerate_freelancer_card`), the DECISIONS row and the AGENTS section. Regenerate the web's API types if the repo generates them from the OpenAPI schema (check `apps/web/src/lib/api*` and how `api-types` are produced; a binary response may need no type).
- [ ] **Step 4: Run** the new tests, `test_admin_api.py` and `uv run pytest -q projects/hub/packages/core/tests/test_ai_config.py` → PASS; ruff, mypy clean.
- [ ] **Step 5: Commit** `feat(api): download a talent's anonymous CV as a PDF`.

### Task 6: The button

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts` (`admin.downloadAnonymousCv(id): Promise<{ blob: Blob; filename: string }>`, POST, reads the filename from `Content-Disposition`, throws `ApiError` with the API's sentence on a non-2xx)
- Modify: `projects/hub/apps/web/src/pages/admin/SchedaAnonima.tsx` (the button beside «Rigenera scheda» when `hasCv`; label «Scarica CV anonimo», pending «Preparo il CV…», disabled while pending; saves through an object URL and a temporary `<a download>`; error under the heading with `role="alert"`, the `ApiError` message or «Non riesco a preparare il CV anonimo.»)
- Modify: `projects/hub/apps/web/src/pages/admin/ModelliAi.tsx` (`TASK_LABELS.cv_anonimo = 'CV anonimo'`)
- Modify: `projects/hub/apps/web/src/pages/admin/AuditTrail.tsx` (label for `anon_cv_downloaded`: «CV anonimo scaricato», and «(già pronto)» when `riusato`)
- Test: `SchedaAnonima.test.tsx`, `ModelliAi.test.tsx`, `AuditTrail.test.tsx`

**Interfaces:**
- Consumes: Task 5's route and audit entry shape.

- [ ] **Step 1: Write the failing tests**: button absent without a CV; click calls `admin.downloadAnonymousCv('f1')`, shows «Preparo il CV…» disabled while pending, creates an object URL and clicks an anchor with the server's filename; a rejected call shows the API's sentence in an alert (mock `api.POST` `{error, response}` and let `unwrap` throw, not a bare rejected `vi.fn`, which fails under `act`); the Modelli AI row reads «CV anonimo»; the trail reads «CV anonimo scaricato».
- [ ] **Step 2: Run** `pnpm --filter hub test -- SchedaAnonima ModelliAi AuditTrail` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pnpm --filter hub test` and `pnpm --filter hub lint` and the web type-check (`pnpm --filter hub exec tsc -b` or the package's script) → PASS.
- [ ] **Step 5: Commit** `feat(web): «Scarica CV anonimo» on the talent's page`.

### After the tasks (controller)

Preflight (`python3 .github/kit/bin/preflight`), the PR per the `pr-creation` skill with the screenshots section (before/after of the section, one rendered PDF page), the Greptile + CodeRabbit loop, then on the preview: set the `cv_anonimo` task, download a real talent's PDF, read every page against their CV for identity leaks and missing roles, and post the evidence on REB-862.
