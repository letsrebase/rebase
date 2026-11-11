"""`tax_payment_due` and `tax_figure_missing` (REB-1078, taxes design § 4): both fire on their
dates, are quiet when settled or declared, name the tax and the date and never an amount, and
are told to the admins whose scope covers the legal entity and to nobody else."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

from test_reminders_space import NOON, SETTINGS, Space, _admin
from test_reminders_space import space as space  # noqa: F401, PLC0414

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.scope import apply_scope
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.reminders.catalogue import Severity
from pigrocrm.core.reminders.detectors.tax_dates import figure_subject
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import ReminderPreferenceWrite
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.space_settings import SpaceSetting

DUE = dt.date(2026, 11, 30)
AMOUNT = "1234.56"


def _item(
    space: Space,
    *,
    kind: str = "tax_advance",
    due: dt.date = DUE,
    state: str = "expected",
    legal_entity: object = None,
) -> DueItem:
    item = DueItem(
        legal_entity_id=legal_entity or space.legal_entity,
        direction="out",
        state=state,
        counterparty_type="tax_office",
        tax="substitute_tax",
        amount=Decimal(AMOUNT),
        description=f"Acconto imposta sostitutiva, {AMOUNT}",
        source_kind=kind,
        source_id=uuid4(),
        own_date=due,
        due_date=due,
    )
    space.session.add(item)
    space.session.flush()
    return item


def _told(space: Space, day: dt.date, type_key: str = "tax_payment_due") -> dict[UUID, Severity]:
    return space.told(type_key, space.evaluate(today=day, now=NOON))


# -- tax_payment_due ----------------------------------------------------------------------


def test_a_tax_item_warns_14_days_before_and_is_serious_3_days_before(space: Space) -> None:
    _item(space)
    both = {space.owner.id, space.other_admin.id}

    assert _told(space, DUE - dt.timedelta(days=15)) == {}
    for days, severity in ((14, "warning"), (4, "warning"), (3, "serious"), (0, "serious")):
        told = _told(space, DUE - dt.timedelta(days=days))
        assert set(told) == both and set(told.values()) == {severity}, days
    assert _told(space, DUE + dt.timedelta(days=1)) == {}, "past its date the schedule says it"


def test_every_tax_kind_is_told_and_a_settled_or_dropped_item_is_quiet(space: Space) -> None:
    for kind in ("tax_balance", "tax_advance", "tax_instalment"):
        _item(space, kind=kind)
    _item(space, state="settled")
    _item(space, state="dropped")
    _item(space, kind="sales_invoice")
    day = DUE - dt.timedelta(days=3)

    evaluation = space.evaluate(today=day, now=NOON)

    mine = [r for r in evaluation.reminders if r.type == "tax_payment_due"]
    assert len(mine) == 3 * 2, "three open tax items, two admins"
    assert {r.title.split(" ")[0] for r in mine} == {"Saldo", "Acconto", "Rata"}


def test_the_messages_name_the_tax_and_the_date_and_never_the_amount(space: Space) -> None:
    _item(space)
    evaluation = space.evaluate(today=DUE - dt.timedelta(days=3), now=NOON)

    reminder = next(r for r in evaluation.reminders if r.type == "tax_payment_due")

    text = f"{reminder.title} {reminder.detail}"
    assert "IMPOSTA SOSTITUTIVA" in text.upper()
    assert "30/11/2026" in text
    assert "1234" not in text and "1.234" not in text and "€" not in text


def test_a_member_and_an_admin_out_of_scope_are_not_told(space: Space) -> None:
    other = LegalEntity(name="Seconda", legal_name="Seconda Srl", active=True)
    space.session.add(other)
    space.session.flush()
    apply_scope(space.session, space.other_admin, [other.id])
    _item(space)

    told = _told(space, DUE - dt.timedelta(days=3))

    assert set(told) == {space.owner.id}, "no collaborator, and no admin scoped elsewhere"


def test_the_preferences_decide_the_ladder_and_a_switched_off_type_raises_nothing(
    space: Space,
) -> None:
    _item(space)
    ReminderPreferenceService(space.session).write(
        Actor(id=space.owner.id, type="user", role="admin"),
        "space",
        "tax_payment_due",
        ReminderPreferenceWrite(ladder={"warning": 30, "serious": 7}),
    )

    assert set(_told(space, DUE - dt.timedelta(days=30)).values()) == {"warning"}
    assert set(_told(space, DUE - dt.timedelta(days=7)).values()) == {"serious"}


def test_a_switched_off_type_raises_nothing(space: Space) -> None:
    _item(space)
    ReminderPreferenceService(space.session).write(
        Actor(id=space.owner.id, type="user", role="admin"),
        "space",
        "tax_payment_due",
        ReminderPreferenceWrite(enabled=False),
    )

    assert _told(space, DUE - dt.timedelta(days=3)) == {}


# -- tax_figure_missing -------------------------------------------------------------------

# The flat-rate pack files the 2025 return of the substitute tax on 31 October 2026, a Saturday,
# so on Monday 2 November.
FILED = dt.date(2026, 11, 2)


def _old_entity(space: Space) -> None:
    entity = space.session.get(LegalEntity, space.legal_entity)
    assert entity is not None
    entity.created_at = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    space.session.flush()


def _missing(space: Space, day: dt.date) -> dict[UUID, Severity]:
    return _told(space, day, "tax_figure_missing")


def test_a_missing_declared_figure_is_told_after_the_filing_date_not_before(space: Space) -> None:
    _old_entity(space)
    both = {space.owner.id, space.other_admin.id}

    assert _missing(space, FILED) == {}, "the filing day itself is still on time"
    day_after = _told(space, FILED + dt.timedelta(days=1), "tax_figure_missing")
    assert set(day_after) == both and set(day_after.values()) == {"warning"}
    thirty = _missing(space, FILED + dt.timedelta(days=30))
    assert set(thirty.values()) == {"serious"}


def test_a_declared_figure_or_a_later_creation_silences_it(space: Space) -> None:
    _old_entity(space)
    day = FILED + dt.timedelta(days=5)
    assert _missing(space, day) != {}

    DeclaredTaxFigureService(space.session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax="substitute_tax", year=2025, tax_due=Decimal("100.00")),
        _admin(),
        legal_entity_id=space.legal_entity,
    )
    assert _missing(space, day) == {}


def test_a_legal_entity_created_after_the_filing_date_is_not_asked_for_it(space: Space) -> None:
    entity = space.session.get(LegalEntity, space.legal_entity)
    assert entity is not None
    entity.created_at = dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    space.session.flush()

    assert _missing(space, dt.date(2026, 12, 1)) == {}


def test_the_missing_figure_message_names_the_tax_and_the_date_and_no_amount(
    space: Space,
) -> None:
    _old_entity(space)
    evaluation = space.evaluate(today=FILED + dt.timedelta(days=2), now=NOON)

    reminder = next(r for r in evaluation.reminders if r.type == "tax_figure_missing")

    assert reminder.entity_id == space.legal_entity
    assert "02/11/2026" in reminder.detail and "2025" in reminder.title
    assert "€" not in reminder.detail


def test_the_missing_figure_is_for_admins_whose_scope_covers_the_entity(space: Space) -> None:
    _old_entity(space)
    other = LegalEntity(name="Seconda", legal_name="Seconda Srl", active=True)
    space.session.add(other)
    space.session.flush()
    apply_scope(space.session, space.other_admin, [other.id])

    assert set(_missing(space, FILED + dt.timedelta(days=2))) == {space.owner.id}


def test_the_occurrence_is_one_per_entity_tax_and_year() -> None:
    entity = uuid4()
    assert figure_subject(entity, "irpef", 2025) == figure_subject(entity, "irpef", 2025)
    assert figure_subject(entity, "irpef", 2025) != figure_subject(entity, "irpef", 2026)
    assert figure_subject(entity, "irpef", 2025) != figure_subject(entity, "ires", 2025)


def test_a_recomputed_item_keeps_its_reminders_identity(space: Space) -> None:
    first = _item(space)
    day = DUE - dt.timedelta(days=3)
    before = {r.key for r in space.evaluate(today=day, now=NOON).reminders}
    # The tax writer drops a changed item and writes a new row for the same slot.
    first.state = "dropped"
    again = _item(space)
    again.source_id = first.source_id
    space.session.flush()

    after = {r.key for r in space.evaluate(today=day, now=NOON).reminders}

    assert {k for k in before if k.startswith("tax_payment_due")} == {
        k for k in after if k.startswith("tax_payment_due")
    }


def test_the_titles_name_the_legal_entity(space: Space) -> None:
    _item(space)
    entity = space.session.get(LegalEntity, space.legal_entity)
    assert entity is not None

    evaluation = space.evaluate(today=DUE - dt.timedelta(days=3), now=NOON)

    reminder = next(r for r in evaluation.reminders if r.type == "tax_payment_due")
    assert reminder.title.endswith(entity.name)


def test_an_agent_credential_without_full_access_is_not_shown_the_tax_reminders(
    space: Space,
) -> None:
    _item(space)
    _old_entity(space)
    day = FILED + dt.timedelta(days=5)
    _item(space, due=day + dt.timedelta(days=3))
    service = ReminderService(space.session, SETTINGS)

    def types(actor: Actor) -> set[str]:
        return {r.type for r in service.list(actor, today=day, now=NOON)}

    admin = Actor(id=space.owner.id, type="user", role="admin")
    token = Actor(id=space.owner.id, type="mcp", role="admin", full_access=False)
    full = Actor(id=space.owner.id, type="mcp", role="admin", full_access=True)
    tax = {"tax_payment_due", "tax_figure_missing"}

    assert tax <= types(admin)
    assert tax <= types(full)
    assert not tax & types(token)


def test_a_figure_for_another_year_or_tax_does_not_silence_the_missing_one(space: Space) -> None:
    _old_entity(space)
    service = DeclaredTaxFigureService(space.session)
    for tax, year in (("substitute_tax", 2024), ("irpef", 2025)):
        service.set_declared_tax_figure(
            DeclaredTaxFigureSet(tax=tax, year=year, tax_due=Decimal("100.00")),  # type: ignore[arg-type]
            _admin(),
            legal_entity_id=space.legal_entity,
        )

    assert _missing(space, FILED + dt.timedelta(days=5)) != {}


def test_both_types_are_skipped_while_the_taxes_module_is_off(space: Space) -> None:
    _item(space)
    _old_entity(space)
    space.session.add(SpaceSetting(key="module_taxes", value="false"))
    space.session.flush()

    evaluation = space.evaluate(today=FILED + dt.timedelta(days=5), now=NOON)

    assert not {"tax_payment_due", "tax_figure_missing"} & evaluation.ran


def test_the_chamber_fee_is_told_like_any_tax_date(space: Space) -> None:
    item = _item(space, kind="chamber_fee")
    item.tax = "chamber_fee"
    space.session.flush()

    evaluation = space.evaluate(today=DUE - dt.timedelta(days=3), now=NOON)

    reminder = next(r for r in evaluation.reminders if r.type == "tax_payment_due")
    assert reminder.title.startswith("Diritto camerale")
    assert "€" not in reminder.detail and "1234" not in reminder.detail
