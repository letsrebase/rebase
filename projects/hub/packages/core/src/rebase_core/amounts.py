"""An amount as an Italian types it, read once for every adapter (REB-485).

The same rule the web app reads its amount fields with (`apps/web/src/lib/amount.ts`):
with a comma, the comma is the decimal and every dot a thousands separator («1.234,50»
is 1234.50); without one, dots that group the digits in threes are thousands too
(«12.000» is 12000, «1.500» is 1500), while a single dot before one or two digits is
already the machine form («480.50»), which is also what the API answers with. The
multipart wizard (`rebase_api.routers.freelancers`) and the MCP tools read a day rate,
a budget or a filter through it, so «1.500» is 1500 whichever door it came in by.
"""

import re
from decimal import Decimal

_THOUSANDS = re.compile(r"[0-9]{1,3}(?:\.[0-9]{3})+")
# The machine form alone: no exponent, no `NaN` or `Infinity`, no underscores and no
# digits outside ASCII, all of which `Decimal()` would accept on its own.
_MACHINE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


class NotAnAmount(ValueError):
    """What was typed is not an amount. Each caller turns it into its own refusal: a 422
    naming the field in the API, a `ToolError` in the MCP server."""


def italian_amount(text: str) -> Decimal:
    """`text` read the Italian way, or `NotAnAmount`. Blank is not an amount either: a
    caller for which blank means "none" checks that first."""
    value = text.strip()
    if "," in value:
        value = value.replace(".", "").replace(",", ".", 1)
    elif _THOUSANDS.fullmatch(value):
        value = value.replace(".", "")
    if not _MACHINE.fullmatch(value):
        raise NotAnAmount(text)
    return Decimal(value)
