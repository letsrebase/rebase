from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.agent_actions import AGENT_ALLOWED_ACTIONS, FULL_ACCESS_ACTIONS
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ScopedAdmin

ActorType = Literal["user", "mcp", "system", "rebase"]
Role = Literal["admin", "collaborator", "readonly"]

WRITE_ROLES: tuple[str, ...] = ("admin", "collaborator")
ADMIN_ROLES: tuple[str, ...] = ("admin",)


class Actor(BaseModel):
    """Who is performing an operation. Always passed explicitly — never inferred
    from global state — so that authorization is testable and the timeline is honest
    about whether a human or an agent made the change."""

    model_config = ConfigDict(frozen=True)

    id: UUID | None
    type: ActorType
    role: Role
    # What this credential may do, carried on the credential itself rather than read
    # from a global at the point of use. `PatService.resolve` sets it from the token's
    # own `full_access` and the space's
    # `Settings.mcp_full_access`; a browser session never sets it because a `user` actor
    # is not subject to the list at all. Keeping it here is what makes a REST request
    # presenting a PAT behave exactly like the MCP transport -- the asymmetry that let a
    # `curl` issue an invoice while the tool was unregistered (commit 086c561).
    full_access: bool = False
    # The legal entities this actor may see and write (REB-633, spec 2026-10-03 §1.11, §4):
    # `None` for an unscoped actor (`users.limited_scope` false, `system`, `rebase`),
    # the tuple of `user_legal_entities` rows, possibly empty, for a scoped one. Carried on the
    # actor so the one place that binds it (`db/scope.py`) needs nothing but the actor;
    # the rows themselves are hidden by Postgres, never by a check at the point of use.
    legal_entities: tuple[UUID, ...] | None = None

    @classmethod
    def system(cls) -> Self:
        return cls(id=None, type="system", role="admin")

    @classmethod
    def rebase(cls) -> Self:
        """rebase acting inside a freelancer's space through the engagements door
        (spec 2026-09-25 § 2.5): an admin for what it writes, never an agent, and named
        «rebase» in the timeline so the freelancer knows who did what."""
        return cls(id=None, type="rebase", role="admin")

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    @property
    def can_administer(self) -> bool:
        return self.role in ADMIN_ROLES

    @property
    def reads_pay(self) -> bool:
        """Whether this actor may read what pay is in (payslips, and the inbox items and
        proposals that carry them, staff design § 4): an administrator, and for an agent
        credential only one with `full_access`. Never a member with the collaborator or
        readonly role."""
        return self.can_administer and (self.type != "mcp" or self.full_access)

    @property
    def scoped(self) -> bool:
        return self.legal_entities is not None

    def _refuse_if_agent(self, action: str) -> None:
        """Checked before the role, deliberately.

        A `readonly` agent asking to issue an invoice should be told the operation is
        closed to agents, not that it needs a better role: the second answer invites
        somebody to hand the token a bigger role, which is exactly the wrong move.

        An agent passes an action in `AGENT_ALLOWED_ACTIONS`, or one in
        `FULL_ACCESS_ACTIONS` when its credential carries `full_access`. Any other action,
        including one nobody classified, is refused (`agent_actions.py`).
        """
        if self.type != "mcp" or action in AGENT_ALLOWED_ACTIONS:
            return
        if self.full_access and action in FULL_ACCESS_ACTIONS:
            return
        raise AgentForbidden(action)

    def require_agent_allowed(self, action: str) -> None:
        """The agent half of the check, on its own, for an operation whose *role* half
        is somebody else's business.

        `require_write`/`require_admin` answer two questions at once, and that is right
        for a service method: the operation both writes and is closed to agents. Reading
        a Drive folder is neither -- it writes nothing, and whether this credential may
        use the connected Drive at all is decided by `GoogleDriveAccountService.usable`,
        which is about the *grant* rather than the role. Calling `require_write` there
        would refuse a `readonly` person a read, which is the wrong answer to the
        question actually being asked.

        So this exists to let `drive_reader_for` apply the ban and nothing else, at the
        one point every reader of Drive has to pass through -- an MCP tool today, a REST
        route tomorrow. Public for exactly that reason: `_refuse_if_agent` is private
        because a caller reaching past `require_*` was, until this, always a mistake.
        """
        self._refuse_if_agent(action)

    def require_person(self, action: str) -> None:
        """Refuses every agent credential, `full_access` or not (REB-806).

        `_refuse_if_agent` has a class of actions an installation may open; this is not
        one. A decision that exists to be taken by a person (accepting or rejecting what
        an agent proposed) must not be takeable by the credential that read the untrusted
        text the proposal came from, so no flag can turn it on. Checked before the role,
        for `_refuse_if_agent`'s reason."""
        if self.type == "mcp":
            raise AgentForbidden(action)

    def require_write(self, action: str) -> None:
        self._refuse_if_agent(action)
        if not self.can_write:
            raise PermissionDenied(action, list(WRITE_ROLES), self.role)

    def require_admin(self, action: str) -> None:
        self._refuse_if_agent(action)
        if not self.can_administer:
            raise PermissionDenied(action, list(ADMIN_ROLES), self.role)

    def require_pay_reader(self, action: str) -> None:
        """`require_admin`, and for an agent credential `full_access` as well: what the action
        touches is pay, which a default token never reads (staff design § 4). `action` is
        classified as an action every token may reach (it is not pay by itself: it is pay
        only for the item or record it is aimed at), so the second half is checked here."""
        self.require_admin(action)
        if self.type == "mcp" and not self.full_access:
            raise AgentForbidden(action)

    def require_unscoped_admin(self, action: str) -> None:
        """`require_admin`, and the whole space in view (spec 2026-10-03 §1.11): the
        configuration a space decides for itself (Team, Spazio, Google, Drive, templates,
        stages, categories, period locks) is an unscoped admin's. A scoped admin keeps
        the role, and the refusal names what is missing rather than the role, so nobody
        is invited to hand them a bigger one."""
        self.require_admin(action)
        if self.scoped:
            raise ScopedAdmin(action)
