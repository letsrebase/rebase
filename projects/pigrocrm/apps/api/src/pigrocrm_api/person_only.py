"""The route-level gate for an operation no service guards: a person's, never a token's.

Almost every operation is guarded where it is performed, in the service, which asks
`actor.require_write(action)` and is closed to an agent credential unless the action is
classified in `core/agent_actions.py`. A few routes have no service behind them (minting
and revoking a personal access token, a browser's push subscription), so nothing there
would ask. `person_only` is that question put on the route, for the same reason and with
the same answer as `Actor.require_person`: `agent_forbidden`, whatever the token carries.
"""

from typing import Annotated

from fastapi import Depends
from fastapi.params import Depends as DependsMarker

from pigrocrm.core.actor import Actor
from pigrocrm_api.deps import get_actor


def person_only(action: str) -> DependsMarker:
    """`dependencies=[person_only("create_token")]` on the route decorator."""

    def gate(actor: Annotated[Actor, Depends(get_actor)]) -> None:
        actor.require_person(action)

    marker: DependsMarker = Depends(gate)
    return marker
