"""Read and change what the system does by itself.

Two public methods, and the split is what the architecture test needs: spec §11.1 puts
`describe_automations` on both surfaces and `update_automation_config` on the API only,
and requires the slice-6 exclusion list to be **exactly** one name. A `get`/`upsert` pair
would put two names on that list, so `get` is not public -- `describe_automations` returns
the configuration inside its payload, which is what both surfaces actually need.
"""

from typing import Any

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.repository import AutomationConfigRepository
from pigrocrm.core.automations.runs import recent_runs
from pigrocrm.core.automations.schemas import (
    KIND_CONFIG_CHANGED,
    RULE_DESCRIPTIONS,
    AutomationConfigRead,
    AutomationConfigUpdate,
    AutomationRuleCode,
    AutomationRuleDescription,
    AutomationsDescription,
)

ENTITY = "automation_config"
RULE_CODES: tuple[AutomationRuleCode, ...] = (
    "A1",
    "A2",
    "sent_offer_follow_up",
    "stalled_deal",
    "overdue_invoice",
)
_RUN_LIMIT = 20


class AutomationConfigService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AutomationConfigRepository(session)
        self.activities = ActivityService(session)

    def describe_automations(self, actor: Actor) -> AutomationsDescription:
        """The rules, their state, and the last twenty executions with their outcome.

        No authorisation check: it is a read of what the system does by itself, every role
        may see it, and spec §13 states this slice adds no authorisation rule. `actor` is
        still taken because every service method in this project takes it.

        This method writes -- `get_or_create` may insert the single row -- so it commits.
        A read method that commits is unusual enough to say out loud: the alternative is
        that the first `GET` of a fresh installation leaves an uncommitted row and the
        second one inserts a duplicate.
        """
        row = self.repo.get_or_create()
        config = AutomationConfigRead.model_validate(row)
        rules = [
            AutomationRuleDescription(
                code=code,
                title=RULE_DESCRIPTIONS[code][0],
                description=RULE_DESCRIPTIONS[code][1],
                active=self._is_active(config, code),
            )
            for code in RULE_CODES
        ]
        runs = recent_runs(self.session, _RUN_LIMIT)
        self.session.commit()
        return AutomationsDescription(configuration=config, rules=rules, runs=runs)

    @staticmethod
    def _is_active(config: AutomationConfigRead, code: AutomationRuleCode) -> bool:
        match code:
            case "A1":
                return config.a1_wins_accepted_offer_deal
            case "A2":
                return config.a2_advances_sent_offer_deal
            case "sent_offer_follow_up":
                return config.a3_sent_offer_follow_up
            case "stalled_deal":
                return config.a4_stalled_deal
            case "overdue_invoice":
                return config.a5_overdue_invoice

    def update_automation_config(
        self, data: AutomationConfigUpdate, actor: Actor
    ) -> AutomationConfigRead:
        """Admin only, audited, and absent from the MCP surface.

        Admin because it changes what the system will do to *future* data without a human
        in the loop -- slice 4 §11's reason 2, and the reason it has no MCP tool. The audit
        entry is residuo **R5** closed for this table, with slice 3 §7.1's argument:
        changing what the system does by itself is of a different order of seriousness from
        renaming a stage. R5 itself stays open: it names field definitions, pipeline
        stages, users and personal access tokens, and none of those four gains a timeline
        entry here.

        `exclude_unset=True`, not `exclude_none`: with two booleans, "not sent" and `None`
        have to be distinguishable, or switching A1 off would silently switch A2 on.
        """
        actor.require_unscoped_admin("update_automation_config")
        row = self.repo.get_or_create()

        changes: dict[str, Any] = {}
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is None:
                continue
            current = getattr(row, field)
            # Only a real difference is recorded: an audit entry that lists every field on
            # every save is an audit entry nobody reads. `!=` and not `is not`, because the
            # threshold is an int and two equal ints are not promised to be one object.
            if current != value:
                changes[field] = {"da": current, "a": value}
                setattr(row, field, value)

        if changes:
            # Last thing that touches the session before the commit, as
            # `ActivityService.record`'s docstring requires.
            self.activities.record(ENTITY, row.id, KIND_CONFIG_CHANGED, actor, changes)
        self.session.commit()
        return AutomationConfigRead.model_validate(row)
