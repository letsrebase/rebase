# Phase 3: sign. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an admin presses «Invia per la firma» on a match and the freelancer gets one rebase mail per document with a button that opens Documenso; the signature comes back by webhook, the sealed PDF is stored and mailed to both parties, a letter that waited for its framework agreement leaves on its own, the admin can refresh, resend and cancel, and the freelancer reads their contracts in the member area.

**Architecture:** a `DocumensoClient` on the hub's `HttpCall` seam makes the five calls the phase 1 probe recorded (create, get, distribute, download, cancel). `SigningService` (new `rebase_core.signing`) owns every transition: it typesets a document again as it leaves (the send date in rebase's blank, today's parties, the labels under the signing blanks left undrawn), hands it to Documenso with `distributionMethod: NONE`, stores the envelope and its item, and mails the link itself. The webhook only locks the row, moves it and commits (`apply`); the download, the mails and the released letters run after the response in a session of their own (`finish`), each step idempotent so «Aggiorna stato» can run it again. The member area reads its own documents through `MemberContractService` behind `MeDep`.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, Pydantic 2, FastAPI 0.141 (`BackgroundTasks`), urllib behind `rebase_core.http`, pypdf 6.17 (tests), testcontainers Postgres 17; React 19, TanStack Router and Query, `@rebase/ui`, vitest with jsdom.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-23-matches-and-contract-signing-design.md` (§ 1, § 4, § 6, § 8, § 9, § 10 item 3), with the facts of `projects/hub/docs/superpowers/specs/2026-09-23-documenso-probe.md` (§ 4 the calls, § 5 the webhook, § 7 the sealed PDF, § 11 what changes). Where the two differ on Documenso, the probe wins. Builds on the interfaces of `projects/hub/docs/superpowers/plans/2026-09-23-matches-phase-2-match-and-generate.md`, which must be merged on `main` before Task 1 starts.

## Controller rulings (2026-09-23)

- Cards: Task 1 is REB-390, Task 2 REB-406, Task 3 REB-391, Task 4 REB-407, Task 5 REB-392.
- Task 2 adds a preview-only setting, `REBASE_CONTRACTS_ALLOW_DRAFT` (default empty, meaning off), that lets a `status: draft` text be sent to Documenso with its BOZZA watermark, so the whole flow can be exercised on the preview before Ivan and Lorenzo finalise the texts (spec § 1f says the flow must be exercisable). Production's `.env` never sets it; the send answers with a sentence naming the setting when it refuses a draft. Cost if wrong: remove one setting.
- Task 2 refuses to send when `REBASE_SIGNER_JSON` is empty, with a sentence (spec § 8), carried from milestone 2's Task 2 review.
- Task 2 regenerates every document at send from current data: the issue date «Documento emesso da rebase il …» becomes the send date (spec § 5), and the signer's and the freelancer's tax data are read again, since a draft stored in milestone 2 froze all three at generation and tax data stay editable (milestone 2's final review, finding 6).
- Draft and cancelled matches consume real `YYYY-NNN` letter numbers in milestone 2; raised with Ivan, no change unless he asks.

## Global Constraints

- English for code, comments, docs and commit messages; Italian only for what the product says to people: UI copy, the mails, the contract texts, MCP tool descriptions, API error sentences.
- No em dashes anywhere you write: code, comments, docs, UI copy, mails, commit messages. An empty value in the UI is a word, not a dash glyph.
- Conventional Commits in the first person (`feat(hub): ...`, body «I add ...»). Stage with explicit pathspecs, never `git add -A` or `git add .`. No AI co-author trailer and no «Generated with» line: the body's last line is the task's card, `REB-390.`, `REB-391.` or `REB-392.` as each task says.
- Every command runs from the repository root: `uv run pytest -q projects/hub/...`, `uv run mypy`, `uv run ruff check projects/hub`, `uv run ruff format --check projects/hub`, `pnpm --filter hub test`, `pnpm --filter hub lint`, `pnpm --filter hub build`.
- A new `REBASE_*` setting the API reads lands in three places at once: `packages/core/src/rebase_core/config.py`, `projects/hub/.env.example` and the `x-api-environment` list of `projects/hub/docker-compose.yml`.
- No test reaches the network (`projects/hub/conftest.py` refuses every socket but the loopback). Documenso is always `FakeDocumenso` through the `HttpCall` seam, mail is always `RecordingSender`, the renderer is always `FakeRenderer` except in `test_contract_render.py`.
- Phase 3 adds columns, not tables. Every fixture that writes contracts deletes, children first: `admin_actions`, `contract_documents`, `matches`, `contract_letter_counters`, `freelancer_fiscal`, `comments`, `freelancers`, `companies`, `users`, `signups` (sessions, logins and magic links cascade from `users`).
- The Documenso calls are the probe's, byte for byte: `POST /api/v2/envelope/create` (multipart, `payload` JSON and `files` PDF), `GET /api/v2/envelope/{id}`, `POST /api/v2/envelope/distribute` with `{"envelopeId", "meta": {"distributionMethod": "NONE"}}`, `GET /api/v2/envelope/item/{itemId}/download?version=signed`, `POST /api/v2/envelope/cancel` with `{"envelopeId", "reason"}`. The create's `meta` sets `distributionMethod: NONE`, `language: it`, `timezone: Europe/Rome`, `dateFormat: dd/MM/yyyy`, `envelopeExpirationPeriod: {"disabled": true}` and every `emailSettings` flag false, `ownerDocumentCompleted` included.
- The date of a signature is the signer's `signedAt`; `completedAt` counts only together with `status: COMPLETED`, because a cancellation sets it too (probe § 4).
- An admin reads Documenso's `message` at most, never its error body's stack trace. The signing URL (its path is the recipient's token) is never in a response an admin page, the audit trail or an MCP tool reads; only the freelancer's own «Contratti» sees it, and only while the document waits for the signature.
- A text whose front matter says `status: draft` never leaves for signature (spec § 1f) unless the setting `REBASE_CONTRACTS_ALLOW_DRAFT` is true, which only the preview's `.env` sets; the refusal names the setting. Task 2 adds it as a boolean (`config.py`, `.env.example`, and the compose list with the default `${REBASE_CONTRACTS_ALLOW_DRAFT:-false}`, since an empty value would fail to parse at start-up).
- Lock order: every write of the signing service (send, webhook transitions, refresh, resend, cancel, notice) locks the freelancer's row first, as `MatchService.create` and `FiscalService.save` already do, then the match, then its document or documents. The webhook handler finds the document by its envelope without a lock, then takes the locks in that order. Without it, a send racing «Crea match» for the same freelancer can mark a framework agreement already out for signature `annullato` and its signature is then ignored.
- The company's `budget_giornaliero` reaches no document, mail or page (spec § 1h), as in phase 2.
- Migration 0018 is defensive (`ADD COLUMN IF NOT EXISTS`, `CREATE ... IF NOT EXISTS`, a `pg_constraint` guard). If another pull request took `0018` on `main` first, refetch `main` and renumber before merging.
- `packages/core` imports neither adapter. Admin routes stay under `/api/hub/` behind `AdminDep`; member routes under `/api/hub/me` behind `MeDep`; the webhook under `/api/hub/documenso/` with no cookie.
- Audit entries go through `AdminActionService.record` after the change's own commit. A framework agreement's actions are recorded on entity `freelancer` (it belongs to no match), a letter's and a match's on entity `match`; the kinds are phase 2's (`documents_sent`, `document_cancelled`, `mail_resent`, `notice_recorded`, `match_cancelled`).
- The web writes no primitive: everything visual comes from `@rebase/ui`; page files export components only and helpers live in `src/lib/`.
- Web pull requests carry before and after screenshots and the video the repository asks for; the controller takes them.

## Review Focus

1. **Two deliveries of one event at once.** Documenso retries at once after a failure and even while a slow first delivery is still running (probe § 5): the second must wait on the document's row and change nothing, so there is one transition, one download and one pair of mails. Task 3 `test_a_second_delivery_waits_for_the_first_and_changes_nothing`.
2. **A signature late in the evening.** 23:30 UTC on 30 September is 1 October in Rome: `signed_at` is the signer's `signedAt`, never `completedAt`, and the letter it releases cites «1° ottobre 2026». Task 3 `test_a_framework_signed_late_at_night_releases_its_letter_with_the_rome_date`.
3. **Two admins send two matches of one freelancer at once,** and both need the same framework agreement: one envelope, and the second letter waits for it. Task 2 `test_two_matches_sent_at_once_send_the_framework_once`.
4. **Documenso refuses after the envelope exists,** or only the mail is refused: in the first case nothing is marked sent and the admin reads Documenso's sentence alone; in the second the document is `inviato` and the report says the mail did not leave. Task 2 `test_documenso_refusing_the_distribution_marks_nothing_sent` and `test_a_refused_mail_leaves_the_document_sent_and_says_so`.
5. **Tax data corrected after the draft was saved:** the copy that leaves prints today's tax data and the day it leaves, not the draft's. Task 2 `test_the_sent_copy_says_the_day_it_left_and_prints_the_tax_data_saved_since_the_draft`.

---

## File Structure

```
projects/hub/packages/core/src/rebase_core/
  http.py                      T1: urllib_download_call, MAX_DOWNLOAD_BYTES
  mail.py                      T1: Attachment, Mail.attachments, document_name, signing_request_mail, signed_copy_mail
  errors.py                    T1: DocumensoFailed, SigningUnavailable
  config.py                    T1: documenso_url, documenso_api_token, documenso_webhook_secret, contracts_mail
  documenso.py                 T1: DocumensoClient, Envelope, Outcome, fields_from_blanks, WebhookBody, outcomes
  contracts/render.py          T1: the `signing` copy, Renderer.signature_blanks, Renderer.is_draft, text_is_draft
  contracts/contract.typ.template  T1: `for-signing` leaves the signing blanks' labels undrawn
  models.py                    T2: ContractDocument.documenso_item_id, cancel_reason
  matches.py                   T2: data_for_sending, write_framework
  contract_schemas.py          T2: SendReport; T4: ContractDocumentRead.cancel_reason; T5: MemberContract(s)
  framework.py                 T4: document_read carries cancel_reason
  signing.py                   T2: send_match; T3: apply, finish; T4: refresh, resend_mail, cancel_document, cancel_match, record_notice
  member_contracts.py          T5: MemberContractService
packages/core/migrations/versions/0018_documenso_envelopes.py      T2
packages/core/tests/
  fakes_contracts.py (modify), fakes_documenso.py, test_documenso.py   T1
  test_http.py, test_mail.py, test_contract_render.py (modify)          T1
  test_migrations.py (modify), test_signing.py                          T2 (T3, T4 append)
  test_member_contracts.py                                              T5 (borrows test_signing's helpers)
apps/api/src/rebase_api/
  deps.py                      T2: get_documenso, get_signing_factory; T3: get_session_opener
  main.py                      T2: 502 and 503 for the two new errors; T3: the webhook router
  routers/matches.py           T2: send; T4: refresh, resend, cancel, notice, match cancel through SigningService
  routers/documenso.py         T3: the webhook
  routers/members.py           T5: /me/contracts
apps/api/tests/
  contract_flow.py             T2: what «Crea match» leaves behind, over HTTP (Tasks 3 and 5 import it)
  test_signing_api.py          T2 (T4 appends)
  test_documenso_webhook_api.py  T3
  test_member_contracts_api.py   T5
apps/web/src/
  lib/api.ts                   T2, T4, T5
  lib/contracts.ts (+ test)    T2: sendReportMessage
  lib/format.ts                T5: MEMBER_DOCUMENT_STATE_LABELS
  pages/admin/Contratti.tsx (+ test)   T2 send; T4 every other signing action
  pages/admin/CreaMatch.tsx (+ test)   T2
  pages/member/Contratti.tsx (+ test)  T5
  pages/member/Area.tsx (+ test)       T5
projects/hub/.env.example, docker-compose.yml   T1
projects/hub/AGENTS.md                          T3
```

Task order: 1 → 2 → 3 → 4 → 5. Task 2 needs every piece of Task 1; Task 3 needs Task 2's send (a document must be `inviato` to be signed); Task 4 needs Task 3's `apply` and `finish`; Task 5 needs a sent document to show and reads nothing else of Tasks 3 and 4, so it could follow Task 2, but its tests sign a document through `apply`.

---

### Task 1: Mail attachments, the Documenso client and the copy for signing (REB-390, part 1 of 2)

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/http.py`
- Modify: `projects/hub/packages/core/src/rebase_core/mail.py`
- Modify: `projects/hub/packages/core/src/rebase_core/errors.py` (append)
- Create: `projects/hub/packages/core/src/rebase_core/documenso.py`
- Modify: `projects/hub/packages/core/src/rebase_core/config.py` (a block after phase 2's `signer_json`)
- Modify: `projects/hub/packages/core/src/rebase_core/contracts/render.py`, `contracts/contract.typ.template`
- Modify: `projects/hub/packages/core/tests/fakes_contracts.py`
- Create: `projects/hub/packages/core/tests/fakes_documenso.py`, `projects/hub/packages/core/tests/test_documenso.py`
- Modify: `projects/hub/packages/core/tests/test_http.py`, `test_mail.py`, `test_contract_render.py`
- Modify: `projects/hub/docker-compose.yml` (end of `x-api-environment`), `projects/hub/.env.example` (after phase 2's contracts block)

**Interfaces:**
- Consumes (phase 2): `rebase_core.contracts.render.{Rendered, SignatureBlank, Renderer, ContractRenderer, render, signature_blanks, text_path, _typst_source, A4_WIDTH_PT, A4_HEIGHT_PT}`, `rebase_core.contracts.fields.{ContractFailed, Value, checked, is_draft}`, `fakes_contracts.{FakeRenderer, FailingRenderer}`, `rebase_core.mail.{Mail, ResendSender, _frame, _button, _quiet_link, INK_QUIET}`, `rebase_core.http.{HttpCall, NETWORK_ERROR_STATUS, MAX_BODY_BYTES}`.
- Produces:
  - `rebase_core.http.MAX_DOWNLOAD_BYTES = 16 * 1_048_576`; `urllib_download_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]`.
  - `rebase_core.mail`: `@dataclass(frozen=True) Attachment(filename: str, content: bytes)`; `Mail.attachments: tuple[Attachment, ...] = ()`; `document_name(kind: str, numero: str | None) -> str` («contratto quadro rebase», «lettera di incarico n. 2026-001»); `signing_request_mail(to: str, nome: str, kind: str, numero: str | None, signing_url: str) -> Mail`; `signed_copy_mail(to: str, *, kind: str, numero: str | None, attachment: Attachment, nome: str, cognome: str, for_rebase: bool = False) -> Mail`.
  - `rebase_core.errors.DocumensoFailed(message: str, detail: str = "")` (code `documenso_failed`, `.detail` for the log); `SigningUnavailable(message)` (code `signing_unavailable`).
  - `Settings.documenso_url: str = ""`, `documenso_api_token: str = ""`, `documenso_webhook_secret: str = ""`, `contracts_mail: str = "ciao@letsrebase.com"`.
  - `rebase_core.documenso`: `API_PREFIX`, `SIGNATURE`, `DATE`, `FIELD_BY_BLANK`, `EMAIL_SETTINGS_OFF: dict[str, bool]`, `META: dict[str, Any]`, `COMPLETED, REJECTED, CANCELLED = "completed", "rejected", "cancelled"`, `UNREACHABLE`, `UNREADABLE`; `fields_from_blanks(blanks: Sequence[SignatureBlank]) -> list[dict[str, Any]]` (raises `ContractFailed` for an unknown blank or no signature); `@dataclass(frozen=True) Envelope(id: str, status: str, item_id: str, external_id: str | None, signed_at: datetime | None, rejection_reason: str | None)`; `DocumensoClient(base_url: str, token: str, http: HttpCall = urllib_download_call)` with `create(*, title: str, external_id: str, filename: str, pdf: bytes, signer_email: str, signer_name: str, fields: list[dict[str, Any]]) -> str`, `get(envelope_id: str) -> Envelope`, `distribute(envelope_id: str) -> str` (the signing URL), `download_signed(item_id: str) -> bytes`, `cancel(envelope_id: str, reason: str) -> None`; `client_from_settings(settings: Settings) -> DocumensoClient | None`; `@dataclass(frozen=True) Outcome(envelope_id: str, kind: str, signed_at: datetime | None = None, reason: str | None = None)`; Pydantic `WebhookBody(event: str, payload: WebhookEnvelope)`, `WebhookEnvelope(envelope_id [alias envelopeId], status, completed_at [alias completedAt], recipients: list[WebhookRecipient])`, `WebhookRecipient(role, signed_at [alias signedAt], rejection_reason [alias rejectionReason])`; `outcome_from_webhook(body: WebhookBody) -> Outcome | None`; `outcome_from_envelope(envelope: Envelope) -> Outcome | None`.
  - `rebase_core.contracts.render`: `render(document, data, signing: bool = False)`, `signature_blanks(document, data, signing: bool = False)`, `text_is_draft(document: str) -> bool`; `Renderer` protocol: `render(self, document, data, *, signing: bool = False) -> Rendered`, `signature_blanks(self, document, data, *, signing: bool = False) -> list[SignatureBlank]`, `is_draft(self, document: str) -> bool`; `ContractRenderer` implements all three.
  - Test helpers: `fakes_contracts.FakeRenderer` gains `signing: list[bool]`, `signature_blanks` (answers `FAKE_BLANKS[document]`) and `is_draft` (answers `self.draft`); `FailingRenderer` gains `draft: bool = True` and both methods; `fakes_contracts.FAKE_BLANKS`. `fakes_documenso.{BASE, TOKEN, SIGNING_HOST, FakeEnvelope, FakeDocumenso}`; `FakeDocumenso()` is an `HttpCall` with `.envelopes: dict[str, FakeEnvelope]`, `.calls: list[tuple[str, str]]` (method, path after `/api/v2`, query included), `.bodies: list[bytes]`, `.headers: list[dict[str, str]]`, and `client()`, `created() -> list[dict]`, `fail(operation, status=400, message=...)`, `down(operation)`, `sign(envelope_id, at)`, `reject(envelope_id, reason)`, `signed_pdf(envelope_id) -> bytes`, `webhook(envelope_id, event) -> dict`. Operations are `create`, `get`, `distribute`, `download`, `cancel`.

- [ ] **Step 1: Write the failing tests for the download seam and the mails**

Append to `projects/hub/packages/core/tests/test_http.py` (add `MAX_DOWNLOAD_BYTES` and `urllib_download_call` to the `rebase_core.http` import):

```python
def test_a_download_reads_past_the_json_cap_and_keeps_a_cap_of_its_own(
    oversized_server: str,
) -> None:
    """The sealed PDFs the Documenso client downloads (REB-387) are larger than any JSON
    the hub reads: the download seam takes the whole body the JSON seam would cut, and
    is still capped, one byte past its own limit."""
    status, body = urllib_download_call("GET", oversized_server, {}, b"")
    assert status == 200
    assert len(body) == MAX_BODY_BYTES + 1000
    assert MAX_DOWNLOAD_BYTES > MAX_BODY_BYTES
```

Append to `projects/hub/packages/core/tests/test_mail.py` (add `import base64`; add `Attachment`, `signed_copy_mail`, `signing_request_mail` to the `rebase_core.mail` import):

```python
def test_resend_sends_each_attachment_as_base64_with_its_name() -> None:
    """REB-387: the signed contracts leave as attachments. Resend takes each file as
    base64 in the JSON body; a mail with none sends no `attachments` key at all."""
    http = FakeHttp()
    attachment = Attachment("lettera-di-incarico-2026-001-firmato.pdf", b"%PDF-1.7 firmato")
    mail = Mail(to="ada@studio.it", subject="x", text="y", attachments=(attachment,))
    assert ResendSender(KEY, FROM, http=http).send(mail) is True
    body = json.loads(http.calls[0][3])
    assert body["attachments"] == [
        {
            "filename": "lettera-di-incarico-2026-001-firmato.pdf",
            "content": base64.b64encode(b"%PDF-1.7 firmato").decode("ascii"),
        }
    ]
    plain = Mail(to="ada@studio.it", subject="x", text="y")
    assert ResendSender(KEY, FROM, http=http).send(plain) is True
    assert "attachments" not in json.loads(http.calls[1][3])


def test_the_signing_mail_names_the_document_and_carries_the_one_link() -> None:
    """Spec § 6: one mail per document, the subject naming it, one button. Documenso
    sends nothing itself, so this mail is the only way the link reaches the person."""
    url = "https://firma.letsrebase.com/sign/abc123"
    quadro = signing_request_mail("ada@studio.it", "Ada", "quadro", None, url)
    lettera = signing_request_mail("ada@studio.it", "Ada", "lettera", "2026-001", url)
    assert quadro.subject == "Da firmare: contratto quadro rebase"
    assert lettera.subject == "Da firmare: lettera di incarico n. 2026-001"
    for mail in (quadro, lettera):
        assert mail.to == "ada@studio.it"
        assert mail.text.startswith("Ciao Ada,")
        assert url in mail.text
        assert mail.html is not None
        # The button's href, and the bare URL as href and as text for blocked buttons.
        assert mail.html.count(url) == 3
        assert "Firma il documento" in mail.html
        assert mail.attachments == ()
    assert "dodici mesi" in quadro.text
    assert "lettera di incarico n. 2026-001" in lettera.text
    hostile = signing_request_mail(
        "ada@studio.it", "<b>Ada</b>", "quadro", None, 'https://x.it/?t="><script>'
    )
    assert hostile.html is not None
    assert "<script>" not in hostile.html and "<b>Ada" not in hostile.html


def test_the_signed_copy_travels_as_an_attachment_to_both_parties() -> None:
    """Spec § 6: the sealed PDF to the freelancer and to rebase's contracts address. Its
    last page is Documenso's certificate, in English (probe § 7): the mail says so."""
    attachment = Attachment("lettera-di-incarico-2026-001-firmato.pdf", b"%PDF-1.7 firmato")
    mine = signed_copy_mail(
        "ada@studio.it",
        kind="lettera",
        numero="2026-001",
        attachment=attachment,
        nome="Ada",
        cognome="Lovelace",
    )
    ours = signed_copy_mail(
        "ciao@letsrebase.com",
        kind="lettera",
        numero="2026-001",
        attachment=attachment,
        nome="Ada",
        cognome="Lovelace",
        for_rebase=True,
    )
    assert mine.subject == "Firmata: lettera di incarico n. 2026-001"
    assert ours.subject == "Firmata da Ada Lovelace: lettera di incarico n. 2026-001"
    assert mine.attachments == ours.attachments == (attachment,)
    assert mine.text.startswith("Ciao Ada,") and ours.text.startswith("Ciao,")
    assert "Ada Lovelace ha firmato la lettera di incarico n. 2026-001" in ours.text
    for mail in (mine, ours):
        assert "certificato della firma elettronica" in mail.text
        assert mail.html is not None
    quadro = signed_copy_mail(
        "ada@studio.it",
        kind="quadro",
        numero=None,
        attachment=attachment,
        nome="Ada",
        cognome="Lovelace",
    )
    assert quadro.subject == "Firmato: contratto quadro rebase"
    assert "hai firmato il contratto quadro rebase" in quadro.text
    hostile = signed_copy_mail(
        "ciao@letsrebase.com",
        kind="quadro",
        numero=None,
        attachment=attachment,
        nome="Ada",
        cognome="<script>",
        for_rebase=True,
    )
    assert hostile.html is not None and "<script>" not in hostile.html
```

- [ ] **Step 2: Write the fake Documenso** (`projects/hub/packages/core/tests/fakes_documenso.py`)

```python
"""A Documenso in a dict, behind the `HttpCall` seam (REB-387): the signing tests' fake,
the way `RecordingSender` is the mail's.

It answers the five calls the hub makes, in the shapes the phase 1 probe recorded
(`docs/superpowers/specs/2026-09-23-documenso-probe.md` § 4), keeps what every create
carried, and plays the rest of the world on request: the signer (`sign`, `reject`), a
refusal with Documenso's error body (`fail`), a dead network (`down`), and the body
Documenso would POST to the webhook (`webhook`)."""

import email
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from rebase_core.documenso import DocumensoClient

BASE = "http://documenso.test"
TOKEN = "api_fake0123456789"
SIGNING_HOST = "https://firma.letsrebase.test"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _error(message: str, status: int) -> bytes:
    """Documenso's error body: the sentence, and a stack trace no admin may read."""
    return json.dumps(
        {
            "message": message,
            "code": "BAD_REQUEST",
            "data": {
                "code": "BAD_REQUEST",
                "httpStatus": status,
                "stack": "Error: at handler (/app/node_modules/@documenso/api/index.js:1:1)",
            },
        }
    ).encode()


def _form(content_type: str, body: bytes) -> dict[str, tuple[str | None, bytes]]:
    """The multipart body's parts, by name: (file name, bytes)."""
    message = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    parts: dict[str, tuple[str | None, bytes]] = {}
    for part in message.get_payload():
        name = part.get_param("name", header="content-disposition")
        parts[str(name)] = (part.get_filename(), part.get_payload(decode=True))
    return parts


@dataclass
class FakeEnvelope:
    id: str
    item_id: str
    payload: dict[str, Any]
    filename: str
    pdf: bytes
    token: str
    status: str = "DRAFT"
    signed_at: datetime | None = None
    completed_at: datetime | None = None
    rejection_reason: str | None = None


@dataclass
class FakeDocumenso:
    envelopes: dict[str, FakeEnvelope] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    failures: dict[str, tuple[int, str]] = field(default_factory=dict)
    outages: set[str] = field(default_factory=set)

    # ---- the test's hand -------------------------------------------------------------

    def client(self) -> DocumensoClient:
        return DocumensoClient(BASE, TOKEN, http=self)

    def created(self) -> list[dict[str, Any]]:
        return [envelope.payload for envelope in self.envelopes.values()]

    def fail(self, operation: str, status: int = 400, message: str = "Qualcosa non va") -> None:
        """The next `operation` answers `status` with Documenso's error body."""
        self.failures[operation] = (status, message)

    def down(self, operation: str) -> None:
        """The next `operation` raises the way urllib does on a refused connection."""
        self.outages.add(operation)

    def sign(self, envelope_id: str, at: datetime) -> None:
        envelope = self.envelopes[envelope_id]
        envelope.status, envelope.signed_at, envelope.completed_at = "COMPLETED", at, at

    def reject(self, envelope_id: str, reason: str) -> None:
        envelope = self.envelopes[envelope_id]
        envelope.status, envelope.rejection_reason = "REJECTED", reason

    def signed_pdf(self, envelope_id: str) -> bytes:
        """The sealed copy: the original and Documenso's certificate page."""
        return self.envelopes[envelope_id].pdf + b"\n%certificato della firma\n"

    def webhook(self, envelope_id: str, event: str) -> dict[str, Any]:
        """What Documenso POSTs for `event` (probe § 5): the envelope, with the legacy
        numeric `id` and `envelopeId` beside it."""
        shown = self._envelope_json(self.envelopes[envelope_id])
        shown.pop("envelopeItems")
        payload = {**shown, "id": 7, "envelopeId": envelope_id, "teamId": 1, "userId": 1}
        return {
            "event": event,
            "payload": payload,
            "createdAt": _iso(datetime.now(UTC)),
            "webhookEndpoint": "http://api:8000/api/hub/documenso/webhook",
        }

    # ---- the seam ----------------------------------------------------------------------

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        parts = urlsplit(url)
        path = parts.path.removeprefix("/api/v2")
        self.calls.append((method, path + (f"?{parts.query}" if parts.query else "")))
        self.bodies.append(body)
        self.headers.append(dict(headers))
        operation = self._operation(method, path)
        if operation in self.outages:
            self.outages.discard(operation)
            raise OSError("connection refused")
        if headers.get("Authorization") != TOKEN:
            return 401, _error("Invalid session or API token.", 401)
        if operation in self.failures:
            status, message = self.failures.pop(operation)
            return status, _error(message, status)
        if operation == "create":
            return self._create(headers, body)
        if operation == "distribute":
            return self._distribute(body)
        if operation == "cancel":
            return self._cancel(body)
        if operation == "download":
            return self._download(path.split("/")[-2], parts.query)
        if operation == "get":
            return self._get(path.split("/")[-1])
        return 404, _error("Not found", 404)

    @staticmethod
    def _operation(method: str, path: str) -> str:
        if method == "POST" and path in ("/envelope/create", "/envelope/distribute", "/envelope/cancel"):
            return path.rsplit("/", 1)[-1]
        if method == "GET" and path.startswith("/envelope/item/") and path.endswith("/download"):
            return "download"
        if method == "GET" and path.startswith("/envelope/"):
            return "get"
        return "unknown"

    def _envelope_json(self, envelope: FakeEnvelope) -> dict[str, Any]:
        recipient = envelope.payload["recipients"][0]
        signing_status = {"COMPLETED": "SIGNED", "REJECTED": "REJECTED"}.get(
            envelope.status, "NOT_SIGNED"
        )
        return {
            "id": envelope.id,
            "status": envelope.status,
            "externalId": envelope.payload.get("externalId"),
            "title": envelope.payload.get("title"),
            "completedAt": _iso(envelope.completed_at),
            "envelopeItems": [{"id": envelope.item_id, "title": envelope.filename}],
            "recipients": [
                {
                    "id": 1,
                    "email": recipient["email"],
                    "name": recipient["name"],
                    "role": "SIGNER",
                    "signingStatus": signing_status,
                    "signedAt": _iso(envelope.signed_at),
                    "rejectionReason": envelope.rejection_reason,
                    "token": envelope.token,
                }
            ],
        }

    def _create(self, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        form = _form(headers["Content-Type"], body)
        number = len(self.envelopes) + 1
        filename, pdf = form["files"]
        envelope = FakeEnvelope(
            id=f"envelope_{number:04d}",
            item_id=f"envelope_item_{number:04d}",
            payload=json.loads(form["payload"][1]),
            filename=filename or "",
            pdf=pdf,
            token=f"token{number:04d}",
        )
        self.envelopes[envelope.id] = envelope
        return 200, json.dumps({"id": envelope.id}).encode()

    def _get(self, envelope_id: str) -> tuple[int, bytes]:
        envelope = self.envelopes.get(envelope_id)
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        return 200, json.dumps(self._envelope_json(envelope)).encode()

    def _distribute(self, body: bytes) -> tuple[int, bytes]:
        envelope = self.envelopes.get(json.loads(body)["envelopeId"])
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        envelope.status = "PENDING"
        recipient = self._envelope_json(envelope)["recipients"][0]
        recipient["signingUrl"] = f"{SIGNING_HOST}/sign/{envelope.token}"
        answer = {"success": True, "id": envelope.id, "recipients": [recipient]}
        return 200, json.dumps(answer).encode()

    def _cancel(self, body: bytes) -> tuple[int, bytes]:
        envelope = self.envelopes.get(json.loads(body)["envelopeId"])
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        if envelope.status != "PENDING":
            return 400, _error("Only pending documents can be cancelled", 400)
        envelope.status, envelope.completed_at = "CANCELLED", datetime.now(UTC)
        return 200, json.dumps({"success": True}).encode()

    def _download(self, item_id: str, query: str) -> tuple[int, bytes]:
        envelope = next((e for e in self.envelopes.values() if e.item_id == item_id), None)
        if envelope is None or query != "version=signed":
            return 404, _error("Envelope item not found", 404)
        return 200, self.signed_pdf(envelope.id)
```

- [ ] **Step 3: Write the failing client tests** (`projects/hub/packages/core/tests/test_documenso.py`)

```python
"""The Documenso client against a Documenso in a dict (REB-387): the probe's exact calls,
what the hub reads back, and the one sentence an admin reads when it fails."""

import json
from datetime import UTC, datetime

import pytest
from fakes_documenso import BASE, TOKEN, FakeDocumenso

from rebase_core.config import Settings
from rebase_core.contracts.fields import ContractFailed
from rebase_core.contracts.render import SignatureBlank
from rebase_core.documenso import (
    CANCELLED,
    COMPLETED,
    EMAIL_SETTINGS_OFF,
    REJECTED,
    UNREACHABLE,
    DocumensoClient,
    Outcome,
    WebhookBody,
    client_from_settings,
    fields_from_blanks,
    outcome_from_envelope,
    outcome_from_webhook,
)
from rebase_core.errors import DocumensoFailed
from rebase_core.http import NETWORK_ERROR_STATUS

PDF = b"%PDF-1.7 lettera"
EXTERNAL = "0192a0c0-0000-7000-8000-000000000001"
# Where the probe measured the letter's two blanks (probe § 9), in points.
SIGNATURE = SignatureBlank("firma professionista", 2, 303.638, 707.281, 155.906, 22.660)
DATE = SignatureBlank("data firma", 2, 165.811, 647.241, 85.039, 6.660)
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


def _create(fake: FakeDocumenso) -> str:
    return fake.client().create(
        title="Lettera di incarico n. 2026-001",
        external_id=EXTERNAL,
        filename="lettera-di-incarico-2026-001.pdf",
        pdf=PDF,
        signer_email="ada@studio.it",
        signer_name="Ada Lovelace",
        fields=fields_from_blanks([DATE, SIGNATURE]),
    )


def _outcome(fake: FakeDocumenso, envelope_id: str, event: str) -> Outcome | None:
    return outcome_from_webhook(WebhookBody.model_validate(fake.webhook(envelope_id, event)))


def test_the_create_sends_the_probes_payload_with_every_documenso_mail_off() -> None:
    fake = FakeDocumenso()
    envelope = fake.envelopes[_create(fake)]
    assert envelope.payload == {
        "type": "DOCUMENT",
        "title": "Lettera di incarico n. 2026-001",
        "externalId": EXTERNAL,
        "recipients": [
            {
                "email": "ada@studio.it",
                "name": "Ada Lovelace",
                "role": "SIGNER",
                "fields": [
                    {
                        "type": "DATE",
                        "page": 2,
                        "positionX": 27.854,
                        "positionY": 76.88,
                        "width": 14.286,
                        "height": 0.791,
                        "fieldMeta": {"type": "date", "fontSize": 9, "textAlign": "left"},
                    },
                    {
                        "type": "SIGNATURE",
                        "page": 2,
                        "positionX": 51.008,
                        "positionY": 84.011,
                        "width": 26.191,
                        "height": 2.692,
                    },
                ],
            }
        ],
        "meta": {
            "distributionMethod": "NONE",
            "language": "it",
            "timezone": "Europe/Rome",
            "dateFormat": "dd/MM/yyyy",
            "envelopeExpirationPeriod": {"disabled": True},
            "emailSettings": dict.fromkeys(EMAIL_SETTINGS_OFF, False),
        },
    }
    # The owner's «Signing Complete!» is the one Documenso mail the probe still saw.
    assert EMAIL_SETTINGS_OFF["ownerDocumentCompleted"] is False
    assert len(EMAIL_SETTINGS_OFF) == 9
    assert (envelope.filename, envelope.pdf) == ("lettera-di-incarico-2026-001.pdf", PDF)
    assert fake.calls == [("POST", "/envelope/create")]
    assert fake.headers[0]["Authorization"] == TOKEN
    assert fake.headers[0]["Content-Type"].startswith("multipart/form-data; boundary=")


def test_get_then_distribute_answer_the_item_and_the_signing_url() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    envelope = client.get(envelope_id)
    assert (envelope.id, envelope.status) == (envelope_id, "DRAFT")
    assert envelope.item_id == fake.envelopes[envelope_id].item_id
    assert envelope.external_id == EXTERNAL
    url = client.distribute(envelope_id)
    assert url == f"https://firma.letsrebase.test/sign/{fake.envelopes[envelope_id].token}"
    assert json.loads(fake.bodies[-1]) == {
        "envelopeId": envelope_id,
        "meta": {"distributionMethod": "NONE"},
    }
    assert client.get(envelope_id).status == "PENDING"


def test_the_signed_copy_is_downloaded_by_item_and_the_signers_date_read_back() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    client.distribute(envelope_id)
    fake.sign(envelope_id, SIGNED_AT)
    envelope = client.get(envelope_id)
    assert (envelope.status, envelope.signed_at) == ("COMPLETED", SIGNED_AT)
    assert client.download_signed(envelope.item_id) == fake.signed_pdf(envelope_id)
    assert fake.calls[-1] == ("GET", f"/envelope/item/{envelope.item_id}/download?version=signed")


def test_cancel_takes_only_an_envelope_out_for_signature() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    client = fake.client()
    with pytest.raises(DocumensoFailed, match="Only pending documents can be cancelled"):
        client.cancel(envelope_id, "Annullato da rebase.")
    client.distribute(envelope_id)
    client.cancel(envelope_id, "Annullato da rebase.")
    assert json.loads(fake.bodies[-1]) == {
        "envelopeId": envelope_id,
        "reason": "Annullato da rebase.",
    }
    assert client.get(envelope_id).status == "CANCELLED"


def test_a_refusal_carries_documensos_sentence_and_never_its_stack() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    fake.fail("distribute", 400, "Recipient is missing a signature field")
    with pytest.raises(DocumensoFailed) as caught:
        fake.client().distribute(envelope_id)
    message = caught.value.message
    assert message == "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    assert "node_modules" not in message and "stack" not in message
    assert "HTTP 400" in caught.value.detail


def test_a_wrong_token_is_refused_in_documensos_words() -> None:
    fake = FakeDocumenso()
    envelope_id = _create(fake)
    wrong = DocumensoClient(BASE, "api_sbagliato", http=fake)
    with pytest.raises(DocumensoFailed, match="Invalid session or API token"):
        wrong.get(envelope_id)


def test_no_answer_is_one_sentence_whatever_the_network_did() -> None:
    fake = FakeDocumenso()
    fake.down("create")
    with pytest.raises(DocumensoFailed) as caught:
        _create(fake)
    assert caught.value.message == UNREACHABLE
    silent = DocumensoClient(BASE, TOKEN, http=lambda m, u, h, b: (NETWORK_ERROR_STATUS, b""))
    with pytest.raises(DocumensoFailed) as again:
        silent.get("envelope_0001")
    assert again.value.message == UNREACHABLE


def test_an_answer_the_hub_cannot_read_is_refused_not_guessed() -> None:
    odd = DocumensoClient(BASE, TOKEN, http=lambda m, u, h, b: (200, b"<html>proxy</html>"))
    with pytest.raises(DocumensoFailed, match="non riconosco"):
        odd.get("envelope_0001")
    with pytest.raises(DocumensoFailed, match="non riconosco"):
        odd.download_signed("envelope_item_0001")


def test_an_unknown_blank_or_a_document_without_a_signature_is_refused() -> None:
    with pytest.raises(ContractFailed):
        fields_from_blanks([SignatureBlank("firma rebase", 2, 10, 10, 10, 10), SIGNATURE])
    with pytest.raises(ContractFailed):
        fields_from_blanks([DATE])


def test_the_webhooks_outcome_is_the_signers_never_the_cancellations() -> None:
    fake = FakeDocumenso()
    client = fake.client()
    signed, refused, cancelled = _create(fake), _create(fake), _create(fake)
    for envelope_id in (signed, refused, cancelled):
        client.distribute(envelope_id)
    fake.sign(signed, SIGNED_AT)
    fake.reject(refused, "Il compenso non è quello concordato")
    client.cancel(cancelled, "Annullato da rebase.")
    assert _outcome(fake, signed, "DOCUMENT_COMPLETED") == Outcome(
        signed, COMPLETED, signed_at=SIGNED_AT
    )
    assert _outcome(fake, refused, "DOCUMENT_REJECTED") == Outcome(
        refused, REJECTED, reason="Il compenso non è quello concordato"
    )
    # A cancellation sets `completedAt` too: it is not a signature date.
    assert fake.webhook(cancelled, "DOCUMENT_CANCELLED")["payload"]["completedAt"] is not None
    assert _outcome(fake, cancelled, "DOCUMENT_CANCELLED") == Outcome(cancelled, CANCELLED)
    for event in ("DOCUMENT_OPENED", "DOCUMENT_SENT", "DOCUMENT_SIGNED", "RECIPIENT_EXPIRED"):
        assert _outcome(fake, signed, event) is None


def test_refresh_reads_the_same_outcomes_from_the_envelope() -> None:
    fake = FakeDocumenso()
    client = fake.client()
    pending, signed, refused, cancelled = (_create(fake) for _ in range(4))
    for envelope_id in (pending, signed, refused, cancelled):
        client.distribute(envelope_id)
    fake.sign(signed, SIGNED_AT)
    fake.reject(refused, "No")
    client.cancel(cancelled, "Annullato da rebase.")
    assert outcome_from_envelope(client.get(pending)) is None
    assert outcome_from_envelope(client.get(signed)) == Outcome(signed, COMPLETED, SIGNED_AT)
    assert outcome_from_envelope(client.get(refused)) == Outcome(refused, REJECTED, reason="No")
    assert outcome_from_envelope(client.get(cancelled)) == Outcome(cancelled, CANCELLED)


def test_no_url_or_no_token_means_no_client() -> None:
    assert client_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    assert client_from_settings(Settings(_env_file=None, documenso_url=BASE)) is None  # type: ignore[call-arg]
    both = Settings(_env_file=None, documenso_url=BASE, documenso_api_token=TOKEN)  # type: ignore[call-arg]
    assert isinstance(client_from_settings(both), DocumensoClient)
    assert Settings(_env_file=None).contracts_mail == "ciao@letsrebase.com"  # type: ignore[call-arg]
```

- [ ] **Step 4: Write the failing real-render test for the copy for signing**

Append to `projects/hub/packages/core/tests/test_contract_render.py` (add `import re`, `import unicodedata`, `from io import BytesIO`, `from pypdf import PdfReader`, and `ContractRenderer`, `text_is_draft` to the `rebase_core.contracts.render` import):

```python
def _words(pdf: bytes) -> str:
    """The PDF's text with every space removed and every ligature undone, so a label is
    found whatever pypdf does with the glyph runs of a small grey word."""
    reader = PdfReader(BytesIO(pdf))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def test_the_copy_for_signing_leaves_the_signing_blanks_labels_undrawn() -> None:
    """Probe § 11.9: the grey label under a blank the signing site fills shows through
    the signature and the date. The copy that goes out lays it out and does not draw it,
    so every blank stays exactly where the plain copy has it."""
    data = _example()
    plain = _words(render("lettera-di-incarico", data).pdf)
    signing = _words(render("lettera-di-incarico", data, signing=True).pdf)
    assert "firmaprofessionista" in plain and "datafirma" in plain
    assert "firmaprofessionista" not in signing and "datafirma" not in signing
    assert signature_blanks("lettera-di-incarico", data, signing=True) == signature_blanks(
        "lettera-di-incarico", data
    )


def test_the_renderer_says_whether_a_text_is_a_draft_as_its_render_does() -> None:
    for document in DOCUMENTS:
        assert text_is_draft(document) is render(document, _example()).draft
        assert ContractRenderer().is_draft(document) is text_is_draft(document)
```

- [ ] **Step 5: Run the new tests to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_http.py projects/hub/packages/core/tests/test_mail.py projects/hub/packages/core/tests/test_documenso.py projects/hub/packages/core/tests/test_contract_render.py`
Expected: FAIL: `ImportError: cannot import name 'urllib_download_call'`, `cannot import name 'Attachment'`, `No module named 'rebase_core.documenso'` (collected through `fakes_documenso`), and `render() got an unexpected keyword argument 'signing'`.

- [ ] **Step 6: The download seam** (`projects/hub/packages/core/src/rebase_core/http.py`)

After `MAX_BODY_BYTES = 1_048_576` add:

```python
# The sealed contracts the Documenso client downloads (REB-387) run to a few hundred
# kilobytes, and a long framework agreement with Documenso's certificate page must never
# meet the cap above: that client reads through `urllib_download_call`, capped here.
MAX_DOWNLOAD_BYTES = 16 * 1_048_576
```

Replace the body of `urllib_call` with a shared `_open` and the two public calls:

```python
def _open(
    method: str, url: str, headers: dict[str, str], body: bytes, cap: int
) -> tuple[int, bytes]:
    sent = {"User-Agent": USER_AGENT, **headers}
    # `None` rather than `b""` for a bodiless request: with `data=b""` urllib writes
    # `Content-Length: 0` and a form content type on a GET, which some proxies refuse.
    request = urllib.request.Request(url, data=body or None, headers=sent, method=method)
    try:
        with _OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(cap + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(cap + 1)


def urllib_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """`urllib`, no dependency.

    An HTTP error is a *status*, not an exception: `urllib` raises `HTTPError` for a
    4xx, and unwrapping it here is what lets `send` treat "OpenAI refused the event"
    and "OpenAI accepted it" through one path. The body is read up to one byte past the
    cap, so the caller can tell "too long" from "exactly the cap".
    """
    return _open(method, url, headers, body, MAX_BODY_BYTES)


def urllib_download_call(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, bytes]:
    """`urllib_call` with room for a file: the Documenso client's seam (REB-387), whose
    downloads are sealed PDFs rather than a few fields of JSON."""
    return _open(method, url, headers, body, MAX_DOWNLOAD_BYTES)
```

- [ ] **Step 7: Attachments and the two contract mails** (`projects/hub/packages/core/src/rebase_core/mail.py`)

Add `import base64` to the imports. Replace the `Mail` dataclass with:

```python
@dataclass(frozen=True)
class Attachment:
    """A file a mail carries: the name the reader saves it under, and its bytes."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class Mail:
    """The text is required and is what arrives everywhere; the HTML, when there is one,
    is the same words in the landing's box for the clients that render it. Since REB-387
    the signed contracts leave as attachments; every other mail carries none."""

    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()
```

In `ResendSender.send`, after `if mail.html is not None: body["html"] = mail.html`, add:

```python
        if mail.attachments:
            # Resend takes each file as base64 inside the JSON body, beside its name.
            body["attachments"] = [
                {
                    "filename": item.filename,
                    "content": base64.b64encode(item.content).decode("ascii"),
                }
                for item in mail.attachments
            ]
```

Append at the end of the module:

```python
# ---- the contracts (REB-387) --------------------------------------------------------------
#
# The hub sends the signing mails itself: Documenso distributes with
# `distributionMethod: NONE` and every mail of its own switched off, so these are the only
# mails a freelancer gets about a contract, in the same box as the magic link.


def document_name(kind: str, numero: str | None) -> str:
    """How a mail names a contract: «contratto quadro rebase», «lettera di incarico n.
    2026-001». `kind` is `quadro` or `lettera`."""
    if kind == "quadro":
        return "contratto quadro rebase"
    return f"lettera di incarico n. {numero}"


def _article(kind: str) -> str:
    return "il" if kind == "quadro" else "la"


def signing_request_mail(
    to: str, nome: str, kind: str, numero: str | None, signing_url: str
) -> Mail:
    """One mail per document to sign (spec § 6): the subject names it, one paragraph says
    what it is, one button opens the signing site. The link is the only way the
    document reaches the person, and it goes into an attribute and into text: escaped
    both times, like the magic link."""
    e = html_escape.escape
    name = document_name(kind, numero)
    what = (
        "il contratto quadro con rebase: le regole di ogni lavoro che fai tramite noi. "
        "Si firma una volta e si rinnova da solo ogni dodici mesi"
        if kind == "quadro"
        else f"la {name}: il lavoro, le date e il compenso che abbiamo concordato"
    )
    paragraph = (
        f"ti mandiamo da firmare {what}. Si firma online, senza creare un account, dal "
        "bottone qui sotto; la copia firmata ti arriva per email e resta nella tua area "
        "su rebase."
    )
    greeting = f"Ciao {nome}," if nome else "Ciao,"
    subject = f"Da firmare: {name}"
    text = f"{greeting}\n\n{paragraph}\n\n{signing_url}\n\nNoi di rebase\n"
    safe_url = e(signing_url, quote=True)
    small = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};'
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0 0 24px 0;">{e(paragraph)}</p>',
            _button(safe_url, "Firma il documento"),
            f'<p {small}word-break:break-all;">'
            "Se il bottone non si apre, copia questo indirizzo nel browser:<br>"
            f"{_quiet_link(safe_url, safe_url)}</p>",
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(to=to, subject=subject, text=text, html=_frame(subject, body))


def signed_copy_mail(
    to: str,
    *,
    kind: str,
    numero: str | None,
    attachment: Attachment,
    nome: str,
    cognome: str,
    for_rebase: bool = False,
) -> Mail:
    """The sealed copy, attached (spec § 6): to the freelancer, and with `for_rebase` to
    rebase's contracts address. Its last page is Documenso's certificate of the
    signature, in English (probe § 7): the mail says so, so nobody takes it for a stray
    page."""
    e = html_escape.escape
    name = document_name(kind, numero)
    certificate = "L'ultima pagina, in inglese, è il certificato della firma elettronica."
    signed = "Firmato" if kind == "quadro" else "Firmata"
    if for_rebase:
        greeting = "Ciao,"
        paragraph = (
            f"{nome} {cognome} ha firmato {_article(kind)} {name}: la copia firmata è in "
            f"allegato. {certificate}"
        )
        subject = f"{signed} da {nome} {cognome}: {name}"
    else:
        greeting = f"Ciao {nome}," if nome else "Ciao,"
        paragraph = (
            f"hai firmato {_article(kind)} {name}: la copia firmata è in allegato, e la "
            f"trovi anche nella tua area su rebase. {certificate}"
        )
        subject = f"{signed}: {name}"
    text = f"{greeting}\n\n{paragraph}\n\nNoi di rebase\n"
    body = "\n".join(
        (
            f'<p style="margin:0 0 20px 0;">{e(greeting)}</p>',
            f'<p style="margin:0;">{e(paragraph)}</p>',
            '<p style="margin:24px 0 0 0;">Noi di rebase</p>',
        )
    )
    return Mail(
        to=to,
        subject=subject,
        text=text,
        html=_frame(subject, body),
        attachments=(attachment,),
    )
```

- [ ] **Step 8: The two errors** (`projects/hub/packages/core/src/rebase_core/errors.py`, append)

```python
class DocumensoFailed(DomainError):
    """Documenso refused a call or did not answer (REB-387). `message` is a sentence for
    the admin, carrying Documenso's own `message` at most and never the stack trace its
    error bodies include; `detail` is for the log. A 502 in the API."""

    code = "documenso_failed"

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.detail = detail


class SigningUnavailable(DomainError):
    """This environment cannot send for signature: no Documenso, no mail, or no data for
    whoever signs for rebase. A 503 with the sentence, like the member area without a
    mail key."""

    code = "signing_unavailable"
```

- [ ] **Step 9: The settings** (`projects/hub/packages/core/src/rebase_core/config.py`, after phase 2's `signer_json`)

```python
    # --- the signing site: Documenso (REB-387) ---------------------------------------
    # The instance's base URL as this process reaches it (production: the compose
    # service, `http://documenso:3000`; preview: `https://firma.letsrebase.com`), and the
    # API token of this environment's own Documenso user and team: one user per
    # environment, because a token reads and cancels every envelope of its user's teams
    # (probe § 8). Either empty: signing is off and «Invia per la firma» answers 503.
    documenso_url: str = ""
    documenso_api_token: str = ""
    # The secret typed into this environment's Documenso webhook, which Documenso sends
    # verbatim as `X-Documenso-Secret`. Empty: the webhook answers 503.
    documenso_webhook_secret: str = ""
    # Where rebase's own copy of every signed contract is mailed.
    contracts_mail: str = "ciao@letsrebase.com"
```

- [ ] **Step 10: The client** (`projects/hub/packages/core/src/rebase_core/documenso.py`)

```python
"""Documenso, the signing site, over API v2: the five calls the hub makes (REB-387).

The shapes are the ones phase 1 saw a self-hosted `documenso/documenso:v2.18.0` answer
(`docs/superpowers/specs/2026-09-23-documenso-probe.md` § 4 and § 5). The hub creates an
envelope from a PDF with the freelancer as its one signer and the fields where the
template's blanks landed, reads back the envelope item's id (the create answers only the
envelope's, and the sealed copy is downloaded by item), and distributes it with
`distributionMethod: NONE`, which sends no mail and answers the signing URL: the mail is
the hub's own. Every `emailSettings` flag is off, the owner's «Signing Complete!»
included, and the links never expire.

Everything goes through the `HttpCall` seam, so the tests hand `FakeDocumenso`. A
refusal becomes `DocumensoFailed` carrying Documenso's `message` alone.
"""

import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.config import Settings
from rebase_core.contracts.fields import ContractFailed
from rebase_core.contracts.render import A4_HEIGHT_PT, A4_WIDTH_PT, SignatureBlank
from rebase_core.errors import DocumensoFailed
from rebase_core.http import (
    MAX_DOWNLOAD_BYTES,
    NETWORK_ERROR_STATUS,
    HttpCall,
    urllib_download_call,
)

API_PREFIX = "/api/v2"
SIGNATURE, DATE = "SIGNATURE", "DATE"
# The two blanks the freelancer fills, by the label the template prints under them.
FIELD_BY_BLANK = {"firma professionista": SIGNATURE, "data firma": DATE}
# The probe's `fieldMeta` for the date: nine points, left-aligned on the rule.
DATE_META: dict[str, Any] = {"type": "date", "fontSize": 9, "textAlign": "left"}
EMAIL_SETTINGS_OFF = {
    "recipientSigningRequest": False,
    "recipientRemoved": False,
    "recipientSigned": False,
    "documentPending": False,
    "documentCompleted": False,
    "documentDeleted": False,
    "ownerDocumentCompleted": False,
    "ownerRecipientExpired": False,
    "ownerDocumentCreated": False,
}
META: dict[str, Any] = {
    "distributionMethod": "NONE",
    "language": "it",
    "timezone": "Europe/Rome",
    "dateFormat": "dd/MM/yyyy",
    "envelopeExpirationPeriod": {"disabled": True},
    "emailSettings": EMAIL_SETTINGS_OFF,
}
COMPLETED, REJECTED, CANCELLED = "completed", "rejected", "cancelled"
UNREACHABLE = "Documenso non risponde: riprova tra qualche minuto."
UNREADABLE = "Documenso ha dato una risposta che non riconosco: riprova tra qualche minuto."


# ---- the fields -----------------------------------------------------------------------


def _percent(value: float, whole: float) -> float:
    return round(value / whole * 100, 3)


def fields_from_blanks(blanks: Sequence[SignatureBlank]) -> list[dict[str, Any]]:
    """Documenso's fields from the blanks Typst placed: percentages of the A4 page from
    its top-left corner, the probe's conversion (§ 9). A blank the signing site does not
    fill is a template the hub does not know, and a document with no signature field
    could be completed with nobody signing it: both are refused."""
    fields: list[dict[str, Any]] = []
    for blank in blanks:
        kind = FIELD_BY_BLANK.get(blank.name)
        if kind is None:
            raise ContractFailed(f"a signing blank the hub does not know: {blank.name!r}")
        field: dict[str, Any] = {
            "type": kind,
            "page": blank.page,
            "positionX": _percent(blank.x, A4_WIDTH_PT),
            "positionY": _percent(blank.y, A4_HEIGHT_PT),
            "width": _percent(blank.width, A4_WIDTH_PT),
            "height": _percent(blank.height, A4_HEIGHT_PT),
        }
        if kind == DATE:
            field["fieldMeta"] = dict(DATE_META)
        fields.append(field)
    if not any(field["type"] == SIGNATURE for field in fields):
        raise ContractFailed("the document has no blank for the freelancer's signature")
    return fields


# ---- the client -----------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """What `GET /envelope/{id}` says, as far as the hub reads it (probe § 4). `signed_at`
    and `rejection_reason` are the signer's own, never the envelope's `completedAt`,
    which a cancellation sets too."""

    id: str
    status: str
    item_id: str
    external_id: str | None
    signed_at: datetime | None
    rejection_reason: str | None


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _signer(recipients: object) -> dict[str, Any]:
    if isinstance(recipients, list):
        for recipient in recipients:
            if isinstance(recipient, dict) and recipient.get("role", "SIGNER") == "SIGNER":
                return recipient
    return {}


def _refusal(answer: bytes) -> str:
    """Documenso's own sentence, and never the stack trace its error bodies carry."""
    try:
        message = json.loads(answer).get("message")
    except (ValueError, AttributeError):
        message = None
    if isinstance(message, str) and message.strip():
        return f"Documenso ha rifiutato la richiesta: {message.strip()[:300]}"
    return "Documenso ha rifiutato la richiesta."


def _json_body(value: dict[str, Any]) -> bytes:
    return json.dumps(value).encode()


def _multipart(payload: dict[str, Any], filename: str, pdf: bytes) -> tuple[str, bytes]:
    """`payload` as a JSON string and the PDF as `files`, the two parts the create takes.
    The file name is the hub's own (`lettera-di-incarico-2026-001.pdf`), ASCII, and
    becomes the envelope item's title and the sealed copy's name with `_signed`."""
    boundary = f"rebase-{secrets.token_hex(12)}"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="payload"\r\n\r\n'
        f"{json.dumps(payload)}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", head + pdf + tail


class DocumensoClient:
    def __init__(self, base_url: str, token: str, http: HttpCall = urllib_download_call) -> None:
        self.api = base_url.rstrip("/") + API_PREFIX
        self.token = token
        self.http = http

    def create(
        self,
        *,
        title: str,
        external_id: str,
        filename: str,
        pdf: bytes,
        signer_email: str,
        signer_name: str,
        fields: list[dict[str, Any]],
    ) -> str:
        """The envelope, `DRAFT`, with the freelancer as its one `SIGNER`: its id."""
        payload = {
            "type": "DOCUMENT",
            "title": title,
            "externalId": external_id,
            "recipients": [
                {"email": signer_email, "name": signer_name, "role": "SIGNER", "fields": fields}
            ],
            "meta": META,
        }
        content_type, body = _multipart(payload, filename, pdf)
        answer = self._json("POST", "/envelope/create", body, content_type)
        envelope_id = answer.get("id")
        if not isinstance(envelope_id, str) or not envelope_id:
            raise DocumensoFailed(UNREADABLE, f"create answered {answer!r}")
        return envelope_id

    def get(self, envelope_id: str) -> Envelope:
        answer = self._json("GET", f"/envelope/{envelope_id}")
        items = answer.get("envelopeItems")
        first = items[0] if isinstance(items, list) and items else None
        item_id = first.get("id") if isinstance(first, dict) else None
        status = answer.get("status")
        if not isinstance(item_id, str) or not isinstance(status, str):
            raise DocumensoFailed(UNREADABLE, f"envelope {envelope_id}: no item or no status")
        signer = _signer(answer.get("recipients"))
        external = answer.get("externalId")
        reason = signer.get("rejectionReason")
        return Envelope(
            id=envelope_id,
            status=status,
            item_id=item_id,
            external_id=external if isinstance(external, str) else None,
            signed_at=_when(signer.get("signedAt")),
            rejection_reason=reason if isinstance(reason, str) and reason else None,
        )

    def distribute(self, envelope_id: str) -> str:
        """`PENDING`, with no mail from Documenso: the signing URL of the one signer."""
        body = _json_body({"envelopeId": envelope_id, "meta": {"distributionMethod": "NONE"}})
        answer = self._json("POST", "/envelope/distribute", body)
        url = _signer(answer.get("recipients")).get("signingUrl")
        if answer.get("success") is not True or not isinstance(url, str) or not url:
            raise DocumensoFailed(UNREADABLE, f"distribute answered no signing URL for {envelope_id}")
        return url

    def download_signed(self, item_id: str) -> bytes:
        _status, body = self._call("GET", f"/envelope/item/{item_id}/download?version=signed")
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise DocumensoFailed(UNREADABLE, f"the signed copy of {item_id} passes the cap")
        if not body.startswith(b"%PDF-"):
            raise DocumensoFailed(UNREADABLE, f"the signed copy of {item_id} is not a PDF")
        return body

    def cancel(self, envelope_id: str, reason: str) -> None:
        """Only a `PENDING` envelope can be cancelled; a draft answers 400 (probe § 4)."""
        body = _json_body({"envelopeId": envelope_id, "reason": reason})
        answer = self._json("POST", "/envelope/cancel", body)
        if answer.get("success") is not True:
            raise DocumensoFailed(UNREADABLE, f"cancel answered {answer!r}")

    def _call(
        self, method: str, path: str, body: bytes = b"", content_type: str | None = None
    ) -> tuple[int, bytes]:
        headers = {"Authorization": self.token}
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            status, answer = self.http(method, f"{self.api}{path}", headers, body)
        except Exception as exc:  # urllib raises on a refused connection or a timeout
            raise DocumensoFailed(UNREACHABLE, f"{method} {path}: {exc!r}") from exc
        if status == NETWORK_ERROR_STATUS:
            raise DocumensoFailed(UNREACHABLE, f"{method} {path}: no response")
        if not 200 <= status < 300:
            raise DocumensoFailed(_refusal(answer), f"{method} {path}: HTTP {status}")
        return status, answer

    def _json(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        _status, answer = self._call(method, path, body, content_type if body else None)
        try:
            parsed = json.loads(answer)
        except ValueError as exc:
            raise DocumensoFailed(UNREADABLE, f"{method} {path}: not JSON") from exc
        if not isinstance(parsed, dict):
            raise DocumensoFailed(UNREADABLE, f"{method} {path}: not a JSON object")
        return parsed


def client_from_settings(settings: Settings) -> DocumensoClient | None:
    """`None` without a URL or a token: signing is off on this environment, and says so."""
    if not settings.documenso_url or not settings.documenso_api_token:
        return None
    return DocumensoClient(settings.documenso_url, settings.documenso_api_token)


# ---- what an envelope's outcome is --------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What happened to an envelope, from the webhook or from «Aggiorna stato»:
    `completed` (with the signer's date), `rejected` (with the signer's reason) or
    `cancelled`."""

    envelope_id: str
    kind: str
    signed_at: datetime | None = None
    reason: str | None = None


class WebhookRecipient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    role: str = "SIGNER"
    signed_at: datetime | None = Field(default=None, alias="signedAt")
    rejection_reason: str | None = Field(default=None, alias="rejectionReason")


class WebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    envelope_id: str = Field(alias="envelopeId", min_length=1, max_length=100)
    status: str | None = None
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    recipients: list[WebhookRecipient] = Field(default_factory=list)


class WebhookBody(BaseModel):
    """What Documenso POSTs (probe § 5): the event, upper case, and the envelope. The rest
    of the body (`createdAt`, `webhookEndpoint`, the numeric `id`, the recipients'
    tokens) is read by nobody."""

    model_config = ConfigDict(extra="ignore")

    event: str = Field(max_length=60)
    payload: WebhookEnvelope


def outcome_from_webhook(body: WebhookBody) -> Outcome | None:
    """The three events that move a document; every other one (created, sent, opened,
    signed by one of several, reminders, templates) moves nothing."""
    envelope = body.payload
    signer = next((r for r in envelope.recipients if r.role == "SIGNER"), None)
    if body.event == "DOCUMENT_COMPLETED":
        signed_at = signer.signed_at if signer is not None else None
        if signed_at is None and envelope.status == "COMPLETED":
            signed_at = envelope.completed_at
        return Outcome(envelope.envelope_id, COMPLETED, signed_at=signed_at)
    if body.event == "DOCUMENT_REJECTED":
        reason = signer.rejection_reason if signer is not None else None
        return Outcome(envelope.envelope_id, REJECTED, reason=reason)
    if body.event == "DOCUMENT_CANCELLED":
        return Outcome(envelope.envelope_id, CANCELLED)
    return None


def outcome_from_envelope(envelope: Envelope) -> Outcome | None:
    """«Aggiorna stato»: the same outcomes, read from the envelope's status."""
    if envelope.status == "COMPLETED":
        return Outcome(envelope.id, COMPLETED, signed_at=envelope.signed_at)
    if envelope.status == "REJECTED":
        return Outcome(envelope.id, REJECTED, reason=envelope.rejection_reason)
    if envelope.status == "CANCELLED":
        return Outcome(envelope.id, CANCELLED)
    return None
```

- [ ] **Step 11: The copy for signing in the renderer** (`projects/hub/packages/core/src/rebase_core/contracts/render.py`)

Replace the `Renderer` protocol and `ContractRenderer` with:

```python
class Renderer(Protocol):
    """The seam the services take, so a test hands `FakeRenderer` and needs no binary.

    `signing` asks for the copy that goes out for signature (REB-387 phase 3): the same
    page, with the labels under the blanks the signing site fills laid out and not drawn,
    since they show through the signature and the date (probe § 11.9). `is_draft` says
    whether the text in the package today is `status: draft`, which never leaves."""

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered: ...

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]: ...

    def is_draft(self, document: str) -> bool: ...


class ContractRenderer:
    """The production renderer: pandoc and Typst on this machine."""

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        return render(document, data, signing=signing)

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        return signature_blanks(document, data, signing=signing)

    def is_draft(self, document: str) -> bool:
        return text_is_draft(document)
```

After `text_version`, add:

```python
def text_is_draft(document: str) -> bool:
    """Whether the text in the package today says `status: draft` (spec § 1f)."""
    source = text_path(document)
    return is_draft(source.read_text(encoding="utf-8"), source.name)
```

Give `_typst_source` a last parameter `signing: bool = False` and add, right after the `--variable=draft:...` argument of the pandoc call:

```python
            f"--variable=forsigning:{'true' if signing else 'false'}",
```

Give `render` and `signature_blanks` the parameter `signing: bool = False` after `data`, and pass it on: `_typst_source(document, data, workdir, signing)` in both.

In `contract.typ.template`, right after `#let draft = $draft$` add:

```
// The copy that goes out for signature (`render(..., signing=True)`): the labels under
// the blanks the signing site fills are laid out and left undrawn, so the signature and
// the date do not sit on them and every blank keeps its place (probe § 11.9).
#let for-signing = $forsigning$
```

and in `field`, replace the `else` branch's last line

```
    box(width: width, stroke: stroke, inset: inset, [#mark<signing-blank>] + label)
```

with

```
    let shown = if for-signing { hide(label) } else { label }
    box(width: width, stroke: stroke, inset: inset, [#mark<signing-blank>] + shown)
```

`measure` still measures the box with the label, and `hide` keeps its layout, so the marker's size and position are the plain copy's. If Step 14 shows pypdf still reading the hidden label (Typst 0.14's tagged export keeping hidden text), use an empty box of the label's size instead, which draws nothing and keeps the layout just the same: `let shown = if for-signing { box(width: measure(label).width, height: measure(label).height) } else { label }`. The test that compares the two copies' blanks holds either way.

- [ ] **Step 12: Teach the fakes the new protocol** (`projects/hub/packages/core/tests/fakes_contracts.py`)

Change the render import to `from rebase_core.contracts.render import Rendered, SignatureBlank, text_path, text_version` and replace both classes with:

```python
# Where the phase 1 probe measured the letter's two blanks (probe § 9), and the same shape
# for the framework agreement's three: what `signature_blanks` answers without Typst.
FAKE_BLANKS: dict[str, list[SignatureBlank]] = {
    "lettera-di-incarico": [
        SignatureBlank("data firma", 2, 165.811, 647.241, 85.039, 6.660),
        SignatureBlank("firma professionista", 2, 303.638, 707.281, 155.906, 22.660),
    ],
    "contratto-quadro": [
        SignatureBlank("data firma", 5, 165.811, 520.0, 85.039, 6.660),
        SignatureBlank("firma professionista", 5, 303.638, 580.0, 155.906, 22.660),
        SignatureBlank("firma professionista", 6, 303.638, 300.0, 155.906, 22.660),
    ],
}


@dataclass
class FakeRenderer:
    calls: list[tuple[str, dict[str, Value]]] = field(default_factory=list)
    draft: bool = True
    # Whether each `render` asked for the copy that goes out for signature.
    signing: list[bool] = field(default_factory=list)

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        filled = checked(dict(data))
        self.calls.append((document, dict(data)))
        self.signing.append(signing)
        asked = dict.fromkeys(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        blank = [key for key in asked if filled.get(key) in (None, "")]
        return Rendered(
            pdf=b"%PDF-1.7 fake " + document.encode(),
            blank=blank,
            version=text_version(document),
            draft=self.draft,
        )

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        checked(dict(data))
        return list(FAKE_BLANKS[document])

    def is_draft(self, document: str) -> bool:
        text_path(document)
        return self.draft


@dataclass
class FailingRenderer:
    """Fails like a machine without pandoc, on `document` or on every one."""

    document: str | None = None
    draft: bool = True

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        if self.document is None or document == self.document:
            raise ContractFailed("pandoc is not on PATH")
        return FakeRenderer(draft=self.draft).render(document, data, signing=signing)

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        if self.document is None or document == self.document:
            raise ContractFailed("typst is not on PATH")
        return list(FAKE_BLANKS[document])

    def is_draft(self, document: str) -> bool:
        return self.draft
```

- [ ] **Step 13: Carry the settings through compose and `.env.example`**

`projects/hub/docker-compose.yml`, at the end of `x-api-environment` (after phase 2's `REBASE_SIGNER_JSON`):

```yaml
  # The signing site (REB-387): Documenso's URL as this stack reaches it, this
  # environment's own token and webhook secret, and where rebase's signed copies go.
  # Empty URL or token: signing is off and says so.
  REBASE_DOCUMENSO_URL: ${REBASE_DOCUMENSO_URL:-}
  REBASE_DOCUMENSO_API_TOKEN: ${REBASE_DOCUMENSO_API_TOKEN:-}
  REBASE_DOCUMENSO_WEBHOOK_SECRET: ${REBASE_DOCUMENSO_WEBHOOK_SECRET:-}
  REBASE_CONTRACTS_MAIL: ${REBASE_CONTRACTS_MAIL:-ciao@letsrebase.com}
```

`projects/hub/.env.example`, after phase 2's `REBASE_SIGNER_JSON=` line:

```
# --- the signing site: Documenso (REB-387) -------------------------------------------
# One Documenso instance runs beside production (REB-393); each environment signs through
# a Documenso user of its own, with its own team, API token and webhook, made by hand in
# Documenso's UI (probe § 3 and § 8).
#   production: REBASE_DOCUMENSO_URL=http://documenso:3000 (the compose service)
#   preview:    REBASE_DOCUMENSO_URL=https://firma.letsrebase.com
# Empty URL or token: «Invia per la firma» answers 503 with a sentence. Empty secret: the
# webhook answers 503. The secret is the value typed into the webhook's form.
REBASE_DOCUMENSO_URL=
REBASE_DOCUMENSO_API_TOKEN=
REBASE_DOCUMENSO_WEBHOOK_SECRET=
# Where rebase's copy of every signed contract is mailed.
REBASE_CONTRACTS_MAIL=ciao@letsrebase.com
```

- [ ] **Step 14: Run the tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_http.py projects/hub/packages/core/tests/test_mail.py projects/hub/packages/core/tests/test_documenso.py projects/hub/packages/core/tests/test_contract_render.py projects/hub/packages/core/tests/test_matches.py`
Expected: all pass (`test_matches.py` proves phase 2's services still work with the widened fakes).

- [ ] **Step 15: Whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green. If mypy complains about `part.get_payload()` in `fakes_documenso._form`, it is typeshed's `Any`: annotate the loop variable as `part: Message` from `email.message` rather than adding an ignore.

- [ ] **Step 16: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/http.py \
  projects/hub/packages/core/src/rebase_core/mail.py \
  projects/hub/packages/core/src/rebase_core/errors.py \
  projects/hub/packages/core/src/rebase_core/config.py \
  projects/hub/packages/core/src/rebase_core/documenso.py \
  projects/hub/packages/core/src/rebase_core/contracts/render.py \
  projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template \
  projects/hub/packages/core/tests/fakes_contracts.py \
  projects/hub/packages/core/tests/fakes_documenso.py \
  projects/hub/packages/core/tests/test_documenso.py \
  projects/hub/packages/core/tests/test_http.py \
  projects/hub/packages/core/tests/test_mail.py \
  projects/hub/packages/core/tests/test_contract_render.py \
  projects/hub/docker-compose.yml projects/hub/.env.example
git commit -F - <<'EOF'
feat(hub): the pieces a signature needs: attachments, a Documenso client, the copy to sign

I give Mail optional attachments, sent base64 to Resend, and add the two
contract mails: one per document to sign, with the link as its one button,
and the signed copy to the freelancer and to rebase. The Documenso client
makes the five calls the phase 1 probe recorded on the HTTP seam, with
distributionMethod NONE and every Documenso mail off, and shows an admin
only Documenso's message. The renderer typesets a copy for signing that
leaves the labels under the signing blanks undrawn, and says whether a text
is a draft. Four REBASE_* settings configure it; a fake Documenso tests it.

REB-390.
EOF
```

---
### Task 2: Send a match for signature (REB-390, part 2 of 2)

**Files:**
- Create: `projects/hub/packages/core/migrations/versions/0018_documenso_envelopes.py`
- Modify: `projects/hub/packages/core/src/rebase_core/models.py` (`ContractDocument` and one constant)
- Modify: `projects/hub/packages/core/src/rebase_core/matches.py` (two methods before `# ---- lookups`)
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py` (append `SendReport`)
- Create: `projects/hub/packages/core/src/rebase_core/signing.py`
- Modify: `projects/hub/packages/core/tests/test_migrations.py`
- Create: `projects/hub/packages/core/tests/test_signing.py`
- Modify: `projects/hub/apps/api/src/rebase_api/deps.py` (append), `main.py` (the error handler), `routers/matches.py` (one route)
- Create: `projects/hub/apps/api/tests/contract_flow.py`, `projects/hub/apps/api/tests/test_signing_api.py`
- Modify: `projects/hub/apps/web/src/lib/api.ts`, `lib/contracts.ts`, `lib/contracts.test.ts`, `pages/admin/Contratti.tsx`, `pages/admin/Contratti.test.tsx`, `pages/admin/CreaMatch.tsx`, `pages/admin/CreaMatch.test.tsx`

**Interfaces:**
- Consumes: Task 1 (`DocumensoClient`, `fields_from_blanks`, `client_from_settings`, `DocumensoFailed`, `SigningUnavailable`, `document_name`, `signing_request_mail`, `Renderer.render(..., signing=True)`, `Renderer.signature_blanks`, `Renderer.is_draft`, `FakeRenderer`, `FakeDocumenso`, `Settings.contracts_mail`); phase 2 (`MatchService` and its private `_freelancer`, `_fiscal`, `_quadro_data`, `_rebase_fields`, `_signing_fields`, `_document`, `_renderer`, `get`, `cancel`; `matches.{ENTITY, QUADRO, LETTERA, DOCUMENT_BY_KIND, _full_name}`; `framework.{active_framework, pending_framework, rome_today, signed_on}`; `contracts.fields.{italian_date, signer_data}`; `MatchRead`; `deps.{RendererDep, SenderDep, SettingsDep, SessionDep, AdminDep}`; the web's `admin.createMatch`, `Match`, `failureOf`, `PreviewStep`, `AdminCreaMatch`, `MatchesSection`, `AdminContratti`).
- Produces:
  - Migration `0018`: `contract_documents.documenso_item_id VARCHAR(100)`, `contract_documents.cancel_reason VARCHAR(500)`, unique index `uq_contract_documents_documenso_id`, check `ck_contract_documents_envelope_item` (`(documenso_id IS NULL) = (documenso_item_id IS NULL)`).
  - `rebase_core.models.CANCEL_REASON_MAX_LENGTH = 500`; `ContractDocument.documenso_item_id: str | None`, `ContractDocument.cancel_reason: str | None`.
  - `MatchService.data_for_sending(document: ContractDocument, today: date, framework: ContractDocument | None) -> dict[str, Value]`; `MatchService.write_framework(freelancer_id: UUID, admin_id: UUID) -> ContractDocument` (flushed, not committed).
  - `rebase_core.contract_schemas.SendReport(match: MatchRead, inviato: str | None, mail_inviata: bool | None)`.
  - `rebase_core.signing`: `FREELANCER = "freelancer"`, `DEFAULT_CONTRACTS_MAIL`, `DRAFT_REFUSED: dict[str, str]`, `NO_DOCUMENSO`, `NO_SENDER`; `SigningService(session: Session, *, renderer: Renderer | None = None, documenso: DocumensoClient | None = None, sender: EmailSender | None = None, signer: Mapping[str, Value] | None = None, contracts_mail: str = DEFAULT_CONTRACTS_MAIL, today: Callable[[], date] = rome_today, now: Callable[[], datetime] = utcnow)` with `.matches: MatchService`, `send_match(match_id: UUID, admin_id: UUID) -> SendReport`, and the helpers later tasks call: `_lock(document_id) -> ContractDocument`, `_lock_match(match_id) -> Match`, `_dispatch(document, framework, sent_by) -> None`, `_mail_signing_request(document) -> bool`, `_owner(freelancer_id) -> User`, `_renderer()`, `_documenso()`, `_sender()`.
  - API test helpers (`projects/hub/apps/api/tests/contract_flow.py`): `PDF`, `ADMIN_EMAIL`, `FREELANCER_EMAIL = "ada@studio.it"`, `MISSING`, `SIGNER`, `FISCAL`, `CLIENTE`, `LETTERA`, `TABLES`, `enter(client, sender, email) -> None`, `draft_match(client, sender) -> dict[str, Any]` (the created `MatchRead` as JSON).
  - HTTP: `POST /api/hub/matches/{match_id}/send` → `SendReport` (401 without the cookie, 409 draft text or nothing left to send, 502 Documenso refused, 503 signing off or signer incomplete).
  - `rebase_api.deps.get_documenso() -> DocumensoClient | None`, `DocumensoDep`, `SigningFactory = Callable[[Session], SigningService]`, `get_signing_factory(...)`, `SigningDep`.
  - Web: `SendReport` type and `admin.sendMatch(matchId)` in `api.ts`; `sendReportMessage(report: SendReport): string` in `lib/contracts.ts`; «Invia per la firma» on «Match e contratti» (`aria-label` «Invia per la firma il match con {azienda}») and enabled on step 5 of «Crea match».

- [ ] **Step 1: Write the failing migration tests** (append to `projects/hub/packages/core/tests/test_migrations.py`)

```python
def test_an_envelope_belongs_to_one_document_and_keeps_its_item(hub_engine: Engine) -> None:
    """REB-387 phase 3: the webhook finds its document by the envelope's id, so an
    envelope is one document's; and the item the sealed copy is downloaded by is stored
    with it or not at all."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) VALUES "
                "(gen_random_uuid(), 'ck-envelopes@studio.it', 'A', 'B', 'admin', true) "
                "RETURNING id"
            )
        ).scalar()
        freelancer_id = connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, links, stato, compilata_da) VALUES "
                "(gen_random_uuid(), :user_id, '[]', 'nuovo', 'persona') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar()

        def _document(envelope: str | None, item: str | None) -> None:
            connection.execute(
                text(
                    "INSERT INTO contract_documents (id, kind, freelancer_id, text_version, "
                    "testo_bozza, data, pdf, stato, documenso_id, documenso_item_id, "
                    "created_by) VALUES (gen_random_uuid(), 'quadro', :freelancer_id, '0.1', "
                    "false, '{}', :pdf, 'inviato', :envelope, :item, :user_id)"
                ),
                {
                    "freelancer_id": freelancer_id,
                    "pdf": b"%PDF-",
                    "envelope": envelope,
                    "item": item,
                    "user_id": user_id,
                },
            )

        for envelope, item in (("envelope_1", None), (None, "envelope_item_1")):
            with pytest.raises(IntegrityError), connection.begin_nested():
                _document(envelope, item)
        with connection.begin_nested():
            _document("envelope_1", "envelope_item_1")
            _document(None, None)
            _document(None, None)
        with pytest.raises(IntegrityError), connection.begin_nested():
            _document("envelope_1", "envelope_item_2")
        outer.rollback()


def test_migration_0018_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0018's statements over columns that already exist, and the
    downgrade leaves 0017's schema: both must work, and the result must be the models'."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0017")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0017'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar() == head_revision()
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_migrations.py -k "envelope or 0018"`
Expected: FAIL, `column "documenso_item_id" of relation "contract_documents" does not exist`, and the head is still 0017.

- [ ] **Step 3: The two columns on the model** (`projects/hub/packages/core/src/rebase_core/models.py`)

After `DOCUMENSO_ID_MAX_LENGTH = 100` add:

```python
# Why a document became `annullato`: the freelancer's own reason when they refused it on
# the signing site, or who cancelled it (REB-387 phase 3).
CANCEL_REASON_MAX_LENGTH = 500
```

In `ContractDocument`, right after `documenso_id`:

```python
    # The envelope *item* the sealed copy is downloaded by: the create answers only the
    # envelope's id, so the hub reads this once, right after (probe § 4 and § 11.7).
    documenso_item_id: Mapped[str | None] = mapped_column(
        String(DOCUMENSO_ID_MAX_LENGTH), default=None
    )
```

and right after `notice_at`:

```python
    cancel_reason: Mapped[str | None] = mapped_column(
        String(CANCEL_REASON_MAX_LENGTH), default=None
    )
```

In its `__table_args__` add, after `Index("uq_contract_documents_numero", ...)`:

```python
        # The webhook finds its document by the envelope: one envelope, one document.
        Index("uq_contract_documents_documenso_id", "documenso_id", unique=True),
        CheckConstraint(
            "(documenso_id IS NULL) = (documenso_item_id IS NULL)",
            name="ck_contract_documents_envelope_item",
        ),
```

Replace the last sentence of the class docstring («The Documenso columns and `notice_at` are phase 3's.») with «The Documenso columns, `notice_at` and `cancel_reason` are phase 3's: an envelope and its item are stored together, and an envelope is one document's.»

- [ ] **Step 4: Write the migration** (`projects/hub/packages/core/migrations/versions/0018_documenso_envelopes.py`)

```python
"""contract_documents: the envelope item Documenso seals, and why a document was cancelled

Revision ID: 0018
Revises: 0017

REB-387, phase 3. Documenso answers a create with the envelope's id only, and the sealed
copy is downloaded by the envelope *item*'s id (probe § 4 and § 11.7), so the hub reads
it once after the create and keeps it beside `documenso_id`: the two are set together or
not at all. `cancel_reason` says why a document became `annullato`: the freelancer
refused it on the signing site, with their reason, Documenso cancelled it, or an admin
did. `documenso_id` becomes unique, because the webhook finds its document by it.

Conditional like every migration of this package: a retried deploy passes over what the
previous attempt already added. The check constraint is added through a `pg_constraint`
guard, since `ADD CONSTRAINT` has no `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATEMENTS = (
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS documenso_item_id VARCHAR(100)",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS cancel_reason VARCHAR(500)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_documenso_id "
    "ON contract_documents (documenso_id)",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'ck_contract_documents_envelope_item') THEN "
    "ALTER TABLE contract_documents ADD CONSTRAINT ck_contract_documents_envelope_item "
    "CHECK ((documenso_id IS NULL) = (documenso_item_id IS NULL)); "
    "END IF; END $$",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS ck_contract_documents_envelope_item",
        "DROP INDEX IF EXISTS uq_contract_documents_documenso_id",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS cancel_reason",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS documenso_item_id",
    ):
        op.execute(statement)
```

- [ ] **Step 5: Run the migration tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_migrations.py projects/hub/packages/core/tests/test_warehouse_contract.py`
Expected: all pass, `test_the_migrations_produce_exactly_the_models_schema` included. If the schema diff reports a type or an index, fix the model or the SQL until it is empty; do not relax the test.

- [ ] **Step 6: Write the failing service tests** (`projects/hub/packages/core/tests/test_signing.py`)

```python
"""REB-387 phase 3: a match's documents go out through Documenso and come back signed.
Every test hands `FakeRenderer`, `FakeDocumenso` and a recording mailbox: nothing here
runs pandoc, reaches Documenso or sends a mail."""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import (
    ClienteData,
    FiscalData,
    LetteraFields,
    MatchCreate,
    MatchRead,
    SendReport,
)
from rebase_core.contracts.fields import Value
from rebase_core.contracts.render import Renderer
from rebase_core.db import session_factory
from rebase_core.errors import DocumensoFailed, InvalidState, SigningUnavailable
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import EmailSender, Mail, RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, User
from rebase_core.schemas import CompanyCreate, FreelancerCreate
from rebase_core.signing import SigningService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
TODAY = date(2026, 9, 23)
NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
# 23:30 UTC on 30 September is already 1 October in Rome.
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
CONTRACTS_MAIL = "contratti@rebase.test"
# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER: dict[str, Value] = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
)


class RefusingSender:
    """A provider that turns every mail away, and keeps what it refused."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return False


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(session: Session, email: str = "ada@studio.it", nome: str = "Ada") -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome=nome,
            cognome="Lovelace",
            email=email,
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(session: Session) -> UUID:
    row, _ = CompanyService(session).request(
        CompanyCreate(
            nome_azienda="ACME Srl",
            referente_nome="Wile",
            referente_cognome="E.",
            email="wile@acme.it",
            telefono="+39 345 1234567",
            figura_richiesta="Backend developer",
            progetto="Le API del prodotto, per tre mesi.",
            periodo_da=date(2026, 10, 1),
            durata="3 mesi",
            budget_giornaliero=Decimal("777.77"),
            remoto="remoto",
            numero_risorse=1,
        )
    )
    return row.id


def _fiscal(
    session: Session, freelancer_id: UUID, admin_id: UUID, domicilio: str = "Via Roma 1, Milano"
) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio=domicilio),
        admin_id,
    )


def _setup(session: Session) -> tuple[UUID, UUID, UUID]:
    admin_id, freelancer_id, company_id = _admin(session), _card(session), _request(session)
    _fiscal(session, freelancer_id, admin_id)
    return admin_id, freelancer_id, company_id


def _body(company_id: UUID) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        cliente=ClienteData(
            cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
        ),
        lettera=LetteraFields(
            ruolo="Backend developer",
            attivita="Le API del prodotto.",
            data_inizio=date(2026, 10, 1),
            compenso=Decimal("450"),
            giorni_pagamento=30,
            fine_mese=True,
        ),
    )


def _matches(session: Session, renderer: Renderer) -> MatchService:
    return MatchService(session, renderer, SIGNER, today=lambda: TODAY)


def _signing(
    session: Session,
    renderer: Renderer,
    fake: FakeDocumenso | None,
    sender: EmailSender | None,
    *,
    today: date = TODAY,
    signer: dict[str, Value] | None = None,
) -> SigningService:
    return SigningService(
        session,
        renderer=renderer,
        documenso=fake.client() if fake is not None else None,
        sender=sender,
        signer=SIGNER if signer is None else signer,
        contracts_mail=CONTRACTS_MAIL,
        today=lambda: today,
        now=lambda: NOW,
    )


def _documents(
    session: Session, freelancer_id: UUID, kind: str | None = None
) -> list[ContractDocument]:
    session.expire_all()
    stmt = select(ContractDocument).where(ContractDocument.freelancer_id == freelancer_id)
    if kind is not None:
        stmt = stmt.where(ContractDocument.kind == kind)
    return list(session.scalars(stmt.order_by(ContractDocument.created_at, ContractDocument.id)))


def _framework_of(session: Session, freelancer_id: UUID) -> ContractDocument:
    return _documents(session, freelancer_id, "quadro")[-1]


def _letter_of(session: Session, match_id: UUID) -> ContractDocument:
    session.expire_all()
    return session.scalars(
        select(ContractDocument).where(ContractDocument.match_id == match_id)
    ).one()


def _active_framework(
    session: Session, freelancer_id: UUID, admin_id: UUID, signed_at: datetime = SIGNED_AT
) -> ContractDocument:
    """A framework agreement as a signature leaves one."""
    document = ContractDocument(
        kind="quadro",
        freelancer_id=freelancer_id,
        text_version="0.1",
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato="firmato",
        signed_at=signed_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


def _draft(
    session: Session, renderer: Renderer, freelancer_id: UUID, company_id: UUID, admin_id: UUID
) -> MatchRead:
    return _matches(session, renderer).create(freelancer_id, _body(company_id), admin_id)


def test_the_first_send_hands_documenso_the_framework_and_the_letter_waits(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.inviato, report.mail_inviata) == ("quadro", True)
    assert (report.match.stato, report.match.lettera.stato) == ("in_firma", "in_attesa")
    quadro = _framework_of(clean, freelancer_id)
    [envelope] = fake.envelopes.values()
    assert quadro.stato == "inviato"
    assert (quadro.documenso_id, quadro.documenso_item_id) == (envelope.id, envelope.item_id)
    assert quadro.signing_url == f"https://firma.letsrebase.test/sign/{envelope.token}"
    assert (quadro.sent_at, quadro.sent_by) == (NOW, admin_id)
    assert envelope.payload["title"] == "Contratto quadro rebase"
    assert envelope.payload["externalId"] == str(quadro.id)
    assert envelope.filename == "contratto-quadro-v0.1.pdf"
    assert envelope.payload["meta"]["distributionMethod"] == "NONE"
    assert not any(envelope.payload["meta"]["emailSettings"].values())
    fields = envelope.payload["recipients"][0]["fields"]
    assert [field["type"] for field in fields] == ["DATE", "SIGNATURE", "SIGNATURE"]
    assert envelope.payload["recipients"][0]["email"] == "ada@studio.it"
    assert renderer.signing[-1] is True
    [mail] = sender.sent
    assert (mail.to, mail.subject) == ("ada@studio.it", "Da firmare: contratto quadro rebase")
    assert quadro.signing_url in mail.text
    assert AdminActionService(clean).timeline("match", match.id)[0].kind == "documents_sent"


def test_the_sent_copy_says_the_day_it_left_and_prints_the_tax_data_saved_since_the_draft(
    clean: Session,
) -> None:
    """Review Focus 5: the draft was saved on the 23rd with the old address; the admin
    corrected the tax data and sends on the 25th. What leaves is today's."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _fiscal(clean, freelancer_id, admin_id, domicilio="Corso Como 1, Milano")

    _signing(clean, renderer, fake, RecordingSender(), today=date(2026, 9, 25)).send_match(
        match.id, admin_id
    )

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.data["firma-rebase"] == "Documento emesso da rebase il 25 settembre 2026"
    assert quadro.data["professionista-domicilio"] == "Corso Como 1, Milano"
    document, data = renderer.calls[-1]
    assert document == "contratto-quadro" and data == quadro.data
    assert quadro.pdf == b"%PDF-1.7 fake contratto-quadro"
    assert quadro.testo_bozza is False


def test_with_an_active_framework_the_letter_leaves_and_cites_its_signature(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "generato"

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.inviato, report.match.lettera.stato) == ("lettera", "inviato")
    letter = _letter_of(clean, match.id)
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    [envelope] = fake.envelopes.values()
    assert envelope.payload["title"] == f"Lettera di incarico n. {letter.numero}"
    assert envelope.filename == f"lettera-di-incarico-{letter.numero}.pdf"
    assert [mail.subject for mail in sender.sent] == [
        f"Da firmare: lettera di incarico n. {letter.numero}"
    ]


def test_a_letter_waiting_on_a_framework_already_out_for_signature_sends_nothing_new(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    first = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    second = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(first.id, admin_id)

    report = signing.send_match(second.id, admin_id)

    assert (report.inviato, report.mail_inviata) == (None, None)
    assert (report.match.stato, report.match.lettera.stato) == ("in_firma", "in_attesa")
    assert len(fake.envelopes) == 1 and len(sender.sent) == 1


def test_two_matches_sent_at_once_send_the_framework_once(
    hub_engine: Engine, clean: Session
) -> None:
    """Review Focus 3: two admins send two matches of one freelancer at the same moment,
    and both need the same framework agreement. The second send waits on the framework's
    row, then finds it out for signature: one envelope, and the second letter waits."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    second = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    framework_id = _framework_of(clean, freelancer_id).id
    factory = session_factory(hub_engine)
    first_admin, second_admin = factory(), factory()
    reports: list[SendReport] = []

    def send_second() -> None:
        service = _signing(second_admin, renderer, fake, RecordingSender())
        reports.append(service.send_match(second.id, admin_id))

    try:
        # The first admin's send, caught holding the framework's row and not sent yet.
        held = first_admin.scalars(
            select(ContractDocument).where(ContractDocument.id == framework_id).with_for_update()
        ).one()
        worker = threading.Thread(target=send_second)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second send did not wait for the framework's row"
        held.stato = "inviato"
        held.documenso_id, held.documenso_item_id = "envelope_primo", "envelope_item_primo"
        held.signing_url = "https://firma.letsrebase.test/sign/primo"
        first_admin.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        first_admin.close()
        second_admin.close()
    assert [(r.inviato, r.match.lettera.stato) for r in reports] == [(None, "in_attesa")]
    assert fake.envelopes == {}


def test_a_draft_text_never_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=True), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(InvalidState, match="ancora una bozza"):
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert fake.calls == [] and sender.sent == []
    assert _matches(clean, renderer).get(match.id).stato == "bozza"
    assert _framework_of(clean, freelancer_id).stato == "generato"


def test_without_rebases_signer_nothing_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(SigningUnavailable, match="REBASE_SIGNER_JSON") as caught:
        _signing(clean, renderer, fake, RecordingSender(), signer={}).send_match(
            match.id, admin_id
        )

    assert "rebase-sede" in caught.value.message
    assert fake.calls == []


def test_documenso_refusing_the_distribution_marks_nothing_sent(clean: Session) -> None:
    """Review Focus 4: the envelope exists on Documenso, the distribution fails. Nothing
    in the hub says sent, the admin reads Documenso's sentence alone, and the same draft
    can be sent again with the same letter number."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.fail("distribute", 400, "Recipient is missing a signature field")
    signing = _signing(clean, renderer, fake, sender)

    with pytest.raises(DocumensoFailed) as caught:
        signing.send_match(match.id, admin_id)

    assert caught.value.message == (
        "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    )
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.documenso_id, quadro.signing_url, quadro.sent_at) == (
        "generato",
        None,
        None,
        None,
    )
    assert _matches(clean, renderer).get(match.id).stato == "bozza"
    assert sender.sent == []
    again = signing.send_match(match.id, admin_id)
    assert again.inviato == "quadro"
    assert again.match.lettera.numero == match.lettera.numero


def test_a_refused_mail_leaves_the_document_sent_and_says_so(clean: Session) -> None:
    """Review Focus 4, the other half: Documenso took it, the mail provider did not."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, FakeDocumenso(), RefusingSender()).send_match(
        match.id, admin_id
    )

    assert (report.inviato, report.mail_inviata) == ("quadro", False)
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_without_documenso_or_without_mail_the_send_says_why(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(SigningUnavailable, match="Documenso"):
        _signing(clean, renderer, None, RecordingSender()).send_match(match.id, admin_id)
    with pytest.raises(SigningUnavailable, match="email"):
        _signing(clean, renderer, FakeDocumenso(), None).send_match(match.id, admin_id)
    assert _matches(clean, renderer).get(match.id).stato == "bozza"


def test_a_cancelled_match_has_nothing_to_send(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _matches(clean, renderer).cancel(match.id, admin_id)
    with pytest.raises(InvalidState, match="bozza o in firma"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).send_match(
            match.id, admin_id
        )


def test_a_letter_whose_framework_was_refused_gets_a_new_one_when_its_match_is_sent(
    clean: Session,
) -> None:
    """A framework agreement refused or cancelled while its letter waited: «Invia per la
    firma» on the match writes a new one from today's tax data and sends it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    refused = _framework_of(clean, freelancer_id)
    refused.stato, refused.cancel_reason = "annullato", "Rifiutato dal freelance sul sito di firma."
    clean.commit()

    report = signing.send_match(match.id, admin_id)

    assert report.inviato == "quadro"
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["annullato", "inviato"]
    assert len(fake.envelopes) == 2
```

- [ ] **Step 7: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py`
Expected: collection error, `No module named 'rebase_core.signing'`.

- [ ] **Step 8: What a document prints when it leaves** (`projects/hub/packages/core/src/rebase_core/matches.py`, before `# ---- lookups`)

```python
    # ---- phase 3: the copy that leaves ---------------------------------------------------

    def data_for_sending(
        self, document: ContractDocument, today: date, framework: ContractDocument | None
    ) -> dict[str, Value]:
        """The fields `document` prints when it goes out for signature (REB-387 phase 3):
        the engagement as the admin wrote it, the parties as they are today (the tax data
        and the name saved since the draft, rebase's signer as the setting says now), the
        day it leaves in rebase's blank, and for a letter the date its framework
        agreement was signed, in Rome."""
        freelancer, user = self._freelancer(document.freelancer_id)
        fiscal = self._fiscal(freelancer.id)
        if document.kind == QUADRO:
            return self._quadro_data(user, fiscal, today)
        signed = signed_on(framework) if framework is not None else None
        return {
            **document.data,
            **self._rebase_fields(),
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": _full_name(user),
            "professionista-piva": fiscal.partita_iva,
            **self._signing_fields(today),
        }

    def write_framework(self, freelancer_id: UUID, admin_id: UUID) -> ContractDocument:
        """A new framework agreement, added and flushed and not committed, for a match
        whose letter waits on one that was cancelled or refused: «Invia per la firma»
        writes it and sends it in one transaction (REB-387 phase 3)."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        fiscal = self._fiscal(freelancer.id)
        document = self._document(
            renderer,
            QUADRO,
            self._quadro_data(user, fiscal, self.today()),
            freelancer.id,
            None,
            None,
            "generato",
            admin_id,
        )
        self.session.add(document)
        self.session.flush()
        return document
```

- [ ] **Step 9: The report** (`projects/hub/packages/core/src/rebase_core/contract_schemas.py`, append)

```python
class SendReport(BaseModel):
    """What «Invia per la firma» did (REB-387 phase 3): the match as it is now, the kind
    of the document that left (`quadro`, `lettera`, or none when the letter waits for a
    framework agreement already out for signature), and whether its mail left too."""

    match: MatchRead
    inviato: str | None
    mail_inviata: bool | None
```

- [ ] **Step 10: Write `signing.py`** (`projects/hub/packages/core/src/rebase_core/signing.py`)

```python
"""Signing: a match's documents go out through Documenso and come back signed (REB-387,
phase 3).

`send_match` is «Invia per la firma». At most one document leaves at a time: the
framework agreement when the freelancer has none active (the letter waits for it,
`in_attesa`, spec § 1e), else the letter. A document is typeset again as it leaves, with
the parties as they are today, the day it leaves in rebase's blank («Documento emesso da
rebase il ...», spec § 5) and the labels under the signing blanks left undrawn; Documenso
gets the PDF with the freelancer as its one signer (`rebase_core.documenso`), and the hub
mails the link itself, one mail per document. A text that says `status: draft` never
leaves (spec § 1f). If Documenso refuses or does not answer, the transaction rolls back
and nothing is marked sent; if only the mail fails, the document is `inviato` and the
report says so, for «Reinvia email».

Every transition takes the row lock of what it moves (`_lock`), because two admins, the
webhook and «Aggiorna stato» can reach the same document at once.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.contract_schemas import SendReport
from rebase_core.contracts.fields import ContractFailed, Value
from rebase_core.contracts.render import Renderer
from rebase_core.documenso import DocumensoClient, fields_from_blanks
from rebase_core.errors import InvalidState, NotFound, SigningUnavailable
from rebase_core.framework import active_framework, pending_framework, rome_today
from rebase_core.mail import EmailSender, document_name, signing_request_mail
from rebase_core.matches import DOCUMENT_BY_KIND, ENTITY, LETTERA, QUADRO, MatchService
from rebase_core.models import ContractDocument, Freelancer, Match, User

_log = logging.getLogger(__name__)

FREELANCER = "freelancer"
DEFAULT_CONTRACTS_MAIL = "ciao@letsrebase.com"
# What a document must print once it leaves: rebase's, the freelancer's and the client's
# data, and the fields the hub fills itself. What may stay blank is what the signing site
# fills (the signature, the date) and the engagement's optional lines.
MUST_PRINT_PREFIXES = ("rebase-", "professionista-", "cliente-")
MUST_PRINT = frozenset({"numero", "data-contratto-quadro", "luogo-firma", "firma-rebase"})
DRAFT_REFUSED = {
    QUADRO: (
        "Il testo del contratto quadro è ancora una bozza (status: draft): si genera e si "
        "salva, ma non parte per la firma."
    ),
    LETTERA: (
        "Il testo della lettera di incarico è ancora una bozza (status: draft): si genera "
        "e si salva, ma non parte per la firma."
    ),
}
NO_DOCUMENSO = (
    "La firma elettronica non è attiva su questo ambiente: mancano l'indirizzo o il token "
    "di Documenso."
)
NO_SENDER = (
    "L'invio delle email non è attivo su questo ambiente: il link per firmare non "
    "arriverebbe a nessuno."
)


def _full_name(user: User) -> str:
    return f"{user.nome} {user.cognome}".strip()


def _filename(document: ContractDocument, version: str) -> str:
    """The name Documenso keeps and seals as `<name>_signed.pdf` (probe § 4)."""
    if document.kind == LETTERA:
        return f"lettera-di-incarico-{document.numero}.pdf"
    return f"contratto-quadro-v{version}.pdf"


def _refuse_blanks(blank: list[str]) -> None:
    """A document that would leave with a party's data missing is refused, naming it: the
    signer's gaps are this environment's setting (a 503), any other gap the match's."""
    unfilled = [key for key in blank if key.startswith(MUST_PRINT_PREFIXES) or key in MUST_PRINT]
    signer = [key for key in unfilled if key.startswith("rebase-")]
    if signer:
        raise SigningUnavailable(
            f"Mancano i dati di chi firma per rebase ({', '.join(signer)}): vanno in "
            "REBASE_SIGNER_JSON prima di inviare."
        )
    if unfilled:
        raise InvalidState(
            f"Il documento lascerebbe in bianco {', '.join(unfilled)}: completali prima di "
            "inviarlo."
        )


class SigningService:
    def __init__(
        self,
        session: Session,
        *,
        renderer: Renderer | None = None,
        documenso: DocumensoClient | None = None,
        sender: EmailSender | None = None,
        signer: Mapping[str, Value] | None = None,
        contracts_mail: str = DEFAULT_CONTRACTS_MAIL,
        today: Callable[[], date] = rome_today,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        """Each collaborator is needed only by the steps that use it: a webhook that
        cancels a document needs no renderer, a draft's cancellation no Documenso."""
        self.session = session
        self.renderer = renderer
        self.documenso = documenso
        self.sender = sender
        self.contracts_mail = contracts_mail
        self.today = today
        self.now = now
        self.matches = MatchService(session, renderer, signer, today)

    # ---- «Invia per la firma» ---------------------------------------------------------

    def send_match(self, match_id: UUID, admin_id: UUID) -> SendReport:
        renderer = self._renderer()
        self._documenso()
        self._sender()
        match = self._lock_match(match_id)
        leaving: ContractDocument | None = None
        try:
            if match.stato not in ("bozza", "in_firma"):
                raise InvalidState(
                    f"Si invia per la firma solo un match in bozza o in firma: questo è "
                    f"{match.stato}.",
                    stato=match.stato,
                )
            letter = self._lock_letter(match.id)
            if letter is None or letter.stato not in ("generato", "in_attesa"):
                raise InvalidState(
                    "La lettera di questo match è già partita, firmata o annullata: non c'è "
                    "nulla da inviare."
                )
            active = active_framework(self.session, match.freelancer_id)
            for kind in (LETTERA,) if active is not None else (QUADRO, LETTERA):
                if renderer.is_draft(DOCUMENT_BY_KIND[kind]):
                    raise InvalidState(DRAFT_REFUSED[kind])
            if active is not None:
                leaving = letter
                self._dispatch(letter, active, admin_id)
            else:
                letter.stato = "in_attesa"
                leaving = self._framework_to_send(match.freelancer_id, admin_id)
                if leaving is not None:
                    self._dispatch(leaving, None, admin_id)
            match.stato = "in_firma"
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        sent_kind = leaving.kind if leaving is not None else None
        sent_id = leaving.id if leaving is not None else None
        mailed = self._mail_signing_request(leaving) if leaving is not None else None
        AdminActionService(self.session).record(
            ENTITY,
            match_id,
            "documents_sent",
            admin_id,
            {"documento": sent_id, "kind": sent_kind, "mail": mailed},
        )
        return SendReport(match=self.matches.get(match_id), inviato=sent_kind, mail_inviata=mailed)

    def _framework_to_send(self, freelancer_id: UUID, admin_id: UUID) -> ContractDocument | None:
        """The framework agreement a waiting letter needs, locked: none when one is out for
        signature already (the letter leaves after it), the one generated for a draft,
        else a new one, written now because the last was cancelled or refused."""
        pending = pending_framework(self.session, freelancer_id)
        if pending is None:
            return self.matches.write_framework(freelancer_id, admin_id)
        framework = self._lock(pending.id)
        if framework.stato == "inviato":
            return None
        if framework.stato == "generato":
            return framework
        raise InvalidState(
            "Il contratto quadro è cambiato mentre lo inviavi: ricarica la pagina e riprova."
        )

    def _dispatch(
        self, document: ContractDocument, framework: ContractDocument | None, sent_by: UUID
    ) -> None:
        """Typeset `document` as it leaves, hand it to Documenso and record the envelope,
        inside the caller's transaction: nothing here commits, so a refusal at any step
        leaves the document as it was."""
        renderer, documenso = self._renderer(), self._documenso()
        name = DOCUMENT_BY_KIND[document.kind]
        data = self.matches.data_for_sending(document, self.today(), framework)
        rendered = renderer.render(name, data, signing=True)
        if rendered.draft:
            raise InvalidState(DRAFT_REFUSED[document.kind])
        _refuse_blanks(rendered.blank)
        fields = fields_from_blanks(renderer.signature_blanks(name, data, signing=True))
        user = self._owner(document.freelancer_id)
        title = document_name(document.kind, document.numero)
        envelope_id = documenso.create(
            title=title[0].upper() + title[1:],
            external_id=str(document.id),
            filename=_filename(document, rendered.version),
            pdf=rendered.pdf,
            signer_email=user.email,
            signer_name=_full_name(user),
            fields=fields,
        )
        envelope = documenso.get(envelope_id)
        signing_url = documenso.distribute(envelope_id)
        document.data = dict(data)
        document.pdf = rendered.pdf
        document.text_version = rendered.version
        document.testo_bozza = rendered.draft
        document.documenso_id = envelope_id
        document.documenso_item_id = envelope.item_id
        document.signing_url = signing_url
        document.stato = "inviato"
        document.sent_at = self.now()
        document.sent_by = sent_by

    def _mail_signing_request(self, document: ContractDocument) -> bool:
        """After the commit: a refused mail leaves the document `inviato`, and says so."""
        if document.signing_url is None or self.sender is None:
            return False
        user = self._owner(document.freelancer_id)
        mail = signing_request_mail(
            user.email, user.nome, document.kind, document.numero, document.signing_url
        )
        if not self.sender.send(mail):
            _log.warning("the signing mail of document %s was refused by the provider", document.id)
            return False
        return True

    # ---- plumbing ----------------------------------------------------------------------

    def _lock(self, document_id: UUID) -> ContractDocument:
        """The document, row-locked until this transaction ends, and read again from the
        database rather than from the session's memory: another transaction may have
        moved it while this one waited."""
        document = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def _lock_match(self, match_id: UUID) -> Match:
        match = self.session.scalars(
            select(Match)
            .where(Match.id == match_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if match is None:
            raise NotFound(ENTITY, match_id)
        return match

    def _lock_letter(self, match_id: UUID) -> ContractDocument | None:
        return self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def _owner(self, freelancer_id: UUID) -> User:
        user = self.session.scalar(
            select(User).join(Freelancer, Freelancer.user_id == User.id).where(
                Freelancer.id == freelancer_id
            )
        )
        if user is None:
            raise NotFound("freelancer", freelancer_id)
        return user

    def _renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to SigningService")
        return self.renderer

    def _documenso(self) -> DocumensoClient:
        if self.documenso is None:
            raise SigningUnavailable(NO_DOCUMENSO)
        return self.documenso

    def _sender(self) -> EmailSender:
        if self.sender is None:
            raise SigningUnavailable(NO_SENDER)
        return self.sender
```

- [ ] **Step 11: Run the service tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py projects/hub/packages/core/tests/test_matches.py`
Expected: all pass.

- [ ] **Step 12: Write the shared API helpers and the failing API tests**

`projects/hub/apps/api/tests/contract_flow.py` (a neighbour module, importable by bare name because `projects/hub/apps/api/tests` is on `pythonpath`; Tasks 3 and 5 import it too):

```python
"""What «Crea match» leaves behind, over HTTP, for the signing tests (REB-387 phase 3):
an admin signed in, a card, a request, the tax data and a draft match. The fixtures of
each test file hand the fakes; these helpers only walk the routes."""

import re
from typing import Any

from fastapi.testclient import TestClient

from rebase_core.mail import RecordingSender

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
FREELANCER_EMAIL = "ada@studio.it"
MISSING = "00000000-0000-7000-8000-000000000000"
# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}
FISCAL = {
    "codice_fiscale": "LVLDAA85T50H501Z",
    "partita_iva": "01234567890",
    "domicilio": "Via Roma 1, Milano",
    "pec": None,
}
CLIENTE = {
    "cliente_ragione_sociale": "ACME S.r.l.",
    "cliente_piva": "01234567890",
    "cliente_sede": "Milano",
}
LETTERA = {
    "ruolo": "Backend developer",
    "attivita": "Le API del prodotto.",
    "data_inizio": "2026-10-01",
    "compenso": "450",
    "giorni_pagamento": 30,
    "fine_mese": True,
}
# Children first; sessions, logins and magic links cascade from `users`.
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
    "signups",
)


def enter(client: TestClient, sender: RecordingSender, email: str) -> None:
    """Signs `email` in through the magic link, as the member area does."""
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    found = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert found
    assert client.post("/api/hub/auth/enter", json={"token": found.group(1)}).status_code == 200


def draft_match(client: TestClient, sender: RecordingSender) -> dict[str, Any]:
    """The admin signed in, Ada's card, ACME's request, her tax data and a draft match:
    the match as `POST /api/hub/freelancers/{id}/matches` answered it."""
    enter(client, sender, ADMIN_EMAIL)
    applied = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": FREELANCER_EMAIL,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    requested = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "ACME Srl",
            "referente_nome": "Wile",
            "referente_cognome": "E.",
            "email": "wile@acme.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Un backend developer per tre mesi.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "777.77",
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert requested.status_code == 201, requested.text
    company_id = client.get("/api/hub/companies").json()["items"][0]["id"]
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body
```

`projects/hub/apps/api/tests/test_signing_api.py`:

```python
"""REB-387 phase 3 over HTTP: «Invia per la firma» and the actions that follow it, with a
Documenso that lives in a dict and a mailbox that keeps what it gets."""

import json
from collections.abc import Iterator

import pytest
from contract_flow import ADMIN_EMAIL, MISSING, SIGNER, TABLES, draft_match
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer, get_sender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import User

CONTRACTS_MAIL = "contratti@rebase.test"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer(draft=False)
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def documenso(client: TestClient) -> Iterator[FakeDocumenso]:
    fake = FakeDocumenso()
    client.app.dependency_overrides[get_documenso] = fake.client  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, signer_json=json.dumps(SIGNER), contracts_mail=CONTRACTS_MAIL
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def test_without_the_cookie_the_send_is_a_401(client: TestClient, admin: None) -> None:
    assert client.post(f"/api/hub/matches/{MISSING}/send").status_code == 401


def test_the_send_hands_documenso_the_framework_and_mails_one_link(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match = draft_match(client, sender)
    before = len(sender.sent)

    sent = client.post(f"/api/hub/matches/{match['id']}/send")

    assert sent.status_code == 200, sent.text
    report = sent.json()
    assert (report["inviato"], report["mail_inviata"]) == ("quadro", True)
    assert report["match"]["stato"] == "in_firma"
    assert report["match"]["lettera"]["stato"] == "in_attesa"
    [payload] = documenso.created()
    assert payload["meta"]["distributionMethod"] == "NONE"
    assert not any(payload["meta"]["emailSettings"].values())
    assert payload["recipients"][0]["email"] == "ada@studio.it"
    [mail] = sender.sent[before:]
    assert mail.subject == "Da firmare: contratto quadro rebase"
    assert "https://firma.letsrebase.test/sign/" in mail.text
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches")
    assert page.json()["quadro"]["stato"] == "inviato"
    # The recipient's token is the signing URL's secret half: no admin page reads it.
    assert "firma.letsrebase.test/sign" not in page.text
    assert "777.77" not in sent.text


def test_without_documenso_the_send_says_signing_is_off(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    client.app.dependency_overrides[get_documenso] = lambda: None  # type: ignore[attr-defined]
    match = draft_match(client, sender)
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 503
    assert "non è attiva" in answered.json()["detail"]
    assert client.get(f"/api/hub/matches/{match['id']}").json()["stato"] == "bozza"


def test_a_draft_text_never_leaves(
    client: TestClient, admin: None, sender: RecordingSender, documenso: FakeDocumenso
) -> None:
    draft = FakeRenderer(draft=True)
    client.app.dependency_overrides[get_renderer] = lambda: draft  # type: ignore[attr-defined]
    match = draft_match(client, sender)
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 409
    assert "ancora una bozza" in answered.json()["detail"]
    assert documenso.created() == []


def test_documenso_refusing_is_a_502_with_its_sentence_alone(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match = draft_match(client, sender)
    documenso.fail("create", 400, "Invalid PDF")
    answered = client.post(f"/api/hub/matches/{match['id']}/send")
    assert answered.status_code == 502
    assert answered.json() == {"detail": "Documenso ha rifiutato la richiesta: Invalid PDF"}
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches").json()
    assert page["quadro"]["stato"] == "generato"
    assert page["matches"][0]["stato"] == "bozza"
```

- [ ] **Step 13: Run them to see them fail**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_signing_api.py`
Expected: FAIL, `ImportError: cannot import name 'get_documenso' from 'rebase_api.deps'`.

- [ ] **Step 14: The dependencies, the two statuses, the route**

`projects/hub/apps/api/src/rebase_api/deps.py`: add `from collections.abc import Callable, Iterator` (replacing the `Iterator` import), `from rebase_core.contracts.fields import signer_data`, `from rebase_core.documenso import DocumensoClient, client_from_settings`, `from rebase_core.signing import SigningService`, and append:

```python
def get_documenso(settings: SettingsDep) -> DocumensoClient | None:
    """This environment's Documenso client (REB-387), or `None` when signing is off: the
    send answers 503 with a sentence, as the member area does without a mail key."""
    return client_from_settings(settings)


DocumensoDep = Annotated[DocumensoClient | None, Depends(get_documenso)]

SigningFactory = Callable[[Session], SigningService]


def get_signing_factory(
    settings: SettingsDep, renderer: RendererDep, documenso: DocumensoDep, sender: SenderDep
) -> SigningFactory:
    """`SigningService` as this environment configures it, for any session: the
    request's own, or the one a background task opens for itself (the webhook's)."""
    signer = signer_data(settings.signer_json)

    def build(session: Session) -> SigningService:
        return SigningService(
            session,
            renderer=renderer,
            documenso=documenso,
            sender=sender,
            signer=signer,
            contracts_mail=settings.contracts_mail,
        )

    return build


SigningDep = Annotated[SigningFactory, Depends(get_signing_factory)]
```

`projects/hub/apps/api/src/rebase_api/main.py`: add `import logging`, `_log = logging.getLogger(__name__)` after the imports, extend the `rebase_core.errors` import with `DocumensoFailed, SigningUnavailable`, and in `domain_error_handler`, before the final `return`, add:

```python
    if isinstance(exc, DocumensoFailed):
        # The signing site refused or did not answer: a gateway's failure, in its words
        # and never its stack trace, which only the log keeps.
        _log.warning("documenso refused a call: %s", exc.detail)
        return JSONResponse({"detail": exc.message}, status_code=502)
    if isinstance(exc, SigningUnavailable):
        return JSONResponse({"detail": exc.message}, status_code=503)
```

and add «`DocumensoFailed` a 502, `SigningUnavailable` a 503» to its docstring.

`projects/hub/apps/api/src/rebase_api/routers/matches.py`: add `SigningDep` to the `rebase_api.deps` import and `SendReport` to the `contract_schemas` import, and after `close_match`:

```python
@router.post("/matches/{match_id}/send", response_model=SendReport)
def send_match(admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID) -> SendReport:
    """«Invia per la firma»: the document that can leave now goes to Documenso and the
    freelancer gets its mail; a letter whose framework agreement is not signed yet waits
    for it. 503 when this environment cannot sign, 502 when Documenso refuses, 409 for a
    draft text or a match with nothing left to send."""
    return signing(session).send_match(match_id, admin.id)
```

Replace the router module's docstring sentence «Nothing leaves the hub yet: sending for signature arrives with Documenso (phase 3).» with ««Invia per la firma» sends a match's documents through Documenso (`rebase_core.signing`, phase 3).»

- [ ] **Step 15: Run the API suite**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_signing_api.py projects/hub/apps/api/tests/test_matches_api.py`
Expected: all pass.

- [ ] **Step 16: Write the failing web tests**

`projects/hub/apps/web/src/lib/contracts.test.ts`, append (add `sendReportMessage` to the `./contracts` import):

```ts
describe('sendReportMessage (REB-390)', () => {
  const match = { lettera: { numero: '2026-001' } } as never
  it('says which document left and which waits', () => {
    expect(sendReportMessage({ match, inviato: 'quadro', mail_inviata: true })).toBe(
      'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.',
    )
    expect(sendReportMessage({ match, inviato: 'lettera', mail_inviata: true })).toBe(
      'Partita la lettera di incarico n. 2026-001.',
    )
    expect(sendReportMessage({ match, inviato: null, mail_inviata: null })).toBe(
      'La lettera n. 2026-001 aspetta il contratto quadro già in firma e partirà da sola dopo.',
    )
  })
  it('says when the mail did not leave, and what to do', () => {
    expect(sendReportMessage({ match, inviato: 'lettera', mail_inviata: false })).toBe(
      'Partita la lettera di incarico n. 2026-001. La mail però non è partita: usa «Reinvia email».',
    )
  })
})
```

`projects/hub/apps/web/src/pages/admin/Contratti.test.tsx`:

1. In `routeFetch`, replace `return answer(200, typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler)` with

```tsx
    const body = typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler
    return body instanceof Response ? body : answer(200, body)
```

2. In the first test, replace the two lines

```tsx
    // Phase 2 signs nothing: none of the signing actions is on the page yet.
    expect(screen.queryByRole('button', { name: /firma|Reinvia|Aggiorna stato|disdetta/i })).toBeNull()
```

with

```tsx
    // A framework agreement never leaves on its own: it goes with a match.
    expect(within(section).queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
```

3. Append inside the `describe`:

```tsx
  it('sends a draft match for signature and says what left (REB-390)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/matches/m1/send': {
        match: { ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'in_attesa' } },
        inviato: 'quadro',
        mail_inviata: true,
      },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/send', expect.objectContaining({ method: 'POST' })))
    expect(
      await screen.findByText('Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.'),
    ).toBeInTheDocument()
  })

  it('offers no send for a letter that waits on a framework agreement already out for signature', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        quadro: { ...QUADRO, stato: 'inviato', attivo: false, signed_at: null },
        matches: [{ ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'in_attesa' } }],
      },
    })
    mount('/admin/freelance/f1/contracts')
    await screen.findByText('Rossi Studio')
    expect(screen.queryByRole('button', { name: /Invia per la firma/ })).toBeNull()
  })

  it('shows the server’s sentence when a text is still a draft', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/matches/m1/send': () =>
        answer(409, {
          detail:
            'Il testo della lettera di incarico è ancora una bozza (status: draft): si genera e si salva, ma non parte per la firma.',
        }),
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma il match con Rossi Studio' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ancora una bozza')
  })
```

`projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx`:

1. In the first test, replace the four lines from `const send = screen.getByRole('button', { name: 'Invia per la firma' })` to `expect((await screen.findAllByText('Arriva con la firma elettronica')).length).toBeGreaterThan(0)` with

```tsx
    expect(screen.getByRole('button', { name: 'Invia per la firma' })).toBeEnabled()
```

2. Append inside the `describe`:

```tsx
  it('writes the match and sends it in one click, and after a refusal sends that same match again', async () => {
    let tries = 0
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
      'POST /api/hub/matches/m1/send': () =>
        ++tries === 1
          ? answer(503, { detail: 'La firma elettronica non è attiva su questo ambiente.' })
          : { match: { id: 'm1' }, inviato: 'quadro', mail_inviata: true },
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Invia per la firma' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('non è attiva')
    expect(alert).toHaveTextContent('La bozza è salvata')
    await userEvent.click(screen.getByRole('button', { name: 'Invia per la firma' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    const creates = spy.mock.calls.filter(
      ([url, init]) => url === '/api/hub/freelancers/f1/matches' && init?.method === 'POST',
    )
    expect(creates).toHaveLength(1)
    expect(tries).toBe(2)
  })
```

- [ ] **Step 17: Run them to see them fail**

Run: `pnpm --filter hub exec vitest run src/lib/contracts.test.ts src/pages/admin/Contratti.test.tsx src/pages/admin/CreaMatch.test.tsx`
Expected: FAIL: `sendReportMessage` is not exported, no «Invia per la firma il match con Rossi Studio» button, and the «Invia per la firma» of step 5 is disabled.

- [ ] **Step 18: `api.ts` and `lib/contracts.ts`**

`projects/hub/apps/web/src/lib/api.ts`, after `FreelancerContracts`:

```ts
/** What «Invia per la firma» did (REB-390): the document that left now, none when the
 *  letter waits for a framework agreement already out for signature, and whether its
 *  mail left too. */
export interface SendReport {
  match: Match
  inviato: 'quadro' | 'lettera' | null
  mail_inviata: boolean | null
}
```

and inside `admin`, after `closeMatch`:

```ts
  /** «Invia per la firma» (REB-390): the document that can leave now goes to Documenso. */
  sendMatch: (matchId: string) => request<SendReport>(`/api/hub/matches/${matchId}/send`, { method: 'POST' }),
```

`projects/hub/apps/web/src/lib/contracts.ts`: add `SendReport` to the type import from `./api` and append:

```ts
/** The sentence the pages show after «Invia per la firma» (REB-390). */
export function sendReportMessage(report: SendReport): string {
  const numero = report.match.lettera.numero
  const sent =
    report.inviato === 'quadro'
      ? `Partito il contratto quadro: la lettera n. ${numero} partirà da sola dopo la sua firma.`
      : report.inviato === 'lettera'
        ? `Partita la lettera di incarico n. ${numero}.`
        : `La lettera n. ${numero} aspetta il contratto quadro già in firma e partirà da sola dopo.`
  return report.mail_inviata === false ? `${sent} La mail però non è partita: usa «Reinvia email».` : sent
}
```

- [ ] **Step 19: «Invia per la firma» on «Match e contratti»** (`projects/hub/apps/web/src/pages/admin/Contratti.tsx`)

Import `sendReportMessage` from `@/lib/contracts` beside `draftFromFiscal`. In `MatchesSection`, add two props to the destructuring and to the type:

```tsx
  canSend,
  onSend,
```

```tsx
  canSend: (match: Match) => boolean
  onSend: (match: Match) => void
```

and in the actions cell, before `{match.stato === 'bozza' && (`, add:

```tsx
                    {canSend(match) && (
                      <Button
                        type="button"
                        size="sm"
                        className="mr-2"
                        disabled={busy}
                        aria-label={`Invia per la firma il match con ${match.nome_azienda}`}
                        onClick={() => onSend(match)}
                      >
                        Invia per la firma
                      </Button>
                    )}
```

In `AdminContratti`, after the `close` mutation add:

```tsx
  const [message, setMessage] = useState<string | null>(null)
  const send = useMutation({
    mutationFn: (matchId: string) => admin.sendMatch(matchId),
    onSuccess: (report) => {
      setMessage(sendReportMessage(report))
      refresh()
    },
  })
```

change `const actionError = cancel.error ?? close.error` to `const actionError = send.error ?? cancel.error ?? close.error`, add after `const name = ...`:

```tsx
  // A draft leaves on request; a match in signature only when its letter still waits and
  // no framework agreement is out for signature to carry it (a cancelled or refused one,
  // or a signed one whose release failed).
  const canSend = (match: Match) =>
    match.stato === 'bozza' ||
    (match.stato === 'in_firma' && match.lettera.stato === 'in_attesa' && data.quadro?.stato !== 'inviato')
```

render, right before `<MatchesSection`:

```tsx
      {message && (
        <p role="status" className="px-6 pb-3 text-sm">
          {message}
        </p>
      )}
```

and pass to `MatchesSection`: `busy={send.isPending || cancel.isPending || close.isPending}`, `canSend={canSend}`, `onSend={(match) => { setMessage(null); send.mutate(match.id) }}`. Replace the component's docstring with:

```tsx
/** «Match e contratti» (REB-387): the framework agreement with its dates, the tax data,
 *  and every match with its letter; «Invia per la firma» on a match that has a document
 *  to send (REB-390). */
```

- [ ] **Step 20: «Invia per la firma» on step 5 of «Crea match»** (`projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`)

Remove the `@rebase/ui/tooltip` import, add `type Match` to the `@/lib/api` import, and replace `PreviewStep` with:

```tsx
function PreviewStep({
  previews,
  prefill,
  onBack,
  onSave,
  onSend,
  saving,
  sending,
  locked,
  failure,
}: {
  previews: Previews
  prefill: MatchPrefill
  onBack: () => void
  onSave: () => void
  onSend: () => void
  saving: boolean
  sending: boolean
  locked: boolean
  failure: Failure | null
}) {
  const order = prefill.quadro_necessario
    ? 'Con la firma elettronica partirà per primo il contratto quadro; la lettera di incarico aspetterà la sua firma e partirà da sola subito dopo.'
    : prefill.lettera_in_attesa
      ? 'La lettera di incarico aspetterà la firma del contratto quadro già inviato, e partirà da sola subito dopo.'
      : 'Il contratto quadro è già attivo: con la firma elettronica partirà solo la lettera di incarico.'
  return (
    <div className="space-y-4">
      <ul className="space-y-2 text-sm">
        <li>
          <a className="underline underline-offset-2" href={previews.lettera} target="_blank" rel="noreferrer">
            Apri la lettera di incarico
          </a>
        </li>
        {previews.quadro && (
          <li>
            <a className="underline underline-offset-2" href={previews.quadro} target="_blank" rel="noreferrer">
              Apri il contratto quadro
            </a>
          </li>
        )}
      </ul>
      <p className="text-sm">{order}</p>
      <p className="text-sm text-muted-foreground">
        «Salva come bozza» non manda nulla a nessuno; «Invia per la firma» manda al freelance una mail per il documento
        che parte.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={onBack} disabled={saving || sending || locked}>
          Indietro
        </Button>
        <Button type="button" variant="outline" onClick={onSave} disabled={saving || sending}>
          {saving ? 'Salvo…' : 'Salva come bozza'}
        </Button>
        <Button type="button" onClick={onSend} disabled={saving || sending}>
          {sending ? 'Invio…' : 'Invia per la firma'}
        </Button>
      </div>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure.message}
        </p>
      )}
    </div>
  )
}
```

In `AdminCreaMatch`, after the `save` mutation add:

```tsx
  // «Invia per la firma» writes the match first, once: after a refusal the draft exists,
  // and the next click sends that one rather than writing another with a new number.
  const [created, setCreated] = useState<Match | null>(null)
  const sendNow = useMutation({
    mutationFn: async (payload: MatchCreate) => {
      const match = created ?? (await admin.createMatch(id, payload))
      setCreated(match)
      return admin.sendMatch(match.id)
    },
    onSuccess: () => void navigate({ to: '/admin/freelance/$id/contracts', params: { id } }),
  })
  const sendFailure = failureOf(sendNow.error, 'Non riesco a inviare per la firma.')
  const previewFailure =
    failureOf(save.error, 'Non riesco a salvare la bozza.') ??
    (sendFailure && created
      ? { ...sendFailure, message: `${sendFailure.message} La bozza è salvata: la trovi in «Match e contratti».` }
      : sendFailure)
```

and replace the `PreviewStep` element's props from `onSave` to `failure` with:

```tsx
            onSave={() => {
              if (created) return void navigate({ to: '/admin/freelance/$id/contracts', params: { id } })
              const body = payload()
              if (body) save.mutate(body)
            }}
            onSend={() => {
              const body = payload()
              if (body) sendNow.mutate(body)
            }}
            saving={save.isPending}
            sending={sendNow.isPending}
            locked={created !== null}
            failure={previewFailure}
```

Replace the component's docstring sentence ««Invia per la firma» arrives with the electronic signature (phase 3).» with ««Invia per la firma» writes the match once and sends it (REB-390).»

- [ ] **Step 21: Run the web checks**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: green.

- [ ] **Step 22: Whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green.

- [ ] **Step 23: Commit**

```bash
git add projects/hub/packages/core/migrations/versions/0018_documenso_envelopes.py \
  projects/hub/packages/core/src/rebase_core/models.py \
  projects/hub/packages/core/src/rebase_core/matches.py \
  projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/tests/test_migrations.py \
  projects/hub/packages/core/tests/test_signing.py \
  projects/hub/apps/api/src/rebase_api/deps.py \
  projects/hub/apps/api/src/rebase_api/main.py \
  projects/hub/apps/api/src/rebase_api/routers/matches.py \
  projects/hub/apps/api/tests/contract_flow.py \
  projects/hub/apps/api/tests/test_signing_api.py \
  projects/hub/apps/web/src/lib/api.ts \
  projects/hub/apps/web/src/lib/contracts.ts \
  projects/hub/apps/web/src/lib/contracts.test.ts \
  projects/hub/apps/web/src/pages/admin/Contratti.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.test.tsx \
  projects/hub/apps/web/src/pages/admin/CreaMatch.tsx \
  projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx
git commit -F - <<'EOF'
feat(hub): send a match for signature through Documenso

I add SigningService.send_match behind POST /api/hub/matches/{id}/send and
the «Invia per la firma» buttons. One document leaves at a time: the
framework agreement while the freelancer has none active, the letter held
in_attesa, else the letter. Each is typeset again as it leaves, with today's
parties and the send date in rebase's blank, handed to Documenso with
distributionMethod NONE, and mailed by the hub. A draft text never leaves,
a refusal marks nothing sent, and a refused mail is reported. Migration 0018
keeps the envelope item the sealed copy is downloaded by, and why a
document was cancelled.

REB-390.
EOF
```

---
### Task 3: The webhook: signed, refused, cancelled, and the letters a signature releases (REB-391, part 1 of 2)

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/signing.py` (docstring, imports, a new section)
- Modify: `projects/hub/packages/core/tests/test_signing.py` (append)
- Create: `projects/hub/apps/api/src/rebase_api/routers/documenso.py`
- Modify: `projects/hub/apps/api/src/rebase_api/deps.py` (append the session opener), `main.py` (the router)
- Create: `projects/hub/apps/api/tests/test_documenso_webhook_api.py`
- Modify: `projects/hub/AGENTS.md` (a new section)

**Interfaces:**
- Consumes: Task 1 (`Outcome`, `COMPLETED`, `REJECTED`, `WebhookBody`, `outcome_from_webhook`, `DocumensoClient.download_signed`, `Attachment`, `signed_copy_mail`, `FakeDocumenso.sign/reject/webhook/signed_pdf/fail`); Task 2 (`SigningService` with `_lock`, `_dispatch`, `_mail_signing_request`, `_owner`, `_renderer`, `_documenso`, `DRAFT_REFUSED`; `ContractDocument.documenso_item_id`, `cancel_reason`; `models.CANCEL_REASON_MAX_LENGTH`; `deps.SigningDep`, `SigningFactory`; `contract_flow.*`; the test helpers of `test_signing.py`); phase 2 (`framework.is_active`, `MatchService.document_pdf`, `schemas.Ack`).
- Produces:
  - `SigningService.apply(outcome: Outcome) -> UUID | None` (the id of a document just signed); `SigningService.finish(document_id: UUID) -> None` (idempotent: download, store and mail the sealed copy once; release the letters an active framework agreement waited for); module constants `REFUSED_ON_SITE = "Rifiutato dal freelance sul sito di firma."`, `CANCELLED_ON_DOCUMENSO = "Annullato su Documenso."`.
  - HTTP: `POST /api/hub/documenso/webhook`, no cookie, header `X-Documenso-Secret`; 503 without `REBASE_DOCUMENSO_WEBHOOK_SECRET`, 401 for a missing, empty or wrong secret, 200 `{"ok": true}` for everything else, handled or not.
  - `rebase_api.deps.SessionOpener = Callable[[], AbstractContextManager[Session]]`, `get_session_opener() -> SessionOpener`, `SessionOpenerDep`.
  - `rebase_api.routers.documenso.verify_secret` (a dependency), `router`.

- [ ] **Step 1: Write the failing service tests** (append to `projects/hub/packages/core/tests/test_signing.py`)

Add to the imports: `from rebase_core.documenso import Outcome, WebhookBody, outcome_from_webhook`, and `Match` to the `rebase_core.models` import.

```python
def _sent(
    session: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    sender: RecordingSender,
    freelancer_id: UUID,
    company_id: UUID,
    admin_id: UUID,
) -> MatchRead:
    """A draft match, sent: its framework agreement `inviato`, its letter waiting."""
    match = _draft(session, renderer, freelancer_id, company_id, admin_id)
    _signing(session, renderer, fake, sender).send_match(match.id, admin_id)
    return match


def _webhook(fake: FakeDocumenso, envelope_id: str, event: str) -> Outcome:
    outcome = outcome_from_webhook(WebhookBody.model_validate(fake.webhook(envelope_id, event)))
    assert outcome is not None
    return outcome


def _envelope_of(document: ContractDocument) -> str:
    assert document.documenso_id is not None
    return document.documenso_id


def test_a_completion_signs_the_document_with_the_signers_date_and_calls_nobody(
    clean: Session,
) -> None:
    """The webhook's transaction moves the row and nothing else: Documenso gets its
    answer before any download or mail (probe § 11.2)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    calls, mails = len(fake.calls), len(sender.sent)

    signed = _signing(clean, renderer, fake, sender).apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))

    quadro = _framework_of(clean, freelancer_id)
    assert signed == quadro.id
    assert (quadro.stato, quadro.signed_at, quadro.signed_pdf) == ("firmato", SIGNED_AT, None)
    assert (len(fake.calls), len(sender.sent)) == (calls, mails)


def test_a_second_delivery_waits_for_the_first_and_changes_nothing(
    hub_engine: Engine, clean: Session
) -> None:
    """Review Focus 1: Documenso retries at once, and even while a slow first delivery is
    still running (probe § 5). The second waits on the row, then finds it signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    envelope = _envelope_of(framework)
    fake.sign(envelope, SIGNED_AT)
    outcome = _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    results: list[UUID | None] = []

    def deliver_again() -> None:
        results.append(_signing(second, renderer, fake, sender).apply(outcome))

    try:
        # The first delivery, caught holding the row with its transition not committed.
        held = first.scalars(
            select(ContractDocument).where(ContractDocument.id == framework.id).with_for_update()
        ).one()
        worker = threading.Thread(target=deliver_again)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second delivery did not wait for the first one's lock"
        held.stato, held.signed_at = "firmato", SIGNED_AT
        first.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        first.close()
        second.close()
    assert results == [None]


def test_finish_downloads_and_mails_the_signed_copy_once(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    before = len(sender.sent)

    signing.finish(signed)
    signing.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    copies = [mail for mail in sender.sent[before:] if mail.attachments]
    assert [(mail.to, mail.subject) for mail in copies] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]
    assert all(
        mail.attachments[0].filename == "contratto-quadro-v0.1-firmato.pdf"
        and mail.attachments[0].content == quadro.signed_pdf
        for mail in copies
    )


def test_a_download_that_fails_is_done_by_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("download", 500, "Internal server error")

    signing.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf is None

    signing.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)


def test_a_framework_signed_late_at_night_releases_its_letter_with_the_rome_date(
    clean: Session,
) -> None:
    """Review Focus 2: signed at 23:30 UTC on 30 September, which is 1 October in Rome.
    The letter that waited leaves on its own, citing that date, mailed as its own."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender, today=date(2026, 10, 1))
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.finish(signed)

    letter = _letter_of(clean, match.id)
    assert letter.stato == "inviato"
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    assert letter.data["firma-rebase"] == "Documento emesso da rebase il 1° ottobre 2026"
    assert letter.sent_by == admin_id
    assert sender.sent[-1].subject == f"Da firmare: lettera di incarico n. {letter.numero}"
    assert len(fake.envelopes) == 2


def test_a_release_documenso_refuses_waits_for_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("create", 500, "Internal server error")

    signing.finish(signed)
    assert _letter_of(clean, match.id).stato == "in_attesa"

    signing.finish(signed)
    assert _letter_of(clean, match.id).stato == "inviato"


def test_a_draft_matchs_letter_is_not_released(clean: Session) -> None:
    """Only a match an admin sent is released; a draft's letter leaves with its match."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    sent = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    draft = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.finish(signed)

    assert _letter_of(clean, sent.id).stato == "inviato"
    assert _letter_of(clean, draft.id).stato == "in_attesa"


def test_a_signed_letter_turns_its_match_active(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)

    _signing(clean, renderer, fake, sender).apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))

    clean.expire_all()
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]
    assert _letter_of(clean, match.id).stato == "firmato"


def test_a_refused_document_is_cancelled_with_the_freelancers_reason(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "La PEC indicata non è la mia")

    signed = _signing(clean, renderer, fake, sender).apply(_webhook(fake, envelope, "DOCUMENT_REJECTED"))

    quadro = _framework_of(clean, freelancer_id)
    assert signed is None
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: La PEC indicata non è la mia",
    )
    # The letter keeps waiting: «Invia per la firma» on its match writes a new framework.
    assert _letter_of(clean, match.id).stato == "in_attesa"


def test_a_cancellation_on_documenso_cancels_and_a_late_event_is_ignored(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.client().cancel(envelope, "Annullato a mano.")
    signing = _signing(clean, renderer, fake, sender)

    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_CANCELLED")) is None
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.cancel_reason) == ("annullato", "Annullato su Documenso.")
    # An envelope already cancelled, or one the hub never recorded, moves nothing.
    fake.sign(envelope, SIGNED_AT)
    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED")) is None
    assert _framework_of(clean, freelancer_id).stato == "annullato"
    assert signing.apply(Outcome("envelope_sconosciuto", "completed", SIGNED_AT)) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py`
Expected: FAIL, `AttributeError: 'SigningService' object has no attribute 'apply'`, and the Task 2 tests still pass.

- [ ] **Step 3: `apply` and `finish`** (`projects/hub/packages/core/src/rebase_core/signing.py`)

Append to the module docstring:

```
The webhook's side is `apply` and `finish`. `apply` only locks the document, moves it
and commits, so Documenso gets its answer long before its ten seconds (probe § 5), and a
second delivery of the same event, which can arrive while the first is still running,
waits on the lock and finds nothing left to do. `finish` runs after that commit, in the
webhook's background task or under «Aggiorna stato»: the sealed copy downloaded, stored
and mailed to both parties once, then, for a framework agreement, the letters that
waited for it typeset with its signature date and sent. Each step checks under the lock
whether it is still to do, so running `finish` twice does everything once.
```

Extend the imports: `from rebase_core.documenso import COMPLETED, REJECTED, DocumensoClient, Outcome, fields_from_blanks`, `from rebase_core.errors import DocumensoFailed, InvalidState, NotFound, SigningUnavailable`, `from rebase_core.framework import active_framework, is_active, pending_framework, rome_today`, `from rebase_core.mail import Attachment, EmailSender, document_name, signed_copy_mail, signing_request_mail`, `from rebase_core.models import CANCEL_REASON_MAX_LENGTH, ContractDocument, Freelancer, Match, User`.

After `NO_SENDER` add:

```python
REFUSED_ON_SITE = "Rifiutato dal freelance sul sito di firma."
CANCELLED_ON_DOCUMENSO = "Annullato su Documenso."


def _cancel_reason(outcome: Outcome) -> str:
    """Why a document the webhook cancels is `annullato`, as the page shows it."""
    if outcome.kind == REJECTED:
        reason = (outcome.reason or "").strip()
        if not reason:
            return REFUSED_ON_SITE
        return f"Rifiutato dal freelance: {reason}"[:CANCEL_REASON_MAX_LENGTH]
    return CANCELLED_ON_DOCUMENSO
```

Before `# ---- plumbing`, add:

```python
    # ---- the webhook -------------------------------------------------------------------

    def apply(self, outcome: Outcome) -> UUID | None:
        """One envelope's outcome, applied once (probe § 11.2). Only a document still
        `inviato` moves; an unknown envelope (the other environment's, or one the hub
        never recorded) and a document already signed or cancelled are acknowledged and
        left alone. No call leaves this method. Returns the id of a document that has
        just been signed, for `finish` after the commit; `None` otherwise."""
        document = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.documenso_id == outcome.envelope_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if document is None or document.stato != "inviato":
            self.session.rollback()
            return None
        signed: UUID | None = None
        if outcome.kind == COMPLETED:
            document.stato = "firmato"
            # The signer's own date; the moment the hub heard of it only if Documenso
            # said nothing, which a completed envelope never does.
            document.signed_at = outcome.signed_at or self.now()
            if document.match_id is not None:
                match = self.session.get(
                    Match, document.match_id, with_for_update=True, populate_existing=True
                )
                if match is not None and match.stato == "in_firma":
                    match.stato = "attivo"
            signed = document.id
        else:
            document.stato = "annullato"
            document.cancel_reason = _cancel_reason(outcome)
        self.session.commit()
        return signed

    def finish(self, document_id: UUID) -> None:
        """What a signature leaves to do once it is committed, each step idempotent: the
        sealed copy downloaded, stored and mailed once; for an active framework agreement,
        the letters that waited for it released. A step that fails is logged and left for
        the next call (the next «Aggiorna stato»); the others still run."""
        try:
            stored = self._store_signed_copy(document_id)
        except (DocumensoFailed, SigningUnavailable, NotFound):
            self.session.rollback()
            _log.warning("the signed copy of document %s is not stored yet", document_id, exc_info=True)
            stored = None
        if stored is not None:
            self._mail_signed_copy(stored)
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is not None and is_active(document):
            self._release_letters(document)

    def _store_signed_copy(self, document_id: UUID) -> ContractDocument | None:
        """The download happens under the row's lock, so two callers download once: the
        second waits, then finds the copy stored. `None` when there is nothing to store."""
        document = self._lock(document_id)
        if (
            document.stato not in ("firmato", "disdetto")
            or document.signed_pdf is not None
            or document.documenso_item_id is None
        ):
            self.session.rollback()
            return None
        document.signed_pdf = self._documenso().download_signed(document.documenso_item_id)
        self.session.commit()
        return document

    def _mail_signed_copy(self, document: ContractDocument) -> None:
        """To the freelancer and to rebase's contracts address, each with the sealed copy.
        A refusal is logged: the copy stays on «Match e contratti» and in «Contratti»."""
        if self.sender is None:
            _log.warning("no mail sender: the signed copy of document %s was not mailed", document.id)
            return
        user = self._owner(document.freelancer_id)
        pdf = self.matches.document_pdf(document.id, signed=True)
        attachment = Attachment(filename=pdf.filename, content=pdf.content)
        for to, for_rebase in ((user.email, False), (self.contracts_mail, True)):
            mail = signed_copy_mail(
                to,
                kind=document.kind,
                numero=document.numero,
                attachment=attachment,
                nome=user.nome,
                cognome=user.cognome,
                for_rebase=for_rebase,
            )
            if not self.sender.send(mail):
                _log.warning("the signed copy of document %s was refused by the provider", document.id)

    def _release_letters(self, framework: ContractDocument) -> None:
        """The letters that waited for this framework agreement (spec § 1e), each in a
        transaction of its own and mailed after its commit: a Documenso refusal leaves
        that letter waiting for the next `finish` and lets the others go. Only matches an
        admin sent (`in_firma`); a draft's letter leaves when its match is sent."""
        waiting = list(
            self.session.scalars(
                select(ContractDocument.id)
                .join(Match, Match.id == ContractDocument.match_id)
                .where(
                    ContractDocument.kind == LETTERA,
                    ContractDocument.freelancer_id == framework.freelancer_id,
                    ContractDocument.stato == "in_attesa",
                    Match.stato == "in_firma",
                )
                .order_by(ContractDocument.created_at, ContractDocument.id)
            )
        )
        framework_id, sent_by = framework.id, framework.sent_by or framework.created_by
        self.session.rollback()
        for letter_id in waiting:
            try:
                letter = self._send_waiting(letter_id, framework_id, sent_by)
            except (ContractFailed, DocumensoFailed, InvalidState, NotFound, SigningUnavailable):
                self.session.rollback()
                _log.warning("letter %s keeps waiting: its release failed", letter_id, exc_info=True)
                continue
            if letter is not None:
                self._mail_signing_request(letter)

    def _send_waiting(
        self, letter_id: UUID, framework_id: UUID, sent_by: UUID
    ) -> ContractDocument | None:
        letter = self._lock(letter_id)
        match = (
            self.session.get(Match, letter.match_id, populate_existing=True)
            if letter.match_id is not None
            else None
        )
        framework = self.session.get(ContractDocument, framework_id, populate_existing=True)
        if (
            letter.stato != "in_attesa"
            or match is None
            or match.stato != "in_firma"
            or framework is None
            or not is_active(framework)
        ):
            self.session.rollback()
            return None
        if self._renderer().is_draft(DOCUMENT_BY_KIND[LETTERA]):
            raise InvalidState(DRAFT_REFUSED[LETTERA])
        self._dispatch(letter, framework, sent_by)
        self.session.commit()
        return letter
```

- [ ] **Step 4: Run the service tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py`
Expected: all pass.

- [ ] **Step 5: Write the failing webhook tests** (`projects/hub/apps/api/tests/test_documenso_webhook_api.py`)

```python
"""REB-387 phase 3: Documenso's webhook over HTTP. The secret before anything, a fast
answer, and the slow part (the sealed copy, the two mails, the released letter) after it,
in a session of its own."""

import json
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from contract_flow import ADMIN_EMAIL, SIGNER, TABLES, draft_match
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer, get_sender, get_session_opener
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, User

SECRET = "segreto-del-webhook-di-prova"
CONTRACTS_MAIL = "contratti@rebase.test"
# 23:30 UTC on 30 September is already 1 October in Rome.
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
WEBHOOK = "/api/hub/documenso/webhook"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def documenso(client: TestClient, api_session: Session) -> Iterator[FakeDocumenso]:
    """Documenso, the renderer, and the background task's session, which is the test's:
    `finish` runs inside the TestClient call, after the response."""
    fake = FakeDocumenso()
    renderer = FakeRenderer(draft=False)
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_documenso] = fake.client
    overrides[get_renderer] = lambda: renderer
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        signer_json=json.dumps(SIGNER),
        contracts_mail=CONTRACTS_MAIL,
        documenso_webhook_secret=SECRET,
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _deliver(
    client: TestClient, body: dict[str, Any], secret: str | None = SECRET
) -> httpx.Response:
    headers = {} if secret is None else {"X-Documenso-Secret": secret}
    return client.post(WEBHOOK, json=body, headers=headers)


def _document(session: Session, kind: str) -> ContractDocument:
    session.expire_all()
    return session.scalars(select(ContractDocument).where(ContractDocument.kind == kind)).one()


def _sent(
    client: TestClient, sender: RecordingSender, session: Session
) -> tuple[dict[str, Any], str]:
    """A match sent for signature: the match, and its framework agreement's envelope, read
    from the row since no page shows it."""
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    envelope = _document(session, "quadro").documenso_id
    assert envelope is not None
    return match, envelope


def test_a_webhook_without_the_secret_or_with_an_empty_one_is_refused(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    """Probe § 11.4: a webhook saved without a secret sends the header empty, so an empty
    header is refused as a wrong one is; and nothing moves on a refused delivery."""
    _match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    body = documenso.webhook(envelope, "DOCUMENT_COMPLETED")
    for secret in (None, "", "segreto-sbagliato", SECRET.upper()):
        assert _deliver(client, body, secret).status_code == 401, secret
    assert _document(api_session, "quadro").stato == "inviato"


def test_without_the_setting_the_webhook_is_off(
    client: TestClient, admin: None, documenso: FakeDocumenso
) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[attr-defined,call-arg]
    assert _deliver(client, {"event": "DOCUMENT_COMPLETED", "payload": {"envelopeId": "x"}}).status_code == 503


def test_a_signed_framework_is_stored_mailed_and_releases_its_letter(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    before = len(sender.sent)

    answered = _deliver(client, documenso.webhook(envelope, "DOCUMENT_COMPLETED"))

    assert answered.status_code == 200 and answered.json() == {"ok": True}
    quadro = _document(api_session, "quadro")
    assert (quadro.stato, quadro.signed_at) == ("firmato", SIGNED_AT)
    assert quadro.signed_pdf == documenso.signed_pdf(envelope)
    mails = sender.sent[before:]
    assert [(mail.to, mail.subject) for mail in mails[:2]] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]
    assert all(mail.attachments[0].content == quadro.signed_pdf for mail in mails[:2])
    letter = _document(api_session, "lettera")
    assert letter.stato == "inviato"
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    assert mails[2].subject == f"Da firmare: lettera di incarico n. {letter.numero}"

    assert letter.documenso_id is not None
    documenso.sign(letter.documenso_id, SIGNED_AT)
    assert _deliver(client, documenso.webhook(letter.documenso_id, "DOCUMENT_COMPLETED")).status_code == 200
    assert client.get(f"/api/hub/matches/{match['id']}").json()["stato"] == "attivo"


def test_the_same_completion_twice_does_everything_once(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    _match, envelope = _sent(client, sender, api_session)
    documenso.sign(envelope, SIGNED_AT)
    body = documenso.webhook(envelope, "DOCUMENT_COMPLETED")
    assert _deliver(client, body).status_code == 200
    mails, envelopes = len(sender.sent), len(documenso.envelopes)

    assert _deliver(client, body).status_code == 200

    assert (len(sender.sent), len(documenso.envelopes)) == (mails, envelopes)
    downloads = [call for call in documenso.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1


def test_a_refusal_cancels_the_document_and_says_why(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    _match, envelope = _sent(client, sender, api_session)
    documenso.reject(envelope, "Il domicilio è sbagliato")
    assert _deliver(client, documenso.webhook(envelope, "DOCUMENT_REJECTED")).status_code == 200
    quadro = _document(api_session, "quadro")
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: Il domicilio è sbagliato",
    )


def test_an_unknown_envelope_and_an_event_nobody_handles_are_acknowledged(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    _match, envelope = _sent(client, sender, api_session)
    opened = documenso.webhook(envelope, "DOCUMENT_OPENED")
    assert _deliver(client, opened).status_code == 200
    stranger = {**documenso.webhook(envelope, "DOCUMENT_COMPLETED")}
    stranger["payload"] = {**stranger["payload"], "envelopeId": "envelope_di_un_altro_ambiente"}
    assert _deliver(client, stranger).status_code == 200
    assert _document(api_session, "quadro").stato == "inviato"
```


- [ ] **Step 6: Run them to see them fail**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_documenso_webhook_api.py`
Expected: FAIL, `ImportError: cannot import name 'get_session_opener' from 'rebase_api.deps'`.

- [ ] **Step 7: The session opener** (`projects/hub/apps/api/src/rebase_api/deps.py`, append; add `from contextlib import AbstractContextManager, closing`)

```python
SessionOpener = Callable[[], AbstractContextManager[Session]]


def get_session_opener() -> SessionOpener:
    """A session for work that runs after the response (REB-387's webhook): a background
    task must not borrow the request's session, which its dependency closes."""
    factory = _get_session_factory()
    return lambda: closing(factory())


SessionOpenerDep = Annotated[SessionOpener, Depends(get_session_opener)]
```

- [ ] **Step 8: The webhook route** (`projects/hub/apps/api/src/rebase_api/routers/documenso.py`)

```python
"""Documenso's webhook (REB-387, phase 3): a signature, a refusal or a cancellation.

No cookie: Documenso authenticates with `X-Documenso-Secret`, the value typed into the
webhook's form, sent verbatim (probe § 5). It is compared in constant time with
`REBASE_DOCUMENSO_WEBHOOK_SECRET`, and a missing header and an empty one are refused
alike, since a webhook saved without a secret sends the header empty (probe § 11.4). The
check is a dependency, so it runs before the body is validated.

The answer is fast on purpose. Documenso gives up on a delivery after ten seconds and
retries at once, three times within about 160 ms, then never again (probe § 5): the
route only locks the document, moves it and commits (`SigningService.apply`), and the
slow part (the sealed copy's download, the two mails, the letters a framework agreement
releases) runs after the response, in a session of its own (`SigningService.finish`).
Every well-formed delivery is answered 200, handled or not, so Documenso never retries
an event the hub chose to ignore. A delivery the hub missed entirely is recovered by
«Aggiorna stato».
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from rebase_api.deps import (
    SessionDep,
    SessionOpener,
    SessionOpenerDep,
    SettingsDep,
    SigningDep,
    SigningFactory,
)
from rebase_core.documenso import WebhookBody, outcome_from_webhook
from rebase_core.schemas import Ack

router = APIRouter(prefix="/api/hub", tags=["hub-documenso"])

_log = logging.getLogger(__name__)


def verify_secret(
    settings: SettingsDep,
    x_documenso_secret: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.documenso_webhook_secret
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "La firma elettronica non è attiva su questo ambiente.",
        )
    presented = x_documenso_secret or ""
    # Bytes, not str: `compare_digest` refuses a `str` with a non-ASCII character, and
    # Starlette decodes headers as latin-1 (the same care as `/members/lookup`).
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "segreto non valido")


def _finish(open_session: SessionOpener, build: SigningFactory, document_id: UUID) -> None:
    """After the response: it never raises, since nobody is left to read it."""
    try:
        with open_session() as session:
            build(session).finish(document_id)
    except Exception:
        _log.exception("finishing the signature of document %s failed", document_id)


@router.post("/documenso/webhook", response_model=Ack, dependencies=[Depends(verify_secret)])
def documenso_webhook(
    payload: WebhookBody,
    background: BackgroundTasks,
    session: SessionDep,
    signing: SigningDep,
    open_session: SessionOpenerDep,
) -> Ack:
    outcome = outcome_from_webhook(payload)
    if outcome is None:
        return Ack()
    signed = signing(session).apply(outcome)
    if signed is not None:
        background.add_task(_finish, open_session, signing, signed)
    return Ack()
```

`projects/hub/apps/api/src/rebase_api/main.py`: add `documenso` to the `rebase_api.routers` import and `app.include_router(documenso.router)` after `app.include_router(matches.router)`.

- [ ] **Step 9: Run the API tests**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_documenso_webhook_api.py projects/hub/apps/api/tests/test_signing_api.py`
Expected: all pass.

- [ ] **Step 10: Say how signing runs** (`projects/hub/AGENTS.md`, a new section after «The contracts are typeset at request time»)

```
## Contracts are signed on Documenso

Since REB-387 phase 3 «Invia per la firma» sends a match's documents through Documenso
(`rebase_core.signing`, `rebase_core.documenso`), and the hub mails the signing link
itself: Documenso sends no mail of its own. Documenso calls back
`POST /api/hub/documenso/webhook` with `X-Documenso-Secret` equal to
`REBASE_DOCUMENSO_WEBHOOK_SECRET`; production's webhook points at
`http://api:8000/api/hub/documenso/webhook` inside the compose network, preview's at
`https://preview.letsrebase.com/api/hub/documenso/webhook`. Documenso retries a failed
delivery only at once, so an event lost while the API restarts stays lost: «Aggiorna
stato» on «Match e contratti» reads the envelope and applies it, and an admin presses it
on a document that has waited for its signature longer than expected. Without
`REBASE_DOCUMENSO_URL` and `REBASE_DOCUMENSO_API_TOKEN` signing answers 503, and a text
whose front matter says `status: draft` never leaves.
```

- [ ] **Step 11: Whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green.

- [ ] **Step 12: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/tests/test_signing.py \
  projects/hub/apps/api/src/rebase_api/routers/documenso.py \
  projects/hub/apps/api/src/rebase_api/deps.py \
  projects/hub/apps/api/src/rebase_api/main.py \
  projects/hub/apps/api/tests/test_documenso_webhook_api.py \
  projects/hub/AGENTS.md
git commit -F - <<'EOF'
feat(hub): Documenso's webhook signs, refuses and cancels, and releases waiting letters

I add POST /api/hub/documenso/webhook. The secret is checked first, in
constant time, and an empty header is refused like a wrong one. The route
locks the document, moves it and commits: signed with the signer's own date,
or cancelled with the reason. A second delivery waits on the lock and finds
nothing to do. After the response, in a session of its own, the hub
downloads the sealed copy by its envelope item, mails it to the freelancer
and to rebase, turns a signed letter's match active and sends the letters a
signed framework agreement released, each step once however often it runs.

REB-391.
EOF
```

---
### Task 4: «Aggiorna stato», «Reinvia email», «Annulla» and «Registra disdetta» (REB-391, part 2 of 2)

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/signing.py` (imports, constants, a new section)
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py` (`ContractDocumentRead.cancel_reason`), `framework.py` (`document_read`)
- Modify: `projects/hub/packages/core/tests/test_signing.py` (append)
- Modify: `projects/hub/apps/api/src/rebase_api/routers/matches.py` (four routes, the match cancel)
- Modify: `projects/hub/apps/api/tests/test_signing_api.py` (append)
- Modify: `projects/hub/apps/web/src/lib/api.ts`, `pages/admin/Contratti.tsx`, `pages/admin/Contratti.test.tsx`

**Interfaces:**
- Consumes: Tasks 1 to 3 (`DocumensoClient.get`, `DocumensoClient.cancel`, `outcome_from_envelope`, `SigningService.apply`, `finish`, `_lock`, `_lock_match`, `_mail_signing_request`, `_documenso`, `_sender`, `FREELANCER`, `SigningDep`, `sendReportMessage`, `admin.sendMatch`, the Task 2 changes to `Contratti.tsx`); phase 2 (`framework.document_read`, `framework.is_active`, `render.text_version`, `MatchService.cancel`, `MatchService.get`, `MatchService.prefill`, `ContractDocumentRead`, `MatchRead`).
- Produces:
  - `SigningService.refresh(document_id: UUID) -> ContractDocumentRead`, `resend_mail(document_id: UUID, admin_id: UUID) -> ContractDocumentRead`, `cancel_document(document_id: UUID, admin_id: UUID) -> ContractDocumentRead` (framework agreements only), `cancel_match(match_id: UUID, admin_id: UUID) -> MatchRead` (`bozza` or `in_firma`), `record_notice(document_id: UUID, admin_id: UUID) -> ContractDocumentRead`; constants `CANCELLED_BY_REBASE = "Annullato da rebase."`, `CANCELLED_WITH_MATCH = "Annullato da rebase con il suo match."`.
  - `ContractDocumentRead.cancel_reason: str | None`.
  - HTTP, behind the admin cookie: `POST /api/hub/contract-documents/{document_id}/refresh`, `/resend`, `/cancel`, `/notice` → `ContractDocumentRead`; `POST /api/hub/matches/{match_id}/cancel` now also cancels a match `in_firma`. 409 on the wrong state, 502 when Documenso refuses, 503 without Documenso or mail.
  - Web: `ContractDocument.cancel_reason`, `admin.refreshDocument`, `admin.resendDocument`, `admin.cancelDocument`, `admin.recordNotice`; on «Match e contratti» the buttons named «Reinvia email {di che}», «Aggiorna stato {di che}» (`del contratto quadro`, `della lettera n. 2026-001`), «Annulla il contratto quadro», «Registra disdetta», and the confirmations «Sì, annulla il contratto quadro», «Sì, registra la disdetta».

- [ ] **Step 1: Write the failing service tests** (append to `projects/hub/packages/core/tests/test_signing.py`)

Add `NotFound` to the `rebase_core.errors` import.

```python
def _signed_framework(
    clean: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    sender: RecordingSender,
) -> tuple[UUID, UUID, MatchRead, str]:
    """A match sent and its framework agreement signed on Documenso, the webhook never
    heard: (admin, freelancer, match, envelope)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    return admin_id, freelancer_id, match, envelope


def test_refresh_applies_a_signature_the_webhook_never_delivered(clean: Session) -> None:
    """Probe § 11.3: four attempts in 160 ms and then nothing. «Aggiorna stato» reads the
    envelope and does everything the webhook and `finish` would have done."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _admin_id, freelancer_id, match, envelope = _signed_framework(clean, renderer, fake, sender)

    read = _signing(clean, renderer, fake, sender).refresh(_framework_of(clean, freelancer_id).id)

    assert (read.stato, read.attivo, read.ha_pdf_firmato, read.signed_at) == (
        "firmato",
        True,
        True,
        SIGNED_AT,
    )
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)
    assert _letter_of(clean, match.id).stato == "inviato"


def test_refresh_of_a_document_still_waiting_changes_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    mails = len(sender.sent)

    read = _signing(clean, renderer, fake, sender).refresh(_framework_of(clean, freelancer_id).id)

    assert read.stato == "inviato"
    assert len(sender.sent) == mails


def test_refresh_of_a_document_that_never_left_says_so(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="mai partito"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).refresh(
            _framework_of(clean, freelancer_id).id
        )


def test_resend_mails_the_same_link_again_and_leaves_a_trace(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    quadro = _framework_of(clean, freelancer_id)

    _signing(clean, renderer, fake, sender).resend_mail(quadro.id, admin_id)

    first, again = sender.sent[-2:]
    assert first.subject == again.subject == "Da firmare: contratto quadro rebase"
    assert quadro.signing_url is not None and quadro.signing_url in again.text
    trail = AdminActionService(clean).timeline("freelancer", freelancer_id)
    assert trail[0].kind == "mail_resent"


def test_resend_refuses_a_document_that_is_not_waiting(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="aspetta la firma"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).resend_mail(
            _framework_of(clean, freelancer_id).id, admin_id
        )


def test_cancelling_a_framework_out_for_signature_cancels_its_envelope_first(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))

    read = _signing(clean, renderer, fake, sender).cancel_document(
        _framework_of(clean, freelancer_id).id, admin_id
    )

    assert (read.stato, read.cancel_reason) == ("annullato", "Annullato da rebase.")
    assert fake.envelopes[envelope].status == "CANCELLED"
    assert _letter_of(clean, match.id).stato == "in_attesa"
    trail = AdminActionService(clean).timeline("freelancer", freelancer_id)
    assert trail[0].kind == "document_cancelled"


def test_a_cancel_documenso_refuses_leaves_the_document_as_it_was(clean: Session) -> None:
    """The freelancer signed a moment before the admin pressed «Annulla»: Documenso
    cancels only a `PENDING` envelope, and the hub changes nothing."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    admin_id, freelancer_id, _match, _envelope = _signed_framework(clean, renderer, fake, sender)
    with pytest.raises(DocumensoFailed, match="Only pending documents can be cancelled"):
        _signing(clean, renderer, fake, sender).cancel_document(
            _framework_of(clean, freelancer_id).id, admin_id
        )
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_a_letter_is_cancelled_with_its_match_not_alone(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="con il suo match"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).cancel_document(
            _letter_of(clean, match.id).id, admin_id
        )


def test_cancelling_a_match_in_signature_cancels_its_letters_envelope(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))

    read = _signing(clean, renderer, fake, sender).cancel_match(match.id, admin_id)

    assert (read.stato, read.lettera.stato) == ("annullato", "annullato")
    assert _letter_of(clean, match.id).cancel_reason == "Annullato da rebase con il suo match."
    assert fake.envelopes[envelope].status == "CANCELLED"
    assert AdminActionService(clean).timeline("match", match.id)[0].kind == "match_cancelled"


def test_cancelling_a_draft_match_needs_no_documenso(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    read = _signing(clean, renderer, None, RecordingSender()).cancel_match(match.id, admin_id)
    assert read.stato == "annullato"


def test_a_notice_ends_the_active_framework_and_the_next_match_writes_a_new_one(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    active = _active_framework(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    signing = _signing(clean, renderer, FakeDocumenso(), RecordingSender())

    read = signing.record_notice(active.id, admin_id)

    assert (read.stato, read.attivo, read.notice_at) == ("disdetto", False, NOW)
    assert _matches(clean, renderer).prefill(freelancer_id, company_id).quadro_necessario is True
    with pytest.raises(InvalidState, match="attivo"):
        signing.record_notice(active.id, admin_id)
    with pytest.raises(NotFound):
        signing.record_notice(UUID("00000000-0000-7000-8000-000000000000"), admin_id)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py`
Expected: FAIL, `AttributeError: 'SigningService' object has no attribute 'refresh'`.

- [ ] **Step 3: Carry the reason to the pages**

`projects/hub/packages/core/src/rebase_core/contract_schemas.py`, in `ContractDocumentRead` after `notice_at: datetime | None`:

```python
    # Why it became `annullato`: the freelancer's own reason when they refused it, or who
    # cancelled it (REB-387 phase 3).
    cancel_reason: str | None
```

`projects/hub/packages/core/src/rebase_core/framework.py`, in `document_read` after `notice_at=document.notice_at,`:

```python
        cancel_reason=document.cancel_reason,
```

- [ ] **Step 4: The recovery actions** (`projects/hub/packages/core/src/rebase_core/signing.py`)

Extend the imports: `from rebase_core.contract_schemas import ContractDocumentRead, MatchRead, SendReport`, `from rebase_core.contracts.render import Renderer, text_version`, add `outcome_from_envelope` to the `rebase_core.documenso` import and `document_read` to the `rebase_core.framework` import. After `CANCELLED_ON_DOCUMENSO` add:

```python
CANCELLED_BY_REBASE = "Annullato da rebase."
CANCELLED_WITH_MATCH = "Annullato da rebase con il suo match."
```

Before `# ---- plumbing`, add:

```python
    # ---- recovery, and the framework agreement's end -------------------------------------

    def refresh(self, document_id: UUID) -> ContractDocumentRead:
        """«Aggiorna stato»: Documenso's own word on the envelope, applied the way the
        webhook applies it, then whatever a signature still leaves to do (spec § 6, probe
        § 11.3). The net for an event Documenso gave up on, a copy not downloaded yet, a
        letter whose release failed."""
        document = self._document(document_id)
        if document.documenso_id is None:
            raise InvalidState(
                "Questo documento non è mai partito per la firma: non c'è nulla da aggiornare.",
                stato=document.stato,
            )
        envelope = self._documenso().get(document.documenso_id)
        self.session.rollback()
        outcome = outcome_from_envelope(envelope)
        if outcome is not None:
            self.apply(outcome)
        self.finish(document_id)
        return self._read(document_id)

    def resend_mail(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Reinvia email»: the signing mail again, the same link, for a document that
        still waits for the signature."""
        self._sender()
        document = self._document(document_id)
        if document.stato != "inviato" or document.signing_url is None:
            raise InvalidState(
                "Si reinvia la mail solo di un documento che aspetta la firma.",
                stato=document.stato,
            )
        if not self._mail_signing_request(document):
            raise SigningUnavailable(
                "La mail non è partita: il provider l'ha rifiutata. Riprova tra qualche minuto."
            )
        self._record(document, "mail_resent", admin_id)
        return self._read(document_id)

    def cancel_document(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Annulla» on a framework agreement not signed yet: its envelope cancelled on
        Documenso first, under the row's lock (only a `PENDING` one can be, probe § 4),
        then the row. A letter is cancelled with its match. The letters that waited for
        this framework agreement keep waiting: «Invia per la firma» on their match writes
        a new one."""
        document = self._lock(document_id)
        try:
            if document.kind != QUADRO:
                raise InvalidState("Una lettera di incarico si annulla con il suo match.")
            if document.stato not in ("generato", "inviato"):
                raise InvalidState(
                    f"Si annulla solo un contratto quadro non ancora firmato: questo è "
                    f"{document.stato}.",
                    stato=document.stato,
                )
            if document.stato == "inviato" and document.documenso_id is not None:
                self._documenso().cancel(document.documenso_id, CANCELLED_BY_REBASE)
            document.stato = "annullato"
            document.cancel_reason = CANCELLED_BY_REBASE
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self._record(document, "document_cancelled", admin_id)
        return self._read(document_id)

    def cancel_match(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """«Annulla» on a match: a draft as phase 2 cancels it; a match in signature also
        cancels its letter's envelope when the letter is out for signature, so the link
        the freelancer got stops working. The framework agreement is the freelancer's, not
        the match's, and stays."""
        match = self._lock_match(match_id)
        if match.stato == "bozza":
            self.session.rollback()
            return self.matches.cancel(match_id, admin_id)
        letters: list[ContractDocument] = []
        try:
            if match.stato != "in_firma":
                raise InvalidState(
                    f"Si annulla solo un match in bozza o in firma: questo è {match.stato}.",
                    stato=match.stato,
                )
            letters = list(
                self.session.scalars(
                    select(ContractDocument)
                    .where(
                        ContractDocument.match_id == match.id,
                        ContractDocument.stato.in_(("generato", "in_attesa", "inviato")),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for letter in letters:
                if letter.stato == "inviato" and letter.documenso_id is not None:
                    self._documenso().cancel(letter.documenso_id, CANCELLED_BY_REBASE)
                letter.stato = "annullato"
                letter.cancel_reason = CANCELLED_WITH_MATCH
            match.stato = "annullato"
            match.cancelled_at = self.now()
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        AdminActionService(self.session).record(
            ENTITY, match_id, "match_cancelled", admin_id, {"documenti": [d.id for d in letters]}
        )
        return self.matches.get(match_id)

    def record_notice(self, document_id: UUID, admin_id: UUID) -> ContractDocumentRead:
        """«Registra disdetta»: a notice or a withdrawal on an active framework agreement
        (spec § 1d). From now the freelancer has none active, and their next match writes
        a new one."""
        document = self._lock(document_id)
        if not is_active(document):
            self.session.rollback()
            raise InvalidState(
                "Si registra la disdetta solo di un contratto quadro attivo.",
                stato=document.stato,
            )
        document.notice_at = self.now()
        document.stato = "disdetto"
        self.session.commit()
        self._record(document, "notice_recorded", admin_id)
        return self._read(document_id)

    def _document(self, document_id: UUID) -> ContractDocument:
        document = self.session.get(ContractDocument, document_id, populate_existing=True)
        if document is None:
            raise NotFound("documento", document_id)
        return document

    def _read(self, document_id: UUID) -> ContractDocumentRead:
        return document_read(
            self._document(document_id), self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])
        )

    def _record(self, document: ContractDocument, kind: str, admin_id: UUID) -> None:
        """A framework agreement's action lands on its freelancer's trail, since it
        belongs to no match; a letter's on its match's."""
        if document.match_id is not None:
            entity, entity_id = ENTITY, document.match_id
        else:
            entity, entity_id = FREELANCER, document.freelancer_id
        AdminActionService(self.session).record(
            entity, entity_id, kind, admin_id, {"documento": document.id, "kind": document.kind}
        )
```

Append to the module docstring:

```
The recovery actions are the admin's: «Aggiorna stato» (`refresh`) for the event
Documenso gave up on, «Reinvia email» (`resend_mail`), «Annulla» on a framework agreement
(`cancel_document`) or on a match (`cancel_match`), both cancelling the envelope on
Documenso before the row, and «Registra disdetta» (`record_notice`).
```

- [ ] **Step 5: Run the service tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_signing.py projects/hub/packages/core/tests/test_framework.py`
Expected: all pass.

- [ ] **Step 6: Write the failing API tests** (append to `projects/hub/apps/api/tests/test_signing_api.py`)

Add `from datetime import UTC, datetime` and `from typing import Any` to the imports and after `CONTRACTS_MAIL`:

```python
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
```

```python
def _sent(client: TestClient, sender: RecordingSender) -> tuple[dict[str, Any], dict[str, Any]]:
    """A match sent: (the match, the page's framework agreement)."""
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    page = client.get(f"/api/hub/freelancers/{match['freelancer_id']}/matches").json()
    return match, page["quadro"]


def test_without_the_cookie_the_signing_actions_are_401s(client: TestClient, admin: None) -> None:
    for action in ("refresh", "resend", "cancel", "notice"):
        answered = client.post(f"/api/hub/contract-documents/{MISSING}/{action}")
        assert answered.status_code == 401, action


def test_resend_refresh_and_cancel_a_framework_out_for_signature(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match, quadro = _sent(client, sender)
    before = len(sender.sent)

    resent = client.post(f"/api/hub/contract-documents/{quadro['id']}/resend")
    assert resent.status_code == 200 and resent.json()["stato"] == "inviato"
    assert sender.sent[before].subject == "Da firmare: contratto quadro rebase"
    refreshed = client.post(f"/api/hub/contract-documents/{quadro['id']}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["stato"] == "inviato"

    cancelled = client.post(f"/api/hub/contract-documents/{quadro['id']}/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["stato"], cancelled.json()["cancel_reason"]) == (
        "annullato",
        "Annullato da rebase.",
    )
    [envelope] = documenso.envelopes.values()
    assert envelope.status == "CANCELLED"
    letter = client.get(f"/api/hub/matches/{match['id']}").json()["lettera"]
    assert client.post(f"/api/hub/contract-documents/{letter['id']}/cancel").status_code == 409


def test_refresh_recovers_a_signature_and_the_match_can_then_be_cancelled(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    match, quadro = _sent(client, sender)
    [envelope] = documenso.envelopes.values()
    documenso.sign(envelope.id, SIGNED_AT)

    read = client.post(f"/api/hub/contract-documents/{quadro['id']}/refresh").json()

    assert (read["stato"], read["ha_pdf_firmato"], read["attivo"]) == ("firmato", True, True)
    signed = client.get(f"/api/hub/contract-documents/{quadro['id']}/pdf", params={"firmato": "true"})
    assert signed.content == documenso.signed_pdf(envelope.id)
    letter = client.get(f"/api/hub/matches/{match['id']}").json()["lettera"]
    assert letter["stato"] == "inviato"

    cancelled = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["stato"] == "annullato"
    letter_envelope = [e for e in documenso.envelopes.values() if e.id != envelope.id][0]
    assert letter_envelope.status == "CANCELLED"


def test_a_notice_is_recorded_on_an_active_framework_only(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    documenso: FakeDocumenso,
) -> None:
    _match, quadro = _sent(client, sender)
    assert client.post(f"/api/hub/contract-documents/{quadro['id']}/notice").status_code == 409
    [envelope] = documenso.envelopes.values()
    documenso.sign(envelope.id, SIGNED_AT)
    assert client.post(f"/api/hub/contract-documents/{quadro['id']}/refresh").status_code == 200

    noticed = client.post(f"/api/hub/contract-documents/{quadro['id']}/notice")

    assert noticed.status_code == 200
    assert (noticed.json()["stato"], noticed.json()["attivo"]) == ("disdetto", False)
```

- [ ] **Step 7: Run them to see them fail**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_signing_api.py`
Expected: FAIL, 404 or 405 on `/contract-documents/{id}/resend`.

- [ ] **Step 8: The routes** (`projects/hub/apps/api/src/rebase_api/routers/matches.py`)

Add `ContractDocumentRead` to the `contract_schemas` import. Replace `cancel_match` with:

```python
@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(
    admin: AdminDep, session: SessionDep, signing: SigningDep, match_id: UUID
) -> MatchRead:
    """A draft; or a match in signature, whose letter's envelope is cancelled on
    Documenso too."""
    return signing(session).cancel_match(match_id, admin.id)
```

and after `send_match` add:

```python
@router.post("/contract-documents/{document_id}/refresh", response_model=ContractDocumentRead)
def refresh_contract(
    _: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Aggiorna stato»: what Documenso says about the envelope, applied as the webhook
    would, and whatever a signature still leaves to do."""
    return signing(session).refresh(document_id)


@router.post("/contract-documents/{document_id}/resend", response_model=ContractDocumentRead)
def resend_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Reinvia email»: the signing mail again, for a document still waiting."""
    return signing(session).resend_mail(document_id, admin.id)


@router.post("/contract-documents/{document_id}/cancel", response_model=ContractDocumentRead)
def cancel_contract(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Annulla» on a framework agreement not signed yet; a letter goes with its match."""
    return signing(session).cancel_document(document_id, admin.id)


@router.post("/contract-documents/{document_id}/notice", response_model=ContractDocumentRead)
def record_contract_notice(
    admin: AdminDep, session: SessionDep, signing: SigningDep, document_id: UUID
) -> ContractDocumentRead:
    """«Registra disdetta» on an active framework agreement."""
    return signing(session).record_notice(document_id, admin.id)
```

- [ ] **Step 9: Run the API suite**

Run: `uv run pytest -q projects/hub/apps/api/tests`
Expected: all pass, phase 2's `test_matches_api.py` cancel tests included (a draft still cancels through `SigningService.cancel_match`).

- [ ] **Step 10: Write the failing page tests** (append inside the `describe` of `projects/hub/apps/web/src/pages/admin/Contratti.test.tsx`)

```tsx
  const OUT = {
    ...QUADRO,
    stato: 'inviato',
    attivo: false,
    signed_at: null,
    ha_pdf_firmato: false,
    rinnovo: null,
    ultimo_giorno_disdetta: null,
    cancel_reason: null,
  }

  it('resends the signing mail and refreshes a framework agreement out for signature (REB-391)', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, quadro: OUT, quadri: [OUT] },
      'POST /api/hub/contract-documents/d1/resend': OUT,
      'POST /api/hub/contract-documents/d1/refresh': OUT,
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Reinvia email del contratto quadro' }))
    expect(await screen.findByText('Mail inviata di nuovo.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Aggiorna stato del contratto quadro' }))
    expect(await screen.findByText('Stato letto da Documenso.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/resend', expect.objectContaining({ method: 'POST' }))
    expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/refresh', expect.objectContaining({ method: 'POST' }))
  })

  it('cancels a framework agreement out for signature after asking, and says why it is cancelled', async () => {
    let cancelled = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () => {
        const quadro = cancelled ? { ...OUT, stato: 'annullato', cancel_reason: 'Annullato da rebase.' } : OUT
        return { ...PAGE, quadro, quadri: [quadro] }
      },
      'POST /api/hub/contract-documents/d1/cancel': () => {
        cancelled = true
        return { ...OUT, stato: 'annullato', cancel_reason: 'Annullato da rebase.' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla il contratto quadro' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Sì, annulla il contratto quadro' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Annullato da rebase.')).toBeInTheDocument()
  })

  it('records a notice on an active framework agreement after asking', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'POST /api/hub/contract-documents/d1/notice': { ...QUADRO, stato: 'disdetto', attivo: false },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Registra disdetta' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Sì, registra la disdetta' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d1/notice', expect.objectContaining({ method: 'POST' })),
    )
    expect(await screen.findByText('Disdetta registrata.')).toBeInTheDocument()
  })

  it('shows why a letter was cancelled and offers no signing action on it', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [
          {
            ...MATCH,
            stato: 'in_firma',
            lettera: { ...LETTERA, stato: 'annullato', cancel_reason: 'Rifiutato dal freelance: il compenso è sbagliato' },
          },
        ],
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByText('Rifiutato dal freelance: il compenso è sbagliato')).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Reinvia|Aggiorna stato|Invia per la firma/ })).toBeNull()
    expect(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' })).toBeInTheDocument()
  })

  it('warns that cancelling a match whose letter left stops the freelancer’s link', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': {
        ...PAGE,
        matches: [{ ...MATCH, stato: 'in_firma', lettera: { ...LETTERA, stato: 'inviato' } }],
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByRole('button', { name: 'Reinvia email della lettera n. 2026-001' })).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' }))
    expect(await screen.findByText(/il link ricevuto dal freelance smette di funzionare/)).toBeInTheDocument()
  })
```

- [ ] **Step 11: Run them to see them fail**

Run: `pnpm --filter hub exec vitest run src/pages/admin/Contratti.test.tsx`
Expected: FAIL, no «Reinvia email del contratto quadro» button.

- [ ] **Step 12: `api.ts`**

In `ContractDocument`, after `notice_at: string | null`:

```ts
  /** Why it was cancelled: the freelancer's reason when they refused it, or who did (REB-391). */
  cancel_reason: string | null
```

Inside `admin`, after `sendMatch`:

```ts
  /** «Aggiorna stato» (REB-391): what Documenso says, applied as the webhook would. */
  refreshDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/refresh`, { method: 'POST' }),
  /** «Reinvia email»: the signing mail again, for a document still waiting. */
  resendDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/resend`, { method: 'POST' }),
  /** «Annulla» on a framework agreement not signed yet. */
  cancelDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/cancel`, { method: 'POST' }),
  /** «Registra disdetta» on an active framework agreement. */
  recordNotice: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/notice`, { method: 'POST' }),
```

- [ ] **Step 13: The page's signing actions** (`projects/hub/apps/web/src/pages/admin/Contratti.tsx`)

Leave `FiscalFields`, `DocumentLinks` and `FiscalSection` as they are. After `DocumentLinks`, add:

```tsx
/** How a button's label names a document: «del contratto quadro», «della lettera n. 2026-001». */
function whatOf(document: ContractDocument): string {
  return document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
}

/** The signing actions a document has in its state (REB-391): «Reinvia email» while it
 *  waits for the signature; «Aggiorna stato» while Documenso may know more than the hub
 *  (a lost webhook, a signed copy not downloaded yet, letters a signed framework
 *  agreement has still to release). */
function SigningActions({
  document,
  busy,
  onRefresh,
  onResend,
}: {
  document: ContractDocument
  busy: boolean
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
}) {
  const what = whatOf(document)
  const refreshable =
    document.stato === 'inviato' ||
    (document.stato === 'firmato' && (document.kind === 'quadro' || !document.ha_pdf_firmato))
  return (
    <>
      {document.stato === 'inviato' && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Reinvia email ${what}`}
          onClick={() => onResend(document)}
        >
          Reinvia email
        </Button>
      )}
      {refreshable && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Aggiorna stato ${what}`}
          onClick={() => onRefresh(document)}
        >
          Aggiorna stato
        </Button>
      )}
    </>
  )
}

/** A question before an action that cannot be taken back. */
function Confirm({
  open,
  title,
  description,
  confirm,
  pending,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirm: string
  pending: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Indietro
          </Button>
          <Button type="button" variant="destructive" disabled={pending} onClick={onConfirm}>
            {confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function cancelDescription(match: Match): string {
  const base = `Il match con ${match.nome_azienda} e la lettera n. ${match.lettera.numero} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`
  return match.lettera.stato === 'inviato'
    ? `${base} La lettera è già partita: viene annullata anche sul sito di firma, e il link ricevuto dal freelance smette di funzionare.`
    : base
}
```

Replace `FrameworkSection` with:

```tsx
function FrameworkSection({
  quadro,
  busy,
  onRefresh,
  onResend,
  onCancel,
  onNotice,
}: {
  quadro: ContractDocument | null
  busy: boolean
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
  onCancel: () => void
  onNotice: () => void
}) {
  return (
    <section aria-labelledby="contratti-quadro" className="space-y-3 px-6 py-6">
      <h2 id="contratti-quadro" className="text-sm font-medium">
        Contratto quadro
      </h2>
      {quadro === null ? (
        <p className="text-sm text-muted-foreground">Nessun contratto quadro: lo genera il primo match.</p>
      ) : (
        <dl className="space-y-3 text-sm">
          <Row label="Stato">
            <span className="flex flex-wrap items-center gap-2">
              {/* An element of its own, so a test finds the state by its words alone. */}
              <span>{DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</span>
              {quadro.attivo && <Badge variant="pill">Attivo</Badge>}
              {quadro.testo_bozza && <Badge variant="pill">Testo in bozza</Badge>}
            </span>
          </Row>
          {quadro.cancel_reason && <Row label="Perché">{quadro.cancel_reason}</Row>}
          <Row label="Firmato il">{quadro.signed_at ? formatDate(quadro.signed_at) : 'non ancora'}</Row>
          <Row label="Prossimo rinnovo">{quadro.rinnovo ? formatDate(quadro.rinnovo) : 'dopo la firma'}</Row>
          <Row label="Ultimo giorno per la disdetta">
            {quadro.ultimo_giorno_disdetta ? formatDate(quadro.ultimo_giorno_disdetta) : 'dopo la firma'}
          </Row>
          <Row label="Versione del testo">
            <span className="flex flex-wrap items-center gap-2">
              <span>{quadro.text_version}</span>
              {quadro.nuova_versione && <Badge variant="pill">Nuova versione disponibile</Badge>}
            </span>
          </Row>
          <Row label="Documento">
            <DocumentLinks document={quadro} />
          </Row>
          {quadro.stato !== 'annullato' && quadro.stato !== 'disdetto' && (
            <Row label="Azioni">
              <span className="flex flex-wrap gap-2">
                <SigningActions document={quadro} busy={busy} onRefresh={onRefresh} onResend={onResend} />
                {(quadro.stato === 'generato' || quadro.stato === 'inviato') && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    aria-label="Annulla il contratto quadro"
                    onClick={onCancel}
                  >
                    Annulla
                  </Button>
                )}
                {quadro.attivo && (
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onNotice}>
                    Registra disdetta
                  </Button>
                )}
              </span>
            </Row>
          )}
        </dl>
      )}
    </section>
  )
}
```

Replace `MatchesSection` with:

```tsx
function MatchesSection({
  matches,
  busy,
  canSend,
  onSend,
  onRefresh,
  onResend,
  onCancel,
  onClose,
  error,
}: {
  matches: Match[]
  busy: boolean
  canSend: (match: Match) => boolean
  onSend: (match: Match) => void
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
  onCancel: (match: Match) => void
  onClose: (match: Match) => void
  error: string | null
}) {
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Match</h2>
      {matches.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessun match per questa persona.</p>
      ) : (
        <div className="overflow-x-auto border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Azienda</TableHead>
                <TableHead>Creato</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>Lettera di incarico</TableHead>
                <TableHead>
                  <span className="sr-only">Azioni</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {matches.map((match) => (
                <TableRow key={match.id}>
                  <TableCell>
                    <p className="font-medium">{match.nome_azienda}</p>
                    <p className="text-xs text-muted-foreground">{match.figura_richiesta}</p>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(match.created_at)}</TableCell>
                  <TableCell>
                    <Badge variant="pill">{MATCH_STATE_LABELS[match.stato] ?? match.stato}</Badge>
                  </TableCell>
                  <TableCell className="space-y-1">
                    <p className="text-sm">
                      n. {match.lettera.numero} · {DOCUMENT_STATE_LABELS[match.lettera.stato] ?? match.lettera.stato}
                    </p>
                    {match.lettera.cancel_reason && (
                      <p className="text-xs text-muted-foreground">{match.lettera.cancel_reason}</p>
                    )}
                    <DocumentLinks document={match.lettera} />
                  </TableCell>
                  <TableCell>
                    <span className="flex flex-wrap justify-end gap-2">
                      {canSend(match) && (
                        <Button
                          type="button"
                          size="sm"
                          disabled={busy}
                          aria-label={`Invia per la firma il match con ${match.nome_azienda}`}
                          onClick={() => onSend(match)}
                        >
                          Invia per la firma
                        </Button>
                      )}
                      <SigningActions document={match.lettera} busy={busy} onRefresh={onRefresh} onResend={onResend} />
                      {(match.stato === 'bozza' || match.stato === 'in_firma') && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`Annulla il match con ${match.nome_azienda}`}
                          onClick={() => onCancel(match)}
                        >
                          Annulla
                        </Button>
                      )}
                      {match.stato === 'attivo' && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`Chiudi il match con ${match.nome_azienda}`}
                          onClick={() => onClose(match)}
                        >
                          Chiudi match
                        </Button>
                      )}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}
```

Replace `AdminContratti` with:

```tsx
/** «Match e contratti» (REB-387): the framework agreement with its dates and its signing
 *  actions, the tax data, and every match with its letter. «Invia per la firma» sends a
 *  match's document (REB-390); «Reinvia email», «Aggiorna stato», «Annulla» and
 *  «Registra disdetta» follow the signature (REB-391). */
export function AdminContratti() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/contracts' })
  const client = useQueryClient()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const contracts = useQuery({ queryKey: ['contracts', id], queryFn: () => admin.contracts(id) })
  const [message, setMessage] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<Match | null>(null)
  const [confirmingQuadro, setConfirmingQuadro] = useState<'annulla' | 'disdetta' | null>(null)
  const refresh = () => void client.invalidateQueries({ queryKey: ['contracts', id] })
  const saying = (sentence: string) => () => {
    setMessage(sentence)
    refresh()
  }
  const send = useMutation({
    mutationFn: (matchId: string) => admin.sendMatch(matchId),
    onSuccess: (report) => {
      setMessage(sendReportMessage(report))
      refresh()
    },
  })
  const cancel = useMutation({ mutationFn: (matchId: string) => admin.cancelMatch(matchId), onSuccess: refresh })
  const close = useMutation({ mutationFn: (matchId: string) => admin.closeMatch(matchId), onSuccess: refresh })
  const resend = useMutation({
    mutationFn: (documentId: string) => admin.resendDocument(documentId),
    onSuccess: saying('Mail inviata di nuovo.'),
  })
  const update = useMutation({
    mutationFn: (documentId: string) => admin.refreshDocument(documentId),
    onSuccess: saying('Stato letto da Documenso.'),
  })
  const cancelQuadro = useMutation({
    mutationFn: (documentId: string) => admin.cancelDocument(documentId),
    onSuccess: saying('Contratto quadro annullato.'),
  })
  const notice = useMutation({
    mutationFn: (documentId: string) => admin.recordNotice(documentId),
    onSuccess: saying('Disdetta registrata.'),
  })
  const actions = [send, cancel, close, resend, update, cancelQuadro, notice]

  if (contracts.isError) return <Empty>Non riesco a leggere i contratti di questa persona.</Empty>
  if (contracts.isPending) return <Empty>Caricamento…</Empty>
  const data = contracts.data
  const quadro = data.quadro
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const busy = actions.some((action) => action.isPending)
  const actionError = actions.map((action) => action.error).find((error) => error !== null) ?? null
  const actionFailure =
    actionError instanceof ApiError
      ? actionError.message
      : actionError
        ? 'Non riesco a completare l’operazione.'
        : null
  // A draft leaves on request; a match in signature only when its letter still waits and
  // no framework agreement is out for signature to carry it (a cancelled or refused one,
  // or a signed one whose release failed).
  const canSend = (match: Match) =>
    match.stato === 'bozza' ||
    (match.stato === 'in_firma' && match.lettera.stato === 'in_attesa' && quadro?.stato !== 'inviato')
  const onRefresh = (document: ContractDocument) => {
    setMessage(null)
    update.mutate(document.id)
  }
  const onResend = (document: ContractDocument) => {
    setMessage(null)
    resend.mutate(document.id)
  }
  return (
    <>
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'} />
      <FrameworkSection
        quadro={quadro}
        busy={busy}
        onRefresh={onRefresh}
        onResend={onResend}
        onCancel={() => setConfirmingQuadro('annulla')}
        onNotice={() => setConfirmingQuadro('disdetta')}
      />
      <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaved={refresh} />
      {message && (
        <p role="status" className="px-6 pb-3 text-sm">
          {message}
        </p>
      )}
      <MatchesSection
        matches={data.matches}
        busy={busy}
        canSend={canSend}
        onSend={(match) => {
          setMessage(null)
          send.mutate(match.id)
        }}
        onRefresh={onRefresh}
        onResend={onResend}
        onCancel={setConfirming}
        onClose={(match) => close.mutate(match.id)}
        error={actionFailure}
      />
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Torna alla scheda
        </Link>
      </p>
      <Confirm
        open={confirming !== null}
        title="Annullare il match?"
        description={confirming ? cancelDescription(confirming) : ''}
        confirm={cancel.isPending ? 'Annullo…' : 'Annulla il match'}
        pending={cancel.isPending}
        onConfirm={() => {
          if (confirming) cancel.mutate(confirming.id, { onSettled: () => setConfirming(null) })
        }}
        onClose={() => setConfirming(null)}
      />
      <Confirm
        open={confirmingQuadro === 'annulla'}
        title="Annullare il contratto quadro?"
        description="Se è già partito, viene annullato anche sul sito di firma e il link ricevuto dal freelance smette di funzionare. Le lettere che lo aspettano restano in attesa: «Invia per la firma» sul loro match ne genera uno nuovo."
        confirm={cancelQuadro.isPending ? 'Annullo…' : 'Sì, annulla il contratto quadro'}
        pending={cancelQuadro.isPending}
        onConfirm={() => {
          if (quadro) cancelQuadro.mutate(quadro.id, { onSettled: () => setConfirmingQuadro(null) })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
      <Confirm
        open={confirmingQuadro === 'disdetta'}
        title="Registrare la disdetta?"
        description="Da oggi il contratto quadro non è più attivo, e il prossimo match ne genera uno nuovo. Si registra quando il freelance o rebase ha dato disdetta, o uno dei due ha receduto."
        confirm={notice.isPending ? 'Registro…' : 'Sì, registra la disdetta'}
        pending={notice.isPending}
        onConfirm={() => {
          if (quadro) notice.mutate(quadro.id, { onSettled: () => setConfirmingQuadro(null) })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
    </>
  )
}
```

The `Dialog*` imports stay; `type ContractDocument` is already imported from `@/lib/api`.

- [ ] **Step 14: Run the web checks**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: green, phase 2's and Task 2's `Contratti.test.tsx` cases included.

- [ ] **Step 15: Whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green (`test_match_tools.py` reads `cancel_reason` as one more key of every document, harmlessly).

- [ ] **Step 16: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/framework.py \
  projects/hub/packages/core/tests/test_signing.py \
  projects/hub/apps/api/src/rebase_api/routers/matches.py \
  projects/hub/apps/api/tests/test_signing_api.py \
  projects/hub/apps/web/src/lib/api.ts \
  projects/hub/apps/web/src/pages/admin/Contratti.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.test.tsx
git commit -F - <<'EOF'
feat(hub): refresh, resend, cancel and record a notice on a contract

I add the admin's actions after «Invia per la firma». «Aggiorna stato»
reads the envelope from Documenso and applies it the way the webhook does,
then finishes whatever the signature still leaves to do, which recovers an
event Documenso gave up on. «Reinvia email» sends the same link again.
«Annulla» cancels a framework agreement, or a match in signature with its
letter, on Documenso first and then in the hub. «Registra disdetta» ends an
active framework agreement. The page shows why a document was cancelled.

REB-391.
EOF
```

---
### Task 5: The member's «Contratti» (REB-392)

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py` (append `MemberContract`, `MemberContracts`)
- Create: `projects/hub/packages/core/src/rebase_core/member_contracts.py`
- Create: `projects/hub/packages/core/tests/test_member_contracts.py`
- Modify: `projects/hub/apps/api/src/rebase_api/routers/members.py` (two routes)
- Create: `projects/hub/apps/api/tests/test_member_contracts_api.py`
- Modify: `projects/hub/apps/web/src/lib/api.ts`, `lib/format.ts`
- Create: `projects/hub/apps/web/src/pages/member/Contratti.tsx`, `pages/member/Contratti.test.tsx`
- Modify: `projects/hub/apps/web/src/pages/member/Area.tsx`, `pages/member/Area.test.tsx`

**Interfaces:**
- Consumes: Task 2 (`SigningService.send_match`, the test helpers of `test_signing.py`, imported by bare name as this repository's suites import their neighbours: `SIGNED_AT`, `TABLES`, `_card`, `_draft`, `_framework_of`, `_setup`, `_signing`; `contract_flow.*`); Task 3 (`SigningService.apply`, `finish`, `test_signing._webhook`, `test_signing._envelope_of`, `deps.get_session_opener`, the webhook route); phase 2 (`framework.{is_active, signed_on, next_renewal, last_notice_day, rome_today}`, `MatchService.document_pdf`, `matches.{QUADRO, LETTERA}`, `contract_schemas.ContractPdf`, `downloads.pdf_response`); main (`MemberService.require_card`, `deps.MeDep`, `ratelimit.reset_rate_limit`); the web's `DocumentStato` type in `api.ts` (phase 2).
- Produces:
  - `rebase_core.contract_schemas.MemberContract(id, kind, numero, stato, cliente, inizio, fine, sent_at, signed_at, signing_url, ha_pdf_firmato, attivo, rinnovo, ultimo_giorno_disdetta)`, `MemberContracts(quadro: MemberContract | None, lettere: list[MemberContract])`.
  - `rebase_core.member_contracts.MemberContractService(session: Session, today: Callable[[], date] = rome_today)` with `for_user(user_id: UUID) -> MemberContracts` and `signed_pdf(user_id: UUID, document_id: UUID) -> ContractPdf`; both raise `NotFound("scheda")` for a person with no card, and `signed_pdf` raises `NotFound("documento")` for anybody else's document and for an unsigned one.
  - HTTP behind `MeDep`: `GET /api/hub/me/contracts` → `MemberContracts`; `GET /api/hub/me/contracts/{document_id}/pdf` → the signed copy as an attachment; 401 without the cookie, 404 for someone else's document.
  - Web: `MemberContract`, `MemberContracts`, `member.contracts()`, `member.contractPdfUrl(documentId)` in `api.ts`; `MEMBER_DOCUMENT_STATE_LABELS` in `format.ts`; `MemberContratti` in `pages/member/Contratti.tsx`, rendered by `Area` for a person with a card.

- [ ] **Step 1: Write the failing service tests** (`projects/hub/packages/core/tests/test_member_contracts.py`)

```python
"""REB-387, spec § 4: the member area's «Contratti», a freelancer's own documents and
nobody else's. The admin side is `test_signing.py`, whose helpers this file borrows."""

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_signing import (
    SIGNED_AT,
    TABLES,
    _card,
    _draft,
    _envelope_of,
    _framework_of,
    _setup,
    _signing,
    _webhook,
)

from rebase_core.contract_schemas import MemberContracts
from rebase_core.errors import NotFound
from rebase_core.mail import RecordingSender
from rebase_core.member_contracts import MemberContractService
from rebase_core.models import Freelancer

# The day after the signature: the next renewal is a year on.
AFTER = date(2026, 10, 2)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _user_of(session: Session, freelancer_id: UUID) -> UUID:
    freelancer = session.get(Freelancer, freelancer_id)
    assert freelancer is not None
    return freelancer.user_id


def _service(session: Session) -> MemberContractService:
    return MemberContractService(session, today=lambda: AFTER)


def test_the_freelancer_sees_what_reached_them_and_the_link_to_sign(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    quadro = _framework_of(clean, freelancer_id)
    assert mine.quadro is not None
    assert (mine.quadro.id, mine.quadro.stato) == (quadro.id, "inviato")
    assert mine.quadro.signing_url == quadro.signing_url
    [lettera] = mine.lettere
    assert (lettera.numero, lettera.stato, lettera.cliente) == (
        match.lettera.numero,
        "in_attesa",
        "ACME S.r.l.",
    )
    assert lettera.inizio == "1° ottobre 2026"
    assert lettera.signing_url is None


def test_a_draft_match_shows_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _draft(clean, FakeRenderer(draft=False), freelancer_id, company_id, admin_id)
    assert _service(clean).for_user(_user_of(clean, freelancer_id)) == MemberContracts(
        quadro=None, lettere=[]
    )


def test_a_signed_framework_shows_its_dates_its_copy_and_no_link(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)
    user_id = _user_of(clean, freelancer_id)

    mine = _service(clean).for_user(user_id)

    assert mine.quadro is not None
    assert (mine.quadro.stato, mine.quadro.attivo, mine.quadro.signing_url) == ("firmato", True, None)
    assert mine.quadro.ha_pdf_firmato is True
    assert (mine.quadro.rinnovo, mine.quadro.ultimo_giorno_disdetta) == (
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    copy = _service(clean).signed_pdf(user_id, signed)
    assert copy.content == fake.signed_pdf(envelope)
    assert copy.filename == "contratto-quadro-v0.1-firmato.pdf"
    [lettera] = mine.lettere
    assert lettera.stato == "inviato" and lettera.signing_url is not None


def test_a_cancelled_document_says_so_and_offers_no_link(clean: Session) -> None:
    """Probe § 11.10: Documenso's page still opens a cancelled document and fails only at
    the click. The member area is where the person reads that it is not to be signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "Il domicilio è sbagliato")
    signing.apply(_webhook(fake, envelope, "DOCUMENT_REJECTED"))

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.quadro is not None
    assert (mine.quadro.stato, mine.quadro.signing_url) == ("annullato", None)


def test_someone_elses_document_and_an_unsigned_copy_are_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    grace_id = _card(clean, email="grace@studio.it", nome="Grace")
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    quadro = _framework_of(clean, freelancer_id)
    grace, ada = _user_of(clean, grace_id), _user_of(clean, freelancer_id)

    with pytest.raises(NotFound):
        _service(clean).signed_pdf(grace, quadro.id)
    with pytest.raises(NotFound):
        _service(clean).signed_pdf(ada, quadro.id)
    assert _service(clean).for_user(grace) == MemberContracts(quadro=None, lettere=[])


def test_a_person_without_a_card_has_no_contracts(clean: Session) -> None:
    admin_id, _freelancer_id, _company_id = _setup(clean)
    with pytest.raises(NotFound, match="scheda"):
        _service(clean).for_user(admin_id)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_member_contracts.py`
Expected: collection error, `No module named 'rebase_core.member_contracts'`.

- [ ] **Step 3: The read models** (`projects/hub/packages/core/src/rebase_core/contract_schemas.py`, append)

```python
class MemberContract(BaseModel):
    """A contract as its freelancer reads it in «Contratti» (REB-387, spec § 4): never
    the PDF's bytes and never `data`. `cliente` and the two dates are a letter's, as the
    letter prints them; `signing_url` is there only while the document waits for the
    signature, since its path is the signer's token."""

    id: UUID
    kind: str
    numero: str | None
    stato: str
    cliente: str | None
    inizio: str | None
    fine: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    ha_pdf_firmato: bool
    attivo: bool
    rinnovo: date | None
    ultimo_giorno_disdetta: date | None


class MemberContracts(BaseModel):
    """«Contratti»: the framework agreement (the active one, else the newest that reached
    the person), and the letters newest first."""

    quadro: MemberContract | None
    lettere: list[MemberContract]
```

- [ ] **Step 4: The service** (`projects/hub/packages/core/src/rebase_core/member_contracts.py`)

```python
"""A freelancer's own contracts, for the member area's «Contratti» (REB-387, spec § 4 and
§ 1g).

Read-only, and only ever the caller's: the card comes from the session's user
(`MemberService.require_card`), never from the URL, and a document that is not theirs is
the same 404 as one that does not exist, never a 403. The person sees what has reached
them: every document that went out for signature, and a letter that waits for its
framework agreement once its match was sent. A draft match, and a document cancelled
before it left, are the admin's business. The signing link is shown only while the
document waits for the signature: its path is the signer's token (probe § 4). A cancelled
document's page still opens on Documenso and fails only at the click (probe § 11.10), so
the state shown here is what tells the person not to sign it.
"""

from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractPdf, MemberContract, MemberContracts
from rebase_core.errors import NotFound
from rebase_core.framework import is_active, last_notice_day, next_renewal, rome_today, signed_on
from rebase_core.matches import LETTERA, QUADRO, MatchService
from rebase_core.members import MemberService
from rebase_core.models import ContractDocument, Match


def _visible(document: ContractDocument, match: Match | None) -> bool:
    if document.sent_at is not None:
        return True
    return (
        document.kind == LETTERA
        and document.stato == "in_attesa"
        and match is not None
        and match.stato == "in_firma"
    )


def _printed(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _read(document: ContractDocument, match: Match | None, today: date) -> MemberContract:
    active = is_active(document)
    signed = signed_on(document)
    renewal = next_renewal(signed, today) if active and signed is not None else None
    data = document.data or {}
    return MemberContract(
        id=document.id,
        kind=document.kind,
        numero=document.numero,
        stato=document.stato,
        cliente=match.cliente_ragione_sociale if match is not None else None,
        inizio=_printed(data.get("data-inizio")),
        fine=_printed(data.get("data-fine")),
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        signing_url=document.signing_url if document.stato == "inviato" else None,
        ha_pdf_firmato=document.signed_pdf is not None,
        attivo=active,
        rinnovo=renewal,
        ultimo_giorno_disdetta=last_notice_day(renewal) if renewal is not None else None,
    )


class MemberContractService:
    def __init__(self, session: Session, today: Callable[[], date] = rome_today) -> None:
        self.session = session
        self.today = today

    def for_user(self, user_id: UUID) -> MemberContracts:
        freelancer = MemberService(self.session).require_card(user_id)
        rows = self.session.execute(
            select(ContractDocument, Match)
            .outerjoin(Match, Match.id == ContractDocument.match_id)
            .where(ContractDocument.freelancer_id == freelancer.id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        ).all()
        today = self.today()
        visible = [(document, match) for document, match in rows if _visible(document, match)]
        quadri = [_read(document, None, today) for document, _ in visible if document.kind == QUADRO]
        quadro = next((q for q in quadri if q.attivo), None) or (quadri[0] if quadri else None)
        lettere = [
            _read(document, match, today) for document, match in visible if document.kind == LETTERA
        ]
        return MemberContracts(quadro=quadro, lettere=lettere)

    def signed_pdf(self, user_id: UUID, document_id: UUID) -> ContractPdf:
        freelancer = MemberService(self.session).require_card(user_id)
        document = self.session.get(ContractDocument, document_id)
        if (
            document is None
            or document.freelancer_id != freelancer.id
            or document.signed_pdf is None
        ):
            raise NotFound("documento", document_id)
        return MatchService(self.session).document_pdf(document_id, signed=True)
```

- [ ] **Step 5: Run the service tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_member_contracts.py`
Expected: all pass.

- [ ] **Step 6: Write the failing API tests** (`projects/hub/apps/api/tests/test_member_contracts_api.py`)

```python
"""REB-387, spec § 4 over HTTP: `/me/contracts` answers the caller's own documents from
the session, and someone else's document is a 404, never a 403."""

import json
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from contract_flow import (
    ADMIN_EMAIL,
    FREELANCER_EMAIL,
    PDF,
    SIGNER,
    TABLES,
    draft_match,
    enter,
)
from fakes_contracts import FakeRenderer
from fakes_documenso import SIGNING_HOST, FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer, get_sender, get_session_opener
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, User

SECRET = "segreto-del-webhook-di-prova"
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def documenso(client: TestClient, api_session: Session) -> Iterator[FakeDocumenso]:
    fake = FakeDocumenso()
    renderer = FakeRenderer(draft=False)
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_documenso] = fake.client
    overrides[get_renderer] = lambda: renderer
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, signer_json=json.dumps(SIGNER), documenso_webhook_secret=SECRET
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _quadro(session: Session) -> ContractDocument:
    session.expire_all()
    return session.scalars(select(ContractDocument).where(ContractDocument.kind == "quadro")).one()


def test_without_the_cookie_the_contracts_are_a_401(client: TestClient, admin: None) -> None:
    assert client.get("/api/hub/me/contracts").status_code == 401
    missing = "00000000-0000-7000-8000-000000000000"
    assert client.get(f"/api/hub/me/contracts/{missing}/pdf").status_code == 401


def test_a_freelancer_reads_their_contracts_and_downloads_the_signed_copy(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    reset_rate_limit()
    enter(client, sender, FREELANCER_EMAIL)

    mine = client.get("/api/hub/me/contracts")

    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["quadro"]["stato"] == "inviato"
    assert body["quadro"]["signing_url"].startswith(f"{SIGNING_HOST}/sign/")
    assert [lettera["stato"] for lettera in body["lettere"]] == ["in_attesa"]
    assert "777.77" not in mine.text

    envelope = _quadro(api_session).documenso_id
    assert envelope is not None
    documenso.sign(envelope, SIGNED_AT)
    delivered = client.post(
        "/api/hub/documenso/webhook",
        json=documenso.webhook(envelope, "DOCUMENT_COMPLETED"),
        headers={"X-Documenso-Secret": SECRET},
    )
    assert delivered.status_code == 200
    quadro_id = body["quadro"]["id"]
    copy = client.get(f"/api/hub/me/contracts/{quadro_id}/pdf")
    assert copy.status_code == 200
    assert copy.content == documenso.signed_pdf(envelope)
    assert copy.headers["content-type"].startswith("application/pdf")
    assert client.get("/api/hub/me/contracts").json()["quadro"]["signing_url"] is None


def test_someone_elses_document_is_a_404_never_a_403(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    ada_quadro = _quadro(api_session)
    reset_rate_limit()
    applied = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Grace",
            "cognome": "Hopper",
            "email": "grace@studio.it",
            "tariffa_giornaliera": "500",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Grace CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text
    enter(client, sender, "grace@studio.it")

    assert client.get(f"/api/hub/me/contracts/{ada_quadro.id}/pdf").status_code == 404
    assert client.get("/api/hub/me/contracts").json() == {"quadro": None, "lettere": []}
```

- [ ] **Step 7: Run them to see them fail**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_member_contracts_api.py`
Expected: FAIL, 404 on `/api/hub/me/contracts` (no such route).

- [ ] **Step 8: The two routes** (`projects/hub/apps/api/src/rebase_api/routers/members.py`)

Add `from uuid import UUID`, add `pdf_response` to the `rebase_api.downloads` import, and import `from rebase_core.contract_schemas import MemberContracts` and `from rebase_core.member_contracts import MemberContractService`. After `my_guide` add:

```python
@router.get("/me/contracts", response_model=MemberContracts)
def my_contracts(me: MeDep, session: SessionDep) -> MemberContracts:
    """«Contratti» (REB-387): the caller's own framework agreement and letters, read from
    the session and never from the URL. A person with no card is a 404 named «scheda»,
    as `/me/cv` is."""
    return MemberContractService(session).for_user(me.id)


@router.get("/me/contracts/{document_id}/pdf")
def my_signed_contract(me: MeDep, session: SessionDep, document_id: UUID) -> Response:
    """The signed copy of one of the caller's own documents. Someone else's document, and
    one not signed yet, are the same 404 as a document that does not exist: a 403 would
    say it exists."""
    pdf = MemberContractService(session).signed_pdf(me.id, document_id)
    return pdf_response(pdf.filename, pdf.content)
```

Add to the module docstring, after the `/me/company` sentences: «`GET /me/contracts` (REB-387) is the same discipline for the contracts: the caller's own, a 404 for anybody else's.»

- [ ] **Step 9: Run the API tests**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_member_contracts_api.py projects/hub/apps/api/tests/test_member_api.py`
Expected: all pass.

- [ ] **Step 10: Write the failing web tests**

`projects/hub/apps/web/src/pages/member/Contratti.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/lib/format'
import { MemberContratti } from './Contratti'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const QUADRO = {
  id: 'd1',
  kind: 'quadro',
  numero: null,
  stato: 'inviato',
  cliente: null,
  inizio: null,
  fine: null,
  sent_at: '2026-09-23T10:00:00Z',
  signed_at: null,
  signing_url: 'https://firma.letsrebase.com/sign/abc',
  ha_pdf_firmato: false,
  attivo: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
}
const LETTERA = {
  ...QUADRO,
  id: 'd2',
  kind: 'lettera',
  numero: '2026-001',
  stato: 'in_attesa',
  cliente: 'ACME S.r.l.',
  inizio: '1° ottobre 2026',
  fine: '29 gennaio 2027',
  sent_at: null,
  signing_url: null,
}

function mount(contracts: unknown) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer(200, contracts))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemberContratti />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Contratti» in the member area (REB-392)', () => {
  it('offers «Firma il documento» on what waits for a signature, and nothing on a letter that waits for its turn', async () => {
    mount({ quadro: QUADRO, lettere: [LETTERA] })
    expect(await screen.findByRole('link', { name: 'Firma il contratto quadro' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/abc',
    )
    expect(screen.getByText('Da firmare')).toBeInTheDocument()
    const letter = screen.getByText('Lettera di incarico n. 2026-001').closest('li')!
    expect(within(letter).getByText('Parte dopo la firma del contratto quadro')).toBeInTheDocument()
    expect(within(letter).getByText('ACME S.r.l., dal 1° ottobre 2026 al 29 gennaio 2027')).toBeInTheDocument()
    expect(within(letter).queryByRole('link')).toBeNull()
  })

  it('offers the signed copy and the framework agreement’s dates once signed, and no link to sign', async () => {
    mount({
      quadro: {
        ...QUADRO,
        stato: 'firmato',
        signing_url: null,
        signed_at: '2026-10-01T09:00:00Z',
        ha_pdf_firmato: true,
        attivo: true,
        rinnovo: '2027-10-01',
        ultimo_giorno_disdetta: '2027-09-01',
      },
      lettere: [],
    })
    expect(await screen.findByRole('link', { name: 'Scarica la copia firmata del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/me/contracts/d1/pdf',
    )
    expect(screen.queryByRole('link', { name: /Firma/ })).toBeNull()
    expect(screen.getByText(new RegExp(formatDate('2027-10-01')))).toBeInTheDocument()
    expect(screen.getByText(/certificato della firma elettronica/)).toBeInTheDocument()
  })

  it('says a cancelled document is not to be signed, and offers no link', async () => {
    mount({ quadro: { ...QUADRO, stato: 'annullato', signing_url: null }, lettere: [] })
    expect(await screen.findByText('Annullato: non va più firmato')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('says so, in words, when nothing has reached the person yet', async () => {
    mount({ quadro: null, lettere: [] })
    expect(
      await screen.findByText('Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.'),
    ).toBeInTheDocument()
  })
})
```

`projects/hub/apps/web/src/pages/member/Area.test.tsx`:

1. After `answer`, add:

```tsx
const NO_CONTRACTS = { quadro: null, lettere: [] }

/** `/me` answers `profile`; a card's page also reads its contracts (REB-392). A fresh
 *  Response per call, since a body can be read once. */
function meFetch(profile: unknown, contracts: unknown = NO_CONTRACTS) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async (input) => answer(200, String(input) === '/api/hub/me/contracts' ? contracts : profile))
}
```

2. Replace every `vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, X))` with `meFetch(X)`; the ten occurrences are one shape, so from the repository root:

```bash
sed -i.bak -E "s/vi\.spyOn\(globalThis, 'fetch'\)\.mockResolvedValue\(answer\(200, ([A-Z_]+)\)\)/meFetch(\1)/" projects/hub/apps/web/src/pages/member/Area.test.tsx && rm projects/hub/apps/web/src/pages/member/Area.test.tsx.bak
```

3. Append:

```tsx
describe('/me, «Contratti» (REB-392: on a card, never on a company-only profile)', () => {
  it('shows the section on a card, with what there is to sign', async () => {
    meFetch(PROFILE, {
      quadro: {
        id: 'd1',
        kind: 'quadro',
        numero: null,
        stato: 'inviato',
        cliente: null,
        inizio: null,
        fine: null,
        sent_at: '2026-09-23T10:00:00Z',
        signed_at: null,
        signing_url: 'https://firma.letsrebase.com/sign/abc',
        ha_pdf_firmato: false,
        attivo: false,
        rinnovo: null,
        ultimo_giorno_disdetta: null,
      },
      lettere: [],
    })
    mount()
    expect(await screen.findByRole('heading', { name: 'Contratti' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Firma il contratto quadro' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/abc',
    )
  })

  it('has no «Contratti» for a company-only profile', async () => {
    meFetch(COMPANY_ONLY)
    mount()
    await screen.findByText('La tua richiesta più recente')
    expect(screen.queryByRole('heading', { name: 'Contratti' })).toBeNull()
  })
})
```

- [ ] **Step 11: Run them to see them fail**

Run: `pnpm --filter hub exec vitest run src/pages/member/Contratti.test.tsx src/pages/member/Area.test.tsx`
Expected: FAIL, `Failed to resolve import "./Contratti"`; the existing Area cases still pass with `meFetch`.

- [ ] **Step 12: `api.ts` and `format.ts`**

`projects/hub/apps/web/src/lib/api.ts`, after `SendReport`:

```ts
/** A contract as its freelancer reads it in «Contratti» (REB-392): the signing link only
 *  while the document waits for the signature. */
export interface MemberContract {
  id: string
  kind: 'quadro' | 'lettera'
  numero: string | null
  stato: DocumentStato
  cliente: string | null
  inizio: string | null
  fine: string | null
  sent_at: string | null
  signed_at: string | null
  signing_url: string | null
  ha_pdf_firmato: boolean
  attivo: boolean
  rinnovo: string | null
  ultimo_giorno_disdetta: string | null
}

export interface MemberContracts {
  quadro: MemberContract | null
  lettere: MemberContract[]
}
```

Inside `member`, after `guideUrl`:

```ts
  /** «Contratti» (REB-392): the caller's own, from the session. */
  contracts: () => request<MemberContracts>('/api/hub/me/contracts'),
  /** A signed copy, a plain href like `cvUrl`: the route answers an attachment. */
  contractPdfUrl: (documentId: string) => `/api/hub/me/contracts/${documentId}/pdf`,
```

`projects/hub/apps/web/src/lib/format.ts`, append:

```ts
/** REB-392: a contract's state in the freelancer's own words. */
export const MEMBER_DOCUMENT_STATE_LABELS: Record<string, string> = {
  in_attesa: 'Parte dopo la firma del contratto quadro',
  inviato: 'Da firmare',
  firmato: 'Firmato',
  annullato: 'Annullato: non va più firmato',
  disdetto: 'Disdetto',
}
```

- [ ] **Step 13: The section** (`projects/hub/apps/web/src/pages/member/Contratti.tsx`)

```tsx
import { useQuery } from '@tanstack/react-query'
import { Download, PenLine } from 'lucide-react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { member, type MemberContract } from '@/lib/api'
import { MEMBER_DOCUMENT_STATE_LABELS, formatDate } from '@/lib/format'

function Actions({ document }: { document: MemberContract }) {
  const what = document.kind === 'quadro' ? 'il contratto quadro' : `la lettera n. ${document.numero}`
  const of = document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
  if (!document.signing_url && !document.ha_pdf_firmato) return null
  return (
    <div className="flex flex-wrap gap-2">
      {document.signing_url && (
        <Button asChild size="sm">
          <a href={document.signing_url} target="_blank" rel="noreferrer" aria-label={`Firma ${what}`}>
            <PenLine className="mr-2 size-4" aria-hidden="true" />
            Firma il documento
          </a>
        </Button>
      )}
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a href={member.contractPdfUrl(document.id)} aria-label={`Scarica la copia firmata ${of}`}>
            <Download className="mr-2 size-4" aria-hidden="true" />
            Copia firmata
          </a>
        </Button>
      )}
    </div>
  )
}

function Framework({ quadro }: { quadro: MemberContract }) {
  return (
    <div className="space-y-3 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">Contratto quadro</p>
        <Badge variant="pill">{MEMBER_DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</Badge>
      </div>
      {quadro.attivo && quadro.signed_at && quadro.rinnovo && quadro.ultimo_giorno_disdetta && (
        <p className="text-muted-foreground">
          Firmato il {formatDate(quadro.signed_at)}: si rinnova da solo il {formatDate(quadro.rinnovo)}, e per la
          disdetta c’è tempo fino al {formatDate(quadro.ultimo_giorno_disdetta)}.
        </p>
      )}
      <Actions document={quadro} />
    </div>
  )
}

function Letter({ lettera }: { lettera: MemberContract }) {
  const period = lettera.inizio
    ? lettera.fine
      ? `dal ${lettera.inizio} al ${lettera.fine}`
      : `dal ${lettera.inizio}`
    : null
  return (
    <li className="space-y-2 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">Lettera di incarico n. {lettera.numero}</p>
        <Badge variant="pill">{MEMBER_DOCUMENT_STATE_LABELS[lettera.stato] ?? lettera.stato}</Badge>
      </div>
      <p className="text-muted-foreground">{[lettera.cliente, period].filter(Boolean).join(', ')}</p>
      <Actions document={lettera} />
    </li>
  )
}

/** «Contratti» in the member area (REB-392, spec § 4): the framework agreement's state and
 *  dates, then each letter with its client and dates. A document that waits for the
 *  signature has «Firma il documento», which opens the signing site; a signed one offers
 *  its copy. A cancelled one says so here, since the signing site still opens it and only
 *  fails at the click (probe § 11.10). */
export function MemberContratti() {
  const contracts = useQuery({ queryKey: ['me', 'contracts'], queryFn: () => member.contracts() })
  const data = contracts.data
  const signed = data ? [data.quadro, ...data.lettere].some((document) => document?.ha_pdf_firmato) : false
  return (
    <section aria-labelledby="me-contratti" className="space-y-3">
      <h2 id="me-contratti" className="text-lg font-semibold tracking-tight">
        Contratti
      </h2>
      {contracts.isError ? (
        <p className="text-sm text-muted-foreground">Non riesco a leggere i tuoi contratti. Riprova tra poco.</p>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">Caricamento…</p>
      ) : data.quadro === null && data.lettere.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.
        </p>
      ) : (
        <>
          {data.quadro && <Framework quadro={data.quadro} />}
          {data.lettere.length > 0 && (
            <ul className="space-y-3">
              {data.lettere.map((lettera) => (
                <Letter key={lettera.id} lettera={lettera} />
              ))}
            </ul>
          )}
          {signed && (
            <p className="text-xs text-muted-foreground">
              La copia firmata ha in fondo una pagina in inglese: è il certificato della firma elettronica.
            </p>
          )}
        </>
      )}
    </section>
  )
}
```

`projects/hub/apps/web/src/pages/member/Area.tsx`: add `import { MemberContratti } from '@/pages/member/Contratti'`, render `<MemberContratti />` inside the `{value && (<>...</>)}` fragment, right after the `<section aria-label="Quello che ci hai mandato">` element, and add to the component's docstring: «A card also gets «Contratti» (REB-392), which reads its own route and renders nothing wizard-shaped.»

- [ ] **Step 14: Run the web checks**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: green.

- [ ] **Step 15: Whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green.

- [ ] **Step 16: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/member_contracts.py \
  projects/hub/packages/core/tests/test_member_contracts.py \
  projects/hub/apps/api/src/rebase_api/routers/members.py \
  projects/hub/apps/api/tests/test_member_contracts_api.py \
  projects/hub/apps/web/src/lib/api.ts \
  projects/hub/apps/web/src/lib/format.ts \
  projects/hub/apps/web/src/pages/member/Contratti.tsx \
  projects/hub/apps/web/src/pages/member/Contratti.test.tsx \
  projects/hub/apps/web/src/pages/member/Area.tsx \
  projects/hub/apps/web/src/pages/member/Area.test.tsx
git commit -F - <<'EOF'
feat(hub): a freelancer reads and signs their contracts from the member area

I add «Contratti» to /me for a person with a card: the framework agreement's
state and dates, then each letter with its client and dates. A document that
waits for the signature opens the signing site from «Firma il documento», a
signed one offers its copy, and a cancelled one says it is not to be signed.
GET /api/hub/me/contracts reads the caller's own documents from the session;
someone else's document is a 404, never a 403, and a draft match shows
nothing until it is sent.

REB-392.
EOF
```

---

## Self-Review

**Spec coverage (phase 3, § 10 item 3).** Mail attachments: Task 1 (`Attachment`, base64 to Resend). The Documenso client with the probe's calls, `distributionMethod: NONE`, every `emailSettings` flag off: Task 1. Sending (`POST /matches/{id}/send`), one rebase mail per document, `testo_bozza` refused, the letter held `in_attesa`, regenerated at send with the send date: Task 2. The envelope item stored for the download: Task 2 (migration 0018). The webhook: secret in constant time, empty header refused, row lock, fast answer with the download and mails after the commit, `firmato` with the signer's `signedAt`, the letter released by its framework agreement, a signed letter's match `attivo`, `DOCUMENT_REJECTED` and `DOCUMENT_CANCELLED` to `annullato` with the reason: Task 3. «Aggiorna stato», «Reinvia email», «Annulla» with the probe's cancel call: Task 4. The member «Contratti» on `MeDep` with a 404 for someone else's document: Task 5. Spec § 3's «Registra disdetta» (audit kind `notice_recorded`), which phase 2 deferred and no phase 3 card names, is in Task 4. Not here by design: the compose services, the vhost, DNS, the certificate and the settings on the servers (phase 4, REB-393), and the end-to-end signature on the preview, which needs Documenso running (phase 4) and texts that no longer say `status: draft`.

**Placeholder scan.** Every step carries its code or its exact command; the one conditional (Step 11 of Task 1, if Typst still exports hidden text) names the replacement line.

**Type consistency.** `SigningService(session, *, renderer, documenso, sender, signer, contracts_mail, today, now)` is built the same way in `deps.get_signing_factory` and in `test_signing._signing`. `Outcome(envelope_id, kind, signed_at, reason)` is produced by `outcome_from_webhook` and `outcome_from_envelope` and consumed by `apply`. `SendReport(match, inviato, mail_inviata)` matches the web's `SendReport`. `ContractDocumentRead.cancel_reason` (Task 4) matches `ContractDocument.cancel_reason` in `api.ts`. `MemberContract` fields match the web's `MemberContract`.

**Review Focus.** Each of the five lines has its test in the owning task: 1 and 2 in Task 3, 3, 4 and 5 in Task 2.

## Execution

Subagent-driven, one fresh implementer per task, each task one Linear card (REB-390 for Tasks 1 and 2, REB-391 for Tasks 3 and 4, REB-392 for Task 5), a fresh reviewer between tasks and a whole-branch review at the end.
