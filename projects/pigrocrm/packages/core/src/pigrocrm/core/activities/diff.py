"""Builds the `changed`/`before`/`after` shape shared by every configuration audit.

Configuration is not entity data. `OrganizationService.update` records only
`{"changed": sorted(changes)}` -- the *values* of a customer are the row itself, and
copying them into the timeline would make `activities` a slow, unbounded duplicate of
`organizations` full of the same personal data. Configuration is the opposite case: the
whole question an audit of a field definition, a pipeline stage or a user has to
answer six months later is "who turned this off, and what was it before?", and a bare
list of touched key names cannot answer it. The values here are small, bounded and
administrative (a label, a role, a boolean, a position), so recording both sides costs
nothing and is the entire point.

`changed` is the *real* delta, not the set of keys the caller happened to send. A
patch that sets `required` to the value it already had is not a change, and recording
it as one would fill an administrator's timeline with events that never happened --
the same reasoning behind `OrganizationService.restore` only recording "restored" when the
row really was deleted. Callers use the empty result to skip recording entirely.
"""

from collections.abc import Mapping
from typing import Any


def field_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """`{}` when nothing actually changed, else `{"changed": [...], "before": {...},
    "after": {...}}` restricted to the keys whose value really differs.

    Only keys present in `after` are considered: `after` is the set of attributes the
    caller is touching, and a key absent from it was not part of this operation at
    all. `before.get(key)` rather than `before[key]` so a key that did not exist
    before -- a value being set for the first time -- reads as `None` -> value instead
    of raising inside an audit path, which must never be able to fail the operation it
    is recording.
    """
    changed = sorted(key for key, value in after.items() if before.get(key) != value)
    if not changed:
        return {}
    return {
        "changed": changed,
        "before": {key: before.get(key) for key in changed},
        "after": {key: after[key] for key in changed},
    }
