# Phase 2: match and generate, no signature. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an admin opens a card from «Talenti», creates a match with one of the company requests in five steps, and the hub writes the letter of engagement (and the framework agreement when the freelancer has none active) as PDFs stored in Postgres, readable from a «Match e contratti» page and from two read-only MCP tools. Nothing is sent anywhere.

**Architecture:** the contract build of REB-386 moves from `tools/build_contract_pdf.py` into a `rebase_core.contracts` package (pure field logic, brand reading, pandoc/Typst renderer with a `typst query` for the signature blanks), with the texts as package data and the script kept as a thin laptop wrapper. The API image gains pandoc, Typst, fontTools and the brand files. Migration 0017 adds four tables; `FiscalService` and `MatchService` sit on a `Session` like every other service and take the renderer as a seam (`FakeRenderer` in tests). FastAPI routes under `/api/hub/` behind `AdminDep`, two MCP tools, and three web pieces in the admin SPA.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0, Alembic, Pydantic 2, FastAPI, the `mcp` SDK's `MCPServer`, pandoc 3.8.2.1, Typst 0.14.2, fontTools 4.62.0, testcontainers Postgres 17; React 19, TanStack Router and Query, `@rebase/ui`, vitest with jsdom.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-23-matches-and-contract-signing-design.md` (§ 1c to 1h, § 2, § 3, § 5, § 8 `REBASE_SIGNER_JSON`, § 9, § 10 phase 2). Phases 1, 3 and 4 are out of scope here.

## Global Constraints

- English for code, comments, docs and commit messages; Italian only for what the product says to people: UI copy, the contract texts, MCP tool descriptions (they are Italian today, `apps/mcp/src/rebase_mcp/server.py:56-64`), API error sentences.
- No em dashes anywhere you write: code, comments, docs, UI copy, commit messages. An empty value in the UI is a word («non ancora», «nessuna»), not a dash glyph.
- Conventional Commits, written in the first person (`feat(hub): ...`, body «I move ...»). Stage with explicit pathspecs (`git add <paths>`), never `git add -A` or `git add .`. No AI co-author trailer and no «Generated with» line: the commit body's last line is the card id, written `REB-N.` in this plan; the controller replaces `REB-N` with each task's Linear card.
- Every command runs from the repository root: `uv run pytest -q projects/hub/...`, `uv run mypy`, `uv run ruff check projects/hub`, `uv run ruff format --check projects/hub`, `pnpm --filter hub test`, `pnpm --filter hub lint`, `pnpm --filter hub build`.
- The letter never carries the company's `budget_giornaliero` (spec § 1h): no schema, prefill, stored `data` or page of this flow reads it.
- The framework agreement lasts 12 months and renews itself (article 9.1). It is **active** while `stato = 'firmato'` and `notice_at IS NULL`; its next renewal is the first anniversary of `signed_at` (as a date in Europe/Rome) after today, and the last day for a notice is 30 days before that. Computed, never stored. Phase 2 can only produce `generato` and `in_attesa` documents, but the computation and its tests belong here.
- A text whose front matter says `status: draft` may be generated (BOZZA watermark, `testo_bozza = true` on the row). Phase 2 sends nothing: no Documenso, no mail, no member section, no send route. «Invia per la firma» is rendered disabled with the tooltip «Arriva con la firma elettronica».
- Letter numbers are `YYYY-NNN`, the year of the generation day in Europe/Rome, taken in the same transaction that writes the letter: a failed generation takes no number.
- Pins: pandoc 3.8.2.1 and Typst 0.14.2 (the same `ARG`s as `projects/pigrocrm/Dockerfile.api:12-13`), `fonttools[woff]==4.62.0`.
- Do not modify `projects/hub/tools/build_guide_pdf.py` or `projects/hub/tools/guide.typ.template`: their hashes are locked by `packages/core/tests/test_guide_pdf.py` through `tools/guide-pdf.lock.json`.
- Migration 0017 is defensive (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `DROP ... IF EXISTS`), like 0016 (`packages/core/migrations/versions/0016_company_contact_and_request_shape.py:28-35`). Tests run on the testcontainer Postgres brought to `head` (`packages/core/tests/conftest.py:24-31`), never `create_all`.
- Every test fixture that writes to the new tables deletes them in its `DELETE FROM` list, children first: `admin_actions`, `contract_documents`, `matches`, `contract_letter_counters`, `freelancer_fiscal`, then `comments`, `freelancers`, `companies`, `users`, `signups`.
- No test reaches the network (`projects/hub/conftest.py`); no service test needs pandoc or Typst: they hand `FakeRenderer`. Only `test_contract_render.py` runs the real binaries.
- `packages/core` imports neither adapter and nothing from PigroCRM (`projects/hub/ruff.toml`). The web writes no primitive: everything visual comes from `@rebase/ui`, and page files export components only (`react-refresh/only-export-components`); helpers live in `src/lib/`.
- Admin routes live under `/api/hub/` behind `AdminDep` (`apps/api/src/rebase_api/routers/admin.py:60`), not under an `/api/hub/admin/` prefix the hub does not have.
- Audit entries go through `AdminActionService.record` after the change's own commit (`packages/core/src/rebase_core/audit.py:199-233`); kinds fit 20 characters; no tax identifier ever enters a payload.
- Web pull requests carry before and after screenshots, as the repository asks; the controller takes them.

## Review Focus

1. Two admins generating a letter at the same moment must get two consecutive numbers, never the same one and never a failure: `test_two_letters_taken_at_once_get_two_numbers` in Task 4 holds the first transaction open while a second one asks.
2. A card with no day rate (drafted from a signup, ORB-155) must prefill an empty fee, and the letter must refuse to be generated without one, naming the field: Task 5 `test_a_card_without_a_day_rate_prefills_no_fee_and_the_letter_needs_one`, Task 8 `shows an empty, required fee for a card without a day rate and names the fee the server refused`.
3. A framework agreement signed late in the evening UTC must print the Italian calendar date it was signed on (23:30 UTC on 30 September is 1 October in Rome): Task 5 `test_with_an_active_framework_only_the_letter_is_written_and_it_cites_the_signature_date`.
4. A match must stay on «Match e contratti», with its company's name, after an admin soft-deletes the company request it came from: Task 5 `test_a_match_stays_on_the_page_after_its_request_is_deleted`.
5. A client name or an activity full of Typst syntax (`#`, `$`, `*`, `_`, `<`, `@`, quotes, backslashes, `[[`, `{{...}}`) must print as text and never break or rewrite the document: Task 1 `test_a_value_full_of_typst_syntax_prints_as_text`.

---

## File Structure

```
projects/hub/packages/core/src/rebase_core/
  contracts/                     Task 1: the renderer, package data included
    __init__.py                  docstring only
    fields.py                    markers, checks, Italian number/date, ContractFailed, layers (+ signer_data in Task 2)
    brand.py                     palette and Outfit instances, same reading as build_guide_pdf.py
    render.py                    pandoc + Typst, Rendered, SignatureBlank, Renderer protocol, signature_blanks
    contract.typ.template        moved from tools/, signature blanks marked with metadata
    texts/contratto-quadro.md    moved from content/contratti/
    texts/lettera-di-incarico.md moved from content/contratti/
    texts/rebase.json            moved from content/contratti/
  contract_schemas.py            Task 4: request and read models of the flow
  fiscal.py                      Task 4: FiscalService
  framework.py                   Task 4: active framework, renewal dates, letter numbers, document_read
  matches.py                     Task 5: MatchService
  models.py                      Task 3: FreelancerFiscal, Match, ContractDocument, LetterCounter
  errors.py                      Task 4: InvalidState
  config.py, cli.py              Task 2: signer_json, `rebase contracts-check`
packages/core/migrations/versions/0017_matches_and_contracts.py   Task 3
packages/core/tests/
  test_contract_pdf.py (modify), test_contract_render.py, test_api_image.py   Tasks 1-2
  test_migrations.py (modify)                                                  Task 3
  test_contract_schemas.py, test_fiscal.py, test_framework.py                  Task 4
  fakes_contracts.py, test_matches.py                                          Task 5
apps/api/src/rebase_api/routers/matches.py (+ main.py, deps.py, downloads.py)  Task 6
apps/api/tests/test_matches_api.py                                             Task 6
apps/mcp/src/rebase_mcp/server.py (modify), apps/mcp/tests/test_match_tools.py Task 6
apps/web/src/lib/contracts.ts, pages/admin/Contratti.tsx (+ tests)            Task 7
apps/web/src/pages/admin/CreaMatch.tsx (+ tests)                               Task 8
```

Task order and dependencies: 1 → 2 (image renders what 1 built) → 3 → 4 → 5 → 6 → 7 → 8. Tasks 3 and 1-2 are independent of each other; 4 needs 1 and 3; 5 needs 4; 6 needs 5; 7 needs 6's routes (by contract, the web tests mock them); 8 needs 7.

---

### Task 1: The contract renderer moves into `rebase_core.contracts`

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/contracts/__init__.py`
- Create: `projects/hub/packages/core/src/rebase_core/contracts/fields.py`
- Create: `projects/hub/packages/core/src/rebase_core/contracts/brand.py`
- Create: `projects/hub/packages/core/src/rebase_core/contracts/render.py`
- Move (`git mv`): `projects/hub/content/contratti/contratto-quadro.md`, `lettera-di-incarico.md`, `rebase.json` → `projects/hub/packages/core/src/rebase_core/contracts/texts/`
- Move (`git mv`) and modify: `projects/hub/tools/contract.typ.template` → `projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template` (lines 10-13 comment, 34-45 `field`)
- Rewrite: `projects/hub/tools/build_contract_pdf.py` (was 339 lines, becomes a thin wrapper)
- Rewrite: `projects/hub/packages/core/tests/test_contract_pdf.py`
- Create: `projects/hub/packages/core/tests/test_contract_render.py`
- Modify: `.github/preflight.json:141-151` (`contract-pdf`), `.github/workflows/ci.yml:520-534` (`hub-py`), `projects/hub/content/README.md:9-13,55,69-73`, `projects/hub/AGENTS.md:27,30`, `pyproject.toml:128-131`

**Interfaces:**
- Consumes: nothing new. Reads `shared/brand/palette.css`, `shared/brand/fonts/outfit-variable-latin.woff2`, `projects/hub/content/contratti/incarico.esempio.json`.
- Produces (later tasks import these exact names):
  - `rebase_core.contracts.fields`: `Value = str | int | float | bool | None`; `class ContractFailed(DomainError)` (code `contract_failed`, `.message` Italian sentence, `.detail` English detail); `FIELD: re.Pattern[str]`; `TERM, DAYS, MONTH_END = "termine-pagamento", "giorni-pagamento", "fine-mese"`; `DAYS_LIMIT = 60`; `DAYS_LIMIT_MONTH_END = 30`; `check_layer(loaded: object, source: str) -> dict[str, Value]`; `read_layer(text: str, source: str) -> dict[str, Value]`; `merge_data(*layers: Mapping[str, Value]) -> dict[str, Value]`; `checked(data: dict[str, Value]) -> dict[str, Value]`; `italian(number: Decimal, places: int) -> str`; `italian_date(day: date) -> str`; `rendered(key: str, value: Value) -> str`; `fill(typst: str, data: Mapping[str, Value]) -> tuple[str, list[str]]`; `mark_proposals(typst: str, name: str, draft: bool) -> str`; `survived(markdown: str, typst: str, name: str) -> None`; `front_matter(markdown: str, name: str) -> dict[str, str]`; `is_draft(markdown: str, name: str) -> bool`.
  - `rebase_core.contracts.brand`: `REPO`, `PALETTE`, `FONT`, `TOKENS`, `WEIGHTS`, `GRID_INK_SHARE`, `palette() -> dict[str, str]`, `static_fonts(into: Path) -> None`, `fonts_dir() -> Path`.
  - `rebase_core.contracts.render`: `TEXTS: Path`, `TEMPLATE: Path`, `COMPANY_DEFAULTS: Path`, `DOCUMENTS = ("contratto-quadro", "lettera-di-incarico")`, `A4_WIDTH_PT`, `A4_HEIGHT_PT`, `@dataclass(frozen=True) Rendered(pdf: bytes, blank: list[str], version: str, draft: bool)`, `@dataclass(frozen=True) SignatureBlank(name: str, page: int, x: float, y: float, width: float, height: float)`, `class Renderer(Protocol): def render(self, document: str, data: Mapping[str, Value]) -> Rendered`, `class ContractRenderer` (the real one), `text_path(document: str) -> Path`, `text_version(document: str) -> str`, `company_defaults() -> dict[str, Value]`, `render(document: str, data: Mapping[str, Value]) -> Rendered`, `signature_blanks(document: str, data: Mapping[str, Value]) -> list[SignatureBlank]`.

- [ ] **Step 1: Move the texts and the template with history**

```bash
mkdir -p projects/hub/packages/core/src/rebase_core/contracts/texts
git mv projects/hub/content/contratti/contratto-quadro.md projects/hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro.md
git mv projects/hub/content/contratti/lettera-di-incarico.md projects/hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md
git mv projects/hub/content/contratti/rebase.json projects/hub/packages/core/src/rebase_core/contracts/texts/rebase.json
git mv projects/hub/tools/contract.typ.template projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template
```

`content/contratti/` keeps `.gitignore`, `incarico.esempio.json` and the ignored `rebase.local.json` and `dist/`: they are the laptop's, not the API's.

- [ ] **Step 2: Write the failing pure tests** (replace the whole of `projects/hub/packages/core/tests/test_contract_pdf.py`)

```python
"""The contract build's own logic, which is everything in it that is not pandoc or Typst.

Since REB-387 that logic is `rebase_core.contracts` and `tools/build_contract_pdf.py` is
the laptop's thin wrapper over it. What can go wrong without anybody noticing is small
and bad: a letter that prints a fee other than the one agreed, a field typed into the
Markdown that no data file knows about, a signed text that still carries a proposal
nobody decided, or the contracts' brand drifting away from the guide's. The real render
is `test_contract_render.py`.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import build_guide_pdf
import pytest
from build_contract_pdf import CONTRACTS, load_data

from rebase_core.contracts import brand
from rebase_core.contracts.fields import (
    FIELD,
    ContractFailed,
    Value,
    checked,
    fill,
    front_matter,
    is_draft,
    italian,
    italian_date,
    mark_proposals,
    rendered,
    survived,
)
from rebase_core.contracts.render import COMPANY_DEFAULTS, DOCUMENTS, TEXTS

EXAMPLE = CONTRACTS / "incarico.esempio.json"
# Signed on the signing site or on paper, never typed into a data file ahead of time.
SIGNATURES = {"firma-rebase", "firma-professionista"}


@pytest.mark.parametrize(
    ("fee", "complaint"),
    [
        # As text it would print whatever it says, unchecked.
        ("450", "JSON number"),
        # More precision than the page prints: 450.005 would print as 450,01.
        (450.005, "two decimals"),
        (-450, "above zero"),
        (0, "above zero"),
        # Past what Decimal holds at its default precision.
        (1e30, "not a number a contract can print"),
    ],
)
def test_a_fee_the_page_could_not_print_as_given_stops_the_build(
    fee: Value, complaint: str
) -> None:
    with pytest.raises(ContractFailed, match=complaint):
        checked({"compenso": fee})


def test_the_payment_term_is_written_from_the_days_and_the_month_end() -> None:
    assert checked({"giorni-pagamento": 30, "fine-mese": True})["termine-pagamento"] == (
        "30 giorni data fattura fine mese"
    )
    assert checked({"giorni-pagamento": 60})["termine-pagamento"] == "60 giorni data fattura"


@pytest.mark.parametrize(
    ("data", "complaint"),
    [
        # Counted from the end of the month, 45 days can reach 75 from the invoice.
        ({"giorni-pagamento": 45, "fine-mese": True}, "from 1 to 30"),
        ({"giorni-pagamento": 90}, "from 1 to 60"),
        ({"giorni-pagamento": "30"}, "whole number"),
        ({"giorni-pagamento": 30, "fine-mese": "sì"}, "true or false"),
        # Typed by hand it would skip the check above.
        ({"termine-pagamento": "90 giorni"}, "written from"),
    ],
)
def test_a_payment_term_past_the_law_stops_the_build(
    data: dict[str, Value], complaint: str
) -> None:
    with pytest.raises(ContractFailed, match=complaint):
        checked(data)


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", "1e400"])
def test_a_data_file_with_no_printable_number_is_refused(tmp_path: Path, raw: str) -> None:
    data = tmp_path / "job.json"
    data.write_text('{"compenso": ' + raw + "}", encoding="utf-8")
    with pytest.raises(ContractFailed, match="not a number a contract can print"):
        load_data(data)


def test_the_fee_is_written_the_italian_way_and_identifiers_as_given() -> None:
    assert checked({"compenso": 450})["compenso"] == 450
    assert rendered("compenso", 450) == "450,00 €"
    assert italian(Decimal("1234.5"), 2) == "1.234,50"
    assert rendered("compenso", 12000) == "12.000,00 €"
    assert rendered("numero", 2026001) == "2026001"
    assert rendered("giorni-preavviso", 30) == "30"
    assert rendered("risultati", "l'Incarico") == "l\u2019Incarico"


def test_a_date_is_written_the_way_a_contract_writes_it() -> None:
    assert italian_date(date(2026, 10, 1)) == "1° ottobre 2026"
    assert italian_date(date(2026, 11, 2)) == "2 novembre 2026"
    assert italian_date(date(2027, 1, 31)) == "31 gennaio 2027"


def test_a_field_is_the_value_when_known_and_a_labelled_blank_otherwise() -> None:
    typst, blank = fill(
        'Tra {{professionista-nome}} e {{cliente-sede}} "x"',
        {
            "professionista-nome": 'Anna "Nina" Rossi',
            "cliente-sede": None,
        },
    )
    assert '#value("Anna \\"Nina\\" Rossi");' in typst
    assert '#field("cliente sede");' in typst
    assert blank == ["cliente-sede"]


def test_a_proposal_is_highlighted_in_a_draft_and_refused_in_a_final_text() -> None:
    escaped = "il \\[\\[15%\\]\\] degli importi"
    marked = mark_proposals(escaped, "doc", draft=True)
    assert marked == "il #proposal[15%]; degli importi"
    with pytest.raises(ContractFailed, match="no longer a draft"):
        mark_proposals(escaped, "doc", draft=False)
    with pytest.raises(ContractFailed, match="against"):
        mark_proposals("\\[\\[15%", "doc", draft=True)


def test_a_marker_pandoc_stopped_passing_through_stops_the_build() -> None:
    markdown = "il {{cliente-sede}} e il [[15%]]"
    survived(markdown, "il {{cliente-sede}} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(ContractFailed, match="1 fields"):
        survived(markdown, "il \\{\\{cliente-sede\\}\\} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(ContractFailed, match="1 proposals"):
        survived(markdown, "il {{cliente-sede}} e il [[15%]]", "doc")


def test_every_contract_has_the_front_matter_the_page_reads() -> None:
    sources = sorted(TEXTS.glob("*.md"))
    assert {path.stem for path in sources} == set(DOCUMENTS)
    for path in sources:
        markdown = path.read_text(encoding="utf-8")
        matter = front_matter(markdown, path.name)
        is_draft(markdown, path.name)
        for key in ("title", "version", "date", "status"):
            assert matter.get(key), f"{path.name} lacks {key}"
    letter = (TEXTS / "lettera-di-incarico.md").read_text(encoding="utf-8")
    assert "{{numero}}" in front_matter(letter, "lettera-di-incarico.md")["subtitle"]


def test_the_example_names_every_field_the_letter_asks_for() -> None:
    """A field added to the letter and not to the example is a field nobody knows to fill."""
    letter = (TEXTS / "lettera-di-incarico.md").read_text(encoding="utf-8")
    asked = set(FIELD.findall(letter))
    company = json.loads(COMPANY_DEFAULTS.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # `termine-pagamento` is in no data file on purpose: the build writes it.
    known = set(checked({**company, **example}))
    assert asked - known - SIGNATURES == set()


def test_the_example_is_fiction() -> None:
    """The repository is public, so the example may name nobody real."""
    example = json.loads(Path(EXAMPLE).read_text(encoding="utf-8"))
    assert example["professionista-nome"] == "Nome Cognome"
    assert set(example["professionista-piva"]) == {"0"}
    assert set(example["cliente-piva"]) == {"0"}


def test_the_contracts_read_the_brand_exactly_as_the_guide_does() -> None:
    """`brand` is a copy of the guide script's reading, because that script's bytes are
    locked and a package cannot import a script: this keeps the two documents of one
    brand from drifting apart one hand-edited hex at a time."""
    assert brand.TOKENS == build_guide_pdf.TOKENS
    assert brand.WEIGHTS == build_guide_pdf.WEIGHTS
    assert brand.GRID_INK_SHARE == build_guide_pdf.GRID_INK_SHARE
    assert brand.PALETTE == build_guide_pdf.PALETTE
    assert brand.FONT == build_guide_pdf.FONT
    assert brand.palette() == build_guide_pdf.palette()
```

- [ ] **Step 3: Write the failing real-render tests** (`projects/hub/packages/core/tests/test_contract_render.py`)

```python
"""The real render, the way the API runs it: pandoc and Typst over both contracts.

Needs both binaries and fontTools. Every machine that runs the hub's suite has them: the
Nix shell and the `contract-pdf`/`guide-pdf` preflight checks need the same pair, and the
hub's CI job installs it (`renderer: true` in ci.yml). This is the one file that proves
the markers, the template and the signature query against the real tools; every service
test hands `FakeRenderer` instead.
"""

from pathlib import Path

from rebase_core.contracts.fields import Value, read_layer
from rebase_core.contracts.render import (
    A4_HEIGHT_PT,
    A4_WIDTH_PT,
    DOCUMENTS,
    company_defaults,
    render,
    signature_blanks,
    text_version,
)

EXAMPLE = Path(__file__).resolve().parents[3] / "content" / "contratti" / "incarico.esempio.json"
# 55 mm, the room a pen needs, in points.
SIGNATURE_WIDTH_PT = 155.0
LETTER_BLANKS = {"data-firma", "firma-rebase", "firma-professionista", "rebase-rappresentante"}


def _example() -> dict[str, Value]:
    """rebase's defaults and the public example, with the date of signing left blank as
    the hub leaves it for the signing site."""
    data = {**company_defaults(), **read_layer(EXAMPLE.read_text(encoding="utf-8"), EXAMPLE.name)}
    data["data-firma"] = None
    return data


def test_both_contracts_typeset_into_a_pdf_that_carries_its_version() -> None:
    for document in DOCUMENTS:
        rendered = render(document, _example())
        assert rendered.pdf.startswith(b"%PDF-"), document
        assert rendered.version == text_version(document), document
        assert rendered.draft is True, document


def test_a_filled_letter_leaves_blank_only_what_nobody_typed_ahead() -> None:
    assert set(render("lettera-di-incarico", _example()).blank) == LETTER_BLANKS


def test_the_framework_marks_two_signatures_and_one_date_for_the_freelancer() -> None:
    blanks = signature_blanks("contratto-quadro", _example())
    names = [blank.name for blank in blanks]
    # The contract's own signature and the specific approval of the onerous clauses.
    assert names.count("firma professionista") == 2
    assert names.count("data firma") == 1
    for blank in blanks:
        assert blank.page >= 1
        assert 0 <= blank.x < A4_WIDTH_PT and 0 <= blank.y < A4_HEIGHT_PT
        assert blank.width > 0 and blank.height > 0
    signatures = [blank for blank in blanks if blank.name == "firma professionista"]
    assert all(blank.width >= SIGNATURE_WIDTH_PT for blank in signatures)
    first, approval = signatures
    assert (approval.page, approval.y) > (first.page, first.y)


def test_a_letter_marks_one_signature_and_one_date() -> None:
    names = [blank.name for blank in signature_blanks("lettera-di-incarico", _example())]
    assert names.count("firma professionista") == 1
    assert names.count("data firma") == 1


def test_a_blank_the_data_fill_is_not_marked() -> None:
    data = {
        **_example(),
        "firma-rebase": "Documento emesso da rebase il 1° ottobre 2026",
        "rebase-rappresentante": "Nome Cognome",
    }
    names = [blank.name for blank in signature_blanks("lettera-di-incarico", data)]
    assert "firma rebase" not in names
    assert names.count("firma professionista") == 1


def test_a_value_full_of_typst_syntax_prints_as_text() -> None:
    """Review Focus 5: a value is a Typst string, so markup inside it is inert, and it is
    inserted after the markers are counted, so a `[[` or a `{{...}}` in it is neither a
    proposal nor a field."""
    data = {
        **_example(),
        "cliente-ragione-sociale": 'Rossi & "Figli" #1 $x$ *uno* _due_ <tre> @quattro \\ S.r.l.',
        "attivita": "Correzioni [[in corso]] e {{non-un-campo}} al 10% // senza /* commenti */",
    }
    rendered = render("lettera-di-incarico", data)
    assert rendered.pdf.startswith(b"%PDF-")
    assert set(rendered.blank) == LETTER_BLANKS
```

- [ ] **Step 4: Run both files to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_contract_pdf.py projects/hub/packages/core/tests/test_contract_render.py`
Expected: collection errors, `ModuleNotFoundError: No module named 'rebase_core.contracts'`.

- [ ] **Step 5: Write `contracts/__init__.py`**

```python
"""The contracts a freelancer signs, typeset by the hub itself (REB-387).

`fields` is everything about a contract that is not a binary: the `{{key}}` fields, the
`[[...]]` proposals, the checks on the fee and the payment term, an Italian number and
an Italian date. `brand` reads the palette and instances the typeface. `render` runs
pandoc and Typst over the Markdown in `texts/` with `contract.typ.template`, and asks
Typst where the blanks the signing site fills have landed. `tools/build_contract_pdf.py`
is the laptop's thin wrapper over the same three.
"""
```

- [ ] **Step 6: Write `contracts/fields.py`** (the logic of `tools/build_contract_pdf.py:57-223`, `Failed` renamed `ContractFailed`, plus layers, the front matter and dates)

```python
"""A contract's fields and markers, and the two numbers a letter must not get wrong.

Nothing here runs a binary, so all of it is tested in the hub's own pytest gate. Two
markers are the reason a contract is not a plain pandoc call, and both are replaced in
the Typst pandoc writes, after pandoc has escaped everything else:

- `{{key}}` is a field: a value from the data when it has one, otherwise a blank line
  labelled with the key, so the same file is both the template and the printable form.
- `[[text]]` is a proposal still to be decided, highlighted in the draft. A document
  whose front matter no longer says `status: draft` may not carry one.

The fee and the payment term are checked here: a fee that is not a JSON number, is not
above zero or has more decimals than the page prints is refused, and the payment term
is written from `giorni-pagamento` and `fine-mese`, refusing one that could fall past
the 60 days of law 81/2017 (article 7.1 of the framework agreement). Moved out of
`tools/build_contract_pdf.py` (REB-386) when the hub started typesetting at request time.
"""

import json
import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation

from rebase_core.errors import DomainError

# A field key is lowercase words joined by single hyphens. Pandoc's Typst writer escapes
# neither braces nor hyphens, so the token reaches the intermediate file as written.
FIELD = re.compile(r"\{\{([a-z0-9]+(?:-[a-z0-9]+)*)\}\}")
# `[[` and `]]` do not survive pandoc as written: the Typst writer escapes every bracket.
PROPOSAL_OPEN = r"\[\["
PROPOSAL_CLOSE = r"\]\]"

FEE = "compenso"
CENT = Decimal("0.01")
TERM, DAYS, MONTH_END = "termine-pagamento", "giorni-pagamento", "fine-mese"
# Article 3 of law 81/2017: no term past 60 days from the invoice. Counted from the end
# of the month, a term can add up to 30 days to the invoice's date, so it may be 30 at most.
DAYS_LIMIT, DAYS_LIMIT_MONTH_END = 60, 30

MONTHS = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)

Value = str | int | float | bool | None


class ContractFailed(DomainError):
    """A contract that could not be typeset: a value the page cannot print, a marker
    pandoc stopped passing through, a binary missing. The detail stays in English, as
    the build script always wrote it; the sentence around it is what an admin reads."""

    code = "contract_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(f"La generazione del contratto non è riuscita: {detail}", detail=detail)
        self.detail = detail


def not_a_number(constant: str) -> float:
    """Python's JSON reader accepts `NaN` and `Infinity`, which JSON itself does not."""
    raise ContractFailed(f"{constant} is not a number a contract can print")


def check_layer(loaded: object, source: str) -> dict[str, Value]:
    """One layer of data, checked: one object of `field: value`, every key a field name,
    every value text, a finite number, a boolean or `null` (not known yet)."""
    if not isinstance(loaded, dict):
        raise ContractFailed(f"{source} must hold one JSON object of field: value")
    layer: dict[str, Value] = {}
    for key, value in loaded.items():
        if not isinstance(key, str) or not FIELD.fullmatch("{{" + key + "}}"):
            raise ContractFailed(f"{source}: {key!r} is not a field name (lowercase-with-hyphens)")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ContractFailed(f"{source}: {key} must be text, a number or null")
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractFailed(f"{source}: {key} is {value}, not a number a contract can print")
        layer[key] = value
    return layer


def read_layer(text: str, source: str) -> dict[str, Value]:
    """A layer written as JSON: a file on a laptop, the package's `rebase.json`, or the
    `REBASE_SIGNER_JSON` setting."""
    try:
        loaded = json.loads(text, parse_constant=not_a_number)
    except json.JSONDecodeError as exc:
        raise ContractFailed(f"cannot read {source}: {exc}") from exc
    return check_layer(loaded, source)


def merge_data(*layers: Mapping[str, Value]) -> dict[str, Value]:
    """Later layers win, and a later layer's `null` blanks an earlier value."""
    merged: dict[str, Value] = {}
    for layer in layers:
        merged.update(layer)
    return merged


def amount(data: Mapping[str, Value], key: str) -> Decimal | None:
    """A number the page prints to the cent, or None when the field is not filled in."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractFailed(f"{key} must be a JSON number, not {value!r}")
    exact = Decimal(str(value))
    try:
        cents = exact.quantize(CENT)
    except InvalidOperation as exc:
        raise ContractFailed(f"{key} is {value}, not a number a contract can print") from exc
    if exact != cents:
        raise ContractFailed(f"{key} is {exact}: at most two decimals, which is what the page prints")
    return exact


def checked(data: dict[str, Value]) -> dict[str, Value]:
    """The data, with the fee checked and the payment term written from its two parts."""
    fee = amount(data, FEE)
    if fee is not None and fee <= 0:
        raise ContractFailed(f"{FEE} is {fee}: a fee above zero")
    if data.get(TERM) is not None:
        raise ContractFailed(f"{TERM} is written from {DAYS} and {MONTH_END}; give those two instead")
    days, month_end = data.get(DAYS), data.get(MONTH_END, False)
    if days is None:
        return data
    if isinstance(days, bool) or not isinstance(days, int):
        raise ContractFailed(f"{DAYS} must be a whole number of days, not {days!r}")
    if not isinstance(month_end, bool):
        raise ContractFailed(f"{MONTH_END} must be true or false, not {month_end!r}")
    limit = DAYS_LIMIT_MONTH_END if month_end else DAYS_LIMIT
    if not 0 < days <= limit:
        raise ContractFailed(
            f"{DAYS} is {days}: from 1 to {limit}"
            + (" when counted from the end of the month," if month_end else ",")
            + " or the letter breaks the 60 days of law 81/2017"
        )
    term = f"{days} giorni data fattura" + (" fine mese" if month_end else "")
    return {**data, TERM: term}


def italian(number: Decimal, places: int) -> str:
    """`1234.5` as `1.234,50`: a dot between thousands, a comma before the decimals."""
    text = f"{number:,.{places}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def italian_date(day: date) -> str:
    """`2026-10-01` as `1° ottobre 2026`: the ordinal on the first of the month, the way
    a contract writes it (and `incarico.esempio.json` already does), the plain number on
    every other day."""
    number = "1°" if day.day == 1 else str(day.day)
    return f"{number} {MONTHS[day.month - 1]} {day.year}"


def rendered(key: str, value: Value) -> str:
    """What the page prints for a value. Only the fee is reformatted: a letter number or
    a VAT number is an identifier, printed as it was given."""
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, (int, float)):
        if key == FEE:
            return f"{italian(Decimal(str(value)), 2)} €"
        return str(value)
    # Pandoc's `smart` curls the apostrophes of the Markdown around this value; a value
    # typed with a straight one would sit beside them looking like a typo.
    return str(value).replace("'", "\u2019")


def typst_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\r", "").replace("\n", "\\n").replace("\t", " ") + '"'


def fill(typst: str, data: Mapping[str, Value]) -> tuple[str, list[str]]:
    """Replace every field token; return the Typst and the fields left blank.

    Each call ends in a semicolon, which closes the embedded expression: without it a
    field followed by `(` or by `.word` would be read as a call or a field access.
    """
    blank: list[str] = []

    def one(match: re.Match[str]) -> str:
        key = match.group(1)
        value = data.get(key)
        if value is None or value == "":
            if key not in blank:
                blank.append(key)
            return "#field(" + typst_string(key.replace("-", " ")) + ");"
        return "#value(" + typst_string(rendered(key, value)) + ");"

    return FIELD.sub(one, typst), blank


def mark_proposals(typst: str, name: str, draft: bool) -> str:
    opened, closed = typst.count(PROPOSAL_OPEN), typst.count(PROPOSAL_CLOSE)
    if opened != closed:
        raise ContractFailed(f"{name}: {opened} `[[` against {closed} `]]`")
    if opened and not draft:
        raise ContractFailed(f"{name} is no longer a draft and still carries {opened} proposals")
    return typst.replace(PROPOSAL_OPEN, "#proposal[").replace(PROPOSAL_CLOSE, "];")


def survived(markdown: str, typst: str, name: str) -> None:
    """Both markers rely on how pandoc's Typst writer escapes, which a pandoc release could
    change. A field it escaped would print as `{{key}}` and an unescaped proposal as plain
    text, both without an error, so count them on either side of pandoc instead."""
    for marker, before, after in (
        ("fields", len(FIELD.findall(markdown)), len(FIELD.findall(typst))),
        ("proposals", markdown.count("[["), typst.count(PROPOSAL_OPEN)),
    ):
        if after < before:
            raise ContractFailed(
                f"{name}: {before} {marker} in the Markdown, {after} in pandoc's Typst."
                " pandoc escapes them differently now; adjust the markers in rebase_core.contracts."
            )


def front_matter(markdown: str, name: str) -> dict[str, str]:
    """The front matter's `key: value` lines (title, subtitle, version, date, status),
    quotes stripped. The version is what a document row records (`text_version`)."""
    front = re.match(r"---\n(.*?)\n---\n", markdown, re.S)
    if front is None:
        raise ContractFailed(f"{name} has no front matter (title, version, date, status)")
    found: dict[str, str] = {}
    for line in front.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() and not key.startswith((" ", "\t")):
            found[key.strip()] = value.strip().strip("\"'")
    return found


def is_draft(markdown: str, name: str) -> bool:
    status = front_matter(markdown, name).get("status")
    if status is None:
        raise ContractFailed(f"{name}: the front matter says no `status`")
    return status == "draft"
```

- [ ] **Step 7: Write `contracts/brand.py`** (the reading of `tools/build_guide_pdf.py:47-177`, which stays untouched)

```python
"""The brand a contract is typeset in: five values of the palette and Outfit at the two
weights the site uses.

The same reading `tools/build_guide_pdf.py` does, written again here rather than
imported: a package cannot import a script, and that script's own bytes are part of the
guide's lock (`test_guide_pdf.py`), so it is not the one to move. `test_contract_pdf.py`
compares the two readings, so two documents of one brand cannot drift apart. The files
are read at the repository's own paths, which the API image mirrors (`Dockerfile.api`
copies both), so the woff2 in `shared/brand/fonts` stays the single source of the
typeface.
"""

import re
import tempfile
import threading
from pathlib import Path

from rebase_core.contracts.fields import ContractFailed

# `.../projects/hub/packages/core/src/rebase_core/contracts/brand.py`: seven levels up is
# the root of the checkout, or `/app` in the image, which mirrors the repository.
REPO = Path(__file__).resolve().parents[7]
PALETTE = REPO / "shared" / "brand" / "palette.css"
FONT = REPO / "shared" / "brand" / "fonts" / "outfit-variable-latin.woff2"

# Weight 300 is `body`'s in landing.css, 500 is what `h1`, `h2`, `h3` and `.kicker` share.
WEIGHTS = {300: "Light", 500: "Medium"}
# The five values the template needs, by the token that holds each one.
TOKENS = {
    "ink": "--color-prussian-blue",
    "inkquiet": "--color-charcoal-blue",
    "paper": "--color-paper",
    "cta": "--color-watermelon-strong",
    "gold": "--color-royal-gold",
}
# `--system-grid` is the ink at 7% over the ground (system.css), mixed here once.
GRID_INK_SHARE = 0.07


def palette() -> dict[str, str]:
    """The template's colours, by name, as `#rrggbb`: the five tokens, and the grid line
    mixed from two of them, exactly as `build_guide_pdf.palette` returns them."""
    try:
        css = PALETTE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractFailed(f"cannot read {PALETTE}: {exc}") from exc
    found: dict[str, str] = {}
    for name, token in TOKENS.items():
        match = re.search(rf"^\s*{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}});", css, re.M)
        if match is None:
            raise ContractFailed(f"{token} is gone from {PALETTE}")
        found[name] = match.group(1)
    found["gridline"] = mix(found["ink"], found["paper"], GRID_INK_SHARE)
    return found


def mix(top: str, bottom: str, share: float) -> str:
    """`top` at `share` over an opaque `bottom`, as a hex string."""

    def channels(value: str) -> tuple[int, int, int]:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))

    blended = (
        round(a * share + b * (1 - share))
        for a, b in zip(channels(top), channels(bottom), strict=True)
    )
    return "#" + "".join(f"{c:02x}" for c in blended)


def static_fonts(into: Path) -> None:
    """Outfit's variable woff2, as one static TTF per weight the site uses: Typst reads
    neither woff2 nor a variable axis."""
    try:
        # fontTools ships no py.typed marker and has no stubs package, so these two
        # names are Any under `strict`.
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]
        from fontTools.varLib.instancer import (  # type: ignore[import-untyped]
            instantiateVariableFont,
        )
    except ImportError as exc:  # pragma: no cover - a dependency of rebase_core
        raise ContractFailed("fontTools is missing: run `uv sync`") from exc
    if not FONT.is_file():
        raise ContractFailed(f"{FONT} is missing: the API image copies it from shared/brand/fonts")
    into.mkdir(parents=True, exist_ok=True)
    for weight, name in WEIGHTS.items():
        # recalcTimestamp=False: the same instance bytes on every run, as the guide keeps them.
        font = TTFont(FONT, recalcTimestamp=False)
        instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)
        # `updateFontNames` names an instance after the axis' default (Thin 100).
        names = ((1, "Outfit"), (2, name), (4, f"Outfit {name}"), (6, f"Outfit-{name}"))
        for name_id, value in names:
            font["name"].setName(value, name_id, 3, 1, 0x409)
            font["name"].setName(value, name_id, 1, 0, 0)
        font.flavor = None
        font.save(into / f"Outfit-{name}.ttf")


_fonts: Path | None = None
_fonts_lock = threading.Lock()


def fonts_dir() -> Path:
    """The static instances, built once per process into a directory of their own and
    reused by every render after the first: instancing the variable font is the slowest
    step of a render and its output cannot change while the process lives. Built again
    if something removed the directory, as a temp cleaner on a long-lived host would."""
    global _fonts
    with _fonts_lock:
        present = _fonts is not None and all(
            (_fonts / f"Outfit-{name}.ttf").is_file() for name in WEIGHTS.values()
        )
        if not present:
            directory = Path(tempfile.mkdtemp(prefix="rebase-contract-fonts-"))
            static_fonts(directory)
            _fonts = directory
        assert _fonts is not None
        return _fonts
```

- [ ] **Step 8: Write `contracts/render.py`**

```python
"""pandoc and Typst over a contract's Markdown: the PDF, and where its signing blanks are.

The toolchain is PigroCRM's and the guide's: pandoc for Markdown to Typst, Typst to
compile, `--creation-timestamp 0` so the same data give the same bytes. The texts, the
template and rebase's own defaults are this package's data, so the API image needs
nothing from `content/` or `tools/`. Every call works in a directory of its own, removed
when it returns; the static fonts are the one thing shared (`brand.fonts_dir`).

`signature_blanks` compiles the same source and asks `typst query` for every
`<signature-blank>` the template left behind: the page and the box, in points from the
page's top-left corner, of each blank the signing site fills (the freelancer's
signatures and the date of signing). Phase 3 turns them into the signing site's own
percentages; phase 1's note says how the reported point relates to the drawn rule.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rebase_core.contracts.brand import fonts_dir, palette
from rebase_core.contracts.fields import (
    ContractFailed,
    Value,
    checked,
    fill,
    front_matter,
    is_draft,
    mark_proposals,
    read_layer,
    survived,
)

PACKAGE = Path(__file__).resolve().parent
TEXTS = PACKAGE / "texts"
TEMPLATE = PACKAGE / "contract.typ.template"
COMPANY_DEFAULTS = TEXTS / "rebase.json"
DOCUMENTS = ("contratto-quadro", "lettera-di-incarico")
SIGNATURE_LABEL = "signature-blank"
TOOL_TIMEOUT_SECONDS = 60
# The page the template sets (`paper: "a4"`), in points.
A4_WIDTH_PT = 595.2756
A4_HEIGHT_PT = 841.8898


@dataclass(frozen=True)
class Rendered:
    """A typeset contract: the bytes, the fields it printed as blank lines, the text's
    `version` and whether the text is still a draft (the BOZZA watermark is on)."""

    pdf: bytes
    blank: list[str]
    version: str
    draft: bool


@dataclass(frozen=True)
class SignatureBlank:
    """One blank the signing site fills, as Typst placed it: `name` is the field's label
    (`firma professionista`, `data firma`), `page` counts from 1, the rest are points."""

    name: str
    page: int
    x: float
    y: float
    width: float
    height: float


class Renderer(Protocol):
    """The seam the services take, so a test hands `FakeRenderer` and needs no binary."""

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered: ...


class ContractRenderer:
    """The production renderer: pandoc and Typst on this machine."""

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        return render(document, data)


def text_path(document: str) -> Path:
    if document not in DOCUMENTS:
        raise ContractFailed(f"no such document: {document} (have: {', '.join(DOCUMENTS)})")
    return TEXTS / f"{document}.md"


def text_version(document: str) -> str:
    """The `version` of the text as it is in the package today."""
    source = text_path(document)
    version = front_matter(source.read_text(encoding="utf-8"), source.name).get("version")
    if not version:
        raise ContractFailed(f"{source.name}: the front matter says no `version`")
    return version


def company_defaults() -> dict[str, Value]:
    """rebase's company data and the defaults every letter starts from (`rebase.json`)."""
    return read_layer(COMPANY_DEFAULTS.read_text(encoding="utf-8"), COMPANY_DEFAULTS.name)


def _run(args: list[str], what: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=TOOL_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise ContractFailed(f"{args[0]} is not on PATH: contracts need pandoc and typst") from exc
    except subprocess.TimeoutExpired as exc:
        raise ContractFailed(f"{what} took longer than {TOOL_TIMEOUT_SECONDS} seconds") from exc


def _typst_world(workdir: Path) -> list[str]:
    """What both `typst compile` and `typst query` need to see the same document."""
    return ["--root", str(workdir), "--font-path", str(fonts_dir()), "--ignore-system-fonts"]


def _typst_source(
    document: str, data: Mapping[str, Value], workdir: Path
) -> tuple[Path, list[str], str, bool]:
    """pandoc's Typst with every field filled and every proposal marked: the path, the
    fields left blank, the text's version and whether it is a draft."""
    source = text_path(document)
    markdown = source.read_text(encoding="utf-8")
    version = front_matter(markdown, source.name).get("version")
    if not version:
        raise ContractFailed(f"{source.name}: the front matter says no `version`")
    draft = is_draft(markdown, source.name)
    intermediate = workdir / f"{document}.typ"
    pandoc = _run(
        [
            "pandoc",
            "--from=markdown+smart",
            "--to=typst",
            "--standalone",
            f"--template={TEMPLATE}",
            # The articles are `##`, under a title that comes from the front matter.
            "--shift-heading-level-by=-1",
            "--wrap=preserve",
            *(f"--variable={name}:{value.lstrip('#')}" for name, value in palette().items()),
            f"--variable=draft:{'true' if draft else 'false'}",
            "--output",
            str(intermediate),
            str(source),
        ],
        f"pandoc on {source.name}",
    )
    if pandoc.returncode != 0:
        raise ContractFailed(f"pandoc failed on {source.name}:\n{pandoc.stderr.strip()}")
    written = intermediate.read_text(encoding="utf-8")
    survived(markdown, written, source.name)
    typst, blank = fill(written, checked(dict(data)))
    intermediate.write_text(mark_proposals(typst, source.name, draft), encoding="utf-8")
    return intermediate, blank, version, draft


def render(document: str, data: Mapping[str, Value]) -> Rendered:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, blank, version, draft = _typst_source(document, data, workdir)
        output = workdir / f"{document}.pdf"
        compiled = _run(
            [
                "typst",
                "compile",
                *_typst_world(workdir),
                "--creation-timestamp",
                "0",
                str(intermediate),
                str(output),
            ],
            f"typst on {document}",
        )
        if compiled.returncode != 0:
            raise ContractFailed(f"typst failed on {document}:\n{compiled.stderr.strip()}")
        warnings = [line for line in compiled.stderr.splitlines() if "warning" in line]
        if warnings:
            raise ContractFailed(f"typst warned on {document}:\n" + "\n".join(warnings))
        return Rendered(pdf=output.read_bytes(), blank=blank, version=version, draft=draft)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def signature_blanks(document: str, data: Mapping[str, Value]) -> list[SignatureBlank]:
    workdir = Path(tempfile.mkdtemp(prefix="rebase-contract-"))
    try:
        intermediate, _blank, _version, _draft = _typst_source(document, data, workdir)
        queried = _run(
            [
                "typst",
                "query",
                *_typst_world(workdir),
                "--field",
                "value",
                "--format",
                "json",
                str(intermediate),
                f"<{SIGNATURE_LABEL}>",
            ],
            f"typst query on {document}",
        )
        if queried.returncode != 0:
            raise ContractFailed(f"typst query failed on {document}:\n{queried.stderr.strip()}")
        try:
            values = json.loads(queried.stdout)
            return [
                SignatureBlank(
                    name=str(value["name"]),
                    page=int(value["page"]),
                    x=float(value["x"]),
                    y=float(value["y"]),
                    width=float(value["width"]),
                    height=float(value["height"]),
                )
                for value in values
            ]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ContractFailed(f"typst query on {document} answered {queried.stdout!r}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 9: Mark the signing blanks in the template**

In `projects/hub/packages/core/src/rebase_core/contracts/contract.typ.template`, replace the comment lines 10-13 with:

```
// `rebase_core.contracts.fields` rewrites two markers in the body before Typst sees it: a
// field becomes `field("label")` when it is blank and `value("text")` when the data fill
// it, and a proposal still to be decided becomes `proposal[...]`. `render.py` passes
// `draft`, read from the front matter's `status`, as a Typst boolean.
//
// A blank somebody fills on the signing site (a signature, the date of signing) also
// leaves a `metadata` labelled `<signature-blank>` with its page and box, in points from
// the page's top-left corner, which `render.signature_blanks` reads back with
// `typst query`.
```

and replace the `field` definition (lines 34-45) with:

```
// A blank to fill in: a rule with the field's name under the reader's pen. A signature
// gets the room a pen needs above its rule, and a blank the signing site fills leaves
// its position behind for `typst query`.
#let signing(name) = name.starts-with("firma") or name == "data firma"
#let field(name) = context {
  let signature = name.starts-with("firma")
  let label = text(size: 0.75em, fill: inkquiet, name)
  let blank = box(
    width: calc.max(measure(label).width + 6pt, if signature { 55mm } else { 30mm }),
    stroke: (bottom: 0.6pt + inkquiet),
    inset: (x: 2pt, top: if signature { 16pt } else { 0pt }, bottom: 1.5pt),
    label,
  )
  if signing(name) {
    let pos = here().position()
    let size = measure(blank)
    [#metadata((
      name: name,
      page: pos.page,
      x: pos.x.pt(),
      y: pos.y.pt(),
      width: size.width.pt(),
      height: size.height.pt(),
    ))<signature-blank>]
  }
  blank
}
```

No `$` appears in the new lines, so pandoc's template syntax is untouched. If Typst warns that layout did not converge (the render treats any warning as a failure), emit the metadata from a second `context` placed after `blank` instead of before it; the query and the tests do not change.

- [ ] **Step 10: Rewrite `projects/hub/tools/build_contract_pdf.py` as the thin wrapper**

```python
"""Typeset the freelancer contracts into PDFs, on a laptop.

    uv run python projects/hub/tools/build_contract_pdf.py
    uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --data job.json

Since REB-387 the texts, the Typst template and rebase's defaults are package data of
`rebase_core.contracts`, which the hub's API renders at request time; this script is the
thin wrapper that keeps a preview on a laptop. With no argument it builds both documents
as blank forms; naming one or more (by stem) builds only those. `--data` fills the
fields from a JSON object, read over the package's `rebase.json` and over
`content/contratti/rebase.local.json` when that file exists: git ignores it, and it holds
the real data of whoever signs for rebase today, which a public repository does not
print. `--public` leaves it out and writes to `dist/public/`. The PDFs land in
`content/contratti/dist/`, which git ignores too.
"""

import argparse
import sys
from pathlib import Path

from rebase_core.contracts.fields import ContractFailed, Value, merge_data, read_layer
from rebase_core.contracts.render import DOCUMENTS, company_defaults, render

HUB = Path(__file__).resolve().parent.parent
REPO = HUB.parent.parent
CONTRACTS = HUB / "content" / "contratti"
COMPANY_LOCAL = CONTRACTS / "rebase.local.json"
OUTPUT = CONTRACTS / "dist"


def load_data(path: Path | None, local: bool = True) -> dict[str, Value]:
    """rebase's defaults, then the local signer file, then the caller's.

    `null` means not known yet, and a later layer's `null` blanks an earlier value."""
    layers = [company_defaults()]
    for source in (COMPANY_LOCAL if local and COMPANY_LOCAL.is_file() else None, path):
        if source is None:
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise ContractFailed(f"cannot read {source}: {exc}") from exc
        layers.append(read_layer(text, str(source)))
    return merge_data(*layers)


def documents(names: list[str]) -> list[str]:
    if not names:
        return list(DOCUMENTS)
    unknown = [name for name in names if name not in DOCUMENTS]
    if unknown:
        raise ContractFailed(
            f"no such document: {', '.join(unknown)} (have: {', '.join(DOCUMENTS)})"
        )
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("documents", nargs="*", help="document stems; all if none")
    parser.add_argument("--data", type=Path, help="JSON object of field: value to fill in")
    parser.add_argument(
        "--public",
        action="store_true",
        help=f"leave out {COMPANY_LOCAL.name}, and write to dist/public unless --out says",
    )
    parser.add_argument(
        "--out", type=Path, help=f"directory for the PDFs (default {OUTPUT.relative_to(REPO)})"
    )
    args = parser.parse_args(argv)
    out: Path = args.out or (OUTPUT / "public" if args.public else OUTPUT)
    try:
        data = load_data(args.data, local=not args.public)
        out.mkdir(parents=True, exist_ok=True)
        for document in documents(args.documents):
            result = render(document, data)
            suffix = f"-{args.data.stem}" if args.data else ""
            target = out / f"{document}{suffix}.pdf"
            target.write_bytes(result.pdf)
            print(f"{target}: {len(result.pdf)} bytes")
            if args.data and result.blank:
                print(f"  left blank: {', '.join(result.blank)}")
        return 0
    except ContractFailed as exc:
        print(f"build_contract_pdf: {exc.detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 11: Run the tests, then the real script**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_contract_pdf.py projects/hub/packages/core/tests/test_contract_render.py projects/hub/packages/core/tests/test_guide_pdf.py`
Expected: all pass (test_guide_pdf still passes: nothing it locks moved).

Run: `uv run python projects/hub/tools/build_contract_pdf.py && uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --public --data projects/hub/content/contratti/incarico.esempio.json && uv run python projects/hub/tools/build_guide_pdf.py --check`
Expected: two PDFs, then one filled letter listing `left blank: ...firma...`, then `...: current, 49053 bytes`.

- [ ] **Step 12: Point the checks, CI and docs at the new home**

`.github/preflight.json`, replace the whole `contract-pdf` object with:

```json
    {
      "id": "contract-pdf",
      "run": "uv run python projects/hub/tools/build_contract_pdf.py && uv run pytest -q projects/hub/packages/core/tests/test_contract_render.py",
      "when": [
        "projects/hub/content/contratti/**",
        "projects/hub/tools/**",
        "projects/hub/packages/core/src/rebase_core/contracts/**",
        "projects/hub/packages/core/tests/test_contract_render.py",
        "shared/brand/palette.css",
        "shared/brand/fonts/**"
      ],
      "why": "Typesets both contracts with pandoc and Typst twice: through the laptop script into the ignored content/contratti/dist/, and through rebase_core.contracts the way the API renders them, the signature query included. Nothing is committed, so nothing can go stale; what this catches is a Markdown edit, a marker or a template change that no longer compiles."
    },
```

`.github/workflows/ci.yml`, in the `hub-py` job after the `tests:` block (line 534), add:

```yaml
      # The contracts are typeset at request time since REB-387 (rebase_core.contracts),
      # and test_contract_render.py runs the real pandoc and Typst over both texts.
      renderer: true
```

`projects/hub/content/README.md`: replace the `contratti/` bullet (lines 9-13) with

```
- `contratti/`: the example data and the ignored local files for the contract between
  rebase and a freelancer (roadmap #285, REB-386). The two texts moved to
  `../packages/core/src/rebase_core/contracts/texts/` in REB-387, because the hub
  typesets them at request time and the API image carries only `packages/`:
  `contratto-quadro.md` is signed once and holds every rule; `lettera-di-incarico.md` is
  signed per engagement and holds the client, the work, the dates and the numbers.
  Italian for the same reason. A draft, see [The contracts](#the-contracts) below.
```

replace «Two markers, both handled by `../tools/build_contract_pdf.py`:» (line 55) with «Two markers, both handled by `rebase_core.contracts.fields` (`../tools/build_contract_pdf.py` is the laptop's thin wrapper over it):», replace «`contratti/rebase.json` holds rebase's company data» (line 70) with «`texts/rebase.json`, package data beside the two texts, holds rebase's company data», and «The PDFs land in `contratti/dist/`, which git ignores: nothing serves them, so unlike the guide nothing is committed or locked.» with «The laptop's PDFs land in `contratti/dist/`, which git ignores; the hub stores its own per document, in its database (REB-387). Unlike the guide, nothing is committed or locked.»

`projects/hub/AGENTS.md`: line 27 becomes `packages/core/   rebase_core: models, migrations, services, the ad conversion, the perk files, the contracts and their texts`; line 30 becomes `content/         the prose a perk is made of, reviewed as prose, and the contracts' example data`.

`pyproject.toml` lines 128-130: replace the comment with

```toml
  # The hub's two typesetting scripts are modules of one directory, not a package:
  # `test_contract_pdf.py` imports both by bare name, which is how Python resolves them
  # when either runs, and this is how mypy does too.
```

- [ ] **Step 13: Lint, types, the whole hub suite**

Run: `uv run ruff check --fix projects/hub && uv run ruff format projects/hub && uv run mypy && uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests`
Expected: clean, all green.

- [ ] **Step 14: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contracts \
  projects/hub/tools/build_contract_pdf.py \
  projects/hub/packages/core/tests/test_contract_pdf.py \
  projects/hub/packages/core/tests/test_contract_render.py \
  .github/preflight.json .github/workflows/ci.yml \
  projects/hub/content/README.md projects/hub/AGENTS.md pyproject.toml
git commit -F - <<'EOF'
feat(hub): typeset the contracts from rebase_core, and find their signing blanks

I move the logic of tools/build_contract_pdf.py into rebase_core.contracts,
with the two Markdown texts, the Typst template and rebase.json as its
package data, so the API can typeset a contract without content/ or tools/.
The script stays as the laptop's thin wrapper with --data, --public and
rebase.local.json. The template now leaves a metadata label on every blank
the signing site fills, and signature_blanks reads their page and box back
with typst query. The brand is read the way the guide reads it, and a test
holds the two readings equal since the guide's script is locked. The
contract-pdf preflight check and the hub's CI job run the real render.

REB-N.
EOF
```

---

### Task 2: The API image can typeset a contract

**Files:**
- Modify: `projects/hub/Dockerfile.api:1-25`
- Modify: `projects/hub/packages/core/pyproject.toml:6-20` (dependency), `uv.lock` (by `uv lock`), `pyproject.toml:51-56` (dev comment)
- Modify: `projects/hub/packages/core/src/rebase_core/config.py:79` (new block before `get_settings`)
- Modify: `projects/hub/packages/core/src/rebase_core/contracts/fields.py` (append `SIGNER_FIELDS`, `signer_data`)
- Modify: `projects/hub/packages/core/src/rebase_core/cli.py:81,211-247` (`contracts-check`)
- Modify: `projects/hub/docker-compose.yml:5-24`, `projects/hub/.env.example:48-49`
- Create: `projects/hub/packages/core/tests/test_api_image.py`
- Modify: `projects/hub/packages/core/tests/test_contract_pdf.py`, `test_contract_render.py`
- Modify: `.github/preflight.json:312-326` (`hub-image`), `.github/workflows/ci.yml:617-635` (`hub-images`), `projects/hub/AGENTS.md` (new section)

**Interfaces:**
- Consumes (Task 1): `rebase_core.contracts.fields.{ContractFailed, Value, read_layer, merge_data}`, `rebase_core.contracts.render.{DOCUMENTS, company_defaults, render, signature_blanks}`, `rebase_core.contracts.brand.{PALETTE, FONT, REPO}`.
- Produces: `Settings.signer_json: str` (env `REBASE_SIGNER_JSON`, default `""`); `rebase_core.contracts.fields.SIGNER_FIELDS: tuple[str, ...]` (the seven `rebase-*` keys) and `signer_data(raw: str) -> dict[str, Value]` (empty string gives `{}`, a foreign key or bad JSON raises `ContractFailed`); CLI `rebase contracts-check` (exit 0 and one line per document).

- [ ] **Step 1: Write the failing tests**

`projects/hub/packages/core/tests/test_api_image.py`:

```python
"""The API image carries the renderer PigroCRM's image carries, at the same versions, and
the two brand files the contracts are typeset in (REB-387)."""

import re
from pathlib import Path

from rebase_core.contracts import brand

REPO = Path(__file__).resolve().parents[5]
HUB_IMAGE = REPO / "projects" / "hub" / "Dockerfile.api"
CRM_IMAGE = REPO / "projects" / "pigrocrm" / "Dockerfile.api"


def _pins(dockerfile: Path) -> dict[str, str]:
    text = dockerfile.read_text(encoding="utf-8")
    return dict(re.findall(r"^ARG (PANDOC_VERSION|TYPST_VERSION)=(\S+)$", text, re.M))


def test_the_hub_image_pins_the_renderer_pigrocrm_was_verified_against() -> None:
    assert _pins(HUB_IMAGE) == _pins(CRM_IMAGE) == {
        "PANDOC_VERSION": "3.8.2.1",
        "TYPST_VERSION": "0.14.2",
    }


def test_the_hub_image_copies_the_brand_where_the_renderer_reads_it() -> None:
    text = HUB_IMAGE.read_text(encoding="utf-8")
    for source in (brand.PALETTE, brand.FONT):
        path = str(source.relative_to(REPO))
        assert f"COPY {path} {path}" in text, path
    assert brand.REPO == REPO
```

Append to `test_contract_pdf.py` (and add `signer_data` to its `rebase_core.contracts.fields` import):

```python
def test_the_signer_setting_fills_only_rebases_own_fields() -> None:
    assert signer_data("") == {}
    assert signer_data("  ") == {}
    assert signer_data('{"rebase-rappresentante": "Nome Cognome"}') == {
        "rebase-rappresentante": "Nome Cognome"
    }
    with pytest.raises(ContractFailed, match="only the rebase-"):
        signer_data('{"professionista-nome": "Qualcuno"}')
    with pytest.raises(ContractFailed, match="cannot read REBASE_SIGNER_JSON"):
        signer_data("{non json")
```

Append to `test_contract_render.py` (add `import pytest` and `from rebase_core.cli import main`):

```python
def test_the_contracts_check_command_typesets_both_texts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["contracts-check"]) == 0
    out = capsys.readouterr().out
    for document in DOCUMENTS:
        assert f"{document}: " in out
    assert out.count("spazi da firmare") == len(DOCUMENTS)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_api_image.py projects/hub/packages/core/tests/test_contract_pdf.py projects/hub/packages/core/tests/test_contract_render.py -k "image or signer or contracts_check"`
Expected: FAIL (`{} != {...}` on the pins, `ImportError: cannot import name 'signer_data'`, `invalid choice: 'contracts-check'`).

- [ ] **Step 3: Add the signer layer to `fields.py`** (append)

```python
# The fields of the two contracts that describe rebase itself. `REBASE_SIGNER_JSON` may
# fill these and nothing else: a freelancer's or a client's data never come from a
# server setting.
SIGNER_FIELDS = (
    "rebase-ragione-sociale",
    "rebase-sede",
    "rebase-cf",
    "rebase-piva",
    "rebase-codice-destinatario",
    "rebase-pec",
    "rebase-rappresentante",
)


def signer_data(raw: str) -> dict[str, Value]:
    """`REBASE_SIGNER_JSON` as a layer over `rebase.json`: `{}` when unset, which prints
    those fields as blank lines (a preview can live with it, sending in phase 3 cannot)."""
    if not raw.strip():
        return {}
    layer = read_layer(raw, "REBASE_SIGNER_JSON")
    foreign = sorted(set(layer) - set(SIGNER_FIELDS))
    if foreign:
        raise ContractFailed(
            f"REBASE_SIGNER_JSON may fill only the rebase-* fields, not {', '.join(foreign)}"
        )
    return layer
```

- [ ] **Step 4: Add the setting** (`config.py`, before `@lru_cache`)

```python
    # --- the contracts (REB-387) -----------------------------------------------------
    # Who signs for rebase, as the `rebase-*` fields of the framework agreement and the
    # letter: one JSON object, e.g. {"rebase-rappresentante": "Nome Cognome"}, read over
    # the package's own `rebase.json`. The server's `.env` only: this repository is
    # public. Empty means those fields print as blank lines.
    signer_json: str = ""
```

- [ ] **Step 5: Add `rebase contracts-check`** (`cli.py`: imports at the top, the function after `conversions_check`, the parser and dispatch in `main`)

```python
from rebase_core.contracts.fields import ContractFailed, Value, merge_data
from rebase_core.contracts.render import DOCUMENTS, company_defaults, render, signature_blanks
```

```python
# Fiction only, as the public example is: no signer data and no person, so the check
# can run anywhere and prints nothing anybody would mind reading.
_CHECK_DATA: dict[str, Value] = {
    "numero": "2026-000",
    "professionista-nome": "Nome Cognome",
    "cliente-ragione-sociale": "Azienda Esempio S.r.l.",
    "compenso": 450,
}


def contracts_check() -> int:
    """`rebase contracts-check`: can this machine typeset a contract?

    Renders both texts from fiction with this machine's pandoc, Typst, palette and
    typeface, and asks Typst where the signing blanks landed. Writes no file and no row.
    The `hub-image` preflight check and CI's image job run it inside the built image."""
    try:
        for document in DOCUMENTS:
            data = merge_data(company_defaults(), _CHECK_DATA)
            result = render(document, data)
            blanks = signature_blanks(document, data)
            print(
                f"{document}: {len(result.pdf)} byte, versione {result.version}, "
                f"{len(blanks)} spazi da firmare"
            )
    except ContractFailed as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0
```

In `main`, after the `conversions-check` parser:

```python
    sub.add_parser(
        "contracts-check",
        help="Compone i due contratti con pandoc e Typst: questa macchina li sa generare?",
    )
```

and in the dispatch: `if args.command == "contracts-check": return contracts_check()`.

- [ ] **Step 6: Make fontTools a runtime dependency**

`projects/hub/packages/core/pyproject.toml`, append to `dependencies`:

```toml
  # The contracts are typeset at request time (REB-387): Typst reads neither woff2 nor
  # a variable axis, so fontTools instances the brand's Outfit. `[woff]` brings brotli.
  # The same pin as the root dev group, which the guide's build already uses.
  "fonttools[woff]==4.62.0",
```

Root `pyproject.toml` lines 51-56: change the last sentence of the fontTools comment to «Also a runtime dependency of `rebase-core` since REB-387, which typesets the contracts in the API; listed here too for the guide's script.»

Run: `uv lock && uv sync --frozen`
Expected: `uv.lock` changes only in `rebase-core`'s dependency list.

- [ ] **Step 7: Install the renderer and the brand in the image**

Replace `projects/hub/Dockerfile.api` lines 1-4 with:

```dockerfile
FROM python:3.13-slim-bookworm

# The contracts a freelancer signs are typeset at request time since REB-387
# (`rebase_core.contracts`), with the pair PigroCRM's renderer was verified against:
# Pandoc 3.8.2.1 and Typst 0.14.2, downloaded as projects/pigrocrm/Dockerfile.api does.
# `packages/core/tests/test_api_image.py` fails when the two files name different pins.
ARG TYPST_VERSION=0.14.2
ARG PANDOC_VERSION=3.8.2.1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 ca-certificates curl xz-utils \
    && curl -fL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-amd64.tar.gz" \
       | tar -xz -C /tmp \
    && mv /tmp/pandoc-${PANDOC_VERSION}/bin/pandoc /usr/local/bin/pandoc \
    && chmod +x /usr/local/bin/pandoc \
    && curl -fL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
       | tar -xJ -C /tmp \
    && mv /tmp/typst-x86_64-unknown-linux-musl/typst /usr/local/bin/typst \
    && chmod +x /usr/local/bin/typst \
    && apt-get purge -y curl xz-utils && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /tmp/pandoc-${PANDOC_VERSION} /tmp/typst-x86_64-unknown-linux-musl
```

and after `COPY projects/hub/packages ./projects/hub/packages` add:

```dockerfile
# The palette and the typeface a contract is typeset in, at the paths
# `rebase_core.contracts.brand` reads them from: the image mirrors the repository.
COPY shared/brand/palette.css shared/brand/palette.css
COPY shared/brand/fonts/outfit-variable-latin.woff2 shared/brand/fonts/outfit-variable-latin.woff2
```

- [ ] **Step 8: Carry the setting through compose and `.env.example`**

`docker-compose.yml`, at the end of `x-api-environment`:

```yaml
  # Who signs for rebase in the contracts (REB-387): one JSON object of `rebase-*`
  # fields. Empty: those fields print as blank lines.
  REBASE_SIGNER_JSON: ${REBASE_SIGNER_JSON:-}
```

`.env.example`, after the PigroCRM block:

```
# --- the contracts (REB-387) ---------------------------------------------------------
# Who signs for rebase, as the `rebase-*` fields of the framework agreement and the
# letter: one JSON object on one line, in single quotes so the double quotes survive.
# Read over the package's own `rebase.json`, whose nulls wait for the SRL (roadmap #284).
# Empty: those fields print as blank lines, fine for a preview. Never in the repository.
# REBASE_SIGNER_JSON='{"rebase-rappresentante": "Nome Cognome", "rebase-sede": "Milano"}'
REBASE_SIGNER_JSON=
```

- [ ] **Step 9: Run the image in preflight and CI**

`.github/preflight.json`, `hub-image`: `run` becomes

```
cd projects/hub && POSTGRES_PASSWORD=build-only-never-used REBASE_DATA_DIR=./build-only-never-mounted docker compose -p rebase build && docker run --rm rebase-api uv run --no-sync rebase contracts-check
```

add to its `when`: `"shared/brand/palette.css"`, `"shared/brand/fonts/**"`, `"projects/hub/packages/core/pyproject.toml"`, `"projects/hub/packages/core/src/rebase_core/contracts/**"`, and append to its `why`: « Then it typesets both contracts inside the built image (`rebase contracts-check`), which is the one place pandoc, Typst, fontTools and the brand files all have to be present at once.»

`.github/workflows/ci.yml`, `hub-images`, after the build step:

```yaml
      - name: Typeset both contracts inside the image
        # REB-387: the API renders contracts at request time, so an image that builds
        # but lacks pandoc, Typst, fontTools or the brand files is a broken deploy.
        run: docker run --rm rebase-api uv run --no-sync rebase contracts-check
```

`projects/hub/AGENTS.md`, a new section after «The guide is a generated file...»:

```
## The contracts are typeset at request time

Since REB-387 the API writes the framework agreement and the letter of engagement itself,
with `rebase_core.contracts`: pandoc and Typst over the Markdown in
`packages/core/src/rebase_core/contracts/texts/`, the template beside it, and the palette
and the typeface read from `shared/brand/` at the paths the image mirrors. So
`Dockerfile.api` carries PigroCRM's pandoc and Typst (`test_api_image.py` holds the two
images to one pair) and fontTools is a dependency of `rebase_core`. `rebase
contracts-check` typesets both texts from fiction and says whether a machine can; the
`hub-image` preflight check and CI's image job run it inside the built image. Who signs
for rebase comes from `REBASE_SIGNER_JSON` in the host `.env`, never from the repository.
```

- [ ] **Step 10: Run everything, then the image**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_api_image.py projects/hub/packages/core/tests/test_contract_pdf.py projects/hub/packages/core/tests/test_contract_render.py && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: all pass.

Run: `cd projects/hub && POSTGRES_PASSWORD=build-only-never-used REBASE_DATA_DIR=./build-only-never-mounted docker compose -p rebase build && docker run --rm rebase-api uv run --no-sync rebase contracts-check`
Expected: two lines, `contratto-quadro: ... byte, versione 0.1, 4 spazi da firmare` and `lettera-di-incarico: ... 3 spazi da firmare` (two freelancer signatures, the date and rebase's blank on the framework; one, the date and rebase's on the letter).

- [ ] **Step 11: Commit**

```bash
git add projects/hub/Dockerfile.api projects/hub/packages/core/pyproject.toml uv.lock pyproject.toml \
  projects/hub/packages/core/src/rebase_core/config.py \
  projects/hub/packages/core/src/rebase_core/contracts/fields.py \
  projects/hub/packages/core/src/rebase_core/cli.py \
  projects/hub/docker-compose.yml projects/hub/.env.example \
  projects/hub/packages/core/tests/test_api_image.py \
  projects/hub/packages/core/tests/test_contract_pdf.py \
  projects/hub/packages/core/tests/test_contract_render.py \
  .github/preflight.json .github/workflows/ci.yml projects/hub/AGENTS.md
git commit -F - <<'EOF'
feat(hub): the API image typesets contracts, signed for rebase from a setting

I install PigroCRM's pandoc and Typst pins in the hub's API image, copy the
brand's palette and Outfit where rebase_core.contracts reads them, and make
fontTools a runtime dependency of rebase_core. REBASE_SIGNER_JSON carries
the rebase-* fields of whoever signs for rebase, and refuses anything else.
`rebase contracts-check` typesets both texts from fiction; the hub-image
preflight check and CI's image job run it inside the built image, and a test
holds the two Dockerfiles to one pair of pins.

REB-N.
EOF
```

---

### Task 3: Migration 0017 and the four models

**Files:**
- Create: `projects/hub/packages/core/migrations/versions/0017_matches_and_contracts.py`
- Modify: `projects/hub/packages/core/src/rebase_core/models.py:367-376` (audit constants) and append the new section after `Company` (line 333)
- Modify: `projects/hub/packages/core/tests/test_migrations.py`

**Interfaces:**
- Consumes: `rebase_core.db.{Base, PrimaryKeyMixin, TimestampMixin}`, `rebase_core.migrate.{INI_PATH, upgrade_to_head, head_revision}`.
- Produces (in `rebase_core.models`): constants `MATCH_STATES = ("bozza", "in_firma", "attivo", "concluso", "annullato")`, `CONTRACT_KINDS = ("quadro", "lettera")`, `CONTRACT_STATES = ("generato", "in_attesa", "inviato", "firmato", "annullato", "disdetto")`, `CODICE_FISCALE_MAX_LENGTH = 16`, `PARTITA_IVA_MAX_LENGTH = 11`, `DOMICILIO_MAX_LENGTH = 300`, `PEC_MAX_LENGTH = 320`, `CLIENTE_PIVA_MAX_LENGTH = 32`, `SEDE_MAX_LENGTH = 300`, `LETTER_NUMBER_MAX_LENGTH = 12`, `TEXT_VERSION_MAX_LENGTH = 20`, `DOCUMENSO_ID_MAX_LENGTH = 100`; classes `FreelancerFiscal` (`freelancer_id`, `codice_fiscale`, `partita_iva`, `domicilio`, `pec`, `updated_by`, timestamps), `Match` (`freelancer_id`, `company_id`, `cliente_ragione_sociale`, `cliente_piva`, `cliente_sede`, `stato`, `created_by`, `cancelled_at`, timestamps), `ContractDocument` (`kind`, `freelancer_id`, `match_id`, `numero`, `text_version`, `testo_bozza`, `data: dict[str, Any]`, `pdf: bytes`, `stato`, `documenso_id`, `signing_url`, `sent_at`, `signed_at`, `signed_pdf`, `notice_at`, `created_by`, `sent_by`, timestamps), `LetterCounter` (`anno` primary key, `ultimo`). Table names: `freelancer_fiscal`, `matches`, `contract_documents`, `contract_letter_counters`. Audit constants extended with entity types `"match"`, `"freelancer_fiscal"` and kinds `"match_created"`, `"match_cancelled"`, `"match_closed"`, `"fiscal_updated"`, `"documents_sent"`, `"document_cancelled"`, `"mail_resent"`, `"notice_recorded"`.

- [ ] **Step 1: Write the failing migration tests** (append to `test_migrations.py`; add `from alembic import command`, `from alembic.config import Config` and `INI_PATH` to the `rebase_core.migrate` import)

```python
def test_the_contract_constraints_are_installed(hub_engine: Engine) -> None:
    """REB-387: a framework agreement never hangs on a match and never takes a letter
    number, a letter always does both, a number is taken once, and a notice is a
    framework agreement's state alone. Real constraints, proven with raw inserts inside
    one transaction rolled back at the end."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) VALUES "
                "(gen_random_uuid(), 'ck-contracts@studio.it', 'A', 'B', 'admin', true) "
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
        company_id = connection.execute(
            text(
                "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
                "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato) VALUES "
                "(gen_random_uuid(), :user_id, 'ACME', 'Dev', 'Un progetto', '2026-10-01', "
                "'3 mesi', 500, 'remoto', 1, 'nuovo') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar()
        match_id = connection.execute(
            text(
                "INSERT INTO matches (id, freelancer_id, company_id, cliente_ragione_sociale, "
                "cliente_piva, cliente_sede, stato, created_by) VALUES (gen_random_uuid(), "
                ":freelancer_id, :company_id, 'ACME S.r.l.', '01234567890', 'Milano', 'bozza', "
                ":user_id) RETURNING id"
            ),
            {"freelancer_id": freelancer_id, "company_id": company_id, "user_id": user_id},
        ).scalar()

        def _document(kind: str, match: object, numero: str | None, stato: str) -> None:
            connection.execute(
                text(
                    "INSERT INTO contract_documents (id, kind, freelancer_id, match_id, numero, "
                    "text_version, testo_bozza, data, pdf, stato, created_by) VALUES "
                    "(gen_random_uuid(), :kind, :freelancer_id, :match_id, :numero, '0.1', true, "
                    "'{}', :pdf, :stato, :user_id)"
                ),
                {
                    "kind": kind,
                    "freelancer_id": freelancer_id,
                    "match_id": match,
                    "numero": numero,
                    "pdf": b"%PDF-",
                    "stato": stato,
                    "user_id": user_id,
                },
            )

        refused = (
            ("quadro", match_id, None, "generato"),  # a framework hangs on no match
            ("quadro", None, "2026-001", "generato"),  # and takes no number
            ("lettera", match_id, None, "generato"),  # a letter always has a number
            ("lettera", None, "2026-001", "generato"),  # and a match
            ("lettera", match_id, "2026-001", "disdetto"),  # a notice is a framework's
            ("quadro", None, None, "forse"),  # an unknown state
            ("fattura", None, None, "generato"),  # an unknown kind
        )
        for kind, match, numero, stato in refused:
            with pytest.raises(IntegrityError), connection.begin_nested():
                _document(kind, match, numero, stato)

        with connection.begin_nested():  # the valid shapes are accepted
            _document("quadro", None, None, "generato")
            _document("lettera", match_id, "2026-001", "in_attesa")
        with pytest.raises(IntegrityError), connection.begin_nested():  # a number, once
            _document("lettera", match_id, "2026-001", "generato")
        with pytest.raises(IntegrityError), connection.begin_nested():  # one tax row per card
            for _ in range(2):
                connection.execute(
                    text(
                        "INSERT INTO freelancer_fiscal (id, freelancer_id, codice_fiscale, "
                        "partita_iva, domicilio, updated_by) VALUES (gen_random_uuid(), "
                        ":freelancer_id, 'LVLDAA85T50H501Z', '01234567890', 'Milano', :user_id)"
                    ),
                    {"freelancer_id": freelancer_id, "user_id": user_id},
                )
        outer.rollback()


def test_migration_0017_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0017's statements over tables that already exist, and the
    downgrade leaves 0016's schema: both must work, and the result must still be the
    models' schema."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0016")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0016'"))
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

Run: `uv run pytest -q projects/hub/packages/core/tests/test_migrations.py`
Expected: FAIL. The constraints test stops on `relation "matches" does not exist`; the second test stops because the head is still 0016, so the downgrade to 0016 is a no-op and the tables it checks through the models' schema diff do not exist.

- [ ] **Step 3: Write the models** (in `models.py`, after `Company`)

```python
# ---- matches and the contracts they write (REB-387) -------------------------------------

MATCH_STATES = ("bozza", "in_firma", "attivo", "concluso", "annullato")
CONTRACT_KINDS = ("quadro", "lettera")
CONTRACT_STATES = ("generato", "in_attesa", "inviato", "firmato", "annullato", "disdetto")
CODICE_FISCALE_MAX_LENGTH = 16
PARTITA_IVA_MAX_LENGTH = 11
DOMICILIO_MAX_LENGTH = 300
PEC_MAX_LENGTH = 320
# A client may be a foreign company, whose VAT number is not eleven Italian digits.
CLIENTE_PIVA_MAX_LENGTH = 32
SEDE_MAX_LENGTH = 300
LETTER_NUMBER_MAX_LENGTH = 12
TEXT_VERSION_MAX_LENGTH = 20
DOCUMENSO_ID_MAX_LENGTH = 100


class FreelancerFiscal(Base, PrimaryKeyMixin, TimestampMixin):
    """A freelancer's tax data as the two contracts print them: one row per card
    (`uq_freelancer_fiscal_freelancer_id`). A table of its own rather than columns on
    `freelancers`, which PostHog's warehouse syncs whole (spec § 2); none of the four
    tables of this section is synced. `updated_by` is the admin who saved them last."""

    __tablename__ = "freelancer_fiscal"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    codice_fiscale: Mapped[str] = mapped_column(String(CODICE_FISCALE_MAX_LENGTH), nullable=False)
    partita_iva: Mapped[str] = mapped_column(String(PARTITA_IVA_MAX_LENGTH), nullable=False)
    domicilio: Mapped[str] = mapped_column(String(DOMICILIO_MAX_LENGTH), nullable=False)
    pec: Mapped[str | None] = mapped_column(String(PEC_MAX_LENGTH), default=None)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (Index("uq_freelancer_fiscal_freelancer_id", "freelancer_id", unique=True),)


class Match(Base, PrimaryKeyMixin, TimestampMixin):
    """A freelancer card paired with a company request, and the client's legal data as
    the letter prints them. `stato` is one of `MATCH_STATES`: `bozza` once the documents
    are generated, `in_firma` and `attivo` with the signature (phase 3), `concluso` and
    `annullato` by an admin."""

    __tablename__ = "matches"

    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    cliente_ragione_sociale: Mapped[str] = mapped_column(String(AZIENDA_MAX_LENGTH), nullable=False)
    cliente_piva: Mapped[str] = mapped_column(String(CLIENTE_PIVA_MAX_LENGTH), nullable=False)
    cliente_sede: Mapped[str] = mapped_column(String(SEDE_MAX_LENGTH), nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="bozza")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_matches_freelancer_created", "freelancer_id", "created_at"),
        CheckConstraint(
            "stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')",
            name="ck_matches_stato",
        ),
    )


class ContractDocument(Base, PrimaryKeyMixin, TimestampMixin):
    """One generated contract, its PDF in the row as the CV is (one place to delete
    from). A framework agreement (`quadro`) belongs to the freelancer and hangs on no
    match; a letter (`lettera`) belongs to a match and carries a `numero`, `YYYY-NNN`.
    `data` is every field value the PDF printed, so a document can be regenerated the
    same; `testo_bozza` says the text was still `status: draft`, a preview nothing may
    send. The Documenso columns and `notice_at` are phase 3's."""

    __tablename__ = "contract_documents"

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    match_id: Mapped[UUID | None] = mapped_column(ForeignKey("matches.id"), default=None)
    numero: Mapped[str | None] = mapped_column(String(LETTER_NUMBER_MAX_LENGTH), default=None)
    text_version: Mapped[str] = mapped_column(String(TEXT_VERSION_MAX_LENGTH), nullable=False)
    testo_bozza: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False)
    documenso_id: Mapped[str | None] = mapped_column(String(DOCUMENSO_ID_MAX_LENGTH), default=None)
    signing_url: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    sent_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (
        Index("ix_contract_documents_freelancer", "freelancer_id", "kind", "created_at"),
        Index("ix_contract_documents_match_id", "match_id"),
        Index("uq_contract_documents_numero", "numero", unique=True),
        CheckConstraint("kind IN ('quadro', 'lettera')", name="ck_contract_documents_kind"),
        CheckConstraint(
            "stato IN ('generato', 'in_attesa', 'inviato', 'firmato', 'annullato', 'disdetto')",
            name="ck_contract_documents_stato",
        ),
        CheckConstraint(
            "(kind = 'quadro') = (match_id IS NULL)",
            name="ck_contract_documents_match_for_letters",
        ),
        CheckConstraint(
            "(kind = 'lettera') = (numero IS NOT NULL)",
            name="ck_contract_documents_numero_for_letters",
        ),
        CheckConstraint(
            "stato <> 'disdetto' OR kind = 'quadro'",
            name="ck_contract_documents_notice_for_quadro",
        ),
    )


class LetterCounter(Base):
    """The last letter number taken in a year: `2026-001`, `2026-002`, ... Bumped with
    `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` inside the transaction that writes
    the letter (`rebase_core.framework.next_letter_number`), so two letters written at
    once never share a number and a generation that fails leaves no gap."""

    __tablename__ = "contract_letter_counters"

    anno: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ultimo: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("ultimo >= 1", name="ck_contract_letter_counters_positive"),)
```

In the audit block (lines 369-376) extend the two tuples:

```python
ADMIN_ACTION_ENTITY_TYPES = ("freelancer", "company", "match", "freelancer_fiscal")
```

```python
# REB-387 adds the matches' own kinds, on entity type `match` (phase 3 writes the last
# four), and `fiscal_updated` on `freelancer_fiscal`, whose payload names the fields that
# changed and never their values: a tax identifier is not copied into this table.
ADMIN_ACTION_KINDS = (
    "overridden",
    "cleared",
    "deleted",
    "restored",
    "match_created",
    "match_cancelled",
    "match_closed",
    "fiscal_updated",
    "documents_sent",
    "document_cancelled",
    "mail_resent",
    "notice_recorded",
)
```

- [ ] **Step 4: Write the migration** (`0017_matches_and_contracts.py`)

```python
"""freelancer_fiscal, matches, contract_documents, contract_letter_counters

Revision ID: 0017
Revises: 0016

REB-387, phase 2: an admin pairs a freelancer card with a company request (`matches`),
and the hub writes the letter of engagement and, when the freelancer has no active one,
the framework agreement (`contract_documents`, PDFs in the row like the CV). The
freelancer's tax data are a table of their own (`freelancer_fiscal`) rather than
columns on `freelancers`, which PostHog's warehouse syncs whole; none of these four
tables is synced, so `test_warehouse_contract.py` does not change.
`contract_letter_counters` holds the last letter number taken in each year.

Every statement is conditional (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
EXISTS`), the discipline migration 0001's docstring states for this package: a retried
deploy must not error on a table the previous attempt already created. The check
constraints are declared inside each `CREATE TABLE`, so they arrive with their table
and need no `pg_constraint` guard of their own.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
    "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
)

_TABLES = (
    "CREATE TABLE IF NOT EXISTS freelancer_fiscal ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE, "
    "codice_fiscale VARCHAR(16) NOT NULL, "
    "partita_iva VARCHAR(11) NOT NULL, "
    "domicilio VARCHAR(300) NOT NULL, "
    "pec VARCHAR(320), "
    "updated_by UUID NOT NULL REFERENCES users (id), "
    f"{_TIMESTAMPS})",
    "CREATE TABLE IF NOT EXISTS matches ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "company_id UUID NOT NULL REFERENCES companies (id), "
    "cliente_ragione_sociale VARCHAR(200) NOT NULL, "
    "cliente_piva VARCHAR(32) NOT NULL, "
    "cliente_sede VARCHAR(300) NOT NULL, "
    "stato VARCHAR(20) NOT NULL, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "cancelled_at TIMESTAMP WITH TIME ZONE, "
    f"{_TIMESTAMPS}, "
    "CONSTRAINT ck_matches_stato CHECK "
    "(stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')))",
    "CREATE TABLE IF NOT EXISTS contract_documents ("
    "id UUID PRIMARY KEY, "
    "kind VARCHAR(10) NOT NULL, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "match_id UUID REFERENCES matches (id), "
    "numero VARCHAR(12), "
    "text_version VARCHAR(20) NOT NULL, "
    "testo_bozza BOOLEAN NOT NULL, "
    "data JSONB NOT NULL, "
    "pdf BYTEA NOT NULL, "
    "stato VARCHAR(20) NOT NULL, "
    "documenso_id VARCHAR(100), "
    "signing_url TEXT, "
    "sent_at TIMESTAMP WITH TIME ZONE, "
    "signed_at TIMESTAMP WITH TIME ZONE, "
    "signed_pdf BYTEA, "
    "notice_at TIMESTAMP WITH TIME ZONE, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "sent_by UUID REFERENCES users (id), "
    f"{_TIMESTAMPS}, "
    "CONSTRAINT ck_contract_documents_kind CHECK (kind IN ('quadro', 'lettera')), "
    "CONSTRAINT ck_contract_documents_stato CHECK (stato IN "
    "('generato', 'in_attesa', 'inviato', 'firmato', 'annullato', 'disdetto')), "
    "CONSTRAINT ck_contract_documents_match_for_letters CHECK "
    "((kind = 'quadro') = (match_id IS NULL)), "
    "CONSTRAINT ck_contract_documents_numero_for_letters CHECK "
    "((kind = 'lettera') = (numero IS NOT NULL)), "
    "CONSTRAINT ck_contract_documents_notice_for_quadro CHECK "
    "(stato <> 'disdetto' OR kind = 'quadro'))",
    "CREATE TABLE IF NOT EXISTS contract_letter_counters ("
    "anno INTEGER PRIMARY KEY, "
    "ultimo INTEGER NOT NULL, "
    "CONSTRAINT ck_contract_letter_counters_positive CHECK (ultimo >= 1))",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_freelancer_fiscal_freelancer_id "
    "ON freelancer_fiscal (freelancer_id)",
    "CREATE INDEX IF NOT EXISTS ix_matches_freelancer_created "
    "ON matches (freelancer_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_matches_company_id ON matches (company_id)",
    "CREATE INDEX IF NOT EXISTS ix_contract_documents_freelancer "
    "ON contract_documents (freelancer_id, kind, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_contract_documents_match_id ON contract_documents (match_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_numero ON contract_documents (numero)",
)


def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES):
        op.execute(statement)


def downgrade() -> None:
    for table in ("contract_letter_counters", "contract_documents", "matches", "freelancer_fiscal"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
```

- [ ] **Step 5: Run the migration tests and the schema diff**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_migrations.py projects/hub/packages/core/tests/test_warehouse_contract.py`
Expected: all pass; `test_the_migrations_produce_exactly_the_models_schema` reports no diff. If it reports a type or index difference, fix the model or the SQL until it is empty; do not relax the test.

- [ ] **Step 6: Lint, types, commit**

Run: `uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`

```bash
git add projects/hub/packages/core/migrations/versions/0017_matches_and_contracts.py \
  projects/hub/packages/core/src/rebase_core/models.py \
  projects/hub/packages/core/tests/test_migrations.py
git commit -F - <<'EOF'
feat(hub): the tables a match and its contracts live in

I add migration 0017 with freelancer_fiscal, matches, contract_documents
and the per-year letter counter, conditional like the others so a retried
deploy passes over tables that exist. The constraints hold a framework
agreement off any match and any number, a letter to both, a number to one
letter and a notice to framework agreements. The audit trail learns the
matches' kinds; a test proves the migration runs twice and rolls back.

REB-N.
EOF
```

---

### Task 4: Tax data, framework terms, letter numbers and the flow's schemas

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/contract_schemas.py`
- Create: `projects/hub/packages/core/src/rebase_core/framework.py`
- Create: `projects/hub/packages/core/src/rebase_core/fiscal.py`
- Modify: `projects/hub/packages/core/src/rebase_core/errors.py` (append `InvalidState`)
- Create: `projects/hub/packages/core/tests/test_contract_schemas.py`, `test_framework.py`, `test_fiscal.py`

**Interfaces:**
- Consumes: Task 1 (`contracts.fields.{DAYS, MONTH_END, TERM, DAYS_LIMIT, DAYS_LIMIT_MONTH_END, FIELD, Value, italian_date}`, `contracts.render.{text_path, text_version}`), Task 3 models, `rebase_core.schemas.{PROGETTO_MAX_LENGTH, TARIFFA_MIN, TARIFFA_MAX, clean_multiline}`, `rebase_core.audit.AdminActionService`.
- Produces:
  - `rebase_core.errors.InvalidState(DomainError)` (code `invalid_state`, a 409 in the API).
  - `rebase_core.contract_schemas`: `LETTERA_TEXT_FIELDS: tuple[str, ...]` (25 names), `LETTER_AUTO_FIELDS: frozenset[str]`, `FiscalData(codice_fiscale, partita_iva, domicilio, pec)`, `FiscalRead(freelancer_id, codice_fiscale, partita_iva, domicilio, pec, updated_by, updated_at)`, `ClienteData(cliente_ragione_sociale, cliente_piva, cliente_sede)`, `ClienteDraft` (same, all `str | None`), `LetteraDraft` (31 optional fields), `LetteraFields(LetteraDraft)` (required `ruolo`, `attivita`, `data_inizio`, `compenso`, `giorni_pagamento`, `fine_mese`; `to_fields() -> dict[str, Value]`), `MatchCreate(company_id: UUID, cliente: ClienteData, lettera: LetteraFields)`, `ContractDocumentRead`, `MatchRead`, `FreelancerContracts`, `MatchPrefill`, `ContractPdf(filename: str, content: bytes)`.
  - `rebase_core.framework`: `ROME: ZoneInfo`, `NOTICE_DAYS = 30`, `rome_today() -> date`, `anniversary(signed_on: date, year: int) -> date`, `next_renewal(signed_on: date, today: date) -> date`, `last_notice_day(renewal: date) -> date`, `signed_on(document: ContractDocument) -> date | None`, `is_active(document: ContractDocument) -> bool`, `active_framework(session, freelancer_id) -> ContractDocument | None`, `pending_framework(session, freelancer_id) -> ContractDocument | None` (prefers `inviato`, else the newest `generato`), `next_letter_number(session, year: int) -> str`, `document_read(document, today: date, current_version: str) -> ContractDocumentRead`.
  - `rebase_core.fiscal`: `ENTITY = "freelancer_fiscal"`, `FISCAL_FIELDS`, `FiscalService(session).get(freelancer_id) -> FiscalRead | None`, `.save(freelancer_id, data: FiscalData, admin_id) -> FiscalRead` (NotFound for an unknown or deleted card).

- [ ] **Step 1: Write the failing schema tests** (`test_contract_schemas.py`)

```python
"""What «Crea match» sends: tax data an Italian contract can print, a letter whose
numbers the law allows, and nothing that could carry the client's budget (REB-387)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rebase_core.contract_schemas import (
    LETTER_AUTO_FIELDS,
    ClienteData,
    FiscalData,
    LetteraDraft,
    LetteraFields,
    MatchCreate,
)
from rebase_core.contracts.fields import DAYS, FIELD, MONTH_END, TERM
from rebase_core.contracts.render import text_path

REQUIRED = {
    "ruolo": "Backend developer",
    "attivita": "Le API del prodotto.",
    "data_inizio": date(2026, 10, 1),
    "compenso": Decimal("450"),
    "giorni_pagamento": 30,
    "fine_mese": True,
}


def test_tax_identifiers_are_stored_the_way_they_print() -> None:
    data = FiscalData(
        codice_fiscale=" lvl daa85t50h501z ",
        partita_iva="IT 012 345 678 90",
        domicilio="Via Roma 1, Milano",
    )
    assert (data.codice_fiscale, data.partita_iva, data.pec) == (
        "LVLDAA85T50H501Z",
        "01234567890",
        None,
    )
    assert FiscalData(codice_fiscale="01234567890", partita_iva="01234567890", domicilio="X")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("codice_fiscale", "LVLDAA85T50"),
        ("partita_iva", "0123456789"),
        ("domicilio", "Via Roma 1\nMilano"),
        ("pec", "non-una-pec"),
    ],
)
def test_tax_data_a_contract_could_not_print_are_refused(field: str, value: str) -> None:
    payload = {
        "codice_fiscale": "LVLDAA85T50H501Z",
        "partita_iva": "01234567890",
        "domicilio": "Via Roma 1, Milano",
        field: value,
    }
    with pytest.raises(ValidationError):
        FiscalData(**payload)  # type: ignore[arg-type]


def test_a_letter_turns_into_the_markdowns_own_keys_and_printable_values() -> None:
    fields = LetteraFields(**REQUIRED, data_fine=date(2027, 1, 29)).to_fields()  # type: ignore[arg-type]
    assert fields["data-inizio"] == "1° ottobre 2026"
    assert fields["data-fine"] == "29 gennaio 2027"
    assert fields["compenso"] == 450 and isinstance(fields["compenso"], int)
    assert fields["giorni-pagamento"] == 30 and fields["fine-mese"] is True
    assert fields["risultati"] is None
    half = LetteraFields(**{**REQUIRED, "compenso": Decimal("450.50")}).to_fields()  # type: ignore[arg-type]
    assert half["compenso"] == 450.5


@pytest.mark.parametrize(
    "change",
    [
        {"giorni_pagamento": 45, "fine_mese": True},  # past 60 days from the invoice
        {"giorni_pagamento": 61, "fine_mese": False},
        {"compenso": Decimal("0")},
        {"compenso": Decimal("450.005")},
        {"ruolo": "   "},
        {"data_inizio": date(2026, 10, 1), "data_fine": date(2026, 9, 30)},
    ],
)
def test_a_letter_the_law_or_the_page_would_not_allow_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LetteraFields(**{**REQUIRED, **change})  # type: ignore[arg-type]


def test_the_form_covers_every_field_of_the_letter_and_the_hub_fills_the_rest() -> None:
    """A field added to the Markdown and to neither list is a blank nobody fills."""
    asked = set(FIELD.findall(text_path("lettera-di-incarico").read_text(encoding="utf-8")))
    written = {name.replace("_", "-") for name in LetteraDraft.model_fields}
    assert asked == (written - {DAYS, MONTH_END}) | LETTER_AUTO_FIELDS | {TERM}


def test_nothing_in_the_flow_can_carry_the_client_budget() -> None:
    """Spec § 1h: what rebase agrees with the client never reaches a freelancer's
    document, so no model of this flow has a field it could travel in."""
    for model in (FiscalData, ClienteData, LetteraDraft, LetteraFields, MatchCreate):
        assert not any("budget" in name for name in model.model_fields), model
```

- [ ] **Step 2: Write the failing framework and fiscal tests**

`test_framework.py`:

```python
"""The framework agreement's twelve months, and the letter numbers (REB-387)."""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebase_core.db import session_factory
from rebase_core.framework import (
    document_read,
    is_active,
    last_notice_day,
    next_letter_number,
    next_renewal,
    signed_on,
)
from rebase_core.models import ContractDocument


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM contract_letter_counters"))
    hub_session.commit()


@pytest.mark.parametrize(
    ("signed", "today", "renewal"),
    [
        (date(2026, 10, 1), date(2026, 12, 1), date(2027, 10, 1)),
        (date(2026, 10, 1), date(2027, 9, 30), date(2027, 10, 1)),
        # On the anniversary itself the new twelve months have started.
        (date(2026, 10, 1), date(2027, 10, 1), date(2028, 10, 1)),
        (date(2026, 10, 1), date(2026, 10, 1), date(2027, 10, 1)),
        # 29 February has an anniversary in common years too: the day before March.
        (date(2028, 2, 29), date(2029, 1, 10), date(2029, 2, 28)),
    ],
)
def test_the_next_renewal_is_the_first_anniversary_after_today(
    signed: date, today: date, renewal: date
) -> None:
    assert next_renewal(signed, today) == renewal


def test_the_last_day_for_a_notice_is_thirty_days_before_the_renewal() -> None:
    assert last_notice_day(date(2027, 10, 1)) == date(2027, 9, 1)


def _quadro(**columns: object) -> ContractDocument:
    return ContractDocument(kind="quadro", **columns)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("document", "active"),
    [
        (_quadro(stato="firmato", notice_at=None), True),
        (_quadro(stato="firmato", notice_at=datetime(2027, 1, 1, tzinfo=UTC)), False),
        (_quadro(stato="disdetto", notice_at=datetime(2027, 1, 1, tzinfo=UTC)), False),
        (_quadro(stato="generato", notice_at=None), False),
        (_quadro(stato="inviato", notice_at=None), False),
        (ContractDocument(kind="lettera", stato="firmato", notice_at=None), False),
    ],
)
def test_a_framework_is_active_while_signed_and_without_notice(
    document: ContractDocument, active: bool
) -> None:
    assert is_active(document) is active


def test_a_signature_is_dated_in_rome() -> None:
    """Review Focus 3: 23:30 UTC on 30 September is 1 October where rebase signs."""
    late = _quadro(signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    assert signed_on(late) == date(2026, 10, 1)
    assert signed_on(_quadro(signed_at=None)) is None


def test_the_read_model_computes_the_dates_only_for_an_active_framework() -> None:
    common = {
        "id": UUID("01a00000-0000-7000-8000-00000000000a"),
        "freelancer_id": UUID("01a00000-0000-7000-8000-00000000000b"),
        "created_by": UUID("01a00000-0000-7000-8000-00000000000c"),
        "created_at": datetime(2026, 9, 23, tzinfo=UTC),
        "match_id": None,
        "numero": None,
        "testo_bozza": False,
        "sent_at": None,
        "signed_pdf": None,
    }
    signed = _quadro(
        **common,
        text_version="0.0",
        stato="firmato",
        signed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        notice_at=None,
    )
    read = document_read(signed, date(2026, 12, 1), current_version="0.1")
    assert (read.attivo, read.rinnovo, read.ultimo_giorno_disdetta) == (
        True,
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    assert read.nuova_versione is True and read.ha_pdf_firmato is False
    generated = _quadro(**common, text_version="0.1", stato="generato", signed_at=None, notice_at=None)
    read = document_read(generated, date(2026, 12, 1), current_version="0.1")
    assert (read.attivo, read.rinnovo, read.ultimo_giorno_disdetta, read.nuova_versione) == (
        False,
        None,
        None,
        False,
    )


def test_letters_are_numbered_per_year_and_a_rollback_leaves_no_gap(clean: Session) -> None:
    assert next_letter_number(clean, 2030) == "2030-001"
    assert next_letter_number(clean, 2030) == "2030-002"
    clean.commit()
    assert next_letter_number(clean, 2030) == "2030-003"
    clean.rollback()
    assert next_letter_number(clean, 2030) == "2030-003"
    assert next_letter_number(clean, 2031) == "2031-001"
    clean.commit()


def test_two_letters_taken_at_once_get_two_numbers(hub_engine: Engine, clean: Session) -> None:
    """Review Focus 1: the second transaction waits on the first one's row and then takes
    the next number, instead of reading the same one or failing on the key."""
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    taken: list[str] = []

    def take() -> None:
        taken.append(next_letter_number(second, 2032))
        second.commit()

    try:
        assert next_letter_number(first, 2032) == "2032-001"
        worker = threading.Thread(target=take)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second number was taken while the first was still open"
        first.commit()
        worker.join(timeout=5)
        assert taken == ["2032-002"]
    finally:
        first.close()
        second.close()
```

`test_fiscal.py`:

```python
"""A freelancer's tax data: saved once, reused, audited by name only (REB-387)."""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerFiscal, User
from rebase_core.schemas import FreelancerCreate

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
MISSING = UUID("00000000-0000-7000-8000-000000000000")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in ("admin_actions", "freelancer_fiscal", "freelancers", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(session: Session) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email="ada@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _data(**change: str | None) -> FiscalData:
    payload: dict[str, str | None] = {
        "codice_fiscale": "LVLDAA85T50H501Z",
        "partita_iva": "01234567890",
        "domicilio": "Via Roma 1, Milano",
        "pec": None,
    }
    payload.update(change)
    return FiscalData(**payload)  # type: ignore[arg-type]


def test_tax_data_are_saved_once_and_updated_in_place(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    fiscal = FiscalService(clean)
    assert fiscal.get(card) is None
    saved = fiscal.save(card, _data(), admin_id)
    assert (saved.codice_fiscale, saved.partita_iva, saved.updated_by) == (
        "LVLDAA85T50H501Z",
        "01234567890",
        admin_id,
    )
    fiscal.save(card, _data(domicilio="Corso Buenos Aires 2, Milano", pec="ada@pec.it"), admin_id)
    read = fiscal.get(card)
    assert read is not None and (read.domicilio, read.pec) == ("Corso Buenos Aires 2, Milano", "ada@pec.it")
    assert clean.scalar(select(func.count()).select_from(FreelancerFiscal)) == 1


def test_the_audit_names_the_fields_that_changed_and_never_their_values(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    fiscal = FiscalService(clean)
    fiscal.save(card, _data(), admin_id)
    fiscal.save(card, _data(partita_iva="09876543210"), admin_id)
    fiscal.save(card, _data(partita_iva="09876543210"), admin_id)  # nothing moved
    trail = AdminActionService(clean).timeline("freelancer_fiscal", card)
    assert [action.kind for action in trail] == ["fiscal_updated", "fiscal_updated"]
    assert trail[0].payload == {"changed": ["partita_iva"]}
    assert trail[1].payload == {"changed": ["codice_fiscale", "partita_iva", "domicilio"]}
    assert "09876543210" not in str([action.payload for action in trail])


def test_an_unknown_or_deleted_card_has_no_tax_data(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    with pytest.raises(NotFound):
        FiscalService(clean).get(MISSING)
    FreelancerService(clean).soft_delete(card, admin_id)
    with pytest.raises(NotFound):
        FiscalService(clean).save(card, _data(), admin_id)
```

- [ ] **Step 3: Run the three files to see them fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_contract_schemas.py projects/hub/packages/core/tests/test_framework.py projects/hub/packages/core/tests/test_fiscal.py`
Expected: collection errors, `No module named 'rebase_core.contract_schemas'`.

- [ ] **Step 4: Add `InvalidState`** (append to `errors.py`)

```python
class InvalidState(DomainError):
    """An action the row's current state does not allow: cancelling a match that is no
    longer a draft, closing one that is not active. The API answers it with a 409, the
    status every `DomainError` without a mapping of its own already gets."""

    code = "invalid_state"
```

- [ ] **Step 5: Write `contract_schemas.py`**

```python
"""What the matches and contracts pages send and read (REB-387, phase 2).

Kept out of `schemas.py`, already the size of a chapter: everything here is one flow,
the admin's «Crea match» and «Match e contratti», and the MCP tools that read the same
rows. A letter's field names are the Markdown's own keys with underscores for hyphens
(`data_inizio` is `{{data-inizio}}`), so `LetteraFields.to_fields` is a rename and a
formatting step, nothing else. The company's `budget_giornaliero` has no field anywhere
here on purpose: what rebase agrees with the client never reaches a freelancer's
document (spec § 1h), and `test_contract_schemas.py` holds every model to that.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.contracts.fields import DAYS_LIMIT, DAYS_LIMIT_MONTH_END, Value, italian_date
from rebase_core.models import (
    AZIENDA_MAX_LENGTH,
    CLIENTE_PIVA_MAX_LENGTH,
    DOMICILIO_MAX_LENGTH,
    POSIZIONE_MAX_LENGTH,
    SEDE_MAX_LENGTH,
)
from rebase_core.schemas import PROGETTO_MAX_LENGTH, TARIFFA_MAX, TARIFFA_MIN, clean_multiline
from rebase_core.validation import SafeStr

# A person's codice fiscale is sixteen letters and digits; a ditta's is its eleven digits.
_CODICE_FISCALE = re.compile(r"[A-Z0-9]{16}|[0-9]{11}")
_PARTITA_IVA = re.compile(r"[0-9]{11}")
# Wide enough for the typed value with its spaces, which are dropped before the check.
_IDENTIFIER_INPUT_MAX_LENGTH = 40
LETTERA_TEXT_MAX_LENGTH = PROGETTO_MAX_LENGTH
PREAVVISO_MAX_DAYS = 365

# The letter's text fields an admin writes at step 4, in the Markdown's order.
LETTERA_TEXT_FIELDS = (
    "ruolo",
    "attivita",
    "risultati",
    "accettazione",
    "impegno",
    "periodo_verifica",
    "luogo",
    "coordinamento",
    "referente_cliente",
    "referente_rebase",
    "modalita",
    "unita",
    "lavoro_extra",
    "spese",
    "scadenze_fatturazione",
    "dati_personali",
    "dati_finalita",
    "dati_categorie",
    "dati_interessati",
    "dati_autorizzazione",
    "esclusiva",
    "portfolio",
    "assicurazione",
    "altre_condizioni",
    "rapporti_precedenti",
)

# The letter's fields the hub fills itself, never the admin at step 4: the number, the
# framework's date, the two parties, and the four signing fields.
LETTER_AUTO_FIELDS = frozenset(
    {
        "numero",
        "data-contratto-quadro",
        "rebase-ragione-sociale",
        "rebase-rappresentante",
        "professionista-nome",
        "professionista-piva",
        "cliente-ragione-sociale",
        "cliente-piva",
        "cliente-sede",
        "luogo-firma",
        "data-firma",
        "firma-rebase",
        "firma-professionista",
    }
)


def _one_line(value: str, what: str) -> str:
    cleaned = clean_multiline(value, what=what)
    if any(character in cleaned for character in "\n\r\t"):
        raise ValueError(f"{what} sta su una riga sola")
    return cleaned


def _compact(value: str) -> str:
    """An identifier as it was typed, its spaces gone and in capitals."""
    return "".join(value.split()).upper()


class FiscalData(BaseModel):
    """What step 2 of «Crea match» and «Match e contratti» save for a freelancer."""

    model_config = ConfigDict(extra="forbid")

    codice_fiscale: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    partita_iva: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    domicilio: SafeStr = Field(min_length=1, max_length=DOMICILIO_MAX_LENGTH)
    pec: EmailStr | None = None

    @field_validator("codice_fiscale", mode="after")
    @classmethod
    def _codice_fiscale(cls, value: str) -> str:
        compact = _compact(value)
        if not _CODICE_FISCALE.fullmatch(compact):
            raise ValueError("il codice fiscale ha 16 caratteri, o 11 cifre per una ditta")
        return compact

    @field_validator("partita_iva", mode="after")
    @classmethod
    def _partita_iva(cls, value: str) -> str:
        compact = _compact(value).removeprefix("IT")
        if not _PARTITA_IVA.fullmatch(compact):
            raise ValueError("la partita IVA ha 11 cifre")
        return compact

    @field_validator("domicilio", mode="after")
    @classmethod
    def _domicilio(cls, value: str) -> str:
        return _one_line(value, "il domicilio")


class FiscalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    freelancer_id: UUID
    codice_fiscale: str
    partita_iva: str
    domicilio: str
    pec: str | None
    updated_by: UUID
    updated_at: datetime


class ClienteData(BaseModel):
    """The client as the letter prints it (step 3)."""

    model_config = ConfigDict(extra="forbid")

    cliente_ragione_sociale: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    cliente_piva: SafeStr = Field(min_length=1, max_length=CLIENTE_PIVA_MAX_LENGTH)
    cliente_sede: SafeStr = Field(min_length=1, max_length=SEDE_MAX_LENGTH)

    @field_validator("cliente_ragione_sociale", "cliente_piva", "cliente_sede", mode="after")
    @classmethod
    def _line(cls, value: str) -> str:
        return _one_line(value, "un valore")


class ClienteDraft(BaseModel):
    """What the prefill suggests for step 3: any of the three may be unknown."""

    cliente_ragione_sociale: str | None = None
    cliente_piva: str | None = None
    cliente_sede: str | None = None


class LetteraDraft(BaseModel):
    """Every field of `lettera-di-incarico.md` an admin writes at step 4, all optional:
    the shape the prefill suggests. `LetteraFields` is the one a letter is written from."""

    model_config = ConfigDict(extra="forbid")

    ruolo: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    attivita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    risultati: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    accettazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    data_inizio: date | None = None
    data_fine: date | None = None
    impegno: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    periodo_verifica: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    luogo: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    coordinamento: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    referente_cliente: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    referente_rebase: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    modalita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    compenso: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    unita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    lavoro_extra: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    spese: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    giorni_pagamento: int | None = Field(default=None, ge=1, le=DAYS_LIMIT)
    fine_mese: bool | None = None
    scadenze_fatturazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    giorni_preavviso: int | None = Field(default=None, ge=1, le=PREAVVISO_MAX_DAYS)
    dati_personali: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_finalita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_categorie: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_interessati: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_autorizzazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    esclusiva: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    portfolio: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    assicurazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    altre_condizioni: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    rapporti_precedenti: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)

    @field_validator(*LETTERA_TEXT_FIELDS, mode="after")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        """A paragraph may span lines; control characters and a value of blanks may not.
        The web sends `null` for an empty box, which prints as a labelled blank line."""
        return None if value is None else clean_multiline(value, what="un valore")


class LetteraFields(LetteraDraft):
    """The letter as it is written: the role, the work, the start, the fee and the
    payment term are required; every other field may stay a blank line on the page."""

    ruolo: SafeStr = Field(min_length=1, max_length=POSIZIONE_MAX_LENGTH)
    attivita: SafeStr = Field(min_length=1, max_length=LETTERA_TEXT_MAX_LENGTH)
    data_inizio: date
    compenso: Decimal = Field(max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX)
    giorni_pagamento: int = Field(ge=1, le=DAYS_LIMIT)
    fine_mese: bool

    @model_validator(mode="after")
    def _dates_and_term(self) -> "LetteraFields":
        if self.data_fine is not None and self.data_fine < self.data_inizio:
            raise ValueError("la fine prevista viene prima dell'inizio")
        if self.fine_mese and self.giorni_pagamento > DAYS_LIMIT_MONTH_END:
            raise ValueError(
                "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)"
            )
        return self

    def to_fields(self) -> dict[str, Value]:
        """The Markdown's own keys and the values the page prints: a date the Italian way,
        the fee as the JSON number `checked` insists on, everything else as it is."""
        fields: dict[str, Value] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            key = name.replace("_", "-")
            if isinstance(value, date):
                fields[key] = italian_date(value)
            elif isinstance(value, Decimal):
                fields[key] = int(value) if value == value.to_integral_value() else float(value)
            else:
                fields[key] = value
        return fields


class MatchCreate(BaseModel):
    """Steps 1, 3 and 4 of «Crea match». The tax data of step 2 are saved by their own
    route when the admin leaves that step, and read back from `freelancer_fiscal`."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    cliente: ClienteData
    lettera: LetteraFields


class ContractDocumentRead(BaseModel):
    """A document as the pages and the MCP tools read it: never the PDF bytes and never
    `data`, which carries rebase's signer and the freelancer's tax identifiers."""

    id: UUID
    kind: str
    freelancer_id: UUID
    match_id: UUID | None
    numero: str | None
    text_version: str
    testo_bozza: bool
    stato: str
    created_at: datetime
    created_by: UUID
    sent_at: datetime | None
    signed_at: datetime | None
    notice_at: datetime | None
    ha_pdf_firmato: bool
    attivo: bool
    rinnovo: date | None
    ultimo_giorno_disdetta: date | None
    nuova_versione: bool


class MatchRead(BaseModel):
    id: UUID
    freelancer_id: UUID
    company_id: UUID
    nome_azienda: str
    figura_richiesta: str
    cliente_ragione_sociale: str
    cliente_piva: str
    cliente_sede: str
    stato: str
    created_at: datetime
    created_by: UUID
    cancelled_at: datetime | None
    updated_at: datetime
    lettera: ContractDocumentRead


class FreelancerContracts(BaseModel):
    """«Match e contratti»: the framework agreement at the top (the active one, else the
    newest not cancelled), every framework agreement newest first, the matches newest
    first, and the tax data the page edits."""

    freelancer_id: UUID
    quadro: ContractDocumentRead | None
    quadri: list[ContractDocumentRead]
    matches: list[MatchRead]
    fiscale: FiscalRead | None


class MatchPrefill(BaseModel):
    """What steps 2 to 4 start from. `quadro_necessario`: this match writes a framework
    agreement. `lettera_in_attesa`: the letter waits for a framework's signature."""

    fiscale: FiscalRead | None
    cliente: ClienteDraft
    lettera: LetteraDraft
    quadro_attivo: ContractDocumentRead | None
    quadro_necessario: bool
    lettera_in_attesa: bool


class ContractPdf(BaseModel):
    """A document's bytes and the name a browser saves them under."""

    filename: str
    content: bytes
```

- [ ] **Step 6: Write `framework.py`**

```python
"""The framework agreement's twelve months, and the letters' numbers (REB-387).

Article 9.1: the agreement lasts twelve months from its signature and renews itself
for twelve more unless either party gives notice thirty days before the end. For the
hub it is active from its signature until someone records a notice or a withdrawal
(spec § 1d); the next renewal and the last day for a notice are computed here and never
stored. Dates are Rome's, where rebase signs: a signature at 23:30 UTC on 30 September
is dated 1 October.
"""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import ContractDocumentRead
from rebase_core.models import ContractDocument, LetterCounter

ROME = ZoneInfo("Europe/Rome")
NOTICE_DAYS = 30
QUADRO = "quadro"


def rome_today() -> date:
    return datetime.now(ROME).date()


def anniversary(signed_on: date, year: int) -> date:
    """`signed_on` in `year`; 29 February falls on the 28th in a common year."""
    try:
        return signed_on.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def next_renewal(signed_on: date, today: date) -> date:
    """The first anniversary of the signature after `today`: on the anniversary itself
    the new twelve months have already started."""
    renewal = anniversary(signed_on, today.year)
    if renewal <= today:
        renewal = anniversary(signed_on, today.year + 1)
    return renewal


def last_notice_day(renewal: date) -> date:
    return renewal - timedelta(days=NOTICE_DAYS)


def signed_on(document: ContractDocument) -> date | None:
    return document.signed_at.astimezone(ROME).date() if document.signed_at is not None else None


def is_active(document: ContractDocument) -> bool:
    return document.kind == QUADRO and document.stato == "firmato" and document.notice_at is None


def active_framework(session: Session, freelancer_id: UUID) -> ContractDocument | None:
    return session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.kind == QUADRO,
            ContractDocument.freelancer_id == freelancer_id,
            ContractDocument.stato == "firmato",
            ContractDocument.notice_at.is_(None),
        )
        .order_by(ContractDocument.signed_at.desc(), ContractDocument.created_at.desc())
        .limit(1)
    ).first()


def pending_framework(session: Session, freelancer_id: UUID) -> ContractDocument | None:
    """A framework agreement written and not signed yet: the one out for signature
    (`inviato`, phase 3) when there is one, else the newest merely generated."""
    pending = list(
        session.scalars(
            select(ContractDocument)
            .where(
                ContractDocument.kind == QUADRO,
                ContractDocument.freelancer_id == freelancer_id,
                ContractDocument.stato.in_(("generato", "inviato")),
            )
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
    )
    for document in pending:
        if document.stato == "inviato":
            return document
    return pending[0] if pending else None


def next_letter_number(session: Session, year: int) -> str:
    """The next letter number of `year`, `YYYY-NNN`, taken inside the caller's
    transaction and never committed here: a generation that fails rolls it back, and a
    second transaction asking at the same moment waits on this row, then takes the next."""
    taken = session.execute(
        pg_insert(LetterCounter)
        .values(anno=year, ultimo=1)
        .on_conflict_do_update(
            index_elements=[LetterCounter.anno], set_={"ultimo": LetterCounter.ultimo + 1}
        )
        .returning(LetterCounter.ultimo)
    ).scalar_one()
    return f"{year}-{taken:03d}"


def document_read(
    document: ContractDocument, today: date, current_version: str
) -> ContractDocumentRead:
    active = is_active(document)
    signed = signed_on(document)
    renewal = next_renewal(signed, today) if active and signed is not None else None
    return ContractDocumentRead(
        id=document.id,
        kind=document.kind,
        freelancer_id=document.freelancer_id,
        match_id=document.match_id,
        numero=document.numero,
        text_version=document.text_version,
        testo_bozza=document.testo_bozza,
        stato=document.stato,
        created_at=document.created_at,
        created_by=document.created_by,
        sent_at=document.sent_at,
        signed_at=document.signed_at,
        notice_at=document.notice_at,
        ha_pdf_firmato=document.signed_pdf is not None,
        attivo=active,
        rinnovo=renewal,
        ultimo_giorno_disdetta=last_notice_day(renewal) if renewal is not None else None,
        nuova_versione=document.kind == QUADRO and document.text_version != current_version,
    )
```

- [ ] **Step 7: Write `fiscal.py`**

```python
"""A freelancer's tax data, as the two contracts print them (REB-387).

A table of its own rather than columns on `freelancers`, because PostHog's warehouse
syncs `freelancers` whole (spec § 2). Filled by an admin in the match flow the first
time, reused afterwards, editable from «Match e contratti». The audit entry names the
fields that changed and never their values: a codice fiscale copied into
`admin_actions` would be one more place to delete it from.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData, FiscalRead
from rebase_core.errors import NotFound
from rebase_core.models import Freelancer, FreelancerFiscal

ENTITY = "freelancer_fiscal"
FISCAL_FIELDS = ("codice_fiscale", "partita_iva", "domicilio", "pec")


class FiscalService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, freelancer_id: UUID) -> FiscalRead | None:
        self._require_card(freelancer_id)
        row = self._row(freelancer_id)
        return FiscalRead.model_validate(row) if row is not None else None

    def save(self, freelancer_id: UUID, data: FiscalData, admin_id: UUID) -> FiscalRead:
        """Writes or rewrites the one row of this card. Two first saves racing on one
        card: the unique index decides, and the loser writes again over the winner."""
        self._require_card(freelancer_id)
        try:
            return self._write(freelancer_id, data, admin_id)
        except IntegrityError:
            self.session.rollback()
            return self._write(freelancer_id, data, admin_id)

    def _write(self, freelancer_id: UUID, data: FiscalData, admin_id: UUID) -> FiscalRead:
        row = self._row(freelancer_id)
        before = {field: getattr(row, field) for field in FISCAL_FIELDS} if row is not None else {}
        if row is None:
            row = FreelancerFiscal(freelancer_id=freelancer_id)
            self.session.add(row)
        for field in FISCAL_FIELDS:
            setattr(row, field, getattr(data, field))
        row.updated_by = admin_id
        self.session.commit()
        changed = [field for field in FISCAL_FIELDS if before.get(field) != getattr(row, field)]
        if changed:
            AdminActionService(self.session).record(
                ENTITY, freelancer_id, "fiscal_updated", admin_id, {"changed": changed}
            )
        return FiscalRead.model_validate(row)

    def _row(self, freelancer_id: UUID) -> FreelancerFiscal | None:
        return self.session.scalar(
            select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == freelancer_id)
        )

    def _require_card(self, freelancer_id: UUID) -> None:
        card = self.session.get(Freelancer, freelancer_id)
        if card is None or card.deleted_at is not None:
            raise NotFound("freelancer", freelancer_id)
```

- [ ] **Step 8: Run the three files**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_contract_schemas.py projects/hub/packages/core/tests/test_framework.py projects/hub/packages/core/tests/test_fiscal.py`
Expected: all pass. `test_nothing_in_the_flow_can_carry_the_client_budget` and `test_the_form_covers_every_field_of_the_letter...` pass as written: if the second fails, the Markdown gained a field; add it to `LetteraDraft` or `LETTER_AUTO_FIELDS`, never loosen the test.

- [ ] **Step 9: Lint, types, commit**

Run: `uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`

```bash
git add projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/framework.py \
  projects/hub/packages/core/src/rebase_core/fiscal.py \
  projects/hub/packages/core/src/rebase_core/errors.py \
  projects/hub/packages/core/tests/test_contract_schemas.py \
  projects/hub/packages/core/tests/test_framework.py \
  projects/hub/packages/core/tests/test_fiscal.py
git commit -F - <<'EOF'
feat(hub): tax data, the framework's twelve months and the letter numbers

I add the schemas of the match flow, with nothing that could carry the
client's budget, and FiscalService, which keeps one row of tax data per
card and audits which fields changed without their values. framework.py
says when a framework agreement is active, when it renews and the last
day for a notice, in Rome's calendar, and takes letter numbers per year
inside the caller's transaction, so two at once get two numbers.

REB-N.
EOF
```

---

### Task 5: `MatchService`: prefill, preview, create, read, cancel, close

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/matches.py`
- Create: `projects/hub/packages/core/tests/fakes_contracts.py`
- Create: `projects/hub/packages/core/tests/test_matches.py`

**Interfaces:**
- Consumes: Task 1 (`Renderer`, `Rendered`, `company_defaults`, `text_path`, `text_version`, `ContractFailed`, `FIELD`, `Value`, `checked`, `merge_data`, `italian_date`), Task 3 models, Task 4 (`contract_schemas.*`, `framework.*`, `FiscalService`, `InvalidState`), `rebase_core.audit.{AdminActionService, utcnow}`.
- Produces:
  - `rebase_core.matches.MatchService(session: Session, renderer: Renderer | None = None, signer: Mapping[str, Value] | None = None, today: Callable[[], date] = rome_today)` with `prefill(freelancer_id: UUID, company_id: UUID) -> MatchPrefill`, `preview(freelancer_id: UUID, data: MatchCreate, kind: str) -> ContractPdf` (`kind` is `"lettera"` or `"quadro"`), `create(freelancer_id: UUID, data: MatchCreate, admin_id: UUID) -> MatchRead`, `get(match_id: UUID) -> MatchRead`, `for_freelancer(freelancer_id: UUID) -> FreelancerContracts`, `cancel(match_id: UUID, admin_id: UUID) -> MatchRead`, `close(match_id: UUID, admin_id: UUID) -> MatchRead`, `document_pdf(document_id: UUID, *, signed: bool = False) -> ContractPdf`. Constants `ENTITY = "match"`, `QUADRO`, `LETTERA`, `DOCUMENT_BY_KIND = {"quadro": "contratto-quadro", "lettera": "lettera-di-incarico"}`, `SIGNED_ELECTRONICALLY = "firmato elettronicamente"`, `PEC_MISSING = "non indicata"`, `issued_by_rebase(day: date) -> str`, `luogo_suggestion(remoto: str, giorni_presenza: int | None) -> str`.
  - Test helpers (importable from any hub test root, `packages/core/tests` is on `pythonpath`): `fakes_contracts.FakeRenderer` (dataclass; `calls: list[tuple[str, dict[str, Value]]]`, `draft: bool = True`) and `fakes_contracts.FailingRenderer(document: str | None = None)` which raises `ContractFailed("pandoc is not on PATH")` for that document (or every one).

- [ ] **Step 1: Write the fakes** (`fakes_contracts.py`)

```python
"""Renderers that never run pandoc or Typst: the service and API tests' seam, the way
`RecordingSender` is the mail's. `FakeRenderer` still runs the checks the real one runs
before typesetting (`checked`), so a fee or a payment term the page could not print
fails here exactly as it would in production, and it reports the text's real version
and the fields it would leave blank."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from rebase_core.contracts.fields import FIELD, ContractFailed, Value, checked
from rebase_core.contracts.render import Rendered, text_path, text_version


@dataclass
class FakeRenderer:
    calls: list[tuple[str, dict[str, Value]]] = field(default_factory=list)
    draft: bool = True

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        filled = checked(dict(data))
        self.calls.append((document, dict(data)))
        asked = dict.fromkeys(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        blank = [key for key in asked if filled.get(key) in (None, "")]
        return Rendered(
            pdf=b"%PDF-1.7 fake " + document.encode(),
            blank=blank,
            version=text_version(document),
            draft=self.draft,
        )


@dataclass
class FailingRenderer:
    """Fails like a machine without pandoc, on `document` or on every one."""

    document: str | None = None

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        if self.document is None or document == self.document:
            raise ContractFailed("pandoc is not on PATH")
        return FakeRenderer().render(document, data)
```

- [ ] **Step 2: Write the failing service tests** (`test_matches.py`)

```python
"""REB-387 phase 2: a match written from a card and a request, and the documents it
generates, never sent. Every test hands `FakeRenderer`: the real typesetting is
`test_contract_render.py`'s."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import ClienteData, FiscalData, LetteraFields, MatchCreate
from rebase_core.contracts.fields import FIELD, TERM, ContractFailed, Value
from rebase_core.contracts.render import Renderer, text_path
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Freelancer, Match, User
from rebase_core.schemas import CompanyCreate, FreelancerCreate, StatusChange

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
TODAY = date(2026, 9, 23)
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


def _card(session: Session) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email="ada@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(session: Session, **change: object) -> UUID:
    """A request whose budget is a number that must appear nowhere in the flow."""
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Le API del prodotto, per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("777.77"),
        "remoto": "ibrido",
        "giorni_presenza": 2,
        "numero_risorse": 1,
    }
    payload.update(change)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def _fiscal(session: Session, freelancer_id: UUID, admin_id: UUID) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio="Via Roma 1, Milano"
        ),
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


def _service(
    session: Session, renderer: Renderer | None = None, today: date = TODAY
) -> MatchService:
    return MatchService(session, renderer or FakeRenderer(), SIGNER, today=lambda: today)


def _framework(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    *,
    stato: str = "firmato",
    signed_at: datetime | None = datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    notice_at: datetime | None = None,
    text_version: str = "0.1",
) -> ContractDocument:
    """A framework agreement as phase 3 will leave one: only a signature makes these."""
    document = ContractDocument(
        kind="quadro",
        freelancer_id=freelancer_id,
        text_version=text_version,
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato=stato,
        signed_at=signed_at,
        notice_at=notice_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


def _documents(session: Session, freelancer_id: UUID, kind: str | None = None) -> list[ContractDocument]:
    stmt = select(ContractDocument).where(ContractDocument.freelancer_id == freelancer_id)
    if kind is not None:
        stmt = stmt.where(ContractDocument.kind == kind)
    return list(session.scalars(stmt.order_by(ContractDocument.created_at, ContractDocument.id)))


def test_the_first_match_writes_the_framework_agreement_and_a_letter_that_waits_for_it(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)

    assert match.stato == "bozza"
    assert (match.lettera.numero, match.lettera.stato) == ("2026-001", "in_attesa")
    quadro, lettera = _documents(clean, freelancer_id)
    assert (quadro.kind, quadro.stato, quadro.match_id, quadro.numero) == ("quadro", "generato", None, None)
    assert (quadro.text_version, quadro.testo_bozza) == ("0.1", True)
    assert (lettera.kind, lettera.match_id) == ("lettera", match.id)
    assert lettera.data["data-contratto-quadro"] is None
    assert [document for document, _ in renderer.calls] == ["contratto-quadro", "lettera-di-incarico"]
    assert [a.kind for a in AdminActionService(clean).timeline("match", match.id)] == ["match_created"]


def test_the_framework_prints_the_freelancer_and_rebases_signer_and_leaves_the_signing_blanks(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    (quadro_doc, quadro), (letter_doc, letter) = renderer.calls
    assert quadro["professionista-nome"] == "Ada Lovelace"
    assert quadro["professionista-cf"] == "LVLDAA85T50H501Z"
    assert quadro["professionista-email"] == "ada@studio.it"
    assert quadro["professionista-pec"] == "non indicata"
    assert quadro["rebase-rappresentante"] == "Nome Cognome"
    assert quadro["luogo-firma"] == "firmato elettronicamente"
    assert quadro["firma-rebase"] == "Documento emesso da rebase il 23 settembre 2026"
    assert quadro["data-firma"] is None and quadro["firma-professionista"] is None
    for document, data in ((quadro_doc, quadro), (letter_doc, letter)):
        asked = set(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        assert asked - {TERM} <= set(data), document
    assert letter["professionista-piva"] == "01234567890"
    assert letter["cliente-ragione-sociale"] == "ACME S.r.l."
    assert letter["compenso"] == 450


def test_with_an_active_framework_only_the_letter_is_written_and_it_cites_the_signature_date(
    clean: Session,
) -> None:
    """Review Focus 3: 23:30 UTC on 30 September is 1 October in Rome."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "generato"
    assert [document for document, _ in renderer.calls] == ["lettera-di-incarico"]
    assert renderer.calls[0][1]["data-contratto-quadro"] == "1° ottobre 2026"
    assert len(_documents(clean, freelancer_id, "quadro")) == 1


@pytest.mark.parametrize("stato", ["firmato", "disdetto"])
def test_a_framework_with_a_notice_recorded_is_no_longer_active(clean: Session, stato: str) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, stato=stato, notice_at=datetime(2026, 11, 1, tzinfo=UTC))
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "in_attesa"
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == [stato, "generato"]


def test_an_unsent_framework_from_an_earlier_draft_is_replaced_not_duplicated(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id), admin_id)
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["annullato", "generato"]


def test_a_framework_already_out_for_signature_is_waited_for_not_replaced(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, stato="inviato", signed_at=None)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "in_attesa"
    assert [document for document, _ in renderer.calls] == ["lettera-di-incarico"]


def test_letters_are_numbered_per_year_in_the_order_they_are_written(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    numbers = [
        _service(clean, today=day).create(freelancer_id, _body(company_id), admin_id).lettera.numero
        for day in (date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 1))
    ]
    assert numbers == ["2026-001", "2026-002", "2027-001"]


def test_a_render_that_fails_takes_no_number_and_leaves_nothing_behind(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(ContractFailed):
        _service(clean, FailingRenderer(document="lettera-di-incarico")).create(
            freelancer_id, _body(company_id), admin_id
        )
    assert clean.scalar(select(func.count()).select_from(Match)) == 1
    # The earlier framework was not replaced by a generation that failed.
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    assert _service(clean).create(freelancer_id, _body(company_id), admin_id).lettera.numero == "2026-002"


def test_the_client_budget_reaches_neither_the_prefill_nor_the_letter(clean: Session) -> None:
    # The whole amount with its dot: a bare "777" could turn up inside a UUID's hex or a
    # timestamp's microseconds and fail this test for nothing.
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    assert "777.77" not in service.prefill(freelancer_id, company_id).model_dump_json()
    match = service.create(freelancer_id, _body(company_id), admin_id)
    letter = clean.get(ContractDocument, match.lettera.id)
    assert letter is not None
    assert not any("777.77" in str(value) for value in letter.data.values())
    assert not any("budget" in key for key in letter.data)


def test_the_prefill_suggests_from_the_request_the_card_and_rebases_defaults(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    prefill = _service(clean).prefill(freelancer_id, company_id)
    lettera = prefill.lettera
    assert (lettera.ruolo, lettera.attivita) == ("Backend developer", "Le API del prodotto, per tre mesi.")
    assert (lettera.data_inizio, lettera.impegno) == (date(2026, 10, 1), "3 mesi")
    assert lettera.luogo == "in parte da remoto, con 2 giornate a settimana presso il Cliente"
    assert lettera.referente_cliente == "Wile E."
    assert (lettera.modalita, lettera.unita) == ("a giornata", "a giornata")
    assert lettera.compenso == Decimal("450.00")
    assert (lettera.giorni_pagamento, lettera.fine_mese) == (30, True)
    assert prefill.cliente.cliente_ragione_sociale == "ACME Srl"
    assert prefill.cliente.cliente_piva is None
    assert prefill.fiscale is not None and prefill.fiscale.partita_iva == "01234567890"
    assert (prefill.quadro_attivo, prefill.quadro_necessario, prefill.lettera_in_attesa) == (None, True, True)


def test_a_card_without_a_day_rate_prefills_no_fee_and_the_letter_needs_one(clean: Session) -> None:
    """Review Focus 2: a card drafted from a signup has no rate yet."""
    admin_id, freelancer_id, company_id = _setup(clean)
    card = clean.get(Freelancer, freelancer_id)
    assert card is not None
    card.tariffa_giornaliera = None
    clean.commit()
    assert _service(clean).prefill(freelancer_id, company_id).lettera.compenso is None
    with pytest.raises(PydanticValidationError, match="compenso"):
        LetteraFields(
            ruolo="Backend developer",
            attivita="API",
            data_inizio=date(2026, 10, 1),
            giorni_pagamento=30,
            fine_mese=True,
        )  # type: ignore[call-arg]


def test_the_client_data_come_back_from_the_same_company_users_last_match(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    # The same referente files a second request: another row, the same client.
    second = _request(clean, nome_azienda="ACME", figura_richiesta="Frontend developer")
    cliente = _service(clean).prefill(freelancer_id, second).cliente
    assert (cliente.cliente_ragione_sociale, cliente.cliente_piva, cliente.cliente_sede) == (
        "ACME S.r.l.",
        "01234567890",
        "Milano",
    )


def test_a_closed_request_can_be_neither_previewed_nor_matched(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).set_status(company_id, StatusChange(stato="chiuso"))
    service = _service(clean)
    for call in (
        lambda: service.create(freelancer_id, _body(company_id), admin_id),
        lambda: service.preview(freelancer_id, _body(company_id), "lettera"),
    ):
        with pytest.raises(ValidationFailed) as refused:
            call()
        assert refused.value.details["field"] == "company_id"


def test_a_match_needs_the_tax_data_saved_first(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert refused.value.details["field"] == "fiscale"


def test_a_deleted_card_or_request_is_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).soft_delete(company_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).prefill(freelancer_id, company_id)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).for_freelancer(freelancer_id)


def test_cancelling_a_draft_cancels_its_letter_and_leaves_the_framework(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    cancelled = service.cancel(match.id, admin_id)
    assert (cancelled.stato, cancelled.lettera.stato) == ("annullato", "annullato")
    assert cancelled.cancelled_at is not None
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    with pytest.raises(InvalidState):
        service.cancel(match.id, admin_id)
    kinds = [a.kind for a in AdminActionService(clean).timeline("match", match.id)]
    assert kinds == ["match_cancelled", "match_created"]


def test_only_an_active_match_can_be_closed(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(InvalidState):
        service.close(match.id, admin_id)
    row = clean.get(Match, match.id)
    assert row is not None
    row.stato = "attivo"  # only a signed letter gets a match here (phase 3)
    clean.commit()
    assert service.close(match.id, admin_id).stato == "concluso"


def test_the_contracts_page_reads_the_active_framework_its_dates_and_the_matches_newest_first(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, text_version="0.0")
    service = _service(clean, today=date(2026, 12, 1))
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.for_freelancer(freelancer_id)
    assert page.quadro is not None and page.quadro.attivo
    assert (page.quadro.rinnovo, page.quadro.ultimo_giorno_disdetta) == (date(2027, 10, 1), date(2027, 9, 1))
    assert page.quadro.nuova_versione is True
    assert [m.id for m in page.matches] == [second.id, first.id]
    assert page.fiscale is not None


def test_a_match_stays_on_the_page_after_its_request_is_deleted(clean: Session) -> None:
    """Review Focus 4: a soft-deleted request still names the match that came from it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)
    assert [(m.id, m.nome_azienda) for m in service.for_freelancer(freelancer_id).matches] == [
        (match.id, "ACME Srl")
    ]
    assert service.get(match.id).nome_azienda == "ACME Srl"


def test_a_preview_renders_without_saving_numbering_or_replacing_anything(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    service = _service(clean, renderer)
    service.create(freelancer_id, _body(company_id), admin_id)
    letter = service.preview(freelancer_id, _body(company_id), "lettera")
    assert letter.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    service.preview(freelancer_id, _body(company_id), "quadro")
    assert renderer.calls[-1][0] == "contratto-quadro"
    assert clean.scalar(select(func.count()).select_from(Match)) == 1
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    assert service.create(freelancer_id, _body(company_id), admin_id).lettera.numero == "2026-002"


def test_a_document_downloads_as_its_own_pdf_and_a_missing_signed_copy_is_not_found(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    pdf = service.document_pdf(match.lettera.id)
    assert (pdf.filename, pdf.content[:5]) == ("lettera-di-incarico-2026-001.pdf", b"%PDF-")
    quadro = _documents(clean, freelancer_id, "quadro")[0]
    assert service.document_pdf(quadro.id).filename == "contratto-quadro-v0.1.pdf"
    with pytest.raises(NotFound):
        service.document_pdf(match.lettera.id, signed=True)
```

- [ ] **Step 3: Run it to see it fail**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_matches.py`
Expected: collection error, `No module named 'rebase_core.matches'`.

- [ ] **Step 4: Write `matches.py`**

```python
"""Matches: a freelancer card paired with a company request, and the contracts they
write (REB-387, phase 2: generate, never send).

`create` writes in one transaction the match (`bozza`), the letter of engagement with
the year's next number, and the framework agreement when the freelancer has none
active. A letter whose framework agreement is not signed yet waits for it (`in_attesa`,
spec § 1e) and prints its date as a blank line until phase 3 regenerates it on the
signature. A framework agreement generated for an earlier draft and never sent is
replaced, so the newest tax data win; one already out for signature (`inviato`, phase 3)
is waited for instead. A render that fails rolls everything back, the number included.
`preview` renders the same documents and writes nothing: step 5 of «Crea match».

The company's `budget_giornaliero` is read nowhere in this module: what rebase agrees
with the client never reaches a freelancer's document (spec § 1h).
"""

from collections.abc import Callable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.contract_schemas import (
    ClienteDraft,
    ContractPdf,
    FreelancerContracts,
    LetteraDraft,
    MatchCreate,
    MatchPrefill,
    MatchRead,
)
from rebase_core.contracts.fields import (
    DAYS,
    MONTH_END,
    ContractFailed,
    Value,
    italian_date,
    merge_data,
)
from rebase_core.contracts.render import Renderer, company_defaults, text_version
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.framework import (
    active_framework,
    document_read,
    next_letter_number,
    pending_framework,
    rome_today,
    signed_on,
)
from rebase_core.models import Company, ContractDocument, Freelancer, FreelancerFiscal, Match, User

ENTITY = "match"
QUADRO, LETTERA = "quadro", "lettera"
DOCUMENT_BY_KIND = {QUADRO: "contratto-quadro", LETTERA: "lettera-di-incarico"}
SIGNED_ELECTRONICALLY = "firmato elettronicamente"
PEC_MISSING = "non indicata"
DAY_RATE = "a giornata"


def issued_by_rebase(day: date) -> str:
    """What rebase's signature blank prints, since rebase does not sign (spec § 1c)."""
    return f"Documento emesso da rebase il {italian_date(day)}"


def luogo_suggestion(remoto: str, giorni_presenza: int | None) -> str:
    if remoto == "ibrido" and giorni_presenza:
        giornate = "1 giornata" if giorni_presenza == 1 else f"{giorni_presenza} giornate"
        return f"in parte da remoto, con {giornate} a settimana presso il Cliente"
    if remoto == "in_sede":
        return "presso la sede del Cliente"
    return "da remoto"


def _full_name(user: User) -> str:
    return f"{user.nome} {user.cognome}".strip()


class MatchService:
    def __init__(
        self,
        session: Session,
        renderer: Renderer | None = None,
        signer: Mapping[str, Value] | None = None,
        today: Callable[[], date] = rome_today,
    ) -> None:
        """`renderer` is needed only to write or preview a document; the reads never
        typeset anything. `signer` is `REBASE_SIGNER_JSON` as `signer_data` read it."""
        self.session = session
        self.renderer = renderer
        self.signer: Mapping[str, Value] = signer or {}
        self.today = today

    # ---- reads -------------------------------------------------------------------------

    def get(self, match_id: UUID) -> MatchRead:
        row = self.session.execute(
            select(Match, Company).join(Company, Company.id == Match.company_id).where(Match.id == match_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, match_id)
        letter = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.match_id == match_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            .limit(1)
        ).first()
        if letter is None:
            raise NotFound("lettera", match_id)
        return self._match_read(row[0], row[1], letter)

    def for_freelancer(self, freelancer_id: UUID) -> FreelancerContracts:
        self._freelancer(freelancer_id)
        today, current = self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])
        frameworks = self.session.scalars(
            select(ContractDocument)
            .where(ContractDocument.kind == QUADRO, ContractDocument.freelancer_id == freelancer_id)
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
        quadri = [document_read(document, today, current) for document in frameworks]
        shown = next((q for q in quadri if q.attivo), None) or next(
            (q for q in quadri if q.stato != "annullato"), None
        )
        rows = self.session.execute(
            select(Match, Company)
            .join(Company, Company.id == Match.company_id)
            .where(Match.freelancer_id == freelancer_id)
            .order_by(Match.created_at.desc(), Match.id.desc())
        ).all()
        letters: dict[UUID, ContractDocument] = {}
        if rows:
            for document in self.session.scalars(
                select(ContractDocument)
                .where(ContractDocument.match_id.in_([row[0].id for row in rows]))
                .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            ):
                if document.match_id is not None:
                    letters.setdefault(document.match_id, document)
        return FreelancerContracts(
            freelancer_id=freelancer_id,
            quadro=shown,
            quadri=quadri,
            matches=[
                self._match_read(match, company, letters[match.id])
                for match, company in rows
                if match.id in letters
            ],
            fiscale=FiscalService(self.session).get(freelancer_id),
        )

    def prefill(self, freelancer_id: UUID, company_id: UUID) -> MatchPrefill:
        """Steps 2 to 4 as the hub can fill them: the saved tax data, the client as the
        same company user's last match named it (else the request's company name), and
        the letter from the request, the card and `rebase.json`."""
        freelancer, _user = self._freelancer(freelancer_id)
        company, referente = self._company(company_id)
        previous = self.session.scalars(
            select(Match)
            .join(Company, Company.id == Match.company_id)
            .where(Company.user_id == company.user_id)
            .order_by(Match.created_at.desc(), Match.id.desc())
            .limit(1)
        ).first()
        cliente = (
            ClienteDraft(
                cliente_ragione_sociale=previous.cliente_ragione_sociale,
                cliente_piva=previous.cliente_piva,
                cliente_sede=previous.cliente_sede,
            )
            if previous is not None
            else ClienteDraft(cliente_ragione_sociale=company.nome_azienda)
        )
        defaults = company_defaults()
        days, month_end = defaults.get(DAYS), defaults.get(MONTH_END)
        lettera = LetteraDraft(
            ruolo=company.figura_richiesta,
            attivita=company.progetto,
            data_inizio=company.periodo_da,
            impegno=company.durata,
            luogo=luogo_suggestion(company.remoto, company.giorni_presenza),
            referente_cliente=_full_name(referente) or None,
            modalita=DAY_RATE,
            unita=DAY_RATE,
            compenso=freelancer.tariffa_giornaliera,
            giorni_pagamento=days if isinstance(days, int) and not isinstance(days, bool) else None,
            fine_mese=month_end if isinstance(month_end, bool) else None,
        )
        active = active_framework(self.session, freelancer.id)
        pending = pending_framework(self.session, freelancer.id)
        today = self.today()
        return MatchPrefill(
            fiscale=FiscalService(self.session).get(freelancer.id),
            cliente=cliente,
            lettera=lettera,
            quadro_attivo=(
                document_read(active, today, text_version(DOCUMENT_BY_KIND[QUADRO]))
                if active is not None
                else None
            ),
            quadro_necessario=active is None and (pending is None or pending.stato != "inviato"),
            lettera_in_attesa=active is None,
        )

    def document_pdf(self, document_id: UUID, *, signed: bool = False) -> ContractPdf:
        document = self.session.get(ContractDocument, document_id)
        if document is None:
            raise NotFound("documento", document_id)
        content = document.signed_pdf if signed else document.pdf
        if content is None:
            raise NotFound("documento firmato", document_id)
        base = (
            f"lettera-di-incarico-{document.numero}"
            if document.kind == LETTERA
            else f"contratto-quadro-v{document.text_version}"
        )
        return ContractPdf(filename=f"{base}{'-firmato' if signed else ''}.pdf", content=content)

    # ---- writes ------------------------------------------------------------------------

    def preview(self, freelancer_id: UUID, data: MatchCreate, kind: str) -> ContractPdf:
        """One document as `create` would write it, with no number, and nothing saved."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        self._matchable_company(data.company_id)
        fiscal = self._fiscal(freelancer.id)
        today = self.today()
        if kind == QUADRO:
            rendered = renderer.render(DOCUMENT_BY_KIND[QUADRO], self._quadro_data(user, fiscal, today))
            return ContractPdf(filename="anteprima-contratto-quadro.pdf", content=rendered.pdf)
        if kind == LETTERA:
            active = active_framework(self.session, freelancer.id)
            data_fields = self._lettera_data(user, fiscal, data, None, active, today)
            rendered = renderer.render(DOCUMENT_BY_KIND[LETTERA], data_fields)
            return ContractPdf(filename="anteprima-lettera-di-incarico.pdf", content=rendered.pdf)
        raise ValidationFailed(ENTITY, "documento", "uno fra lettera e quadro")

    def create(self, freelancer_id: UUID, data: MatchCreate, admin_id: UUID) -> MatchRead:
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        company, _referente = self._matchable_company(data.company_id)
        fiscal = self._fiscal(freelancer.id)
        today = self.today()
        try:
            active = active_framework(self.session, freelancer.id)
            documents: list[ContractDocument] = []
            if active is None:
                pending = pending_framework(self.session, freelancer.id)
                if pending is None or pending.stato != "inviato":
                    for stale in list(
                        self.session.scalars(
                            select(ContractDocument).where(
                                ContractDocument.kind == QUADRO,
                                ContractDocument.freelancer_id == freelancer.id,
                                ContractDocument.stato == "generato",
                            )
                        )
                    ):
                        stale.stato = "annullato"
                    documents.append(
                        self._document(
                            renderer,
                            QUADRO,
                            self._quadro_data(user, fiscal, today),
                            freelancer.id,
                            None,
                            None,
                            "generato",
                            admin_id,
                        )
                    )
            match = Match(
                freelancer_id=freelancer.id,
                company_id=company.id,
                cliente_ragione_sociale=data.cliente.cliente_ragione_sociale,
                cliente_piva=data.cliente.cliente_piva,
                cliente_sede=data.cliente.cliente_sede,
                stato="bozza",
                created_by=admin_id,
            )
            self.session.add(match)
            self.session.flush()
            numero = next_letter_number(self.session, today.year)
            documents.append(
                self._document(
                    renderer,
                    LETTERA,
                    self._lettera_data(user, fiscal, data, numero, active, today),
                    freelancer.id,
                    match.id,
                    numero,
                    "generato" if active is not None else "in_attesa",
                    admin_id,
                )
            )
            self.session.add_all(documents)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        AdminActionService(self.session).record(
            ENTITY,
            match.id,
            "match_created",
            admin_id,
            {"company_id": company.id, "numero": numero, "documenti": [d.id for d in documents]},
        )
        return self.get(match.id)

    def cancel(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        """A draft and its letter become `annullato`; the framework agreement, which is
        the freelancer's and not the match's, stays as it is. Phase 3 extends this to a
        match in signature, which also has an envelope to cancel."""
        match = self._match(match_id)
        if match.stato != "bozza":
            raise InvalidState(
                f"Si annulla solo un match in bozza: questo è {match.stato}.", stato=match.stato
            )
        letters = list(
            self.session.scalars(
                select(ContractDocument).where(
                    ContractDocument.match_id == match.id,
                    ContractDocument.stato.in_(("generato", "in_attesa")),
                )
            )
        )
        match.stato = "annullato"
        match.cancelled_at = utcnow()
        for letter in letters:
            letter.stato = "annullato"
        self.session.commit()
        AdminActionService(self.session).record(
            ENTITY, match.id, "match_cancelled", admin_id, {"documenti": [d.id for d in letters]}
        )
        return self.get(match.id)

    def close(self, match_id: UUID, admin_id: UUID) -> MatchRead:
        match = self._match(match_id)
        if match.stato != "attivo":
            raise InvalidState(
                f"Si chiude solo un match attivo: questo è {match.stato}.", stato=match.stato
            )
        match.stato = "concluso"
        self.session.commit()
        AdminActionService(self.session).record(ENTITY, match.id, "match_closed", admin_id, {})
        return self.get(match.id)

    # ---- the documents' data -----------------------------------------------------------

    def _rebase_fields(self) -> dict[str, Value]:
        return merge_data(company_defaults(), self.signer)

    def _signing_fields(self, today: date) -> dict[str, Value]:
        """The date and the freelancer's signature stay blank for the signing site; rebase,
        which does not sign, prints who issued the document and when (spec § 5)."""
        return {
            "luogo-firma": SIGNED_ELECTRONICALLY,
            "data-firma": None,
            "firma-rebase": issued_by_rebase(today),
            "firma-professionista": None,
        }

    def _quadro_data(self, user: User, fiscal: FreelancerFiscal, today: date) -> dict[str, Value]:
        return {
            **self._rebase_fields(),
            "professionista-nome": _full_name(user),
            "professionista-cf": fiscal.codice_fiscale,
            "professionista-piva": fiscal.partita_iva,
            "professionista-domicilio": fiscal.domicilio,
            "professionista-email": user.email,
            "professionista-pec": fiscal.pec or PEC_MISSING,
            **self._signing_fields(today),
        }

    def _lettera_data(
        self,
        user: User,
        fiscal: FreelancerFiscal,
        data: MatchCreate,
        numero: str | None,
        active: ContractDocument | None,
        today: date,
    ) -> dict[str, Value]:
        signed = signed_on(active) if active is not None else None
        return {
            **self._rebase_fields(),
            **data.lettera.to_fields(),
            "numero": numero,
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": _full_name(user),
            "professionista-piva": fiscal.partita_iva,
            "cliente-ragione-sociale": data.cliente.cliente_ragione_sociale,
            "cliente-piva": data.cliente.cliente_piva,
            "cliente-sede": data.cliente.cliente_sede,
            **self._signing_fields(today),
        }

    def _document(
        self,
        renderer: Renderer,
        kind: str,
        data: dict[str, Value],
        freelancer_id: UUID,
        match_id: UUID | None,
        numero: str | None,
        stato: str,
        admin_id: UUID,
    ) -> ContractDocument:
        rendered = renderer.render(DOCUMENT_BY_KIND[kind], data)
        return ContractDocument(
            kind=kind,
            freelancer_id=freelancer_id,
            match_id=match_id,
            numero=numero,
            text_version=rendered.version,
            testo_bozza=rendered.draft,
            data=dict(data),
            pdf=rendered.pdf,
            stato=stato,
            created_by=admin_id,
        )

    # ---- lookups -----------------------------------------------------------------------

    def _match_read(self, match: Match, company: Company, letter: ContractDocument) -> MatchRead:
        return MatchRead(
            id=match.id,
            freelancer_id=match.freelancer_id,
            company_id=match.company_id,
            nome_azienda=company.nome_azienda,
            figura_richiesta=company.figura_richiesta,
            cliente_ragione_sociale=match.cliente_ragione_sociale,
            cliente_piva=match.cliente_piva,
            cliente_sede=match.cliente_sede,
            stato=match.stato,
            created_at=match.created_at,
            created_by=match.created_by,
            cancelled_at=match.cancelled_at,
            updated_at=match.updated_at,
            lettera=document_read(letter, self.today(), text_version(DOCUMENT_BY_KIND[QUADRO])),
        )

    def _renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to MatchService")
        return self.renderer

    def _freelancer(self, freelancer_id: UUID) -> tuple[Freelancer, User]:
        row = self.session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .where(Freelancer.id == freelancer_id)
        ).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("freelancer", freelancer_id)
        return row[0], row[1]

    def _company(self, company_id: UUID) -> tuple[Company, User]:
        row = self.session.execute(
            select(Company, User).join(User, User.id == Company.user_id).where(Company.id == company_id)
        ).first()
        if row is None or row[0].deleted_at is not None:
            raise NotFound("company", company_id)
        return row[0], row[1]

    def _matchable_company(self, company_id: UUID) -> tuple[Company, User]:
        company, referente = self._company(company_id)
        if company.stato == "chiuso":
            raise ValidationFailed(ENTITY, "company_id", "la richiesta è chiusa: riaprila prima")
        return company, referente

    def _fiscal(self, freelancer_id: UUID) -> FreelancerFiscal:
        row = self.session.scalar(
            select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == freelancer_id)
        )
        if row is None:
            raise ValidationFailed(ENTITY, "fiscale", "mancano i dati fiscali del freelance")
        return row

    def _match(self, match_id: UUID) -> Match:
        match = self.session.get(Match, match_id)
        if match is None:
            raise NotFound(ENTITY, match_id)
        return match
```

- [ ] **Step 5: Run the service tests**

Run: `uv run pytest -q projects/hub/packages/core/tests/test_matches.py`
Expected: all pass.

- [ ] **Step 6: The whole hub suite, lint, types**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`
Expected: green. Other modules' fixtures still delete `freelancers`, `companies` and `users` cleanly because every test here deletes its own rows first.

- [ ] **Step 7: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/matches.py \
  projects/hub/packages/core/tests/fakes_contracts.py \
  projects/hub/packages/core/tests/test_matches.py
git commit -F - <<'EOF'
feat(hub): a match writes its letter, and the framework agreement when none is active

I add MatchService. It prefills the flow from the request, the card and
rebase.json, never from the client's budget; it previews a document
without saving or numbering it; and it creates the draft match, the
numbered letter and, when the freelancer has no active framework
agreement, a fresh one the letter waits for, all in one transaction a
failed render rolls back. It reads a freelancer's contracts for the page,
cancels a draft and closes an active match. The services are tested with
FakeRenderer, which runs the same checks without pandoc or Typst.

REB-N.
EOF
```

---

### Task 6: Admin API routes, PDF downloads and two read-only MCP tools

**Files:**
- Create: `projects/hub/apps/api/src/rebase_api/routers/matches.py`
- Modify: `projects/hub/apps/api/src/rebase_api/main.py:9,22-42,48-54`, `deps.py` (append `get_renderer`, `RendererDep`), `downloads.py` (append `pdf_response`)
- Create: `projects/hub/apps/api/tests/test_matches_api.py`
- Modify: `projects/hub/apps/mcp/src/rebase_mcp/server.py` (imports, two tools before `_run`)
- Create: `projects/hub/apps/mcp/tests/test_match_tools.py`
- Modify: `projects/hub/apps/mcp/tests/test_tools.py:141-172` (tool names)

**Interfaces:**
- Consumes: Task 5 `MatchService` (all methods), Task 4 `FiscalService`, `contract_schemas.{FiscalData, FiscalRead, FreelancerContracts, MatchCreate, MatchPrefill, MatchRead}`, Task 2 `signer_data`, Task 1 `ContractRenderer`, `Renderer`, `ContractFailed`; test fakes from `fakes_contracts`.
- Produces (HTTP, all behind the admin cookie, 401 without it):
  - `GET /api/hub/freelancers/{freelancer_id}/fiscal` → `FiscalRead | null`
  - `PUT /api/hub/freelancers/{freelancer_id}/fiscal` body `FiscalData` → `FiscalRead`
  - `GET /api/hub/freelancers/{freelancer_id}/matches` → `FreelancerContracts`
  - `GET /api/hub/freelancers/{freelancer_id}/matches/prefill?company_id=` → `MatchPrefill`
  - `POST /api/hub/freelancers/{freelancer_id}/matches/preview?documento=lettera|quadro` body `MatchCreate` → `application/pdf`
  - `POST /api/hub/freelancers/{freelancer_id}/matches` body `MatchCreate` → 201 `MatchRead`
  - `GET /api/hub/matches/{match_id}` → `MatchRead`; `POST /api/hub/matches/{match_id}/cancel` and `/close` → `MatchRead` (409 on the wrong state)
  - `GET /api/hub/contract-documents/{document_id}/pdf[?firmato=true]` → `application/pdf` attachment (404 when there is no signed copy)
  - A `ContractFailed` anywhere is a 503 with `{"detail": "La generazione del contratto non è riuscita: ..."}`.
  - `rebase_api.deps.get_renderer() -> Renderer`, `RendererDep`; `rebase_api.downloads.pdf_response(filename: str, content: bytes) -> Response`.
  - MCP tools `list_matches(freelancer_id: str)` and `get_match(match_id: str)`: every document carries `pdf_url` (and `pdf_firmato_url` when signed), `{origin of REBASE_HUB_URL}/api/hub/contract-documents/{id}/pdf`, or the bare path without settings; `list_matches` never returns `fiscale`.

- [ ] **Step 1: Write the failing API tests** (`test_matches_api.py`)

```python
"""REB-387 phase 2 over HTTP: tax data, a match created from a card and a request, both
PDFs downloadable, a draft cancelled; nothing at all without the admin cookie."""

import re
from collections.abc import Iterator

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_renderer, get_sender
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import User

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
# What the company would pay rebase a day: it must appear nowhere this flow answers.
BUDGET = "777.77"
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


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer()
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender) -> None:
    assert client.post("/api/hub/auth/link", json={"email": ADMIN_EMAIL}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient) -> str:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": "ada@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/freelancers").json()["items"][0]["id"])


def _request_company(client: TestClient) -> str:
    response = client.post(
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
            "budget_giornaliero": BUDGET,
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/companies").json()["items"][0]["id"])


def _ready(client: TestClient, sender: RecordingSender) -> tuple[str, str]:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL).status_code == 200
    return freelancer_id, company_id


def test_without_the_cookie_every_match_route_is_a_401(client: TestClient, admin: None) -> None:
    for method, path in (
        ("GET", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("PUT", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches/prefill?company_id={MISSING}"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches/preview"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", f"/api/hub/matches/{MISSING}"),
        ("POST", f"/api/hub/matches/{MISSING}/cancel"),
        ("POST", f"/api/hub/matches/{MISSING}/close"),
        ("GET", f"/api/hub/contract-documents/{MISSING}/pdf"),
    ):
        assert client.request(method, path, json={}).status_code == 401, (method, path)


def test_an_admin_matches_a_card_with_a_request_and_downloads_both_pdfs(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/fiscal").json() is None
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["partita_iva"] == "01234567890"

    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.status_code == 200, prefill.text
    assert prefill.json()["lettera"]["compenso"] == "450.00"
    assert prefill.json()["quadro_necessario"] is True
    assert BUDGET not in prefill.text

    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    match = created.json()
    letter = match["lettera"]
    assert match["stato"] == "bozza"
    assert re.fullmatch(r"\d{4}-001", letter["numero"])
    assert letter["stato"] == "in_attesa"
    assert BUDGET not in created.text

    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["quadro"]["stato"] == "generato"
    assert [item["id"] for item in page["matches"]] == [match["id"]]
    assert page["fiscale"]["codice_fiscale"] == "LVLDAA85T50H501Z"
    assert client.get(f"/api/hub/matches/{match['id']}").json()["lettera"]["numero"] == letter["numero"]

    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert f"lettera-di-incarico-{letter['numero']}.pdf" in pdf.headers["content-disposition"]
    assert client.get(f"/api/hub/contract-documents/{page['quadro']['id']}/pdf").status_code == 200
    signed = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf", params={"firmato": "true"})
    assert signed.status_code == 404


def test_the_preview_renders_a_document_without_saving_or_numbering_it(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    body = {"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview", params={"documento": "lettera"}, json=body
    )
    assert preview.status_code == 200, preview.text
    assert preview.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    quadro = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview", params={"documento": "quadro"}, json=body
    )
    assert quadro.status_code == 200
    assert renderer.calls[-1][0] == "contratto-quadro"
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_a_match_without_tax_data_names_the_step_that_is_missing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 422
    assert created.json()["detail"][0]["loc"] == ["body", "fiscale"]


def test_a_closed_request_and_a_fee_the_law_refuses_are_422s_on_their_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    assert client.patch(f"/api/hub/companies/{company_id}", json={"stato": "chiuso"}).status_code == 200
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_a_draft_is_cancelled_once_and_only_an_active_match_closes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 409
    cancelled = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["stato"], cancelled.json()["lettera"]["stato"]) == ("annullato", "annullato")
    again = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert again.status_code == 409
    assert "bozza" in again.json()["detail"]


def test_a_render_that_fails_is_a_503_with_a_sentence(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_renderer] = lambda: FailingRenderer()  # type: ignore[attr-defined]
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert created.json()["detail"].startswith("La generazione del contratto non è riuscita")
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_a_malformed_signer_setting_is_a_503_not_a_crash(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None, signer_json="{non json"  # type: ignore[call-arg]
    )
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert "REBASE_SIGNER_JSON" in created.json()["detail"]
```

- [ ] **Step 2: Write the failing MCP tests** (`apps/mcp/tests/test_match_tools.py`)

```python
"""REB-387's two read-only MCP tools: an agent reads a freelancer's matches and
contracts, with links to the PDFs and never their bytes or the tax data."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fakes_contracts import FakeRenderer
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, PDF, _payload

from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.contract_schemas import ClienteData, FiscalData, LetteraFields, MatchCreate
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import User
from rebase_core.schemas import CompanyCreate, FreelancerCreate
from rebase_mcp.server import build_server

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


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed(factory: sessionmaker[Session]) -> tuple[str, str]:
    """A card, a request, the tax data and one draft match, written by `IVAN`, whose
    `users` row this file needs because the match names its author."""
    session = factory()
    try:
        session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, cognome="", role="admin"))
        session.commit()
        card, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        request, _ = CompanyService(session).request(
            CompanyCreate(
                nome_azienda="ACME Srl",
                referente_nome="Wile",
                referente_cognome="E.",
                email="wile@acme.it",
                telefono="+39 345 1234567",
                figura_richiesta="Backend developer",
                progetto="Un backend developer per tre mesi.",
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=Decimal("500"),
                remoto="remoto",
                numero_risorse=1,
            )
        )
        FiscalService(session).save(
            card.id,
            FiscalData(codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio="Milano"),
            IVAN.id,
        )
        match = MatchService(session, FakeRenderer()).create(
            card.id,
            MatchCreate(
                company_id=request.id,
                cliente=ClienteData(
                    cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
                ),
                lettera=LetteraFields(
                    ruolo="Backend developer",
                    attivita="Le API.",
                    data_inizio=date(2026, 10, 1),
                    compenso=Decimal("450"),
                    giorni_pagamento=30,
                    fine_mese=True,
                ),
            ),
            IVAN.id,
        )
        return str(card.id), str(match.id)
    finally:
        session.close()


async def test_list_matches_reads_the_page_with_links_and_no_tax_data(
    factory: sessionmaker[Session],
) -> None:
    card, match = _seed(factory)
    try:
        server = build_server(factory, lambda: IVAN, settings=Settings(_env_file=None))  # type: ignore[call-arg]
        async with Client(server) as client:
            body = _payload(await client.call_tool("list_matches", {"freelancer_id": card}))
        assert "fiscale" not in body
        assert body["quadro"]["stato"] == "generato"
        assert body["quadro"]["pdf_url"] == (
            f"https://letsrebase.com/api/hub/contract-documents/{body['quadro']['id']}/pdf"
        )
        (item,) = body["matches"]
        assert item["id"] == match
        assert item["lettera"]["pdf_url"].endswith(f"/contract-documents/{item['lettera']['id']}/pdf")
        assert "pdf" not in item["lettera"] and "data" not in item["lettera"]
    finally:
        _wipe(factory)


async def test_get_match_answers_the_letter_and_the_framework_with_links(
    factory: sessionmaker[Session],
) -> None:
    _card, match = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            body = _payload(await client.call_tool("get_match", {"match_id": match}))
            missing = await client.call_tool("get_match", {"match_id": str(UUID(int=7))})
        assert body["stato"] == "bozza"
        assert body["lettera"]["pdf_url"] == f"/api/hub/contract-documents/{body['lettera']['id']}/pdf"
        assert body["quadro"]["pdf_url"].startswith("/api/hub/contract-documents/")
        assert missing.is_error
    finally:
        _wipe(factory)
```

In `test_tools.py`, add `"list_matches"` and `"get_match"` to the expected tool name set (lines 142-172).

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q projects/hub/apps/api/tests/test_matches_api.py projects/hub/apps/mcp/tests/test_match_tools.py projects/hub/apps/mcp/tests/test_tools.py`
Expected: FAIL (`cannot import name 'get_renderer'`; unknown tool `list_matches`).

- [ ] **Step 4: The dependency, the download, the 503** 

`deps.py` (append; import `ContractRenderer, Renderer` from `rebase_core.contracts.render`):

```python
def get_renderer() -> Renderer:
    """The contracts' typesetter (REB-387): pandoc and Typst in the image. A dependency
    so a test hands `FakeRenderer` and never needs either binary."""
    return ContractRenderer()


RendererDep = Annotated[Renderer, Depends(get_renderer)]
```

`downloads.py` (append):

```python
def pdf_response(filename: str, content: bytes) -> Response:
    """A contract's PDF as an attachment (REB-387), through `cv_response` so the one
    header-safety rule stays in one place. The name carries a letter number, never
    something a person typed, but a second shape for this header is what would drift."""
    return cv_response(CvFile(filename=filename, mime="application/pdf", content=content))
```

`main.py`: import `ContractFailed` from `rebase_core.contracts.fields` and `matches` from `rebase_api.routers`; in `domain_error_handler`, before the final `return`, add

```python
    if isinstance(exc, ContractFailed):
        # A contract that could not be typeset is the server's failure, not the
        # request's: pandoc missing, a template that no longer compiles, a malformed
        # REBASE_SIGNER_JSON. A sentence, and a status that says so.
        return JSONResponse({"detail": exc.message}, status_code=503)
```

and `app.include_router(matches.router)` after `admin.router`. Update the handler's docstring: «`ContractFailed` a 503».

- [ ] **Step 5: Write `routers/matches.py`**

```python
"""Matches and the contracts they write, behind the admin cookie (REB-387, phase 2).

An admin pairs a freelancer card with a company request; the hub writes the letter of
engagement and, when the freelancer has no active framework agreement, the framework
agreement too, and both PDFs are downloadable from here. Nothing leaves the hub yet:
sending for signature arrives with Documenso (phase 3). The routes sit under `/api/hub/`
beside the rest of the admin area. A contract that cannot be typeset is a 503 with a
sentence (`main.domain_error_handler`).
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Response, status
from sqlalchemy.orm import Session

from rebase_api.deps import AdminDep, RendererDep, SessionDep, SettingsDep
from rebase_api.downloads import pdf_response
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    FiscalData,
    FiscalRead,
    FreelancerContracts,
    MatchCreate,
    MatchPrefill,
    MatchRead,
)
from rebase_core.contracts.fields import signer_data
from rebase_core.contracts.render import Renderer
from rebase_core.fiscal import FiscalService
from rebase_core.matches import MatchService

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])


def _writing(session: Session, settings: Settings, renderer: Renderer) -> MatchService:
    """The service as the two routes that typeset need it: the renderer, and who signs
    for rebase. The reads take neither."""
    return MatchService(session, renderer, signer_data(settings.signer_json))


@router.get("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead | None)
def get_fiscal(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> FiscalRead | None:
    return FiscalService(session).get(freelancer_id)


@router.put("/freelancers/{freelancer_id}/fiscal", response_model=FiscalRead)
def save_fiscal(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, payload: FiscalData
) -> FiscalRead:
    """Step 2 of «Crea match» and the form on «Match e contratti»: saved for next time."""
    return FiscalService(session).save(freelancer_id, payload, admin.id)


@router.get("/freelancers/{freelancer_id}/matches", response_model=FreelancerContracts)
def list_freelancer_matches(
    _: AdminDep, session: SessionDep, freelancer_id: UUID
) -> FreelancerContracts:
    return MatchService(session).for_freelancer(freelancer_id)


@router.get("/freelancers/{freelancer_id}/matches/prefill", response_model=MatchPrefill)
def prefill_match(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, company_id: UUID
) -> MatchPrefill:
    return MatchService(session).prefill(freelancer_id, company_id)


@router.post("/freelancers/{freelancer_id}/matches/preview")
def preview_match_document(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    renderer: RendererDep,
    freelancer_id: UUID,
    payload: MatchCreate,
    documento: Literal["lettera", "quadro"] = "lettera",
) -> Response:
    """Step 5's preview: one document, typeset now, saved nowhere, numbered never."""
    pdf = _writing(session, settings, renderer).preview(freelancer_id, payload, documento)
    return pdf_response(pdf.filename, pdf.content)


@router.post(
    "/freelancers/{freelancer_id}/matches",
    response_model=MatchRead,
    status_code=status.HTTP_201_CREATED,
)
def create_match(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    renderer: RendererDep,
    freelancer_id: UUID,
    payload: MatchCreate,
) -> MatchRead:
    """«Salva come bozza»: the draft match with its numbered letter and, when needed,
    the framework agreement. 422 naming `fiscale` without tax data, `company_id` for a
    closed request."""
    return _writing(session, settings, renderer).create(freelancer_id, payload, admin.id)


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(_: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    return MatchService(session).get(match_id)


@router.post("/matches/{match_id}/cancel", response_model=MatchRead)
def cancel_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    return MatchService(session).cancel(match_id, admin.id)


@router.post("/matches/{match_id}/close", response_model=MatchRead)
def close_match(admin: AdminDep, session: SessionDep, match_id: UUID) -> MatchRead:
    return MatchService(session).close(match_id, admin.id)


@router.get("/contract-documents/{document_id}/pdf")
def download_contract(
    _: AdminDep, session: SessionDep, document_id: UUID, firmato: bool = False
) -> Response:
    pdf = MatchService(session).document_pdf(document_id, signed=firmato)
    return pdf_response(pdf.filename, pdf.content)
```

- [ ] **Step 6: Add the two MCP tools** (`server.py`: `from urllib.parse import urlsplit`, `from rebase_core.matches import MatchService`; inside `build_server`, after `login_stats` and before `_run`)

```python
    hub = urlsplit(settings.hub_url) if settings is not None else None
    # The PDFs are links an admin opens with their own session, never bytes in an
    # agent's context: the API's origin is the SPA's (`REBASE_HUB_URL`), or none at all.
    api_origin = f"{hub.scheme}://{hub.netloc}" if hub is not None and hub.netloc else ""

    def _document_links(document: dict[str, Any]) -> None:
        path = f"/api/hub/contract-documents/{document['id']}/pdf"
        document["pdf_url"] = f"{api_origin}{path}"
        if document.get("ha_pdf_firmato"):
            document["pdf_firmato_url"] = f"{api_origin}{path}?firmato=true"

    @mcp.tool()
    def list_matches(freelancer_id: str) -> dict[str, Any]:
        """I match e i contratti di un freelance, per id della scheda, come li mostra la
        pagina «Match e contratti»: `quadro` è il contratto quadro (quello attivo, o
        l'ultimo non annullato) con stato, data di firma, prossimo rinnovo, ultimo giorno
        per la disdetta e versione del testo; `quadri` li elenca tutti; `matches` sono i
        match dal più recente, ognuno con l'azienda, lo stato e la lettera di incarico
        con il suo numero. Ogni documento porta `pdf_url`, il link al PDF da aprire con
        l'accesso admin: mai i byte, mai i dati fiscali. Solo lettura: i match si creano
        dall'area admin."""
        body = _run(lambda s: MatchService(s).for_freelancer(UUID(freelancer_id)))
        body.pop("fiscale", None)
        for document in (body["quadro"], *body["quadri"]):
            if document is not None:
                _document_links(document)
        for match in body["matches"]:
            _document_links(match["lettera"])
        return body

    @mcp.tool()
    def get_match(match_id: str) -> dict[str, Any]:
        """Un match, per id: l'azienda e la figura richiesta, i dati del cliente come li
        stampa la lettera, lo stato (bozza, in_firma, attivo, concluso, annullato), la
        lettera di incarico con numero e stato, e in `quadro` il contratto quadro del
        freelance. Ogni documento porta `pdf_url`, mai i byte. Solo lettura."""
        session = factory()
        try:
            service = MatchService(session)
            match = service.get(UUID(match_id))
            quadro = service.for_freelancer(match.freelancer_id).quadro
            body = match.model_dump(mode="json")
            body["quadro"] = quadro.model_dump(mode="json") if quadro is not None else None
        except DomainError as exc:
            raise ToolError(exc.message) from exc
        finally:
            session.close()
        _document_links(body["lettera"])
        if body["quadro"] is not None:
            _document_links(body["quadro"])
        return body
```

- [ ] **Step 7: Run the API and MCP suites**

Run: `uv run pytest -q projects/hub/apps/api/tests projects/hub/apps/mcp/tests`
Expected: all pass.

- [ ] **Step 8: Whole hub suite, lint, types, commit**

Run: `uv run pytest -q projects/hub/packages/core/tests projects/hub/apps/api/tests projects/hub/apps/mcp/tests && uv run ruff check projects/hub && uv run ruff format --check projects/hub && uv run mypy`

```bash
git add projects/hub/apps/api/src/rebase_api/routers/matches.py \
  projects/hub/apps/api/src/rebase_api/main.py projects/hub/apps/api/src/rebase_api/deps.py \
  projects/hub/apps/api/src/rebase_api/downloads.py \
  projects/hub/apps/api/tests/test_matches_api.py \
  projects/hub/apps/mcp/src/rebase_mcp/server.py \
  projects/hub/apps/mcp/tests/test_match_tools.py projects/hub/apps/mcp/tests/test_tools.py
git commit -F - <<'EOF'
feat(hub): the admin API and two MCP tools for matches and their PDFs

I add the routes the two new admin pages call: tax data read and saved,
a freelancer's matches and contracts, the prefill, a preview that saves
nothing, the draft match itself, cancel and close, and the PDF download,
all behind the admin cookie under /api/hub/. A contract that cannot be
typeset answers 503 with a sentence. The MCP server gains list_matches
and get_match, read-only, with links to the PDFs instead of their bytes
and without the freelancer's tax data.

REB-N.
EOF
```

---

### Task 7: The talent row menu and the «Match e contratti» page

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts` (types after `CompanyList`, functions in `admin`)
- Create: `projects/hub/apps/web/src/lib/contracts.ts`
- Modify: `projects/hub/apps/web/src/lib/format.ts` (two label maps)
- Create: `projects/hub/apps/web/src/pages/admin/Contratti.tsx`, `Contratti.test.tsx`
- Modify: `projects/hub/apps/web/src/router.tsx:21-27,262-266,369-375` (route)
- Modify: `projects/hub/apps/web/src/pages/admin/lists.tsx:1-40,408-455,871-912,998` (menu, header link, export `Row`)
- Modify: `projects/hub/apps/web/src/pages/admin/lists.test.tsx:170-231` (mount) and a new `describe`

**Interfaces:**
- Consumes (HTTP, Task 6): `GET /api/hub/freelancers/{id}`, `GET /api/hub/freelancers/{id}/matches`, `PUT /api/hub/freelancers/{id}/fiscal`, `POST /api/hub/matches/{id}/cancel`, `POST /api/hub/matches/{id}/close`, `GET /api/hub/contract-documents/{id}/pdf[?firmato=true]`.
- Produces: in `api.ts`, types `MatchStato`, `DocumentStato`, `FiscalData`, `Fiscal`, `ContractDocument`, `Match`, `FreelancerContracts`, and `admin.contracts(freelancerId)`, `admin.fiscal(freelancerId)`, `admin.saveFiscal(freelancerId, data)`, `admin.cancelMatch(matchId)`, `admin.closeMatch(matchId)`, `admin.contractPdfUrl(documentId, firmato?)`; in `lib/contracts.ts`, `FiscalDraft`, `FISCAL_EMPTY`, `draftFromFiscal(fiscal: Fiscal | null)`, `toFiscalData(draft)`; in `format.ts`, `MATCH_STATE_LABELS`, `DOCUMENT_STATE_LABELS`; page component `AdminContratti` and reusable `FiscalFields({ idPrefix, draft, onChange, wrong })` in `pages/admin/Contratti.tsx`; `Row` exported from `lists.tsx`; route id `/signedIn/admin/freelance/$id/contracts`.

- [ ] **Step 1: Write the failing page tests** (`Contratti.test.tsx`)

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { formatDate } from '@/lib/format'
import { AdminContratti } from './Contratti'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** `lists.test.tsx`'s router of fetches, plus a handler that may be a function of the
 *  request, so one test can answer a GET differently after a POST. */
function routeFetch(handlers: Record<string, unknown>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    const handler = handlers[key]
    return answer(200, typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler)
  })
}

const PERSON = { id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' }
const QUADRO = {
  id: 'd1',
  kind: 'quadro',
  freelancer_id: 'f1',
  match_id: null,
  numero: null,
  text_version: '0.1',
  testo_bozza: true,
  stato: 'firmato',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  sent_at: '2026-09-23T11:00:00Z',
  signed_at: '2026-10-01T09:00:00Z',
  notice_at: null,
  ha_pdf_firmato: true,
  attivo: true,
  rinnovo: '2027-10-01',
  ultimo_giorno_disdetta: '2027-09-01',
  nuova_versione: true,
}
const LETTERA = {
  ...QUADRO,
  id: 'd2',
  kind: 'lettera',
  match_id: 'm1',
  numero: '2026-001',
  testo_bozza: true,
  stato: 'generato',
  sent_at: null,
  signed_at: null,
  ha_pdf_firmato: false,
  attivo: false,
  rinnovo: null,
  ultimo_giorno_disdetta: null,
  nuova_versione: false,
}
const MATCH = {
  id: 'm1',
  freelancer_id: 'f1',
  company_id: 'c1',
  nome_azienda: 'Rossi Studio',
  figura_richiesta: 'Backend developer',
  cliente_ragione_sociale: 'Rossi Studio S.r.l.',
  cliente_piva: '01234567890',
  cliente_sede: 'Milano',
  stato: 'bozza',
  created_at: '2026-09-23T10:00:00Z',
  created_by: 'a1',
  cancelled_at: null,
  updated_at: '2026-09-23T10:00:00Z',
  lettera: LETTERA,
}
const FISCALE = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
const PAGE = { freelancer_id: 'f1', quadro: QUADRO, quadri: [QUADRO], matches: [MATCH], fiscale: FISCALE }

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: AdminContratti,
  })
  const card = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id',
    component: () => <p>scheda</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([contratti, card])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

afterEach(() => vi.restoreAllMocks())

describe('«Match e contratti» (REB-387)', () => {
  it('shows the framework agreement, its dates, its version and its PDFs', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByRole('heading', { name: 'Match e contratti · Ada Lovelace' })).toBeInTheDocument()
    const section = screen.getByRole('region', { name: 'Contratto quadro' })
    expect(within(section).getByText('Firmato')).toBeInTheDocument()
    expect(within(section).getByText('Attivo')).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2026-10-01T09:00:00Z'))).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2027-10-01'))).toBeInTheDocument()
    expect(within(section).getByText(formatDate('2027-09-01'))).toBeInTheDocument()
    expect(within(section).getByText('Nuova versione disponibile')).toBeInTheDocument()
    expect(within(section).getByText('Testo in bozza')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'PDF del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf',
    )
    expect(within(section).getByRole('link', { name: 'PDF firmato del contratto quadro' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d1/pdf?firmato=true',
    )
    // Phase 2 signs nothing: none of the signing actions is on the page yet.
    expect(screen.queryByRole('button', { name: /firma|Reinvia|Aggiorna stato|disdetta/i })).toBeNull()
  })

  it('lists the matches with their letter and cancels a draft after asking', async () => {
    let cancelled = false
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': () =>
        cancelled
          ? { ...PAGE, matches: [{ ...MATCH, stato: 'annullato', lettera: { ...LETTERA, stato: 'annullato' } }] }
          : PAGE,
      'POST /api/hub/matches/m1/cancel': () => {
        cancelled = true
        return { ...MATCH, stato: 'annullato' }
      },
    })
    mount('/admin/freelance/f1/contracts')
    const row = (await screen.findByText('Rossi Studio')).closest('tr')!
    expect(within(row).getByText('Bozza')).toBeInTheDocument()
    expect(within(row).getByText(/n\. 2026-001/)).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'PDF della lettera n. 2026-001' })).toHaveAttribute(
      'href',
      '/api/hub/contract-documents/d2/pdf',
    )
    await userEvent.click(within(row).getByRole('button', { name: 'Annulla il match con Rossi Studio' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Annulla il match' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/cancel', expect.objectContaining({ method: 'POST' })))
    expect(await screen.findByText('Annullato')).toBeInTheDocument()
  })

  it('closes an active match', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { ...PAGE, matches: [{ ...MATCH, stato: 'attivo' }] },
      'POST /api/hub/matches/m1/close': { ...MATCH, stato: 'concluso' },
    })
    mount('/admin/freelance/f1/contracts')
    await userEvent.click(await screen.findByRole('button', { name: 'Chiudi il match con Rossi Studio' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('/api/hub/matches/m1/close', expect.objectContaining({ method: 'POST' })))
  })

  it('saves the tax data from the page', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': PAGE,
      'PUT /api/hub/freelancers/f1/fiscal': { ...FISCALE, domicilio: 'Corso Como 1, Milano' },
    })
    mount('/admin/freelance/f1/contracts')
    const domicilio = await screen.findByLabelText('Domicilio professionale')
    expect(domicilio).toHaveValue('Via Roma 1, Milano')
    await userEvent.clear(domicilio)
    await userEvent.type(domicilio, 'Corso Como 1, Milano')
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Corso Como 1, Milano',
      pec: null,
    })
  })

  it('says so, in words, when there is no framework agreement and no match yet', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': { freelancer_id: 'f1', quadro: null, quadri: [], matches: [], fiscale: null },
    })
    mount('/admin/freelance/f1/contracts')
    expect(await screen.findByText('Nessun contratto quadro: lo genera il primo match.')).toBeInTheDocument()
    expect(screen.getByText('Nessun match per questa persona.')).toBeInTheDocument()
    expect(screen.getByLabelText('Codice fiscale')).toHaveValue('')
  })
})
```

Add to `lists.test.tsx`: in `mount` (line 170 onward), a stub route beside `freelanceDetail`,

```tsx
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: () => <p>contratti</p>,
  })
```

added to `signedIn.addChildren([...])`, and a new block:

```tsx
describe('the talent row menu and the card link to its contracts (REB-387)', () => {
  it('offers «Match e contratti» on a card row and no menu on a lead', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      answer(200, { totale: 2, items: [CARD_TALENTO, LEAD_TALENTO], per_stato: {} }),
    )
    mount('/admin/talent')
    const bob = (await screen.findByText('bob@example.org')).closest('tr')!
    expect(within(bob).queryByRole('button', { name: /Azioni per/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Azioni per Ada Lovelace' }))
    const item = await screen.findByRole('menuitem', { name: 'Match e contratti' })
    expect(item.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/contracts$/)
  })

  it('links a card to its matches and contracts from its header', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f2': COMPLETE, 'GET /api/hub/freelancers/f2/audit': [] })
    mount('/admin/freelance/f2')
    const link = await screen.findByRole('link', { name: 'Match e contratti' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f2\/contracts$/)
  })
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `pnpm --filter hub exec vitest run src/pages/admin/Contratti.test.tsx src/pages/admin/lists.test.tsx`
Expected: FAIL, `Failed to resolve import "./Contratti"`, and no `Azioni per Ada Lovelace` button.

- [ ] **Step 3: Types and calls in `api.ts`** (after `CompanyList`; functions appended inside `admin`)

```ts
/** REB-387: a match's state, and a contract document's. */
export type MatchStato = 'bozza' | 'in_firma' | 'attivo' | 'concluso' | 'annullato'
export type DocumentStato = 'generato' | 'in_attesa' | 'inviato' | 'firmato' | 'annullato' | 'disdetto'

/** A freelancer's tax data, as the two contracts print them. */
export interface FiscalData {
  codice_fiscale: string
  partita_iva: string
  domicilio: string
  pec: string | null
}

export interface Fiscal extends FiscalData {
  freelancer_id: string
  updated_by: string
  updated_at: string
}

/** A generated contract as the pages read it: never its bytes, which are a link. */
export interface ContractDocument {
  id: string
  kind: 'quadro' | 'lettera'
  freelancer_id: string
  match_id: string | null
  numero: string | null
  text_version: string
  testo_bozza: boolean
  stato: DocumentStato
  created_at: string
  created_by: string
  sent_at: string | null
  signed_at: string | null
  notice_at: string | null
  ha_pdf_firmato: boolean
  attivo: boolean
  rinnovo: string | null
  ultimo_giorno_disdetta: string | null
  nuova_versione: boolean
}

export interface Match {
  id: string
  freelancer_id: string
  company_id: string
  nome_azienda: string
  figura_richiesta: string
  cliente_ragione_sociale: string
  cliente_piva: string
  cliente_sede: string
  stato: MatchStato
  created_at: string
  created_by: string
  cancelled_at: string | null
  updated_at: string
  lettera: ContractDocument
}

/** «Match e contratti»: the framework agreement at the top, every one of them, the
 *  matches newest first, and the tax data the page edits. */
export interface FreelancerContracts {
  freelancer_id: string
  quadro: ContractDocument | null
  quadri: ContractDocument[]
  matches: Match[]
  fiscale: Fiscal | null
}
```

```ts
  /** A freelancer's matches and contracts (REB-387). */
  contracts: (freelancerId: string) =>
    request<FreelancerContracts>(`/api/hub/freelancers/${freelancerId}/matches`),
  fiscal: (freelancerId: string) => request<Fiscal | null>(`/api/hub/freelancers/${freelancerId}/fiscal`),
  saveFiscal: (freelancerId: string, data: FiscalData) =>
    request<Fiscal>(`/api/hub/freelancers/${freelancerId}/fiscal`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  cancelMatch: (matchId: string) => request<Match>(`/api/hub/matches/${matchId}/cancel`, { method: 'POST' }),
  closeMatch: (matchId: string) => request<Match>(`/api/hub/matches/${matchId}/close`, { method: 'POST' }),
  /** A plain href, like `cvUrl`: the route answers an attachment behind the cookie. */
  contractPdfUrl: (documentId: string, firmato = false) =>
    `/api/hub/contract-documents/${documentId}/pdf${firmato ? '?firmato=true' : ''}`,
```

`format.ts`, append:

```ts
/** REB-387: what a match and a contract document are, in the admin's words. */
export const MATCH_STATE_LABELS: Record<string, string> = {
  bozza: 'Bozza',
  in_firma: 'In firma',
  attivo: 'Attivo',
  concluso: 'Concluso',
  annullato: 'Annullato',
}
export const DOCUMENT_STATE_LABELS: Record<string, string> = {
  generato: 'Generato',
  in_attesa: 'In attesa del contratto quadro',
  inviato: 'Inviato',
  firmato: 'Firmato',
  annullato: 'Annullato',
  disdetto: 'Disdetto',
}
```

- [ ] **Step 4: `lib/contracts.ts`**

```ts
/**
 * The drafts the matches pages edit (REB-387): an input always holds a string, the API
 * takes typed values, and these helpers are the one place the two meet. Kept out of the
 * page files, which export components only (`react-refresh/only-export-components`).
 */
import type { Fiscal, FiscalData } from './api'

export type FiscalDraft = Record<keyof FiscalData, string>

export const FISCAL_EMPTY: FiscalDraft = { codice_fiscale: '', partita_iva: '', domicilio: '', pec: '' }

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    codice_fiscale: fiscal.codice_fiscale,
    partita_iva: fiscal.partita_iva,
    domicilio: fiscal.domicilio,
    pec: fiscal.pec ?? '',
  }
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address. */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    codice_fiscale: draft.codice_fiscale.trim(),
    partita_iva: draft.partita_iva.trim(),
    domicilio: draft.domicilio.trim(),
    pec: draft.pec.trim() || null,
  }
}
```

- [ ] **Step 5: `pages/admin/Contratti.tsx`**

```tsx
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, Download } from 'lucide-react'
import { useState, type ComponentProps, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type ContractDocument, type Fiscal, type FiscalData, type Match } from '@/lib/api'
import { draftFromFiscal, toFiscalData, type FiscalDraft } from '@/lib/contracts'
import { DOCUMENT_STATE_LABELS, MATCH_STATE_LABELS, formatDate } from '@/lib/format'
import { Empty, Header, Row } from './lists'

/** The four tax fields, shared by this page and step 2 of «Crea match». */
export function FiscalFields({
  idPrefix,
  draft,
  onChange,
  wrong,
}: {
  idPrefix: string
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  wrong: (field: string) => true | undefined
}) {
  const field = (name: keyof FiscalDraft, label: string, props: ComponentProps<typeof Input> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${idPrefix}-${name}`}>{label}</Label>
      <Input
        id={`${idPrefix}-${name}`}
        value={draft[name]}
        onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        aria-invalid={wrong(name)}
        {...props}
      />
    </div>
  )
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {field('codice_fiscale', 'Codice fiscale', { required: true, maxLength: 40, autoComplete: 'off' })}
      {field('partita_iva', 'Partita IVA', { required: true, maxLength: 40, inputMode: 'numeric' })}
      {field('domicilio', 'Domicilio professionale', { required: true, maxLength: 300 })}
      {field('pec', 'PEC, se ce l’ha', { type: 'email', maxLength: 320 })}
    </div>
  )
}

function DocumentLinks({ document }: { document: ContractDocument }) {
  const what = document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
  return (
    <span className="flex flex-wrap gap-2">
      <Button asChild variant="outline" size="sm">
        <a href={admin.contractPdfUrl(document.id)} aria-label={`PDF ${what}`}>
          <Download className="mr-2 size-4" />
          PDF
        </a>
      </Button>
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a href={admin.contractPdfUrl(document.id, true)} aria-label={`PDF firmato ${what}`}>
            <Download className="mr-2 size-4" />
            PDF firmato
          </a>
        </Button>
      )}
    </span>
  )
}

function FrameworkSection({ quadro }: { quadro: ContractDocument | null }) {
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
        </dl>
      )}
    </section>
  )
}

function FiscalSection({ freelancerId, fiscale, onSaved }: { freelancerId: string; fiscale: Fiscal | null; onSaved: () => void }) {
  const [draft, setDraft] = useState<FiscalDraft>(() => draftFromFiscal(fiscale))
  const [saved, setSaved] = useState(false)
  const save = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(freelancerId, data),
    onSuccess: () => {
      setSaved(true)
      onSaved()
    },
  })
  const failure = save.error instanceof ApiError ? save.error : null
  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    save.mutate(toFiscalData(draft))
  }
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Dati fiscali del freelance</h2>
      <form onSubmit={submit} className="max-w-2xl space-y-3 border p-4">
        <FiscalFields
          idPrefix="contratti"
          draft={draft}
          onChange={setDraft}
          wrong={(field) => failure?.fields.includes(field) || undefined}
        />
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva i dati fiscali'}
        </Button>
        {saved && (
          <p role="status" className="text-sm text-muted-foreground">
            Dati fiscali salvati.
          </p>
        )}
        {save.error && (
          <p role="alert" className="text-sm text-destructive">
            {failure ? failure.message : 'Non riesco a salvare i dati fiscali.'}
          </p>
        )}
      </form>
    </section>
  )
}

function MatchesSection({
  matches,
  busy,
  onCancel,
  onClose,
  error,
}: {
  matches: Match[]
  busy: boolean
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
                    <DocumentLinks document={match.lettera} />
                  </TableCell>
                  <TableCell className="text-right">
                    {match.stato === 'bozza' && (
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

/** «Match e contratti» (REB-387, phase 2): the framework agreement with its dates, the
 *  tax data, and every match with its letter. No signing action yet: sending, resending,
 *  refreshing and recording a notice arrive with the electronic signature (phase 3). */
export function AdminContratti() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/contracts' })
  const client = useQueryClient()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const contracts = useQuery({ queryKey: ['contracts', id], queryFn: () => admin.contracts(id) })
  const refresh = () => void client.invalidateQueries({ queryKey: ['contracts', id] })
  const cancel = useMutation({ mutationFn: (matchId: string) => admin.cancelMatch(matchId), onSuccess: refresh })
  const close = useMutation({ mutationFn: (matchId: string) => admin.closeMatch(matchId), onSuccess: refresh })
  const [confirming, setConfirming] = useState<Match | null>(null)

  if (contracts.isError) return <Empty>Non riesco a leggere i contratti di questa persona.</Empty>
  if (contracts.isPending) return <Empty>Caricamento…</Empty>
  const data = contracts.data
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const actionError = cancel.error ?? close.error
  const actionFailure =
    actionError instanceof ApiError
      ? actionError.message
      : actionError
        ? 'Non riesco a completare l’operazione.'
        : null
  return (
    <>
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'} />
      <FrameworkSection quadro={data.quadro} />
      <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaved={refresh} />
      <MatchesSection
        matches={data.matches}
        busy={cancel.isPending || close.isPending}
        onCancel={setConfirming}
        onClose={(match) => close.mutate(match.id)}
        error={actionFailure}
      />
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Torna alla scheda
        </Link>
      </p>
      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Annullare il match?</DialogTitle>
            <DialogDescription>
              {confirming &&
                `Il match con ${confirming.nome_azienda} e la lettera n. ${confirming.lettera.numero} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(null)}>
              Indietro
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={cancel.isPending}
              onClick={() => {
                if (confirming) cancel.mutate(confirming.id, { onSettled: () => setConfirming(null) })
              }}
            >
              {cancel.isPending ? 'Annullo…' : 'Annulla il match'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
```

- [ ] **Step 6: The route, the row menu, the card's header link**

`router.tsx`: `import { AdminContratti } from '@/pages/admin/Contratti'` and, after `adminFreelanceDetail`,

```tsx
// REB-387: a card's matches and contracts, from the talent row's menu and the card's header.
const adminFreelanceContracts = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id/contracts',
  component: AdminContratti,
})
```

added to `adminArea.addChildren([...])` right after `adminFreelanceDetail`.

`lists.tsx`: add `MoreHorizontal` to the `lucide-react` import and

```tsx
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
```

In `AdminTalenti`'s table header, after `<TableHead className="text-right">Quando</TableHead>`: `<TableHead className="w-12"><span className="sr-only">Azioni</span></TableHead>`. In `TalentoRow` (lines 438-456) leave the four existing cells as they are and add a fifth one as the row's last child, after the `Quando` cell:

```tsx
      <TableCell className="w-12 text-right">
        {item.stato !== 'lead' && <TalentoMenu id={item.id} label={name || item.email} />}
      </TableCell>
```

and add the menu right after `TalentoRow`:

```tsx
/** The row's own actions (REB-387), on a card only: a bare sign-up has no card to match
 *  and no contract to read. The name keeps linking to the card. */
function TalentoMenu({ id, label }: { id: string; label: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`Azioni per ${label}`}>
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link to="/admin/freelance/$id/contracts" params={{ id }}>
            Match e contratti
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
```

In `AdminFreelancerDetail`'s header, right before the «Modifica scheda» button (line 891), add:

```tsx
          {f.deleted_at === null && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/freelance/$id/contracts" params={{ id: f.id }}>
                Match e contratti
              </Link>
            </Button>
          )}
```

and turn `function Row(` (line 998) into `export function Row(`.

- [ ] **Step 7: Run the web checks**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: all green; the existing `lists.test.tsx` cases still pass (the new column is last, and the menu's items only exist while it is open).

- [ ] **Step 8: Commit**

```bash
git add projects/hub/apps/web/src/lib/api.ts projects/hub/apps/web/src/lib/contracts.ts \
  projects/hub/apps/web/src/lib/format.ts projects/hub/apps/web/src/router.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.tsx projects/hub/apps/web/src/pages/admin/Contratti.test.tsx \
  projects/hub/apps/web/src/pages/admin/lists.tsx projects/hub/apps/web/src/pages/admin/lists.test.tsx
git commit -F - <<'EOF'
feat(hub): «Match e contratti», from the talent row's menu and the card

I add the admin page that reads a card's contracts: the framework
agreement with its state, signature date, next renewal, last day for a
notice and text version, the tax data as a form, and every match with its
numbered letter, both PDFs one click away. A draft can be cancelled after
a confirmation and an active match closed; no signing action exists yet.
Card rows on «Talenti» get a menu that opens it, and so does the card's
own header.

REB-N.
EOF
```

---

### Task 8: The five-step «Crea match»

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts` (letter types, `requestBlob`, three calls)
- Modify: `projects/hub/apps/web/src/lib/contracts.ts` (client and letter forms)
- Create: `projects/hub/apps/web/src/lib/contracts.test.ts`
- Create: `projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`, `CreaMatch.test.tsx`
- Modify: `projects/hub/apps/web/src/router.tsx` (route), `pages/admin/lists.tsx` (menu item), `pages/admin/lists.test.tsx`, `pages/admin/Contratti.tsx` («Crea match» button), `pages/admin/Contratti.test.tsx`

**Interfaces:**
- Consumes (HTTP, Task 6): `GET /api/hub/freelancers/{id}`, `GET /api/hub/companies?q=&limit=50`, `GET /api/hub/freelancers/{id}/matches/prefill?company_id=`, `PUT /api/hub/freelancers/{id}/fiscal`, `POST /api/hub/freelancers/{id}/matches/preview?documento=lettera|quadro`, `POST /api/hub/freelancers/{id}/matches`. From Task 7: `FiscalFields`, `FISCAL_EMPTY`, `draftFromFiscal`, `toFiscalData`, `FiscalDraft`, `Header`, `Empty`, `admin.saveFiscal`, `admin.companies`, `Company`, `Match`, `Fiscal`.
- Produces: in `api.ts`, `LETTERA_TEXT_KEYS` (the 25 text keys, same order as the Python `LETTERA_TEXT_FIELDS`), `LetteraTextKey`, `Lettera`, `LetteraDraft`, `Cliente`, `ClienteDraft`, `MatchCreate`, `MatchPrefill`, `admin.matchPrefill(freelancerId, companyId)`, `admin.matchPreview(freelancerId, payload, documento): Promise<Blob>`, `admin.createMatch(freelancerId, payload)`; in `lib/contracts.ts`, `ClienteForm`, `CLIENTE_EMPTY`, `clienteForm`, `toCliente`, `LetteraForm`, `LetteraFieldKey`, `LETTERA_EMPTY`, `letteraForm`, `toLettera`, `LETTERA_LABELS`, `LETTERA_GROUPS`, `LETTERA_REQUIRED`, `LETTERA_MULTILINE`; page `AdminCreaMatch` at route id `/signedIn/admin/freelance/$id/match/new`.

- [ ] **Step 1: Write the failing unit tests** (`lib/contracts.test.ts`)

```ts
import { describe, expect, it } from 'vitest'
import { LETTERA_TEXT_KEYS, type LetteraDraft } from './api'
import { LETTERA_EMPTY, LETTERA_GROUPS, LETTERA_LABELS, letteraForm, toCliente, toLettera } from './contracts'

describe('the letter form (REB-387)', () => {
  it('shows every field of the letter exactly once, in a group', () => {
    const grouped = LETTERA_GROUPS.flatMap((group) => group.fields)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect(new Set(grouped)).toEqual(new Set(Object.keys(LETTERA_LABELS)))
    expect(grouped).toHaveLength(LETTERA_TEXT_KEYS.length + 6)
  })

  it('sends an empty box as null, a comma decimal with a dot, and numbers as numbers', () => {
    const lettera = toLettera({
      ...LETTERA_EMPTY,
      ruolo: ' Backend developer ',
      attivita: 'Le API',
      data_inizio: '2026-10-01',
      compenso: '450,50',
      giorni_pagamento: '30',
      fine_mese: true,
    })
    expect(lettera.ruolo).toBe('Backend developer')
    expect(lettera.risultati).toBeNull()
    expect(lettera.data_fine).toBeNull()
    expect(lettera.compenso).toBe('450.50')
    expect(lettera.giorni_pagamento).toBe(30)
    expect(lettera.giorni_preavviso).toBeNull()
  })

  it('reads a prefill with no fee as an empty box, not the word null', () => {
    const keys = [...LETTERA_TEXT_KEYS, 'data_inizio', 'data_fine', 'compenso', 'giorni_pagamento', 'fine_mese', 'giorni_preavviso']
    const draft = Object.fromEntries(keys.map((key) => [key, null])) as unknown as LetteraDraft
    const form = letteraForm(draft)
    expect(form.compenso).toBe('')
    expect(form.giorni_pagamento).toBe('')
    expect(form.fine_mese).toBe(false)
  })

  it('trims the client data', () => {
    expect(toCliente({ cliente_ragione_sociale: ' ACME S.r.l. ', cliente_piva: ' 01234567890', cliente_sede: 'Milano ' })).toEqual({
      cliente_ragione_sociale: 'ACME S.r.l.',
      cliente_piva: '01234567890',
      cliente_sede: 'Milano',
    })
  })
})
```

- [ ] **Step 2: Write the failing page tests** (`CreaMatch.test.tsx`)

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LETTERA_TEXT_KEYS } from '@/lib/api'
import { AdminCreaMatch } from './CreaMatch'

function answer(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

type Handler = unknown
/** Routes by `"<method> <url>"`; a handler may be a function of the request and may
 *  answer a whole `Response` (the previews are PDFs, not JSON). */
function routeFetch(handlers: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    if (!(key in handlers)) throw new Error(`unhandled fetch in this test: ${key}`)
    const handler = handlers[key]
    const body = typeof handler === 'function' ? (handler as (init?: RequestInit) => unknown)(init) : handler
    return body instanceof Response ? body : answer(200, body)
  })
}

const pdf = () => new Response('%PDF-1.7 anteprima', { status: 200, headers: { 'Content-Type': 'application/pdf' } })

const PERSON = { id: 'f1', nome: 'Ada', cognome: 'Lovelace', email: 'ada@studio.it' }
const OPEN = {
  id: 'c1',
  nome_azienda: 'Rossi Studio',
  referente: 'Mario Rossi',
  email: 'mario@rossi.it',
  telefono: null,
  figura_richiesta: 'Backend developer',
  progetto: 'Piattaforma di prenotazione',
  periodo_da: '2026-10-01',
  durata: '3 mesi',
  budget_giornaliero: '777.77',
  remoto: 'remoto',
  giorni_presenza: null,
  numero_risorse: 1,
  stato: 'nuovo',
  note: null,
  origine: null,
  utm_source: null,
  created_at: '2026-09-10T10:00:00Z',
  commenti: [],
  deleted_at: null,
}
const CLOSED = { ...OPEN, id: 'c2', nome_azienda: 'Bianchi Srl', stato: 'chiuso' }
const FISCALE = {
  freelancer_id: 'f1',
  codice_fiscale: 'LVLDAA85T50H501Z',
  partita_iva: '01234567890',
  domicilio: 'Via Roma 1, Milano',
  pec: null,
  updated_by: 'a1',
  updated_at: '2026-09-23T10:00:00Z',
}
function prefill(compenso: string | null) {
  return {
    fiscale: FISCALE,
    cliente: { cliente_ragione_sociale: 'Rossi Studio', cliente_piva: null, cliente_sede: null },
    lettera: {
      ...Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, null])),
      ruolo: 'Backend developer',
      attivita: 'Piattaforma di prenotazione',
      impegno: '3 mesi',
      luogo: 'da remoto',
      referente_cliente: 'Mario Rossi',
      modalita: 'a giornata',
      unita: 'a giornata',
      data_inizio: '2026-10-01',
      data_fine: null,
      compenso,
      giorni_pagamento: 30,
      fine_mese: true,
      giorni_preavviso: null,
    },
    quadro_attivo: null,
    quadro_necessario: true,
    lettera_in_attesa: true,
  }
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const nuovo = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/match/new',
    component: AdminCreaMatch,
  })
  const contratti = createRoute({
    getParentRoute: () => signedIn,
    path: '/admin/freelance/$id/contracts',
    component: () => <p>pagina contratti</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([nuovo, contratti])]),
    history: createMemoryHistory({ initialEntries: ['/admin/freelance/f1/match/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

let made = 0
beforeEach(() => {
  made = 0
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => `blob:anteprima-${++made}`) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
})
afterEach(() => vi.restoreAllMocks())

async function throughTheFirstThreeSteps() {
  await userEvent.click(await screen.findByRole('button', { name: /Rossi Studio/ }))
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  expect(await screen.findByLabelText('Codice fiscale')).toHaveValue('LVLDAA85T50H501Z')
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
  expect(await screen.findByLabelText('Ragione sociale del cliente')).toHaveValue('Rossi Studio')
  await userEvent.type(screen.getByLabelText('Partita IVA del cliente'), '09876543210')
  await userEvent.type(screen.getByLabelText('Sede del cliente'), 'Milano')
  await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
}

describe('«Crea match» in five steps (REB-387)', () => {
  it('walks from a request to a draft, previews both documents and saves the draft', async () => {
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 2, items: [OPEN, CLOSED], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill('450.00'),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
      'POST /api/hub/freelancers/f1/matches/preview?documento=quadro': pdf,
      'POST /api/hub/freelancers/f1/matches': { id: 'm1' },
    })
    mount()
    expect(await screen.findByRole('button', { name: /Bianchi Srl/ })).toBeDisabled()
    expect(screen.getByText('1. Azienda')).toHaveAttribute('aria-current', 'step')
    await throughTheFirstThreeSteps()

    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(String(put[1]!.body))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Via Roma 1, Milano',
      pec: null,
    })
    expect(await screen.findByLabelText('Compenso, IVA esclusa (€)')).toHaveValue('450.00')
    expect(screen.getByLabelText('Ruolo')).toHaveValue('Backend developer')
    expect(document.body.textContent).not.toMatch(/777\.77/)

    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('link', { name: 'Apri la lettera di incarico' })).toHaveAttribute('href', 'blob:anteprima-1')
    expect(screen.getByRole('link', { name: 'Apri il contratto quadro' })).toHaveAttribute('href', 'blob:anteprima-2')
    expect(screen.getByText(/partirà per primo il contratto quadro/)).toBeInTheDocument()
    const send = screen.getByRole('button', { name: 'Invia per la firma' })
    expect(send).toBeDisabled()
    await userEvent.hover(send.parentElement!)
    expect((await screen.findAllByText('Arriva con la firma elettronica')).length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toMatch(/777\.77/)

    await userEvent.click(screen.getByRole('button', { name: 'Salva come bozza' }))
    expect(await screen.findByText('pagina contratti')).toBeInTheDocument()
    const created = spy.mock.calls.find(([url, init]) => url === '/api/hub/freelancers/f1/matches' && init?.method === 'POST')!
    const body = JSON.parse(String(created[1]!.body))
    expect(body.company_id).toBe('c1')
    expect(body.cliente).toEqual({ cliente_ragione_sociale: 'Rossi Studio', cliente_piva: '09876543210', cliente_sede: 'Milano' })
    expect(body.lettera).toMatchObject({ ruolo: 'Backend developer', compenso: '450.00', giorni_pagamento: 30, fine_mese: true, risultati: null })
    expect(JSON.stringify(body)).not.toMatch(/budget|777\.77/)
  })

  it('shows an empty, required fee for a card without a day rate and names the fee the server refused', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill(null),
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': () =>
        answer(422, { detail: [{ loc: ['body', 'lettera', 'compenso'], msg: 'Input should be greater than or equal to 1' }] }),
    })
    mount()
    await throughTheFirstThreeSteps()
    const fee = await screen.findByLabelText('Compenso, IVA esclusa (€)')
    expect(fee).toHaveValue('')
    expect(fee).toBeRequired()
    await userEvent.type(fee, '0')
    await userEvent.click(screen.getByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be greater than or equal to 1')
    expect(screen.getByLabelText('Compenso, IVA esclusa (€)')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('button', { name: 'Salva come bozza' })).toBeNull()
  })

  it('goes back from the preview to the letter and forgets the stale preview', async () => {
    routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/companies?limit=50': { totale: 1, items: [OPEN], per_stato: {}, next_cursor: null },
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': { ...prefill('450.00'), quadro_necessario: false, lettera_in_attesa: false },
      'PUT /api/hub/freelancers/f1/fiscal': FISCALE,
      'POST /api/hub/freelancers/f1/matches/preview?documento=lettera': pdf,
    })
    mount()
    await throughTheFirstThreeSteps()
    await userEvent.click(await screen.findByRole('button', { name: 'Genera l’anteprima' }))
    expect(await screen.findByText(/partirà solo la lettera di incarico/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Apri il contratto quadro' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    expect(await screen.findByLabelText('Ruolo')).toBeInTheDocument()
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:anteprima-1'))
  })
})
```

Append to `lists.test.tsx`'s REB-387 block (and add a stub route `/admin/freelance/$id/match/new` to its `mount`, like the `contracts` stub):

```tsx
  it('offers «Crea match» first in a card row menu', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(answer(200, { totale: 1, items: [CARD_TALENTO], per_stato: {} }))
    mount('/admin/talent')
    await userEvent.click(await screen.findByRole('button', { name: 'Azioni per Ada Lovelace' }))
    const items = await screen.findAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual(['Crea match', 'Match e contratti'])
    expect(items[0]!.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })
```

Append to `Contratti.test.tsx` (and add the same stub route to its `mount`):

```tsx
  it('starts a new match from the page', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': PAGE })
    mount('/admin/freelance/f1/contracts')
    const link = await screen.findByRole('link', { name: 'Crea match' })
    expect(link.getAttribute('href')).toMatch(/\/admin\/freelance\/f1\/match\/new$/)
  })
```

- [ ] **Step 3: Run them to see them fail**

Run: `pnpm --filter hub exec vitest run src/lib/contracts.test.ts src/pages/admin/CreaMatch.test.tsx src/pages/admin/lists.test.tsx src/pages/admin/Contratti.test.tsx`
Expected: FAIL (`LETTERA_TEXT_KEYS` is not exported, `./CreaMatch` does not resolve, no «Crea match» item or link).

- [ ] **Step 4: `api.ts`**: the blob request, the letter types, three calls

After `request` (line 56):

```ts
/** A file the API answers, a preview PDF: the same error handling as `request`. */
async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) await fail(response)
  return response.blob()
}
```

After `FreelancerContracts`:

```ts
/** The letter's text fields, in the order `lettera-di-incarico.md` asks for them and the
 *  server's `LETTERA_TEXT_FIELDS` lists them. */
export const LETTERA_TEXT_KEYS = [
  'ruolo',
  'attivita',
  'risultati',
  'accettazione',
  'impegno',
  'periodo_verifica',
  'luogo',
  'coordinamento',
  'referente_cliente',
  'referente_rebase',
  'modalita',
  'unita',
  'lavoro_extra',
  'spese',
  'scadenze_fatturazione',
  'dati_personali',
  'dati_finalita',
  'dati_categorie',
  'dati_interessati',
  'dati_autorizzazione',
  'esclusiva',
  'portfolio',
  'assicurazione',
  'altre_condizioni',
  'rapporti_precedenti',
] as const
export type LetteraTextKey = (typeof LETTERA_TEXT_KEYS)[number]

/** What `LetteraFields` takes: an empty text field is `null` and prints a blank line. */
export type Lettera = Record<LetteraTextKey, string | null> & {
  data_inizio: string
  data_fine: string | null
  compenso: string
  giorni_pagamento: number
  fine_mese: boolean
  giorni_preavviso: number | null
}
export type LetteraDraft = { [K in keyof Lettera]: Lettera[K] | null }

export interface Cliente {
  cliente_ragione_sociale: string
  cliente_piva: string
  cliente_sede: string
}
export type ClienteDraft = { [K in keyof Cliente]: string | null }

export interface MatchCreate {
  company_id: string
  cliente: Cliente
  lettera: Lettera
}

export interface MatchPrefill {
  fiscale: Fiscal | null
  cliente: ClienteDraft
  lettera: LetteraDraft
  quadro_attivo: ContractDocument | null
  quadro_necessario: boolean
  lettera_in_attesa: boolean
}
```

Inside `admin`:

```ts
  matchPrefill: (freelancerId: string, companyId: string) =>
    request<MatchPrefill>(
      `/api/hub/freelancers/${freelancerId}/matches/prefill?company_id=${encodeURIComponent(companyId)}`,
    ),
  /** Step 5's preview: a PDF typeset now and saved nowhere. */
  matchPreview: (freelancerId: string, payload: MatchCreate, documento: 'lettera' | 'quadro') =>
    requestBlob(`/api/hub/freelancers/${freelancerId}/matches/preview?documento=${documento}`, json(payload)),
  /** «Salva come bozza»: the draft match with its numbered letter. */
  createMatch: (freelancerId: string, payload: MatchCreate) =>
    request<Match>(`/api/hub/freelancers/${freelancerId}/matches`, json(payload)),
```

- [ ] **Step 5: `lib/contracts.ts`**, append

```ts
import { LETTERA_TEXT_KEYS, type Cliente, type ClienteDraft, type Lettera, type LetteraDraft, type LetteraTextKey } from './api'

export type ClienteForm = Record<keyof Cliente, string>
export const CLIENTE_EMPTY: ClienteForm = { cliente_ragione_sociale: '', cliente_piva: '', cliente_sede: '' }

export function clienteForm(draft: ClienteDraft): ClienteForm {
  return {
    cliente_ragione_sociale: draft.cliente_ragione_sociale ?? '',
    cliente_piva: draft.cliente_piva ?? '',
    cliente_sede: draft.cliente_sede ?? '',
  }
}

export function toCliente(form: ClienteForm): Cliente {
  return {
    cliente_ragione_sociale: form.cliente_ragione_sociale.trim(),
    cliente_piva: form.cliente_piva.trim(),
    cliente_sede: form.cliente_sede.trim(),
  }
}

export type LetteraForm = Record<LetteraTextKey, string> & {
  data_inizio: string
  data_fine: string
  compenso: string
  giorni_pagamento: string
  fine_mese: boolean
  giorni_preavviso: string
}
export type LetteraFieldKey = keyof LetteraForm

const TEXT_EMPTY = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, ''])) as Record<LetteraTextKey, string>

export const LETTERA_EMPTY: LetteraForm = {
  ...TEXT_EMPTY,
  data_inizio: '',
  data_fine: '',
  compenso: '',
  giorni_pagamento: '',
  fine_mese: false,
  giorni_preavviso: '',
}

export function letteraForm(draft: LetteraDraft): LetteraForm {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, draft[key] ?? ''])) as Record<LetteraTextKey, string>
  return {
    ...text,
    data_inizio: draft.data_inizio ?? '',
    data_fine: draft.data_fine ?? '',
    compenso: draft.compenso ?? '',
    giorni_pagamento: draft.giorni_pagamento === null ? '' : String(draft.giorni_pagamento),
    fine_mese: draft.fine_mese ?? false,
    giorni_preavviso: draft.giorni_preavviso === null ? '' : String(draft.giorni_preavviso),
  }
}

export function toLettera(form: LetteraForm): Lettera {
  const text = Object.fromEntries(LETTERA_TEXT_KEYS.map((key) => [key, form[key].trim() || null])) as Record<
    LetteraTextKey,
    string | null
  >
  return {
    ...text,
    data_inizio: form.data_inizio,
    data_fine: form.data_fine || null,
    compenso: form.compenso.replace(',', '.').trim(),
    giorni_pagamento: Number(form.giorni_pagamento),
    fine_mese: form.fine_mese,
    giorni_preavviso: form.giorni_preavviso.trim() ? Number(form.giorni_preavviso) : null,
  }
}

/** The letter's fields as step 4 names them. The client's budget has no label here,
 *  because it has no field anywhere in this flow (spec § 1h). */
export const LETTERA_LABELS: Record<LetteraFieldKey, string> = {
  ruolo: 'Ruolo',
  attivita: 'Cosa fa il professionista',
  risultati: 'Risultati da consegnare, solo a corpo',
  accettazione: 'Come il cliente accetta i risultati, solo a corpo',
  data_inizio: 'Inizio',
  data_fine: 'Fine prevista',
  impegno: 'Impegno',
  periodo_verifica: 'Periodo iniziale di verifica',
  luogo: 'Luogo',
  coordinamento: 'Coordinamento concordato con il cliente',
  referente_cliente: 'Referente del cliente',
  referente_rebase: 'Referente di rebase',
  modalita: 'Modalità',
  compenso: 'Compenso, IVA esclusa (€)',
  unita: 'Unità del compenso',
  lavoro_extra: 'Lavoro festivo o fuori fascia',
  spese: 'Spese',
  giorni_pagamento: 'Giorni di pagamento',
  fine_mese: 'Contati da fine mese',
  scadenze_fatturazione: 'Fatture, solo a corpo',
  giorni_preavviso: 'Giorni di preavviso',
  dati_personali: 'Tratta dati personali del cliente',
  dati_finalita: 'Natura e finalità del trattamento',
  dati_categorie: 'Categorie di dati',
  dati_interessati: 'Categorie di interessati',
  dati_autorizzazione: 'Autorizzazione scritta del cliente alla nomina',
  esclusiva: 'Esclusiva verso il cliente',
  portfolio: 'Citazione nel portfolio',
  assicurazione: 'Assicurazione di responsabilità civile professionale',
  altre_condizioni: 'Altre condizioni',
  rapporti_precedenti: 'Rapporti precedenti con il cliente',
}

/** Step 4 in the letter's own sections. */
export const LETTERA_GROUPS: readonly { title: string; fields: readonly LetteraFieldKey[] }[] = [
  { title: 'Attività', fields: ['ruolo', 'attivita', 'risultati', 'accettazione'] },
  { title: 'Tempi e impegno', fields: ['data_inizio', 'data_fine', 'impegno', 'periodo_verifica'] },
  { title: 'Modalità di lavoro', fields: ['luogo', 'coordinamento', 'referente_cliente', 'referente_rebase'] },
  {
    title: 'Condizioni economiche',
    fields: ['modalita', 'compenso', 'unita', 'lavoro_extra', 'spese', 'giorni_pagamento', 'fine_mese', 'scadenze_fatturazione'],
  },
  { title: 'Preavviso', fields: ['giorni_preavviso'] },
  {
    title: 'Dati personali',
    fields: ['dati_personali', 'dati_finalita', 'dati_categorie', 'dati_interessati', 'dati_autorizzazione'],
  },
  { title: 'Condizioni particolari', fields: ['esclusiva', 'portfolio', 'assicurazione', 'altre_condizioni'] },
  { title: 'Rapporti precedenti', fields: ['rapporti_precedenti'] },
]

export const LETTERA_REQUIRED: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'ruolo',
  'attivita',
  'data_inizio',
  'compenso',
  'giorni_pagamento',
])
export const LETTERA_MULTILINE: ReadonlySet<LetteraFieldKey> = new Set<LetteraFieldKey>([
  'attivita',
  'risultati',
  'accettazione',
  'dati_finalita',
  'altre_condizioni',
])
```

Move the new `import` line to the top of the file, merged with the existing `import type { Fiscal, FiscalData } from './api'`.

- [ ] **Step 6: `pages/admin/CreaMatch.tsx`**

```tsx
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@rebase/ui/tooltip'
import { admin, ApiError, type Company, type FiscalData, type MatchCreate, type MatchPrefill } from '@/lib/api'
import {
  CLIENTE_EMPTY,
  FISCAL_EMPTY,
  LETTERA_EMPTY,
  LETTERA_GROUPS,
  LETTERA_LABELS,
  LETTERA_MULTILINE,
  LETTERA_REQUIRED,
  clienteForm,
  draftFromFiscal,
  letteraForm,
  toCliente,
  toFiscalData,
  toLettera,
  type ClienteForm,
  type FiscalDraft,
  type LetteraFieldKey,
  type LetteraForm,
} from '@/lib/contracts'
import { formatDate } from '@/lib/format'
import { FiscalFields } from './Contratti'
import { Header } from './lists'

const STEPS = ['Azienda', 'Freelance', 'Cliente', 'Lettera di incarico', 'Anteprima'] as const
const SEARCH_DEBOUNCE_MS = 300

interface Previews {
  lettera: string
  quadro: string | null
}

interface Failure {
  message: string
  fields: string[]
}

function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function failureOf(error: unknown, fallback: string): Failure | null {
  if (!error) return null
  if (error instanceof ApiError) return { message: error.message, fields: error.fields }
  return { message: fallback, fields: [] }
}

function StepFooter({
  onBack,
  next,
  pending,
  ready = true,
  failure,
}: {
  onBack?: () => void
  next: string
  pending: boolean
  ready?: boolean
  failure: Failure | null
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {onBack && (
          <Button type="button" variant="outline" onClick={onBack}>
            Indietro
          </Button>
        )}
        <Button type="submit" disabled={pending || !ready}>
          {pending ? 'Un momento…' : next}
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

function CompanyStep({
  selected,
  onSelect,
  onNext,
  pending,
  failure,
}: {
  selected: Company | null
  onSelect: (company: Company) => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  const [q, setQ] = useState('')
  const term = useDebounce(q.trim(), SEARCH_DEBOUNCE_MS)
  const companies = useQuery({
    queryKey: ['companies', 'match', term],
    queryFn: () => admin.companies({ q: term || undefined, limit: 50 }),
  })
  let list: ReactNode
  if (companies.isError) list = <p className="text-sm text-destructive">Non riesco a leggere le richieste.</p>
  else if (companies.isPending) list = <p className="text-sm text-muted-foreground">Caricamento…</p>
  else if (companies.data.items.length === 0) list = <p className="text-sm text-muted-foreground">Nessuna richiesta trovata.</p>
  else
    list = (
      <ul className="space-y-2">
        {companies.data.items.map((item) => {
          const closed = item.stato === 'chiuso'
          const chosen = selected?.id === item.id
          return (
            <li key={item.id}>
              <Button
                type="button"
                variant={chosen ? 'default' : 'outline'}
                aria-pressed={chosen}
                disabled={closed}
                onClick={() => onSelect(item)}
                className={cn('h-auto w-full justify-start whitespace-normal py-2 text-left', closed && 'opacity-50')}
              >
                <span className="flex flex-col items-start gap-0.5">
                  <span className="font-medium">
                    {item.nome_azienda}
                    {closed && ' · chiusa'}
                  </span>
                  <span className="text-xs">
                    {item.referente} · {item.figura_richiesta} · dal {formatDate(item.periodo_da)}
                  </span>
                </span>
              </Button>
            </li>
          )
        })}
      </ul>
    )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="match-azienda-q">Cerca una richiesta</Label>
        <Input
          id="match-azienda-q"
          type="search"
          maxLength={200}
          placeholder="Azienda, referente, email…"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>
      {list}
      <StepFooter next="Avanti" pending={pending} ready={selected !== null} failure={failure} />
    </form>
  )
}

function ClienteStep({
  form,
  onChange,
  onBack,
  onNext,
}: {
  form: ClienteForm
  onChange: (form: ClienteForm) => void
  onBack: () => void
  onNext: () => void
}) {
  const field = (name: keyof ClienteForm, label: string, maxLength: number) => (
    <div className="space-y-1.5">
      <Label htmlFor={`match-${name}`}>{label}</Label>
      <Input
        id={`match-${name}`}
        required
        maxLength={maxLength}
        value={form[name]}
        onChange={(event) => onChange({ ...form, [name]: event.target.value })}
      />
    </div>
  )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      <p className="text-sm text-muted-foreground">Il cliente come lo stampa la lettera di incarico.</p>
      {field('cliente_ragione_sociale', 'Ragione sociale del cliente', 200)}
      {field('cliente_piva', 'Partita IVA del cliente', 32)}
      {field('cliente_sede', 'Sede del cliente', 300)}
      <StepFooter onBack={onBack} next="Avanti" pending={false} failure={null} />
    </form>
  )
}

function LetteraInput({
  field,
  form,
  onChange,
  invalid,
}: {
  field: LetteraFieldKey
  form: LetteraForm
  onChange: (form: LetteraForm) => void
  invalid: (field: string) => true | undefined
}) {
  const inputId = `lettera-${field}`
  if (field === 'fine_mese') {
    return (
      <div className="flex items-center gap-2">
        <Checkbox
          id={inputId}
          checked={form.fine_mese}
          onCheckedChange={(checked) => onChange({ ...form, fine_mese: checked === true })}
        />
        <Label htmlFor={inputId} className="font-normal">
          {LETTERA_LABELS.fine_mese}
        </Label>
      </div>
    )
  }
  const value = form[field]
  const set = (next: string) => onChange({ ...form, [field]: next })
  const type = field === 'data_inizio' || field === 'data_fine' ? 'date' : field === 'giorni_pagamento' || field === 'giorni_preavviso' ? 'number' : 'text'
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{LETTERA_LABELS[field]}</Label>
      {LETTERA_MULTILINE.has(field) ? (
        <Textarea
          id={inputId}
          rows={3}
          required={LETTERA_REQUIRED.has(field)}
          value={value}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      ) : (
        <Input
          id={inputId}
          type={type}
          inputMode={field === 'compenso' ? 'decimal' : undefined}
          required={LETTERA_REQUIRED.has(field)}
          value={value}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      )}
    </div>
  )
}

function PreviewStep({
  previews,
  prefill,
  onBack,
  onSave,
  saving,
  failure,
}: {
  previews: Previews
  prefill: MatchPrefill
  onBack: () => void
  onSave: () => void
  saving: boolean
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
      <p className="text-sm text-muted-foreground">Per ora si salva una bozza: nulla viene inviato.</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={onBack}>
          Indietro
        </Button>
        <Button type="button" onClick={onSave} disabled={saving}>
          {saving ? 'Salvo…' : 'Salva come bozza'}
        </Button>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="inline-flex">
                <Button type="button" disabled>
                  Invia per la firma
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>Arriva con la firma elettronica</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure.message}
        </p>
      )}
    </div>
  )
}

/** «Crea match» (REB-387, phase 2): five steps from a card to a draft match with its
 *  documents. The tax data are saved when the admin leaves step 2; step 5 shows previews
 *  that nothing stores; «Salva come bozza» writes the match and takes the letter's
 *  number. «Invia per la firma» arrives with the electronic signature (phase 3). The
 *  company's `budget_giornaliero` is never shown or sent from this page (spec § 1h). */
export function AdminCreaMatch() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/match/new' })
  const navigate = useNavigate()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const [step, setStep] = useState(0)
  const [company, setCompany] = useState<Company | null>(null)
  const [prefill, setPrefill] = useState<MatchPrefill | null>(null)
  const [fiscal, setFiscal] = useState<FiscalDraft>(FISCAL_EMPTY)
  const [cliente, setCliente] = useState<ClienteForm>(CLIENTE_EMPTY)
  const [lettera, setLettera] = useState<LetteraForm>(LETTERA_EMPTY)
  const [previews, setPreviews] = useState<Previews | null>(null)

  // A preview is a blob in this tab's memory: let it go once replaced, or with the page.
  useEffect(() => {
    if (!previews) return
    return () => {
      URL.revokeObjectURL(previews.lettera)
      if (previews.quadro) URL.revokeObjectURL(previews.quadro)
    }
  }, [previews])

  const loadPrefill = useMutation({
    mutationFn: (companyId: string) => admin.matchPrefill(id, companyId),
    onSuccess: (data) => {
      setPrefill(data)
      setFiscal(draftFromFiscal(data.fiscale))
      setCliente(clienteForm(data.cliente))
      setLettera(letteraForm(data.lettera))
      setStep(1)
    },
  })
  const saveFiscal = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(id, data),
    onSuccess: () => setStep(2),
  })
  const generate = useMutation({
    mutationFn: async (payload: MatchCreate): Promise<Previews> => {
      const letter = await admin.matchPreview(id, payload, 'lettera')
      const quadro = prefill?.quadro_necessario ? await admin.matchPreview(id, payload, 'quadro') : null
      return { lettera: URL.createObjectURL(letter), quadro: quadro ? URL.createObjectURL(quadro) : null }
    },
    onSuccess: (made) => {
      setPreviews(made)
      setStep(4)
    },
  })
  const save = useMutation({
    mutationFn: (payload: MatchCreate) => admin.createMatch(id, payload),
    onSuccess: () => void navigate({ to: '/admin/freelance/$id/contracts', params: { id } }),
  })

  const payload = (): MatchCreate | null =>
    company ? { company_id: company.id, cliente: toCliente(cliente), lettera: toLettera(lettera) } : null
  const fiscalFailure = failureOf(saveFiscal.error, 'Non riesco a salvare i dati fiscali.')
  const letterFailure = failureOf(generate.error, 'Non riesco a generare l’anteprima.')
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''

  function submitLettera(event: FormEvent) {
    event.preventDefault()
    const body = payload()
    if (body) generate.mutate(body)
  }

  return (
    <>
      <Header title={name ? `Crea match · ${name}` : 'Crea match'} />
      <ol aria-label="Passi" className="flex flex-wrap gap-x-4 gap-y-1 border-b px-6 py-3 text-sm">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? 'step' : undefined}
            className={cn(index === step ? 'font-medium' : 'text-muted-foreground')}
          >
            {index + 1}. {label}
          </li>
        ))}
      </ol>
      <div className="max-w-3xl space-y-4 p-6">
        {step === 0 && (
          <CompanyStep
            selected={company}
            onSelect={setCompany}
            onNext={() => company && loadPrefill.mutate(company.id)}
            pending={loadPrefill.isPending}
            failure={failureOf(loadPrefill.error, 'Non riesco a leggere questa richiesta.')}
          />
        )}
        {step === 1 && (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              saveFiscal.mutate(toFiscalData(fiscal))
            }}
          >
            <p className="text-sm text-muted-foreground">
              I dati fiscali del freelance, come li stampano i contratti: salvati qui, restano per il prossimo match.
            </p>
            <FiscalFields
              idPrefix="match"
              draft={fiscal}
              onChange={setFiscal}
              wrong={(field) => fiscalFailure?.fields.includes(field) || undefined}
            />
            <StepFooter onBack={() => setStep(0)} next="Avanti" pending={saveFiscal.isPending} failure={fiscalFailure} />
          </form>
        )}
        {step === 2 && (
          <ClienteStep form={cliente} onChange={setCliente} onBack={() => setStep(1)} onNext={() => setStep(3)} />
        )}
        {step === 3 && (
          <form className="space-y-6" onSubmit={submitLettera}>
            {LETTERA_GROUPS.map((group) => (
              <fieldset key={group.title} className="space-y-3">
                <legend className="text-sm font-medium">{group.title}</legend>
                {group.fields.map((field) => (
                  <LetteraInput
                    key={field}
                    field={field}
                    form={lettera}
                    onChange={setLettera}
                    invalid={(key) => letterFailure?.fields.includes(key) || undefined}
                  />
                ))}
              </fieldset>
            ))}
            <StepFooter onBack={() => setStep(2)} next="Genera l’anteprima" pending={generate.isPending} failure={letterFailure} />
          </form>
        )}
        {step === 4 && previews && prefill && (
          <PreviewStep
            previews={previews}
            prefill={prefill}
            onBack={() => {
              setPreviews(null)
              setStep(3)
            }}
            onSave={() => {
              const body = payload()
              if (body) save.mutate(body)
            }}
            saving={save.isPending}
            failure={failureOf(save.error, 'Non riesco a salvare la bozza.')}
          />
        )}
      </div>
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id/contracts" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Match e contratti
        </Link>
      </p>
    </>
  )
}
```

- [ ] **Step 7: The route, the menu item, the page button**

`router.tsx`: `import { AdminCreaMatch } from '@/pages/admin/CreaMatch'` and, after `adminFreelanceContracts`,

```tsx
const adminFreelanceMatchNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id/match/new',
  component: AdminCreaMatch,
})
```

added to `adminArea.addChildren([...])` after `adminFreelanceContracts`.

`lists.tsx`, `TalentoMenu`: before the «Match e contratti» item, add

```tsx
        <DropdownMenuItem asChild>
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </DropdownMenuItem>
```

`Contratti.tsx`, in `AdminContratti`, give `Header` a child:

```tsx
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'}>
        <Button asChild size="sm">
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </Button>
      </Header>
```

- [ ] **Step 8: Run the web checks**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add projects/hub/apps/web/src/lib/api.ts projects/hub/apps/web/src/lib/contracts.ts \
  projects/hub/apps/web/src/lib/contracts.test.ts projects/hub/apps/web/src/router.tsx \
  projects/hub/apps/web/src/pages/admin/CreaMatch.tsx projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx \
  projects/hub/apps/web/src/pages/admin/lists.tsx projects/hub/apps/web/src/pages/admin/lists.test.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.tsx projects/hub/apps/web/src/pages/admin/Contratti.test.tsx
git commit -F - <<'EOF'
feat(hub): «Crea match», five steps from a card to a draft with its contracts

I add the page that pairs a card with a company request: pick the request
(a closed one is greyed), save the freelancer's tax data for next time,
check the client, fill the letter prefilled from the request and the card,
then open the previews of the letter and, when needed, of the framework
agreement. «Salva come bozza» writes the draft and its number; «Invia per
la firma» is there, disabled, until the electronic signature arrives. The
client's budget is shown and sent nowhere. The talent row menu and the
contracts page both open it.

REB-N.
EOF
```

---

## Self-Review

**Spec coverage (phase 2, § 10).** Migration 0017 and models: Task 3. The core service: Tasks 4-5 (match creation, tax data, prefill from the request and the freelancer, framework only when none is active, `YYYY-NNN` numbers, states `bozza`/`annullato`/`concluso` and `generato`/`in_attesa`, the active computation, audit kinds). The runtime renderer with package data, the CLI kept, `REBASE_SIGNER_JSON`, the Typst metadata and `typst query`: Tasks 1-2. The image with pandoc, Typst, fontTools and the brand: Task 2. Admin routes and PDF downloads: Task 6. MCP read tools: Task 6. Row menu on card rows only, «Match e contratti» without signing actions, the five steps with «Salva come bozza» and a disabled «Invia per la firma» with its tooltip: Tasks 7-8. § 9's `DELETE FROM` lists: every new fixture. Not here by design: Documenso, mail attachments, the webhook, sending, resending, «Aggiorna stato», «Registra disdetta», the member section, the compose services (phases 3 and 4).

**Placeholders.** None: `REB-N` is the controller's card id by the Global Constraints, not a gap.

**Type consistency.** `Renderer.render(document, data) -> Rendered` is the same in Tasks 1, 5 and 6; `MatchService(session, renderer, signer, today)` in Tasks 5 and 6; `ContractDocumentRead` fields match `ContractDocument` in `api.ts`; `LETTERA_TEXT_FIELDS` (Python) and `LETTERA_TEXT_KEYS` (TS) list the same 25 names in the same order; route id `/signedIn/admin/freelance/$id/contracts` and `.../match/new` match `router.tsx` and the tests' own trees.

**Review Focus.** Each of the five lines has its test in the owning task (Tasks 4, 5, 5, 5 and 1, and Task 8 for the fee at the UI).

## Execution

Plan complete and saved to `projects/hub/docs/superpowers/plans/2026-09-23-matches-phase-2-match-and-generate.md`. Execution is subagent-driven, one fresh implementer per task and one Linear card per task, as already chosen. Please review the plan: does it capture what you want?
