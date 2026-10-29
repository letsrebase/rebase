"""What PostHog's warehouse reads, asserted against the schema that feeds it.

On 2026-09-21 PostHog paused a sync with «something this sync depends on no longer
exists». It was ours: the identity merge renamed `member_logins` to `logins` (REB-281,
migration 0012). Nothing refused anybody -- a `GRANT` travels with a rename -- so the
suite was green, the deploy was green, and the only thing that noticed was an email from
PostHog nine days later.

This file is the thing that notices. It is the same shape as the website's
`path-map-plugin.test.ts`, which reads `deploy/nginx.conf` and compares it with the copy
the dev server uses: two copies of one fact, and a test that fails when they disagree.
Here the fact is the list of tables a machine outside this repository selects from, the
copies are `WAREHOUSE_TABLES` below and the runbook's own sentence, and the schema is the
third party to both.

It reads a file outside this project, which nothing else in `packages/core` does. The
import rule this repository enforces is about imports, and there is no module to import:
the list lives in prose because its reader is a console nobody here can call.
"""

from pathlib import Path

import pytest

from rebase_core.db import Base
from rebase_core.models import Freelancer  # noqa: F401  (registers every model on Base)

# The hub tables `posthog_ro` may select, as `docs/adding-a-project.md` § 7 lists them.
# Adding one here is not enough to sync it: the grant and the schema refresh are steps on
# the server and in PostHog, and the runbook has them in order.
WAREHOUSE_TABLES = frozenset(
    {"signups", "freelancers", "companies", "guide_downloads", "logins", "notes"}
)

# Binary columns inside a synced table, each one a decision somebody took on purpose.
# A PostHog Postgres source selects every column of a table unless a person unchecks it,
# so a new `LargeBinary` here means the bytes leave for a third party the day the sync
# next runs.
#
# `freelancers.cv_bytes` is in this set because it is what production holds today, not
# because the question is settled: the model's own docstring says the CV lives in the row
# so that there is one place to delete from, and a copy in a warehouse is a second one.
# Whoever settles it moves this line, in either direction, with the DECISIONS row that
# settled it.
BINARY_COLUMNS_DECIDED = frozenset({("freelancers", "cv_bytes")})

REPO_ROOT = Path(__file__).resolve().parents[5]
RUNBOOK = REPO_ROOT / "docs" / "adding-a-project.md"


def _runbook_tables() -> set[str]:
    """The names the runbook's own sentence lists for the hub's database."""
    text = RUNBOOK.read_text(encoding="utf-8")
    start = text.index("PostHog's data warehouse reads the hub's production database")
    sentence = text[start : text.index(")", start)]
    _, listed = sentence.split("55435:", 1)
    return {name.strip(" `\n") for name in listed.split(",")}


def test_every_table_the_warehouse_reads_is_a_table_this_schema_still_declares() -> None:
    """The one this file exists for. A rename or a drop fails here, in the suite that
    runs on the pull request, instead of in an email after the next scheduled sync."""
    missing = sorted(name for name in WAREHOUSE_TABLES if name not in Base.metadata.tables)
    assert missing == [], (
        f"{missing} is read by PostHog and no longer exists in the schema. A migration "
        "that renames or drops a synced table has to be followed by a schema refresh and "
        "a re-pointed sync in PostHog (docs/adding-a-project.md § 7), and the old rows "
        "over there deleted; the grant follows the table on its own and hides the break."
    )


def test_the_runbook_lists_exactly_those_tables() -> None:
    """The copy a person reads and the copy the suite reads say the same thing, or this
    file is describing a warehouse that does not exist."""
    assert _runbook_tables() == set(WAREHOUSE_TABLES)


@pytest.mark.parametrize("table_name", sorted(WAREHOUSE_TABLES))
def test_a_synced_table_grows_no_binary_column_nobody_decided_about(table_name: str) -> None:
    """A column added to a synced table is synced too, and a `LargeBinary` one carries a
    file out of this database. The failure names the pair so the decision is taken before
    the bytes leave, not after somebody reads the storage bill."""
    table = Base.metadata.tables[table_name]
    binaries = {
        (table_name, column.name)
        for column in table.columns
        if column.type.__class__.__name__ in {"LargeBinary", "BLOB"}
    }
    undecided = sorted(pair for pair in binaries if pair not in BINARY_COLUMNS_DECIDED)
    assert undecided == [], (
        f"{undecided} would leave for PostHog with the next sync. Uncheck the column in "
        "the source's «Columns» picker, and add the pair to BINARY_COLUMNS_DECIDED with "
        "the DECISIONS row that says why it may stay."
    )
