"""The `rebase` actor: the hub acting inside a freelancer's space through the
engagements door (spec 2026-09-25 § 2.5, REB-490).

Milestone A opens a service-token door through which the hub sets up a freelancer's
space, customer and deal for a signed engagement. Every write it makes through that
door needs an actor to record in the timeline, and it must be an actor that can write
and administer without tripping the agent ban in `AGENT_FORBIDDEN_ACTIONS`
(`actor.py`): `rebase` is not an agent working on someone's behalf, it is the hub
itself, so `require_write`/`require_admin` must treat it exactly as they treat a human
admin.
"""

from pigrocrm.core.actor import Actor


def test_rebase_actor_is_an_admin_that_is_not_an_agent() -> None:
    actor = Actor.rebase()
    assert actor.type == "rebase"
    assert actor.can_write and actor.can_administer
    actor.require_write("creare un cliente")  # no AgentForbidden: not an mcp actor
