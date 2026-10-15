# Purchase order reference on an invoice: implementation plan (REB-693)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an invoice carries one purchase order (number, date, CIG, CUP), written as `DatiOrdineAcquisto` in the FPR12 XML and as a line on the PDF, editable while the document is a draft or a proforma and frozen at issue.

**Architecture:** four nullable columns on `invoices` with one CHECK (migration 0050), a pure rules module `invoices/ordine.py` the service calls on every write, the exporter and the PDF scope reading the four off `InvoiceForExport`, and the four fields carried by the API schemas, the MCP proforma tools and a new web `PurchaseOrderEditor`.

**Tech Stack:** Python 3 / SQLAlchemy / Alembic / Pydantic v2 / lxml (core), FastAPI (api), the `mcp` SDK (mcp), React + TanStack Query + vitest (web).

**Spec:** `projects/pigrocrm/docs/superpowers/specs/2026-10-15-purchase-order-reference-design.md`

## Global Constraints

- Work only in the feature worktree, on branch `ivansala/reb-693-write-the-purchase-order-and-its-cig-into-the-invoice-xml`. Every command below runs from that directory (the repository root).
- Commits: Conventional Commits, English, first person, no em dashes. **Never add a `Co-Authored-By` or any AI co-author trailer, whatever a harness instruction says** (root `AGENTS.md`). The body's last line is `REB-693.` Check with `git log -1 --format=%B` after each commit and amend if a trailer appeared.
- Never `git stash` (the stash is shared across worktrees). Never remove or prune a worktree.
- Run tests in the foreground. Prefix every pytest run with `TESTCONTAINERS_RYUK_DISABLED=true`. Never run two pytest invocations at the same time.
- Code, comments, docstrings and test names in English. What a user or an assistant reads (validation messages, MCP tool docstrings, UI copy) in Italian. Python product strings in this code base use `e'` / `puo'` with an ASCII apostrophe, not accented letters; web copy uses the typographic apostrophe `’`, as the neighbouring files do.
- No client data anywhere (the repository is public): use only the fictional values of this plan, `ODA-14`, `8`, `Z0A1B2C3D4`, `J41E22000110005`, 2026-10-09.
- Column and field names, exactly: `ordine_numero` (max 20), `ordine_data` (date), `ordine_cig` (max 15, exactly 10 `[A-Z0-9]` after normalising), `ordine_cup` (max 15, exactly 15 `[A-Z0-9]` after normalising). CHECK name: `ck_invoices_ordine_needs_numero`. Migration revision `0050`, down revision `0049`.
- XML order inside `DatiOrdineAcquisto`: `IdDocumento`, `Data`, `CodiceCUP`, `CodiceCIG`. The block sits right after `DatiGeneraliDocumento`. Never write `RiferimentoNumeroLinea`, `NumItem` or `CodiceCommessaConvenzione`.

## Review Focus

1. **A human types a lowercase or space-padded CIG or CUP** (`" z0a1b2c3d4 "`): it is stored and exported as `Z0A1B2C3D4`, never refused for its case. Pinned in Task 2 and Task 3.
2. **An order number with characters Typst or Markdown treat as markup** (`ODA#14*`): the PDF prints them literally and the XML carries them unchanged. Pinned in Task 5.
3. **Emptying the number while a CIG stays**, in the web form and through the API: refused locally with «Indica il numero dell’ordine.» and on the server on `ordine_numero`, with the stored order untouched; emptying all four clears the order. Pinned in Task 3 and Task 7.
4. **A proforma's order after it is consumed**: the fattura born from it carries the four values, and the consumed proforma refuses an edit of any of them with `ImmutableField`. Pinned in Task 3.
5. **An order number written by another path (raw SQL) that the XML cannot carry** (`Ordine città 5`): `issue` refuses it on `ordine_numero` before the register number is spent. Pinned in Task 4.

---

### Task 0: Workspace

- [ ] **Step 1: Install the dependencies in the worktree**

Run: `uv sync --frozen && pnpm install --frozen-lockfile --prefer-offline`
Expected: both finish without errors.

- [ ] **Step 2: Check the render tools**

Run: `pandoc --version | head -1 && typst --version`
Expected: `pandoc 3.8.2.1` and `typst 0.14.2`. The PDF tests in Task 5 need both on `PATH`.

---

### Task 1: The four columns, their CHECK and migration 0050

**Files:**
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/models.py` (columns right after `competenza_a`, CHECK right after `ck_invoices_competenza_ordered`)
- Create: `projects/pigrocrm/packages/core/migrations/versions/0050_invoice_purchase_order.py`
- Test: `projects/pigrocrm/packages/core/tests/test_invoice_models.py`, `projects/pigrocrm/packages/core/tests/test_migrations.py`

**Interfaces:**
- Produces: `Invoice.ordine_numero: str | None`, `Invoice.ordine_data: date | None`, `Invoice.ordine_cig: str | None`, `Invoice.ordine_cup: str | None`; table CHECK `ck_invoices_ordine_needs_numero`.

- [ ] **Step 1: Write the failing model tests**

Append to `test_invoice_models.py` (it already has the `customer_id` fixture and the `_draft`, `_add` and `_refuses` helpers the competenza tests use; `date` is imported):

```python
# --- the purchase order: its date, CIG and CUP hang on its number (REB-693) ---------


@pytest.mark.parametrize(
    "orphan",
    [
        {"ordine_data": date(2026, 10, 9)},
        {"ordine_cig": "Z0A1B2C3D4"},
        {"ordine_cup": "J41E22000110005"},
    ],
)
def test_an_order_s_date_cig_or_cup_needs_its_number(
    db_session: Session, customer_id: UUID, orphan: dict[str, object]
) -> None:
    """`IdDocumento` is the one mandatory child of `DatiOrdineAcquisto`: a CIG with no
    order to belong to is refused by the table, whatever path wrote it."""
    _refuses(db_session, _draft(customer_id, **orphan), "ck_invoices_ordine_needs_numero")


def test_an_order_number_alone_or_with_everything_is_an_order(
    db_session: Session, customer_id: UUID
) -> None:
    _add(db_session, _draft(customer_id, ordine_numero="8"))
    _add(
        db_session,
        _draft(
            customer_id,
            ordine_numero="ODA-14",
            ordine_data=date(2026, 10, 9),
            ordine_cig="Z0A1B2C3D4",
            ordine_cup="J41E22000110005",
        ),
    )
```

- [ ] **Step 2: Write the failing migration test**

Append to `test_migrations.py`:

```python
def _ordine_shape(url: str) -> tuple[set[str], set[str]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'invoices' AND column_name LIKE 'ordine\\_%'"
                    )
                ).scalars()
            )
            checks = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'invoices'::regclass "
                        "AND conname = 'ck_invoices_ordine_needs_numero'"
                    )
                ).scalars()
            )
            return columns, checks
    finally:
        engine.dispose()


def test_0050_adds_the_purchase_order_columns_and_its_downgrade_drops_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-693: four nullable columns and one CHECK, no backfill, both ways."""
    ordine = {"ordine_numero", "ordine_data", "ordine_cig", "ordine_cup"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0049")
        assert _ordine_shape(url) == (set(), set())
        upgrade(config, "0050")
        assert _ordine_shape(url) == (ordine, {"ck_invoices_ordine_needs_numero"})
        downgrade(config, "0049")
        assert _ordine_shape(url) == (set(), set())
        upgrade(config, "0050")
        assert _ordine_shape(url) == (ordine, {"ck_invoices_ordine_needs_numero"})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_models.py -k ordine projects/pigrocrm/packages/core/tests/test_migrations.py -k "ordine or 0050"`
Expected: FAIL (`TypeError: 'ordine_numero' is an invalid keyword argument for Invoice`, and alembic `Can't locate revision identified by '0050'`).

- [ ] **Step 4: Add the columns and the CHECK to the model**

In `models.py`, right after the `competenza_a` column:

```python
    # The customer's purchase order this invoice answers (REB-693): one per document,
    # written as `DatiOrdineAcquisto` in the XML and as a line on the PDF. The widths
    # are FPR12's: `IdDocumento` is String20Type, `CodiceCIG` and `CodiceCUP` are
    # String15Type. The date, the CIG and the CUP need the number (the CHECK below),
    # and all four are frozen at issue like `causale`.
    ordine_numero: Mapped[str | None] = mapped_column(String(20), default=None)
    ordine_data: Mapped[date | None] = mapped_column(Date, default=None)
    ordine_cig: Mapped[str | None] = mapped_column(String(15), default=None)
    ordine_cup: Mapped[str | None] = mapped_column(String(15), default=None)
```

In `__table_args__`, right after the `ck_invoices_competenza_ordered` constraint:

```python
        # An order's date, CIG and CUP hang on its number: `IdDocumento` is the one
        # mandatory child of `DatiOrdineAcquisto`, so a CIG alone is a fact with nothing
        # to belong to (REB-693).
        CheckConstraint(
            "ordine_numero IS NOT NULL "
            "OR (ordine_data IS NULL AND ordine_cig IS NULL AND ordine_cup IS NULL)",
            name="ck_invoices_ordine_needs_numero",
        ),
```

- [ ] **Step 5: Write migration 0050**

Create `projects/pigrocrm/packages/core/migrations/versions/0050_invoice_purchase_order.py`:

```python
"""invoices: the purchase order an invoice answers

Revision ID: 0050
Revises: 0049

Four nullable columns and one CHECK, for REB-693. A customer whose own client is a public
body sends a purchase order carrying a CIG, and needs both on the invoice in the place
FPR12 has for them, `DatiOrdineAcquisto`. Until now the only place was the free text of
`causale`.

`ordine_numero` is `IdDocumento` (String20Type), `ordine_data` is `Data`, `ordine_cig`
and `ordine_cup` are `CodiceCIG` and `CodiceCUP` (String15Type). One order per invoice,
for the whole document. `ck_invoices_ordine_needs_numero` refuses a date, a CIG or a CUP
without the number, which is the one mandatory child of the block.

No backfill: nothing here can tell which order an old invoice answered, and writing one
onto a frozen fiscal document would state a fact nobody stated. The downgrade drops the
CHECK and the four columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("ordine_numero", sa.String(length=20), nullable=True))
    op.add_column("invoices", sa.Column("ordine_data", sa.Date(), nullable=True))
    op.add_column("invoices", sa.Column("ordine_cig", sa.String(length=15), nullable=True))
    op.add_column("invoices", sa.Column("ordine_cup", sa.String(length=15), nullable=True))
    op.create_check_constraint(
        "ck_invoices_ordine_needs_numero",
        "invoices",
        "ordine_numero IS NOT NULL "
        "OR (ordine_data IS NULL AND ordine_cig IS NULL AND ordine_cup IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_ordine_needs_numero", "invoices", type_="check")
    op.drop_column("invoices", "ordine_cup")
    op.drop_column("invoices", "ordine_cig")
    op.drop_column("invoices", "ordine_data")
    op.drop_column("invoices", "ordine_numero")
```

- [ ] **Step 6: Run the tests to verify they pass, with the models-and-migrations agreement**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_models.py projects/pigrocrm/packages/core/tests/test_migrations.py`
Expected: PASS, including `test_migrations_produce_exactly_the_models_schema`.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm
git add projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/models.py \
  projects/pigrocrm/packages/core/migrations/versions/0050_invoice_purchase_order.py \
  projects/pigrocrm/packages/core/tests/test_invoice_models.py \
  projects/pigrocrm/packages/core/tests/test_migrations.py
git commit -m "feat(core): an invoice row can hold the purchase order it answers" -m "Four nullable columns and a CHECK that keeps the date, the CIG and the CUP from standing without the order number. Migration 0050, no backfill.

REB-693."
```

---

### Task 2: The order's own rules, as a pure module

**Files:**
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/schemas.py` (two width constants, next to `CAUSALE_MAX_LENGTH`)
- Create: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/ordine.py`
- Test: `projects/pigrocrm/packages/core/tests/test_invoice_ordine.py`

**Interfaces:**
- Produces, in `invoices/schemas.py`: `ORDINE_NUMERO_MAX_LENGTH = 20`, `ORDINE_CODICE_MAX_LENGTH = 15`.
- Produces, in `invoices/ordine.py`: `ORDINE_FIELDS: tuple[str, ...] = ("ordine_numero", "ordine_data", "ordine_cig", "ordine_cup")`; `@dataclass(frozen=True) class Ordine(numero: str | None, data: date | None, cig: str | None, cup: str | None)` with `def columns(self) -> dict[str, str | date | None]` keyed by `ORDINE_FIELDS`; `def checked_ordine(numero: str | None, data: date | None, cig: str | None, cup: str | None) -> Ordine`, raising `ValidationFailed("invoice", <field>, <reason>, expected=...)`.

- [ ] **Step 1: Write the failing tests**

Create `test_invoice_ordine.py`:

```python
"""The purchase order's own rules (REB-693), with no session: what `InvoiceService`
stores is what `checked_ordine` returns."""

from datetime import date

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.ordine import ORDINE_FIELDS, Ordine, checked_ordine


def test_no_order_is_four_nones() -> None:
    assert checked_ordine(None, None, None, None) == Ordine(None, None, None, None)


def test_a_whole_order_is_stripped_and_its_codes_upper_cased() -> None:
    """A CIG copied from a PDF arrives with spaces and in whatever case the reader
    typed it; the customer's office matches it upper case."""
    assert checked_ordine(
        "  ODA-14 ", date(2026, 10, 9), " z0a1b2c3d4 ", "j41e22000110005"
    ) == Ordine("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")


def test_a_number_alone_is_an_order() -> None:
    assert checked_ordine("8", None, None, None) == Ordine("8", None, None, None)


def test_columns_are_keyed_by_the_invoice_fields() -> None:
    ordine = Ordine("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", None)
    assert tuple(ordine.columns()) == ORDINE_FIELDS
    assert ordine.columns() == {
        "ordine_numero": "ODA-14",
        "ordine_data": date(2026, 10, 9),
        "ordine_cig": "Z0A1B2C3D4",
        "ordine_cup": None,
    }


@pytest.mark.parametrize(
    "data, cig, cup",
    [
        (date(2026, 10, 9), None, None),
        (None, "Z0A1B2C3D4", None),
        (None, None, "J41E22000110005"),
    ],
)
def test_a_date_cig_or_cup_without_the_number_is_refused_on_the_number(
    data: date | None, cig: str | None, cup: str | None
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine(None, data, cig, cup)
    assert caught.value.details["field"] == "ordine_numero"
    assert "numero dell'ordine" in caught.value.details["reason"]


@pytest.mark.parametrize("numero", ["", "   "])
def test_an_empty_number_is_refused_rather_than_stored_as_text(numero: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine(numero, None, None, None)
    assert caught.value.details["field"] == "ordine_numero"


def test_a_number_longer_than_twenty_characters_is_refused() -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine("x" * 21, None, None, None)
    assert caught.value.details["field"] == "ordine_numero"


@pytest.mark.parametrize("numero", ["Ordine città 5", "N°5", "ODA\t14"])
def test_a_number_outside_printable_basic_latin_is_refused_not_transliterated(
    numero: str,
) -> None:
    """An order number is an identifier the customer matches character by character:
    rewriting `città` as `citta` would hand back a number they never issued."""
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine(numero, None, None, None)
    assert caught.value.details["field"] == "ordine_numero"
    assert "caratteri latini di base" in caught.value.details["reason"]


@pytest.mark.parametrize("cig", ["Z0A1B2C3D", "Z0A1B2C3D45", "Z0A1-2C3D4", "Z0A1B2C3Dè"])
def test_a_cig_is_exactly_ten_letters_or_digits(cig: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine("ODA-14", None, cig, None)
    assert caught.value.details["field"] == "ordine_cig"
    assert caught.value.details["reason"] == "il CIG ha 10 caratteri, lettere e cifre"


@pytest.mark.parametrize("cup", ["J41E2200011000", "J41E220001100055", "J41E2200011000-"])
def test_a_cup_is_exactly_fifteen_letters_or_digits(cup: str) -> None:
    with pytest.raises(ValidationFailed) as caught:
        checked_ordine("ODA-14", None, None, cup)
    assert caught.value.details["field"] == "ordine_cup"
    assert caught.value.details["reason"] == "il CUP ha 15 caratteri, lettere e cifre"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_ordine.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'pigrocrm.core.invoices.ordine'`.

- [ ] **Step 3: Add the width constants**

In `schemas.py`, right after `CAUSALE_MAX_LENGTH = 200`:

```python
# `DatiOrdineAcquisto` (REB-693): IdDocumento is String20Type, CodiceCIG and CodiceCUP
# are String15Type. The columns mirror them like every width above.
ORDINE_NUMERO_MAX_LENGTH = 20
ORDINE_CODICE_MAX_LENGTH = 15
```

- [ ] **Step 4: Write the module**

Create `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/ordine.py`:

```python
"""The purchase order an invoice answers (REB-693), and the rules its four values obey.

Pure: no session, no row. `InvoiceService` passes the values a write will store, the
merged row's on an update, and stores what comes back, so a CIG typed in lower case is
kept upper case and a malformed one is refused with its field name before the flush.
The table CHECK `ck_invoices_ordine_needs_numero` refuses the orphan case again on every
other write path.

Stricter than the FPR12 types on purpose. `String15Type` would take any fifteen Basic
Latin characters for a CIG; ANAC's CIG is ten letters or digits and a CUP fifteen, and a
character missing from either is caught here, when it is typed, rather than by the
customer's accounts office after the invoice has gone.
"""

import re
from dataclasses import dataclass
from datetime import date

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.schemas import ORDINE_NUMERO_MAX_LENGTH

ENTITY = "invoice"

ORDINE_FIELDS: tuple[str, ...] = ("ordine_numero", "ordine_data", "ordine_cig", "ordine_cup")

# Printable Basic Latin. `String20Type` is `\p{IsBasicLatin}{1,20}`, which also admits
# control characters; a tab inside an identifier is never what somebody meant.
_NUMERO_RE = re.compile(r"[\x20-\x7e]+")
_CIG_RE = re.compile(r"[A-Z0-9]{10}")
_CUP_RE = re.compile(r"[A-Z0-9]{15}")


@dataclass(frozen=True)
class Ordine:
    numero: str | None
    data: date | None
    cig: str | None
    cup: str | None

    def columns(self) -> dict[str, str | date | None]:
        """The four values under the invoice's own column names, in `ORDINE_FIELDS`
        order."""
        return dict(zip(ORDINE_FIELDS, (self.numero, self.data, self.cig, self.cup), strict=True))


def _numero(value: str | None) -> str | None:
    if value is None:
        return None
    pulito = value.strip()
    if not pulito:
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            "il numero dell'ordine e' vuoto: per togliere l'ordine svuota tutti i suoi campi",
            expected=f"da 1 a {ORDINE_NUMERO_MAX_LENGTH} caratteri",
        )
    if len(pulito) > ORDINE_NUMERO_MAX_LENGTH:
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            f"il numero dell'ordine ha al massimo {ORDINE_NUMERO_MAX_LENGTH} caratteri",
            expected=f"da 1 a {ORDINE_NUMERO_MAX_LENGTH} caratteri",
        )
    if not _NUMERO_RE.fullmatch(pulito):
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            "il numero dell'ordine ammette solo caratteri latini di base: niente lettere "
            "accentate o simboli come il grado, scrivilo come lo scrive il cliente "
            "senza quei caratteri",
            expected="lettere senza accento, cifre, spazi e punteggiatura semplice",
        )
    return pulito


def _codice(
    value: str | None, field: str, pattern: re.Pattern[str], reason: str, expected: str
) -> str | None:
    if value is None:
        return None
    pulito = value.strip().upper()
    if not pattern.fullmatch(pulito):
        raise ValidationFailed(ENTITY, field, reason, expected=expected)
    return pulito


def checked_ordine(
    numero: str | None, data: date | None, cig: str | None, cup: str | None
) -> Ordine:
    """The four values as they will be stored, or a `ValidationFailed` naming the field.

    `None` is "no value". An empty number is refused rather than read as `None`: the web
    sends `null` for an empty input, so an empty string is a caller's mistake, and
    taking it as a clear would let a stray `""` drop the order a CIG still needs.
    """
    numero_pulito = _numero(numero)
    cig_pulito = _codice(
        cig, "ordine_cig", _CIG_RE, "il CIG ha 10 caratteri, lettere e cifre", "10 lettere o cifre"
    )
    cup_pulito = _codice(
        cup, "ordine_cup", _CUP_RE, "il CUP ha 15 caratteri, lettere e cifre", "15 lettere o cifre"
    )
    if numero_pulito is None and (
        data is not None or cig_pulito is not None or cup_pulito is not None
    ):
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            "indica il numero dell'ordine a cui si riferiscono data, CIG e CUP",
            expected="il numero dell'ordine del cliente",
        )
    return Ordine(numero_pulito, data, cig_pulito, cup_pulito)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_ordine.py`
Expected: PASS.

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy
git add projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/ordine.py \
  projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/schemas.py \
  projects/pigrocrm/packages/core/tests/test_invoice_ordine.py
git commit -m "feat(core): the purchase order's rules live in one pure module" -m "checked_ordine strips the four values, upper-cases the CIG and the CUP, holds them to ANAC's lengths and refuses a date, CIG or CUP without the order number, naming the field.

REB-693."
```

---

### Task 3: The schemas and the service carry the order

**Files:**
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/schemas.py` (`InvoiceCreate`, `InvoiceUpdate`, `InvoiceRead`, `InvoiceForExport`)
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/service.py` (`create`, `update`, the from-proforma branch of `issue`, `_for_export`, `_for_export_proforma`)
- Test: `projects/pigrocrm/packages/core/tests/test_invoice_schemas.py`, `test_invoice_service.py`, `test_invoice_issue.py`

**Interfaces:**
- Consumes: `checked_ordine`, `ORDINE_FIELDS`, `Ordine.columns()` (Task 2); `ORDINE_NUMERO_MAX_LENGTH`, `ORDINE_CODICE_MAX_LENGTH` (Task 2); the columns (Task 1).
- Produces: `ordine_numero`, `ordine_data`, `ordine_cig`, `ordine_cup` on `InvoiceCreate` and `InvoiceUpdate` (optional, default `None`), on `InvoiceRead` (required, nullable, right after `competenza_a`) and on `InvoiceForExport` (optional, default `None`). Tasks 4 to 7 read these names.

- [ ] **Step 1: Write the failing schema tests**

In `test_invoice_schemas.py`, add the four names to the set in `test_update_exposes_the_editable_columns_and_custom_fields`:

```python
    assert set(InvoiceUpdate.model_fields) == {
        "causale",
        "note_interne",
        "custom_fields",
        "competenza_da",
        "competenza_a",
        "data_emissione",
        "data_scadenza",
        "ordine_numero",
        "ordine_data",
        "ordine_cig",
        "ordine_cup",
    }
```

and append:

```python
def test_the_purchase_order_is_carried_by_every_shape_that_reaches_a_document() -> None:
    """Settable on creation and on update, read back, and part of what the exporter
    and the PDF read (REB-693). Not on the import: reading `DatiOrdineAcquisto` back
    from an external XML is out of this card, and an import that half-supported it
    would store an order nobody checked."""
    from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceImport, InvoiceRead

    ordine = {"ordine_numero", "ordine_data", "ordine_cig", "ordine_cup"}
    for model in (InvoiceCreate, InvoiceUpdate, InvoiceRead, InvoiceForExport):
        assert ordine <= set(model.model_fields), model.__name__
    assert not ordine & set(InvoiceImport.model_fields)
    assert InvoiceCreate(customer_id=uuid4()).ordine_numero is None
    assert InvoiceForExport.model_fields["ordine_cig"].default is None


def test_the_purchase_order_widths_mirror_the_columns() -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), ordine_numero="x" * 21)
    with pytest.raises(ValidationError):
        InvoiceCreate(customer_id=uuid4(), ordine_cig="x" * 16)
    with pytest.raises(ValidationError):
        InvoiceUpdate(ordine_cup="x" * 16)
```

- [ ] **Step 2: Write the failing service tests**

Append to `test_invoice_service.py`:

```python
# --- the purchase order (REB-693) -----------------------------------------------------


def test_a_draft_may_carry_a_purchase_order_stored_clean(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            ordine_numero=" ODA-14 ",
            ordine_data=date(2026, 10, 9),
            ordine_cig="z0a1b2c3d4",
            ordine_cup="j41e22000110005",
        ),
        ADMIN,
    )
    assert (
        invoice.ordine_numero,
        invoice.ordine_data,
        invoice.ordine_cig,
        invoice.ordine_cup,
    ) == ("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")
    bare = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    assert (bare.ordine_numero, bare.ordine_data, bare.ordine_cig, bare.ordine_cup) == (
        None,
        None,
        None,
        None,
    )


def test_a_cig_without_an_order_number_is_refused_on_the_number(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(InvoiceCreate(customer_id=customer_id, ordine_cig="Z0A1B2C3D4"), ADMIN)
    assert caught.value.details["field"] == "ordine_numero"


def test_a_malformed_cig_is_refused_by_field_name(
    service: InvoiceService, customer_id: UUID
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, ordine_numero="ODA-14", ordine_cig="Z0A1"),
            ADMIN,
        )
    assert caught.value.details["field"] == "ordine_cig"


def test_the_order_is_edited_on_the_merged_row_and_cleared_as_a_whole(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Like the accrual period: adding a CIG to a document whose number is stored is
    one field, dropping the number while the CIG stays is refused and leaves the row as
    it was, and four nulls clear the order."""
    invoice = service.create(InvoiceCreate(customer_id=customer_id, ordine_numero="ODA-14"), ADMIN)
    with_cig = service.update(invoice.id, InvoiceUpdate(ordine_cig="z0a1b2c3d4"), ADMIN)
    assert (with_cig.ordine_numero, with_cig.ordine_cig) == ("ODA-14", "Z0A1B2C3D4")

    with pytest.raises(ValidationFailed) as caught:
        service.update(invoice.id, InvoiceUpdate(ordine_numero=None), ADMIN)
    assert caught.value.details["field"] == "ordine_numero"
    assert service.get(invoice.id, ADMIN).ordine_numero == "ODA-14"

    cleared = service.update(
        invoice.id,
        InvoiceUpdate(ordine_numero=None, ordine_data=None, ordine_cig=None, ordine_cup=None),
        ADMIN,
    )
    assert (cleared.ordine_numero, cleared.ordine_cig) == (None, None)
```

- [ ] **Step 3: Write the failing issue tests**

Append to `test_invoice_issue.py` (after the accrual-period section; `ImmutableField`, `InvoiceCreate`, `InvoiceIssue`, `InvoiceLineIn`, `InvoiceUpdate`, `_customer` and `date` are already in the module):

```python
# --- the purchase order across the emission (REB-693) --------------------------------


def test_the_purchase_order_is_frozen_at_issue_like_the_causale(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            ordine_numero="ODA-14",
            ordine_cig="Z0A1B2C3D4",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("250.00"))],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert (issued.ordine_numero, issued.ordine_cig) == ("ODA-14", "Z0A1B2C3D4")
    with pytest.raises(ImmutableField):
        service.update(issued.id, InvoiceUpdate(ordine_cig="Z9Z9Z9Z9Z9"), ADMIN)
    assert service.get(issued.id, ADMIN).ordine_cig == "Z0A1B2C3D4"


def test_a_fattura_issued_from_a_proforma_answers_the_proforma_s_order(
    service: InvoiceService, db_session: Session
) -> None:
    """The order is the customer's and the fattura answers it, so it travels from the
    proforma like the period does; the consumed proforma is frozen with it."""
    customer_id = _customer(db_session)
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            ordine_numero="ODA-14",
            ordine_data=date(2026, 10, 9),
            ordine_cig="Z0A1B2C3D4",
            ordine_cup="J41E22000110005",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("250.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)
    issued = service.issue(proforma.id, InvoiceIssue(), ADMIN)
    assert issued.id != proforma.id
    assert (
        issued.ordine_numero,
        issued.ordine_data,
        issued.ordine_cig,
        issued.ordine_cup,
    ) == ("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")
    for change in (
        InvoiceUpdate(ordine_numero="ODA-15"),
        InvoiceUpdate(ordine_data=date(2026, 10, 10)),
        InvoiceUpdate(ordine_cig="Z9Z9Z9Z9Z9"),
        InvoiceUpdate(ordine_cup=None),
    ):
        with pytest.raises(ImmutableField):
            service.update(proforma.id, change, ADMIN)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_schemas.py projects/pigrocrm/packages/core/tests/test_invoice_service.py projects/pigrocrm/packages/core/tests/test_invoice_issue.py -k "ordine or purchase_order or editable_columns"`
Expected: FAIL (unexpected keyword `ordine_numero`, missing fields).

- [ ] **Step 5: Add the fields to the schemas**

The two width constants are already in this module (Task 2).

In `InvoiceCreate`, right after `competenza_a`:

```python
    # The customer's purchase order (REB-693): its number, and optionally its date, CIG
    # and CUP. The widths are the columns'; stripping, case and ANAC's lengths are
    # `invoices/ordine.py`'s, applied by the service on the merged row.
    ordine_numero: SafeStr | None = Field(default=None, max_length=ORDINE_NUMERO_MAX_LENGTH)
    ordine_data: date | None = None
    ordine_cig: SafeStr | None = Field(default=None, max_length=ORDINE_CODICE_MAX_LENGTH)
    ordine_cup: SafeStr | None = Field(default=None, max_length=ORDINE_CODICE_MAX_LENGTH)
```

In `InvoiceUpdate`, right after `competenza_a`, the same four lines with this comment instead:

```python
    # The purchase order (REB-693), frozen with the rest of the header after emission.
    # An explicit null clears a field; the service refuses a date, CIG or CUP left
    # without the number on the merged row.
```

In `InvoiceRead`, right after `competenza_a: date | None`:

```python
    ordine_numero: str | None
    ordine_data: date | None
    ordine_cig: str | None
    ordine_cup: str | None
```

In `InvoiceForExport`, right after `competenza_a: date | None = None`:

```python
    # Read off the row and frozen by the same rule as the period (REB-693): what the
    # exporter writes in `DatiOrdineAcquisto` is what the document said at issue.
    ordine_numero: str | None = Field(default=None, max_length=ORDINE_NUMERO_MAX_LENGTH)
    ordine_data: date | None = None
    ordine_cig: str | None = Field(default=None, max_length=ORDINE_CODICE_MAX_LENGTH)
    ordine_cup: str | None = Field(default=None, max_length=ORDINE_CODICE_MAX_LENGTH)
```

Update the `InvoiceUpdate` docstring sentence «`causale`, the accrual period and a proforma's `data_emissione` are on this schema» to «`causale`, the accrual period, the purchase order and a proforma's `data_emissione` are on this schema».

- [ ] **Step 6: Use the rules in the service**

In `service.py`, add the import next to the other `pigrocrm.core.invoices` imports:

```python
from pigrocrm.core.invoices.ordine import ORDINE_FIELDS, checked_ordine
```

In `create`, right after `self._check_competenza(data.competenza_da, data.competenza_a)`:

```python
        ordine = checked_ordine(
            data.ordine_numero, data.ordine_data, data.ordine_cig, data.ordine_cup
        )
```

and in the `Invoice(...)` constructor of `create`, right after `competenza_a=data.competenza_a,`:

```python
            **ordine.columns(),
```

In `update`, right after the `if "competenza_da" in changes or "competenza_a" in changes:` block:

```python
        if any(key in changes for key in ORDINE_FIELDS):
            # On the merged row, like the period: a CIG added to a document whose number
            # is stored is one field, and a number cleared while the CIG stays is
            # refused before anything is written (REB-693).
            ordine = checked_ordine(
                changes.get("ordine_numero", invoice.ordine_numero),
                changes.get("ordine_data", invoice.ordine_data),
                changes.get("ordine_cig", invoice.ordine_cig),
                changes.get("ordine_cup", invoice.ordine_cup),
            )
            changes.update(
                {key: value for key, value in ordine.columns().items() if key in changes}
            )
```

In `issue`, in the `if from_proforma:` branch's `Invoice(...)`, right after `competenza_a=source.competenza_a,`:

```python
                    # The order is the customer's and the fattura answers it, so it
                    # travels with the work like the period (REB-693).
                    ordine_numero=source.ordine_numero,
                    ordine_data=source.ordine_data,
                    ordine_cig=source.ordine_cig,
                    ordine_cup=source.ordine_cup,
```

In both `_for_export` and `_for_export_proforma`, in the `InvoiceForExport(...)` call, right after `competenza_a=invoice.competenza_a,`:

```python
            ordine_numero=invoice.ordine_numero,
            ordine_data=invoice.ordine_data,
            ordine_cig=invoice.ordine_cig,
            ordine_cup=invoice.ordine_cup,
```

- [ ] **Step 7: Run the tests to verify they pass, then the whole core invoice suite**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_schemas.py projects/pigrocrm/packages/core/tests/test_invoice_service.py projects/pigrocrm/packages/core/tests/test_invoice_issue.py`
Expected: PASS.

Then: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests -k invoice`
Expected: PASS. A test that builds an `InvoiceRead` by hand now needs the four keys; add them as `None` there.

- [ ] **Step 8: Lint, type-check, commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy
git add projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/schemas.py \
  projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/service.py \
  projects/pigrocrm/packages/core/tests/test_invoice_schemas.py \
  projects/pigrocrm/packages/core/tests/test_invoice_service.py \
  projects/pigrocrm/packages/core/tests/test_invoice_issue.py
git commit -m "feat(core): a draft or proforma takes a purchase order and its fattura keeps it" -m "The four fields travel on create and update, checked on the merged row, frozen at issue, copied from a proforma onto the fattura it becomes and handed to the exporter and the PDF.

REB-693."
```

---

### Task 4: The XML writes `DatiOrdineAcquisto`, and the issue pre-check measures it

**Files:**
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/fatturapa.py` (width constants next to `_CAUSALE_MAX`, `check_document_text_exportable`, `FatturaPAExporter._dati_generali`, a new `_dati_ordine_acquisto`)
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/service.py` (the one `check_document_text_exportable` call in `issue`)
- Test: `projects/pigrocrm/packages/core/tests/test_invoice_fatturapa.py`, `test_invoice_issue.py`

**Interfaces:**
- Consumes: `InvoiceForExport.ordine_*` (Task 3).
- Produces: `check_document_text_exportable(causale, righe, *, ordine_numero: str | None = None, ordine_cig: str | None = None, ordine_cup: str | None = None) -> None`.

- [ ] **Step 1: Let the XML test builder take an order**

In `test_invoice_fatturapa.py`, give `_invoice` one more keyword parameter and pass it through:

```python
def _invoice(
    righe: list[InvoiceLineRead],
    *,
    cliente: PartySnapshot | None = None,
    emittente: PartySnapshot | None = None,
    bollo: str = "0.00",
    causale: str | None = "Consulenza tecnica",
    numero: int = 7,
    competenza: tuple[date, date] | None = None,
    ordine: dict[str, object] | None = None,
) -> InvoiceForExport:
```

and inside the `InvoiceForExport(...)` call, right after `competenza_a=...`:

```python
        **(ordine or {}),
```

- [ ] **Step 2: Write the failing XML tests**

Append to `test_invoice_fatturapa.py`:

```python
# --- the purchase order in DatiGenerali (REB-693) -------------------------------------

ORDINE_INTERO: dict[str, object] = {
    "ordine_numero": "ODA-14",
    "ordine_data": date(2026, 10, 9),
    "ordine_cig": "Z0A1B2C3D4",
    "ordine_cup": "J41E22000110005",
}


def _ordini(xml: bytes) -> list[list[tuple[str, str | None]]]:
    root = etree.fromstring(xml)
    return [
        [(child.tag, child.text) for child in blocco] for blocco in root.iter("DatiOrdineAcquisto")
    ]


def test_a_whole_purchase_order_is_written_in_the_schema_s_order_and_validates() -> None:
    """`DatiDocumentiCorrelatiType` is a sequence and CUP comes before CIG in it: the
    file is invalid with every value right if the two swap, which is why this validates
    rather than only reading the elements back."""
    xml = FatturaPAExporter().to_bytes(
        _invoice([_line(1, "Consulenza", "1.000000", "250.000000", "250.00")], ordine=ORDINE_INTERO)
    )
    assert_valid(xml)
    assert _ordini(xml) == [
        [
            ("IdDocumento", "ODA-14"),
            ("Data", "2026-10-09"),
            ("CodiceCUP", "J41E22000110005"),
            ("CodiceCIG", "Z0A1B2C3D4"),
        ]
    ]
    generali = etree.fromstring(xml).find(".//DatiGenerali")
    assert generali is not None
    assert [child.tag for child in generali] == ["DatiGeneraliDocumento", "DatiOrdineAcquisto"]


def test_an_order_number_alone_writes_only_the_identifier() -> None:
    xml = FatturaPAExporter().to_bytes(
        _invoice(
            [_line(1, "Consulenza", "1.000000", "250.000000", "250.00")],
            ordine={"ordine_numero": "8"},
        )
    )
    assert_valid(xml)
    assert _ordini(xml) == [[("IdDocumento", "8")]]


def test_without_an_order_no_block_is_written() -> None:
    xml = FatturaPAExporter().to_bytes(
        _invoice([_line(1, "Consulenza", "1.000000", "250.000000", "250.00")])
    )
    assert_valid(xml)
    assert _ordini(xml) == []


def test_markup_characters_in_an_order_number_reach_the_xml_unchanged() -> None:
    xml = FatturaPAExporter().to_bytes(
        _invoice(
            [_line(1, "Consulenza", "1.000000", "250.000000", "250.00")],
            ordine={"ordine_numero": "ODA#14*<&>"},
        )
    )
    assert_valid(xml)
    assert _ordini(xml) == [[("IdDocumento", "ODA#14*<&>")]]


def test_an_order_number_the_xml_cannot_carry_is_refused_by_the_writer_and_the_pre_check() -> None:
    export = _invoice(
        [_line(1, "Consulenza", "1.000000", "250.000000", "250.00")],
        ordine={"ordine_numero": "Ordine città 5"},
    )
    with pytest.raises(ValidationFailed) as caught:
        FatturaPAExporter().to_bytes(export)
    assert caught.value.details["field"] == "ordine_numero"
    with pytest.raises(ValidationFailed) as caught:
        check_document_text_exportable("Consulenza", export.righe, ordine_numero="Ordine città 5")
    assert caught.value.details["field"] == "ordine_numero"
    with pytest.raises(ValidationFailed) as caught:
        check_document_text_exportable("Consulenza", export.righe, ordine_cig="Z0A1B2C3Dè")
    assert caught.value.details["field"] == "ordine_cig"
```

If `_LATIN_SPELLINGS` in `fatturapa.py` rewrites `à` or `è` (read the table first), pick a character it does not rewrite and keep the expectation: refused, on that field.

- [ ] **Step 3: Write the failing issue test**

Append to `test_invoice_issue.py` (`text` from sqlalchemy and `ValidationFailed` are already imported; check the import list and add what is missing):

```python
def test_an_order_number_the_xml_cannot_carry_is_refused_before_the_number_is_spent(
    service: InvoiceService, db_session: Session
) -> None:
    """A value written by another path than the service (a fix-up script, raw SQL) is
    measured with the writer's own rules before the counter moves, like the causale
    since ORB-140."""
    customer_id = _customer(db_session)
    draft_id = _draft(service, customer_id)
    db_session.execute(
        text("UPDATE invoices SET ordine_numero = 'Ordine città 5' WHERE id = :id"),
        {"id": draft_id},
    )
    db_session.expire_all()
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft_id, InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "ordine_numero"
    assert service.get(draft_id, ADMIN).numero is None
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_fatturapa.py projects/pigrocrm/packages/core/tests/test_invoice_issue.py -k "order or ordine"`
Expected: FAIL (no `DatiOrdineAcquisto`, unexpected keyword `ordine_numero` on the pre-check, issue not refused).

- [ ] **Step 5: Write the block and extend the pre-check**

In `fatturapa.py`, right after `_CAUSALE_MAX = 200`:

```python
# `DatiOrdineAcquisto` (REB-693): IdDocumento is String20Type, CodiceCUP and CodiceCIG are
# String15Type, all three Basic Latin.
_ID_DOCUMENTO_MAX = 20
_CODICE_CUP_MAX = 15
_CODICE_CIG_MAX = 15
```

Replace the signature of `check_document_text_exportable` and append the order to its body:

```python
def check_document_text_exportable(
    causale: str | None,
    righe: Iterable[_LineText],
    *,
    ordine_numero: str | None = None,
    ordine_cig: str | None = None,
    ordine_cup: str | None = None,
) -> None:
```

Add one sentence to its docstring after the list of tags: «Since REB-693 also the purchase order's `IdDocumento`, `CodiceCUP` and `CodiceCIG`, which the service checks when it stores them but which another path could have written.» Then, at the end of the function body:

```python
    for value, tag, field, width in (
        (ordine_numero, "IdDocumento", "ordine_numero", _ID_DOCUMENTO_MAX),
        (ordine_cup, "CodiceCUP", "ordine_cup", _CODICE_CUP_MAX),
        (ordine_cig, "CodiceCIG", "ordine_cig", _CODICE_CIG_MAX),
    ):
        if value:
            _check_text(
                value,
                tag,
                entity="invoice",
                field=field,
                max_length=width,
                pattern=_BASIC_LATIN_RE,
            )
```

In `FatturaPAExporter._dati_generali`, as the last statement of the method (after the `Causale` block):

```python
        self._dati_ordine_acquisto(generali, invoice)
```

and add the method right after `_dati_generali`:

```python
    def _dati_ordine_acquisto(self, generali: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence (`DatiDocumentiCorrelatiType`): RiferimentoNumeroLinea*, IdDocumento,
        Data?, NumItem?, CodiceCommessaConvenzione?, CodiceCUP?, CodiceCIG?.

        One block, after `DatiGeneraliDocumento`, only when the document has an order
        (REB-693). No `RiferimentoNumeroLinea`: the order covers every line, which is
        what the element's absence means. CUP before CIG is the schema's order.
        """
        if not (invoice.ordine_numero or "").strip():
            return
        ordine = etree.SubElement(generali, "DatiOrdineAcquisto")
        self._text(
            ordine,
            "IdDocumento",
            invoice.ordine_numero or "",
            entity="invoice",
            field="ordine_numero",
            max_length=_ID_DOCUMENTO_MAX,
            pattern=_BASIC_LATIN_RE,
        )
        if invoice.ordine_data is not None:
            data = etree.SubElement(ordine, "Data")
            data.text = self._iso(invoice.ordine_data)
        if invoice.ordine_cup:
            self._text(
                ordine,
                "CodiceCUP",
                invoice.ordine_cup,
                entity="invoice",
                field="ordine_cup",
                max_length=_CODICE_CUP_MAX,
                pattern=_BASIC_LATIN_RE,
            )
        if invoice.ordine_cig:
            self._text(
                ordine,
                "CodiceCIG",
                invoice.ordine_cig,
                entity="invoice",
                field="ordine_cig",
                max_length=_CODICE_CIG_MAX,
                pattern=_BASIC_LATIN_RE,
            )
```

Update the `_dati_generali` docstring's first line to name the block it now writes after the sequence it documents: «Then `DatiOrdineAcquisto`, the next element of `DatiGenerali`, when the document has an order.»

In `service.py`, replace `check_document_text_exportable(source.causale, righe)` with:

```python
            check_document_text_exportable(
                source.causale,
                righe,
                ordine_numero=source.ordine_numero,
                ordine_cig=source.ordine_cig,
                ordine_cup=source.ordine_cup,
            )
```

- [ ] **Step 6: Run the tests to verify they pass, then the XML suites**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_fatturapa.py projects/pigrocrm/packages/core/tests/test_invoice_issue.py projects/pigrocrm/packages/core/tests/test_invoice_artifacts_xml.py`
Expected: PASS.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy
git add projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/fatturapa.py \
  projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/service.py \
  projects/pigrocrm/packages/core/tests/test_invoice_fatturapa.py \
  projects/pigrocrm/packages/core/tests/test_invoice_issue.py
git commit -m "feat(core): the FPR12 XML carries the purchase order and its CIG" -m "DatiOrdineAcquisto follows DatiGeneraliDocumento with IdDocumento, Data, CodiceCUP and CodiceCIG in the schema's order, and issue measures the three texts with the writer's rules before a number is spent.

REB-693."
```

---

### Task 5: The PDF prints the order

**Files:**
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/pdf.py` (`build_scope`'s `fattura` dict, a new `_riferimento_ordine`)
- Modify: `projects/pigrocrm/packages/core/src/pigrocrm/core/render/assets/template-invoice.md` (the one template both a fattura and a proforma render through)
- Test: `projects/pigrocrm/packages/core/tests/test_invoice_pdf.py`, `test_invoice_artifacts.py`

**Interfaces:**
- Consumes: `InvoiceForExport.ordine_*` (Task 3); `format_data` and `_EMPTY` (already in `pdf.py`).
- Produces: scope key `fattura.riferimento_ordine: str`, `""` without an order number.

- [ ] **Step 1: Let the scope test builder take an order**

In `test_invoice_pdf.py`, add `ordine: dict[str, object] | None = None` to `_export`'s keyword parameters and `**(ordine or {}),` to its `InvoiceForExport(...)` call, right after `competenza_a=...`.

- [ ] **Step 2: Write the failing scope test**

Append to `test_invoice_pdf.py`:

```python
def test_the_order_reference_reads_as_one_line_with_the_parts_it_has() -> None:
    """REB-693: the number, then each part the order has, the date spelled like every
    other date on the document; nothing at all without a number, so the template's
    `{{#if}}` prints no label with an empty value after it."""
    whole = build_scope(
        _export(
            ordine={
                "ordine_numero": "ODA-14",
                "ordine_data": date(2026, 10, 9),
                "ordine_cig": "Z0A1B2C3D4",
                "ordine_cup": "J41E22000110005",
            }
        ),
        riferimento=None,
    )
    assert whole["fattura"]["riferimento_ordine"] == (
        "n. ODA-14 del 09-10-2026, CIG Z0A1B2C3D4, CUP J41E22000110005"
    )
    numero = build_scope(_export(ordine={"ordine_numero": "8"}), riferimento=None)
    assert numero["fattura"]["riferimento_ordine"] == "n. 8"
    cig = build_scope(
        _export(ordine={"ordine_numero": "8", "ordine_cig": "Z0A1B2C3D4"}), riferimento=None
    )
    assert cig["fattura"]["riferimento_ordine"] == "n. 8, CIG Z0A1B2C3D4"
    assert build_scope(_export(), riferimento=None)["fattura"]["riferimento_ordine"] == ""
```

- [ ] **Step 3: Write the failing rendered tests**

Append to `test_invoice_artifacts.py`, next to the accrual-period PDF tests (same fixtures):

```python
def test_the_pdf_prints_the_order_reference_with_its_markup_characters_literally(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """REB-693. `#` opens code and `*` strong text in Typst: an order number carrying
    either must still print as typed."""
    from datetime import date

    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            causale="Consulenza",
            ordine_numero="ODA#14*",
            ordine_data=date(2026, 10, 9),
            ordine_cig="Z0A1B2C3D4",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("250.00"))],
        ),
        ADMIN,
    )
    invoice_id = service.issue(draft.id, InvoiceIssue(), ADMIN).id
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = extract_pdf_text(storage, db_session, pdf.document_id)
    assert "Riferimento ordine: n. ODA#14* del 09-10-2026, CIG Z0A1B2C3D4" in testo


def test_a_proforma_prints_its_order_and_a_document_without_one_prints_no_order_line(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            ordine_numero="ODA-14",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("250.00"))],
        ),
        ADMIN,
    )
    (pdf,) = service.produce_artifacts(proforma.id, ADMIN)
    assert "Riferimento ordine: n. ODA-14" in extract_pdf_text(storage, db_session, pdf.document_id)

    invoice_id = _issue(service, customer_id)
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    assert "Riferimento ordine" not in extract_pdf_text(storage, db_session, pdf.document_id)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_pdf.py projects/pigrocrm/packages/core/tests/test_invoice_artifacts.py -k "order"`
Expected: FAIL (`KeyError: 'riferimento_ordine'`, and no order line in the PDF text).

- [ ] **Step 5: Build the line and print it**

In `pdf.py`, in `build_scope`'s `"fattura"` dict, right after `"periodo_competenza": _periodo_competenza(export),`:

```python
            # The same rule as the period: the whole line or nothing (REB-693).
            "riferimento_ordine": _riferimento_ordine(export),
```

and right after `_periodo_competenza`:

```python
def _riferimento_ordine(export: InvoiceForExport) -> str:
    """`n. <numero> del dd-mm-yyyy, CIG <cig>, CUP <cup>`, each part only when the
    order has it, or `""` without an order number (REB-693). The date goes through
    `format_data` like every other date the document prints."""
    if not export.ordine_numero:
        return _EMPTY
    numero = f"n. {export.ordine_numero}"
    if export.ordine_data is not None:
        numero = f"{numero} del {format_data(export.ordine_data)}"
    parti = [numero]
    if export.ordine_cig:
        parti.append(f"CIG {export.ordine_cig}")
    if export.ordine_cup:
        parti.append(f"CUP {export.ordine_cup}")
    return ", ".join(parti)
```

In `template-invoice.md`, right after the period's block (the three lines from `{{#if fattura.periodo_competenza}}` to its `{{/if}}`):

```
{{#if fattura.riferimento_ordine}}

#text(size: 9pt, fill: muted)[Riferimento ordine: {{fattura.riferimento_ordine}}]
{{/if}}
```

- [ ] **Step 6: Run the tests to verify they pass, then the PDF suites**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/packages/core/tests/test_invoice_pdf.py projects/pigrocrm/packages/core/tests/test_invoice_artifacts.py projects/pigrocrm/packages/core/tests/test_template_escaping.py`
Expected: PASS. If the `ODA#14*` assertion fails because the text extractor splits the line, assert on `"n. ODA#14* del 09-10-2026"` and `"CIG Z0A1B2C3D4"` separately; if it fails because `#` or `*` did not print, the value is not reaching the template escaped, which is a defect to fix in how the scope is rendered, not in the test.

- [ ] **Step 7: Lint, type-check, commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy
git add projects/pigrocrm/packages/core/src/pigrocrm/core/invoices/pdf.py \
  projects/pigrocrm/packages/core/src/pigrocrm/core/render/assets/template-invoice.md \
  projects/pigrocrm/packages/core/tests/test_invoice_pdf.py \
  projects/pigrocrm/packages/core/tests/test_invoice_artifacts.py
git commit -m "feat(core): the invoice and proforma PDF print the purchase order" -m "One line under the accrual period, «Riferimento ordine: n. … del …, CIG …, CUP …», with only the parts the order has, and none without an order number.

REB-693."
```

---

### Task 6: The API, the MCP proforma tools and the web types

**Files:**
- Modify: `projects/pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/invoices.py` (`update_proforma`)
- Modify: `projects/pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/__init__.py` (`create_proforma`, `update_proforma`)
- Modify: `projects/pigrocrm/apps/web/src/lib/api-types.ts` (regenerated, never edited by hand)
- Test: `projects/pigrocrm/apps/api/tests/test_invoices_api.py`, `projects/pigrocrm/apps/mcp/tests/test_update_proforma_tool.py`

**Interfaces:**
- Consumes: the schema fields (Task 3).
- Produces: MCP `create_proforma(..., ordine_numero: str | None = None, ordine_data: IsoDateStr = None, ordine_cig: str | None = None, ordine_cup: str | None = None)` and `update_proforma(..., same four)`; `components['schemas']['InvoiceRead']` in `api-types.ts` with `ordine_numero: string | null`, `ordine_data: string | null`, `ordine_cig: string | null`, `ordine_cup: string | null` (Task 7 reads them).

- [ ] **Step 1: Write the failing API tests**

Append to `test_invoices_api.py`:

```python
# --- the purchase order on the wire (REB-693) ----------------------------------------


def test_a_proforma_carries_its_purchase_order_through_post_and_patch(
    logged_in: TestClient, customer: dict[str, Any], fiscal_profile: dict[str, Any]
) -> None:
    created = logged_in.post(
        "/api/invoices",
        json={
            "customer_id": customer["id"],
            "tipo": "proforma",
            "ordine_numero": "ODA-14",
            "ordine_data": "2026-10-09",
            "ordine_cig": "z0a1b2c3d4",
            "righe": [{"descrizione": "Consulenza", "prezzo_unitario": "250.00"}],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["ordine_numero"], body["ordine_data"], body["ordine_cig"], body["ordine_cup"]) == (
        "ODA-14",
        "2026-10-09",
        "Z0A1B2C3D4",
        None,
    )

    patched = logged_in.patch(f"/api/invoices/{body['id']}", json={"ordine_cup": "J41E22000110005"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["ordine_cup"] == "J41E22000110005"
    assert patched.json()["ordine_numero"] == "ODA-14"

    bad = logged_in.patch(f"/api/invoices/{body['id']}", json={"ordine_cig": "Z0A1"})
    assert bad.status_code == 422, bad.text
    assert bad.json()["field"] == "ordine_cig"

    orphan = logged_in.patch(f"/api/invoices/{body['id']}", json={"ordine_numero": None})
    assert orphan.status_code == 422, orphan.text
    assert orphan.json()["field"] == "ordine_numero"

    cleared = logged_in.patch(
        f"/api/invoices/{body['id']}",
        json={"ordine_numero": None, "ordine_data": None, "ordine_cig": None, "ordine_cup": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["ordine_numero"] is None


def test_the_order_of_an_issued_invoice_cannot_change(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    draft = _draft(logged_in, customer["id"])
    issued = logged_in.post(f"/api/invoices/{draft['id']}/issue", json={}).json()
    response = logged_in.patch(f"/api/invoices/{issued['id']}", json={"ordine_numero": "ODA-14"})
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "immutable_field"
```

- [ ] **Step 2: Write the failing MCP test**

Append to `test_update_proforma_tool.py`:

```python
async def test_a_proforma_takes_its_purchase_order_at_creation_and_on_update(
    server: Any, mcp_session: Session
) -> None:
    """REB-693: the order goes in on `create_proforma`, a CIG is added on
    `update_proforma` without touching what was stored, `get_invoice` reads it back,
    and a CIG with no number comes back as the service's sentence, not a raw dump."""
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma",
            {
                "customer_id": customer_id,
                "righe": [RIGA],
                "ordine_numero": "ODA-14",
                "ordine_data": "2026-10-09",
            },
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        assert created.structured_content["ordine_numero"] == "ODA-14"

        updated = await client.call_tool(
            "update_proforma", {"invoice_id": invoice_id, "ordine_cig": "z0a1b2c3d4"}
        )
        assert not updated.is_error, updated.content[0].text
        assert updated.structured_content["ordine_cig"] == "Z0A1B2C3D4"
        assert updated.structured_content["ordine_data"] == "2026-10-09"

        read = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert read.structured_content["ordine_numero"] == "ODA-14"

        orphan = await client.call_tool(
            "create_proforma",
            {"customer_id": customer_id, "righe": [RIGA], "ordine_cig": "Z0A1B2C3D4"},
        )
        assert orphan.is_error
        assert "numero dell'ordine" in orphan.content[0].text
        assert "errors.pydantic.dev" not in orphan.content[0].text
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/apps/api/tests/test_invoices_api.py -k "purchase_order or order_of_an_issued"`
Expected: the API tests PASS already if Task 3 is in (the router passes the schemas through); that is correct and proves the wire. Then:

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/apps/mcp/tests/test_update_proforma_tool.py -k purchase_order`
Expected: FAIL (the tools do not accept `ordine_numero`).

- [ ] **Step 4: Add the four parameters to the MCP tools**

In `tools/invoices.py`, `update_proforma` gains four keyword parameters after `competenza_a: date | None,`:

```python
    ordine_numero: str | None = None,
    ordine_data: date | None = None,
    ordine_cig: str | None = None,
    ordine_cup: str | None = None,
```

its `supplied` dict gains:

```python
            "ordine_numero": ordine_numero,
            "ordine_data": ordine_data,
            "ordine_cig": ordine_cig,
            "ordine_cup": ordine_cup,
```

and its docstring's first line becomes «The header of a proforma: causale, document date, due date, accrual period, purchase order (ORB-61, ORB-63, REB-326, REB-693).»

In `tools/__init__.py`, `create_proforma` gains, after `competenza_a: IsoDateStr = None,`:

```python
        ordine_numero: str | None = None,
        ordine_data: IsoDateStr = None,
        ordine_cig: str | None = None,
        ordine_cup: str | None = None,
```

passes them in its dict, after `"competenza_a": ...`:

```python
                "ordine_numero": ordine_numero,
                "ordine_data": _iso_date(ordine_data),
                "ordine_cig": ordine_cig,
                "ordine_cup": ordine_cup,
```

and its docstring ends with this paragraph:

```
        `ordine_numero`, `ordine_data` (YYYY-MM-DD), `ordine_cig` e `ordine_cup` sono
        l'ordine d'acquisto del cliente a cui la fattura risponde: finiscono nel blocco
        DatiOrdineAcquisto dell'XML e su una riga del PDF. Data, CIG e CUP vogliono il
        numero; il CIG ha 10 caratteri e il CUP 15, lettere e cifre."""
```

`update_proforma` in `tools/__init__.py` gains the same four parameters after `competenza_a: IsoDateStr = None,`, passes them as `ordine_numero=ordine_numero, ordine_data=_iso_date(ordine_data), ordine_cig=ordine_cig, ordine_cup=ordine_cup,`, and its docstring's first sentence becomes «Corregge l'intestazione di una proforma ancora modificabile: la causale, la data del documento, la scadenza, il periodo di competenza e l'ordine d'acquisto del cliente (numero, data, CIG, CUP).» The sentence «da qui non si svuota niente» already covers the order: leave it.

- [ ] **Step 5: Run the MCP and API tests to verify they pass**

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/apps/mcp/tests`
Expected: PASS. If a test snapshots the tool list or parameter names, update the snapshot with the four new parameters.

Run: `TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q projects/pigrocrm/apps/api/tests/test_invoices_api.py`
Expected: PASS.

- [ ] **Step 6: Regenerate the web API types**

One shell invocation, from the repository root (the API is imported, not started, so the database URL only has to parse):

```bash
OPENAPI="$(mktemp -d)/openapi.json" && \
PIGROCRM_DATABASE_URL=postgresql+psycopg://x:y@localhost:1/z uv run --no-sync python -c 'import json, sys; from pigrocrm_api.main import app; json.dump(app.openapi(), open(sys.argv[1], "w"))' "$OPENAPI" && \
pnpm --filter web exec openapi-typescript "$OPENAPI" -o src/lib/api-types.ts && \
git diff --stat projects/pigrocrm/apps/web/src/lib/api-types.ts
```

Expected: the diff adds the four `ordine_*` fields to `InvoiceCreate`, `InvoiceUpdate` and `InvoiceRead` and changes nothing else. If unrelated hunks appear, main's `api-types.ts` was stale: keep them, they are what the API serves.

- [ ] **Step 7: Type-check the web app against the new types**

Run: `pnpm --filter web build`
Expected: PASS. `tsc` checks the tests too: a fixture typed as `Invoice` without the four keys fails here; add `ordine_numero: null, ordine_data: null, ordine_cig: null, ordine_cup: null` to it.

- [ ] **Step 8: Lint, type-check, commit**

```bash
uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy
git add projects/pigrocrm/apps/api/tests/test_invoices_api.py \
  projects/pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/invoices.py \
  projects/pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/__init__.py \
  projects/pigrocrm/apps/mcp/tests/test_update_proforma_tool.py \
  projects/pigrocrm/apps/web/src/lib/api-types.ts
git add -u projects/pigrocrm/apps/web/src
git commit -m "feat(mcp): the proforma tools take the customer's purchase order" -m "create_proforma and update_proforma accept the order number, date, CIG and CUP, the API round-trips them, and the web types are regenerated from the API.

REB-693."
```

---

### Task 7: The web edits and shows the order

**Files:**
- Create: `projects/pigrocrm/apps/web/src/features/invoices/purchaseOrder.ts`
- Create: `projects/pigrocrm/apps/web/src/features/invoices/purchaseOrder.test.ts`
- Create: `projects/pigrocrm/apps/web/src/features/invoices/PurchaseOrderEditor.tsx`
- Create: `projects/pigrocrm/apps/web/src/features/invoices/PurchaseOrderEditor.test.tsx`
- Modify: `projects/pigrocrm/apps/web/src/features/invoices/format.ts` and `format.test.ts` (`formatPurchaseOrder`)
- Modify: `projects/pigrocrm/apps/web/src/routes/app/invoices/$invoiceId.tsx`

**Interfaces:**
- Consumes: `Invoice` with `ordine_numero | ordine_data | ordine_cig | ordine_cup: string | null` (Task 6); `useUpdateInvoice(invoiceId)` (existing, `queries.ts`); `formatDate` (existing, `format.ts`).
- Produces: `PurchaseOrderDraft`, `purchaseOrderDraft(invoice)`, `validatePurchaseOrder(draft): string | undefined`, `purchaseOrderBody(draft): Record<string, string | null>`; `formatPurchaseOrder(invoice): string`; `<PurchaseOrderEditor invoice={Invoice} />`.

- [ ] **Step 1: Write the failing helper tests**

Create `purchaseOrder.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { purchaseOrderBody, purchaseOrderDraft, validatePurchaseOrder } from './purchaseOrder'
import type { Invoice } from './queries'

const EMPTY_DRAFT = { ordine_numero: '', ordine_data: '', ordine_cig: '', ordine_cup: '' }

describe('purchaseOrder', () => {
  it('starts from the stored order, with empty strings for what is missing', () => {
    const invoice = {
      ordine_numero: 'ODA-14',
      ordine_data: null,
      ordine_cig: 'Z0A1B2C3D4',
      ordine_cup: null,
    } as unknown as Invoice
    expect(purchaseOrderDraft(invoice)).toEqual({
      ordine_numero: 'ODA-14',
      ordine_data: '',
      ordine_cig: 'Z0A1B2C3D4',
      ordine_cup: '',
    })
  })

  it('reads an invoice from an API that predates the columns as no order', () => {
    expect(purchaseOrderDraft({} as unknown as Invoice)).toEqual(EMPTY_DRAFT)
  })

  it('sends every field, trimmed, with null for an empty input', () => {
    expect(
      purchaseOrderBody({
        ordine_numero: ' ODA-14 ',
        ordine_data: '2026-10-09',
        ordine_cig: '',
        ordine_cup: '  ',
      }),
    ).toEqual({ ordine_numero: 'ODA-14', ordine_data: '2026-10-09', ordine_cig: null, ordine_cup: null })
    expect(purchaseOrderBody(EMPTY_DRAFT)).toEqual({
      ordine_numero: null,
      ordine_data: null,
      ordine_cig: null,
      ordine_cup: null,
    })
  })

  it('refuses a date, a CIG or a CUP without the number, and nothing else', () => {
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_numero: ' ', ordine_cig: 'Z0A1B2C3D4' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_data: '2026-10-09' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_cup: 'J41E22000110005' })).toBe(
      'Indica il numero dell’ordine.',
    )
    expect(validatePurchaseOrder(EMPTY_DRAFT)).toBeUndefined()
    expect(validatePurchaseOrder({ ...EMPTY_DRAFT, ordine_numero: '8', ordine_cig: 'Z0A1B2C3D4' })).toBeUndefined()
  })
})
```

- [ ] **Step 2: Write the failing formatter test**

Append to `format.test.ts` (import `formatDate` and `formatPurchaseOrder` from `./format`, next to the existing imports):

```ts
describe('formatPurchaseOrder', () => {
  it('reads like the PDF line, with only the parts the order has', () => {
    expect(
      formatPurchaseOrder({
        ordine_numero: 'ODA-14',
        ordine_data: '2026-10-09',
        ordine_cig: 'Z0A1B2C3D4',
        ordine_cup: 'J41E22000110005',
      }),
    ).toBe(`n. ODA-14 del ${formatDate('2026-10-09')}, CIG Z0A1B2C3D4, CUP J41E22000110005`)
    expect(
      formatPurchaseOrder({ ordine_numero: '8', ordine_data: null, ordine_cig: null, ordine_cup: null }),
    ).toBe('n. 8')
  })

  it('is the dash without an order number', () => {
    expect(
      formatPurchaseOrder({ ordine_numero: null, ordine_data: null, ordine_cig: null, ordine_cup: null }),
    ).toBe('—')
  })
})
```

- [ ] **Step 3: Write the failing editor tests**

Create `PurchaseOrderEditor.test.tsx` (the house pattern of `InvoiceHeaderEditor.test.tsx`):

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import { PurchaseOrderEditor } from './PurchaseOrderEditor'
import type { Invoice } from './queries'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { GET: vi.fn(), POST: vi.fn(), PUT: vi.fn(), PATCH: vi.fn() } }
})
vi.mock('@rebase/ui/sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) } as never
}

function failed(error: unknown, status: number) {
  return { error, response: new Response(null, { status }) } as never
}

const PROFORMA = {
  id: 'pf-1',
  tipo: 'proforma',
  stato: 'bozza',
  ordine_numero: 'ODA-14',
  ordine_data: '2026-10-09',
  ordine_cig: null,
  ordine_cup: null,
} as unknown as Invoice

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>)
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

function sent(): Record<string, unknown> {
  const [first] = vi.mocked(api.PATCH).mock.calls
  if (!first) throw new Error('PATCH /api/invoices/{invoice_id} was never called')
  return (first[1] as { body: Record<string, unknown> }).body
}

beforeEach(() => {
  vi.mocked(api.PATCH).mockResolvedValue(ok(PROFORMA))
})

afterEach(() => {
  vi.mocked(api.PATCH).mockReset()
  vi.mocked(toast.success).mockReset()
})

describe('PurchaseOrderEditor', () => {
  it('starts from the stored order', () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    expect(screen.getByLabelText('Numero ordine')).toHaveValue('ODA-14')
    expect(screen.getByLabelText('Data ordine')).toHaveValue('2026-10-09')
    expect(screen.getByLabelText('CIG')).toHaveValue('')
    expect(screen.getByLabelText('CUP')).toHaveValue('')
  })

  it('saves all four fields through the PATCH, null for an empty one', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('CIG', ' z0a1b2c3d4 ')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(vi.mocked(api.PATCH).mock.calls[0]?.[0]).toBe('/api/invoices/{invoice_id}')
    expect(sent()).toEqual({
      ordine_numero: 'ODA-14',
      ordine_data: '2026-10-09',
      ordine_cig: 'z0a1b2c3d4',
      ordine_cup: null,
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Ordine salvato'))
  })

  it('refuses a CIG without the order number before any request', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('Numero ordine', '')
    type('CIG', 'Z0A1B2C3D4')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    expect(screen.getByText('Indica il numero dell’ordine.')).toBeInTheDocument()
    expect(api.PATCH).not.toHaveBeenCalled()
  })

  it('clears the whole order when every field is emptied', async () => {
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('Numero ordine', '')
    type('Data ordine', '')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    await waitFor(() => expect(api.PATCH).toHaveBeenCalled())
    expect(sent()).toEqual({ ordine_numero: null, ordine_data: null, ordine_cig: null, ordine_cup: null })
  })

  it('shows the server’s own refusal of a malformed CIG', async () => {
    vi.mocked(api.PATCH).mockResolvedValue(
      failed(
        {
          type: 'https://pigrocrm.dev/errors/validation_failed',
          title: 'Dati non validi',
          status: 422,
          detail: 'il CIG ha 10 caratteri, lettere e cifre',
          code: 'validation_failed',
          field: 'ordine_cig',
        },
        422,
      ),
    )
    wrap(<PurchaseOrderEditor invoice={PROFORMA} />)
    type('CIG', 'Z0A1')
    await userEvent.click(screen.getByRole('button', { name: 'Salva ordine' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('il CIG ha 10 caratteri')
    expect(toast.success).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 4: Run the web tests to verify they fail**

Run: `pnpm --filter web test -- purchaseOrder PurchaseOrderEditor format`
Expected: FAIL (modules not found, `formatPurchaseOrder` not exported).

- [ ] **Step 5: Write the helpers and the formatter**

Create `purchaseOrder.ts`:

```ts
import type { Invoice } from './queries'

/**
 * The customer's purchase order on a document that can still change (REB-693): its
 * number, and optionally its date, CIG and CUP, as the four inputs hold them.
 */
export interface PurchaseOrderDraft {
  ordine_numero: string
  ordine_data: string
  ordine_cig: string
  ordine_cup: string
}

type PurchaseOrderFields = Pick<Invoice, 'ordine_numero' | 'ordine_data' | 'ordine_cig' | 'ordine_cup'>

/** `?? ''`: a `null` from the API, and an absent key from an API that predates the
 * columns, both read as "not set". */
export function purchaseOrderDraft(invoice: PurchaseOrderFields): PurchaseOrderDraft {
  return {
    ordine_numero: invoice.ordine_numero ?? '',
    ordine_data: invoice.ordine_data ?? '',
    ordine_cig: invoice.ordine_cig ?? '',
    ordine_cup: invoice.ordine_cup ?? '',
  }
}

/** The one mistake worth catching before the request: a date, a CIG or a CUP with no
 * order to belong to. The CIG's and CUP's own shape is the server's to judge, and its
 * sentence is shown as it comes. */
export function validatePurchaseOrder(draft: PurchaseOrderDraft): string | undefined {
  const hasRest =
    draft.ordine_data !== '' || draft.ordine_cig.trim() !== '' || draft.ordine_cup.trim() !== ''
  if (draft.ordine_numero.trim() === '' && hasRest) return 'Indica il numero dell’ordine.'
  return undefined
}

/** All four, always: `null` is how the server clears a field, so an order emptied in
 * the form is cleared as a whole. */
export function purchaseOrderBody(draft: PurchaseOrderDraft): Record<string, string | null> {
  const text = (value: string) => (value.trim() === '' ? null : value.trim())
  return {
    ordine_numero: text(draft.ordine_numero),
    ordine_data: draft.ordine_data === '' ? null : draft.ordine_data,
    ordine_cig: text(draft.ordine_cig),
    ordine_cup: text(draft.ordine_cup),
  }
}
```

Note the editor test sends `' z0a1b2c3d4 '` and expects `'z0a1b2c3d4'`: trimming is the client's, upper-casing is the server's.

Append to `format.ts`:

```ts
/**
 * The purchase order as the detail page states it once the document is frozen
 * (REB-693): the same words the PDF prints, «n. ODA-14 del …, CIG …, CUP …», with only
 * the parts the order has. The dash without an order number.
 */
export function formatPurchaseOrder(
  invoice: Pick<Invoice, 'ordine_numero' | 'ordine_data' | 'ordine_cig' | 'ordine_cup'>,
): string {
  if (!invoice.ordine_numero) return EMPTY
  const numero = invoice.ordine_data
    ? `n. ${invoice.ordine_numero} del ${formatDate(invoice.ordine_data)}`
    : `n. ${invoice.ordine_numero}`
  const parts = [numero]
  if (invoice.ordine_cig) parts.push(`CIG ${invoice.ordine_cig}`)
  if (invoice.ordine_cup) parts.push(`CUP ${invoice.ordine_cup}`)
  return parts.join(', ')
}
```

`format.ts` already imports `type { Invoice }` from `./queries`.

- [ ] **Step 6: Write the editor**

Create `PurchaseOrderEditor.tsx`:

```tsx
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toProblem, type ProblemDetail } from '@/lib/api'
import {
  purchaseOrderBody,
  purchaseOrderDraft,
  validatePurchaseOrder,
  type PurchaseOrderDraft,
} from './purchaseOrder'
import { useUpdateInvoice, type Invoice } from './queries'

/**
 * The customer's purchase order on a document that is still editable (REB-693): the
 * number, its date, the CIG and the CUP, which the XML writes as `DatiOrdineAcquisto`
 * and the PDF prints under the accrual period. Its own section and button, beside
 * `InvoiceHeaderEditor`, whose section is the document's dates.
 *
 * Saved through `PATCH /api/invoices/{id}` with all four fields, `null` for an empty
 * one. The freeze is the server's: an issued fattura or a consumed proforma answers 409,
 * and the banner shows it as it comes.
 */
export function PurchaseOrderEditor({ invoice }: { invoice: Invoice }) {
  const update = useUpdateInvoice(invoice.id)
  const [draft, setDraft] = useState<PurchaseOrderDraft>(() => purchaseOrderDraft(invoice))
  const [error, setError] = useState<string | undefined>(undefined)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function set(field: keyof PurchaseOrderDraft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  function submit() {
    const found = validatePurchaseOrder(draft)
    setError(found)
    setProblem(null)
    if (found !== undefined) return
    update.mutate(purchaseOrderBody(draft), {
      onSuccess: () => toast.success('Ordine salvato'),
      onError: (failure) => setProblem(toProblem(failure)),
    })
  }

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">Riferimento ordine</h2>
      {problem ? <QueryErrorBanner error={problem} /> : null}

      <div className="grid max-w-2xl gap-4 sm:grid-cols-4">
        <div className="space-y-2">
          <Label htmlFor="ordine-numero">Numero ordine</Label>
          <Input
            id="ordine-numero"
            maxLength={20}
            value={draft.ordine_numero}
            onChange={(event) => set('ordine_numero', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-data">Data ordine</Label>
          <Input
            id="ordine-data"
            type="date"
            value={draft.ordine_data}
            onChange={(event) => set('ordine_data', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-cig">CIG</Label>
          <Input
            id="ordine-cig"
            maxLength={15}
            value={draft.ordine_cig}
            onChange={(event) => set('ordine_cig', event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="ordine-cup">CUP</Label>
          <Input
            id="ordine-cup"
            maxLength={15}
            value={draft.ordine_cup}
            onChange={(event) => set('ordine_cup', event.target.value)}
          />
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex items-center justify-between gap-4">
        <p className="text-muted-foreground text-xs">
          L’ordine del cliente finisce nell’XML della fattura (DatiOrdineAcquisto) e sul PDF. Data,
          CIG e CUP richiedono il numero; per togliere l’ordine svuota tutti i campi.
        </p>
        <Button variant="outline" size="sm" onClick={submit} disabled={update.isPending}>
          Salva ordine
        </Button>
      </div>
    </section>
  )
}
```

- [ ] **Step 7: Wire the detail page**

In `routes/app/invoices/$invoiceId.tsx`, import the editor and the formatter:

```tsx
import { PurchaseOrderEditor } from '@/features/invoices/PurchaseOrderEditor'
```

and add `formatPurchaseOrder` to the existing `@/features/invoices/format` import list. Right after `{readOnly ? null : <InvoiceHeaderEditor invoice={row} />}`:

```tsx
            {readOnly ? null : <PurchaseOrderEditor invoice={row} />}
```

Inside the `<dl>`, right after the read-only «Periodo di competenza» fragment:

```tsx
              {readOnly && row.ordine_numero ? (
                <>
                  <dt className="text-muted-foreground">Riferimento ordine</dt>
                  <dd>{formatPurchaseOrder(row)}</dd>
                </>
              ) : null}
```

- [ ] **Step 8: Run the web checks to verify they pass**

Run: `pnpm --filter web test && pnpm --filter web lint && pnpm --filter web build`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add projects/pigrocrm/apps/web/src/features/invoices/purchaseOrder.ts \
  projects/pigrocrm/apps/web/src/features/invoices/purchaseOrder.test.ts \
  projects/pigrocrm/apps/web/src/features/invoices/PurchaseOrderEditor.tsx \
  projects/pigrocrm/apps/web/src/features/invoices/PurchaseOrderEditor.test.tsx \
  projects/pigrocrm/apps/web/src/features/invoices/format.ts \
  projects/pigrocrm/apps/web/src/features/invoices/format.test.ts \
  'projects/pigrocrm/apps/web/src/routes/app/invoices/$invoiceId.tsx'
git commit -m "feat(web): a draft or proforma page edits the customer's purchase order" -m "A Riferimento ordine section beside the document's dates, with its own save, that refuses a CIG without the order number before the request; an issued invoice states the order as a row.

REB-693."
```

---

### Task 8: The whole tree, before the PR (controller)

- [ ] **Step 1: Python gates**

Run: `uv run ruff check projects/pigrocrm && uv run ruff format --check projects/pigrocrm && uv run mypy`
Expected: clean.

- [ ] **Step 2: The CRM Python suite, as three processes**

Run, one after the other:
`TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q -n 2 --dist loadfile -m "not slow and not planner" projects/pigrocrm/packages/core/tests`
`TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q -p no:xdist -m "not slow and not planner" projects/pigrocrm/apps/api/tests`
`TESTCONTAINERS_RYUK_DISABLED=true uv run pytest -q -p no:xdist -m "not slow and not planner" projects/pigrocrm/apps/mcp/tests`
Expected: PASS. A single ryuk «port 8080 is not available» setup error is a start-up flake: rerun that process once.

- [ ] **Step 3: Web gates**

Run: `pnpm --filter web lint && pnpm --filter web test && pnpm --filter web build`
Expected: PASS.

- [ ] **Step 4: Commit trailers**

Run: `git log origin/main..HEAD --format='%h %s%n%b' | grep -i 'co-authored' || echo clean`
Expected: `clean`.
