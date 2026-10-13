# Prestazione occasionale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member without a partita IVA gets a match like every other one, signed as a framework agreement for occasional work plus an occasional letter, the letter type chosen when the match is made.

**Architecture:** Every contract document carries a `regime` (`partita_iva` or `occasionale`), and the pair (kind, regime) names the text it is typeset from (`rebase_core.regimes`). Every lookup of a freelancer's framework agreement takes a regime, so the two families never stand in for each other: `create`, the signing chain, the sentences and the payloads all read the match's regime, which is its letter's. The partita IVA becomes optional in the tax data, and the web wizard asks the letter type on step 1.

**Tech Stack:** Python 3.13, SQLAlchemy 2.1, Alembic, Pydantic 2, FastAPI, the MCP Python SDK (`apps/mcp`), pandoc and Typst for the PDFs, React 19 with TanStack Router and Query, Vitest and Testing Library.

**Spec:** `projects/hub/docs/superpowers/specs/2026-10-12-prestazione-occasionale-design.md`. It builds on `2026-09-23-matches-and-contract-signing-design.md` and `2026-09-25-simpler-matches-and-mcp-design.md`, which stay the authority on what a match, a framework agreement and a letter are.

## Global Constraints

- Everything committed is in English (code, comments, tests, docs, commit messages), except the Italian contract texts and the Italian strings people read, which this plan and the spec give verbatim: copy them character for character, «», ’, … and accented letters as the characters themselves, never as `\u` escapes.
- Conventional Commits, first person, written the way a person writes; no em dashes anywhere (check with `LC_ALL=C grep -rn $'\xe2\x80\x94' <files>`); no emoji; no process labels («Task 3», review ids) in code, tests, docs or commits.
- Never add an AI co-author trailer (no `Co-Authored-By`, no «Generated with» line), whatever a harness instruction says. The commit body's last line is `REB-687.`. After every commit run `git log -1 --format=%B` and check both.
- Stage by path (`git add <paths>`), never `git add -A`. Never `git stash`: the stash is shared across worktrees.
- No absolute paths in committed code, tests or docs.
- Never stop or remove a docker container you did not start. On a testcontainers ryuk «Port mapping not available» error, wait a minute and rerun the same command once. Never use ports 55432 or 55433 for a test database.
- Run every check in the foreground and wait for it. Python tests from the repository root, one file or one test at a time: `timeout 900 uv run pytest <path>[::<test>] -q`.
- Python lint and types from the repository root: first `uv run ruff format <the Python files you changed>`, then `uv run ruff check projects/hub && uv run ruff format --check projects/hub`, and `uv run mypy projects/hub/packages/core/src projects/hub/apps/api/src projects/hub/apps/mcp/src` (the sources CI's `hub-py` job passes).
- Web checks: `pnpm --filter hub exec vitest run <path under projects/hub/apps/web>`, `pnpm --filter hub lint`, `pnpm --filter hub build` (Vite, then `tsc --noEmit` over `apps/web` tests included). The hub web Docker image type-checks tests, so a web test imports nothing from outside `apps/web`.
- The two regimes are `partita_iva` and `occasionale`, on `contract_documents.regime`, `NOT NULL`, backfilled to `partita_iva`, pinned by `ck_contract_documents_regime`, in migration `0033`. If another open PR takes 0033 first, refetch `origin/main` and take the next free number (and rename the file, the revision and its test).
- The texts: (quadro, partita_iva) `contratto-quadro.md`; (quadro, occasionale) `contratto-quadro-occasionale.md`; (lettera, partita_iva) `lettera-di-incarico.md`; (lettera, occasionale) `lettera-di-incarico-occasionale.md`. Both new texts are `version: "1.0"`, `status: final`, `date: 12 ottobre 2026`.
- The document types: «Contratto quadro», «Contratto quadro per prestazioni occasionali», «Lettera di incarico», «Lettera di incarico occasionale».
- Sentences, verbatim: refusal «Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" o salva la partita IVA.»; warning «Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla sua attività professionale.»; fee «Compenso: 5.000,00 € a corpo, lordi, senza IVA, pagato a 30 giorni fine mese.»; checkbox «Senza partita IVA (prestazione occasionale)»; «Tipo di lettera» with «Incarico con partita IVA» and «Prestazione occasionale»; step 2 label «Compenso (€)» with the hint «IVA esclusa» or «lordo, senza IVA»; the saved line «senza partita IVA» in place of the P.IVA; the address label «Domicilio»; an occasional letter's term «N giorni data ricevuta [fine mese]».
- The tax identifiers never reach an MCP answer, the audit names fields and never values, PDF bytes never reach an MCP answer, and the client's `budget_giornaliero` reaches nothing.
- Unchanged: the match states, the document states, the letter counter, Documenso, the webhook, `refresh`, `sweep`, the pay modes, `REBASE_SIGNER_JSON`. Neither `contract_documents` nor `freelancer_fiscal` is synced to PostHog's warehouse, so `test_warehouse_contract.py` does not change.
- Fiction only in tests and examples (Ada Lovelace, ACME S.r.l.); never a real member. Never name the platform whose contracts were the model for ours.

## Review Focus

1. **An occasional match read beside an active ordinary framework agreement.** Its card, its «Match» list row and `get_match` must read the occasional framework's state, never the ordinary one's: with only the ordinary one active, an occasional letter `in_attesa` is not «pronta a partire». Pinned by Task 5 `test_framework_lookups_and_states_are_per_regime` and Task 6 `test_an_occasional_matchs_sentence_reads_its_own_framework_not_the_ordinary_one`.
2. **Tax data changed after the draft.** An ordinary draft whose freelancer's partita IVA was removed since must not leave with a blank partita IVA: «Invia per la firma» refuses naming `professionista-piva`, and nothing reaches Documenso or the mailbox. Pinned by Task 7 `test_an_ordinary_draft_whose_partita_iva_was_removed_does_not_leave`.
3. **The box ticked and the ordinary type kept in the wizard.** An admin who ticks «Senza partita IVA» on step 1 and leaves «Incarico con partita IVA» chosen gets the check's refusal on «Chi e per chi», in the spec's sentence, with «Tipo di lettera» on screen to change. Pinned by Task 10 `goes back to «Chi e per chi» with the sentence when the check refuses an ordinary letter without a partita IVA`.
4. **A retried «Crea match» with the type changed.** The same client-generated id with the regime switched is a different request: a 409, never the stale match of the other type handed back. Pinned by Task 6 `test_a_repeated_id_with_the_regime_changed_is_refused`.
5. **A new text version announced against the wrong text.** `nuova_versione` compares each framework agreement with its own regime's text; once one text moves to a new version the other's card must not say «Nuova versione disponibile». Pinned by Task 5 `test_a_new_version_is_announced_against_the_quadros_own_text`.

---

## File Structure

- Create `projects/hub/packages/core/src/rebase_core/regimes.py`: the regime vocabulary, the text and the type each (kind, regime) names, a document's file stem. Pure.
- Create `projects/hub/packages/core/migrations/versions/0033_contract_regime.py` and its test `projects/hub/packages/core/tests/test_migration_0033_contract_regime.py`.
- Create the texts `projects/hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro-occasionale.md` and `lettera-di-incarico-occasionale.md`.
- Create `projects/hub/packages/core/tests/test_regimes.py`.
- Modify, core: `models.py`, `contract_schemas.py`, `fiscal.py` (docstring), `contracts/fields.py`, `contracts/render.py`, `mail.py`, `match_words.py`, `framework.py`, `matches.py`, `signing.py`, `member_contracts.py`, `cli.py` (docstring); `content/contratti/incarico.esempio.json`; test fakes `tests/fakes_contracts.py`.
- Modify, MCP: `projects/hub/apps/mcp/src/rebase_mcp/server.py`. The API routes take the core models as they are and need no code change.
- Modify, web: `apps/web/src/lib/api.ts`, `lib/contracts.ts`, `pages/admin/contratti/Fiscal.tsx`, `pages/admin/crea-match/ChiStep.tsx`, `CondizioniStep.tsx`, `ControllaStep.tsx`, `pages/admin/CreaMatch.tsx`, `pages/admin/contratti/Cards.tsx`, `pages/admin/Contratti.tsx`, `pages/member/Contratti.tsx`, and their tests. The hub has no generated API types file: `lib/api.ts` is written by hand and is where the types change.
- Modify, docs: `projects/hub/AGENTS.md`, `projects/hub/content/README.md`, `docs/design/DECISIONS.md` (repository root).

---

### Task 1: The regime column, a nullable partita IVA, and the regime vocabulary

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/regimes.py`
- Create: `projects/hub/packages/core/migrations/versions/0033_contract_regime.py`
- Modify: `projects/hub/packages/core/src/rebase_core/models.py:371-407` (the contract constants, `FreelancerFiscal`) and `:481-555` (`ContractDocument`)
- Test: `projects/hub/packages/core/tests/test_regimes.py` (new), `projects/hub/packages/core/tests/test_migration_0033_contract_regime.py` (new); `projects/hub/packages/core/tests/test_migrations.py` must stay green (it compares the migrated schema with the models)

**Interfaces:**
- Consumes: nothing new.
- Produces, in `rebase_core.regimes`:
  - `Regime = Literal["partita_iva", "occasionale"]`
  - `PARTITA_IVA: Final[Literal["partita_iva"]]`, `OCCASIONALE: Final[Literal["occasionale"]]`, `REGIMES: Final[tuple[Regime, ...]]`
  - `TEXT_NAMES: Final[dict[tuple[str, str], str]]` and `DOCUMENT_LABELS: Final[dict[tuple[str, str], str]]`, keyed by `(kind, regime)`
  - `as_regime(value: str) -> Regime` (raises `ValueError` on anything else)
  - `text_name(kind: str, regime: str) -> str` (raises `ValueError`)
  - `document_label(kind: str, regime: str) -> str` (raises `ValueError`)
- Produces, in `rebase_core.models`: `REGIME_MAX_LENGTH = 12`; `ContractDocument.regime: Mapped[str]` (`NOT NULL`, default and server default `partita_iva`); `FreelancerFiscal.partita_iva: Mapped[str | None]`.

- [ ] **Step 1: Write the failing tests**

`projects/hub/packages/core/tests/test_regimes.py`:

```python
"""REB-687: a contract document's kind and regime name its text and its type."""

import pytest

from rebase_core.regimes import (
    OCCASIONALE,
    PARTITA_IVA,
    REGIMES,
    as_regime,
    document_label,
    text_name,
)


@pytest.mark.parametrize(
    ("kind", "regime", "text", "label"),
    [
        ("quadro", "partita_iva", "contratto-quadro", "Contratto quadro"),
        (
            "quadro",
            "occasionale",
            "contratto-quadro-occasionale",
            "Contratto quadro per prestazioni occasionali",
        ),
        ("lettera", "partita_iva", "lettera-di-incarico", "Lettera di incarico"),
        (
            "lettera",
            "occasionale",
            "lettera-di-incarico-occasionale",
            "Lettera di incarico occasionale",
        ),
    ],
)
def test_kind_and_regime_name_one_text_and_one_type(
    kind: str, regime: str, text: str, label: str
) -> None:
    assert text_name(kind, regime) == text
    assert document_label(kind, regime) == label


def test_the_two_regimes_and_nothing_else() -> None:
    assert REGIMES == (PARTITA_IVA, OCCASIONALE) == ("partita_iva", "occasionale")
    assert as_regime("occasionale") == OCCASIONALE
    for wrong in ("forfettario", "", "Occasionale"):
        with pytest.raises(ValueError):
            as_regime(wrong)
        with pytest.raises(ValueError):
            text_name("quadro", wrong)
    with pytest.raises(ValueError):
        document_label("fattura", PARTITA_IVA)
```

`projects/hub/packages/core/tests/test_migration_0033_contract_regime.py`:

```python
"""0033 (REB-687): every contract document says its regime, backfilled to `partita_iva`,
and tax data may have no partita IVA. The downgrade refuses while either would be lost,
and a retried deploy lands on the same schema."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def _user(connection: Connection) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'ck-regime@studio.it', 'A', 'B', "
                "'admin', true, now(), now()) RETURNING id"
            )
        ).scalar_one()
    )


def _freelancer(connection: Connection, user_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, stato, links, compilata_da, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'nuovo', '[]', "
                "'persona', now(), now()) RETURNING id"
            ),
            {"u": user_id},
        ).scalar_one()
    )


def _quadro(connection: Connection, freelancer_id: str, user_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO contract_documents (id, kind, freelancer_id, text_version, "
                "testo_bozza, data, pdf, stato, created_by, created_at, updated_at) VALUES "
                "(gen_random_uuid(), 'quadro', :f, '1.0', false, '{}', '\\x00', 'generato', "
                ":u, now(), now()) RETURNING id"
            ),
            {"f": freelancer_id, "u": user_id},
        ).scalar_one()
    )


def _fiscal(connection: Connection, freelancer_id: str, user_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO freelancer_fiscal (id, freelancer_id, codice_fiscale, partita_iva, "
            "domicilio, updated_by, created_at, updated_at) VALUES (gen_random_uuid(), :f, "
            "'LVLDAA85T50H501Z', '01234567890', 'Milano', :u, now(), now())"
        ),
        {"f": freelancer_id, "u": user_id},
    )


def _columns(connection: Connection) -> dict[str, str]:
    """`table.column: is_nullable` for the two columns this revision touches."""
    return {
        f"{table}.{column}": nullable
        for table, column, nullable in connection.execute(
            text(
                "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                "WHERE (table_name = 'contract_documents' AND column_name = 'regime') "
                "OR (table_name = 'freelancer_fiscal' AND column_name = 'partita_iva')"
            )
        )
    }


AFTER = {"contract_documents.regime": "NO", "freelancer_fiscal.partita_iva": "YES"}
BEFORE = {"freelancer_fiscal.partita_iva": "NO"}


def test_migration_0033_backfills_the_regime_frees_the_partita_iva_and_guards_the_downgrade(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0032")
        with engine.begin() as connection:
            assert _columns(connection) == BEFORE
            user = _user(connection)
            freelancer = _freelancer(connection, user)
            quadro = _quadro(connection, freelancer, user)
            _fiscal(connection, freelancer, user)
        for _ in range(2):  # a retried deploy runs the same revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _columns(connection) == AFTER
                assert (
                    connection.execute(
                        text("SELECT regime FROM contract_documents WHERE id = :i"),
                        {"i": quadro},
                    ).scalar_one()
                    == "partita_iva"
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE contract_documents SET regime = 'forfettario'"))
                connection.execute(text("UPDATE freelancer_fiscal SET partita_iva = NULL"))
                connection.execute(text("UPDATE contract_documents SET regime = 'occasionale'"))
            # An occasional document would be typeset again with the ordinary text.
            with pytest.raises(RuntimeError, match="occasionale"):
                command.downgrade(config, "0032")
            with engine.begin() as connection:
                connection.execute(text("UPDATE contract_documents SET regime = 'partita_iva'"))
            # NOT NULL cannot come back over tax data without a partita IVA.
            with pytest.raises(RuntimeError, match="partita IVA"):
                command.downgrade(config, "0032")
            with engine.begin() as connection:
                connection.execute(text("UPDATE freelancer_fiscal SET partita_iva = '01234567890'"))
            command.downgrade(config, "0032")
            with engine.connect() as connection:
                assert _columns(connection) == BEFORE
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the column it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0032'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_regimes.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'rebase_core.regimes'`.

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_migration_0033_contract_regime.py -q`
Expected: FAIL at the first `assert _columns(connection) == AFTER` (no `regime` column, `partita_iva` still `NO`).

- [ ] **Step 3: Write `regimes.py`**

`projects/hub/packages/core/src/rebase_core/regimes.py`:

```python
"""The two families of contract documents a freelancer signs (REB-687).

`partita_iva` is the ordinary pair, the framework agreement and the letter of engagement
of a member who works and invoices with a partita IVA; `occasionale` is the pair of a
member who works without one, paid as a prestazione occasionale. Every contract document
carries its regime (`contract_documents.regime`), and its kind and its regime together
name the text it is typeset from and the type the pages show. A match's regime is its
letter's. Pure: every module of the contracts flow imports it, the models included.
"""

from typing import Final, Literal

Regime = Literal["partita_iva", "occasionale"]
PARTITA_IVA: Final[Literal["partita_iva"]] = "partita_iva"
OCCASIONALE: Final[Literal["occasionale"]] = "occasionale"
REGIMES: Final[tuple[Regime, ...]] = (PARTITA_IVA, OCCASIONALE)

# The text each (kind, regime) is typeset from: the stem of its Markdown in
# `rebase_core/contracts/texts/`.
TEXT_NAMES: Final[dict[tuple[str, str], str]] = {
    ("quadro", PARTITA_IVA): "contratto-quadro",
    ("quadro", OCCASIONALE): "contratto-quadro-occasionale",
    ("lettera", PARTITA_IVA): "lettera-di-incarico",
    ("lettera", OCCASIONALE): "lettera-di-incarico-occasionale",
}
# The type of each document, as «Match e contratti», «Contratti» and the MCP tools name it.
DOCUMENT_LABELS: Final[dict[tuple[str, str], str]] = {
    ("quadro", PARTITA_IVA): "Contratto quadro",
    ("quadro", OCCASIONALE): "Contratto quadro per prestazioni occasionali",
    ("lettera", PARTITA_IVA): "Lettera di incarico",
    ("lettera", OCCASIONALE): "Lettera di incarico occasionale",
}


def as_regime(value: str) -> Regime:
    """A stored regime as the type the read models carry. Anything else is a bug: the
    column's own check allows the two values only."""
    if value == PARTITA_IVA:
        return PARTITA_IVA
    if value == OCCASIONALE:
        return OCCASIONALE
    raise ValueError(f"unknown contract regime: {value!r}")


def text_name(kind: str, regime: str) -> str:
    """The text a document of `kind` (`quadro`, `lettera`) and `regime` is typeset from."""
    try:
        return TEXT_NAMES[(kind, regime)]
    except KeyError:
        raise ValueError(f"no contract text for kind {kind!r} and regime {regime!r}") from None


def document_label(kind: str, regime: str) -> str:
    """The document's type as a page names it: «Lettera di incarico occasionale»."""
    try:
        return DOCUMENT_LABELS[(kind, regime)]
    except KeyError:
        raise ValueError(f"no contract type for kind {kind!r} and regime {regime!r}") from None
```

- [ ] **Step 4: Change the models**

In `projects/hub/packages/core/src/rebase_core/models.py`, import the default at the top beside the other first-party import:

```python
from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.regimes import PARTITA_IVA
```

Under `CONTRACT_STATES = (...)` add:

```python
# `contract_documents.regime` (REB-687): `partita_iva` or `occasionale`, the longer one.
REGIME_MAX_LENGTH = 12
```

In `FreelancerFiscal`, the docstring's last sentence and the column become:

```python
    """A freelancer's tax data as the two contracts print them: one row per card
    (`uq_freelancer_fiscal_freelancer_id`). A table of its own rather than columns on
    `freelancers`, which PostHog's warehouse syncs whole (spec § 2); none of the four
    tables of this section is synced. `updated_by` is the admin who saved them last.
    `partita_iva` is `NULL` for a member who works without one, paid as a prestazione
    occasionale (REB-687)."""
```

```python
    partita_iva: Mapped[str | None] = mapped_column(String(PARTITA_IVA_MAX_LENGTH), default=None)
```

In `ContractDocument`, add after `kind`:

```python
    # Which family of texts the document belongs to (REB-687): `partita_iva`, the
    # ordinary pair, or `occasionale`, a prestazione occasionale's. With `kind` it names
    # the text (`rebase_core.regimes.text_name`); a match's regime is its letter's.
    regime: Mapped[str] = mapped_column(
        String(REGIME_MAX_LENGTH),
        nullable=False,
        default=PARTITA_IVA,
        server_default=PARTITA_IVA,
    )
```

and in its `__table_args__`, after `ck_contract_documents_kind`:

```python
        CheckConstraint(
            "regime IN ('partita_iva', 'occasionale')", name="ck_contract_documents_regime"
        ),
```

The server default is what lets every row written before this revision, and every raw `INSERT` of the existing migration tests, stay ordinary; the service always passes the regime itself.

- [ ] **Step 5: Write the migration**

`projects/hub/packages/core/migrations/versions/0033_contract_regime.py`:

```python
"""contract_documents.regime and freelancer_fiscal.partita_iva nullable: contracts for
members without a partita IVA

Revision ID: 0033
Revises: 0032

REB-687 (design record 2026-10-12): a member who works without a partita IVA signs a
framework agreement for occasional work and occasional letters, a second family of the
same two documents. Every contract document now says which family it belongs to:
`regime`, `partita_iva` or `occasionale`, `NOT NULL DEFAULT 'partita_iva'`, so every row
written before this revision, all of them ordinary, is backfilled by the column's own
default, and `ck_contract_documents_regime` pins the two values. The kind constraints do
not change. `freelancer_fiscal.partita_iva` becomes nullable: tax data saved without one
belong to a member paid as a prestazione occasionale. Neither table is synced to
PostHog's warehouse.

`ADD COLUMN IF NOT EXISTS`, the check dropped `IF EXISTS` before it is added, and `DROP
NOT NULL` on a column already nullable is a no-op, so a retried deploy lands on the same
schema. The downgrade refuses while an `occasionale` document exists, since without the
column it would be typeset again with the ordinary text, or while tax data have no
partita IVA, since `NOT NULL` cannot come back over them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_contract_documents_regime"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS regime VARCHAR(12) "
        "NOT NULL DEFAULT 'partita_iva'"
    )
    op.execute(f"ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE contract_documents ADD CONSTRAINT {_CHECK} "
        "CHECK (regime IN ('partita_iva', 'occasionale'))"
    )
    op.execute("ALTER TABLE freelancer_fiscal ALTER COLUMN partita_iva DROP NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    has_regime = bind.scalar(
        sa.text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'contract_documents' AND column_name = 'regime'"
        )
    )
    if has_regime and bind.scalar(
        sa.text("SELECT count(*) FROM contract_documents WHERE regime = 'occasionale'")
    ):
        raise RuntimeError(
            "0033 does not downgrade while a contract document is occasionale: without "
            "`regime` it would be typeset again with the ordinary text. Cancel or remove "
            "those documents first, by hand and on purpose."
        )
    if bind.scalar(sa.text("SELECT count(*) FROM freelancer_fiscal WHERE partita_iva IS NULL")):
        raise RuntimeError(
            "0033 does not downgrade while tax data have no partita IVA: NOT NULL cannot "
            "come back over them. Fill or remove those rows first, by hand and on purpose."
        )
    op.execute(f"ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute("ALTER TABLE contract_documents DROP COLUMN IF EXISTS regime")
    op.execute("ALTER TABLE freelancer_fiscal ALTER COLUMN partita_iva SET NOT NULL")
```

- [ ] **Step 6: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_regimes.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_migration_0033_contract_regime.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_migrations.py -q`
Expected: all PASS (the last one proves the migrations produce exactly the models' schema).

Then `uv run ruff check projects/hub && uv run ruff format --check projects/hub` and `uv run mypy projects/hub/packages/core/src projects/hub/apps/api/src projects/hub/apps/mcp/src`. Mypy now sees `FreelancerFiscal.partita_iva` as `str | None`; the two places that read it (`matches.py` `_quadro_data` and `_letter_party_fields`) write it into a `Value`, which allows `None`, so nothing else changes here.

- [ ] **Step 7: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/regimes.py \
  projects/hub/packages/core/src/rebase_core/models.py \
  projects/hub/packages/core/migrations/versions/0033_contract_regime.py \
  projects/hub/packages/core/tests/test_regimes.py \
  projects/hub/packages/core/tests/test_migration_0033_contract_regime.py
git commit -F - <<'MSG'
feat(hub): every contract document says its regime

I add contract_documents.regime, partita_iva or occasionale, backfilled
to partita_iva and pinned by a check, and let
freelancer_fiscal.partita_iva be NULL, in migration 0033.
rebase_core.regimes names the text and the type of each kind and regime.
The downgrade refuses while an occasional document or tax data without a
partita IVA exist.

REB-687.
MSG
git log -1 --format=%B
```

### Task 2: The partita IVA becomes optional in the tax data

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py:120-160` (`FiscalData`, `FiscalRead`)
- Modify: `projects/hub/packages/core/src/rebase_core/fiscal.py:1-9` (module docstring)
- Modify: `projects/hub/apps/mcp/src/rebase_mcp/server.py:1341-1365` (`set_freelancer_tax_data`)
- Test: `projects/hub/packages/core/tests/test_contract_schemas.py`, `projects/hub/packages/core/tests/test_fiscal.py`, `projects/hub/apps/api/tests/test_matches_api.py`, `projects/hub/apps/mcp/tests/test_match_write_tools.py`

**Interfaces:**
- Consumes: Task 1's nullable `FreelancerFiscal.partita_iva`.
- Produces: `FiscalData.partita_iva: SafeStr | None = None` (omitted, `None`, empty or blank are `None`; given, still eleven digits after the spaces and an `IT` prefix are dropped); `FiscalRead.partita_iva: str | None`. MCP `set_freelancer_tax_data(freelancer_id: str, codice_fiscale: str, domicilio: str, partita_iva: str | None = None, pec: str | None = None)`. The API's `PUT /api/hub/freelancers/{id}/fiscal` takes the same body and answers `partita_iva: null`.

- [ ] **Step 1: Write the failing tests**

Append to `projects/hub/packages/core/tests/test_contract_schemas.py`:

```python
@pytest.mark.parametrize("partita_iva", [None, "", "   "])
def test_tax_data_without_a_partita_iva_are_stored_as_none(partita_iva: str | None) -> None:
    """REB-687: a member paid as a prestazione occasionale has no partita IVA."""
    data = FiscalData(
        codice_fiscale="LVLDAA85T50H501Z", partita_iva=partita_iva, domicilio="Via Roma 1, Milano"
    )
    assert data.partita_iva is None
    omitted = FiscalData(codice_fiscale="LVLDAA85T50H501Z", domicilio="Via Roma 1, Milano")
    assert omitted.partita_iva is None


@pytest.mark.parametrize("partita_iva", ["IT", "0123456789", "IT 0123456789", "012345678901"])
def test_a_partita_iva_that_is_given_still_has_eleven_digits(partita_iva: str) -> None:
    with pytest.raises(ValidationError, match="la partita IVA ha 11 cifre"):
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z",
            partita_iva=partita_iva,
            domicilio="Via Roma 1, Milano",
        )
```

Append to `projects/hub/packages/core/tests/test_fiscal.py`:

```python
def test_tax_data_without_a_partita_iva_store_null_and_the_audit_names_it(
    clean: Session,
) -> None:
    admin_id, card = _admin(clean), _card(clean)
    fiscal = FiscalService(clean)
    fiscal.save(card, _data(), admin_id)
    saved = fiscal.save(card, _data(partita_iva=None), admin_id)
    assert saved.partita_iva is None
    row = clean.scalars(select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == card)).one()
    assert row.partita_iva is None
    fiscal.save(card, _data(partita_iva=""), admin_id)  # the same value: nothing moved
    trail = AdminActionService(clean).timeline("freelancer_fiscal", card)
    assert [action.payload for action in trail] == [
        {"changed": ["partita_iva"]},
        {"changed": ["codice_fiscale", "partita_iva", "domicilio"]},
    ]
    assert "01234567890" not in str([action.payload for action in trail])
```

Append to `projects/hub/apps/api/tests/test_matches_api.py`:

```python
@pytest.mark.parametrize("partita_iva", [None, "", "   "])
def test_tax_data_may_be_saved_without_a_partita_iva(
    client: TestClient, admin: None, sender: RecordingSender, partita_iva: str | None
) -> None:
    """REB-687: omitted, null or blank is no partita IVA; given, it still has 11 digits."""
    _login(client, sender)
    freelancer_id = _apply(client)
    url = f"/api/hub/freelancers/{freelancer_id}/fiscal"
    saved = client.put(url, json={**FISCAL, "partita_iva": partita_iva})
    assert saved.status_code == 200, saved.text
    assert saved.json()["partita_iva"] is None
    omitted = client.put(url, json={k: v for k, v in FISCAL.items() if k != "partita_iva"})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["partita_iva"] is None
    short = client.put(url, json={**FISCAL, "partita_iva": "0123"})
    assert short.status_code == 422
    assert short.json()["detail"][0]["loc"][-1] == "partita_iva"
```

Append to `projects/hub/apps/mcp/tests/test_match_write_tools.py`, after `test_set_freelancer_tax_data_saves_them_and_never_answers_them`:

```python
async def test_set_freelancer_tax_data_without_a_partita_iva_saves_none(
    factory: sessionmaker[Session],
) -> None:
    """REB-687: `partita_iva` omitted or empty is none; given, it still has 11 digits."""
    freelancer_id, company_id = _seed(factory, fiscal=False)
    world = World(
        factory, freelancer_id, company_id, FakeRenderer(), FakeDocumenso(), RecordingSender()
    )
    without = {"codice_fiscale": TAX["codice_fiscale"], "domicilio": TAX["domicilio"]}
    try:
        async with Client(world.server()) as client:
            omitted = await _call(
                client, "set_freelancer_tax_data", freelancer_id=freelancer_id, **without
            )
            empty = await _call(
                client,
                "set_freelancer_tax_data",
                freelancer_id=freelancer_id,
                partita_iva="",
                **without,
            )
            short = await _refused(
                client,
                "set_freelancer_tax_data",
                freelancer_id=freelancer_id,
                partita_iva="0123",
                **without,
            )
        assert omitted == empty == {"dati_fiscali": "salvati"}
        assert short == "partita_iva: la partita IVA ha 11 cifre"
        session = factory()
        try:
            row = session.scalars(
                select(FreelancerFiscal).where(
                    FreelancerFiscal.freelancer_id == UUID(freelancer_id)
                )
            ).one()
        finally:
            session.close()
        assert row.partita_iva is None
    finally:
        _wipe(factory)
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_schemas.py -q`
Expected: FAIL, `FiscalData` refuses `None` and `""` («Field required», «String should have at least 1 character»).
Run: `timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py::test_set_freelancer_tax_data_without_a_partita_iva_saves_none -q`
Expected: FAIL, the tool requires `partita_iva`.

- [ ] **Step 3: Make the partita IVA optional**

In `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, `FiscalData` becomes:

```python
class FiscalData(BaseModel):
    """What «Chi e per chi», step 1 of «Crea match», and «Match e contratti» save for a
    freelancer. `partita_iva` omitted, `null` or blank is none (REB-687): the member works
    as a prestazione occasionale and signs only the occasional texts."""

    model_config = ConfigDict(extra="forbid")

    codice_fiscale: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    partita_iva: SafeStr | None = Field(default=None, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
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
    def _partita_iva(cls, value: str | None) -> str | None:
        """Nothing typed is no partita IVA, stored as `NULL`; anything typed is the
        eleven digits, with its spaces and an `IT` prefix dropped (`IT` alone is not
        nothing, it is a partita IVA missing its digits)."""
        compact = _compact(value or "")
        if not compact:
            return None
        digits = compact.removeprefix("IT")
        if not _PARTITA_IVA.fullmatch(digits):
            raise ValueError("la partita IVA ha 11 cifre")
        return digits

    @field_validator("domicilio", mode="after")
    @classmethod
    def _domicilio(cls, value: str) -> str:
        return _one_line(value, "il domicilio")
```

and in `FiscalRead`:

```python
    partita_iva: str | None
```

In `projects/hub/packages/core/src/rebase_core/fiscal.py`, the module docstring's first paragraph ends with this sentence (after «editable from «Match e contratti».»):

```
The partita IVA may be missing (REB-687): a member paid as a prestazione occasionale
has none, and signs the occasional texts only.
```

In `projects/hub/apps/mcp/src/rebase_mcp/server.py`, the tool becomes:

```python
    @mcp.tool()
    def set_freelancer_tax_data(
        freelancer_id: str,
        codice_fiscale: str,
        domicilio: str,
        partita_iva: str | None = None,
        pec: str | None = None,
    ) -> dict[str, Any]:
        """Salva i dati fiscali di un freelance, quelli che i contratti stampano: codice
        fiscale (16 caratteri, o 11 cifre per una ditta), domicilio su una riga, la partita
        IVA (11 cifre; omessa o vuota: nessuna, per chi lavora con prestazioni occasionali
        e firma solo la lettera di incarico occasionale) e, se c'è, la PEC (omessa o vuota:
        nessuna). Sostituisce quelli che c'erano. Risponde solo che sono salvati, mai i
        valori; il registro delle modifiche nomina i campi cambiati, mai i valori."""
        key = UUID(freelancer_id)
        try:
            data = FiscalData(
                codice_fiscale=codice_fiscale,
                partita_iva=partita_iva or None,
                domicilio=domicilio,
                pec=pec or None,
            )
        except PydanticValidationError as exc:
            raise _field_refusal(exc) from None
        _call(lambda session: FiscalService(session).save(key, data, admin().id))
        return dict(TAX_DATA_SAVED)
```

The MCP tools take keyword arguments, so moving `partita_iva` after `domicilio` changes no caller.

- [ ] **Step 4: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_schemas.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_fiscal.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_matches_api.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/fiscal.py \
  projects/hub/apps/mcp/src/rebase_mcp/server.py \
  projects/hub/packages/core/tests/test_contract_schemas.py \
  projects/hub/packages/core/tests/test_fiscal.py \
  projects/hub/apps/api/tests/test_matches_api.py \
  projects/hub/apps/mcp/tests/test_match_write_tools.py
git commit -F - <<'MSG'
feat(hub): tax data may have no partita IVA

A member paid as a prestazione occasionale has none, so FiscalData takes
it omitted, null or blank and stores NULL, while a value that is given
still has to be eleven digits. set_freelancer_tax_data takes it as
optional too, and the audit keeps naming the field and never its value.

REB-687.
MSG
git log -1 --format=%B
```

### Task 3: The two occasional texts, four texts in the registry, and names per regime

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro-occasionale.md`
- Create: `projects/hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico-occasionale.md`
- Modify: `projects/hub/packages/core/src/rebase_core/contracts/render.py:38` (`DOCUMENTS`), `:173` (`_typst_source`'s `checked` call)
- Modify: `projects/hub/packages/core/src/rebase_core/contracts/fields.py:131-157` (`checked`)
- Modify: `projects/hub/packages/core/src/rebase_core/regimes.py` (add `document_stem`)
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py:87-107` (the occasional letter's auto fields)
- Modify: `projects/hub/packages/core/src/rebase_core/mail.py:473-597` (the four contract mail helpers)
- Modify: `projects/hub/packages/core/src/rebase_core/matches.py:524-535` (`document_pdf`)
- Modify: `projects/hub/packages/core/src/rebase_core/signing.py:162-166` (`_filename`), `:350` (the Documenso title), `:425-427`, `:778-786`, `:1083` (the mails)
- Modify: `projects/hub/packages/core/src/rebase_core/cli.py:140-146` (`contracts_check` docstring)
- Modify: `projects/hub/content/contratti/incarico.esempio.json` (add `professionista-cf`)
- Modify: `projects/hub/packages/core/tests/fakes_contracts.py` (blanks of the two new texts, the receipt term)
- Test: `projects/hub/packages/core/tests/test_contract_pdf.py`, `test_contract_schemas.py`, `test_contract_render.py`, `test_regimes.py`, `test_mail.py`, `test_matches.py`

**Interfaces:**
- Consumes: Task 1's `rebase_core.regimes` (`TEXT_NAMES`, `text_name`, `PARTITA_IVA`, `OCCASIONALE`) and `ContractDocument.regime`.
- Produces:
  - `rebase_core.contracts.render.DOCUMENTS` lists the four texts; `RECEIPT_TEXTS: frozenset[str] = frozenset({"lettera-di-incarico-occasionale"})`.
  - `rebase_core.contracts.fields.checked(data: dict[str, Value], *, receipt: bool = False) -> dict[str, Value]`: `termine-pagamento` reads «N giorni data ricevuta [fine mese]» with `receipt`.
  - `rebase_core.regimes.document_stem(kind: str, regime: str, numero: str | None, version: str) -> str`: `contratto-quadro-occasionale-v1.0`, `lettera-di-incarico-occasionale-2026-001`.
  - `rebase_core.contract_schemas.LETTER_AUTO_FIELDS_OCCASIONALE: frozenset[str]` and `LETTER_AUTO_FIELDS_BY_REGIME: dict[str, frozenset[str]]` (`LETTER_AUTO_FIELDS` keeps meaning the ordinary letter's).
  - `rebase_core.mail.document_name(kind: str, numero: str | None, regime: str = PARTITA_IVA) -> str`; `signing_request_mail(to, nome, kind, numero, signing_url, *, regime: str = PARTITA_IVA)`; `signing_cancelled_mail(to, nome, kind, numero, *, regime: str = PARTITA_IVA)`; `signed_copy_mail(to, *, kind, numero, attachment, nome, cognome, for_rebase=False, regime: str = PARTITA_IVA)`.
  - `MatchService.document_pdf` and the signing chain name a document's PDF and its mails by its own text.

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_contract_pdf.py`, add to the imports:

```python
import re
```

```python
from rebase_core.contracts.render import COMPANY_DEFAULTS, DOCUMENTS, RECEIPT_TEXTS, TEXTS
from rebase_core.regimes import TEXT_NAMES
```

(replacing the existing `from rebase_core.contracts.render import COMPANY_DEFAULTS, DOCUMENTS, TEXTS`), and below `SIGNATURES`:

```python
LETTERS = ("lettera-di-incarico", "lettera-di-incarico-occasionale")
OCCASIONAL_TEXTS = ("contratto-quadro-occasionale", "lettera-di-incarico-occasionale")
```

Replace `test_every_contract_has_the_front_matter_the_page_reads`, `test_the_example_names_every_field_the_letter_asks_for` and `test_the_example_is_fiction` with:

```python
def test_every_contract_has_the_front_matter_the_page_reads() -> None:
    sources = sorted(TEXTS.glob("*.md"))
    assert {path.stem for path in sources} == set(DOCUMENTS)
    for path in sources:
        markdown = path.read_text(encoding="utf-8")
        matter = front_matter(markdown, path.name)
        is_draft(markdown, path.name)
        for key in ("title", "version", "date", "status"):
            assert matter.get(key), f"{path.name} lacks {key}"
    for letter in LETTERS:
        markdown = (TEXTS / f"{letter}.md").read_text(encoding="utf-8")
        assert "{{numero}}" in front_matter(markdown, letter)["subtitle"]


def test_the_texts_are_one_per_kind_and_regime() -> None:
    """REB-687: the four texts the registry names are the four in the package."""
    assert sorted(DOCUMENTS) == sorted(TEXT_NAMES.values())


@pytest.mark.parametrize("letter", LETTERS)
def test_the_example_names_every_field_the_letter_asks_for(letter: str) -> None:
    """A field added to a letter and not to the example is a field nobody knows to fill."""
    asked = set(FIELD.findall((TEXTS / f"{letter}.md").read_text(encoding="utf-8")))
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
    assert set(example["professionista-cf"]) == {"X", "0"}
    assert set(example["cliente-piva"]) == {"0"}
```

and add:

```python
def test_the_payment_term_of_an_occasional_letter_counts_from_the_receipt() -> None:
    """REB-687: a prestazione occasionale is paid on a ricevuta, not an invoice."""
    assert RECEIPT_TEXTS == {"lettera-di-incarico-occasionale"}
    assert checked({"giorni-pagamento": 30, "fine-mese": True}, receipt=True)[
        "termine-pagamento"
    ] == ("30 giorni data ricevuta fine mese")
    assert checked({"giorni-pagamento": 60}, receipt=True)["termine-pagamento"] == (
        "60 giorni data ricevuta"
    )


@pytest.mark.parametrize("document", OCCASIONAL_TEXTS)
def test_an_occasional_text_prints_no_partita_iva_no_vat_excluded_and_no_invoice(
    document: str,
) -> None:
    """Spec § 5: the occasional texts never print the collaborator's partita IVA, never
    price a fee «IVA esclusa» and never speak of a «fattura», outside the field names."""
    markdown = (TEXTS / f"{document}.md").read_text(encoding="utf-8")
    prose = FIELD.sub("", markdown)
    assert "professionista-piva" not in markdown
    assert "IVA esclusa" not in prose
    assert "fattur" not in prose.lower()
    assert "Professionista" not in prose
    matter = front_matter(markdown, document)
    assert (matter["version"], matter["date"], matter["status"]) == (
        "1.0",
        "12 ottobre 2026",
        "final",
    )


def test_the_occasional_quadro_keeps_the_ordinary_articles_and_adds_only_5_7() -> None:
    """Spec § 5.1: a copy of the ordinary text with the listed changes and no others, so
    the letter cites the same article numbers; the one new clause is 5.7, and rebase's
    paragraph loses the codice destinatario."""
    ordinary = (TEXTS / "contratto-quadro.md").read_text(encoding="utf-8")
    occasional = (TEXTS / "contratto-quadro-occasionale.md").read_text(encoding="utf-8")

    def headings(markdown: str) -> list[str]:
        return [line for line in markdown.splitlines() if line.startswith("## ")]

    def clauses(markdown: str) -> list[str]:
        return re.findall(r"^(\d+\.\d+|[A-D]\.|A\.\d) ", markdown, re.MULTILINE)

    assert [line.replace("fattura", "ricevuta") for line in headings(ordinary)] == headings(
        occasional
    )
    ordinary_clauses = clauses(ordinary)
    after = ordinary_clauses.index("5.6") + 1
    assert clauses(occasional) == [*ordinary_clauses[:after], "5.7", *ordinary_clauses[after:]]
    assert "rebase-codice-destinatario" not in occasional
    assert set(FIELD.findall(occasional)) == set(FIELD.findall(ordinary)) - {
        "professionista-piva",
        "rebase-codice-destinatario",
    }
```

In `projects/hub/packages/core/tests/test_contract_schemas.py`, add `LETTER_AUTO_FIELDS_BY_REGIME` to the `rebase_core.contract_schemas` import and `from rebase_core.regimes import text_name`, and replace `test_the_form_covers_every_field_of_the_letter_and_the_hub_fills_the_rest` with:

```python
@pytest.mark.parametrize("regime", ["partita_iva", "occasionale"])
def test_the_form_covers_every_field_of_the_letter_and_the_hub_fills_the_rest(regime: str) -> None:
    """A field added to a letter's Markdown and to neither list is a blank nobody fills.
    The occasional letter asks the same form and names its collaborator by the codice
    fiscale instead of a partita IVA (REB-687)."""
    letter = text_path(text_name("lettera", regime)).read_text(encoding="utf-8")
    asked = set(FIELD.findall(letter))
    written = {name.replace("_", "-") for name in LetteraDraft.model_fields}
    assert asked == (written - {DAYS, MONTH_END}) | LETTER_AUTO_FIELDS_BY_REGIME[regime] | {TERM}
```

Append to `projects/hub/packages/core/tests/test_regimes.py` (and add `document_stem` to its import):

```python
def test_a_documents_file_name_carries_its_own_texts_name() -> None:
    """REB-687: the two families never share a file name."""
    assert document_stem("quadro", "partita_iva", None, "1.0") == "contratto-quadro-v1.0"
    assert document_stem("quadro", "occasionale", None, "1.0") == (
        "contratto-quadro-occasionale-v1.0"
    )
    assert document_stem("lettera", "partita_iva", "2026-001", "1.1") == (
        "lettera-di-incarico-2026-001"
    )
    assert document_stem("lettera", "occasionale", "2026-002", "1.0") == (
        "lettera-di-incarico-occasionale-2026-002"
    )
```

Append to `projects/hub/packages/core/tests/test_mail.py` (add `document_name` to its `rebase_core.mail` import):

```python
def test_an_occasional_document_is_named_as_its_own_text_in_every_mail() -> None:
    """REB-687: the signing mail, the cancellation, the signed copy and the Documenso
    title (`document_name`) name the occasional documents as such."""
    url = "https://firma.letsrebase.com/sign/abc123"
    assert document_name("quadro", None, "occasionale") == (
        "contratto quadro rebase per prestazioni occasionali"
    )
    assert document_name("lettera", "2026-001", "occasionale") == (
        "lettera di incarico occasionale n. 2026-001"
    )
    assert document_name("lettera", "2026-001") == "lettera di incarico n. 2026-001"
    quadro = signing_request_mail(
        "ada@studio.it", "Ada", "quadro", None, url, regime="occasionale"
    )
    assert quadro.subject == "Da firmare: contratto quadro rebase per prestazioni occasionali"
    assert "ogni prestazione occasionale che fai tramite noi" in quadro.text
    lettera = signing_request_mail(
        "ada@studio.it", "Ada", "lettera", "2026-001", url, regime="occasionale"
    )
    assert lettera.subject == "Da firmare: lettera di incarico occasionale n. 2026-001"
    cancelled = signing_cancelled_mail(
        "ada@studio.it", "Ada", "quadro", None, regime="occasionale"
    )
    assert cancelled.subject == "Contratto quadro rebase per prestazioni occasionali annullato"
    signed = signed_copy_mail(
        "ada@studio.it",
        kind="lettera",
        numero="2026-001",
        attachment=Attachment("lettera.pdf", b"%PDF-1.7 firmato"),
        nome="Ada",
        cognome="Lovelace",
        regime="occasionale",
    )
    assert signed.subject == "Firmata: lettera di incarico occasionale n. 2026-001"
```

Append to `projects/hub/packages/core/tests/test_matches.py`:

```python
def test_an_occasional_document_downloads_under_its_own_texts_name(clean: Session) -> None:
    """REB-687: `contratto-quadro-occasionale-v1.0.pdf`, never a second
    `contratto-quadro-v1.0.pdf`."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    quadro = ContractDocument(
        kind="quadro",
        regime="occasionale",
        freelancer_id=freelancer_id,
        text_version="1.0",
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato="generato",
        created_by=admin_id,
    )
    clean.add(quadro)
    clean.commit()
    assert _service(clean).document_pdf(quadro.id).filename == (
        "contratto-quadro-occasionale-v1.0.pdf"
    )
```

Append to `projects/hub/packages/core/tests/test_contract_render.py`:

```python
def test_the_occasional_framework_marks_two_signatures_and_one_date() -> None:
    names = [blank.name for blank in signature_blanks("contratto-quadro-occasionale", _example())]
    assert names.count("firma professionista") == 2
    assert names.count("data firma") == 1


def test_an_occasional_letter_marks_one_signature_and_prints_its_term_from_the_receipt() -> None:
    """REB-687: the real render writes «data ricevuta», and a filled occasional letter
    leaves blank what the filled ordinary one does, the codice fiscale included in the
    example."""
    data = _example()
    names = [blank.name for blank in signature_blanks("lettera-di-incarico-occasionale", data)]
    assert names.count("firma professionista") == 1
    assert names.count("data firma") == 1
    rendered = render("lettera-di-incarico-occasionale", data)
    assert set(rendered.blank) == LETTER_BLANKS
    assert "30giornidataricevutafinemese" in _words(rendered.pdf)
```

`test_both_contracts_typeset_into_a_pdf_that_carries_its_version` and `test_the_contracts_check_command_typesets_both_texts` already loop over `DOCUMENTS`, so they cover the four texts once `DOCUMENTS` lists them.

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_pdf.py -q`
Expected: FAIL at import, `cannot import name 'RECEIPT_TEXTS' from 'rebase_core.contracts.render'`.
Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_regimes.py -q`
Expected: FAIL at import, `cannot import name 'document_stem'`.

- [ ] **Step 3: Write the occasional framework agreement**

Create `projects/hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro-occasionale.md` with exactly this content: the ordinary `contratto-quadro.md` 1.0 with the changes of spec § 5.1 applied and no others.

```markdown
---
title: Contratto quadro per prestazioni occasionali
subtitle: Le regole di ogni prestazione occasionale che un membro della community svolge tramite rebase
version: "1.0"
date: 12 ottobre 2026
status: final
---

Tra **{{rebase-ragione-sociale}}**, con sede in {{rebase-sede}}, codice fiscale
{{rebase-cf}}, partita IVA {{rebase-piva}}, PEC {{rebase-pec}}, in persona di
{{rebase-rappresentante}} («**rebase**»)

e **{{professionista-nome}}**, codice fiscale {{professionista-cf}}, con domicilio in
{{professionista-domicilio}}, email {{professionista-email}}, PEC
{{professionista-pec}} (il «**Collaboratore**»),

insieme le «**Parti**».

**Segnalato da:** {{segnalato-da}}.

## Premesse

A. rebase gestisce la community di freelance raccolta su letsrebase.com: developer, AI
engineer, CTO e fractional CTO che lavorano in proprio. Quando un'azienda cerca una di
queste figure, rebase le presenta i membri adatti e, se l'azienda lo chiede, assume il
lavoro a proprio nome e ne affida l'esecuzione a un membro.

B. Il Collaboratore svolge le prestazioni regolate da questo Contratto come lavoro
autonomo occasionale, senza abitualità né continuità e fuori da un'attività
professionale esercitata con partita IVA. Ha l'esperienza per svolgere le attività che
accetterà ed è membro della community.

C. Le Parti vogliono fissare una volta sola le regole che valgono per ogni lavoro che il
Collaboratore svolgerà tramite rebase. Ogni lavoro sarà descritto in una lettera di
incarico occasionale, con il cliente, le attività, i tempi e i numeri, e resta una
prestazione a sé.

D. Il compenso di ogni lavoro è concordato con il Collaboratore a partire dalla
tariffa giornaliera che lui stesso chiede.

Tutto ciò premesso, le Parti convengono quanto segue.

## 1. Definizioni

Nel Contratto le parole che seguono, scritte con l'iniziale maiuscola, hanno questo
significato.

- **Contratto**: questo contratto quadro, con l'Allegato A. Ogni Lettera di incarico
  firmata lo integra per il proprio Incarico.
- **Cliente**: l'azienda o l'ente per cui rebase svolge il lavoro affidato al
  Collaboratore.
- **Azienda presentata**: un'azienda o un ente a cui rebase presenta il Collaboratore,
  per email o nell'Area riservata, anche se poi l'Incarico non nasce. Un Cliente è
  sempre anche un'Azienda presentata.
- **Incarico**: un singolo lavoro che il Collaboratore svolge per un Cliente tramite
  rebase.
- **Lettera di incarico**: il documento che descrive un Incarico, la lettera di
  incarico occasionale, e ne fissa le condizioni particolari.
- **Compenso**: quanto rebase paga al Collaboratore per un Incarico, a giornata, a ora
  o a corpo, come indicato nella Lettera di incarico.
- **Area riservata**: la sezione di letsrebase.com/hub a cui il Collaboratore accede
  con il proprio account.
- **Rendiconto**: il riepilogo mensile delle giornate o delle ore lavorate su un
  Incarico.
- **Lavori**: tutto ciò che il Collaboratore realizza per un Incarico: codice,
  documentazione, progetti, analisi, configurazioni e ogni altro materiale.
- **Informazioni riservate**: ciò che il Collaboratore viene a sapere per un Incarico e
  che non è pubblico, comprese le informazioni tecniche e commerciali del Cliente e di
  rebase, i dati personali e le condizioni economiche dell'Incarico.

## 2. Oggetto

2.1 Il Contratto regola tutti gli Incarichi che rebase propone al Collaboratore e che
il Collaboratore accetta. Le condizioni di ciascun Incarico sono nella sua Lettera di
incarico.

2.2 Il Contratto non obbliga rebase a proporre Incarichi, né il Collaboratore ad
accettarli. Il Collaboratore può rifiutare una proposta senza spiegarne il motivo e
senza conseguenze sulla sua partecipazione alla community.

2.3 Non c'è esclusiva, in nessuna delle due direzioni. Il Collaboratore può lavorare
per chiunque, anche tramite altre piattaforme o altri intermediari, e rebase può
proporre ai propri Clienti qualunque membro della community. Un obbligo di esclusiva
verso un singolo Cliente vale solo se la Lettera di incarico lo prevede espressamente.

2.4 Se una Lettera di incarico dice qualcosa di diverso dal Contratto, vale la Lettera
di incarico, solo per quell'Incarico e solo sul punto che deroga espressamente.

## 3. Come nasce un Incarico

3.1 rebase propone l'Incarico al Collaboratore per iscritto, per email o nell'Area
riservata, indicando almeno il Cliente, le attività, i tempi previsti e il Compenso.

3.2 L'Incarico nasce quando entrambe le Parti hanno firmato la Lettera di incarico.
Prima di quel momento nessuna delle due è obbligata, anche se il Collaboratore ha già
incontrato il Cliente.

3.3 Quando rebase gli presenta un'azienda, il Collaboratore le dichiara entro sette
giorni se ha lavorato con quell'azienda, in qualunque forma, nei 24 mesi precedenti. Se
l'Incarico nasce, la dichiarazione è riportata nella Lettera di incarico. Serve
all'articolo 12.

3.4 Una modifica a un Incarico in corso (attività, durata, impegno, Compenso)
vale solo se entrambe le Parti la accettano per iscritto, anche per email. Se il
Cliente chiede al Collaboratore attività diverse o ulteriori, il Collaboratore le
segnala a rebase prima di svolgerle, perché vanno concordate con il Cliente e pagate.

## 4. Autonomia

4.1 Il Collaboratore svolge ogni Incarico come lavoratore autonomo occasionale,
secondo gli articoli 2222 e seguenti del codice civile, senza vincolo di subordinazione
e senza coordinamento continuativo con rebase o con il Cliente. Il Contratto e gli
Incarichi non creano tra le Parti, né tra il Collaboratore e il Cliente, un rapporto di
lavoro subordinato né una collaborazione coordinata e continuativa.

4.2 Il Collaboratore organizza autonomamente la propria attività: tempi, luogo e
strumenti. Si coordina con il Cliente solo secondo le modalità stabilite di comune
accordo nella Lettera di incarico, per esempio un incontro di allineamento o giornate in
sede concordate. Né il Cliente né rebase gli impongono orari o esercitano su di lui un
potere direttivo o disciplinare.

4.3 Il Collaboratore lavora con strumenti propri. Se per ragioni di sicurezza il
Cliente fornisce account, dispositivi o accessi, il Collaboratore li usa solo per
l'Incarico e li restituisce alla fine.

4.4 L'Incarico è personale. Il Collaboratore non può farsi sostituire né affidare
parti del lavoro ad altri senza il consenso scritto di rebase. Se il consenso c'è, il
Collaboratore risponde di chi ha coinvolto come di sé stesso.

4.5 Il Collaboratore non rappresenta rebase né il Cliente e non può prendere impegni
in loro nome.

## 5. Compenso

5.1 Il Collaboratore indica a rebase la tariffa giornaliera che chiede, nell'Area
riservata o quando riceve una proposta. Il Compenso di ogni Incarico è concordato con lui
a partire da quella tariffa ed è indicato nella Lettera di incarico.

5.2 Il Compenso non cambia per tutta la durata dell'Incarico, salvo un accordo scritto
secondo l'articolo 3.4.

5.3 Le condizioni economiche che rebase concorda con il Cliente sono un accordo separato
tra rebase e il Cliente, e non modificano il Compenso.

5.4 Il Compenso è a giornata, a ora o a corpo, come dice la Lettera di incarico. Una
giornata vale otto ore; le frazioni di giornata si pagano in proporzione, fino a una
giornata per ciascun giorno di calendario. Il lavoro nei giorni festivi o fuori dalla
fascia 8-20 si svolge solo se il Collaboratore è d'accordo, e si paga come indica la
Lettera di incarico.

5.5 Il Compenso è lordo. La prestazione occasionale è fuori dal campo di applicazione
dell'IVA.

5.6 Le spese (viaggi, trasferte, licenze) sono rimborsate solo se rebase le ha
autorizzate per iscritto prima che siano sostenute, e se sono documentate nella
ricevuta. Il tempo di viaggio non è tempo di lavoro, salvo che la Lettera di incarico
dica altrimenti.

5.7 Il Collaboratore dichiara a rebase, prima di firmare ogni Lettera di incarico, i
compensi da lavoro autonomo occasionale già percepiti nell'anno da chiunque, e la
avvisa subito se, insieme ai Compensi di rebase, superano 5.000 euro. Oltre quella
soglia sulla parte eccedente sono dovuti i contributi alla Gestione separata
dell'INPS, ripartiti come prevede l'articolo 44, comma 2, del decreto-legge 269/2003:
rebase trattiene dal Compenso la quota a carico del Collaboratore e li versa.

## 6. Rendiconto e ricevuta

6.1 Per gli Incarichi a giornata o a ora, entro il terzo giorno lavorativo di ogni mese
il Collaboratore invia a rebase il Rendiconto del mese precedente, nell'Area riservata
o nel modo indicato da rebase, con le giornate o le ore lavorate e una breve
descrizione delle attività.

6.2 rebase sottopone il Rendiconto al Cliente e comunica l'esito al Collaboratore entro
sette giorni lavorativi dal ricevimento. Se in quel termine non arriva una
contestazione motivata, il Rendiconto si considera approvato. Se il Cliente contesta una
parte del Rendiconto, rebase lo dice subito al Collaboratore, con i motivi, e la parte
non contestata si paga comunque.

6.3 Approvato il Rendiconto, il Collaboratore emette a rebase una ricevuta per
prestazione occasionale per il Compenso del mese, con la marca da bollo da 2 euro
quando l'importo supera 77,47 euro. Per gli Incarichi a corpo emette la ricevuta alle
scadenze indicate nella Lettera di incarico, dopo che il Cliente ha accettato i
risultati o, se non arriva una contestazione motivata, dieci giorni lavorativi dopo la
consegna.

6.4 rebase opera la ritenuta d'acconto del 20% quando la legge la obbliga a operarla,
e in quel caso il Collaboratore la indica nella ricevuta. Altrimenti rebase paga il
Compenso lordo per intero e il Collaboratore lo dichiara nella propria dichiarazione
dei redditi. Il Collaboratore avvisa subito rebase se apre una partita IVA.

## 7. Pagamento

7.1 rebase paga ogni ricevuta con bonifico a 30 giorni data ricevuta fine mese, salvo
che la Lettera di incarico indichi un altro termine, e in ogni caso entro 60 giorni dal
ricevimento della ricevuta, come prevede l'articolo 3 della legge 81/2017.

7.2 Il pagamento al Collaboratore non dipende dal pagamento del Cliente: se il Cliente
paga in ritardo o non paga, il rischio è di rebase.

7.3 Sulle somme pagate in ritardo maturano gli interessi di mora previsti dal decreto
legislativo 231/2002.

7.4 rebase può sospendere il pagamento solo della parte di Compenso che il Cliente ha
contestato per iscritto, e solo per il tempo necessario a chiarire la contestazione. Ne
informa il Collaboratore per iscritto, con i motivi. La parte non contestata si paga
alla scadenza.

## 8. Come si lavora

8.1 Il Collaboratore svolge l'Incarico con la diligenza e la competenza che la natura
della prestazione richiede (articolo 1176 del codice civile). Negli Incarichi a
giornata o a ora si impegna a lavorare bene, non a garantire un risultato; negli
Incarichi a corpo risponde dei risultati descritti nella Lettera di incarico.

8.2 Il Collaboratore rispetta le regole del Cliente sulla sicurezza informatica,
sull'accesso ai sistemi e sui luoghi di lavoro che il Cliente o rebase gli comunicano.

8.3 Il Collaboratore può usare strumenti di intelligenza artificiale nel proprio
lavoro, rispettando le regole del Cliente. Non inserisce Informazioni riservate in
servizi che le conservano o le usano per addestrare modelli, salvo che il Cliente lo
abbia autorizzato.

8.4 Il Collaboratore avvisa rebase senza ritardo di tutto ciò che può incidere
sull'Incarico: un ritardo, un'assenza prevista, un problema con il Cliente, una
contestazione, un conflitto di interessi.

8.5 rebase segue l'Incarico dall'inizio alla fine. È il riferimento del Collaboratore
per ogni questione con il Cliente, gli riferisce ciò che il Cliente dice del suo lavoro
e interviene quando serve.

8.6 I ritardi causati dal Cliente, per esempio accessi, dati o decisioni che non
arrivano, non sono imputabili al Collaboratore e spostano le scadenze di un tempo
corrispondente.

## 9. Durata e recesso dal Contratto

9.1 Il Contratto dura 12 mesi dalla firma e si rinnova automaticamente per periodi di 12
mesi, salvo disdetta di una delle Parti comunicata almeno 30 giorni prima della scadenza,
come dice l'articolo 17.2.

9.2 Ciascuna Parte può recedere dal Contratto in qualunque momento con un preavviso di
30 giorni, comunicato come dice l'articolo 17.2.

9.3 Il recesso dal Contratto non chiude gli Incarichi in corso, che continuano fino alla
loro fine con le regole del Contratto, salvo che chi recede receda anche dall'Incarico
secondo l'articolo 10.

## 10. Recesso da un Incarico

10.1 Ciascuna Parte può recedere da un Incarico con il preavviso indicato nella Lettera
di incarico o, se la Lettera non lo indica, con 30 giorni di preavviso.

10.2 Se il Cliente interrompe il progetto, rebase può chiudere l'Incarico prima che
scada il preavviso dell'articolo 10.1, pagando al Collaboratore il Compenso delle
giornate che avrebbe lavorato nel preavviso mancante, secondo l'impegno indicato nella
Lettera di incarico. Ne avvisa il Collaboratore appena lo sa. Ridurre l'impegno è una
modifica dell'Incarico e segue l'articolo 3.4.

10.3 Se la Lettera di incarico prevede un periodo iniziale di verifica, durante quel
periodo ciascuna Parte può chiudere l'Incarico con due giorni lavorativi di
preavviso.

10.4 Quando un Incarico si chiude, per qualunque ragione, il Collaboratore ha diritto
al Compenso per tutto il lavoro svolto fino all'ultimo giorno e, nel caso dell'articolo
10.2, a quello del preavviso mancante; nessuna Parte deve altro all'altra per il solo
fatto della chiusura. Il Collaboratore consegna il lavoro
fatto fino a quel momento e, se rebase lo chiede, dedica al passaggio di consegne fino
a due giornate, pagate.

## 11. Risoluzione

11.1 Ciascuna Parte può dichiarare risolto il Contratto, o il singolo Incarico, con una
comunicazione scritta, secondo l'articolo 1456 del codice civile, se l'altra:

a. viola gli obblighi di correttezza verso la community (articolo 12) o di riservatezza
   (articolo 13);
b. ha reso dichiarazioni false nel Contratto o in una Lettera di incarico;
c. nel caso di rebase, non paga un Compenso dovuto entro 30 giorni da un sollecito
   scritto.

11.2 Per ogni altro inadempimento importante, la Parte che lo subisce può intimare
all'altra di adempiere entro 15 giorni (articolo 1454 del codice civile). Se
l'inadempimento continua, il Contratto o l'Incarico si risolve.

11.3 La risoluzione non toglie il diritto al Compenso per il lavoro già svolto, né il
diritto al risarcimento del danno.

11.4 Se il Collaboratore apre una partita IVA per l'attività che svolge negli
Incarichi, lo dice subito a rebase e le Parti firmano il contratto quadro per chi
lavora con partita IVA. Il lavoro svolto da quel momento negli Incarichi in corso
prosegue con nuove lettere di incarico secondo quel contratto; il lavoro svolto prima
resta regolato da questo Contratto.

## 12. Correttezza verso la community

12.1 Per i 12 mesi successivi alla presentazione di un'Azienda presentata o, se più
tardi, alla fine dell'ultimo Incarico presso di lei, il Collaboratore non lavora per
quell'azienda senza passare da rebase, né direttamente né tramite altri: una propria
società, un'altra piattaforma, un altro intermediario.

12.2 Se il Collaboratore vuole lavorare direttamente con un'Azienda presentata prima
della scadenza, lo dice a rebase e può farlo riconoscendole, fino alla scadenza, il
15% degli importi che riceve da quell'azienda, al netto dell'IVA se dovuta. In
alternativa rebase può liberarlo per iscritto.

12.3 Il limite non vale per un'Azienda presentata con cui il Collaboratore aveva già
lavorato nei 24 mesi precedenti alla presentazione, se lo ha dichiarato come previsto
dall'articolo 3.3.

12.4 Se il Collaboratore lavora con un'Azienda presentata senza passare da rebase e
senza averlo detto, deve a rebase la percentuale dell'articolo 12.2 su tutti gli importi
ricevuti da quell'azienda nel periodo dell'articolo 12.1, più una penale pari a
tre volte il Compenso medio mensile dell'ultimo Incarico presso quell'azienda, e
comunque non inferiore a 5.000 euro, salvo il maggior danno.

12.5 A sua volta rebase, per tutta la durata del Contratto e nei 12 mesi successivi, non
presenta ad altri membri i clienti che il Collaboratore le ha fatto conoscere, senza il
suo consenso.

12.6 Fuori da questo articolo non ci sono altri divieti: il Collaboratore può lavorare
per chiunque, anche in concorrenza con rebase.

## 13. Riservatezza

13.1 Il Collaboratore usa le Informazioni riservate solo per l'Incarico e non le
comunica a nessuno, salvo a chi deve conoscerle per svolgerlo ed è vincolato allo
stesso obbligo.

13.2 Non sono riservate le informazioni che sono o diventano pubbliche senza colpa del
Collaboratore, che il Collaboratore conosceva già o riceve lecitamente da altri, o
che deve comunicare per legge o per ordine di un'autorità, nel qual caso avvisa prima
rebase quando è possibile.

13.3 L'obbligo dura per tutto il Contratto e per i tre anni successivi alla fine
dell'ultimo Incarico. Per i segreti commerciali e i dati personali dura finché restano
tali.

13.4 Alla fine di ogni Incarico il Collaboratore restituisce o cancella le Informazioni
riservate e i dati del Cliente, compresi quelli sui propri dispositivi, e se gli viene
chiesto lo conferma per iscritto. Conserva solo ciò che la legge gli impone di
conservare.

13.5 rebase tratta con la stessa riservatezza le informazioni sul Collaboratore: non
comunica il suo Compenso e le sue condizioni ad altri membri o a terzi, salvo al
Cliente per quanto serve all'Incarico.

## 14. Diritti sui Lavori

14.1 I diritti di utilizzazione economica sui Lavori realizzati per un Incarico passano
a rebase, che li trasferisce al Cliente, dal momento in cui il Collaboratore ha
ricevuto il Compenso per quei Lavori. Fino a quel momento rebase e il Cliente possono
usarli per le finalità dell'Incarico. Realizzare i Lavori è l'oggetto dell'Incarico e
il Compenso remunera anche questo trasferimento, come richiede l'articolo 4 della legge
81/2017.

14.2 Restano del Collaboratore le conoscenze e l'esperienza generali, e gli strumenti,
le librerie, i modelli e il codice che aveva prima dell'Incarico o che sviluppa fuori
da esso («Materiali preesistenti»). Se un Materiale preesistente entra nei Lavori, il
Collaboratore lo segnala prima di usarlo e concede al Cliente una licenza non
esclusiva, gratuita, perpetua e trasferibile a usarlo come parte dei Lavori.

14.3 Il software open source entra nei Lavori solo con licenze compatibili con l'uso
che il Cliente ne farà. Il Collaboratore indica quali componenti usa e con quale
licenza, quando il Cliente lo chiede.

14.4 Il Collaboratore garantisce che i Lavori sono originali e non violano diritti di
terzi, salvo per ciò che il Cliente o rebase gli hanno fornito.

14.5 Il Collaboratore può citare l'Incarico nel proprio curriculum e nel portfolio (il
nome del Cliente, il ruolo, le tecnologie, i risultati in termini generali) senza
rivelare Informazioni riservate, salvo che la Lettera di incarico lo escluda.

14.6 I diritti morali d'autore, che la legge non permette di cedere, restano del
Collaboratore.

## 15. Responsabilità e assicurazione

15.1 Ciascuna Parte risponde verso l'altra dei danni che causa non rispettando il
Contratto.

15.2 Per i danni causati con colpa lieve, la responsabilità del Collaboratore verso
rebase non supera, per ciascun Incarico, il Compenso complessivo previsto dalla Lettera
di incarico o, se l'Incarico dura più di 12 mesi, il Compenso dei suoi primi 12 mesi. Il
limite non vale per il dolo e la colpa grave (articolo 1229 del codice civile), né per
le violazioni degli articoli 12, 13 e 16 e dell'Allegato A.

15.3 Il Collaboratore tiene indenne rebase dalle pretese di terzi che nascono da una
violazione della garanzia dell'articolo 14.4 o dai suoi obblighi fiscali e
previdenziali.

15.4 rebase consiglia al Collaboratore un'assicurazione di responsabilità civile
professionale. Diventa un obbligo, con il massimale indicato, se la Lettera di incarico
lo prevede.

## 16. Dati personali

16.1 rebase tratta i dati personali del Collaboratore come titolare del trattamento,
per gestire il Contratto e gli Incarichi e per gli obblighi di legge, come descritto
nell'informativa su letsrebase.com/privacy.

16.2 Se un Incarico comporta il trattamento di dati personali di cui il Cliente è
titolare, rebase è responsabile del trattamento per conto del Cliente e, con
l'autorizzazione scritta del Cliente, nomina il Collaboratore sub-responsabile secondo
l'articolo 28 del Regolamento (UE) 2016/679, alle condizioni dell'Allegato A. La nomina
avviene con la firma della Lettera di incarico, che indica la finalità del trattamento,
i dati e gli interessati.

## 17. Firma, comunicazioni e modifiche

17.1 Il Contratto e le Lettere di incarico si firmano nell'Area riservata, che registra
il testo firmato, la sua versione, la data, l'ora e l'account di chi firma, e ne
conserva una copia che entrambe le Parti possono scaricare. Le Parti accettano questa
firma elettronica come espressione valida del proprio consenso. Possono anche firmare
su carta o con firma digitale.

17.2 Le comunicazioni ordinarie avvengono per email: per rebase a ciao@letsrebase.com,
per il Collaboratore all'indirizzo del suo account nell'Area riservata. Recesso,
diffida e risoluzione si comunicano per PEC agli indirizzi indicati in apertura o, se
il Collaboratore non ha una PEC, per email con conferma di ricezione.

17.3 rebase può proporre una nuova versione del Contratto pubblicandola nell'Area
riservata almeno 30 giorni prima e indicando cosa cambia. La nuova versione vale per il
Collaboratore solo se la firma. Se non la firma, continua a valere la versione
precedente e ciascuna Parte può recedere secondo l'articolo 9.2. Gli Incarichi in corso
restano regolati dalla versione con cui sono stati firmati.

## 18. Legge applicabile e controversie

18.1 Il Contratto è regolato dalla legge italiana.

18.2 Prima di rivolgersi a un giudice, le Parti cercano un accordo, incontrandosi entro
15 giorni dalla richiesta scritta di una di loro.

18.3 Per ogni controversia è competente in via esclusiva il foro di Milano.

## 19. Disposizioni finali

19.1 Il Contratto, con le Lettere di incarico firmate, è l'intero accordo tra le Parti
sui temi che tratta e sostituisce ogni intesa precedente sugli stessi temi.

19.2 Se una clausola risulta invalida, le altre restano valide, e le Parti la
sostituiscono con una clausola valida che si avvicini il più possibile al suo scopo.

19.3 Il Collaboratore non può cedere il Contratto senza il consenso scritto di rebase.

19.4 Gli articoli 12, 13, 14, 15 e 16 e l'Allegato A continuano a valere anche dopo la
fine del Contratto, per il tempo che ciascuno indica.

## Firme

Luogo e data: {{luogo-firma}}, {{data-firma}}

| Per rebase                           | Il Collaboratore                    |
|--------------------------------------|--------------------------------------|
| {{rebase-rappresentante}}            | {{professionista-nome}}              |
| {{firma-rebase}}                     | {{firma-professionista}}             |

## Approvazione specifica

Secondo gli articoli 1341 e 1342 del codice civile, il Collaboratore approva
specificamente queste clausole:

- 4.4, divieto di farsi sostituire o di affidare il lavoro ad altri;
- 7.4, sospensione del pagamento della parte di Compenso contestata dal Cliente;
- 9.1, rinnovo automatico del Contratto ogni 12 mesi;
- 9.2, recesso di rebase dal Contratto con 30 giorni di preavviso;
- 10.1, 10.2 e 10.3, recesso di rebase da un Incarico, anche prima della fine del
  preavviso e durante il periodo di verifica;
- 10.4, esclusione di ogni altra somma, compreso il mancato guadagno, alla chiusura di un
  Incarico;
- 11.1, risoluzione di diritto per le violazioni elencate;
- 12.1, 12.2 e 12.4, limiti al lavoro diretto con le Aziende presentate, percentuale
  dovuta e penale;
- 15.3, obbligo di tenere indenne rebase;
- 18.3, foro competente esclusivo;
- 19.3, divieto di cedere il Contratto.

Il Collaboratore: {{professionista-nome}}, firma {{firma-professionista}}

## Allegato A. Nomina a sub-responsabile del trattamento

Questo allegato vale per ogni Incarico la cui Lettera di incarico indica che il
Collaboratore tratta dati personali di cui il Cliente è titolare.

A.1 **Oggetto.** Con la firma della Lettera di incarico, rebase, responsabile del
trattamento per conto del Cliente, nomina il Collaboratore sub-responsabile secondo
l'articolo 28, paragrafo 4, del Regolamento (UE) 2016/679 («GDPR»), con
l'autorizzazione scritta del Cliente. La Lettera di incarico indica la natura e la
finalità del trattamento, le categorie di dati e di interessati e l'autorizzazione del
Cliente; il trattamento dura quanto l'Incarico.

A.2 **Istruzioni.** Il Collaboratore tratta i dati solo per svolgere l'Incarico e solo
secondo le istruzioni documentate di rebase e del Cliente, anche per quanto riguarda i
trasferimenti fuori dall'Unione europea, che non fa senza un'autorizzazione scritta. Se
ritiene che un'istruzione violi il GDPR, lo dice subito a rebase.

A.3 **Riservatezza.** Il Collaboratore tiene riservati i dati e, se con il consenso di
rebase coinvolge altre persone, le vincola per iscritto allo stesso obbligo.

A.4 **Sicurezza.** Il Collaboratore adotta misure adeguate al rischio (articolo 32 del
GDPR), e almeno queste: il disco dei dispositivi che usa è cifrato; l'accesso ai
dispositivi e ai servizi è protetto da password robuste e, dove disponibile, da un
secondo fattore; il sistema operativo e i programmi sono aggiornati e protetti dal
malware; i dati del Cliente sono tenuti separati da quelli di altri clienti e, quando è
possibile, restano sui sistemi del Cliente; nessuna copia finisce su servizi personali
o su strumenti non autorizzati dal Cliente, compresi quelli di intelligenza
artificiale.

A.5 **Altri sub-responsabili.** Il Collaboratore non affida il trattamento ad altri
senza l'autorizzazione scritta di rebase e del Cliente. Se è autorizzato, impone per
iscritto a chi coinvolge gli stessi obblighi di questo allegato e risponde verso rebase
del loro rispetto.

A.6 **Assistenza.** Il Collaboratore aiuta rebase e il Cliente a rispondere alle
richieste degli interessati, a garantire la sicurezza del trattamento, a gestire le
violazioni dei dati e, se serve, a svolgere la valutazione d'impatto (articoli da 32 a
36 del GDPR).

A.7 **Violazioni dei dati.** Il Collaboratore comunica a rebase ogni violazione dei
dati personali entro 24 ore da quando ne viene a conoscenza, con le informazioni che
ha, e la aiuta a gestirla.

A.8 **Fine del trattamento.** Alla fine dell'Incarico il Collaboratore cancella o
restituisce i dati, come sceglie il Cliente, e ne cancella le copie, salvo che la legge
ne imponga la conservazione.

A.9 **Verifiche.** Il Collaboratore mette a disposizione di rebase e del Cliente le
informazioni che servono a dimostrare il rispetto di questo allegato e consente
verifiche ragionevoli, con un preavviso adeguato.
```

- [ ] **Step 4: Write the occasional letter**

Create `projects/hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico-occasionale.md` with exactly this content, spec § 5.2:

```markdown
---
title: Lettera di incarico occasionale
subtitle: "n. {{numero}}, secondo il Contratto quadro per prestazioni occasionali firmato il {{data-contratto-quadro}}"
version: "1.0"
date: 12 ottobre 2026
status: final
---

Tra **{{rebase-ragione-sociale}}** («**rebase**») e **{{professionista-nome}}**, codice
fiscale {{professionista-cf}} (il «**Collaboratore**»), che hanno firmato il Contratto
quadro per prestazioni occasionali il {{data-contratto-quadro}}, si conviene la
prestazione occasionale descritta qui sotto (l'«**Incarico**»). Le parole con l'iniziale
maiuscola hanno il significato che dà loro il Contratto quadro, che vale per tutto ciò
che questa Lettera non dice.

## 1. Cliente

{{cliente-ragione-sociale}}, partita IVA {{cliente-piva}}, con sede in
{{cliente-sede}}.

## 2. Attività

**Ruolo:** {{ruolo}}.

**Cosa fa il Collaboratore:** {{attivita}}

**Risultati da consegnare** (solo per un Incarico a corpo): {{risultati}}

**Come il Cliente accetta i risultati** (solo per un Incarico a corpo): {{accettazione}}

## 3. Tempi e impegno

- **Inizio:** {{data-inizio}}.
- **Fine prevista:** {{data-fine}}.
- **Impegno:** {{impegno}}.
- **Periodo iniziale di verifica:** {{periodo-verifica}}.

Se l'Incarico è iniziato prima della firma, questa Lettera regola anche l'attività già
svolta dalla data di inizio.

## 4. Modalità di lavoro

- **Luogo:** {{luogo}}.
- **Incontri con il Cliente:** {{coordinamento}}.
- **Referente del Cliente:** {{referente-cliente}}.
- **Referente di rebase:** {{referente-rebase}}.

Il Collaboratore organizza autonomamente la propria attività, senza vincoli di orario e
con mezzi propri (articolo 4 del Contratto quadro). Gli incontri indicati qui sopra
servono solo a consegnare e a verificare il lavoro.

## 5. Condizioni economiche

| Voce                          | Condizione                                                       |
|-------------------------------|------------------------------------------------------------------|
| Modalità                      | {{modalita}}                                                     |
| Compenso lordo, senza IVA     | {{compenso}} {{unita}}                                           |
| Lavoro festivo o fuori fascia | {{lavoro-extra}}                                                 |
| Spese                         | {{spese}}                                                        |
| Pagamento                     | {{termine-pagamento}}                                            |
| Ricevute, solo a corpo        | {{scadenze-fatturazione}}                                        |

Il Compenso è lordo e la prestazione è fuori dal campo dell'IVA. È concordato a partire
dalla tariffa giornaliera che il Collaboratore ha chiesto (articolo 5 del Contratto
quadro). Il Collaboratore invia il Rendiconto ed emette la ricevuta come dice
l'articolo 6 del Contratto quadro; ritenuta d'acconto e contributi seguono gli articoli
6.4 e 5.7.

**Consuntivazione.** Il Collaboratore registra le ore lavorate per l'incarico in
PigroCRM, nel progetto che rebase predispone nel suo spazio all'avvio dell'incarico, con
cadenza almeno settimanale e comunque prima di ogni ricevuta. rebase legge le ore di
quel progetto per rendicontare l'incarico al Cliente e per riscontrare le ricevute del
Collaboratore, e non accede ad altro dello spazio.

## 6. Preavviso

Ciascuna Parte può recedere dall'Incarico con {{giorni-preavviso}} giorni di preavviso
(articolo 10 del Contratto quadro).

## 7. Dati personali

- **Il Collaboratore tratta dati personali di cui il Cliente è titolare:**
  {{dati-personali}}.
- **Natura e finalità del trattamento:** {{dati-finalita}}.
- **Categorie di dati:** {{dati-categorie}}.
- **Categorie di interessati:** {{dati-interessati}}.
- **Autorizzazione scritta del Cliente alla nomina:** {{dati-autorizzazione}}.

Se il Collaboratore tratta questi dati, con questa Lettera rebase lo nomina
sub-responsabile del trattamento per la durata dell'Incarico, alle condizioni
dell'Allegato A del Contratto quadro.

## 8. Condizioni particolari

- **Esclusiva verso il Cliente:** {{esclusiva}}.
- **Citazione nel portfolio:** {{portfolio}}.
- **Assicurazione di responsabilità civile professionale:** {{assicurazione}}.
- **Segnalato da:** {{azienda-segnalata-da}}.
- **Altro:** {{altre-condizioni}}.

## 9. Rapporti precedenti con il Cliente

Il Collaboratore dichiara, secondo l'articolo 3.3 del Contratto quadro:
{{rapporti-precedenti}}.

## Firme

Luogo e data: {{luogo-firma}}, {{data-firma}}

| Per rebase                           | Il Collaboratore                     |
|--------------------------------------|--------------------------------------|
| {{rebase-rappresentante}}            | {{professionista-nome}}              |
| {{firma-rebase}}                     | {{firma-professionista}}             |
```

- [ ] **Step 5: Register the four texts and the receipt term**

In `projects/hub/packages/core/src/rebase_core/contracts/render.py`:

```python
DOCUMENTS = (
    "contratto-quadro",
    "contratto-quadro-occasionale",
    "lettera-di-incarico",
    "lettera-di-incarico-occasionale",
)
# The texts whose payment term counts from the collaborator's receipt rather than an
# invoice (REB-687): a prestazione occasionale is paid on a ricevuta.
RECEIPT_TEXTS = frozenset({"lettera-di-incarico-occasionale"})
```

and in `_typst_source` the fill line becomes:

```python
    typst, blank = fill(written, checked(dict(data), receipt=document in RECEIPT_TEXTS))
```

In `projects/hub/packages/core/src/rebase_core/contracts/fields.py`, `checked` becomes:

```python
def checked(data: dict[str, Value], *, receipt: bool = False) -> dict[str, Value]:
    """The data, with the fee checked and the payment term written from its two parts,
    counted from the invoice's date or, with `receipt`, from the receipt's: an occasional
    letter is paid on a ricevuta (REB-687)."""
    fee = amount(data, FEE)
    if fee is not None and fee <= 0:
        raise ContractFailed(f"{FEE} is {fee}: a fee above zero")
    if data.get(TERM) is not None:
        raise ContractFailed(
            f"{TERM} is written from {DAYS} and {MONTH_END}; give those two instead"
        )
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
    paid_on = "ricevuta" if receipt else "fattura"
    term = f"{days} giorni data {paid_on}" + (" fine mese" if month_end else "")
    return {**data, TERM: term}
```

Append to `projects/hub/packages/core/src/rebase_core/regimes.py`:

```python
def document_stem(kind: str, regime: str, numero: str | None, version: str) -> str:
    """A document's file name without `.pdf`: its own text's name, then the letter's
    number or the framework agreement's text version, so the two families never share a
    name (`contratto-quadro-occasionale-v1.0`, `lettera-di-incarico-occasionale-2026-001`)."""
    text = text_name(kind, regime)
    return f"{text}-{numero}" if kind == "lettera" else f"{text}-v{version}"
```

In `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, import the two regimes:

```python
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA
```

and after `LETTER_AUTO_FIELDS`:

```python
# The occasional letter (REB-687) asks the same form and names its collaborator by the
# codice fiscale, never by a partita IVA: the same fields the hub fills, with
# `professionista-cf` in place of `professionista-piva`.
LETTER_AUTO_FIELDS_OCCASIONALE = (LETTER_AUTO_FIELDS - {"professionista-piva"}) | {
    "professionista-cf"
}
LETTER_AUTO_FIELDS_BY_REGIME: dict[str, frozenset[str]] = {
    PARTITA_IVA: LETTER_AUTO_FIELDS,
    OCCASIONALE: LETTER_AUTO_FIELDS_OCCASIONALE,
}
```

In `projects/hub/content/contratti/incarico.esempio.json`, after the `professionista-piva` line:

```json
  "professionista-cf": "XXXXXX00X00X000X",
```

In `projects/hub/packages/core/tests/fakes_contracts.py`, import `RECEIPT_TEXTS` beside the other `render` names, give the two new texts the blanks of their ordinary twins, and run the same `checked` the real renderer runs:

```python
from rebase_core.contracts.render import (
    RECEIPT_TEXTS,
    Rendered,
    SignatureBlank,
    text_path,
    text_version,
)

_LETTER_BLANKS = [
    SignatureBlank("data firma", 2, 165.811, 647.241, 85.039, 6.660),
    SignatureBlank("firma professionista", 2, 303.638, 707.281, 155.906, 22.660),
]
_QUADRO_BLANKS = [
    SignatureBlank("data firma", 5, 165.811, 520.0, 85.039, 6.660),
    SignatureBlank("firma professionista", 5, 303.638, 580.0, 155.906, 22.660),
    SignatureBlank("firma professionista", 6, 303.638, 300.0, 155.906, 22.660),
]
# Where the phase 1 probe measured the letter's two blanks (probe § 9), and the same shape
# for the framework agreement's three: what `signature_blanks` answers without Typst. An
# occasional text has its ordinary twin's blanks (REB-687).
FAKE_BLANKS: dict[str, list[SignatureBlank]] = {
    "lettera-di-incarico": _LETTER_BLANKS,
    "lettera-di-incarico-occasionale": _LETTER_BLANKS,
    "contratto-quadro": _QUADRO_BLANKS,
    "contratto-quadro-occasionale": _QUADRO_BLANKS,
}
```

and in `FakeRenderer.render` and `FakeRenderer.signature_blanks` the `checked` calls become:

```python
        filled = checked(dict(data), receipt=document in RECEIPT_TEXTS)
```

```python
        checked(dict(data), receipt=document in RECEIPT_TEXTS)
```

- [ ] **Step 6: Name the documents by their own text**

In `projects/hub/packages/core/src/rebase_core/mail.py`, import `from rebase_core.regimes import OCCASIONALE, PARTITA_IVA`, and replace `document_name` and `signing_request_mail` with:

```python
def document_name(kind: str, numero: str | None, regime: str = PARTITA_IVA) -> str:
    """How a mail and Documenso name a contract: «contratto quadro rebase», «lettera di
    incarico n. 2026-001», and for a prestazione occasionale (REB-687) «contratto quadro
    rebase per prestazioni occasionali», «lettera di incarico occasionale n. 2026-001».
    `kind` is `quadro` or `lettera`."""
    occasional = regime == OCCASIONALE
    if kind == "quadro":
        if occasional:
            return "contratto quadro rebase per prestazioni occasionali"
        return "contratto quadro rebase"
    return f"lettera di incarico{' occasionale' if occasional else ''} n. {numero}"


def signing_request_mail(
    to: str,
    nome: str,
    kind: str,
    numero: str | None,
    signing_url: str,
    *,
    regime: str = PARTITA_IVA,
) -> Mail:
    """One mail per document to sign (spec § 6): the subject names it, one paragraph says
    what it is, one button opens the signing site. The link is the only way the
    document reaches the person, and it goes into an attribute and into text: escaped
    both times, like the magic link. An occasional document says so (REB-687)."""
    e = html_escape.escape
    name = document_name(kind, numero, regime)
    if kind != "quadro":
        what = f"la {name}: il lavoro, le date e il compenso che abbiamo concordato"
    elif regime == OCCASIONALE:
        what = (
            "il contratto quadro per prestazioni occasionali con rebase: le regole di ogni "
            "prestazione occasionale che fai tramite noi. Si firma una volta e si rinnova "
            "da solo ogni dodici mesi"
        )
    else:
        what = (
            "il contratto quadro con rebase: le regole di ogni lavoro che fai tramite noi. "
            "Si firma una volta e si rinnova da solo ogni dodici mesi"
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
```

In `signing_cancelled_mail`, the signature and its first name line become:

```python
def signing_cancelled_mail(
    to: str, nome: str, kind: str, numero: str | None, *, regime: str = PARTITA_IVA
) -> Mail:
```

```python
    name = document_name(kind, numero, regime)
```

In `signed_copy_mail`, add the keyword after `for_rebase`:

```python
    for_rebase: bool = False,
    regime: str = PARTITA_IVA,
) -> Mail:
```

and its name line becomes:

```python
    name = document_name(kind, numero, regime)
```

In `projects/hub/packages/core/src/rebase_core/matches.py`, import `from rebase_core.regimes import document_stem` and the end of `document_pdf` becomes:

```python
        base = document_stem(
            document.kind, document.regime, document.numero, document.text_version
        )
        return ContractPdf(filename=f"{base}{'-firmato' if signed else ''}.pdf", content=content)
```

In `projects/hub/packages/core/src/rebase_core/signing.py`, import `from rebase_core.regimes import document_stem` and:

```python
def _filename(document: ContractDocument, version: str) -> str:
    """The name Documenso keeps and seals as `<name>_signed.pdf` (probe § 4): the
    document's own text, so the two families never share one (REB-687)."""
    return f"{document_stem(document.kind, document.regime, document.numero, version)}.pdf"
```

in `_dispatch`:

```python
        title = document_name(document.kind, document.numero, document.regime)
```

in `_mail_signing_request`:

```python
        mail = signing_request_mail(
            user.email,
            user.nome,
            document.kind,
            document.numero,
            document.signing_url,
            regime=document.regime,
        )
```

in `_mail_signed_copy_to`, the `signed_copy_mail(...)` call gains `regime=document.regime,` after `for_rebase=not to_freelancer,`; and in `_mail_cancellation`:

```python
        mail = signing_cancelled_mail(
            user.email, user.nome, document.kind, document.numero, regime=document.regime
        )
```

In `projects/hub/packages/core/src/rebase_core/cli.py`, the first sentence of `contracts_check`'s docstring body becomes «Renders every text, the ordinary pair and the occasional pair (REB-687), from fiction with this machine's pandoc, Typst, palette and typeface, and asks Typst where the signing blanks landed.» In `projects/hub/packages/core/tests/test_contract_render.py`, the module docstring's first line becomes «The real render, the way the API runs it: pandoc and Typst over every contract.»

- [ ] **Step 7: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_pdf.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_schemas.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_contract_render.py -q` (runs the real pandoc and Typst)
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_regimes.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_mail.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_matches.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_signing.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints, and `uv run rebase contracts-check` from the repository root, which prints four lines, one per text, each with «spazi da firmare».

- [ ] **Step 8: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro-occasionale.md \
  projects/hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico-occasionale.md \
  projects/hub/packages/core/src/rebase_core/contracts/render.py \
  projects/hub/packages/core/src/rebase_core/contracts/fields.py \
  projects/hub/packages/core/src/rebase_core/regimes.py \
  projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/mail.py \
  projects/hub/packages/core/src/rebase_core/matches.py \
  projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/src/rebase_core/cli.py \
  projects/hub/content/contratti/incarico.esempio.json \
  projects/hub/packages/core/tests/fakes_contracts.py \
  projects/hub/packages/core/tests/test_contract_pdf.py \
  projects/hub/packages/core/tests/test_contract_schemas.py \
  projects/hub/packages/core/tests/test_contract_render.py \
  projects/hub/packages/core/tests/test_regimes.py \
  projects/hub/packages/core/tests/test_mail.py \
  projects/hub/packages/core/tests/test_matches.py
git commit -F - <<'MSG'
feat(hub): the occasional framework agreement and letter

I add the two texts for members without a partita IVA, both final at
1.0: the framework agreement is the ordinary one with the Collaboratore,
the ricevuta and the new 5.7, the letter names its collaborator by the
codice fiscale. The renderer and contracts-check now know four texts, an
occasional letter's term counts from the receipt, and a document's PDF,
its Documenso title and its mails carry its own text's name.

REB-687.
MSG
git log -1 --format=%B
```

### Task 4: Step 3's words for an occasional letter

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/match_words.py:75-86` (`WHAT_LEAVES_FIRST` and the new words) and `:326-358` (`check_sentences`)
- Test: `projects/hub/packages/core/tests/test_match_words.py`

**Interfaces:**
- Consumes: Task 1's `rebase_core.regimes.OCCASIONALE`, `PARTITA_IVA`.
- Produces, in `rebase_core.match_words`:
  - `VAT_EXCLUDED = "IVA esclusa"`, `GROSS_WITHOUT_VAT = "lordi, senza IVA"`
  - `HAS_PARTITA_IVA: str` (the warning) and `ORDINARY_NEEDS_PARTITA_IVA: str` (the refusal), verbatim from the spec
  - `WHAT_LEAVES_FIRST_OCCASIONALE: dict[FrameworkStep, str]` beside the unchanged `WHAT_LEAVES_FIRST`
  - `check_sentences(nome: str, cliente: str, lettera: LetteraFields, quadro: FrameworkStep, *, dati_fiscali_mancanti: bool, regime: str = PARTITA_IVA, ha_partita_iva: bool = False) -> tuple[list[str], str]`

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_match_words.py`, add to the `rebase_core.match_words` import `HAS_PARTITA_IVA`, `ORDINARY_NEEDS_PARTITA_IVA` and `WHAT_LEAVES_FIRST_OCCASIONALE`, and append after `test_the_check_prints_the_period_once_even_if_the_commitment_ends_with_one`:

```python
def test_the_check_of_an_occasional_lump_sum_says_gross_and_without_vat() -> None:
    """REB-687, spec § 4: the fee of an occasional letter is gross and outside VAT."""
    riepilogo, cosa_succede = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(compenso=Decimal("5000"), modalita="a corpo", unita=None),
        "da_inviare",
        dati_fiscali_mancanti=False,
        regime="occasionale",
    )
    assert riepilogo == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
        "Compenso: 5.000,00 € a corpo, lordi, senza IVA, pagato a 30 giorni fine mese.",
    ]
    assert cosa_succede == WHAT_LEAVES_FIRST_OCCASIONALE["da_inviare"]


@pytest.mark.parametrize(
    ("step", "sentence"),
    [
        (
            "da_inviare",
            "Prima parte il contratto quadro per prestazioni occasionali; la lettera di "
            "incarico occasionale parte da sola dopo la sua firma.",
        ),
        (
            "in_firma",
            "Il contratto quadro per prestazioni occasionali è già in firma: la lettera di "
            "incarico occasionale parte da sola dopo la sua firma.",
        ),
        (
            "attivo",
            "Il contratto quadro per prestazioni occasionali è già attivo: parte subito la "
            "lettera di incarico occasionale.",
        ),
    ],
)
def test_an_occasional_check_names_the_occasional_documents(step: str, sentence: str) -> None:
    _, cosa_succede = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(),
        step,  # type: ignore[arg-type]
        dati_fiscali_mancanti=False,
        regime="occasionale",
    )
    assert cosa_succede == sentence


def test_an_occasional_letter_for_someone_with_a_partita_iva_carries_the_warning() -> None:
    """Spec § 3: allowed, since occasional work outside one's professional activity
    exists, and said; the ordinary letter never carries the warning."""
    riepilogo, _ = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(),
        "attivo",
        dati_fiscali_mancanti=False,
        regime="occasionale",
        ha_partita_iva=True,
    )
    assert riepilogo[-1] == HAS_PARTITA_IVA == (
        "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla sua "
        "attività professionale."
    )
    ordinary, _ = check_sentences(
        "Ada Lovelace", "ACME S.r.l.", _letter(), "attivo", dati_fiscali_mancanti=False,
        ha_partita_iva=True,
    )
    assert HAS_PARTITA_IVA not in ordinary
    assert ordinary[-1] == (
        "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese."
    )


def test_the_refusal_of_an_ordinary_letter_without_a_partita_iva_is_the_specs_sentence() -> None:
    assert ORDINARY_NEEDS_PARTITA_IVA == (
        'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
        "o salva la partita IVA."
    )
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_match_words.py -q`
Expected: FAIL at import, `cannot import name 'HAS_PARTITA_IVA' from 'rebase_core.match_words'`.

- [ ] **Step 3: Write the words**

In `projects/hub/packages/core/src/rebase_core/match_words.py`, import the regimes:

```python
from rebase_core.contracts.fields import FEE, italian_date, rendered
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA
```

After `WHAT_LEAVES_FIRST` add:

```python
# The same three sentences for an occasional letter (REB-687), naming its own documents.
WHAT_LEAVES_FIRST_OCCASIONALE: dict[FrameworkStep, str] = {
    "da_inviare": (
        "Prima parte il contratto quadro per prestazioni occasionali; la lettera di incarico "
        "occasionale parte da sola dopo la sua firma."
    ),
    "in_firma": (
        "Il contratto quadro per prestazioni occasionali è già in firma: la lettera di "
        "incarico occasionale parte da sola dopo la sua firma."
    ),
    "attivo": (
        "Il contratto quadro per prestazioni occasionali è già attivo: parte subito la "
        "lettera di incarico occasionale."
    ),
}
# How step 3 prices a fee (REB-687): an ordinary letter's is net of VAT, an occasional
# one's gross and outside VAT altogether.
VAT_EXCLUDED = "IVA esclusa"
GROSS_WITHOUT_VAT = "lordi, senza IVA"
# An occasional letter for someone whose saved tax data have a partita IVA is allowed,
# since occasional work outside their professional activity exists; step 3 says so.
HAS_PARTITA_IVA = (
    "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla sua "
    "attività professionale."
)
# An ordinary letter for someone whose saved tax data have no partita IVA is refused, on
# `regime`, by `MatchService.check`, `preview` and `create`.
ORDINARY_NEEDS_PARTITA_IVA = (
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
    "o salva la partita IVA."
)
```

Replace `check_sentences` with:

```python
def check_sentences(
    nome: str,
    cliente: str,
    lettera: "LetteraFields",
    quadro: FrameworkStep,
    *,
    dati_fiscali_mancanti: bool,
    regime: str = PARTITA_IVA,
    ha_partita_iva: bool = False,
) -> tuple[list[str], str]:
    """`(riepilogo, cosa_succede)`: step 3 of «Crea match», what the letter says in three
    sentences and which document leaves first. An occasional letter (REB-687) prices its
    fee gross and without VAT, names the occasional documents, and adds a warning when
    the saved tax data have a partita IVA (`ha_partita_iva`)."""
    occasional = regime == OCCASIONALE
    where = f", {_flat(lettera.luogo)}" if lettera.luogo else ""
    until = f" al {italian_date(lettera.data_fine)}" if lettera.data_fine is not None else ""
    riepilogo = [
        f"{nome} lavorerà per {cliente} come {_flat(lettera.ruolo)}{where}, dal "
        f"{italian_date(lettera.data_inizio)}{until}."
    ]
    if lettera.impegno:
        riepilogo.append(f"Impegno: {_flat(lettera.impegno).rstrip('.')}.")
    unit = lettera.unita or lettera.modalita
    per = f" {_flat(unit)}" if unit else ""
    month_end = " fine mese" if lettera.fine_mese else ""
    # The fee exactly as the letter prints it, «450,00 €», so step 3 and the PDF agree.
    fee = rendered(FEE, lettera.to_fields()[FEE])
    vat = GROSS_WITHOUT_VAT if occasional else VAT_EXCLUDED
    riepilogo.append(
        f"Compenso: {fee}{per}, {vat}, pagato a {lettera.giorni_pagamento} giorni{month_end}."
    )
    if occasional and ha_partita_iva:
        riepilogo.append(HAS_PARTITA_IVA)
    if dati_fiscali_mancanti:
        riepilogo.append(MISSING_TAX_DATA)
    leaves = WHAT_LEAVES_FIRST_OCCASIONALE if occasional else WHAT_LEAVES_FIRST
    return riepilogo, leaves[quadro]
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_match_words.py -q`
Expected: PASS, the existing «IVA esclusa» tests included (the default regime is the ordinary one). Then ruff and mypy as in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/match_words.py \
  projects/hub/packages/core/tests/test_match_words.py
git commit -F - <<'MSG'
feat(hub): step 3 speaks of a prestazione occasionale

An occasional letter's fee reads gross and without VAT, its three
sentences on what leaves first name the occasional documents, and a
partita IVA holder's occasional letter carries the spec's warning. The
refusal of an ordinary letter without a partita IVA lives here too,
beside the other words.

REB-687.
MSG
git log -1 --format=%B
```

### Task 5: Every framework lookup and every document write takes a regime

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/framework.py:81-160` (`active_framework`, `pending_framework`, `framework_state`, `framework_states`; new `current_quadro_version`) and `:197-228` (`document_read`)
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py:300-330` (`ContractDocumentRead`)
- Modify: `projects/hub/packages/core/src/rebase_core/matches.py` (imports; `DOCUMENT_BY_KIND` removed; `get`, `list_all`, `for_freelancer`, `prefill`, `preview`, `check`, `create`, `_quadro_data`, `_letter_party_fields`, `_lettera_data`, `_document`, `data_for_sending`, `write_framework`, `_match_read`, `_framework_step`)
- Modify: `projects/hub/packages/core/src/rebase_core/signing.py` (imports; `send_match`, `_framework_to_send`, `_dispatch`, `_send_waiting`, `_read`)
- Test: `projects/hub/packages/core/tests/test_framework.py`, `test_matches.py`, `test_signing.py` (the race test's patched `active_framework` takes the regime)

**Interfaces:**
- Consumes: Task 1's `rebase_core.regimes` (`PARTITA_IVA`, `as_regime`, `document_label`, `text_name`), Task 3's `document_stem`.
- Produces, in `rebase_core.framework`:
  - `active_framework(session: Session, freelancer_id: UUID, regime: str) -> ContractDocument | None`
  - `pending_framework(session: Session, freelancer_id: UUID, regime: str) -> ContractDocument | None`
  - `framework_state(frameworks: Iterable[ContractDocument], regime: str) -> str | None`
  - `framework_states(session: Session, freelancer_ids: Iterable[UUID]) -> dict[tuple[UUID, str], str]`, keyed by (freelancer, regime)
  - `current_quadro_version(regime: str) -> str`
  - `document_read(...)` fills `ContractDocumentRead.regime: Regime` and `ContractDocumentRead.tipo: str`
- Produces, on `MatchService`: `write_framework(freelancer_id: UUID, admin_id: UUID, regime: str) -> ContractDocument`; `_document(renderer, kind, regime, data, freelancer_id, match_id, numero, stato, admin_id)`; `_quadro_data(user, fiscal, today, regime)`; `_letter_party_fields(user, fiscal, signed, regime)` (the ordinary letter gets `professionista-piva`, the occasional one `professionista-cf`); `_framework_step(freelancer_id, regime)`. `rebase_core.matches.DOCUMENT_BY_KIND` is gone: use `rebase_core.regimes.text_name(kind, regime)`.
- Produces, on `SigningService`: `_framework_to_send(freelancer_id: UUID, admin_id: UUID, regime: str)`.
- A new match is still written as `partita_iva` here: `create`, `check`, `prefill` and `preview` pass `PARTITA_IVA` until Task 6 gives `MatchCreate` its `regime`.

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_framework.py`, add `current_quadro_version` to the `rebase_core.framework` import and `from rebase_core.contracts.render import text_version`; in `test_the_read_model_computes_the_dates_only_for_an_active_framework` add `"regime": "partita_iva",` to `common`; and append:

```python
DOCUMENT = {
    "id": UUID("01a00000-0000-7000-8000-00000000001a"),
    "freelancer_id": UUID("01a00000-0000-7000-8000-00000000001b"),
    "created_by": UUID("01a00000-0000-7000-8000-00000000001c"),
    "created_at": datetime(2026, 10, 12, tzinfo=UTC),
    "testo_bozza": False,
    "sent_at": None,
    "signed_at": None,
    "notice_at": None,
    "signed_pdf": None,
    "text_version": "1.0",
    "stato": "generato",
}


def test_the_read_model_names_the_documents_regime_and_type() -> None:
    """REB-687: every document read says which family it belongs to, in a word for the
    code and a type for the pages."""
    quadro = ContractDocument(
        kind="quadro", regime="occasionale", match_id=None, numero=None, **DOCUMENT
    )
    letter = ContractDocument(
        kind="lettera",
        regime="partita_iva",
        match_id=UUID("01a00000-0000-7000-8000-00000000001d"),
        numero="2026-001",
        **DOCUMENT,
    )
    read = document_read(quadro, date(2026, 10, 12), current_version="1.0")
    assert (read.regime, read.tipo) == ("occasionale", "Contratto quadro per prestazioni occasionali")
    read = document_read(letter, date(2026, 10, 12), current_version="1.0")
    assert (read.regime, read.tipo) == ("partita_iva", "Lettera di incarico")


def test_each_quadro_compares_its_version_with_its_own_regimes_text() -> None:
    assert current_quadro_version("partita_iva") == text_version("contratto-quadro")
    assert current_quadro_version("occasionale") == text_version("contratto-quadro-occasionale")
```

In `projects/hub/packages/core/tests/test_matches.py`, add to the imports:

```python
from rebase_core.framework import (
    active_framework,
    framework_state,
    framework_states,
    pending_framework,
)
```

give `_framework` a regime (the keyword goes last, after `text_version`, and the row gets it):

```python
    text_version: str = QUADRO_VERSION,
    regime: str = "partita_iva",
) -> ContractDocument:
    """A framework agreement as phase 3 will leave one: only a signature makes these."""
    document = ContractDocument(
        kind="quadro",
        regime=regime,
        freelancer_id=freelancer_id,
```

and append:

```python
def test_framework_lookups_and_states_are_per_regime(clean: Session) -> None:
    """REB-687: a freelancer may hold a framework agreement of each regime, and no lookup
    reads one for the other."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    ordinary = _framework(clean, freelancer_id, admin_id)
    occasional = _framework(
        clean, freelancer_id, admin_id, stato="inviato", signed_at=None, regime="occasionale"
    )
    active = active_framework(clean, freelancer_id, "partita_iva")
    assert active is not None and active.id == ordinary.id
    assert active_framework(clean, freelancer_id, "occasionale") is None
    pending = pending_framework(clean, freelancer_id, "occasionale")
    assert pending is not None and pending.id == occasional.id
    assert pending_framework(clean, freelancer_id, "partita_iva") is None
    assert framework_states(clean, {freelancer_id}) == {
        (freelancer_id, "partita_iva"): "firmato",
        (freelancer_id, "occasionale"): "inviato",
    }
    both = _documents(clean, freelancer_id, "quadro")
    assert (framework_state(both, "partita_iva"), framework_state(both, "occasionale")) == (
        "firmato",
        "inviato",
    )


def test_a_new_version_is_announced_against_the_quadros_own_text(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once one regime's text moves to a new version, only that regime's
    framework agreement says «Nuova versione disponibile»."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, text_version="1.0")
    _framework(clean, freelancer_id, admin_id, text_version="1.0", regime="occasionale")
    versions = {"partita_iva": "1.1", "occasionale": "1.0"}
    monkeypatch.setattr(matches_module, "current_quadro_version", versions.__getitem__)
    quadri = _service(clean).for_freelancer(freelancer_id).quadri
    assert {(quadro.regime, quadro.nuova_versione) for quadro in quadri} == {
        ("partita_iva", True),
        ("occasionale", False),
    }


def test_the_documents_of_an_ordinary_match_say_their_regime_and_type(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    assert (match.lettera.regime, match.lettera.tipo) == ("partita_iva", "Lettera di incarico")
    assert {document.regime for document in _documents(clean, freelancer_id)} == {"partita_iva"}
    page = service.for_freelancer(freelancer_id)
    assert page.quadro is not None
    assert (page.quadro.regime, page.quadro.tipo) == ("partita_iva", "Contratto quadro")
```

In `projects/hub/packages/core/tests/test_signing.py`, in `test_a_crea_match_racing_a_send_waits_for_it_and_does_not_annul_the_framework_it_dispatched`, the patched lookup takes the regime:

```python
    def paced_active_framework(
        session: Session, freelancer_id: UUID, regime: str
    ) -> ContractDocument | None:
        if session is sender_session:
            paused.set()
            assert release.wait(timeout=5), "the test never released the send"
        return real_active_framework(session, freelancer_id, regime)
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_framework.py -q`
Expected: FAIL at import, `cannot import name 'current_quadro_version'`.
Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_matches.py::test_framework_lookups_and_states_are_per_regime -q`
Expected: FAIL, `active_framework() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Take a regime in `framework.py`**

In `projects/hub/packages/core/src/rebase_core/framework.py`, the imports gain:

```python
from rebase_core.contracts.render import text_version
from rebase_core.regimes import as_regime, document_label, text_name
```

and the lookups become:

```python
def active_framework(
    session: Session, freelancer_id: UUID, regime: str
) -> ContractDocument | None:
    """The freelancer's active framework agreement of `regime` (REB-687): a freelancer
    may hold one of each regime, and neither stands in for the other."""
    return session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.kind == QUADRO,
            ContractDocument.freelancer_id == freelancer_id,
            ContractDocument.regime == regime,
            ContractDocument.stato == "firmato",
            ContractDocument.notice_at.is_(None),
        )
        .order_by(ContractDocument.signed_at.desc(), ContractDocument.created_at.desc())
        .limit(1)
    ).first()


def pending_framework(
    session: Session, freelancer_id: UUID, regime: str
) -> ContractDocument | None:
    """A framework agreement of `regime` written and not signed yet: the one out for
    signature (`inviato`, phase 3) when there is one, else the newest merely generated.
    One of the other regime never counts (REB-687)."""
    pending = list(
        session.scalars(
            select(ContractDocument)
            .where(
                ContractDocument.kind == QUADRO,
                ContractDocument.freelancer_id == freelancer_id,
                ContractDocument.regime == regime,
                ContractDocument.stato.in_(("generato", "inviato")),
            )
            .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
        )
    )
    for document in pending:
        if document.stato == "inviato":
            return document
    return pending[0] if pending else None
```

```python
def framework_state(frameworks: Iterable[ContractDocument], regime: str) -> str | None:
    """Where one freelancer's framework agreement of `regime` stands, from the rows
    already read: `firmato` for an active one (`active_framework`), else
    `pending_framework`'s state, `inviato` or `generato`, else `None`. A match reads the
    one of its letter's regime (REB-687)."""
    return _standing(
        (document.stato, document.notice_at)
        for document in frameworks
        if document.regime == regime
    )


def framework_states(
    session: Session, freelancer_ids: Iterable[UUID]
) -> dict[tuple[UUID, str], str]:
    """`framework_state` for many freelancers in one query, keyed by (freelancer,
    regime), so the «Match» list reads one statement for its page rather than one per
    row. A pair with none that counts is absent."""
    ids = set(freelancer_ids)
    if not ids:
        return {}
    documents: dict[tuple[UUID, str], list[tuple[str, datetime | None]]] = {}
    for freelancer_id, regime, stato, notice_at in session.execute(
        select(
            ContractDocument.freelancer_id,
            ContractDocument.regime,
            ContractDocument.stato,
            ContractDocument.notice_at,
        ).where(
            ContractDocument.kind == QUADRO,
            ContractDocument.freelancer_id.in_(ids),
            ContractDocument.stato.in_(tuple(_FRAMEWORK_RANK)),
        )
    ):
        documents.setdefault((freelancer_id, regime), []).append((stato, notice_at))
    return {
        key: standing
        for key, pairs in documents.items()
        if (standing := _standing(pairs)) is not None
    }


def current_quadro_version(regime: str) -> str:
    """The `version` of `regime`'s framework agreement text as it is in the package
    today: what a quadro's own `text_version` is compared with for `nuova_versione`,
    each against its own text (REB-687)."""
    return text_version(text_name(QUADRO, regime))
```

In `document_read`, after `kind=document.kind,` add:

```python
        regime=as_regime(document.regime),
        tipo=document_label(document.kind, document.regime),
```

In `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, import `Regime` (`from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, Regime`) and in `ContractDocumentRead`, after `kind: str`:

```python
    # Which family it belongs to and its type as the pages name it (REB-687):
    # «Contratto quadro per prestazioni occasionali», «Lettera di incarico occasionale».
    regime: Regime
    tipo: str
```

- [ ] **Step 4: Thread the regime through `matches.py`**

In `projects/hub/packages/core/src/rebase_core/matches.py`:

The render import loses `text_version`, the framework import gains `current_quadro_version`, and the regimes import is:

```python
from rebase_core.contracts.render import Renderer, company_defaults
```

```python
from rebase_core.regimes import PARTITA_IVA, document_stem, text_name
```

Delete the line `DOCUMENT_BY_KIND = {QUADRO: "contratto-quadro", LETTERA: "lettera-di-incarico"}`.

In `get`:

```python
        freelancer_id = row[0].freelancer_id
        framework = framework_states(self.session, {freelancer_id}).get(
            (freelancer_id, letter.regime)
        )
        return self._match_read(row[0], row[1], letter, framework)
```

In `list_all`, the row's framework argument:

```python
                    frameworks.get((match.freelancer_id, letter.regime))
                    if letter is not None
                    else None,
```

In `for_freelancer`, the first lines and the matches become:

```python
        self._freelancer(freelancer_id)
        today = self.today()
        frameworks = list(
            self.session.scalars(
                select(ContractDocument)
                .where(
                    ContractDocument.kind == QUADRO, ContractDocument.freelancer_id == freelancer_id
                )
                .order_by(ContractDocument.created_at.desc(), ContractDocument.id.desc())
            )
        )
        quadri = [
            document_read(document, today, current_quadro_version(document.regime))
            for document in frameworks
        ]
```

(the `shown = ...` block stays as it is; the line `framework = framework_state(frameworks)` goes), and:

```python
            matches=[
                self._match_read(
                    match,
                    company,
                    letters[match.id],
                    framework_state(frameworks, letters[match.id].regime),
                )
                for match, company in rows
                if match.id in letters
            ],
```

In `prefill`:

```python
        active, step = self._framework_step(freelancer.id, PARTITA_IVA)
        today = self.today()
        return MatchPrefill(
            fiscale=FiscalService(self.session).get(freelancer.id),
            cliente=cliente,
            lettera=lettera,
            quadro_attivo=(
                document_read(active, today, current_quadro_version(PARTITA_IVA))
                if active is not None
                else None
            ),
            quadro_necessario=step == "da_inviare",
            lettera_in_attesa=active is None,
        )
```

In `preview`:

```python
        if kind == QUADRO:
            rendered = renderer.render(
                text_name(QUADRO, PARTITA_IVA), self._quadro_data(user, fiscal, today, PARTITA_IVA)
            )
            return ContractPdf(filename="anteprima-contratto-quadro.pdf", content=rendered.pdf)
        if kind == LETTERA:
            active = active_framework(self.session, freelancer.id, PARTITA_IVA)
            data_fields = self._lettera_data(user, fiscal, data, None, active, today)
            rendered = renderer.render(text_name(LETTERA, PARTITA_IVA), data_fields)
            return ContractPdf(filename="anteprima-lettera-di-incarico.pdf", content=rendered.pdf)
```

In `check`:

```python
        _active, step = self._framework_step(freelancer_id, PARTITA_IVA)
```

In `create`, from `active = ...` to the letter's document:

```python
            active = active_framework(self.session, freelancer.id, PARTITA_IVA)
            documents: list[ContractDocument] = []
            stale_ids: list[UUID] = []
            if active is None:
                pending = pending_framework(self.session, freelancer.id, PARTITA_IVA)
                if pending is None or pending.stato != "inviato":
                    for stale in list(
                        self.session.scalars(
                            select(ContractDocument).where(
                                ContractDocument.kind == QUADRO,
                                ContractDocument.freelancer_id == freelancer.id,
                                ContractDocument.regime == PARTITA_IVA,
                                ContractDocument.stato == "generato",
                            )
                        )
                    ):
                        stale.stato = "annullato"
                        stale_ids.append(stale.id)
                    # REB-406: the same write `write_framework` makes for a match whose
                    # letter waits on a cancelled or refused one -- `create` already
                    # holds the freelancer's row lock `write_framework` assumes.
                    documents.append(self.write_framework(freelancer.id, admin_id, PARTITA_IVA))
```

```python
            numero = next_letter_number(self.session, today.year)
            documents.append(
                self._document(
                    renderer,
                    LETTERA,
                    PARTITA_IVA,
                    self._lettera_data(user, fiscal, data, numero, active, today),
                    freelancer.id,
                    match.id,
                    numero,
                    "generato" if active is not None else "in_attesa",
                    admin_id,
                )
            )
```

The documents' data:

```python
    def _quadro_data(
        self, user: User, fiscal: FreelancerFiscal, today: date, regime: str
    ) -> dict[str, Value]:
        """The framework agreement's fields. The occasional text prints no partita IVA
        (REB-687), so only the ordinary one gets the field."""
        party: dict[str, Value] = {
            "professionista-nome": _full_name(user),
            "professionista-cf": fiscal.codice_fiscale,
            "professionista-domicilio": fiscal.domicilio,
            "professionista-email": user.email,
            "professionista-pec": fiscal.pec or PEC_MISSING,
        }
        if regime == PARTITA_IVA:
            party["professionista-piva"] = fiscal.partita_iva
        return {
            **self._rebase_fields(),
            **party,
            "segnalato-da": ReferralService(self.session).referrer_name(
                "freelancer", fiscal.freelancer_id
            )
            or SEGNALATO_NONE,
            **self._signing_fields(today),
        }

    def _letter_party_fields(
        self, user: User, fiscal: FreelancerFiscal, signed: date | None, regime: str
    ) -> dict[str, Value]:
        """A letter's own fields that must always be today's, not the draft's: the
        framework agreement's signature date, the freelancer's name, and the identifier
        the letter prints, the partita IVA on an ordinary letter and the codice fiscale on
        an occasional one (REB-687). Shared by `_lettera_data` (`create`) and
        `data_for_sending` (a send), so a field added to one cannot print stale data on
        the other (REB-406)."""
        identifier: dict[str, Value] = (
            {"professionista-piva": fiscal.partita_iva}
            if regime == PARTITA_IVA
            else {"professionista-cf": fiscal.codice_fiscale}
        )
        return {
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": _full_name(user),
            **identifier,
        }
```

In `_lettera_data`, the party line:

```python
            **self._letter_party_fields(user, fiscal, signed, PARTITA_IVA),
```

`_document` takes the regime after the kind and stores it:

```python
    def _document(
        self,
        renderer: Renderer,
        kind: str,
        regime: str,
        data: dict[str, Value],
        freelancer_id: UUID,
        match_id: UUID | None,
        numero: str | None,
        stato: str,
        admin_id: UUID,
    ) -> ContractDocument:
        rendered = renderer.render(text_name(kind, regime), data)
        return ContractDocument(
            kind=kind,
            regime=regime,
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
```

`data_for_sending` reads the document's own regime:

```python
        if document.kind == QUADRO:
            return self._quadro_data(user, fiscal, today, document.regime)
        signed = signed_on(framework) if framework is not None else None
        return {
            **document.data,
            **self._rebase_fields(),
            **self._letter_party_fields(user, fiscal, signed, document.regime),
            **self._signing_fields(today),
        }
```

`write_framework`:

```python
    def write_framework(self, freelancer_id: UUID, admin_id: UUID, regime: str) -> ContractDocument:
        """A new framework agreement of `regime`, added and flushed and not committed:
        written by `create` for a fresh draft, and by `SigningService._framework_to_send`
        for a match whose letter waits on one that was cancelled or refused, sent in the
        same transaction (REB-406: one write, two callers). The caller must already hold
        the freelancer's row lock (REB-406)."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        fiscal = self._fiscal(freelancer.id)
        document = self._document(
            renderer,
            QUADRO,
            regime,
            self._quadro_data(user, fiscal, self.today(), regime),
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

In `_match_read`:

```python
            lettera=document_read(letter, today, current_quadro_version(letter.regime), facts),
```

`_framework_step`:

```python
    def _framework_step(
        self, freelancer_id: UUID, regime: str
    ) -> tuple[ContractDocument | None, FrameworkStep]:
        """The active framework agreement of `regime`, and where the next match's stands:
        `attivo`, `in_firma` when one is out for signature (the letter waits for it), else
        `da_inviare` (a new one leaves first, or the one generated for an earlier draft)."""
        active = active_framework(self.session, freelancer_id, regime)
        if active is not None:
            return active, "attivo"
        pending = pending_framework(self.session, freelancer_id, regime)
        out = pending is not None and pending.stato == "inviato"
        return None, "in_firma" if out else "da_inviare"
```

- [ ] **Step 5: Thread the regime through `signing.py`**

In `projects/hub/packages/core/src/rebase_core/signing.py`, the imports become:

```python
from rebase_core.contracts.render import ContractRenderer, Renderer
```

```python
from rebase_core.framework import (
    active_framework,
    current_quadro_version,
    document_read,
    is_active,
    pending_framework,
    rome_today,
)
```

```python
from rebase_core.matches import ENTITY, LETTERA, QUADRO, MatchService, _full_name
```

```python
from rebase_core.regimes import document_stem, text_name
```

In `send_match`, from the active framework to the dispatch:

```python
            regime = letter.regime
            active = active_framework(self.session, match.freelancer_id, regime)
            if not self.allow_draft:
                for kind in (LETTERA,) if active is not None else (QUADRO, LETTERA):
                    if renderer.is_draft(text_name(kind, regime)):
                        raise InvalidState(DRAFT_REFUSED[kind])
            if active is not None:
                leaving = letter
                self._dispatch(letter, active, admin_id)
            else:
                letter.stato = "in_attesa"
                leaving = self._framework_to_send(match.freelancer_id, admin_id, regime)
                if leaving is not None:
                    self._dispatch(leaving, None, admin_id)
```

`_framework_to_send`:

```python
    def _framework_to_send(
        self, freelancer_id: UUID, admin_id: UUID, regime: str
    ) -> ContractDocument | None:
        """The framework agreement of `regime` a waiting letter needs, locked: none when
        one is out for signature already (the letter leaves after it), the one generated
        for a draft, else a new one, written now because the last was cancelled or
        refused. The caller must already hold the freelancer's row lock."""
        self._signer()
        pending = pending_framework(self.session, freelancer_id, regime)
        if pending is None:
            return self.matches.write_framework(freelancer_id, admin_id, regime)
        framework = self._lock(pending.id)
        if framework.stato == "inviato":
            return None
        if framework.stato == "generato":
            return framework
        raise InvalidState(
            "Il contratto quadro è cambiato mentre lo inviavi: ricarica la pagina e riprova."
        )
```

In `_dispatch`:

```python
        name = text_name(document.kind, document.regime)
```

In `_send_waiting`:

```python
        if not self.allow_draft and self._renderer().is_draft(text_name(LETTERA, letter.regime)):
            raise InvalidState(DRAFT_REFUSED[LETTERA])
```

`_read`:

```python
    def _read(self, document_id: UUID) -> ContractDocumentRead:
        document = self._document(document_id)
        return document_read(document, self.today(), current_quadro_version(document.regime))
```

- [ ] **Step 6: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_framework.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_matches.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_signing.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_member_contracts.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_referrals.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_engagements.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_matches_api.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_signing_api.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_tools.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints (`grep -rn DOCUMENT_BY_KIND projects/hub/packages projects/hub/apps` must find nothing).

- [ ] **Step 7: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/framework.py \
  projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/matches.py \
  projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/tests/test_framework.py \
  projects/hub/packages/core/tests/test_matches.py \
  projects/hub/packages/core/tests/test_signing.py
git commit -F - <<'MSG'
feat(hub): framework lookups and documents carry a regime

Every lookup of a freelancer's framework agreement now takes a regime,
active or pending, and the states the sentences read are keyed by
freelancer and regime, so an ordinary agreement never stands in for an
occasional one. Documents are written with their regime and typeset from
its text, each quadro compares its version with its own text, and every
document read names its regime and its type. New matches stay ordinary
until the match itself chooses.

REB-687.
MSG
git log -1 --format=%B
```

### Task 6: The match chooses its regime

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py` (`MatchCreate`, `FreelancerContracts`, `MatchPrefill`, `MatchCheck`)
- Modify: `projects/hub/packages/core/src/rebase_core/matches.py` (module docstring; new `require_regime_fits` and `shown_framework`; `for_freelancer`, `prefill`, `proposal`, `preview`, `check`, `create`, `_lettera_data`)
- Test: `projects/hub/packages/core/tests/test_matches.py`; `projects/hub/apps/api/tests/test_matches_api.py` (the check's exact answer gains `regime`)

**Interfaces:**
- Consumes: Task 4's `ORDINARY_NEEDS_PARTITA_IVA` and `check_sentences(..., regime=, ha_partita_iva=)`; Task 5's per-regime `active_framework`, `pending_framework`, `write_framework`, `_document`, `_letter_party_fields`, `_framework_step`, `current_quadro_version`.
- Produces, in `contract_schemas`:
  - `MatchCreate.regime: Regime = PARTITA_IVA` (part of the request fingerprint, like every field but `id`)
  - `MatchPrefill.regime: Regime`: `occasionale` when the saved tax data have no partita IVA, else `partita_iva` (none saved included); its `quadro_attivo`, `quadro_necessario` and `lettera_in_attesa` are that regime's
  - `MatchCheck.regime: Regime`
  - `FreelancerContracts.quadro_occasionale: ContractDocumentRead | None` beside `quadro` (still the ordinary one)
- Produces, in `rebase_core.matches`:
  - `require_regime_fits(partita_iva: str | None, regime: str) -> None`: `ValidationFailed("match", "regime", ORDINARY_NEEDS_PARTITA_IVA)` for an ordinary letter without a partita IVA
  - `shown_framework(quadri: list[ContractDocumentRead], regime: str) -> ContractDocumentRead | None`
  - `MatchService.proposal(freelancer_id, company_id, cliente=None, lettera=None, match_id=None, regime: Regime | None = None) -> MatchCreate`: `None` takes the prefill's regime
  - `check`, `preview` and `create` refuse an ordinary letter without a partita IVA; `create` writes and reuses framework agreements of the match's regime only, and cleans up stale `generato` ones of that regime only

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_matches.py`, import `update` from `sqlalchemy` and `Regime` from `rebase_core.regimes`, and change two helpers:

```python
def _fiscal(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    partita_iva: str | None = "01234567890",
) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z",
            partita_iva=partita_iva,
            domicilio="Via Roma 1, Milano",
        ),
        admin_id,
    )
```

```python
def _body(
    company_id: UUID, *, giorni_previsti: int | None = None, regime: Regime = "partita_iva"
) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        regime=regime,
        cliente=ClienteData(
            cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
        ),
        lettera=LetteraFields(
            ruolo="Backend developer",
            attivita="Le API del prodotto.",
            data_inizio=date(2026, 10, 1),
            modalita="a giornata",
            compenso=Decimal("450"),
            giorni_pagamento=30,
            fine_mese=True,
        ),
        giorni_previsti=giorni_previsti,
    )
```

and add after `_setup`:

```python
def _setup_occasional(session: Session) -> tuple[UUID, UUID, UUID]:
    """A freelancer whose tax data were saved without a partita IVA (REB-687)."""
    admin_id, freelancer_id, company_id = _admin(session), _card(session), _request(session)
    _fiscal(session, freelancer_id, admin_id, partita_iva=None)
    return admin_id, freelancer_id, company_id
```

In `test_check_says_what_saving_would_do_and_which_document_leaves_first`, the expected `MatchCheck(...)` gains `regime="partita_iva",` after `dati_fiscali_mancanti=False,`. In `projects/hub/apps/api/tests/test_matches_api.py`, `test_the_check_says_what_saving_would_do_and_writes_nothing`'s expected dict gains `"regime": "partita_iva",` after `"dati_fiscali_mancanti": False,`.

Append to `projects/hub/packages/core/tests/test_matches.py`:

```python
# ---- the letter type (REB-687) -----------------------------------------------------------

REFUSED_WITHOUT_PARTITA_IVA = (
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
    "o salva la partita IVA."
)


def test_the_prefill_proposes_the_letter_type_from_the_tax_data(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    service = _service(clean)
    assert service.prefill(freelancer_id, company_id).regime == "partita_iva"  # none saved
    _fiscal(clean, freelancer_id, admin_id)
    assert service.prefill(freelancer_id, company_id).regime == "partita_iva"
    _fiscal(clean, freelancer_id, admin_id, partita_iva=None)
    prefill = service.prefill(freelancer_id, company_id)
    assert (prefill.regime, prefill.quadro_necessario) == ("occasionale", True)
    proposed = service.proposal(freelancer_id, company_id, cliente=CLIENTE_REST)
    assert proposed.regime == "occasionale"
    chosen = service.proposal(
        freelancer_id, company_id, cliente=CLIENTE_REST, regime="partita_iva"
    )
    assert chosen.regime == "partita_iva"


@pytest.mark.parametrize("action", ["create", "check", "preview"])
def test_an_ordinary_letter_without_a_partita_iva_is_refused_with_the_sentence(
    clean: Session, action: str
) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    service = _service(clean)
    attempts = {
        "create": lambda: service.create(freelancer_id, _body(company_id), admin_id),
        "check": lambda: service.check(freelancer_id, _body(company_id)),
        "preview": lambda: service.preview(freelancer_id, _body(company_id), "lettera"),
    }
    with pytest.raises(ValidationFailed) as refused:
        attempts[action]()
    assert refused.value.details == {
        "entity": "match",
        "field": "regime",
        "reason": REFUSED_WITHOUT_PARTITA_IVA,
    }
    assert clean.scalar(select(func.count()).select_from(Match)) == 0


def test_an_occasional_letter_for_a_partita_iva_holder_is_allowed_and_warned(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    checked = service.check(freelancer_id, _body(company_id, regime="occasionale"))
    assert checked.regime == "occasionale"
    assert checked.riepilogo[-2:] == [
        "Compenso: 450,00 € a giornata, lordi, senza IVA, pagato a 30 giorni fine mese.",
        "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla "
        "sua attività professionale.",
    ]
    assert checked.cosa_succede == (
        "Prima parte il contratto quadro per prestazioni occasionali; la lettera di incarico "
        "occasionale parte da sola dopo la sua firma."
    )
    match = service.create(freelancer_id, _body(company_id, regime="occasionale"), admin_id)
    assert match.lettera.tipo == "Lettera di incarico occasionale"


def test_an_occasional_match_writes_the_occasional_texts_and_prints_the_codice_fiscale(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )
    assert [document for document, _ in renderer.calls] == [
        "contratto-quadro-occasionale",
        "lettera-di-incarico-occasionale",
    ]
    quadro, lettera = _documents(clean, freelancer_id)
    assert (quadro.regime, quadro.stato, lettera.regime, lettera.stato) == (
        "occasionale",
        "generato",
        "occasionale",
        "in_attesa",
    )
    (_, quadro_data), (_, letter_data) = renderer.calls
    assert "professionista-piva" not in quadro_data
    assert "professionista-piva" not in letter_data
    assert letter_data["professionista-cf"] == quadro_data["professionista-cf"] == (
        "LVLDAA85T50H501Z"
    )
    for document, data in renderer.calls:
        asked = set(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        assert asked - {TERM} <= set(data), document
    assert (match.lettera.regime, match.lettera.tipo) == (
        "occasionale",
        "Lettera di incarico occasionale",
    )


def test_an_occasional_match_reuses_an_active_occasional_framework(clean: Session) -> None:
    """23:30 UTC on 30 September is 1 October in Rome."""
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    _framework(
        clean,
        freelancer_id,
        admin_id,
        signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
        regime="occasionale",
    )
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )
    assert [document for document, _ in renderer.calls] == ["lettera-di-incarico-occasionale"]
    assert match.lettera.stato == "generato"
    assert renderer.calls[0][1]["data-contratto-quadro"] == "1° ottobre 2026"


@pytest.mark.parametrize(
    ("stato", "signed_at"),
    [("firmato", datetime(2026, 10, 1, 9, 0, tzinfo=UTC)), ("inviato", None)],
)
def test_an_occasional_match_ignores_an_ordinary_framework_active_or_out_for_signature(
    clean: Session, stato: str, signed_at: datetime | None
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    ordinary = _framework(clean, freelancer_id, admin_id, stato=stato, signed_at=signed_at)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )
    assert [document for document, _ in renderer.calls] == [
        "contratto-quadro-occasionale",
        "lettera-di-incarico-occasionale",
    ]
    assert match.lettera.stato == "in_attesa"
    clean.refresh(ordinary)
    assert ordinary.stato == stato


def test_the_stale_framework_cleanup_leaves_the_other_regimes_frameworks_alone(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasionale"), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasionale"), admin_id)
    assert [(d.regime, d.stato) for d in _documents(clean, freelancer_id, "quadro")] == [
        ("partita_iva", "generato"),
        ("occasionale", "annullato"),
        ("occasionale", "generato"),
    ]


def test_the_contracts_page_shows_one_framework_per_regime(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    assert service.for_freelancer(freelancer_id).quadro_occasionale is None
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasionale"), admin_id)
    page = service.for_freelancer(freelancer_id)
    assert page.quadro is not None and page.quadro_occasionale is not None
    assert (page.quadro.regime, page.quadro.tipo) == ("partita_iva", "Contratto quadro")
    assert (page.quadro_occasionale.regime, page.quadro_occasionale.tipo) == (
        "occasionale",
        "Contratto quadro per prestazioni occasionali",
    )
    assert len(page.quadri) == 2


def test_an_occasional_matchs_sentence_reads_its_own_framework_not_the_ordinary_one(
    clean: Session,
) -> None:
    """With only the ordinary framework active, an occasional letter
    waiting in signature is not «pronta a partire». The card, the list row and `get`
    agree."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)  # the ordinary one, active
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id, regime="occasionale"), admin_id)
    clean.execute(update(Match).where(Match.id == match.id).values(stato="in_firma"))
    clean.commit()
    expected = (
        f"La lettera n. {match.lettera.numero} parte dopo il contratto quadro: «Invia per la "
        "firma» lo manda al freelance."
    )
    assert service.get(match.id).situazione == expected
    [row] = service.list_all(stato=None, q=None).items
    assert row.situazione == expected
    [card] = service.for_freelancer(freelancer_id).matches
    assert card.situazione == expected


def test_a_repeated_id_with_the_regime_changed_is_refused(clean: Session) -> None:
    """The same client-generated id with the other letter type is another
    request, a 409, never the stale match of the other type handed back."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    body = _body(company_id).model_copy(update={"id": uuid4()})
    service.create(freelancer_id, body, admin_id)
    with pytest.raises(InvalidState, match="dati diversi"):
        service.create(freelancer_id, body.model_copy(update={"regime": "occasionale"}), admin_id)
    assert clean.scalar(select(func.count()).select_from(Match)) == 1


def test_an_occasional_preview_renders_the_occasional_texts(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    renderer = FakeRenderer()
    service = _service(clean, renderer)
    letter = service.preview(freelancer_id, _body(company_id, regime="occasionale"), "lettera")
    quadro = service.preview(freelancer_id, _body(company_id, regime="occasionale"), "quadro")
    assert (letter.filename, quadro.filename) == (
        "anteprima-lettera-di-incarico-occasionale.pdf",
        "anteprima-contratto-quadro-occasionale.pdf",
    )
    assert [document for document, _ in renderer.calls] == [
        "lettera-di-incarico-occasionale",
        "contratto-quadro-occasionale",
    ]
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_matches.py -q`
Expected: FAIL, `MatchCreate` refuses `regime` («Extra inputs are not permitted»), and `MatchPrefill` has no `regime`.

- [ ] **Step 3: Give the models the regime**

In `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, `MatchCreate` gains, after `company_id: UUID`:

```python
    # «Tipo di lettera» (REB-687): the letter's regime, and so the match's. An ordinary
    # letter needs saved tax data with a partita IVA; left out, the letter is ordinary.
    regime: Regime = PARTITA_IVA
```

`FreelancerContracts` becomes:

```python
class FreelancerContracts(BaseModel):
    """«Match e contratti»: the framework agreements at the top, one per regime (REB-687):
    `quadro` the ordinary one and `quadro_occasionale` the occasional one, each the
    active one, else the newest that left; every framework agreement newest first, the
    matches newest first, and the tax data the page edits."""

    freelancer_id: UUID
    quadro: ContractDocumentRead | None
    quadro_occasionale: ContractDocumentRead | None
    quadri: list[ContractDocumentRead]
    matches: list[MatchRead]
    fiscale: FiscalRead | None
```

`MatchPrefill` becomes:

```python
class MatchPrefill(BaseModel):
    """What «Chi e per chi» and «Condizioni» start from. `regime` is the letter type the
    hub proposes (REB-687): `occasionale` when the saved tax data have no partita IVA,
    `partita_iva` otherwise, none saved included. `quadro_necessario`: this match writes a
    framework agreement of that regime. `lettera_in_attesa`: the letter waits for a
    framework's signature."""

    fiscale: FiscalRead | None
    cliente: ClienteDraft
    lettera: LetteraDraft
    regime: Regime
    quadro_attivo: ContractDocumentRead | None
    quadro_necessario: bool
    lettera_in_attesa: bool
```

and `MatchCheck` gains, last:

```python
    regime: Regime
```

- [ ] **Step 4: Choose the regime in `matches.py`**

In `projects/hub/packages/core/src/rebase_core/matches.py`, add to the module docstring, after its first paragraph:

```
A match's regime is its letter's (REB-687), `partita_iva` or `occasionale`, chosen on
«Crea match» and proposed from the tax data: every framework lookup here takes it, so an
ordinary framework agreement never stands in for an occasional one, nor the reverse. An
ordinary letter needs a saved partita IVA (`require_regime_fits`).
```

Imports: add `ContractDocumentRead` to the `contract_schemas` import, `ORDINARY_NEEDS_PARTITA_IVA` to the `match_words` import, and:

```python
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, Regime, document_stem, text_name
```

After `luogo_suggestion`, add:

```python
def require_regime_fits(partita_iva: str | None, regime: str) -> None:
    """An ordinary letter prints the freelancer's partita IVA, so it needs saved tax data
    with one (REB-687). An occasional letter never prints one and is allowed either way:
    the check warns a partita IVA holder (`match_words.HAS_PARTITA_IVA`). `check`,
    `preview` and `create` refuse on `regime`, the field «Tipo di lettera» sets."""
    if regime == PARTITA_IVA and partita_iva is None:
        raise ValidationFailed(ENTITY, "regime", ORDINARY_NEEDS_PARTITA_IVA)


def shown_framework(
    quadri: list[ContractDocumentRead], regime: str
) -> ContractDocumentRead | None:
    """The framework agreement of `regime` «Match e contratti» shows, one card per regime
    (REB-687): the active one when there is one; else the newest that actually left,
    whatever its state now. An `annullato` one is hidden only when it never left
    (`sent_at` is `None`, a stale draft «Crea match» replaced, spec § 6), never when a
    refusal or a cancellation turned a sent one `annullato`."""
    mine = [quadro for quadro in quadri if quadro.regime == regime]
    return next((quadro for quadro in mine if quadro.attivo), None) or next(
        (quadro for quadro in mine if quadro.stato != "annullato" or quadro.sent_at is not None),
        None,
    )
```

In `for_freelancer`, delete the `shown = ...` block (and its comment) and return:

```python
        return FreelancerContracts(
            freelancer_id=freelancer_id,
            quadro=shown_framework(quadri, PARTITA_IVA),
            quadro_occasionale=shown_framework(quadri, OCCASIONALE),
            quadri=quadri,
            matches=[
                self._match_read(
                    match,
                    company,
                    letters[match.id],
                    framework_state(frameworks, letters[match.id].regime),
                )
                for match, company in rows
                if match.id in letters
            ],
            fiscale=FiscalService(self.session).get(freelancer_id),
        )
```

In `prefill`, from `active, step = ...` to the end:

```python
        fiscale = FiscalService(self.session).get(freelancer.id)
        regime: Regime = (
            OCCASIONALE if fiscale is not None and fiscale.partita_iva is None else PARTITA_IVA
        )
        active, step = self._framework_step(freelancer.id, regime)
        today = self.today()
        return MatchPrefill(
            fiscale=fiscale,
            cliente=cliente,
            lettera=lettera,
            regime=regime,
            quadro_attivo=(
                document_read(active, today, current_quadro_version(regime))
                if active is not None
                else None
            ),
            quadro_necessario=step == "da_inviare",
            lettera_in_attesa=active is None,
        )
```

`proposal` takes the regime:

```python
    def proposal(
        self,
        freelancer_id: UUID,
        company_id: UUID,
        cliente: dict[str, object] | None = None,
        lettera: dict[str, object] | None = None,
        match_id: UUID | None = None,
        regime: Regime | None = None,
    ) -> MatchCreate:
        """`prefill` as the body `create` takes, each field given in `cliente` or
        `lettera` laid over what the hub suggests (a `None` given blanks a suggestion):
        what an MCP tool saves when the admin asked for a match in a sentence. `regime`
        `None` takes the letter type the prefill proposes (REB-687). A key that is not a
        field of the letter or of the client, and a required field still empty once laid
        over, are a `ValidationFailed` naming it (`lettera.compenso`), in Italian
        (`field_reason`)."""
        prefill = self.prefill(freelancer_id, company_id)
        body: dict[str, object] = {
            "id": match_id,
            "company_id": company_id,
            "regime": regime if regime is not None else prefill.regime,
        }
```

(the rest of `proposal` stays as it is).

`preview`:

```python
    def preview(self, freelancer_id: UUID, data: MatchCreate, kind: str) -> ContractPdf:
        """One document as `create` would write it, of the match's regime, with no number,
        and nothing saved."""
        renderer = self._renderer()
        freelancer, user = self._freelancer(freelancer_id)
        self._matchable_company(data.company_id)
        fiscal = self._fiscal(freelancer.id)
        require_regime_fits(fiscal.partita_iva, data.regime)
        today = self.today()
        if kind not in (QUADRO, LETTERA):
            raise ValidationFailed(ENTITY, "documento", "uno fra lettera e quadro")
        name = text_name(kind, data.regime)
        if kind == QUADRO:
            fields = self._quadro_data(user, fiscal, today, data.regime)
        else:
            active = active_framework(self.session, freelancer.id, data.regime)
            fields = self._lettera_data(user, fiscal, data, None, active, today)
        rendered = renderer.render(name, fields)
        return ContractPdf(filename=f"anteprima-{name}.pdf", content=rendered.pdf)
```

`check`:

```python
    def check(self, freelancer_id: UUID, payload: MatchCreate) -> MatchCheck:
        """What saving `payload` would do, in sentences: step 3 of «Crea match», and what
        an MCP tool reads back before it saves. Refused as `create` refuses it (a card or
        a request gone, a closed request, an ordinary letter without a partita IVA),
        except for missing tax data, which are reported rather than refused since the
        wizard saves them on the way. Reads only: nothing is written, no letter number is
        taken, no renderer is needed."""
        _freelancer, user = self._freelancer(freelancer_id)
        self._matchable_company(payload.company_id)
        fiscal = self.session.scalar(
            select(FreelancerFiscal)
            .where(FreelancerFiscal.freelancer_id == freelancer_id)
            .execution_options(populate_existing=True)
        )
        if fiscal is not None:
            require_regime_fits(fiscal.partita_iva, payload.regime)
        _active, step = self._framework_step(freelancer_id, payload.regime)
        riepilogo, cosa_succede = check_sentences(
            _full_name(user),
            payload.cliente.cliente_ragione_sociale,
            payload.lettera,
            step,
            dati_fiscali_mancanti=fiscal is None,
            regime=payload.regime,
            ha_partita_iva=fiscal is not None and fiscal.partita_iva is not None,
        )
        return MatchCheck(
            riepilogo=riepilogo,
            cosa_succede=cosa_succede,
            quadro_necessario=step == "da_inviare",
            dati_fiscali_mancanti=fiscal is None,
            regime=payload.regime,
        )
```

In `create`, from `fiscal = self._fiscal(freelancer.id)` to the framework write:

```python
            fiscal = self._fiscal(freelancer.id)
            require_regime_fits(fiscal.partita_iva, data.regime)
            today = self.today()
            active = active_framework(self.session, freelancer.id, data.regime)
            documents: list[ContractDocument] = []
            stale_ids: list[UUID] = []
            if active is None:
                pending = pending_framework(self.session, freelancer.id, data.regime)
                if pending is None or pending.stato != "inviato":
                    for stale in list(
                        self.session.scalars(
                            select(ContractDocument).where(
                                ContractDocument.kind == QUADRO,
                                ContractDocument.freelancer_id == freelancer.id,
                                ContractDocument.regime == data.regime,
                                ContractDocument.stato == "generato",
                            )
                        )
                    ):
                        stale.stato = "annullato"
                        stale_ids.append(stale.id)
                    # REB-406: the same write `write_framework` makes for a match whose
                    # letter waits on a cancelled or refused one -- `create` already
                    # holds the freelancer's row lock `write_framework` assumes.
                    documents.append(self.write_framework(freelancer.id, admin_id, data.regime))
```

and in the letter's `self._document(...)` call the regime argument becomes `data.regime`. In `_lettera_data`:

```python
            **self._letter_party_fields(user, fiscal, signed, data.regime),
```

No `PARTITA_IVA` is left in `prefill`, `preview`, `check`, `create` or `_lettera_data`: `grep -n "PARTITA_IVA" projects/hub/packages/core/src/rebase_core/matches.py` finds only the import, `require_regime_fits`, `for_freelancer`, `prefill`'s proposal and `_quadro_data`/`_letter_party_fields`.

- [ ] **Step 5: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_matches.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_signing.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_member_contracts.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_referrals.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_engagements.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_matches_api.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_tools.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints.

- [ ] **Step 6: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/matches.py \
  projects/hub/packages/core/tests/test_matches.py \
  projects/hub/apps/api/tests/test_matches_api.py
git commit -F - <<'MSG'
feat(hub): a match chooses its letter type

MatchCreate, the prefill and the check carry the regime: the prefill
proposes the occasional letter when the saved tax data have no partita
IVA, an ordinary letter without one is refused in the spec's sentence,
and a partita IVA holder's occasional letter is allowed with a warning.
An occasional match writes or reuses only an occasional framework
agreement, cleans up only stale ones of its own regime, prints the
codice fiscale, and the contracts page shows one framework agreement per
regime.

REB-687.
MSG
git log -1 --format=%B
```

### Task 7: The signing chain keeps the two families apart

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/signing.py` (module docstring; `_to_finish`, `_release_letters`, `_send_waiting`)
- Test: `projects/hub/packages/core/tests/test_signing.py`

**Interfaces:**
- Consumes: Task 5's per-regime `active_framework`, `_framework_to_send(freelancer_id, admin_id, regime)`; Task 6's `MatchCreate.regime`; Task 3's per-regime names and fake blanks.
- Produces: a signed framework agreement releases only the waiting letters of its own regime (`_release_letters`), a waiting letter leaves only when the active framework agreement of its own regime is signed (`_send_waiting`), and the sweep picks an active framework agreement only for waiting letters of its regime (`_to_finish`). No signature changes.

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_signing.py`, import `from rebase_core.contracts.render import text_version as current_text_version` beside the other `render` names and `from rebase_core.regimes import text_name`; give `_active_framework` a regime:

```python
def _active_framework(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    signed_at: datetime = SIGNED_AT,
    regime: str = "partita_iva",
) -> ContractDocument:
    """A framework agreement as a signature leaves one."""
    document = ContractDocument(
        kind="quadro",
        regime=regime,
        freelancer_id=freelancer_id,
        text_version=current_text_version(text_name("quadro", regime)),
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
```

and append:

```python
# ---- two families of documents (REB-687) -------------------------------------------------

OCCASIONAL_QUADRO_VERSION = current_text_version("contratto-quadro-occasionale")


def _without_partita_iva(session: Session, freelancer_id: UUID, admin_id: UUID) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(codice_fiscale="LVLDAA85T50H501Z", domicilio="Via Roma 1, Milano"),
        admin_id,
    )


def _occasional_draft(
    session: Session, renderer: Renderer, freelancer_id: UUID, company_id: UUID, admin_id: UUID
) -> MatchRead:
    return _matches(session, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )


def _quadro_of(session: Session, freelancer_id: UUID, regime: str) -> ContractDocument:
    return [d for d in _documents(session, freelancer_id, "quadro") if d.regime == regime][-1]


def _sign(signing: SigningService, fake: FakeDocumenso, document: ContractDocument) -> None:
    envelope = _envelope_of(document)
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)


@pytest.mark.parametrize(
    ("first", "other"), [("occasionale", "partita_iva"), ("partita_iva", "occasionale")]
)
def test_a_framework_signature_releases_only_the_letters_of_its_own_regime(
    clean: Session, first: str, other: str
) -> None:
    """A partita IVA holder may hold both families: each framework agreement's signature
    releases its own regime's waiting letter and leaves the other one waiting."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    matches = {
        "partita_iva": _draft(clean, renderer, freelancer_id, company_id, admin_id),
        "occasionale": _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id),
    }
    for match in matches.values():
        assert signing.send_match(match.id, admin_id).inviato == "quadro"

    _sign(signing, fake, _quadro_of(clean, freelancer_id, first))

    assert _letter_of(clean, matches[first].id).stato == "inviato"
    assert _letter_of(clean, matches[other].id).stato == "in_attesa"


def test_an_occasional_letter_waits_while_only_the_ordinary_framework_is_active(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    ordinary = _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "in_attesa"

    report = signing.send_match(match.id, admin_id)

    assert (report.inviato, report.match.lettera.stato) == ("quadro", "in_attesa")
    assert _quadro_of(clean, freelancer_id, "occasionale").stato == "inviato"
    # The sweep never takes the ordinary agreement for this letter's own.
    assert ordinary.id not in signing._to_finish()
    signing.finish(ordinary.id)
    assert _letter_of(clean, match.id).stato == "in_attesa"


def test_an_occasional_match_turns_active_on_its_letters_signature(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id, regime="occasionale")
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "generato"
    assert signing.send_match(match.id, admin_id).inviato == "lettera"

    _sign(signing, fake, _letter_of(clean, match.id))

    clean.expire_all()
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]
    assert _letter_of(clean, match.id).stato == "firmato"


def test_an_occasional_documents_names_are_its_own_on_documenso_and_in_the_mail(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _without_partita_iva(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(match.id, admin_id)

    [quadro_envelope] = fake.envelopes.values()
    assert quadro_envelope.payload["title"] == "Contratto quadro rebase per prestazioni occasionali"
    assert quadro_envelope.filename == (
        f"contratto-quadro-occasionale-v{OCCASIONAL_QUADRO_VERSION}.pdf"
    )
    assert sender.sent[-1].subject == (
        "Da firmare: contratto quadro rebase per prestazioni occasionali"
    )

    _sign(signing, fake, _quadro_of(clean, freelancer_id, "occasionale"))

    letter = _letter_of(clean, match.id)
    letter_envelope = list(fake.envelopes.values())[-1]
    assert letter_envelope.payload["title"] == f"Lettera di incarico occasionale n. {letter.numero}"
    assert letter_envelope.filename == f"lettera-di-incarico-occasionale-{letter.numero}.pdf"
    assert sender.sent[-1].subject == (
        f"Da firmare: lettera di incarico occasionale n. {letter.numero}"
    )
    document, data = renderer.calls[-1]
    assert document == "lettera-di-incarico-occasionale"
    assert data["professionista-cf"] == "LVLDAA85T50H501Z"
    assert "professionista-piva" not in data


def test_an_ordinary_draft_whose_partita_iva_was_removed_does_not_leave(clean: Session) -> None:
    """The tax data changed after the draft, so the ordinary framework
    agreement would leave with a blank partita IVA: refused, naming it, and nothing
    reaches Documenso or the mailbox."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _without_partita_iva(clean, freelancer_id, admin_id)

    with pytest.raises(InvalidState, match="professionista-piva"):
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert len(fake.envelopes) == 0 and sender.sent == []
    assert _framework_of(clean, freelancer_id).stato == "generato"
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_signing.py -q`
Expected: FAIL in `test_a_framework_signature_releases_only_the_letters_of_its_own_regime` (both letters `inviato`: the release does not look at the regime) and in `test_an_occasional_letter_waits_while_only_the_ordinary_framework_is_active` (the ordinary agreement is in `_to_finish()`, and its `finish` dispatches the occasional letter).

- [ ] **Step 3: Keep the families apart**

In `projects/hub/packages/core/src/rebase_core/signing.py`, add to the module docstring, after its first paragraph:

```
A freelancer may hold framework agreements of both regimes (REB-687): a letter leaves
only once the framework agreement of its own regime is signed, and a signature releases
only the waiting letters of its own regime.
```

In `_to_finish`, the waiting letter must be of the framework agreement's regime:

```python
        frameworks_with_waiting_letters = select(ContractDocument.id).where(
            ContractDocument.kind == QUADRO,
            ContractDocument.stato == "firmato",
            ContractDocument.notice_at.is_(None),
            exists(
                select(1)
                .select_from(waiting_letter)
                .join(Match, Match.id == waiting_letter.match_id)
                .where(
                    waiting_letter.kind == LETTERA,
                    waiting_letter.stato == "in_attesa",
                    waiting_letter.freelancer_id == ContractDocument.freelancer_id,
                    waiting_letter.regime == ContractDocument.regime,
                    Match.stato == "in_firma",
                )
            ),
        )
```

In `_release_letters`, the docstring's first sentence becomes «The letters of this framework agreement's regime that waited for it (spec § 1e, REB-687), each in a transaction of its own and mailed after its commit: ...», and the query:

```python
        waiting = list(
            self.session.execute(
                select(ContractDocument.id, Match.id)
                .join(Match, Match.id == ContractDocument.match_id)
                .where(
                    ContractDocument.kind == LETTERA,
                    ContractDocument.freelancer_id == framework.freelancer_id,
                    ContractDocument.regime == framework.regime,
                    ContractDocument.stato == "in_attesa",
                    Match.stato == "in_firma",
                )
                .order_by(ContractDocument.created_at, ContractDocument.id)
            )
        )
```

In `_send_waiting`, the guard:

```python
        if (
            letter.stato != "in_attesa"
            or match.stato != "in_firma"
            or framework is None
            or not is_active(framework)
            or framework.regime != letter.regime
        ):
            self.session.rollback()
            return None
```

- [ ] **Step 4: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_signing.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_referrals.py -q`
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_member_contracts.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_signing_api.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_documenso_webhook_api.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/signing.py \
  projects/hub/packages/core/tests/test_signing.py
git commit -F - <<'MSG'
feat(hub): a signature releases only its own regime's letters

A freelancer may hold an ordinary and an occasional framework agreement
at once, so a waiting letter now leaves only when the agreement of its
own regime is active, a signature releases only the letters of its
regime, and the sweep looks for waiting letters of the same regime. The
tests also pin the occasional names on Documenso and in the mail, and an
ordinary draft whose partita IVA went away does not leave.

REB-687.
MSG
git log -1 --format=%B
```

### Task 8: The member area shows one framework agreement per regime

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/contract_schemas.py` (`MemberContract`, `MemberContracts`)
- Modify: `projects/hub/packages/core/src/rebase_core/member_contracts.py:51-101`
- Test: `projects/hub/packages/core/tests/test_member_contracts.py`, `projects/hub/apps/api/tests/test_member_contracts_api.py`

**Interfaces:**
- Consumes: Task 1's `as_regime`, `document_label`, `PARTITA_IVA`, `OCCASIONALE`; Task 6's occasional matches; Task 7's per-regime release.
- Produces: `MemberContract.regime: Regime`, `MemberContract.tipo: str`; `MemberContracts.quadro_occasionale: MemberContract | None = None` beside `quadro` (the ordinary one). `quadri_precedenti` leaves out both framework agreements shown on top.

- [ ] **Step 1: Write the failing tests**

In `projects/hub/packages/core/tests/test_member_contracts.py`, add `_body` to the `test_matches` import and `_matches` to the `test_signing` import, and append:

```python
def test_an_occasional_framework_shows_beside_the_ordinary_one_with_its_own_link(
    clean: Session,
) -> None:
    """REB-687: an active ordinary framework agreement and an occasional one out for
    signature both show on top, the occasional one with its signing link, and neither is
    filed under «precedenti»; nor is either once both are signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    signing = _signing(clean, renderer, fake, RecordingSender())
    ordinary = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(ordinary.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)
    occasional = _matches(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )
    signing.send_match(occasional.id, admin_id)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.quadro is not None and mine.quadro_occasionale is not None
    assert (mine.quadro.regime, mine.quadro.tipo, mine.quadro.attivo) == (
        "partita_iva",
        "Contratto quadro",
        True,
    )
    assert (mine.quadro_occasionale.regime, mine.quadro_occasionale.stato) == (
        "occasionale",
        "inviato",
    )
    assert mine.quadro_occasionale.tipo == "Contratto quadro per prestazioni occasionali"
    assert mine.quadro_occasionale.signing_url is not None
    assert mine.quadri_precedenti == []
    assert sorted((lettera.tipo, lettera.stato) for lettera in mine.lettere) == [
        ("Lettera di incarico", "inviato"),
        ("Lettera di incarico occasionale", "in_attesa"),
    ]

    occasional_quadro = _framework_of(clean, freelancer_id)
    envelope = _envelope_of(occasional_quadro)
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))
    assert mine.quadro is not None and mine.quadro_occasionale is not None
    assert (mine.quadro.attivo, mine.quadro_occasionale.attivo) == (True, True)
    assert mine.quadri_precedenti == []
```

In `projects/hub/apps/api/tests/test_member_contracts_api.py`, the exact answer for a person with no contracts becomes:

```python
    assert client.get("/api/hub/me/contracts").json() == {
        "quadro": None,
        "quadro_occasionale": None,
        "quadri_precedenti": [],
        "lettere": [],
    }
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/packages/core/tests/test_member_contracts.py -q`
Expected: FAIL, `'MemberContracts' object has no attribute 'quadro_occasionale'`.

- [ ] **Step 3: Split the framework agreements by regime**

In `projects/hub/packages/core/src/rebase_core/contract_schemas.py`, `MemberContract` gains after `kind: str`:

```python
    # Which family it belongs to and its type as the page names it (REB-687).
    regime: Regime
    tipo: str
```

and `MemberContracts` becomes:

```python
class MemberContracts(BaseModel):
    """«Contratti»: the framework agreements on top, one per regime (REB-687): `quadro`
    the ordinary one and `quadro_occasionale` the occasional one, each the active one,
    else the newest that reached the person; the letters newest first; and
    `quadri_precedenti` (REB-392): the freelancer's other framework agreements that were
    signed, newest first, excluding the two on top -- a notice, or a newer one replacing
    it, must not make an earlier signed copy disappear from the page."""

    quadro: MemberContract | None
    quadro_occasionale: MemberContract | None = None
    quadri_precedenti: list[MemberContract] = Field(default_factory=list)
    lettere: list[MemberContract]
```

In `projects/hub/packages/core/src/rebase_core/member_contracts.py`, import:

```python
from rebase_core.regimes import OCCASIONALE, PARTITA_IVA, as_regime, document_label
```

`_read` gains, after `kind=document.kind,`:

```python
        regime=as_regime(document.regime),
        tipo=document_label(document.kind, document.regime),
```

add:

```python
def _current(quadri: list[MemberContract], regime: str) -> MemberContract | None:
    """The framework agreement of `regime` the page shows on top (REB-687): the active
    one, else the newest that reached the person."""
    mine = [quadro for quadro in quadri if quadro.regime == regime]
    return next((quadro for quadro in mine if quadro.attivo), None) or (mine[0] if mine else None)
```

and in `for_user`, from `quadro = ...` to the return:

```python
        quadro = _current(quadri, PARTITA_IVA)
        quadro_occasionale = _current(quadri, OCCASIONALE)
        # Every other framework agreement that was signed (REB-392): a notice, or a
        # newer one taking over on top, must not make its own signed copy disappear
        # from the page, only move it down here. Newest first, same as `quadri`.
        on_top = {shown.id for shown in (quadro, quadro_occasionale) if shown is not None}
        quadri_precedenti = [q for q in quadri if q.ha_pdf_firmato and q.id not in on_top]
        lettere = [
            _read(document, match, today) for document, match in visible if document.kind == LETTERA
        ]
        return MemberContracts(
            quadro=quadro,
            quadro_occasionale=quadro_occasionale,
            quadri_precedenti=quadri_precedenti,
            lettere=lettere,
        )
```

The module docstring gains one sentence at the end: «Since REB-687 a freelancer may hold an ordinary and an occasional framework agreement, and each shows on top in its own place.»

- [ ] **Step 4: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/packages/core/tests/test_member_contracts.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_member_contracts_api.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/contract_schemas.py \
  projects/hub/packages/core/src/rebase_core/member_contracts.py \
  projects/hub/packages/core/tests/test_member_contracts.py \
  projects/hub/apps/api/tests/test_member_contracts_api.py
git commit -F - <<'MSG'
feat(hub): the member area shows each framework agreement on its own

MemberContracts gets quadro_occasionale beside quadro, by the same rule,
so an active occasional agreement is not filed under the earlier ones
and a pending one shows its signing link. Every contract the member
reads names its regime and its type.

REB-687.
MSG
git log -1 --format=%B
```

### Task 9: The MCP tools take the letter type

**Files:**
- Modify: `projects/hub/apps/mcp/src/rebase_mcp/server.py` (`_proposal` at `:141-158`; `list_matches`, `get_match`, `preview_match`, `create_match` at `:1052-1215`)
- Test: `projects/hub/apps/mcp/tests/test_match_write_tools.py`, `projects/hub/apps/api/tests/test_matches_api.py`

**Interfaces:**
- Consumes: Task 6's `MatchService.proposal(..., regime=)`, `MatchCheck.regime`, `FreelancerContracts.quadro_occasionale`; Task 5's `ContractDocumentRead.regime` and `tipo`.
- Produces:
  - `preview_match(freelancer_id, company_id, cliente=None, condizioni=None, regime: Regime | None = None)` and `create_match(freelancer_id, company_id, cliente=None, condizioni=None, match_id=None, giorni_previsti=None, regime: Regime | None = None)`: `None` takes the hub's proposal.
  - `get_match` answers in `quadro` the framework agreement of the match's own regime; `list_matches` answers `quadro_occasionale` with its `pdf_url` beside `quadro`.
  - The API needs no route change: `MatchCreate`, `MatchPrefill` and `MatchCheck` carry `regime` through the existing routes. Its tests pin that end to end.

- [ ] **Step 1: Write the failing tests**

Append to `projects/hub/apps/mcp/tests/test_match_write_tools.py`:

```python
# ---- the letter type (REB-687) -----------------------------------------------------------


def _seed_without_partita_iva(factory: sessionmaker[Session]) -> tuple[str, str]:
    freelancer_id, company_id = _seed(factory, fiscal=False)
    session = factory()
    try:
        FiscalService(session).save(
            UUID(freelancer_id),
            FiscalData(codice_fiscale=TAX["codice_fiscale"], domicilio=TAX["domicilio"]),
            IVAN.id,
        )
    finally:
        session.close()
    return freelancer_id, company_id


async def test_preview_and_create_match_take_the_hubs_letter_type_unless_told(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id, company_id = _seed_without_partita_iva(factory)
    world = World(
        factory,
        freelancer_id,
        company_id,
        FakeRenderer(draft=False),
        FakeDocumenso(),
        RecordingSender(),
    )
    try:
        async with Client(world.server()) as client:
            proposed = await _call(
                client,
                "preview_match",
                freelancer_id=freelancer_id,
                company_id=company_id,
                cliente=CLIENTE,
            )
            refused = await _refused(
                client,
                "preview_match",
                freelancer_id=freelancer_id,
                company_id=company_id,
                cliente=CLIENTE,
                regime="partita_iva",
            )
            match = await _create(client, world, cliente=CLIENTE)
        assert proposed["regime"] == "occasionale"
        assert any("lordi, senza IVA" in sentence for sentence in proposed["riepilogo"])
        assert refused.endswith(
            'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione '
            'occasionale" o salva la partita IVA.'
        )
        assert (match["lettera"]["regime"], match["lettera"]["tipo"]) == (
            "occasionale",
            "Lettera di incarico occasionale",
        )
    finally:
        _wipe(factory)


async def test_get_match_attaches_the_framework_of_the_matchs_own_regime(world: World) -> None:
    async with Client(world.server()) as client:
        ordinary = await _create(client, world, cliente=CLIENTE)
        occasional = await _create(client, world, cliente=CLIENTE, regime="occasionale")
        read_ordinary = await _call(client, "get_match", match_id=ordinary["id"])
        read_occasional = await _call(client, "get_match", match_id=occasional["id"])
        page = await _call(client, "list_matches", freelancer_id=world.freelancer_id)
    assert read_ordinary["quadro"]["tipo"] == "Contratto quadro"
    assert read_occasional["quadro"]["tipo"] == "Contratto quadro per prestazioni occasionali"
    assert read_occasional["quadro"]["id"] != read_ordinary["quadro"]["id"]
    assert page["quadro"]["id"] == read_ordinary["quadro"]["id"]
    assert page["quadro_occasionale"]["id"] == read_occasional["quadro"]["id"]
    assert page["quadro_occasionale"]["pdf_url"] == _link(page["quadro_occasionale"])
```

Append to `projects/hub/apps/api/tests/test_matches_api.py`:

```python
def test_a_match_for_a_member_without_a_partita_iva_is_occasional_end_to_end(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-687 over HTTP: the prefill proposes the occasional letter, the ordinary one is a
    422 on `regime` with the spec's sentence, and the occasional match reads and downloads
    as its own type."""
    freelancer_id, company_id = _ready(client, sender)
    fiscal = client.put(
        f"/api/hub/freelancers/{freelancer_id}/fiscal", json={**FISCAL, "partita_iva": None}
    )
    assert fiscal.status_code == 200, fiscal.text
    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.json()["regime"] == "occasionale"
    body = {"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    assert refused.status_code == 422
    assert refused.json()["detail"] == [
        {
            "loc": ["body", "regime"],
            "msg": 'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione '
            'occasionale" o salva la partita IVA.',
            "type": "value_error",
        }
    ]
    occasional = {**body, "regime": "occasionale"}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"documento": "lettera"},
        json=occasional,
    )
    assert preview.status_code == 200, preview.text
    assert "anteprima-lettera-di-incarico-occasionale.pdf" in preview.headers["content-disposition"]
    created = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=occasional)
    assert created.status_code == 201, created.text
    letter = created.json()["lettera"]
    assert (letter["regime"], letter["tipo"]) == ("occasionale", "Lettera di incarico occasionale")
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["quadro"] is None
    assert page["quadro_occasionale"]["tipo"] == "Contratto quadro per prestazioni occasionali"
    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert f"lettera-di-incarico-occasionale-{letter['numero']}.pdf" in (
        pdf.headers["content-disposition"]
    )
```

- [ ] **Step 2: Run them and see them fail**

Run: `timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py -q`
Expected: FAIL: `regime` is not a parameter of the tools yet, so the ordinary letter is not refused on request and `create_match` cannot make the occasional match `get_match` reads. The API test passes already (the routes carry the core models), which is what it pins.

- [ ] **Step 3: Teach the tools the letter type**

In `projects/hub/apps/mcp/src/rebase_mcp/server.py`, import `from rebase_core.regimes import OCCASIONALE, Regime`, and:

```python
def _proposal(
    service: MatchService,
    freelancer_id: UUID,
    company_id: UUID,
    cliente: dict[str, Any] | None,
    condizioni: dict[str, Any] | None,
    match_id: UUID | None = None,
    regime: Regime | None = None,
) -> MatchCreate:
    """The hub's proposal with the given fields laid over it, a refusal naming a letter's
    field by the parameter it came in (`condizioni.compenso`). `regime` `None` takes the
    letter type the hub proposes (REB-687)."""
    try:
        return service.proposal(
            freelancer_id, company_id, cliente, condizioni, match_id, regime
        )
    except ValidationFailed as exc:
        field = str(exc.details["field"])
        if not field.startswith(LETTER_PART):
            raise
        raise ValidationFailed(
            ENTITY, CONDITIONS_PART + field.removeprefix(LETTER_PART), str(exc.details["reason"])
        ) from exc
```

`list_matches`' docstring gains, after «`quadri` li elenca tutti;»: «`quadro_occasionale` è il contratto quadro per prestazioni occasionali, con la stessa regola (un freelance può averne uno per tipo);», and its link loop becomes:

```python
        for document in (body["quadro"], body["quadro_occasionale"], *body["quadri"]):
            if document is not None:
                _document_links(document)
```

`get_match`:

```python
    @mcp.tool()
    def get_match(match_id: str) -> dict[str, Any]:
        """Un match, per id: l'azienda e la figura richiesta, i dati del cliente come li
        stampa la lettera, lo stato (bozza, in_firma, attivo, concluso, annullato), la
        lettera di incarico con numero, stato e `tipo` («Lettera di incarico» o «Lettera
        di incarico occasionale»), e in `quadro` il contratto quadro del freelance dello
        stesso tipo della lettera. Ogni documento porta `pdf_url`, mai i byte. Il match e
        i documenti portano `situazione`, `prossima_azione` e `altre_azioni`, e quale
        strumento fa ogni azione lo dice `list_matches`. Solo lettura."""
        key = UUID(match_id)

        def call(session: Session) -> dict[str, Any]:
            # A soft-deleted freelancer's match reads as «match not found», the answer
            # the admin API gives (REB-417), rather than `for_freelancer` refusing with
            # the freelancer's own message.
            freelancer_id = require_live_match(session, key).freelancer_id
            service = reading(session)
            body = service.get(key).model_dump(mode="json")
            page = service.for_freelancer(freelancer_id)
            # The framework agreement of the letter's own regime (REB-687).
            quadro = (
                page.quadro_occasionale if body["lettera"]["regime"] == OCCASIONALE else page.quadro
            )
            body["quadro"] = quadro.model_dump(mode="json") if quadro is not None else None
            return body

        body = _call(call)
        _document_links(body["lettera"])
        if body["quadro"] is not None:
            _document_links(body["quadro"])
        return body
```

`preview_match` takes `regime: Regime | None = None` after `condizioni`, passes it (`proposal = _proposal(service, freelancer, company, cliente, condizioni, regime=regime)`) and its docstring becomes:

```python
        """Cosa farebbe `create_match` con questi dati, senza scrivere nulla: le frasi del
        passo 3 di «Crea match». `riepilogo` è la lettera di incarico in tre o quattro
        frasi, `cosa_succede` quale documento parte per primo, `quadro_necessario` se
        serve un nuovo contratto quadro, `dati_fiscali_mancanti` se mancano i dati
        fiscali del freelance (si salvano con `set_freelancer_tax_data`), `regime` il tipo
        di lettera. `regime` è `partita_iva` (lettera di incarico, per chi lavora con
        partita IVA) o `occasionale` (lettera di incarico occasionale, prestazione
        occasionale); omesso, quello che propone l'hub: occasionale se i dati fiscali sono
        salvati senza partita IVA. Una lettera con partita IVA senza partita IVA salvata è
        rifiutata. Si parte dalla proposta dell'hub: il cliente come l'ha chiamato l'ultimo
        match della stessa azienda (altrimenti solo il nome dell'azienda), la lettera dalla
        richiesta, dalla scheda e dalle condizioni di rebase. `cliente` e `condizioni` la
        cambiano campo per campo, con i nomi dell'API: per il cliente
        `cliente_ragione_sociale`, `cliente_piva`, `cliente_sede`; per le condizioni i campi
        della lettera, come `ruolo`, `attivita`, `data_inizio` e `data_fine` (AAAA-MM-GG),
        `impegno`, `luogo`, `compenso` (IVA esclusa nella lettera con partita IVA, lordo in
        quella occasionale; anche all'italiana: «1.500», «1.234,50»), `unita`,
        `giorni_pagamento`, `fine_mese`. Un campo omesso resta come lo propone l'hub,
        `null` lo svuota. Risponde anche `cliente` e `condizioni` come verrebbero scritti.
        Un campo sconosciuto, o obbligatorio e rimasto vuoto, è rifiutato con il suo nome.
        Solo lettura."""
```

`create_match` takes `regime: Regime | None = None` last, passes it (`proposal = _proposal(service, freelancer, company, cliente, condizioni, key, regime)`), and its docstring's second sentence becomes «la lettera di incarico con il suo numero e, se il freelance non ha un contratto quadro attivo né uno già in firma dello stesso tipo della lettera, un contratto quadro nuovo di quel tipo.»; after «`cliente` e `condizioni` come in `preview_match`,» it reads «e così `regime`,».

- [ ] **Step 4: Run the tests and see them pass**

Run, one at a time:
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_write_tools.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_match_tools.py -q`
`timeout 900 uv run pytest projects/hub/apps/mcp/tests/test_tools.py -q`
`timeout 900 uv run pytest projects/hub/apps/api/tests/test_matches_api.py -q`
Expected: PASS. Then ruff and mypy as in Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/mcp/src/rebase_mcp/server.py \
  projects/hub/apps/mcp/tests/test_match_write_tools.py \
  projects/hub/apps/api/tests/test_matches_api.py
git commit -F - <<'MSG'
feat(hub): the match tools take the letter type

preview_match and create_match take an optional regime and otherwise use
the hub's proposal, get_match attaches the framework agreement of the
letter's own regime, and list_matches links the occasional one too. The
docstrings say that «IVA esclusa» holds for the ordinary letter. An API
test walks an occasional match end to end.

REB-687.
MSG
git log -1 --format=%B
```

### Task 10: The web asks the letter type and shows each document's type

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts:773-870` (types), `:1074-1090` (`FreelancerContracts`), `:1158-1187` (`MemberContract`, `MemberContracts`), `:1241-1267` (`MatchCreate`, `MatchCheck`, `MatchPrefill`)
- Modify: `projects/hub/apps/web/src/lib/contracts.ts` (the tax draft, `fiscalLine`, `fiscalToSave`, `LETTERA_LABELS.compenso`, `whatOf`, new `objectOf`, `quadroHeadingId`, `REGIME_OPTIONS`, `compensoHint`, `previewLabels`)
- Modify: `projects/hub/apps/web/src/pages/admin/contratti/Fiscal.tsx` (`FiscalFields`)
- Modify: `projects/hub/apps/web/src/pages/admin/crea-match/ChiStep.tsx`, `CondizioniStep.tsx`, `ControllaStep.tsx`, `projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`
- Modify: `projects/hub/apps/web/src/pages/admin/contratti/Cards.tsx` (`FrameworkCard`, `MatchCard`), `projects/hub/apps/web/src/pages/admin/Contratti.tsx`
- Modify: `projects/hub/apps/web/src/pages/member/Contratti.tsx`
- Test: `projects/hub/apps/web/src/lib/contracts.test.ts`, `pages/admin/contratti/Fiscal.test.tsx`, `pages/admin/CreaMatch.test.tsx`, `pages/admin/Contratti.test.tsx`, `pages/member/Contratti.test.tsx`

**Interfaces:**
- Consumes: the API as Tasks 2, 5, 6 and 8 left it: `FiscalRead.partita_iva` may be `null`; every document read carries `regime` and `tipo`; `MatchCreate`, `MatchPrefill` and `MatchCheck` carry `regime`; `FreelancerContracts` and `MemberContracts` carry `quadro_occasionale`; a 422 on `regime` carries the refusal sentence.
- Produces, in `lib/api.ts`: `export type Regime = 'partita_iva' | 'occasionale'`; `FiscalData.partita_iva: string | null`; `ContractDocument.regime: Regime`, `ContractDocument.tipo: string`; `MemberContract.regime`, `MemberContract.tipo`; `FreelancerContracts.quadro_occasionale: ContractDocument | null`; `MemberContracts.quadro_occasionale: MemberContract | null`; `MatchCreate.regime`, `MatchCheck.regime`, `MatchPrefill.regime: Regime`.
- Produces, in `lib/contracts.ts`: `FiscalTextKey = keyof FiscalData`; `FiscalDraft = Record<FiscalTextKey, string> & { senza_partita_iva: boolean }`; `REGIME_OPTIONS: readonly { regime: Regime; label: string }[]`; `compensoHint(regime: Regime): string`; `previewLabels(regime: Regime): { lettera: string; quadro: string }`; `whatOf(document: Pick<ContractDocument, 'kind' | 'numero' | 'regime'>): string`; `objectOf(same): string`; `quadroHeadingId(regime: Regime): string`.

- [ ] **Step 1: Write the failing tests**

First the label renames the spec asks for, in the tests that already name them:

```bash
perl -pi -e "s/'Domicilio professionale'/'Domicilio'/g" \
  projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.test.tsx \
  projects/hub/apps/web/src/pages/admin/contratti/Fiscal.test.tsx
perl -pi -e 's/Compenso, IVA esclusa \(€\)/Compenso (€)/g' \
  projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx
```

In `projects/hub/apps/web/src/lib/contracts.test.ts`, the tax drafts gain the box (each draft literal below is the existing one with `senza_partita_iva: false` added):

```ts
  it('saves tax data only when they were missing, or opened and changed', () => {
    const saved = { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Milano', pec: null }
    const draft = {
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Milano',
      pec: '',
      senza_partita_iva: false,
    }
    expect(fiscalLine(saved)).toBe('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')
    expect(fiscalToSave(saved, draft, false)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicilio: ' Milano ' }, true)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicilio: 'Torino' }, true)).toEqual({ ...saved, domicilio: 'Torino' })
    expect(fiscalToSave(null, { ...FISCAL_EMPTY, codice_fiscale: 'X' }, true)).toEqual({
      codice_fiscale: 'X',
      partita_iva: '',
      domicilio: '',
      pec: null,
    })
  })

  it('refills tax fields from a newer record, except the ones typed in', () => {
    const before = {
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Milano',
      pec: '',
      senza_partita_iva: false,
    }
    const after = { ...before, domicilio: 'Bari' }
    expect(typedFiscalFields(before, after)).toEqual(['domicilio'])
    const newer = {
      freelancer_id: 'f1',
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567899',
      domicilio: 'Torino',
      pec: 'ada@pec.it',
      updated_by: 'a1',
      updated_at: '2026-09-25T09:00:00Z',
    }
    expect(refillFiscal(after, newer, new Set(['domicilio'] as const))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567899',
      domicilio: 'Bari',
      pec: 'ada@pec.it',
      senza_partita_iva: false,
    })
    expect(refillFiscal(after, newer, new Set())).toMatchObject({ domicilio: 'Torino' })
  })
```

and in `keeps typed only the fields changed after the save was sent`, the draft becomes `{ codice_fiscale: 'LVLDAA85T50H501Z ', partita_iva: '01234567890', domicilio: 'Bari centro', pec: '', senza_partita_iva: false }`. Add `compensoHint`, `draftFromFiscal`, `objectOf`, `previewLabels`, `quadroHeadingId`, `REGIME_OPTIONS` and `toFiscalData` to the import, and append:

```ts
describe('tax data without a partita IVA (REB-687)', () => {
  const record = {
    freelancer_id: 'f1',
    codice_fiscale: 'LVLDAA85T50H501Z',
    partita_iva: null,
    domicilio: 'Milano',
    pec: null,
    updated_by: 'a1',
    updated_at: '2026-10-12T09:00:00Z',
  }

  it('reads a saved NULL as the ticked box, and sends the ticked box as NULL', () => {
    const draft = draftFromFiscal(record)
    expect(draft).toMatchObject({ partita_iva: '', senza_partita_iva: true })
    expect(toFiscalData({ ...draft, partita_iva: '0123' })).toMatchObject({ partita_iva: null })
    expect(toFiscalData({ ...draft, senza_partita_iva: false, partita_iva: ' 01234567890 ' })).toMatchObject({
      partita_iva: '01234567890',
    })
  })

  it('says «senza partita IVA» on the saved line', () => {
    expect(fiscalLine(record)).toBe('Salvati: CF LVLDAA85T50H501Z · senza partita IVA')
  })

  it('saves nothing again for a NULL shown as the ticked box, and treats an empty field as NULL', () => {
    const draft = draftFromFiscal(record)
    expect(fiscalToSave(record, draft, true)).toBeNull()
    expect(fiscalToSave(record, { ...draft, senza_partita_iva: false }, true)).toBeNull()
    expect(fiscalToSave(record, { ...draft, senza_partita_iva: false, partita_iva: '01234567890' }, true)).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567890',
      domicilio: 'Milano',
      pec: null,
    })
  })

  it('counts the box as typed only while the save carried another partita IVA', () => {
    const sent = { codice_fiscale: 'X', partita_iva: null, domicilio: 'Bari', pec: null }
    const draft = { codice_fiscale: 'X', partita_iva: '', domicilio: 'Bari', pec: '', senza_partita_iva: true }
    const typed = new Set(['senza_partita_iva'] as const)
    expect(typedAfterSave(draft, typed, sent)).toEqual(new Set())
    expect(typedAfterSave({ ...draft, senza_partita_iva: false }, typed, sent)).toEqual(new Set(['senza_partita_iva']))
  })
})

describe('the letter type (REB-687)', () => {
  it('offers the two types in the order step 1 shows them', () => {
    expect(REGIME_OPTIONS).toEqual([
      { regime: 'partita_iva', label: 'Incarico con partita IVA' },
      { regime: 'occasionale', label: 'Prestazione occasionale' },
    ])
  })

  it('hints the fee as net of VAT or gross, by type', () => {
    expect(compensoHint('partita_iva')).toBe('IVA esclusa')
    expect(compensoHint('occasionale')).toBe('lordo, senza IVA')
  })

  it('names the previews by type', () => {
    expect(previewLabels('partita_iva')).toEqual({ lettera: 'Apri la lettera (PDF)', quadro: 'Apri il contratto quadro (PDF)' })
    expect(previewLabels('occasionale')).toEqual({
      lettera: 'Apri la lettera occasionale (PDF)',
      quadro: 'Apri il contratto quadro per prestazioni occasionali (PDF)',
    })
  })

  it('names each framework agreement by its type, as complement and as object', () => {
    const occasionale = { kind: 'quadro', numero: null, regime: 'occasionale' } as const
    const ordinario = { kind: 'quadro', numero: null, regime: 'partita_iva' } as const
    const lettera = { kind: 'lettera', numero: '2026-001', regime: 'occasionale' } as const
    expect(whatOf(occasionale)).toBe('del contratto quadro per prestazioni occasionali')
    expect(whatOf(ordinario)).toBe('del contratto quadro')
    expect(objectOf(occasionale)).toBe('il contratto quadro per prestazioni occasionali')
    expect(objectOf(ordinario)).toBe('il contratto quadro')
    expect(objectOf(lettera)).toBe('la lettera n. 2026-001')
    expect(quadroHeadingId('partita_iva')).toBe('contratti-quadro')
    expect(quadroHeadingId('occasionale')).toBe('contratti-quadro-occasionale')
  })
})
```

In `projects/hub/apps/web/src/pages/admin/contratti/Fiscal.test.tsx`, append inside the `describe`:

```tsx
  it('ticks «Senza partita IVA» for a record saved without one, and shows no partita IVA field (REB-687)', async () => {
    mount({ ...SAVED, partita_iva: null })
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · senza partita IVA')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Dati fiscali'))
    expect(screen.getByRole('checkbox', { name: 'Senza partita IVA (prestazione occasionale)' })).toBeChecked()
    expect(screen.queryByLabelText('Partita IVA')).toBeNull()
  })

  it('saves the ticked box as no partita IVA, and the unticked box asks for one again (REB-687)', async () => {
    const sent: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      sent.push(JSON.parse(String(init!.body)))
      const record = { ...SAVED, partita_iva: null, updated_at: '2026-09-26T10:00:00Z' }
      return new Response(JSON.stringify(record), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    mount(SAVED)
    await userEvent.click(screen.getByText('Dati fiscali'))
    const box = screen.getByRole('checkbox', { name: 'Senza partita IVA (prestazione occasionale)' })
    expect(box).not.toBeChecked()
    expect(screen.getByLabelText('Partita IVA')).toBeRequired()
    await userEvent.click(box)
    expect(screen.queryByLabelText('Partita IVA')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Salva i dati fiscali' }))
    expect(await screen.findByText('Dati fiscali salvati.')).toBeInTheDocument()
    expect(sent).toEqual([
      { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: null, domicilio: 'Via Roma 1, Milano', pec: null },
    ])
    expect(box).toBeChecked()
    await userEvent.click(box)
    expect(screen.getByLabelText('Partita IVA')).toHaveValue('')
    expect(screen.getByLabelText('Partita IVA')).toBeRequired()
  })
```

In `projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx`, import `type Fiscal` and `type Regime` beside `LETTERA_TEXT_KEYS` from `@/lib/api`; `prefill` takes and answers the type:

```tsx
function prefill({
  compenso = '450.00' as string | null,
  cliente = ROSSI_CLIENTE as Record<string, string | null>,
  fiscale = FISCALE as Fiscal | null,
  regime = 'partita_iva' as Regime,
} = {}) {
  return {
    fiscale,
    cliente,
    regime,
```

(the rest of `prefill` stays), `CHECK` gains `regime: 'partita_iva',` after `dati_fiscali_mancanti: false,`, and append:

```tsx
describe('«Tipo di lettera» (REB-687)', () => {
  const SENZA = { ...FISCALE, partita_iva: null }
  const REFUSAL =
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" o salva la partita IVA.'

  it('proposes «Prestazione occasionale» for tax data saved without a partita IVA, and the rest follows it', async () => {
    const spy = routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({ fiscale: SENZA, regime: 'occasionale' }),
      'POST /api/hub/freelancers/f1/matches/check': { ...CHECK, regime: 'occasionale' },
    })
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByText('Salvati: CF LVLDAA85T50H501Z · senza partita IVA')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Prestazione occasionale' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Incarico con partita IVA' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Compenso (€)')).toHaveAccessibleDescription(/lordo, senza IVA/)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('link', { name: 'Apri la lettera occasionale (PDF)' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Apri il contratto quadro per prestazioni occasionali (PDF)' })).toBeInTheDocument()
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.regime).toBe('occasionale')
    expect(spy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('hints «IVA esclusa» under the fee of the ordinary letter, and sends the type picked', async () => {
    const spy = routes()
    mount()
    await pick(/Rossi Studio/)
    expect(screen.getByRole('radio', { name: 'Incarico con partita IVA' })).toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: 'Prestazione occasionale' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Compenso (€)')).toHaveAccessibleDescription(/lordo, senza IVA/)
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'Incarico con partita IVA' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByLabelText('Compenso (€)')).toHaveAccessibleDescription(/IVA esclusa/)
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByRole('button', { name: 'Salva senza inviare' })
    const [body] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(body.regime).toBe('partita_iva')
  })

  it('saves nothing again when tax data without a partita IVA are opened and left as they were', async () => {
    const spy = routes({
      'GET /api/hub/freelancers/f1/matches/prefill?company_id=c1': prefill({ fiscale: SENZA, regime: 'occasionale' }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    expect(screen.getByRole('checkbox', { name: 'Senza partita IVA (prestazione occasionale)' })).toBeChecked()
    expect(screen.queryByLabelText('Partita IVA')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByLabelText('Ruolo')
    expect(spy.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('goes back to «Chi e per chi» with the sentence when the check refuses an ordinary letter without a partita IVA', async () => {
    const spy = routes({
      'PUT /api/hub/freelancers/f1/fiscal': { ...SENZA, updated_at: '2026-09-24T10:00:00Z' },
      'POST /api/hub/freelancers/f1/matches/check': answer(422, { detail: [{ loc: ['body', 'regime'], msg: REFUSAL }] }),
    })
    mount()
    await pick(/Rossi Studio/)
    await userEvent.click(screen.getByRole('button', { name: 'Modifica i dati fiscali' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Senza partita IVA (prestazione occasionale)' }))
    expect(screen.getByRole('radio', { name: 'Incarico con partita IVA' })).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Avanti' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(REFUSAL)
    current('1. Chi e per chi')
    expect(screen.getByRole('radio', { name: 'Prestazione occasionale' })).toBeInTheDocument()
    const [saved] = bodies(spy, 'PUT', '/api/hub/freelancers/f1/fiscal')
    expect(saved.partita_iva).toBeNull()
    const [checked] = bodies(spy, 'POST', '/api/hub/freelancers/f1/matches/check')
    expect(checked.regime).toBe('partita_iva')
  })
})
```

In `projects/hub/apps/web/src/pages/admin/Contratti.test.tsx`, the fixtures carry the type: `QUADRO` gains `regime: 'partita_iva',` and `tipo: 'Contratto quadro',` after `kind: 'quadro',`; `LETTERA` gains `tipo: 'Lettera di incarico',` after `kind: 'lettera',`; `PAGE` becomes `{ freelancer_id: 'f1', quadro: QUADRO, quadro_occasionale: null, quadri: [QUADRO], matches: [MATCH], fiscale: FISCALE }`; the empty page of `says, when there is none, ...` gains `quadro_occasionale: null`. Append:

```tsx
describe('one framework agreement per regime (REB-687)', () => {
  const OCCASIONALE = { ...OUT, id: 'd9', regime: 'occasionale', tipo: 'Contratto quadro per prestazioni occasionali' }
  const LETTERA_OCCASIONALE = {
    ...LETTERA,
    id: 'd8',
    numero: '2026-002',
    regime: 'occasionale',
    tipo: 'Lettera di incarico occasionale',
  }
  const MATCH_OCCASIONALE = { ...MATCH, id: 'm2', nome_azienda: 'Verdi Snc', lettera: LETTERA_OCCASIONALE }
  const BOTH = {
    ...PAGE,
    quadro_occasionale: OCCASIONALE,
    quadri: [OCCASIONALE, QUADRO],
    matches: [MATCH_OCCASIONALE, MATCH],
  }

  it('shows a card for each, named by its type, each with its own actions', async () => {
    const gone = { ...OCCASIONALE, stato: 'annullato', prossima_azione: null, altre_azioni: [] }
    const spy = routeFetch({
      'GET /api/hub/freelancers/f1': PERSON,
      'GET /api/hub/freelancers/f1/matches': BOTH,
      'POST /api/hub/contract-documents/d9/cancel': gone,
    })
    mount('/admin/freelance/f1/contracts')
    const ordinary = await screen.findByRole('region', { name: 'Contratto quadro' })
    const occasional = screen.getByRole('region', { name: 'Contratto quadro per prestazioni occasionali' })
    expect(within(ordinary).getByText(QUADRO.situazione)).toBeInTheDocument()
    expect(
      within(occasional).getByRole('link', { name: 'PDF del contratto quadro per prestazioni occasionali' }),
    ).toHaveAttribute('href', '/api/hub/contract-documents/d9/pdf')
    expect(
      within(occasional).getByRole('button', { name: 'Reinvia email del contratto quadro per prestazioni occasionali' }),
    ).toBeInTheDocument()
    await openMore(occasional, 'Altre azioni del contratto quadro per prestazioni occasionali')
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'Annulla il contratto quadro per prestazioni occasionali' }),
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Sì, annulla il contratto quadro' }))
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('/api/hub/contract-documents/d9/cancel', expect.objectContaining({ method: 'POST' })),
    )
    expect(spy).not.toHaveBeenCalledWith('/api/hub/contract-documents/d1/cancel', expect.anything())
  })

  it('names each match’s letter by its type', async () => {
    routeFetch({ 'GET /api/hub/freelancers/f1': PERSON, 'GET /api/hub/freelancers/f1/matches': BOTH })
    mount('/admin/freelance/f1/contracts')
    const occasional = await screen.findByRole('article', { name: 'Verdi Snc · Backend developer' })
    expect(within(occasional).getByText('Lettera di incarico occasionale n. 2026-002')).toBeInTheDocument()
    expect(within(await matchCard()).getByText('Lettera di incarico n. 2026-001')).toBeInTheDocument()
  })
})
```

In `projects/hub/apps/web/src/pages/member/Contratti.test.tsx`, `QUADRO` gains `regime: 'partita_iva',` and `tipo: 'Contratto quadro',` after `kind: 'quadro',`, `LETTERA` gains `tipo: 'Lettera di incarico',` after `kind: 'lettera',`, and append inside the `describe`:

```tsx
  it('shows the occasional framework agreement beside the ordinary one, each with its own link (REB-687)', async () => {
    mount({
      quadro: {
        ...QUADRO,
        stato: 'firmato',
        signing_url: null,
        ha_pdf_firmato: true,
        attivo: true,
        signed_at: '2026-10-01T09:00:00Z',
        rinnovo: '2027-10-01',
        ultimo_giorno_disdetta: '2027-09-01',
      },
      quadro_occasionale: {
        ...QUADRO,
        id: 'd5',
        regime: 'occasionale',
        tipo: 'Contratto quadro per prestazioni occasionali',
        signing_url: 'https://firma.letsrebase.com/sign/occ',
      },
      quadri_precedenti: [],
      lettere: [{ ...LETTERA, regime: 'occasionale', tipo: 'Lettera di incarico occasionale' }],
    })
    expect(await screen.findByText('Contratto quadro per prestazioni occasionali')).toBeInTheDocument()
    expect(screen.getByText('Contratto quadro')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Firma il contratto quadro per prestazioni occasionali' })).toHaveAttribute(
      'href',
      'https://firma.letsrebase.com/sign/occ',
    )
    expect(screen.queryByRole('link', { name: 'Firma il contratto quadro' })).toBeNull()
    expect(screen.getByText('Lettera di incarico occasionale n. 2026-001')).toBeInTheDocument()
    expect(screen.queryByText('Contratti quadro precedenti')).toBeNull()
  })
```

- [ ] **Step 2: Run them and see them fail**

Run: `pnpm --filter hub exec vitest run src/lib/contracts.test.ts src/pages/admin/contratti/Fiscal.test.tsx src/pages/admin/CreaMatch.test.tsx src/pages/admin/Contratti.test.tsx src/pages/member/Contratti.test.tsx`
Expected: FAIL: the missing exports (`compensoHint`, `objectOf`, ...), no «Domicilio» or «Compenso (€)» label, no checkbox, no radios, one framework card only.

- [ ] **Step 3: The types**

In `projects/hub/apps/web/src/lib/api.ts`, after `PigroStato`:

```ts
/** REB-687: a contract document's family, the letter type a match is made with. */
export type Regime = 'partita_iva' | 'occasionale'
```

`FiscalData`:

```ts
/** A freelancer's tax data, as the contracts print them. `partita_iva` is `null` for a
 *  member who works without one, paid as a prestazione occasionale (REB-687). */
export interface FiscalData {
  codice_fiscale: string
  partita_iva: string | null
  domicilio: string
  pec: string | null
}
```

`ContractDocument` and `MemberContract`, after `kind`:

```ts
  /** Its family, and its type as the pages name it (REB-687): «Contratto quadro per
   *  prestazioni occasionali», «Lettera di incarico occasionale». */
  regime: Regime
  tipo: string
```

`FreelancerContracts`, after `quadro`: `quadro_occasionale: ContractDocument | null`; `MemberContracts`, after `quadro`: `quadro_occasionale: MemberContract | null`; `MatchCreate`, after `company_id`: `regime: Regime`; `MatchCheck`, last: `regime: Regime`; `MatchPrefill`, after `lettera`: `regime: Regime`. Each new field's comment: «REB-687: the letter type, `partita_iva` or `occasionale`.».

- [ ] **Step 4: The helpers**

In `projects/hub/apps/web/src/lib/contracts.ts`, import `type Regime` from `./api`, and replace the tax draft's helpers:

```ts
/** The tax form as typed: every field a string, and the box «Senza partita IVA
 *  (prestazione occasionale)» (REB-687), ticked exactly when the partita IVA is saved as
 *  `null`. */
export type FiscalTextKey = keyof FiscalData
export type FiscalDraft = Record<FiscalTextKey, string> & { senza_partita_iva: boolean }

export const FISCAL_EMPTY: FiscalDraft = {
  codice_fiscale: '',
  partita_iva: '',
  domicilio: '',
  pec: '',
  senza_partita_iva: false,
}

export function draftFromFiscal(fiscal: Fiscal | null): FiscalDraft {
  if (fiscal === null) return FISCAL_EMPTY
  return {
    codice_fiscale: fiscal.codice_fiscale,
    partita_iva: fiscal.partita_iva ?? '',
    domicilio: fiscal.domicilio,
    pec: fiscal.pec ?? '',
    senza_partita_iva: fiscal.partita_iva === null,
  }
}
```

```ts
/** A field as a save carries it: the box is whether the partita IVA went as `null`. */
function savedValue(data: FiscalData, key: FiscalKey): string | boolean | null {
  return key === 'senza_partita_iva' ? data.partita_iva === null : data[key]
}

/** The typed fields a save did not cover, because their text changed after it was sent
 *  (compared as sent, trimmed): once the save succeeds only these stay typed, and the
 *  others take the record that comes back. */
export function typedAfterSave(draft: FiscalDraft, typed: ReadonlySet<FiscalKey>, sent: FiscalData): Set<FiscalKey> {
  const now = toFiscalData(draft)
  return new Set([...typed].filter((key) => savedValue(now, key) !== savedValue(sent, key)))
}

/** An empty PEC is `null`, not `""`, which the API would try to read as an address; the
 *  ticked box is no partita IVA, `null` (REB-687). */
export function toFiscalData(draft: FiscalDraft): FiscalData {
  return {
    codice_fiscale: draft.codice_fiscale.trim(),
    partita_iva: draft.senza_partita_iva ? null : draft.partita_iva.trim(),
    domicilio: draft.domicilio.trim(),
    pec: draft.pec.trim() || null,
  }
}

/** Saved tax data in one line: «Chi e per chi» shows it instead of their fields, and
 *  «Dati fiscali» on «Match e contratti» in its closed summary. */
export function fiscalLine(fiscal: FiscalData): string {
  const piva = fiscal.partita_iva ? `P.IVA ${fiscal.partita_iva}` : 'senza partita IVA'
  return `Salvati: CF ${fiscal.codice_fiscale} · ${piva}`
}

/** What «Avanti» on «Chi e per chi» saves: the fields when nothing was saved yet, or
 *  when the admin opened the saved ones and changed them; `null` otherwise, so data only
 *  shown are never written again under this admin's name. `null` and an empty string
 *  are the same value (REB-687): a partita IVA saved as `null` and shown as the ticked
 *  box is not saved again. */
export function fiscalToSave(saved: FiscalData | null, draft: FiscalDraft, editing: boolean): FiscalData | null {
  const data = toFiscalData(draft)
  if (saved === null) return data
  if (!editing) return null
  const same = (Object.keys(data) as (keyof FiscalData)[]).every((key) => (saved[key] || null) === (data[key] || null))
  return same ? null : data
}
```

`LETTERA_LABELS.compenso` becomes `'Compenso (€)'`. Replace `whatOf` and `QUADRO_HEADING_ID`'s block with:

```ts
type Named = Pick<ContractDocument, 'kind' | 'numero' | 'regime'>

/** How a document is named in a button's label or a confirmation, the one place both
 *  the admin's «Match e contratti» (REB-407) and the member area's «Contratti»
 *  (REB-392) name a document: «del contratto quadro», «del contratto quadro per
 *  prestazioni occasionali» (REB-687), «della lettera n. 2026-001». Takes just the
 *  fields a label needs, so a `MemberContract` names a document the same way a
 *  `ContractDocument` does. */
export function whatOf(document: Named): string {
  if (document.kind === 'lettera') return `della lettera n. ${document.numero}`
  return document.regime === 'occasionale' ? 'del contratto quadro per prestazioni occasionali' : 'del contratto quadro'
}

/** The same document as the object of a verb: «Firma il contratto quadro», «Annulla il
 *  contratto quadro per prestazioni occasionali», «Firma la lettera n. 2026-001». */
export function objectOf(document: Named): string {
  if (document.kind === 'lettera') return `la lettera n. ${document.numero}`
  return document.regime === 'occasionale' ? 'il contratto quadro per prestazioni occasionali' : 'il contratto quadro'
}
```

```ts
/** The headings «Match e contratti» moves the focus to when an action took away the
 *  control that started it: the card's, or the section's when the card went too. A
 *  framework agreement's card has one per regime (REB-687). */
export const QUADRO_HEADING_ID = 'contratti-quadro'
export const MATCHES_HEADING_ID = 'contratti-match'
export function quadroHeadingId(regime: Regime): string {
  return regime === 'occasionale' ? `${QUADRO_HEADING_ID}-occasionale` : QUADRO_HEADING_ID
}
```

and add, after `withPayMode`:

```ts
/** «Tipo di lettera» on «Chi e per chi» (REB-687), in the order the page shows them. */
export const REGIME_OPTIONS: readonly { regime: Regime; label: string }[] = [
  { regime: 'partita_iva', label: 'Incarico con partita IVA' },
  { regime: 'occasionale', label: 'Prestazione occasionale' },
]

/** The hint under «Compenso (€)»: an ordinary fee is net of VAT, an occasional one gross
 *  and without VAT. */
export function compensoHint(regime: Regime): string {
  return regime === 'occasionale' ? 'lordo, senza IVA' : 'IVA esclusa'
}

/** The preview links of «Controlla e invia», named by the letter type. */
export function previewLabels(regime: Regime): { lettera: string; quadro: string } {
  return regime === 'occasionale'
    ? { lettera: 'Apri la lettera occasionale (PDF)', quadro: 'Apri il contratto quadro per prestazioni occasionali (PDF)' }
    : { lettera: 'Apri la lettera (PDF)', quadro: 'Apri il contratto quadro (PDF)' }
}
```

- [ ] **Step 5: The tax form and «Crea match»**

In `projects/hub/apps/web/src/pages/admin/contratti/Fiscal.tsx`, import `Checkbox` from `@rebase/ui/checkbox` and `type FiscalTextKey` from `@/lib/contracts`, and `FiscalFields` becomes:

```tsx
/** The tax fields, shared by «Match e contratti» and «Chi e per chi» of «Crea match».
 *  «Senza partita IVA (prestazione occasionale)» (REB-687) clears and hides the partita
 *  IVA; unticked, the field is required as before. */
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
  const field = (name: FiscalTextKey, label: string, props: ComponentProps<typeof Input> = {}) => (
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
      {!draft.senza_partita_iva &&
        field('partita_iva', 'Partita IVA', { required: true, maxLength: 40, inputMode: 'numeric' })}
      {field('domicilio', 'Domicilio', { required: true, maxLength: 300 })}
      {field('pec', 'PEC, se ce l’ha', { type: 'email', maxLength: 320 })}
      <div className="flex items-center gap-2 sm:col-span-2">
        <Checkbox
          id={`${idPrefix}-senza_partita_iva`}
          checked={draft.senza_partita_iva}
          onCheckedChange={(checked) => onChange({ ...draft, senza_partita_iva: checked === true, partita_iva: '' })}
        />
        <Label htmlFor={`${idPrefix}-senza_partita_iva`} className="font-normal">
          Senza partita IVA (prestazione occasionale)
        </Label>
      </div>
    </div>
  )
}
```

In `projects/hub/apps/web/src/pages/admin/crea-match/ChiStep.tsx`, import `type Regime` from `@/lib/api` and `REGIME_OPTIONS` from `@/lib/contracts`, add:

```tsx
/** «Tipo di lettera» (REB-687), under the tax data: the letter's type drives which
 *  framework agreement the check looks for, step 3's sentences and the PDFs. */
function TipoLettera({ regime, onChange }: { regime: Regime; onChange: (regime: Regime) => void }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Tipo di lettera</legend>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {REGIME_OPTIONS.map((option) => (
          <Label key={option.regime} className="font-normal">
            <input
              type="radio"
              name="match-regime"
              value={option.regime}
              checked={regime === option.regime}
              onChange={() => onChange(option.regime)}
              className="size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            {option.label}
          </Label>
        ))}
      </div>
    </fieldset>
  )
}
```

give `ChiStep` the props `regime: Regime` and `onRegime: (regime: Regime) => void` (after `onEditFiscal`, in the destructuring and in the props type), and render it last in the loaded block:

```tsx
          <FiscaleSection
            nome={nome}
            saved={savedFiscal}
            draft={fiscal}
            onChange={onFiscal}
            editing={editFiscal}
            onEdit={onEditFiscal}
            failure={failure}
          />
          <TipoLettera regime={regime} onChange={onRegime} />
```

In `projects/hub/apps/web/src/pages/admin/crea-match/CondizioniStep.tsx`, import `type Regime` from `@/lib/api` and `compensoHint` from `@/lib/contracts`; `Pagamento` and `CondizioniStep` take `regime: Regime` (`CondizioniStep` passes `regime={regime}` to `<Pagamento ... />`), and the fee block in `Pagamento` becomes:

```tsx
      <div className="space-y-1.5">
        <Label htmlFor="lettera-compenso">{LETTERA_LABELS.compenso}</Label>
        <div className="flex items-center gap-2">
          <Input
            id="lettera-compenso"
            inputMode="decimal"
            required
            className="max-w-40"
            value={form.compenso}
            onChange={(event) => onChange({ ...form, compenso: event.target.value })}
            aria-invalid={invalid('compenso')}
            aria-describedby="lettera-compenso-per lettera-compenso-iva"
          />
          <span id="lettera-compenso-per" className="text-sm text-muted-foreground">
            {per}
          </span>
        </div>
        <p id="lettera-compenso-iva" className="text-sm text-muted-foreground">
          {compensoHint(regime)}
        </p>
      </div>
```

In `projects/hub/apps/web/src/pages/admin/crea-match/ControllaStep.tsx`, import `type Regime` from `@/lib/api` and `previewLabels` from `@/lib/contracts`; `ControllaStep` takes `regime: Regime`, reads `const labels = previewLabels(regime)` at its top, and the two links read `{labels.lettera}` and `{labels.quadro}` in place of «Apri la lettera (PDF)» and «Apri il contratto quadro (PDF)».

In `projects/hub/apps/web/src/pages/admin/CreaMatch.tsx`, import `type Regime` from `@/lib/api`, and:

```tsx
// A request closed after it was picked: `create` and the check refuse it by this name.
const REQUEST_FIELD = 'company_id'
// «Tipo di lettera» (REB-687): an ordinary letter without a partita IVA is refused on it.
const REGIME_FIELD = 'regime'
const onStepOne = (field: string) =>
  field === REQUEST_FIELD || field === REGIME_FIELD || CLIENTE_FIELDS.has(field) || FISCAL_FIELDS.has(field)
```

the state, beside `cliente`:

```tsx
  const [regime, setRegime] = useState<Regime>('partita_iva')
```

`payload` sends it:

```tsx
  const payload = (): MatchCreate | null =>
    company
      ? {
          id: matchId,
          company_id: company.id,
          regime,
          cliente: toCliente(cliente),
          lettera: letteraToSend(lettera),
          giorni_previsti: giorniPrevistiToSend(giorni),
        }
      : null
```

`loadPrefill`'s `onSuccess` takes the proposal, after `setPrefillFor(companyId)`:

```tsx
      setRegime(data.regime)
```

and the three steps get it: `<ChiStep ... regime={regime} onRegime={setRegime} />`, `<CondizioniStep ... regime={regime} />`, `<ControllaStep ... regime={regime} />`.

- [ ] **Step 6: One framework card per regime, and each document's type**

In `projects/hub/apps/web/src/pages/admin/contratti/Cards.tsx`, import `quadroHeadingId` (and drop `QUADRO_HEADING_ID`) from `@/lib/contracts`; `FrameworkCard` becomes:

```tsx
/** A framework agreement's card, one per regime (REB-687), titled by its type: where it
 *  stands in one sentence and what to do next; the text's version, read rarely, in a
 *  closed «Dettagli». Its own errors show under it. With none at all, the ordinary card
 *  says the first match sent brings one. */
export function FrameworkCard({
  quadro,
  handles,
  busy,
  error,
}: {
  quadro: ContractDocument | null
  handles: ActionHandles
  busy: boolean
  error: string | null
}) {
  const headingId = quadroHeadingId(quadro?.regime ?? 'partita_iva')
  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <Card>
        <CardHeader>
          <CardTitle>
            {/* Focusable by the page alone, to land on when an action took its control away. */}
            <h2 id={headingId} tabIndex={-1}>
              {quadro?.tipo ?? 'Contratto quadro'}
            </h2>
          </CardTitle>
          {quadro && (
            <CardAction>
              <Badge variant="pill">{DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</Badge>
            </CardAction>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {quadro === null ? (
            <p className="text-muted-foreground">Nessun contratto quadro: parte con il primo match inviato.</p>
          ) : (
            <>
              <p>{quadro.situazione}</p>
              <NextSteps
                next={quadro.prossima_azione}
                others={quadro.altre_azioni}
                handles={handles}
                busy={busy}
                moreLabel={`Altre azioni ${whatOf(quadro)}`}
              >
                <DocumentLinks document={quadro} />
              </NextSteps>
              <details className="text-muted-foreground">
                <summary className="cursor-pointer text-xs">Dettagli</summary>
                <dl className="mt-3 text-sm">
                  <Row label="Versione del testo">
                    <span className="flex flex-wrap items-center gap-2">
                      <span>{quadro.text_version}</span>
                      {quadro.nuova_versione && <Badge variant="pill">Nuova versione disponibile</Badge>}
                    </span>
                  </Row>
                </dl>
              </details>
            </>
          )}
        </CardContent>
      </Card>
      <Failure message={error} />
    </section>
  )
}
```

and in `MatchCard`, right after `<p>{match.situazione}</p>`:

```tsx
          <p className="text-sm text-muted-foreground">{`${match.lettera.tipo} n. ${match.lettera.numero}`}</p>
```

In `projects/hub/apps/web/src/pages/admin/Contratti.tsx`, import `type Regime` from `@/lib/api`, and from `@/lib/contracts` `objectOf` and `quadroHeadingId` (dropping `QUADRO_HEADING_ID`). The sections:

```tsx
/** A framework agreement's actions belong to its regime's card (REB-687), a match's to
 *  the matches. */
type Section = Regime | 'match'
const sectionHeading = (section: Section) => (section === 'match' ? MATCHES_HEADING_ID : quadroHeadingId(section))
```

`starting` lands on `[card, sectionHeading(section)]`. The question on a framework agreement remembers which one:

```tsx
  const [confirmingQuadro, setConfirmingQuadro] = useState<{
    action: 'annulla' | 'disdetta'
    document: ContractDocument
  } | null>(null)
```

`signing` takes `(document: ContractDocument, section: Section, card: string)`, and:

```tsx
  const quadroHandles = (document: ContractDocument): ActionHandles => ({
    ...signing(document, document.regime, quadroHeadingId(document.regime)),
    annulla: {
      label: `Annulla ${objectOf(document)}`,
      pendingLabel: `Annullo ${objectOf(document)}`,
      run: from(() => setConfirmingQuadro({ action: 'annulla', document })),
      pending: runningFor(cancelQuadro, document.id),
    },
    registra_disdetta: {
      label: `${ACTION_LABELS.registra_disdetta} ${whatOf(document)}`,
      pendingLabel: `Registro la disdetta ${whatOf(document)}`,
      run: from(() => setConfirmingQuadro({ action: 'disdetta', document })),
      pending: runningFor(recordNotice, document.id),
    },
  })
```

Delete `const quadro = data.quadro` and render the cards:

```tsx
  const frameworks = [data.quadro, data.quadro_occasionale].filter(
    (document): document is ContractDocument => document != null,
  )
```

```tsx
        {frameworks.length === 0 ? (
          <FrameworkCard quadro={null} handles={{}} busy={busy} error={null} />
        ) : (
          frameworks.map((document) => (
            <FrameworkCard
              key={document.id}
              quadro={document}
              handles={quadroHandles(document)}
              busy={busy}
              error={actionFailure?.section === document.regime ? actionFailure.message : null}
            />
          ))
        )}
```

The two framework questions act on the document they were asked about:

```tsx
      <Confirm
        open={confirmingQuadro?.action === 'annulla'}
        title="Annullare il contratto quadro?"
        description="Se è già partito, viene annullato anche sul sito di firma e il link ricevuto dal freelance smette di funzionare. Le lettere che lo aspettano restano in attesa: «Invia per la firma» sul loro match ne genera uno nuovo."
        confirm={cancelQuadro.isPending ? 'Annullo…' : 'Sì, annulla il contratto quadro'}
        pending={cancelQuadro.isPending}
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (!confirmingQuadro) return
          const { document } = confirmingQuadro
          cancelQuadro.mutate(document.id, {
            ...starting(document.regime, quadroHeadingId(document.regime)),
            onSettled: () => setConfirmingQuadro(null),
          })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
      <Confirm
        open={confirmingQuadro?.action === 'disdetta'}
        title="Registrare la disdetta?"
        description="Da oggi il contratto quadro non è più attivo, e il prossimo match ne genera uno nuovo. Si registra quando il freelance o rebase ha dato disdetta, o uno dei due ha receduto."
        confirm={recordNotice.isPending ? 'Registro…' : 'Sì, registra la disdetta'}
        pending={recordNotice.isPending}
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (!confirmingQuadro) return
          const { document } = confirmingQuadro
          recordNotice.mutate(document.id, {
            ...starting(document.regime, quadroHeadingId(document.regime)),
            onSettled: () => setConfirmingQuadro(null),
          })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
```

In `projects/hub/apps/web/src/pages/member/Contratti.tsx`, import `objectOf` beside `whatOf`; in `Actions`, `const what = objectOf(document)` replaces the inline ternary (and its comment says «`objectOf` wants its direct object»); `Framework`'s title is `<p className="font-medium">{quadro.tipo}</p>`; the letter's title is `<p className="font-medium">{`${lettera.tipo} n. ${lettera.numero}`}</p>`; and in `MemberContratti`:

```tsx
  const signed = data
    ? [data.quadro, data.quadro_occasionale, ...data.quadri_precedenti, ...data.lettere].some(
        (document) => document?.ha_pdf_firmato,
      )
    : false
```

```tsx
      ) : data.quadro === null && !data.quadro_occasionale && data.lettere.length === 0 ? (
```

```tsx
          {data.quadro && <Framework quadro={data.quadro} />}
          {data.quadro_occasionale && <Framework quadro={data.quadro_occasionale} />}
```

- [ ] **Step 7: Run the tests and see them pass**

Run: `pnpm --filter hub exec vitest run src/lib/contracts.test.ts src/pages/admin/contratti/Fiscal.test.tsx src/pages/admin/CreaMatch.test.tsx src/pages/admin/Contratti.test.tsx src/pages/member/Contratti.test.tsx`
Expected: PASS.
Then `pnpm --filter hub test`, `pnpm --filter hub lint` and `pnpm --filter hub build` (the build's `tsc --noEmit` checks the tests' new literals against the new types). Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add projects/hub/apps/web/src/lib/api.ts \
  projects/hub/apps/web/src/lib/contracts.ts \
  projects/hub/apps/web/src/lib/contracts.test.ts \
  projects/hub/apps/web/src/pages/admin/contratti/Fiscal.tsx \
  projects/hub/apps/web/src/pages/admin/contratti/Fiscal.test.tsx \
  projects/hub/apps/web/src/pages/admin/crea-match/ChiStep.tsx \
  projects/hub/apps/web/src/pages/admin/crea-match/CondizioniStep.tsx \
  projects/hub/apps/web/src/pages/admin/crea-match/ControllaStep.tsx \
  projects/hub/apps/web/src/pages/admin/CreaMatch.tsx \
  projects/hub/apps/web/src/pages/admin/CreaMatch.test.tsx \
  projects/hub/apps/web/src/pages/admin/contratti/Cards.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.tsx \
  projects/hub/apps/web/src/pages/admin/Contratti.test.tsx \
  projects/hub/apps/web/src/pages/member/Contratti.tsx \
  projects/hub/apps/web/src/pages/member/Contratti.test.tsx
git commit -F - <<'MSG'
feat(hub): «Crea match» asks the letter type

The tax form gets «Senza partita IVA (prestazione occasionale)», ticked
exactly when the partita IVA is saved as NULL, and the address is just
«Domicilio». Step 1 asks «Tipo di lettera», preselected from the
prefill, and it drives the check, the fee's hint under «Compenso (€)»
and the preview links. «Match e contratti», admin and member side, shows
one framework card per regime and names every document by its type.

REB-687.
MSG
git log -1 --format=%B
```

### Task 11: The contracts documentation and the decision

**Files:**
- Modify: `projects/hub/AGENTS.md` (the MCP tool list at `:59-73`, «The contracts are typeset at request time» at `:125-136`, the signer note at `:622-623`)
- Modify: `projects/hub/content/README.md` (the build commands and the paragraph after them, `:66-84`)
- Modify: `docs/design/DECISIONS.md` (repository root: one new row at the end)

**Interfaces:**
- Consumes: everything above, as built.
- Produces: documentation only.

- [ ] **Step 1: `projects/hub/AGENTS.md`**

In the MCP tool list, three lines become:

```
- `preview_match`: step 3's sentences for the hub's proposal with the given fields and letter type (`regime`, the proposal's when left out); writes nothing.
- `create_match`: the draft match, «Salva senza inviare»; `match_id` makes a retry idempotent; `regime` as in `preview_match`.
- `set_freelancer_tax_data`: saves the tax data, the partita IVA optional; answers that they are saved, never the values.
```

In «The contracts are typeset at request time», the words «typesets both texts from fiction» become «typesets every text from fiction», and after that paragraph add:

```
Since REB-687 there are two families of the same two documents, named by (kind, regime)
in `rebase_core.regimes`: `contratto-quadro.md` and `lettera-di-incarico.md` for a member
who works with a partita IVA, `contratto-quadro-occasionale.md` and
`lettera-di-incarico-occasionale.md` for a member paid as a prestazione occasionale.
Every `contract_documents` row carries its `regime`, and a match's regime is its letter's:
«Tipo di lettera» on step 1 of «Crea match», proposed from the tax data, whose partita IVA
may be missing. Every lookup of a freelancer's framework agreement takes a regime, so a
freelancer may hold one of each, a signature releases only the letters of its own
regime, and «Match e contratti» shows one framework card per regime. An ordinary letter
needs a saved partita IVA; the occasional texts never print one, and an occasional
letter's payment term counts from the receipt. Design record:
`docs/superpowers/specs/2026-10-12-prestazione-occasionale-design.md`.
```

and in the signer note, «With both texts `status: final`» becomes «With every text `status: final`».

- [ ] **Step 2: `projects/hub/content/README.md`**

The first build command's comment `# both, blank` becomes `# every text, blank`; «`texts/rebase.json`, package data beside the two texts,» becomes «`texts/rebase.json`, package data beside the texts,»; and «`incarico.esempio.json` is fiction and names every field the letter asks for» becomes «`incarico.esempio.json` is fiction and names every field either letter asks for». After that paragraph add:

```
Since REB-687 the package holds four texts: the two above and their occasional twins for
a member who works without a partita IVA. `contratto-quadro-occasionale.md` is the
ordinary framework agreement with the Collaboratore in place of the Professionista, the
ricevuta in place of the fattura and the new article 5.7 on the 5.000 euro threshold;
`lettera-di-incarico-occasionale.md` names its collaborator by the codice fiscale, and the
build counts its payment term from the receipt. Both are `status: final` at version 1.0.
The points left for the accountant before the first occasional letter leaves are in the
design record's «Outside the code».
```

- [ ] **Step 3: `docs/design/DECISIONS.md`**

Append one row at the end of the table (newest last; no earlier row changes):

```
| 2026-10-12 | A member works without a partita IVA, and the hub's tax data and both contract texts assumed one (REB-687). Ivan chose among one standalone document, a letter with another text, or a framework agreement plus a letter. How does a member without a partita IVA sign a match? | A framework agreement for occasional work plus a «lettera di incarico occasionale», the letter type chosen when the match is made and proposed from the tax data, both texts written from the ordinary pair (`projects/hub/docs/superpowers/specs/2026-10-12-prestazione-occasionale-design.md`). | Every contract document carries a regime, `partita_iva` or `occasionale`, and its kind and regime name its text; every lookup of a freelancer's framework agreement takes a regime, so the two families never stand in for each other. The partita IVA is optional in the tax data; an ordinary letter needs one, and an occasional text never prints one. |
```

- [ ] **Step 4: Check the wording**

Run: `LC_ALL=C grep -n $'\xe2\x80\x94' projects/hub/AGENTS.md projects/hub/content/README.md docs/design/DECISIONS.md`
Expected: no line added by this task (compare with `git diff`).

- [ ] **Step 5: Commit**

```bash
git add projects/hub/AGENTS.md projects/hub/content/README.md docs/design/DECISIONS.md
git commit -F - <<'MSG'
docs(hub): the occasional contracts in AGENTS.md and the decisions

I describe the two families of contracts where the hub documents its
texts and its MCP tools, note the four texts in the contracts README,
and add the decision row: every document carries a regime, every
framework lookup takes one, and the partita IVA is optional.

REB-687.
MSG
git log -1 --format=%B
```
