"""What the automations expose, and the four reasons one may decline to run.

The `motivo` values are a closed `Literal` in Python and a JSONB value in the database --
not a sized `String` column. A `String(24)` sized exactly to `stage_bersaglio_ambiguo`
would be a column sized precisely to its legal set, which is the defect slice 3 just fixed
on `invoices.tipo`: the width rejects a wrong value before the CHECK beside it can, and
Postgres answers with a raw truncation error instead of the named violation.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# New `kind` values. No migration: `activities.kind` is an open value by project
# (slice 1 §5.8) and `ActivityService.record` takes `kind: str` and truncates to the
# column width without consulting any Literal.
KIND_STAGE_MOVED = "automazione.stage_spostato"
KIND_NOT_EXECUTED = "automazione.non_eseguita"
KIND_CONFIG_CHANGED = "automazione.configurazione_modificata"
# One timeline entry per attivita the daily pass opens, on the attivita itself (REB-727).
KIND_ATTIVITA_OPENED = "automazione.attivita_aperta"
AUTOMATION_KINDS: tuple[str, ...] = (
    KIND_STAGE_MOVED,
    KIND_NOT_EXECUTED,
    KIND_CONFIG_CHANGED,
    KIND_ATTIVITA_OPENED,
)

# A1 and A2 move a deal when an offer changes state; the other three open an attivita from
# the daily pass and are named by the `attivita.regola` they write (slice 7 §6).
AutomationRule = Literal["A1", "A2"]
AttivitaRule = Literal["offerta_inviata_richiama", "deal_fermo", "fattura_scaduta"]
ATTIVITA_RULES: tuple[AttivitaRule, ...] = (
    "offerta_inviata_richiama",
    "deal_fermo",
    "fattura_scaduta",
)
AutomationRuleCode = AutomationRule | AttivitaRule

# The four declared conditions the runner absorbs. Anything not in this list propagates
# and rolls the trigger back (§9.3): a database that cannot write is not an automation
# that did not fire.
AutomationSkipReason = Literal[
    "stage_bersaglio_assente",
    "stage_bersaglio_ambiguo",
    "gia_nello_stato",
    "regola_disattivata",
]


class AutomationConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    a1_offerta_accettata_vince_deal: bool
    a2_offerta_inviata_avanza_deal: bool
    a3_offerta_inviata_richiama: bool
    a4_deal_fermo: bool
    a4_deal_fermo_giorni: int
    a5_fattura_scaduta: bool


class AutomationConfigUpdate(BaseModel):
    """Both optional, and read with `exclude_unset=True`.

    With two booleans, `None` and "not sent" must be distinguishable: `exclude_none` would
    make "switch A1 off without mentioning A2" indistinguishable from "switch A1 off and
    A2 on". This is residuo A14's shape, avoided rather than inherited -- there is no
    nullable typed column here, so no spelling of "clear it" is needed at all.

    `extra="forbid"`, because the only thing a misspelled key can do on a settings form is
    report success for a change that was not made.
    """

    model_config = ConfigDict(extra="forbid")

    a1_offerta_accettata_vince_deal: bool | None = None
    a2_offerta_inviata_avanza_deal: bool | None = None
    a3_offerta_inviata_richiama: bool | None = None
    a4_deal_fermo: bool | None = None
    # The same bounds as the table's CHECK, so a value outside them is a 422 with the
    # field's name and not a database error.
    a4_deal_fermo_giorni: int | None = Field(default=None, ge=1, le=365)
    a5_fattura_scaduta: bool | None = None


class AutomationRuleDescription(BaseModel):
    codice: AutomationRuleCode
    titolo: str
    descrizione: str
    attiva: bool


class AutomationRun(BaseModel):
    kind: str
    occurred_at: datetime
    deal_id: UUID | None
    regola: str | None
    motivo: str | None
    payload: dict[str, Any]


class AutomationsDescription(BaseModel):
    configurazione: AutomationConfigRead
    regole: list[AutomationRuleDescription]
    esecuzioni: list[AutomationRun]


# The prose an agent and a human both read. Here rather than in the service so the two
# surfaces cannot drift, and in Italian like every other user-facing string.
RULE_DESCRIPTIONS: dict[AutomationRuleCode, tuple[str, str]] = {
    "A1": (
        "Offerta accettata → deal vinto",
        "Quando un'offerta passa da «inviata» ad «accettata», il deal collegato viene "
        "spostato nello stato con code «vinto», o nell'unico stato di tipo «won». Se lo "
        "stato manca o ce ne sono due, l'automazione non indovina: non fa nulla e "
        "registra il motivo.",
    ),
    "A2": (
        "Offerta inviata → il deal avanza",
        "Quando un'offerta passa da «bozza» a «inviata», il deal collegato avanza allo "
        "stato con code «offerta», ma solo se la sua posizione attuale è precedente. Non "
        "torna mai indietro: un deal già in negoziazione non retrocede perché è stata "
        "mandata una seconda offerta.",
    ),
    "offerta_inviata_richiama": (
        "Offerta senza risposta → un'attività per richiamare",
        "Una volta al giorno: per ogni offerta inviata da almeno sette giorni e ancora in "
        "attesa (non oltre trenta: dopo, richiamare non è più il seguito dell'invio), apre "
        "«Richiamare {cliente} sull'offerta {titolo}», con scadenza sette giorni dopo "
        "l'invio, al titolare del deal. Una sola finché è aperta.",
    ),
    "deal_fermo": (
        "Deal fermo → un'attività per riprenderlo",
        "Una volta al giorno: per ogni deal aperto che resta nello stesso stato da più "
        "giorni della soglia sotto, apre «Il deal {nome} è fermo da {n} giorni», con "
        "scadenza oggi, al titolare del deal. Una sola finché è aperta.",
    ),
    "fattura_scaduta": (
        "Fattura scaduta → un'attività per sollecitare",
        "Una volta al giorno: per ogni fattura emessa, non incassata e oltre la scadenza, "
        "apre «Sollecitare {cliente} per {numero}», con scadenza oggi, al titolare del "
        "deal della fattura. Il sollecito resta un atto a parte: da qui si arriva alla sua "
        "pagina, ma non parte nessuna richiesta di denaro da sola. Una sola finché è aperta.",
    ),
}
