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
        raise ContractFailed(
            f"{key} is {exact}: at most two decimals, which is what the page prints"
        )
    return exact


def checked(data: dict[str, Value]) -> dict[str, Value]:
    """The data, with the fee checked and the payment term written from its two parts."""
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
