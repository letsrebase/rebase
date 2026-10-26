from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.daily import AutomationPass, PassReport, RuleReport
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.runner import AutomationOutcome, AutomationRunner
from pigrocrm.core.automations.schemas import (
    ATTIVITA_RULES,
    AUTOMATION_KINDS,
    KIND_ATTIVITA_OPENED,
    KIND_CONFIG_CHANGED,
    KIND_NOT_EXECUTED,
    KIND_STAGE_MOVED,
    AttivitaRule,
    AutomationConfigRead,
    AutomationConfigUpdate,
    AutomationRule,
    AutomationRuleCode,
    AutomationRuleDescription,
    AutomationRun,
    AutomationsDescription,
    AutomationSkipReason,
)

__all__ = [
    "ATTIVITA_RULES",
    "AUTOMATION_KINDS",
    "KIND_ATTIVITA_OPENED",
    "KIND_CONFIG_CHANGED",
    "KIND_NOT_EXECUTED",
    "KIND_STAGE_MOVED",
    "AttivitaRule",
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
