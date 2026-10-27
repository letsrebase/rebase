"""The hints every tool carries, so a client can tell a read from a write before calling it.

Three sets, chosen by what the call does to the space and not by its name:

  * `READ_ONLY`: changes nothing. A client may run it without asking.
  * `ADDITIVE`: writes, but adds a record or moves one through a state that has a way
    back on this surface (archive and restore, open and cancel, confirm and discard).
  * `DESTRUCTIVE`: overwrites or discards what was there, or cannot be taken back from
    here: an `update_*` replaces the old value (it survives only on the timeline), a
    numbered invoice never leaves the register, a discarded proforma is not restored.

`destructiveHint` is set on every tool, `False` on a read, because the MCP default for an
unset one is `True`. `openWorldHint` is left out on purpose: it costs bytes on every tool
of a list an agent reads in full, and no client here acts on it.
`tests/test_mcp_tool_annotations.py` fails for a tool without hints and for a name whose
hints contradict what it does.
"""

from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
ADDITIVE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)
