"""The shape `list_talents` answers an agent in (REB-801).

The service reads one `TalentRead` per person, about a kilobyte of it with the email,
the LinkedIn address, the UTM source and twenty skills; an agent matching a request
reads a hundred of them to rank, and 156 rows were 162 KB, which is what truncated one
of them. So the tool answers a short row unless asked for more, and the choice is the
caller's, field by field (`fields`).

Kept apart from `server.py` and free of any session: it turns the rows the service
already read into the rows the tool sends, so a test can pin the shape without a
database.
"""

from collections.abc import Sequence
from typing import Any

import pydantic_core
from mcp.types import CallToolResult, TextContent

from rebase_core.errors import ValidationFailed
from rebase_core.talent_schemas import TalentRead

# The page size an agent gets without asking, and the most it can ask for. The service
# itself still pages 500 at a time for the campaign audiences (`talents.LIST_LIMIT_MAX`);
# a model reading a list is the limit here, not the database.
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200

# The default row: who the person is and what the anonymous card says they do, with no
# address, no UTM and no dates. A row has the keys it has a value for: a lead is just
# `id`, `nome`, `last_name` and `status`. `seniority_uncertain` joins it when true (below).
SHORT_FIELDS = (
    "id",
    "nome",
    "last_name",
    "status",
    "role",
    "seniority",
    "experience_years",
    "daily_rate",
    "location",
    "skills",
    "profile_summary",
)
# «Top skills»: the card lists up to twenty, the first ones are what the person leads
# with, and the summary (up to 400 characters) is cut where a sentence of it is already
# enough to tell whether to look closer. `campi=["competenze", "sintesi"]` asks for them
# whole.
SHORT_SKILLS = 5
SHORT_PROFILE_SUMMARY = 160
ALL = "tutti"
ROW_FIELDS: tuple[str, ...] = tuple(TalentRead.model_fields)


def row_fields(fields: Sequence[str] | None) -> tuple[str, ...] | None:
    """The fields a caller asked for, `None` for «the short row», `ROW_FIELDS` for
    `tutti`. `id` always comes along, since it is what `get_talent` takes. An unknown
    name is refused with the names that exist, never silently dropped: a misspelt field
    would otherwise read as «nobody has it»."""
    if fields is None:
        return None
    names = tuple(dict.fromkeys(name.strip() for name in fields if name.strip()))
    if not names:
        return None
    unknown = [name for name in names if name not in ROW_FIELDS and name != ALL]
    if unknown:
        raise ValidationFailed(
            "talents",
            "fields",
            f"«{unknown[0]}» non esiste: {', '.join(ROW_FIELDS)}, oppure «{ALL}»",
        )
    if ALL in names:
        return ROW_FIELDS
    return tuple(dict.fromkeys(("id", *names)))


def talent_row(row: dict[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    """One row of the list as it is sent. With `fields` exactly those keys, whole;
    without, the short row: `SHORT_FIELDS`, the keys with no value left out, the skills
    cut to `SHORT_SKILLS` and the summary to `SHORT_PROFILE_SUMMARY` characters."""
    if fields is not None:
        return {name: row[name] for name in fields}
    short = {name: row[name] for name in SHORT_FIELDS if row[name] not in (None, [])}
    # A seniority the card's dates could not back (REB-799) says so, only then: `experience_years`
    # is usually left out with it (null), and a flag false on nearly every row would be
    # noise.
    if row["seniority_uncertain"]:
        short["seniority_uncertain"] = True
    # Who this person may also be (REB-800), only when somebody: by id, so the agent
    # compares the two with `get_talent` and an admin merges with `merge_freelancers`.
    if row["duplicate_possible_of"]:
        short["duplicate_possible_of"] = row["duplicate_possible_of"]
    if "skills" in short:
        short["skills"] = short["skills"][:SHORT_SKILLS]
    if "profile_summary" in short:
        short["profile_summary"] = _cut(short["profile_summary"], SHORT_PROFILE_SUMMARY)
    return short


def _cut(text: str, length: int) -> str:
    """`text` as it is when it fits, else cut at the last word that does and closed with
    «…»."""
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def talents_page(page: dict[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    """A dumped `TalentList` with its rows reshaped (`fields` from `row_fields`);
    `total`, `by_status` and `next_cursor` are the service's, untouched."""
    return {**page, "items": [talent_row(row, fields) for row in page["items"]]}


def compact_result(page: dict[str, Any]) -> CallToolResult:
    """The page as the one thing an agent reads: compact JSON text, no structured copy.
    The server's own convention (a returned dict) sends the page twice, once as a
    structured block and once as text indented by two spaces, and the indentation alone
    is a quarter of a list of short rows; for the one tool whose answer can be hundreds
    of rows that is the difference between a list that fits a prompt and one that does
    not (REB-801)."""
    text = pydantic_core.to_json(page).decode()
    return CallToolResult(content=[TextContent(type="text", text=text)])
