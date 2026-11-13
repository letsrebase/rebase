"""REB-781: every change to the CRM leaves a timeline entry that says who, when and how.

Lorenzo, 2026-10-27: «qualunque aggiornamento a qualunque cosa deve essere tracciato». Two
halves, because "every write is recorded" is a claim about code nobody has written yet and
a claim about code that exists, and one test cannot hold both.

**The sweep** is the half that fails when somebody adds a write path and forgets the
record. It reads every `*Service` class under `packages/core`, and the classes a facade splits
its writes out to, the way `apps/mcp/tests/test_mcp_surface_coverage.py` does (the naming
convention is the enumeration) and asks of each public method two things, from its AST and
through the `self.` helpers it calls: does it write (it asks for a role, or it commits), and does it
reach `ActivityService.record`. A method that writes and does not record is either a gap,
which is fixed, or one of the named exclusions below, each with the reason it is not one.
Adding a writer therefore fails this file until it records, or until somebody writes down
why it need not: the decision is forced when it is cheap.

**The attribution** is the half that proves what a row holds. Every write in the table at
the bottom is run twice, once as a member in the browser (`user`) and once as the same
credential an agent holds (`mcp`, the member who owns the token), and each must leave a row
with the member's id, the channel and a real moment, which the timeline then serves with
the member's name -- a deactivated member's included.

What neither half can see, stated here so nobody reads the green as more than it is: a
write that does not go through a `*Service` class (the sweep's inventory), and a method
that records on one branch and skips another (the sweep asks "reachable", not "always").
The second is what the attribution table's one-row-per-operation scenarios are for.
"""

import ast
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fakes.contacts import points_in
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.deals.schemas import DealCreate, DealUpdate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.notes.schemas import NoteCreate, NoteUpdate
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.organizations.schemas import OrganizationCreate, OrganizationUpdate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.schemas import PersonCreate, PersonUpdate
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.purchases.schemas import PurchaseCreate, PurchaseUpdate
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.reminders.preferences import (
    SPACE_DEFAULTS_ENTITY,
    SPACE_DEFAULTS_ID,
    ReminderPreferenceService,
)
from pigrocrm.core.reminders.schemas import GENERAL, ReminderPreferenceWrite
from pigrocrm.core.tasks.schemas import TaskCreate, TaskUpdate
from pigrocrm.core.tasks.service import TaskService
from pigrocrm.core.timetracking.schemas import TimeEntryCreate, TimeEntryUpdate
from pigrocrm.core.timetracking.service import TimeEntryService

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "pigrocrm" / "core"

# The classes a `*Service` facade hands its writes to (`InvoiceService.issue` is
# `InvoiceEmission.issue`): the sweep reads them under their own names, because the facade's
# method is a one-line delegation and would show as a reader.
_SPLIT_OUT = frozenset(
    {
        "DocumentTemplating",
        "DocumentText",
        "DocumentUploads",
        "InvoiceArtifacts",
        "InvoiceDrafting",
        "InvoiceEmission",
        "InvoiceImporter",
        "IssuedInvoiceImporter",
        "RegisterGaps",
        "TimeBilling",
    }
)

Method = tuple[str, str]

# What makes a method a writer: it asks for a role (the convention every service write
# follows, `require_write` and its two siblings) or it commits. A reader does neither;
# the few that do are in the first block below.
_GATES = frozenset({"require_write", "require_admin", "require_unscoped_admin"})


# --- the exclusions ----------------------------------------------------------------
#
# Every public method of a `*Service` that writes, or looks as if it does, and reaches no
# `ActivityService.record` of its own. Keyed by `(class, method)`, never by the bare name
# (`create` and `update` are everywhere), each with the reason. Four reasons, and they are
# different statements: only the first is "nothing changes", only the third is a decision
# about what the timeline is for.

# 1. It looks like a write and is a read: a role check on a path that only looks, or a
#    commit that settles a lazy refresh (a token, a cached figure).
_READS: dict[Method, str] = {
    ("BankAccountService", "get_bank_account"): "reads an account; nothing is written",
    ("BankAccountService", "get_coverage"): "reads the months a statement covers",
    ("BankAccountService", "list_bank_accounts"): "reads the accounts and their balances",
    ("BankStatementReadService", "list_movements"): "reads the movements of an account",
    ("BankStatementReadService", "list_statements"): "reads the statements of an account",
    ("MovementMatchService", "get_movement"): "reads a movement and ranks its candidates",
    ("MovementMatchService", "list_open_items"): "reads the open items a movement could pay",
    ("MovementMatchService", "preview"): "works out a selection and writes nothing",
    ("LetterService", "preview_letter"): "renders a letter and stores nothing",
    ("LetterService", "preview_framework_agreement"): "renders an agreement and stores nothing",
    ("SigningService", "get"): "reads where a document stands: its state, dates and reason",
    ("ExportShareService", "list_export_share_links"): "reads the links of the exports",
    ("ExportShareService", "get_export_share_link"): "reads a link with its openings",
    ("AccountantExportService", "list_exports"): "reads the stored exports",
    ("AccountantExportService", "count_exports"): "reads how many stored exports there are",
    ("AccountantExportService", "describe_parts"): "reads which parts a build would hold",
    ("AccountantExportService", "get_export_checklist"): (
        "reads what would make a period's export incomplete and stores nothing: the build "
        "that keeps it records who built the export"
    ),
    ("AnalyticsService", "economic_overview"): "a figure; nothing is written for the person",
    ("AnalyticsService", "get_fiscal_estimate"): "a figure computed from the profile",
    ("AnalyticsService", "get_tax_calendar"): "reads the pack's dates, writes nothing",
    ("F24GroupService", "get_f24_group"): "reads one F24 group, writes nothing",
    (
        "F24GroupService",
        "list_f24_groups",
    ): "reads the F24 groups of a legal entity, writes nothing",
    ("TaxNoticePlanService", "list_tax_plans"): "reads the plans of a legal entity, writes nothing",
    (
        "IvaSettlementService",
        "list_iva_settlements",
    ): "reads the stored settlements, writes nothing",
    ("IvaRegisterService", "list_periods"): "reads the periods and their ladder, writes nothing",
    ("IvaRegisterService", "read_sales_register"): "reads the sales register, writes nothing",
    ("IvaRegisterService", "read_purchase_register"): "reads the purchase register, writes nothing",
    ("AutomationConfigService", "describe_automations"): "reads the rules",
    ("DaysReportService", "report"): "reads the freelancer's days live; nothing is written",
    ("MonthlyReportService", "for_engagement"): "reads the monthly reports; nothing is written",
    ("EngagementService", "get"): "reads an engagement; an administrator's read",
    ("EngagementService", "list"): "reads engagements; an administrator's read",
    ("EngagementService", "counts"): "counts engagements; an administrator's read",
    (
        "EngagementPaymentService",
        "for_engagement",
    ): "reads what an engagement pays; nothing is written",
    ("OwnEngagementService", "for_contract"): "reads across spaces; nothing is written",
    ("FreelancerService", "availability"): "a computed answer; nothing is written",
    (
        "FreelancerService",
        "set_tax_data",
    ): "writes through `OrganizationService.update`, which records",
    ("DueItemService", "get"): "reads an item; the role check only gates the fiscal kinds",
    ("DueItemService", "list"): "reads items; the role check only gates the fiscal kinds",
    ("ForecastService", "refresh_all"): "the daily job, a system step: forecasts are derived rows",
    ("GmailAttachmentService", "attachment_text"): "reads an attachment and stores nothing",
    ("GmailSyncService", "discover"): "asks Gmail, writes nothing",
    ("GmailSyncService", "suggest_organizations"): "asks Gmail, writes nothing",
    ("InvitationService", "list"): "reads the invitations",
    ("OrganizationService", "duplicates"): "reads the registry; the role check only gates who may",
    ("DocumentTemplating", "render_to_pdf"): (
        "commits only to release the read transaction before the Typst compile; writes nothing"
    ),
    ("AiModelService", "get"): "reads the model's settings, never its key",
    ("AiModelService", "test"): "one call whose cost is the gateway's own usage row (REB-972)",
    ("InvoiceImporter", "review_import"): "classifies stored bytes, writes nothing",
    ("InvoiceImporter", "review_upload"): "classifies uploaded bytes, writes nothing",
    ("PatService", "list_team"): "reads the live tokens of the other members",
    ("ScheduledJobService", "describe"): "reads the job runs",
    ("SlackService", "channels"): "asks Slack, writes nothing",
    ("SlackService", "health"): "reads the workspace and the matches",
    ("SpaceSettingsService", "read"): "reads the settings",
    ("StaffService", "get_staff_member"): "reads a member; the role check only keeps pay private",
    ("StaffService", "list_staff"): "reads the members; the role check only keeps pay private",
    ("StaffService", "get_payroll_pay_day"): "reads the pay day; the role check only gates it",
    ("StaffService", "propose_staff_hourly_cost"): "offers a figure and writes nothing (REB-1009)",
    ("StaffService", "adopt_staff_hourly_cost"): (
        "writes through `TimeEntryService.update_user_rates`, which records the timeline entry"
    ),
    ("PayrollService", "get_payroll_month"): "reads a month's estimate; the role check gates it",
    ("UserService", "list"): "reads the members",
}

# 2. It writes, through a service that records. The sweep follows `self.` helpers only,
#    and these reach the record through another class, which that class's own methods
#    are swept for.
_DELEGATION: dict[Method, str] = {
    ("ApprovalRequestService", "prepare"): (
        "creates the days (`WorkUnitService.create` records on the contract) and the draft "
        "(`EmailDraftService.create` records on the record it is about)"
    ),
    ("ContractEmailService", "prepare"): "`EmailDraftService.create` records the draft",
    ("ContractRegisterService", "render_pdf"): "`DocumentService.create_from_template` records",
    ("OrganizationService", "add_person_from_mail"): "`PersonService._insert` records `created`",
    ("NoteService", "add"): (
        "`append_note` records `note_added` on the parent, the one function the creating "
        "services write a first note with as well"
    ),
    ("DocumentService", "create"): "`DocumentContext.create_row` records `created`",
    ("DocumentTemplating", "regenerate"): "`DocumentUploads.add_version` records the version",
    ("InvoiceArtifacts", "export_xml"): "files the XML through `DocumentService.add_version`",
    ("InvoiceArtifacts", "produce_artifacts"): "files the PDF and XML through `add_version`",
    ("InvoiceImporter", "confirm_import"): (
        "each invoice is written by `IssuedInvoiceImporter.import_issued`, swept under its name"
    ),
    ("InvoiceImporter", "confirm_upload"): (
        "each invoice is written by `IssuedInvoiceImporter.import_issued`, swept under its name"
    ),
    ("TimerService", "stop"): "`TimeEntryService.create` records the entry the timer becomes",
}

# 3. What a person does to their own session or their own notifications, not to the
#    CRM's records: a login, a snoozed reminder, a running clock. Nobody else reads these
#    as history, and a row per login would bury the timelines they would be written on.
_PERSONAL: dict[Method, str] = {
    ("IdentityService", "enter"): "opening a session",
    ("IdentityService", "get_or_create"): "an identity of the person across spaces",
    ("IdentityService", "request"): "asking for a sign-in link",
    ("IdentityService", "revoke_all"): "closing sessions",
    ("IdentityService", "upsert_and_issue"): "opening a session",
    ("MagicLinkService", "enter"): "opening a session",
    ("MagicLinkService", "request"): "asking for a sign-in link",
    ("PushSubscriptionService", "subscribe"): "this browser's own notification endpoint",
    ("PushSubscriptionService", "unsubscribe"): "this browser's own notification endpoint",
    ("RefreshTokenService", "consume"): "session rotation",
    ("RefreshTokenService", "issue"): "session rotation",
    ("RefreshTokenService", "revoke_all"): "session rotation",
    ("RefreshTokenService", "rotate"): "session rotation",
    # `ReminderPreferenceService.write` and `reset` are not here: their `me` scope is a person's
    # own, but the `space` scope is the defaults everybody is reminded by, and records.
    ("ReminderStateService", "acknowledge"): "one member's own reminder, read or snoozed",
    ("ReminderStateService", "snooze"): "one member's own reminder, read or snoozed",
    ("TimerService", "discard"): "the member's own running clock; no hours exist yet",
    ("TimerService", "start"): "the member's own running clock; no hours exist yet",
    ("TimerService", "update"): "the member's own running clock; no hours exist yet",
    ("UserService", "authenticate"): "a sign-in; `last_login_at` is not a change to the CRM",
}

# 4. Machinery with no member behind it, whose outcome is recorded where it matters.
_MACHINERY: dict[Method, str] = {
    ("SlackOAuthService", "start"): "builds a consent URL; `complete` records",
    ("InvitationService", "record_mail"): "delivery status of a mail already recorded as sent",
    ("InvitationService", "refresh_mail_status"): "delivery status of a mail already recorded",
    ("TenantService", "provision"): "creates a space in the registry; the space has no history yet",
    ("ImapSyncService", "sync"): (
        "the cron's read of a mailbox: its health and watermark live on the mailbox row, and "
        "connecting, re-keying and disconnecting are what record"
    ),
}

EXCLUSIONS: dict[Method, str] = {**_READS, **_DELEGATION, **_PERSONAL, **_MACHINERY}


# --- the sweep ---------------------------------------------------------------------


def _is_record(call: ast.Call) -> bool:
    """`<something activity-ish>.record(...)`: the receiver is checked because
    `ApprovalService.record` is a method of its own, and a bare `.record(` match would
    call every approval recorded in the timeline."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "record"
        and "activit" in ast.unparse(func.value).lower()
    )


def _sweep() -> dict[Method, tuple[bool, bool]]:
    """`(writes, records)` for every public method of every `*Service` class, through
    the `self.` helpers it calls (a record in `_insert` counts for `create`)."""
    found: dict[Method, tuple[bool, bool]] = {}
    for path in sorted(CORE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            if not (cls.name.endswith("Service") or cls.name in _SPLIT_OUT):
                continue
            methods = {
                m.name: m for m in cls.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
            }

            def facts(name: str, seen: set[str], methods: dict[str, ast.FunctionDef] = methods):
                if name in seen or name not in methods:
                    return False, False
                seen.add(name)
                writes = records = False
                for node in ast.walk(methods[name]):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                        continue
                    attr = node.func.attr
                    writes |= attr in _GATES or attr == "commit"
                    records |= _is_record(node)
                    receiver = node.func.value
                    if isinstance(receiver, ast.Name) and receiver.id == "self" and attr in methods:
                        inner_writes, inner_records = facts(attr, seen)
                        writes |= inner_writes
                        records |= inner_records
                return writes, records

            for name in methods:
                if not name.startswith("_"):
                    found[(cls.name, name)] = facts(name, set())
    return found


SWEEP = _sweep()
UNRECORDED_WRITERS = sorted(m for m, (writes, records) in SWEEP.items() if writes and not records)


def test_the_sweep_finds_writers_that_record_and_writers_that_do_not() -> None:
    """The guard on the guard: an empty sweep (a moved directory, a renamed convention)
    would make every assertion below pass while checking nothing."""
    assert SWEEP[("OrganizationService", "update")] == (True, True)
    assert SWEEP[("DocumentUploads", "add_version")] == (True, True)  # through `_add_version_row`
    assert SWEEP[("OrganizationService", "get")][0] is False
    assert SWEEP[("ApprovalService", "record")] == (True, True)  # the approval's own row
    assert SWEEP[("TimerService", "start")] == (True, False)
    assert len([m for m, (w, r) in SWEEP.items() if w and r]) > 100


@pytest.mark.parametrize(("service", "method"), UNRECORDED_WRITERS)
def test_every_writer_records_or_is_a_named_exclusion(service: str, method: str) -> None:
    assert (service, method) in EXCLUSIONS, (
        f"{service}.{method} writes and reaches no ActivityService.record. Record who did it "
        "(REB-781), or add it to ESCLUSIONI in this file with the reason it need not."
    )


@pytest.mark.parametrize(("service", "method"), sorted(EXCLUSIONS))
def test_every_exclusion_is_still_a_writer_that_does_not_record(service: str, method: str) -> None:
    """A stale entry is a hole with a reason attached: the method was renamed, now
    records, or never wrote at all."""
    assert (service, method) in SWEEP, f"{service}.{method} no longer exists"
    writes, records = SWEEP[(service, method)]
    assert not records, f"{service}.{method} records now: drop its exclusion"
    assert writes, f"{service}.{method} no longer writes or asks for a role: drop its exclusion"


def test_no_exclusion_hides_in_two_blocks() -> None:
    """Four blocks, one reason each: a method in two is a method nobody decided about."""
    blocks = (_READS, _DELEGATION, _PERSONAL, _MACHINERY)
    assert sum(len(b) for b in blocks) == len(EXCLUSIONS)


# --- the attribution ---------------------------------------------------------------

Scenario = Callable[[Session, Actor, dict[str, UUID]], tuple[str, UUID]]


@pytest.fixture
def members(db_session: Session) -> dict[str, User]:
    """Two members of the space, one of them deactivated afterwards by the test that
    needs it: the one who changes things in a browser and the one whose agent does."""
    made: dict[str, User] = {}
    for key, name in (("web", "Giulia Bianchi"), ("mcp", "Marco Verdi")):
        user = User(
            email=f"{key}-{uuid4()}@example.test",
            password_hash="x",
            name=name,
            role="admin",
        )
        db_session.add(user)
        made[key] = user
    db_session.flush()
    return made


def _actors(members: dict[str, User]) -> dict[str, Actor]:
    return {
        "user": Actor(id=members["web"].id, type="user", role="admin"),
        "mcp": Actor(id=members["mcp"].id, type="mcp", role="admin"),
    }


def _customer(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    created = OrganizationService(session).create(OrganizationCreate(legal_name="ACME"), actor)
    ctx["organization"] = created.id
    return "organization", created.id


def _customer_update(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    _customer(session, actor, ctx)
    OrganizationService(session).update(
        ctx["organization"], OrganizationUpdate(contact_points=points_in(phone="02 1234567")), actor
    )
    return "organization", ctx["organization"]


def _customer_archive_restore(
    session: Session, actor: Actor, ctx: dict[str, UUID]
) -> tuple[str, UUID]:
    _customer(session, actor, ctx)
    OrganizationService(session).soft_delete(ctx["organization"], actor)
    OrganizationService(session).restore(ctx["organization"], actor)
    return "organization", ctx["organization"]


def _customer_notes(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    """A first note on creation, then one added, edited and deleted: four entries, the first
    from the creating service and the rest from `NoteService`."""
    created = OrganizationService(session).create(
        OrganizationCreate(legal_name="ACME", note="Prima nota"), actor
    )
    service = NoteService(session)
    added = service.add("organization", created.id, NoteCreate(text="Seconda nota"), actor)
    service.update(added.id, NoteUpdate(text="Seconda nota, corretta"), actor)
    service.delete(added.id, actor)
    return "organization", created.id


def _person(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    service = PersonService(session)
    person = service.create(PersonCreate(first_name="Anna"), actor)
    service.update(person.id, PersonUpdate(role="CTO"), actor)
    service.soft_delete(person.id, actor)
    service.restore(person.id, actor)
    return "person", person.id


def _deal(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    service = DealService(session)
    deal = service.create(
        DealCreate(
            name="Progetto", customer_id=ctx["seed_customer"], pipeline_stage_id=ctx["open"]
        ),
        actor,
    )
    service.update(deal.id, DealUpdate(name="Progetto bis"), actor)
    service.move_stage(deal.id, ctx["won"], actor)
    service.soft_delete(deal.id, actor)
    service.restore(deal.id, actor)
    return "deal", deal.id


def _task(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    service = TaskService(session)
    created = service.create(TaskCreate(title="Chiamare"), actor)
    service.update(created.id, TaskUpdate(title="Richiamare"), actor)
    service.complete(created.id, actor)
    service.reopen(created.id, actor)
    service.cancel(created.id, actor)
    service.soft_delete(created.id, actor)
    service.restore(created.id, actor)
    return "task", created.id


def _time_entry(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    service = TimeEntryService(session)
    entry = service.create(
        TimeEntryCreate(
            deal_id=ctx["seed_deal"],
            user_id=ctx["worker"],
            date=date(2026, 3, 10),
            hours=Decimal("2.00"),
            description="Analisi",
            applied_rate=Decimal("80.000000"),
        ),
        actor,
    )
    service.update(entry.id, TimeEntryUpdate(description="Analisi e stesura"), actor)
    service.soft_delete(entry.id, actor)
    service.restore(entry.id, actor)
    return "time_entry", entry.id


def _cost(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    service = PurchaseService(session)
    cost = service.create(
        PurchaseCreate(
            deal_id=ctx["seed_deal"],
            category_id=ctx["category"],
            date=date(2026, 3, 10),
            amount=Decimal("12.50"),
            description="Licenza",
        ),
        actor,
    )
    service.update(cost.id, PurchaseUpdate(description="Licenza annuale"), actor)
    service.soft_delete(cost.id, actor)
    service.restore(cost.id, actor)
    return "purchase", cost.id


def _reminder_defaults(session: Session, actor: Actor, ctx: dict[str, UUID]) -> tuple[str, UUID]:
    """The one setting of the reminders that is not a person's own: the defaults the whole
    space is reminded by. The person's own layer (`me`) records nothing, by exclusion."""
    service = ReminderPreferenceService(session)
    service.write(actor, "space", GENERAL, ReminderPreferenceWrite(summary_hour=9))
    service.reset(actor, "space", GENERAL)
    service.write(actor, "me", GENERAL, ReminderPreferenceWrite(summary_hour=8))
    return SPACE_DEFAULTS_ENTITY, SPACE_DEFAULTS_ID


# Each scenario with the kinds its operations must leave, exactly.
SCENARIOS: dict[str, tuple[Scenario, set[str]]] = {
    "customer created": (_customer, {"created"}),
    "customer updated": (_customer_update, {"created", "updated"}),
    "customer notes added, edited, deleted": (
        _customer_notes,
        {"created", "note_added", "note_edited", "note_deleted"},
    ),
    "customer archived and restored": (
        _customer_archive_restore,
        {"created", "deleted", "restored"},
    ),
    "person created, updated, archived, restored": (
        _person,
        {"created", "updated", "deleted", "restored"},
    ),
    "deal created, updated, moved, archived, restored": (
        _deal,
        {"created", "updated", "stage_changed", "deleted", "restored"},
    ),
    "task through every state": (
        _task,
        {"created", "updated", "completed", "reopened", "cancelled", "archived", "restored"},
    ),
    "time entry created, updated, archived, restored": (
        _time_entry,
        {"created", "updated", "deleted", "restored"},
    ),
    "cost created, updated, archived, restored": (
        _cost,
        {"created", "updated", "deleted", "restored"},
    ),
    "the space's reminder defaults": (_reminder_defaults, {"defaults_updated", "defaults_reset"}),
}


@pytest.fixture
def world(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_open_stage_id: UUID,
    seeded_won_stage_id: UUID,
    seeded_category_id: UUID,
    seeded_user_id: UUID,
) -> dict[str, UUID]:
    from pigrocrm.core.deals.models import Deal

    deal = db_session.get(Deal, seeded_deal_id)
    assert deal is not None
    return {
        "seed_deal": seeded_deal_id,
        "seed_customer": deal.customer_id,
        "open": seeded_open_stage_id,
        "won": seeded_won_stage_id,
        "category": seeded_category_id,
        "worker": seeded_user_id,
    }


@pytest.mark.parametrize("channel", ["user", "mcp"])
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_change_leaves_who_when_and_which_channel(
    db_session: Session,
    members: dict[str, User],
    world: dict[str, UUID],
    name: str,
    channel: str,
) -> None:
    actor = _actors(members)[channel]
    scenario, expected = SCENARIOS[name]
    before = datetime.now(UTC)
    entity_type, entity_id = scenario(db_session, actor, dict(world))
    after = datetime.now(UTC)

    rows = list(
        db_session.execute(
            select(Activity).where(
                Activity.entity_type == entity_type, Activity.entity_id == entity_id
            )
        ).scalars()
    )
    assert rows, f"{name}: no timeline entry at all"
    for row in rows:
        assert row.actor_id == actor.id, f"{name}/{row.kind}: wrong or missing member"
        assert row.actor_type == channel, f"{name}/{row.kind}: wrong channel"
        assert before <= row.occurred_at <= after, f"{name}/{row.kind}: not a moment of this call"
    # Every operation of the scenario, not a count: a `restore` or a `cancel` that stopped
    # recording would still leave the rows before it, and a number would not notice.
    assert {row.kind for row in rows} == expected, f"{name}: the entries are not the changes"


def test_the_timeline_names_the_member_and_keeps_naming_a_deactivated_one(
    db_session: Session, members: dict[str, User], world: dict[str, UUID]
) -> None:
    actors = _actors(members)
    _, web_id = _customer(db_session, actors["user"], dict(world))
    _, mcp_id = _customer(db_session, actors["mcp"], dict(world))
    members["web"].active = False
    db_session.flush()

    service = ActivityService(db_session)
    (web_entry,) = service.timeline("organization", web_id)
    (mcp_entry,) = service.timeline("organization", mcp_id)

    assert (web_entry.actor_name, web_entry.actor_type) == ("Giulia Bianchi", "user")
    assert (mcp_entry.actor_name, mcp_entry.actor_type) == ("Marco Verdi", "mcp")


def test_a_row_with_no_member_behind_it_has_no_name(db_session: Session) -> None:
    service = ActivityService(db_session)
    entity = uuid4()
    service.record("organization", entity, "created", Actor.system())
    service.record("organization", entity, "updated", Actor.rebase())
    service.record("organization", entity, "restored", Actor(id=uuid4(), type="user", role="admin"))

    entries = service.timeline("organization", entity)

    assert [e.actor_name for e in entries] == [None, None, None]


def test_an_automations_entry_names_the_member_who_set_it_off(
    db_session: Session, members: dict[str, User]
) -> None:
    service = ActivityService(db_session)
    entity = uuid4()
    service.record(
        "deal",
        entity,
        "automation.stage_moved",
        Actor.system(),
        {"triggered_by": str(members["mcp"].id), "triggered_via": "mcp"},
    )
    service.record(
        "deal", entity, "automation.other", Actor.system(), {"triggered_by": "not-a-uuid"}
    )

    by_kind = {e.kind: e for e in service.timeline("deal", entity)}

    assert by_kind["automation.stage_moved"].triggered_by_name == "Marco Verdi"
    assert by_kind["automation.stage_moved"].actor_name is None
    assert by_kind["automation.other"].triggered_by_name is None
