"""One module per tool family: each registers its tools on the server, in `register`.

The family modules share their names with the call-through modules one level up
(`families/timetracking.py` registers what `tools/timetracking.py` calls), so the package
`tools` must never bind them as attributes: `from pigrocrm_mcp.tools import timetracking`
is the call-through, and `tools/__init__.py` reads `FAMILIES` instead of importing names.
"""

from pigrocrm_mcp.tools.families import (
    analytics,
    attivita,
    automations,
    aziende,
    contracts,
    customers,
    dashboard,
    deals,
    documents,
    invoices,
    people,
    proposals,
    shared,
    timetracking,
    work_units,
)

# The order is the order `list_tools` answers in.
FAMILIES = (
    customers,
    people,
    deals,
    contracts,
    proposals,
    work_units,
    shared,
    automations,
    dashboard,
    documents,
    invoices,
    aziende,
    timetracking,
    analytics,
    attivita,
)
