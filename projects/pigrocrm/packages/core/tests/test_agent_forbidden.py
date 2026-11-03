"""The operations closed to agents, closed on the credential and not on the tool.

Found by review, and it is the one finding that changed the project's threat model.

`apps/mcp/tests/test_mcp_invoice_ban.py` guarantees these operations are unreachable
*structurally* — the MCP tool is not registered — and argues, correctly, that a role
check would guarantee nothing, because a personal access token inherits its owner's full
role and an administrator's token passes any role check.

The gap was that the credential is not confined to the transport. `PatService.resolve`
answers with `Actor(type="mcp", role=<owner's role>)`, and `apps/api`'s `get_actor`
accepts the same `Bearer pgc_…` header on **every** REST route; nothing in that package
ever read `actor.type`. A token given to an agent on the written promise that it "may
prepare but may not emit" could issue an invoice with one `curl` — spending a register
number and producing a FatturaPA, with no way back.

These tests are on `Actor` rather than on a route on purpose. The defect was that the
rule lived in one adapter's tool registry; putting the fix in `packages/core` is what
makes it true of every caller, including one nobody has written yet.
"""

import pytest

from pigrocrm.core.actor import Actor
from pigrocrm.core.agent_actions import (
    AGENT_ALLOWED_ACTIONS,
    FULL_ACCESS_ACTIONS,
    PERSON_ONLY_ACTIONS,
)
from pigrocrm.core.errors import AgentForbidden, PermissionDenied

AGENT_ADMIN = Actor(id=None, type="mcp", role="admin")
# The same credential on an installation that has opted in. Same type, same role --
# the only difference is what `Settings.mcp_full_access` said when `PatService.resolve`
# built it, which is the whole design: the capability rides on the credential.
AGENT_APERTO = Actor(id=None, type="mcp", role="admin", full_access=True)
AGENT_READONLY = Actor(id=None, type="mcp", role="readonly")
HUMAN_ADMIN = Actor(id=None, type="user", role="admin")
SYSTEM = Actor.system()


@pytest.mark.parametrize("action", sorted(FULL_ACCESS_ACTIONS))
def test_an_admin_agent_is_refused_every_forbidden_action(action: str) -> None:
    """The whole point: *admin* is the role a PAT most often carries, because a PAT
    carries whatever its owner has and the person wiring an agent up is usually the
    owner of the install. A check that only stopped a `readonly` agent would stop
    nobody who matters."""
    with pytest.raises(AgentForbidden) as caught:
        AGENT_ADMIN.require_admin(action)
    assert caught.value.details["action"] == action

    with pytest.raises(AgentForbidden):
        AGENT_ADMIN.require_write(action)


@pytest.mark.parametrize("action", sorted(FULL_ACCESS_ACTIONS))
def test_a_human_admin_is_refused_none_of_them(action: str) -> None:
    """The other half, and the one that keeps this from being a way to break the
    product: a person with the right role may still do every one of them. If this passed
    while the product had stopped working, the test above would be satisfied by a
    service that refuses everybody."""
    HUMAN_ADMIN.require_admin(action)
    HUMAN_ADMIN.require_write(action)


def test_the_refusal_names_the_credential_and_not_the_role() -> None:
    """A `readonly` agent gets `AgentForbidden`, never `PermissionDenied`.

    The distinction is not cosmetic. `PermissionDenied` means "ask somebody senior",
    and acting on that advice here means giving the token a bigger role — which does
    nothing, because the ban does not ask about roles, and leaves an over-privileged
    credential behind. The refusal has to say the operation is closed to agents.
    """
    with pytest.raises(AgentForbidden):
        AGENT_READONLY.require_admin("issue_invoice")


def test_an_agent_keeps_what_the_allowlist_names() -> None:
    """The allowlist mirrors the tool surface, not a second role. An agent recording a
    time entry or creating a customer is the product working as designed."""
    AGENT_ADMIN.require_write("log_time")
    AGENT_ADMIN.require_write("create_invoice")
    AGENT_ADMIN.require_write("confirm_proforma")


def test_the_three_classes_are_disjoint() -> None:
    """One operation, one answer: a key in two sets would make the refusal depend on
    which check happened to run first."""
    assert not AGENT_ALLOWED_ACTIONS & FULL_ACCESS_ACTIONS
    assert not AGENT_ALLOWED_ACTIONS & PERSON_ONLY_ACTIONS
    assert not FULL_ACCESS_ACTIONS & PERSON_ONLY_ACTIONS


@pytest.mark.parametrize("action", sorted(PERSON_ONLY_ACTIONS))
def test_a_person_only_action_is_refused_to_every_agent_even_an_opted_in_one(
    action: str,
) -> None:
    """Creating an administrator, rewriting the space settings (the ceiling included),
    minting a token or sending mail in the owner's name is not something the
    `mcp_full_access` switch hands out (PAPI-01)."""
    for agent in (AGENT_ADMIN, AGENT_APERTO, AGENT_READONLY):
        with pytest.raises(AgentForbidden):
            agent.require_admin(action)
        with pytest.raises(AgentForbidden):
            agent.require_write(action)
    HUMAN_ADMIN.require_admin(action)


def test_an_action_nobody_classified_is_refused_to_an_agent() -> None:
    """The default is closed. The denylist this replaced left every operation nobody
    remembered to name open to a token, which is how REST-only operations with no tool
    stayed reachable."""
    with pytest.raises(AgentForbidden):
        AGENT_ADMIN.require_write("an_operation_added_without_classifying_it")
    with pytest.raises(AgentForbidden):
        AGENT_APERTO.require_admin("an_operation_added_without_classifying_it")
    HUMAN_ADMIN.require_write("an_operation_added_without_classifying_it")


def test_a_readonly_agent_still_fails_the_role_check_on_an_allowed_action() -> None:
    """The agent check runs first but does not replace the role check: an unbanned
    action still asks the ordinary question."""
    with pytest.raises(PermissionDenied):
        AGENT_READONLY.require_write("log_time")


def test_the_system_actor_is_not_an_agent() -> None:
    """Migrations, the CLI and `seed_defaults` run as `Actor.system()`. Sweeping them
    into the ban would make the installer unable to install."""
    SYSTEM.require_admin("issue_invoice")
    SYSTEM.require_write("export_invoice_xml")


@pytest.mark.parametrize("action", sorted(FULL_ACCESS_ACTIONS))
def test_an_opted_in_agent_is_refused_none_of_them(action: str) -> None:
    """The other half of the switch, and the reason it is a switch rather than a wall.

    This product is single-tenant and self-hosted, and the person running it may
    reasonably want their own agent to do everything they can do. `mcp_full_access`
    says so for one installation; the default stays closed so that nobody has to know
    the setting exists in order to be safe.

    Asserted per action rather than on a couple of samples, because a partial opt-in --
    all but one of them opening -- would be the worst of both: the operator believes the
    switch is on and one operation still refuses, in a product where the refusal arrives
    at the moment somebody is trying to issue an invoice.
    """
    AGENT_APERTO.require_admin(action)
    AGENT_APERTO.require_write(action)


def test_opting_in_does_not_hand_out_a_role() -> None:
    """`full_access` answers "which operations", never "which role". A `readonly`
    credential on an opted-in installation still cannot write, and the refusal is the
    ordinary `PermissionDenied` -- because at that point the question really is about the
    role, and answering "ask somebody senior" is now the correct advice."""
    readonly_aperto = Actor(id=None, type="mcp", role="readonly", full_access=True)

    with pytest.raises(PermissionDenied):
        readonly_aperto.require_write("issue_invoice")
    with pytest.raises(PermissionDenied):
        readonly_aperto.require_write("log_time")


def test_the_switch_defaults_to_closed() -> None:
    """An `Actor` built without saying anything about it is closed.

    The default lives on the model rather than only in `Settings`, so a caller that
    constructs an actor by hand -- a test, a CLI, a future adapter -- gets the safe
    answer without having to know the setting exists.
    """
    assert Actor(id=None, type="mcp", role="admin").full_access is False
    assert Actor.system().full_access is False
