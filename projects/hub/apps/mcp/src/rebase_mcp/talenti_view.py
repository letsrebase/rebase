"""The shape `list_talenti` answers an agent in (REB-801).

The service reads one `TalentoRead` per person, about a kilobyte of it with the email,
the LinkedIn address, the UTM source and twenty skills; an agent matching a request
reads a hundred of them to rank, and 156 rows were 162 KB, which is what truncated one
of them. So the tool answers a short row unless asked for more, and the choice is the
caller's, field by field (`campi`).

Kept apart from `server.py` and free of any session: it turns the rows the service
already read into the rows the tool sends, so a test can pin the shape without a
database.
"""

from collections.abc import Sequence
from typing import Any

import pydantic_core
from mcp.types import CallToolResult, TextContent

from rebase_core.errors import ValidationFailed
from rebase_core.schemas import TalentoRead

# The page size an agent gets without asking, and the most it can ask for. The service
# itself still pages 500 at a time for the campaign audiences (`talenti.LIST_LIMIT_MAX`);
# a model reading a list is the limit here, not the database.
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200

# The default row: who the person is and what the anonymous card says they do, with no
# address, no UTM and no dates. A row has the keys it has a value for: a lead is just
# `id`, `nome`, `cognome` and `stato`.
SHORT_FIELDS = (
    "id",
    "nome",
    "cognome",
    "stato",
    "ruolo",
    "seniority",
    "anni",
    "tariffa_giornaliera",
    "luogo",
    "competenze",
    "sintesi",
)
# «Top skills»: the card lists up to twenty, the first ones are what the person leads
# with, and the summary (up to 400 characters) is cut where a sentence of it is already
# enough to tell whether to look closer. `campi=["competenze", "sintesi"]` asks for them
# whole.
SHORT_SKILLS = 5
SHORT_SINTESI = 160
ALL = "tutti"
ROW_FIELDS: tuple[str, ...] = tuple(TalentoRead.model_fields)


def row_fields(campi: Sequence[str] | None) -> tuple[str, ...] | None:
    """The fields a caller asked for, `None` for «the short row», `ROW_FIELDS` for
    `tutti`. `id` always comes along, since it is what `get_talento` takes. An unknown
    name is refused with the names that exist, never silently dropped: a misspelt field
    would otherwise read as «nobody has it»."""
    if campi is None:
        return None
    names = tuple(dict.fromkeys(name.strip() for name in campi if name.strip()))
    if not names:
        return None
    unknown = [name for name in names if name not in ROW_FIELDS and name != ALL]
    if unknown:
        raise ValidationFailed(
            "talenti",
            "campi",
            f"«{unknown[0]}» non esiste: {', '.join(ROW_FIELDS)}, oppure «{ALL}»",
        )
    if ALL in names:
        return ROW_FIELDS
    return tuple(dict.fromkeys(("id", *names)))


def talento_row(row: dict[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    """One row of the list as it is sent. With `fields` exactly those keys, whole;
    without, the short row: `SHORT_FIELDS`, the keys with no value left out, the skills
    cut to `SHORT_SKILLS` and the summary to `SHORT_SINTESI` characters."""
    if fields is not None:
        return {name: row[name] for name in fields}
    short = {name: row[name] for name in SHORT_FIELDS if row[name] not in (None, [])}
    if "competenze" in short:
        short["competenze"] = short["competenze"][:SHORT_SKILLS]
    if "sintesi" in short:
        short["sintesi"] = _cut(short["sintesi"], SHORT_SINTESI)
    return short


def _cut(text: str, length: int) -> str:
    """`text` as it is when it fits, else cut at the last word that does and closed with
    «…»."""
    if len(text) <= length:
        return text
    return text[:length].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def talenti_page(page: dict[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    """A dumped `TalentoList` with its rows reshaped (`fields` from `row_fields`);
    `totale`, `per_stato` and `next_cursor` are the service's, untouched."""
    return {**page, "items": [talento_row(row, fields) for row in page["items"]]}


def compact_result(page: dict[str, Any]) -> CallToolResult:
    """The page as the one thing an agent reads: compact JSON text, no structured copy.
    The server's own convention (a returned dict) sends the page twice, once as a
    structured block and once as text indented by two spaces, and the indentation alone
    is a quarter of a list of short rows; for the one tool whose answer can be hundreds
    of rows that is the difference between a list that fits a prompt and one that does
    not (REB-801)."""
    text = pydantic_core.to_json(page).decode()
    return CallToolResult(content=[TextContent(type="text", text=text)])
