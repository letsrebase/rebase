from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.errors import AgentForbidden, PermissionDenied

ActorType = Literal["user", "mcp", "system"]
Role = Literal["admin", "collaboratore", "readonly"]

WRITE_ROLES: tuple[str, ...] = ("admin", "collaboratore")
ADMIN_ROLES: tuple[str, ...] = ("admin",)

# The operations an agent may perform only on an installation that has opted in. Named
# by the string each service already passes to `require_write`/`require_admin`, so the
# rule attaches to the operation itself rather than to a route or a tool registration.
# (It began as slice 3's sixteen and has grown twice since -- the count is deliberately
# not written down here any more, because a number in a comment is the first thing to
# stop being true when a slice adds a line.)
#
# **Why the list exists.** These are not merely privileged: they are irreversible in a
# way the rest of the product is not. Issuing consumes a number from a gap-free fiscal
# register that cannot be handed back; annulling and `mark_transmitted_externally` change
# what an immutable document says after the fact; the rate and period-lock operations
# rewrite what a quarter's work was worth. The worst outcome is not "the agent made a
# mistake" but "the agent made a mistake and nobody can undo it".
#
# **Why the check is here and not in a tool registration.** Until this list existed the
# ban was only half true. `apps/mcp/tests/test_mcp_invoice_ban.py` keeps these unreachable
# *structurally* -- the MCP tool is not registered -- and argues, correctly, that a role
# check would guarantee nothing, since a personal access token inherits its owner's full
# role and an administrator's token passes any role check. But the credential is not
# confined to the MCP transport: `PatService.resolve` answers with `Actor(type="mcp",
# role=<the owner's role>)`, and `apps/api`'s `get_actor` accepts that same `Bearer pgc_…`
# header on every REST route. Nothing in that package read `actor.type`. So a token handed
# to an agent on the promise that it "may prepare but may not emit" could issue an invoice
# with one `curl`. The tool was absent; the capability was not.
#
# The check below is a permission check, which is the thing that file argues against --
# and it is sound here for the reason that argument turns on: it keys on the *credential*,
# not on the role. An administrator's token does not pass it by being an administrator.
#
# **Why it is a switch and not a wall.** The product is single-tenant and self-hosted, and
# the person running it may reasonably want their own agent to do everything they can do.
# `Settings.mcp_full_access` is read once, in `PatService.resolve`, and stamped onto the
# actor as `full_access`. The default is closed, so nobody has to know this exists to be
# safe; opening it is a decision about one installation, recorded in its `.env`.
#
# The list does not change when the switch flips -- it *is* what the switch is about,
# which is why every argument above stays here in full. The real cure remains scopes on a
# token (residuo R10), which would let a token say what it is *for* rather than inherit
# everything and then be told no.
AGENT_FORBIDDEN_ACTIONS: frozenset[str] = frozenset(
    {
        # Fiscal acts: they consume a register number or change what an immutable
        # document says after the fact (slice 3 §11).
        "issue_invoice",
        "annul_invoice",
        "mark_transmitted_externally",
        "export_invoice_xml",
        # `update_fiscal_profile` is *not* here since ORB-188 (2026-09-12). It was, as
        # one of slice 3 §11's four names, because the profile decides the rate, natura
        # and bollo of every future invoice line. But the write is a total replacement
        # that can be replaced again, an issued invoice keeps its own copy and does not
        # move, and in a space born empty it is the first thing a person asks their
        # assistant to do («imposta il mio profilo fiscale»). Setup, not history. The
        # service still asks `require_admin`, so a collaboratore's token is refused.
        # Rates, cost categories and period locks: configuration, or rewriting what a
        # quarter's work was worth (slice 4 §11).
        "recalculate_rates",
        "update_user_rates",
        "update_deal_rate",
        "create_cost_category",
        "update_cost_category",
        "archive_cost_category",
        "unarchive_cost_category",
        "close_period",
        "reopen_period",
        # The bridge from hours to an invoice line, and the annual estimate.
        "bind_time_to_invoice",
        "get_fiscal_estimate",
        # Asking Gmail who at a customer's domain one has written to. It spends the
        # owner's quota under the owner's consent, exactly as a backfill does, and the
        # same switch says whether this installation wants its agent able to do that.
        "discover_gmail_correspondents",
        # Leggere il testo di un allegato di una mail archiviata. La sincronizzazione
        # non salva mai i byte di un allegato (spec 5.4), quindi questa operazione va a
        # prenderli da Google al momento: spende la quota del titolare sotto il suo
        # consenso, come sopra, e quello che restituisce e' un file scritto da un
        # mittente esterno. Non archivia niente, e il fatto che sia una lettura non la
        # rende diversa dalle altre voci di questo blocco: e' contenuto di fuori che
        # entra in un contesto che legge il testo come istruzioni.
        "read_gmail_attachment",
        # Slice 9 §3.6: writing a numbered, issued row straight into the fiscal register,
        # and declaring the numbers it will never carry. Both change what the register
        # says about the past, which is the property every other entry here protects.
        "import_issued_invoice",
        "declare_invoice_register_gaps",
        # REB-365: reads a structured document's own stored bytes back, parsed and
        # unwritten -- no register write of its own, but it exposes exactly the same
        # incoming-supplier and register-conflict facts the two writes above guard,
        # ahead of a human's own confirm step, so it is gated the same way.
        "review_invoice_import",
        # REB-366: converges the review step's own classification onto `import_
        # issued`'s exact validation/lock/write sequence -- the actual register
        # write this human's-own-confirm step exists to gate, so it carries the
        # same ban as `import_issued_invoice` itself.
        "confirm_invoice_import",
        # Slice 9 §4.2: reading the titolare's Google Drive, and copying a file from it
        # into the CRM. Not irreversible -- nothing on Drive changes, and an imported
        # document can be deleted -- but on this list for the reason
        # `discover_gmail_correspondents` is: they spend the titolare's Drive quota
        # under the titolare's OAuth consent, and what they return is the *content* of
        # a personal Drive, where the folder of another job and the rent contract live
        # next to the client's. The confinement to `root_folder_ids` is what makes them
        # offerable at all; the switch is what says this installation wants its agent
        # inside those folders.
        "list_drive_files",
        "read_drive_file",
        "import_drive_file",
        # Slice 9 §5.2: *configuring* the Drive grant, as opposed to reading through it.
        # These three have no MCP tool and never will -- they are settings-panel
        # operations -- so they are the first entries here that the ban list holds on
        # their own, with nothing in `test_mcp_invoice_ban.py`'s `FORBIDDEN` to mirror
        # them (that file's `REST_ONLY_FORBIDDEN` enumerates them for exactly that
        # reason). The credential is what makes them reachable anyway: a PAT is accepted
        # on every REST route, so without these an agent token could PATCH
        # `/api/drive/account/roots` and point the reader -- and the document storage --
        # at any folder of the titolare's Drive it liked, or disconnect the account and
        # take the CRM's own document store offline. Naming the folders the CRM may read
        # and write is the decision every Drive guarantee above rests on; it belongs to
        # the person whose Drive it is.
        #
        # Spelled as the action strings the services already pass -- `account.py`'s
        # `_ROOTS_ACTION` and `oauth.py`'s `_CONNECT_ACTION`/`_DISCONNECT_ACTION` -- and
        # they are Italian sentences rather than tool names because that is what a
        # settings operation's `require_write` was already audited under.
        "impostare le cartelle Drive",
        "collegare Google Drive",
        "scollegare Google Drive",
    }
)


class Actor(BaseModel):
    """Who is performing an operation. Always passed explicitly — never inferred
    from global state — so that authorization is testable and the timeline is honest
    about whether a human or an agent made the change."""

    model_config = ConfigDict(frozen=True)

    id: UUID | None
    type: ActorType
    role: Role
    # What this credential may do, carried on the credential itself rather than read
    # from a global at the point of use. `PatService.resolve` sets it from
    # `Settings.mcp_full_access`; a browser session never sets it because a `user` actor
    # is not subject to the list at all. Keeping it here is what makes a REST request
    # presenting a PAT behave exactly like the MCP transport -- the asymmetry that let a
    # `curl` issue an invoice while the tool was unregistered (commit 086c561).
    full_access: bool = False

    @classmethod
    def system(cls) -> Self:
        return cls(id=None, type="system", role="admin")

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    @property
    def can_administer(self) -> bool:
        return self.role in ADMIN_ROLES

    def _refuse_if_agent(self, action: str) -> None:
        """Checked before the role, deliberately.

        A `readonly` agent asking to issue an invoice should be told the operation is
        closed to agents, not that it needs a better role — the second answer invites
        somebody to hand the token a bigger role, which is exactly the wrong move.
        """
        if self.type == "mcp" and not self.full_access and action in AGENT_FORBIDDEN_ACTIONS:
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

    def require_write(self, action: str) -> None:
        self._refuse_if_agent(action)
        if not self.can_write:
            raise PermissionDenied(action, list(WRITE_ROLES), self.role)

    def require_admin(self, action: str) -> None:
        self._refuse_if_agent(action)
        if not self.can_administer:
            raise PermissionDenied(action, list(ADMIN_ROLES), self.role)
