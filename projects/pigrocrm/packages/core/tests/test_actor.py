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

import pytest

from pigrocrm.core.actor import Actor


def test_rebase_actor_is_an_admin_that_is_not_an_agent() -> None:
    actor = Actor.rebase()
    assert actor.type == "rebase"
    assert actor.can_write and actor.can_administer
    actor.require_write("creare un cliente")  # no AgentForbidden: not an mcp actor


def test_an_unscoped_admin_passes_the_space_level_gate_and_a_scoped_one_does_not() -> None:
    from uuid import uuid4

    from pigrocrm.core.errors import PermissionDenied, ScopedAdmin

    Actor(id=uuid4(), type="user", role="admin").require_unscoped_admin("update_space_settings")
    Actor.system().require_unscoped_admin("seed_pipeline")
    with pytest.raises(ScopedAdmin) as scoped:
        Actor(id=uuid4(), type="user", role="admin", aziende=(uuid4(),)).require_unscoped_admin(
            "update_space_settings"
        )
    assert scoped.value.code == "permission_denied"
    # A scoped admin with no azienda left is still scoped, and still an admin elsewhere.
    empty = Actor(id=uuid4(), type="user", role="admin", aziende=())
    assert empty.scoped is True and empty.can_administer is True
    with pytest.raises(ScopedAdmin):
        empty.require_unscoped_admin("invite_user")
    # The role comes first: a collaboratore is told about the role, not the scope.
    with pytest.raises(PermissionDenied):
        Actor(id=uuid4(), type="user", role="collaboratore", aziende=None).require_unscoped_admin(
            "invite_user"
        )
