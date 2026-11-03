from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.daily import AutomationPass, PassReport, RuleReport
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.runner import AutomationOutcome, AutomationRunner
from pigrocrm.core.automations.schemas import (
    AUTOMATION_KINDS,
    KIND_CONFIG_CHANGED,
    KIND_NOT_EXECUTED,
    KIND_STAGE_MOVED,
    KIND_TASK_OPENED,
    TASK_RULES,
    AutomationConfigRead,
    AutomationConfigUpdate,
    AutomationRule,
    AutomationRuleCode,
    AutomationRuleDescription,
    AutomationRun,
    AutomationsDescription,
    AutomationSkipReason,
    TaskRule,
)

__all__ = [
    "TASK_RULES",
    "AUTOMATION_KINDS",
    "KIND_TASK_OPENED",
    "KIND_CONFIG_CHANGED",
    "KIND_NOT_EXECUTED",
    "KIND_STAGE_MOVED",
    "TaskRule",
    "AutomationConfig",
    "AutomationConfigRead",
    "AutomationConfigService",
    "AutomationConfigUpdate",
    "AutomationOutcome",
    "AutomationPass",
    "AutomationRule",
    "AutomationRuleCode",
    "AutomationRuleDescription",
    "AutomationRun",
    "AutomationRunner",
    "AutomationSkipReason",
    "AutomationsDescription",
    "PassReport",
    "RuleReport",
]
