# Simpler matches, and the same actions over MCP. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** «Crea match» becomes a three-step wizard that asks only what it must; «Match e contratti» and the «Match» list say in sentences what happened and what comes next; every match and contract action is an MCP tool; the contract PDFs carry the echo logo.

**Architecture:** the sentences, the next action and the pre-send check live in the core (`rebase_core/match_words.py`, `MatchService.check`, `MatchService.proposal`), so the web pages and the MCP tools read the same words from the same place. The web pages lose their own state rules and render what the core answers. The MCP tools call the services the admin API calls, with the calling admin as the actor.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Pydantic, the MCP Python SDK (`apps/mcp`), React 19 with TanStack Router and Query, Vitest and Testing Library, pandoc and Typst for the PDFs.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-simpler-matches-and-mcp-design.md`. It builds on `2026-09-23-matches-and-contract-signing-design.md`, which stays the authority on what a match and its documents are.

## Global Constraints

- English for code, comments, docs and commit messages; Italian only for what the product says to people. No em dashes anywhere (check with `LC_ALL=C grep -rn $'\xe2\x80\x94' <files>`). Write «», ’ and … as the characters themselves, never as `\u` escapes.
- Conventional Commits, pathspec staging (`git add <paths>`, never `-A`), no AI co-author trailer and no «Generated with» line; the body's last line is the card, for example `REB-477.`
- No process labels in code, tests, docs or commit messages: no «Task 3», no «fix round», no review ids, no «controller». Linear ids such as REB-477 are fine where the code already uses them.
- Run every check in the foreground and wait for it. Never start a test run in the background and wait on it.
- Python checks, from the repository root: `uv run ruff check projects/hub && uv run ruff format --check projects/hub`, `uv run mypy`, `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests` (needs Docker for the testcontainers Postgres). Web checks: `pnpm --filter hub lint`, `pnpm --filter hub test`, `pnpm --filter hub build`.
- The company's `budget_giornaliero` never reaches a page, a tool answer or a sentence. The tax identifiers never reach an MCP answer. PDF bytes never reach an MCP answer: links only (`pdf_url`).
- The stored states (`bozza`, `in_firma`, `attivo`, `concluso`, `annullato`; `generato`, `in_attesa`, `inviato`, `firmato`, `annullato`, `disdetto`) do not change. No migration in this plan.
- Never name the platform whose contracts were the model for ours, anywhere.

## Review Focus

1. **A match whose letter waits (`in_attesa`) reads differently depending on the framework agreement.** With a framework agreement out for signature the letter leaves by itself and there is no next step; with none, or a cancelled or refused one, «Invia per la firma» is the next step. Task 1 `test_waiting_letter_with_framework_out_has_no_next_step` and `test_waiting_letter_without_framework_out_is_sent_again`.
2. **The «Match» list must not run one framework query per row.** Up to 500 rows. Task 1 `test_list_all_reads_frameworks_in_one_query`.
3. **`check` must write nothing and number nothing**, even when it succeeds. Task 1 `test_check_writes_nothing`.
4. **An MCP write must leave the same audit row the API leaves**, naming the calling admin. Task 2 `test_send_over_mcp_audits_the_calling_admin`.
5. **The wizard must not save the tax data it only showed.** Saved tax data shown as a line and left alone make no `PUT .../fiscal`. Task 3 `does not save tax data it only showed`.

---

## File Structure

- Create `projects/hub/packages/core/src/rebase_core/match_words.py`: labels, sentences, next and other actions, the check's sentences. Pure functions.
- Modify `projects/hub/packages/core/src/rebase_core/contract_schemas.py`: new fields on `ContractDocumentRead`, `MatchRead`, `MatchListItem`; new `MatchCheck`.
- Modify `projects/hub/packages/core/src/rebase_core/matches.py`: fill the new fields; `check`, `proposal`.
- Modify `projects/hub/apps/api/src/rebase_api/routers/matches.py`: `POST /freelancers/{id}/matches/check`.
- Modify `projects/hub/apps/mcp/src/rebase_mcp/server.py`: ten tools, the instructions.
- Create `projects/hub/apps/mcp/tests/test_match_write_tools.py`; modify `projects/hub/apps/mcp/tests/test_tools.py`.
- Modify `projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`, `Contratti.tsx`, `Matches.tsx`, `lib/contracts.ts`, `lib/api.ts`, `lib/format.ts`, and their tests.
- Modify `projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template`, `brand.py`, `render.py`; `projects/hub/Dockerfile.api`; `flake.nix`; `shared/brand/README.md`; `docs/design/DECISIONS.md`.

---

### Task 1: The core says what happened and what comes next (REB-477, REB-476)

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/match_words.py`
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, `projects/hub/packages/core/src/rebase_core/matches.py`, `projects/hub/apps/api/src/rebase_api/routers/matches.py`
- Test: `projects/hub/packages/core/tests/test_match_words.py` (new), the existing matches tests in `projects/hub/packages/core/tests/`, `projects/hub/apps/api/tests/` (the matches router tests)

**Interfaces:**
- Produces, in `rebase_core.match_words`:
  - `Action = Literal["invia", "reinvia_email", "aggiorna_stato", "annulla", "chiudi", "registra_disdetta"]`
  - `MATCH_STATE_LABELS: dict[str, str]` and `DOCUMENT_STATE_LABELS: dict[str, str]` with the admin labels below.
  - `document_words(document: ContractDocumentRead-like fields) -> tuple[str, Action | None, list[Action]]` (sentence, next, others). Take the values it needs as arguments or a small dataclass rather than the Pydantic model, so `matches.py` can call it while building the read model.
  - `match_words(stato, letter, framework_stato: str | None, letter_start: str | None, letter_end: str | None) -> tuple[str, Action | None, list[Action]]`
  - `check_sentences(...) -> tuple[list[str], str]` (riepilogo, cosa_succede)
- Produces, in `contract_schemas`:
  - `ContractDocumentRead` gains `situazione: str`, `prossima_azione: Action | None`, `altre_azioni: list[Action]`.
  - `MatchRead` gains `situazione: str`, `prossima_azione: Action | None`, `altre_azioni: list[Action]`.
  - `MatchListItem` gains `situazione: str`.
  - `class MatchCheck(BaseModel)`: `riepilogo: list[str]`, `cosa_succede: str`, `quadro_necessario: bool`, `dati_fiscali_mancanti: bool`.
- Produces, on `MatchService`:
  - `check(self, freelancer_id: UUID, payload: MatchCreate) -> MatchCheck`
  - `proposal(self, freelancer_id: UUID, company_id: UUID, cliente: dict[str, object] | None = None, lettera: dict[str, object] | None = None, match_id: UUID | None = None) -> MatchCreate`: the prefill as a `MatchCreate`, each given key laid over it; an unknown key is a `DomainError` (422) naming it; the result validated by `MatchCreate`, so a required field still empty after the overlay is refused naming it.
- Produces the route `POST /api/hub/freelancers/{freelancer_id}/matches/check` (body `MatchCreate`, answer `MatchCheck`, admin only).

**The admin labels** (`match_words`, and the web's `lib/format.ts` copies them in Task 4):

| Match state | Label | Document state | Label |
| --- | --- | --- | --- |
| `bozza` | Da inviare | `generato` | Pronto, non inviato |
| `in_firma` | In attesa di firma | `in_attesa` | Parte dopo il contratto quadro |
| `attivo` | Attivo | `inviato` | Da firmare |
| `concluso` | Concluso | `firmato` | Firmato |
| `annullato` | Annullato | `annullato` | Annullato |
| | | `disdetto` | Disdetto |

**The document's sentence and actions** (`d` is the document; dates through `italian_date`):

| State | `situazione` | next | others |
| --- | --- | --- | --- |
| framework `generato` | «Pronto, non ancora inviato: parte con «Invia per la firma» sul match.» | none | `annulla` |
| letter `generato` | «Pronta, non ancora inviata.» | none | none |
| letter `in_attesa` | «Parte da sola dopo la firma del contratto quadro.» | none | none |
| `inviato` | «Inviato il {sent_at}: aspetta la firma del freelance.» (letter: «Inviata …») | `reinvia_email` | `aggiorna_stato`, plus `annulla` for a framework agreement |
| `firmato`, no signed copy yet | «Firmato il {signed_at}; la copia firmata non è ancora arrivata.» (letter: «Firmata …») | `aggiorna_stato` | `registra_disdetta` when `attivo` |
| framework `firmato`, `attivo` | «Firmato il {signed_at}. Si rinnova da solo il {rinnovo}; disdetta entro il {ultimo_giorno_disdetta}.» | none | `aggiorna_stato`, `registra_disdetta` |
| framework `firmato`, not `attivo` | «Firmato il {signed_at}, non più attivo.» | none | `aggiorna_stato` |
| letter `firmato` with its copy | «Firmata il {signed_at}.» | none | none |
| `annullato` | «Annullato: {cancel_reason}.» or «Annullato.» (letter: «Annullata…») | none | none |
| `disdetto` | «Disdetto il {notice_at}.» | none | none |

When `testo_bozza` is true, append « Il testo è ancora in bozza.» to a framework agreement's sentence.

**The match's sentence and actions** (`n` is the letter's number; `start`/`end` are the letter's `data-inizio`/`data-fine` as printed, from `ContractDocument.data`):

| Case | `situazione` | next | others |
| --- | --- | --- | --- |
| `bozza` | «Da inviare: la lettera n. {n} è pronta, il freelance non ha ancora ricevuto nulla.» | `invia` | `annulla` |
| `in_firma`, letter `in_attesa`, framework `inviato` | «La lettera n. {n} aspetta la firma del contratto quadro e parte da sola dopo.» | none | `annulla` |
| `in_firma`, letter `in_attesa`, no framework out | «La lettera n. {n} aspetta un contratto quadro: «Invia per la firma» ne genera uno nuovo.» | `invia` | `annulla` |
| `in_firma`, letter `inviato` | «Lettera n. {n} inviata il {sent_at}: aspetta la firma del freelance.» | `reinvia_email` | `aggiorna_stato`, `annulla` |
| `in_firma` or `attivo`, letter `firmato`, no signed copy | «Lettera n. {n} firmata il {signed_at}; la copia firmata non è ancora arrivata.» | `aggiorna_stato` | `chiudi` when `attivo`, else `annulla` |
| `attivo` | «Attivo: lettera n. {n} firmata il {signed_at}, dal {start}[ al {end}].» | none | `chiudi` |
| `concluso` | «Concluso: lettera n. {n}[, dal {start} al {end}].» | none | none |
| `annullato` | «Annullato: {letter cancel_reason}.» or «Annullato: la lettera n. {n} non va più firmata.» | none | none |

«No framework out» means the freelancer's pending framework agreement is missing or not `inviato` (the web's `quadro?.stato !== 'inviato'` today, read through `pending_framework`).

**The check's sentences** (`check_sentences`; name is the freelancer's `nome cognome`; the fee through the letter's own formatter, `contracts.fields.rendered`, so it reads «450,00 €» as the PDF does):

1. «{nome cognome} lavorerà per {cliente_ragione_sociale} come {ruolo}[, {luogo}], dal {data_inizio}[ al {data_fine}].»
2. When `impegno` is set: «Impegno: {impegno}.»
3. «Compenso: {compenso} {unita or modalita}, IVA esclusa, pagato a {giorni_pagamento} giorni[ fine mese].» (`{compenso}` carries its «€»)
4. `cosa_succede`: with `quadro_necessario`: «Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma.» With a framework agreement already out for signature: «Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua firma.» With an active one: «Il contratto quadro è già attivo: parte subito la lettera di incarico.»
5. With `dati_fiscali_mancanti`, `riepilogo` ends with «Mancano i dati fiscali del freelance: servono prima di salvare.»

- [ ] **Step 1: Write the failing tests** in `test_match_words.py`: one test per row of the two tables above (sentence, next, others), the labels for every state, the check sentences for a day rate and for a lump sum, with and without `data_fine`, `impegno`, `fine_mese`, and with missing tax data. Name the Review Focus ones exactly: `test_waiting_letter_with_framework_out_has_no_next_step`, `test_waiting_letter_without_framework_out_is_sent_again`.
- [ ] **Step 2: Run them** and see them fail on the missing module.
- [ ] **Step 3: Write `match_words.py`.**
- [ ] **Step 4: Add the fields to the read models** and fill them where `document_read`, `_match_read` and the list rows are built in `matches.py`. `_match_read` needs the freelancer's framework state: pass it in from `for_freelancer` and `get`. In `list_all`, read the pending framework agreement of every freelancer on the page in one query, then look each row up. Test `test_list_all_reads_frameworks_in_one_query` counts the statements (a SQLAlchemy `before_cursor_execute` listener) for a page of three matches of three freelancers and asserts the count does not grow with the rows.
- [ ] **Step 5: Write `check` and `proposal`** with their tests: `test_check_writes_nothing` (no `matches`, `contract_documents` or `contract_letter_counters` row changes, compared before and after); `check` refusing a closed request and an invalid field as `create` does; `check` reporting `dati_fiscali_mancanti`; `proposal` keeping the prefill, laying a given field over it, refusing an unknown key by name, refusing a required field left empty by name.
- [ ] **Step 6: Add the route** and its API tests: 200 with the sentences, 422 naming a field, 401 without the admin cookie.
- [ ] **Step 7: Run the Python checks** (Global Constraints) and fix what they find.
- [ ] **Step 8: Commit** `feat(hub): the core says what a match and its documents are doing` with body last line `REB-477.`

### Task 2: The same actions over MCP (REB-478)

**Files:**
- Modify: `projects/hub/apps/mcp/src/rebase_mcp/server.py`
- Create: `projects/hub/apps/mcp/tests/test_match_write_tools.py`
- Modify: `projects/hub/apps/mcp/tests/test_tools.py` (the list of tool names), `projects/hub/apps/mcp/tests/test_match_tools.py` if `list_matches`' answer shape changes
- Modify: `projects/hub/AGENTS.md` (the MCP section: the new tools, one line each)

**Interfaces:**
- Consumes: Task 1's `MatchService.proposal`, `MatchService.check`, `MatchCheck`, the new read-model fields.
- Consumes: `SigningService` built as `apps/api/src/rebase_api/deps.py:get_signing_factory` builds it (renderer `ContractRenderer()`, `client_from_settings`, the mail sender, `settings.signer_json`, `settings.contracts_mail`, `settings.contracts_allow_draft`). Build it once per call from the server's `settings`; accept test doubles through `build_server`'s keyword arguments the way `http` is accepted today (for example `signing: Callable[[Session], SigningService] | None = None`, `renderer: Renderer | None = None`).
- Produces the tools of the spec's section 4 table, with these exact names and parameters:
  - `preview_match(freelancer_id: str, company_id: str, cliente: dict[str, Any] | None = None, condizioni: dict[str, Any] | None = None) -> dict`: `MatchCheck` plus `cliente` and `condizioni` as the proposal holds them (so an agent can show what would be written).
  - `create_match(freelancer_id: str, company_id: str, cliente: dict | None = None, condizioni: dict | None = None, match_id: str | None = None) -> dict`: the `MatchRead`, with `pdf_url` on its letter.
  - `send_match_for_signature(match_id: str) -> dict`: the `SendReport` plus `messaggio`, the sentence the web shows (port `sendReportMessage` from `apps/web/src/lib/contracts.ts` into `match_words.send_report_sentence` and use it here).
  - `resend_signing_mail(document_id: str)`, `refresh_contract(document_id: str)`, `cancel_contract(document_id: str)`, `record_notice(document_id: str) -> dict`: the `ContractDocumentRead` with `pdf_url`.
  - `cancel_match(match_id: str)`, `close_match(match_id: str) -> dict`: the `MatchRead`.
  - `set_freelancer_tax_data(freelancer_id: str, codice_fiscale: str, partita_iva: str, domicilio: str, pec: str | None = None) -> dict`: `{"dati_fiscali": "salvati"}`, never the values.
- Every write takes the actor from `admin()`, the same `AdminProvider` the other write tools use.
- A soft-deleted freelancer's match or document is «not found», through `require_live_freelancer` and the same document guard the API uses.
- Descriptions in Italian, like the other tools. The four that cannot be taken back (`send_match_for_signature`, `cancel_contract`, `cancel_match`, `record_notice`) say so, and `close_match` says it ends the engagement. `INSTRUCTIONS` gains one sentence: matches, contracts and their signature can be handled here too. `list_matches`' description drops «Solo lettura: i match si creano dall'area admin».

- [ ] **Step 1: Write the failing tests** in `test_match_write_tools.py`, with the fakes the existing tests use (`fakes_contracts.FakeRenderer`, and a fake Documenso client and sender as `packages/core/tests` has them for the signing service): each tool's happy path; `preview_match` writing nothing; `create_match` with no overrides using the prefill, with an override, refusing an unknown key by name, idempotent with `match_id`; `send_match_for_signature` answering `messaggio`; `set_freelancer_tax_data` not echoing the values; a soft-deleted freelancer's match not found; no answer carrying `codice_fiscale`, `partita_iva` or `budget_giornaliero` anywhere in its JSON. Name the Review Focus one `test_send_over_mcp_audits_the_calling_admin`.
- [ ] **Step 2: Run them** and see them fail.
- [ ] **Step 3: Write the tools.**
- [ ] **Step 4: Add every new name** to `test_tools.py`'s list.
- [ ] **Step 5: Run the Python checks.**
- [ ] **Step 6: Commit** `feat(hub): match and contract actions over MCP` with body last line `REB-478.`

### Task 3: «Crea match» in three steps (REB-476)

**Files:**
- Modify: `projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`, `projects/hub/apps/web/src/lib/contracts.ts`, `projects/hub/apps/web/src/lib/api.ts`
- Test: `projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx` (rewrite for the new steps)

**Interfaces:**
- Consumes: Task 1's route. `api.ts` gains `admin.matchCheck(freelancerId: string, payload: MatchCreate): Promise<MatchCheck>` and the `MatchCheck` type; the read-model types gain `situazione`, `prossima_azione`, `altre_azioni` (Task 4 uses them).
- Keeps: the per-run `matchId`, the `shown` ref that drops a late prefill or preview, `created` so a retry sends the draft already written, the refused-mail message staying on the last step, the 409 link to «Match e contratti».

**The steps** (`STEPS = ['Chi e per chi', 'Condizioni', 'Controlla e invia']`):

1. **Chi e per chi.** The company list as today. When a request is picked, load the prefill (as today) and show under the list:
   - «Cliente sulla lettera»: one line «{ragione sociale} · P.IVA {piva} · {sede}» with a «Modifica» button when all three are filled; the three fields otherwise, and after «Modifica».
   - «Dati fiscali di {nome}»: when saved, «Salvati: CF {cf} · P.IVA {piva}» with «Modifica»; when missing, «Mancano: servono per il contratto.» and the four `FiscalFields`, required. «Avanti» saves them (`admin.saveFiscal`) only when they were missing or opened with «Modifica» and changed; then goes to step 2.
2. **Condizioni.** In this order: Ruolo; Cosa farà (textarea, the `attivita` field); Inizio; Fine prevista (facoltativa); Impegno; Dove (`luogo`); «Come si paga», two radio buttons «A giornata» and «A corpo» setting both `modalita` and `unita` to `a giornata` or `a corpo`; «Compenso, IVA esclusa (€)», with the hint «al giorno» or «in tutto» after it; «Pagamento a … giorni» and the «fine mese» checkbox. «A corpo» shows `risultati`, `accettazione`, `scadenze_fatturazione` right after the fee. Below, a `<details>` closed by default, summary «Altre condizioni (facoltative)», holding every other letter field in `LETTERA_GROUPS`' groups and order. Required: the same five as `LETTERA_REQUIRED`. «Avanti» calls `admin.matchCheck` and both previews (as step 4 does today), then goes to step 3. A 422 naming a field inside the closed disclosure opens it and marks the field.
3. **Controlla e invia.** The `riepilogo` sentences as paragraphs, then `cosa_succede`; the two PDF links «Apri la lettera (PDF)» and, when written, «Apri il contratto quadro (PDF)»; buttons «Indietro», «Salva senza inviare» (outline) and «Invia a {nome} per la firma» (primary). A successful send navigates to `/admin/freelance/$id/contracts` carrying the report's sentence (`sendReportMessage`) in the router's history state, which Task 4 shows; «Salva senza inviare» navigates there with «Bozza salvata: la trovi qui sotto, da inviare.».

- [ ] **Step 1: Rewrite the tests first**, mocking `admin` as the current test file does: the three step names; the client line with «Modifica» versus the fields; the tax data line versus the fields; `does not save tax data it only showed` (no `saveFiscal` call when shown and untouched); saved when edited; the disclosure closed by default and opened by a 422 on one of its fields; «A corpo» adding its three fields and setting `modalita`/`unita`; step 3 showing the check's sentences and both links; a send navigating with the sentence in history state; a refused mail staying on step 3 with the sentence; a late prefill for another company dropped; `budget_giornaliero` nowhere in the DOM.
- [ ] **Step 2: Run them** (`pnpm --filter hub test -- CreaMatch`) and see them fail.
- [ ] **Step 3: Rewrite the page.** Keep it under about 500 lines by moving the step components into `pages/admin/crea-match/` files if it grows; `lib/contracts.ts` keeps the pure helpers.
- [ ] **Step 4: Run the web checks.**
- [ ] **Step 5: Commit** `feat(hub): Crea match in three steps, prefilled` with body last line `REB-476.`

### Task 4: «Match e contratti» as cards, and the «Match» list in the same words (REB-477)

**Files:**
- Modify: `projects/hub/apps/web/src/pages/admin/Contratti.tsx`, `projects/hub/apps/web/src/pages/admin/Matches.tsx`, `projects/hub/apps/web/src/lib/format.ts`, `projects/hub/apps/web/src/lib/contracts.ts`
- Test: `Contratti.test.tsx`, `Matches.test.tsx`, `lists.test.tsx` if it asserts the labels

**Interfaces:**
- Consumes: `situazione`, `prossima_azione`, `altre_azioni` on documents and matches (Task 1), the history-state sentence from Task 3.
- `MATCH_STATE_LABELS` and `DOCUMENT_STATE_LABELS` in `format.ts` take Task 1's admin labels. The member labels do not change.
- `ACTION_LABELS: Record<Action, string>`: `invia` «Invia per la firma», `reinvia_email` «Reinvia email», `aggiorna_stato` «Aggiorna stato», `annulla` «Annulla», `chiudi` «Chiudi match», `registra_disdetta` «Registra disdetta». Each keeps today's `aria-label` naming its object («Reinvia email del contratto quadro», «Annulla il match con {azienda}»), today's confirm dialogs for `annulla` and `registra_disdetta`, and today's mutation.
- The page no longer computes `canSend`, `refreshable` or the cancel and close conditions: it renders `prossima_azione` as the primary button and `altre_azioni` in a `DropdownMenu` (`@rebase/ui/dropdown-menu`) labelled «Altre azioni», shown only when the list is not empty.

**Layout, top to bottom:** `Header` with «Crea match» as today; the history-state sentence, when present, as a `role="status"` line; the send, resend and other action messages as today.
- «Contratto quadro» card (`@rebase/ui/card`): the state label as a pill, `situazione`, the primary button, the PDF and signed-PDF links, «Altre azioni», and a closed `<details>` «Dettagli» with the text version and «Nuova versione disponibile». With no framework agreement: «Nessun contratto quadro: parte con il primo match inviato.»
- «Match»: one card per match: title «{nome_azienda} · {figura_richiesta}», the state pill, `situazione`, the primary button, the letter's PDF links, «Altre azioni», and a small «Creato il {data}».
- «Dati fiscali»: a closed `<details>`, summary «Dati fiscali» plus «Salvati: CF … · P.IVA …» or «Mancano», holding today's form.
- `Matches.tsx`: the «Stato» cell shows the pill and `situazione` under it in muted text. The «Lettera» column keeps the number.

- [ ] **Step 1: Update the tests first:** a card per state reads its `situazione` and shows exactly the primary button the fixture's `prossima_azione` names; «Altre azioni» lists `altre_azioni` and is absent when empty; each action still calls its API and each destructive one still asks first; «Dettagli» and «Dati fiscali» closed by default; the history-state sentence shown on arrival; the «Match» list's sentence under the pill; the labels.
- [ ] **Step 2: Run them** and see them fail.
- [ ] **Step 3: Rewrite the pages.**
- [ ] **Step 4: Run the web checks.**
- [ ] **Step 5: Commit** `feat(hub): Match e contratti as cards with one next step each` with body last line `REB-477.`

### Task 5: The echo on the contract PDFs (REB-479)

Start by merging `origin/main` into the branch if REB-403 (the Nix `hub-api` carrying the brand files, `REBASE_CONTRACTS_BRAND_DIR`) has landed there, so the Nix side can be done in this task; if it has not, do everything but the `flake.nix` line and say so in the report.

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template`, `brand.py`, `render.py` (wherever the fonts reach Typst's root today, the PNG goes the same way)
- Modify: `projects/hub/Dockerfile.api` (copy `shared/brand/echo/echo-ink-watermelon-outlines.png` next to what it already copies from `shared/brand`)
- Modify: `flake.nix` (install the PNG under `share/hub-api/brand/echo/`, beside the palette and the font)
- Modify: `shared/brand/README.md` (the `echo/` row and the echo section: the signing site's branding and the contract PDFs' title block use `echo-ink-watermelon-outlines.png`; the site's and the hub's headers and the business cards keep the lockup)
- Modify: `docs/design/DECISIONS.md` (a row dated 2026-09-25: Ivan chose the echo for the signing surfaces and the documents, the lockup stays in the site's and the hub's headers and on the business cards; the running header of a contract keeps the words without the tiles, since the echo has no compact variant)
- Modify: `.claude/skills/social-content/SKILL.md` only if it says where the lockup or the echo goes; otherwise leave it
- Regenerate whatever `projects/hub/tools/build_contract_pdf.py` writes, and the `.github/preflight.json` `contract-pdf` inputs if a path changes
- Test: `projects/hub/packages/core/tests/test_contract_render.py` and `test_contract_pdf.py`

**What changes in the template:** the title block's `#mark() #h(3pt) rebase` becomes `#image("echo.png", height: 11mm)` (or the file name the renderer copies it under); the running header's `#mark() #h(3pt) rebase · $title$` becomes `rebase · $title$`. If `mark` is then unused, remove it.

- [ ] **Step 1: Write the failing test:** the rendered framework agreement and letter carry one image on page 1 (read with `pypdf`, already a test dependency if `test_contract_pdf.py` uses it, else `pdfimages -list`), and none on page 2; `brand.ECHO` resolves under the checkout and under `REBASE_CONTRACTS_BRAND_DIR`.
- [ ] **Step 2: Run it** and see it fail.
- [ ] **Step 3: Change the template, `brand.py`, the renderer, the image and Nix.**
- [ ] **Step 4: Run** `uv run python projects/hub/tools/build_contract_pdf.py && uv run pytest -q projects/hub/packages/core/tests/test_contract_render.py`, the Python checks, and `docker compose -p rebase build` then `docker run --rm rebase-api uv run --no-sync rebase contracts-check` from `projects/hub` (the `hub-image` preflight, with its two dummy variables). Open the rendered PDF's first page as a PNG (`pdftoppm -png -r 80 -f 1 -l 1`) and look at it.
- [ ] **Step 5: Update the brand README, DECISIONS and, if needed, the skill.**
- [ ] **Step 6: Commit** `feat(hub): the contracts carry the echo logo` with body last line `REB-479.`
