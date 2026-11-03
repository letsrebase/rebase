"""What the automations expose, and the four reasons one may decline to run.

The `reason` values are a closed `Literal` in Python and a JSONB value in the database --
not a sized `String` column. A `String(24)` sized exactly to `target_stage_ambiguous`
would be a column sized precisely to its legal set, which is the defect slice 3 just fixed
on `invoices.type`: the width rejects a wrong value before the CHECK beside it can, and
Postgres answers with a raw truncation error instead of the named violation.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# New `kind` values. No migration: `activities.kind` is an open value by project
# (slice 1 §5.8) and `ActivityService.record` takes `kind: str` and truncates to the
# column width without consulting any Literal.
KIND_STAGE_MOVED = "automation.stage_moved"
KIND_NOT_EXECUTED = "automation.not_executed"
KIND_CONFIG_CHANGED = "automation.config_changed"
# One timeline entry per task the daily pass opens, on the task itself (REB-727).
KIND_TASK_OPENED = "automation.task_opened"
AUTOMATION_KINDS: tuple[str, ...] = (
    KIND_STAGE_MOVED,
    KIND_NOT_EXECUTED,
    KIND_CONFIG_CHANGED,
    KIND_TASK_OPENED,
)

# A1 and A2 move a deal when an offer changes state; the other three open an task from
# the daily pass and are named by the `tasks.rule` they write (slice 7 §6).
AutomationRule = Literal["A1", "A2"]
TaskRule = Literal["sent_offer_follow_up", "stalled_deal", "overdue_invoice"]
TASK_RULES: tuple[TaskRule, ...] = (
    "sent_offer_follow_up",
    "stalled_deal",
    "overdue_invoice",
)
AutomationRuleCode = AutomationRule | TaskRule
# The rule whose cause a reminder already raises (`invoice_overdue`): the task reminders
# skip what it opened, so one late invoice is not announced twice. See `daily.py`.
RULE_COVERED_BY_REMINDER: TaskRule = "overdue_invoice"

# The four declared conditions the runner absorbs. Anything not in this list propagates
# and rolls the trigger back (§9.3): a database that cannot write is not an automation
# that did not fire.
AutomationSkipReason = Literal[
    "target_stage_missing",
    "target_stage_ambiguous",
    "already_in_state",
    "rule_disabled",
]


class AutomationConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    a1_wins_accepted_offer_deal: bool
    a2_advances_sent_offer_deal: bool
    a3_sent_offer_follow_up: bool
    a4_stalled_deal: bool
    a4_stalled_deal_days: int
    a5_overdue_invoice: bool


class AutomationConfigUpdate(BaseModel):
    """Both optional, and read with `exclude_unset=True`.

    With two booleans, `None` and "not sent" must be distinguishable: `exclude_none` would
    make "switch A1 off without mentioning A2" indistinguishable from "switch A1 off and
    A2 on". This is the leftover shape of A14, avoided rather than inherited -- there is no
    nullable typed column here, so no spelling of "clear it" is needed at all.

    `extra="forbid"`, because the only thing a misspelled key can do on a settings form is
    report success for a change that was not made.
    """

    model_config = ConfigDict(extra="forbid")

    a1_wins_accepted_offer_deal: bool | None = None
    a2_advances_sent_offer_deal: bool | None = None
    a3_sent_offer_follow_up: bool | None = None
    a4_stalled_deal: bool | None = None
    # The same bounds as the table's CHECK, so a value outside them is a 422 with the
    # field's name and not a database error.
    a4_stalled_deal_days: int | None = Field(default=None, ge=1, le=365)
    a5_overdue_invoice: bool | None = None


class AutomationRuleDescription(BaseModel):
    code: AutomationRuleCode
    title: str
    description: str
    active: bool


class AutomationRun(BaseModel):
    kind: str
    occurred_at: datetime
    deal_id: UUID | None
    rule: str | None
    reason: str | None
    payload: dict[str, Any]


class AutomationsDescription(BaseModel):
    configuration: AutomationConfigRead
    rules: list[AutomationRuleDescription]
    runs: list[AutomationRun]


# The prose an agent and a human both read. Here rather than in the service so the two
# surfaces cannot drift, and in Italian like every other user-facing string.
RULE_DESCRIPTIONS: dict[AutomationRuleCode, tuple[str, str]] = {
    "A1": (
        "Offerta accettata → deal vinto",
        "Quando un'offerta passa da «inviata» ad «accettata», il deal collegato viene "
        "spostato nello stato con codice «vinto», o nell'unico stato di tipo «won». Se lo "
        "stato manca o ce ne sono due, l'automazione non indovina: non fa nulla e "
        "registra il motivo.",
    ),
    "A2": (
        "Offerta inviata → il deal avanza",
        "Quando un'offerta passa da «bozza» a «inviata», il deal collegato avanza allo "
        "stato con codice «offerta», ma solo se la sua posizione attuale è precedente. Non "
        "torna mai indietro: un deal già in negoziazione non retrocede perché è stata "
        "mandata una seconda offerta.",
    ),
    "sent_offer_follow_up": (
        "Offerta senza risposta → un'attività per richiamare",
        "Una volta al giorno: per ogni offerta inviata da almeno sette giorni e ancora in "
        "attesa (fino a trentasette: dopo, richiamare non è più il seguito dell'invio), apre "
        "«Richiamare {cliente} sull'offerta {titolo}», con scadenza sette giorni dopo "
        "l'invio, al titolare del deal. Una sola finché è aperta.",
    ),
    "stalled_deal": (
        "Deal fermo → un'attività per riprenderlo",
        "Una volta al giorno: per ogni deal aperto che resta nello stesso stato da almeno i "
        "giorni della soglia sotto, apre «Il deal {nome} è fermo da {n} giorni», con "
        "scadenza oggi, al titolare del deal. Una sola finché è aperta.",
    ),
    "overdue_invoice": (
        "Fattura scaduta → un'attività per sollecitare",
        "Una volta al giorno: per ogni fattura emessa, non incassata e oltre la scadenza, "
        "apre «Sollecitare {cliente} per {numero}», con scadenza oggi, al titolare del "
        "deal della fattura. Il sollecito resta un atto a parte: da qui si arriva alla sua "
        "pagina, ma non parte nessuna richiesta di denaro da sola. Una sola finché è aperta.",
    ),
}
