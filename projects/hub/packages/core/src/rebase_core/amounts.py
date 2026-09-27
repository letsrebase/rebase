"""An amount as an Italian types it, read once for every adapter (REB-485).

The same grammar as the web app's (`apps/web/src/lib/amount.ts`), and both run the table
in `apps/web/src/lib/amount_cases.json`:

- with a comma, the comma is the decimal and dots may only group the digits before it in
  threes («1.234,50» is 1234.50, «1,5» is 1.5); anything else with a comma is refused,
  English notation included («1,000.00» is not 1.00);
- without one, a dot before exactly three digits is a thousands separator («1.500» is
  1500, «100.000» is 100000), so a number shaped that way must be thousands throughout
  («0.500» and «1234.567» are refused); any other dot is the decimal point («480.50»,
  «1500.00», what the API answers with);
- ASCII digits only, no sign, no exponent, no spaces or other separators inside.

Precision is not the parser's business: the schemas refuse more than two decimals where a
value is stored. The multipart wizard (`rebase_api.routers.freelancers`), the MCP tools and
`MatchService.proposal` read an amount through it, so «1.500» is 1500 whichever door it
came in by.
"""

import re
from decimal import Decimal

# What `trim()` drops on the web: whitespace, NBSP (which `\s` covers) and a BOM (which it
# does not).
_EDGES = re.compile(r"^[\s\ufeff]+|[\s\ufeff]+\Z")
_COMMA = re.compile(r"(?:[0-9]+|[1-9][0-9]{0,2}(?:\.[0-9]{3})+),[0-9]+")
_THOUSANDS = re.compile(r"[1-9][0-9]{0,2}(?:\.[0-9]{3})+")
# A decimal point before anything but exactly three digits.
_MACHINE = re.compile(r"[0-9]+(?:\.(?:[0-9]{1,2}|[0-9]{4,}))?")


class NotAnAmount(ValueError):
    """What was typed is not an amount. Each caller turns it into its own refusal: a 422
    naming the field in the API, a `ToolError` in the MCP server, `non valido` in a
    match's proposal."""


def italian_amount(text: str) -> Decimal:
    """`text` read the Italian way, or `NotAnAmount`. Blank is not an amount either: a
    caller for which blank means "none" checks that first."""
    value = _EDGES.sub("", text)
    if "," in value:
        if not _COMMA.fullmatch(value):
            raise NotAnAmount(text)
        value = value.replace(".", "").replace(",", ".")
    elif _THOUSANDS.fullmatch(value):
        value = value.replace(".", "")
    elif not _MACHINE.fullmatch(value):
        raise NotAnAmount(text)
    return Decimal(value)
