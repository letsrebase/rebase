"""What a reminder is: the types, their ladders, and the arithmetic that turns a measure
into a severity (spec 2026-10-26 §3, §5).

A **type** is one kind of reminder (`attivita_overdue`), a **subject** is the record it is
about, and the pair is the reminder's **key**, `type:subject-id`. Nothing here touches the
database: the catalogue is data and the severity is a pure function, which is what lets a
detector be tested on a date and the preference resolver be called from the detectors, the
dispatcher, the API and the MCP alike without any of them computing it a second way.

**Two directions of ladder.** A reminder climbs as a measure grows past a rung («days
overdue», «hours the timer has run») or as it shrinks below one («days before a contract
ends»). `since` is the first, `until` the second; thresholds must be strictly increasing
along the ladder's severity order for `since` and strictly decreasing for `until`, and
the settings refuse a save that breaks that, in Italian (`validate_ladder`).

**Timing and thresholds are one column.** Spec §5 lists «timing» (days) and «thresholds»
(ratios, hours) apart; both are the same thing, the numbers of a ladder, told apart only by
the type's `unit`, so a type stores one `ladder` and the unit says what its numbers mean:
days, hours, minutes (a job that ran a quarter of an hour ago), or a whole **percent** of a
limit (the ceiling's usage, 90 for 90%).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pigrocrm.core.errors import ValidationFailed

Severity = Literal["warning", "serious", "critical"]
SEVERITIES: tuple[Severity, ...] = ("warning", "serious", "critical")
# Mastro's ranking, and the only thing acknowledge, snooze and the ledger compare: a
# reminder seen at rank 2 is not seen again at 1 or 2, and is at 3.
SEVERITY_RANK: Mapping[Severity, int] = {"warning": 1, "serious": 2, "critical": 3}

Channel = Literal["in_app", "push", "slack", "email"]
CHANNELS: tuple[Channel, ...] = ("in_app", "push", "slack", "email")
# The channels a person can switch. In-app is the source of truth and every other channel
# is a copy of it (spec §6.1), so it is always on and never a setting.
SWITCHABLE_CHANNELS: tuple[Channel, ...] = ("push", "slack", "email")

EmailMode = Literal["immediate", "summary"]
EMAIL_MODES: tuple[EmailMode, ...] = ("immediate", "summary")

Direction = Literal["since", "until"]
Unit = Literal["days", "hours", "minutes", "percent"]
Audience = Literal["subject", "admins"]
Ladder = dict[Severity, int]

# The default urgent step of every type (spec §3.1). A type with no `critical` rung is
# never urgent unless somebody lowers it.
DEFAULT_URGENT_FROM: Severity = "critical"

LADDER_MAX = 100_000


@dataclass(frozen=True)
class ReminderType:
    """One kind of reminder, as the code and the settings both read it.

    `subject` names the record the key's id belongs to (the web turns it into a link);
    `daily` is for the types delivered once a day while they hold (`attivita_overdue`,
    «every morning»), through the ledger's `period` column (spec §8.3); `audience` says
    whether the recipients come from the subject or are the space's admins.
    """

    key: str
    label: str
    description: str
    subject: str
    direction: Direction
    unit: Unit
    ladder: Mapping[Severity, int]
    audience: Audience = "subject"
    daily: bool = False


# The first group of the catalogue (spec §3.2, REB-733). The other groups add their rows
# here with their detectors, and nothing is added by being easy (spec §3.6).
REMINDER_TYPES: Mapping[str, ReminderType] = {
    kind.key: kind
    for kind in (
        ReminderType(
            key="attivita_due",
            label="Attività in scadenza",
            description="Un'attività aperta scade oggi, o fra i giorni di anticipo che scegli.",
            subject="attivita",
            direction="until",
            unit="days",
            ladder={"warning": 0},
        ),
        ReminderType(
            key="attivita_overdue",
            label="Attività scaduta",
            description=(
                "Un'attività aperta ha superato la sua scadenza: te lo ricorda ogni mattina."
            ),
            subject="attivita",
            direction="since",
            unit="days",
            ladder={"serious": 1, "critical": 14},
            daily=True,
        ),
        ReminderType(
            key="deal_past_close",
            label="Deal oltre la chiusura prevista",
            description="Un deal aperto ha superato la data di chiusura prevista.",
            subject="deal",
            direction="since",
            unit="days",
            ladder={"warning": 1, "serious": 14},
        ),
        ReminderType(
            key="deal_stale",
            label="Deal fermo",
            description="Un deal aperto non ha né mail, né attività, né ore da diverso tempo.",
            subject="deal",
            direction="since",
            unit="days",
            ladder={"warning": 21},
        ),
        ReminderType(
            key="timer_running_long",
            label="Timer lasciato acceso",
            description="Un timer è in corso da molte ore: forse ti sei dimenticato di fermarlo.",
            subject="timer",
            direction="since",
            unit="hours",
            ladder={"serious": 10, "critical": 24},
        ),
        ReminderType(
            key="invoice_overdue",
            label="Fattura scaduta",
            description="Una fattura emessa e non incassata ha superato la data di scadenza.",
            subject="invoice",
            direction="since",
            unit="days",
            ladder={"warning": 1, "serious": 8, "critical": 30},
        ),
        ReminderType(
            key="invoice_not_transmitted",
            label="Fattura non trasmessa",
            description=(
                "Una fattura emessa non risulta trasmessa all'intermediario dopo qualche giorno."
            ),
            subject="invoice",
            direction="since",
            unit="days",
            ladder={"warning": 2, "serious": 3, "critical": 7},
        ),
        ReminderType(
            key="proforma_not_invoiced",
            label="Proforma non fatturata",
            description="Una proforma confermata non è ancora diventata una fattura.",
            subject="proforma",
            direction="since",
            unit="days",
            ladder={"warning": 7, "serious": 21},
        ),
        ReminderType(
            key="hours_unbilled",
            label="Ore da fatturare",
            description=(
                "Ci sono ore fatturabili di un deal che non sono su nessuna fattura emessa."
            ),
            subject="deal",
            direction="since",
            unit="days",
            ladder={"warning": 30, "serious": 60},
        ),
        ReminderType(
            key="billable_period_closed",
            label="Periodo chiuso da fatturare",
            description=(
                "Il periodo di fatturazione di un contratto è finito con giornate non fatturate."
            ),
            subject="contract",
            direction="since",
            unit="days",
            ladder={"warning": 0, "serious": 30, "critical": 60},
        ),
        ReminderType(
            key="fixed_fee_period_unbilled",
            label="Canone del periodo da fatturare",
            description=(
                "Il periodo di un contratto a canone fisso è finito senza la fattura del canone."
            ),
            subject="contract",
            direction="since",
            unit="days",
            ladder={"warning": 0, "serious": 30, "critical": 60},
        ),
        ReminderType(
            key="on_completion_unbilled",
            label="Contratto concluso da fatturare",
            description=(
                "Un contratto a fatturazione a completamento è concluso e non è stato fatturato."
            ),
            subject="contract",
            direction="since",
            unit="days",
            ladder={"warning": 0, "serious": 30, "critical": 60},
        ),
        ReminderType(
            key="invoice_days_unlinked",
            label="Fattura senza giornate collegate",
            description=(
                "Una fattura emessa non porta le giornate che il contratto ha ancora da fatturare."
            ),
            subject="invoice",
            direction="since",
            unit="days",
            ladder={"serious": 0},
        ),
        ReminderType(
            key="contract_expiring",
            label="Contratto in scadenza",
            description="Un contratto attivo con una data di fine si avvicina alla sua scadenza.",
            subject="contract",
            direction="until",
            unit="days",
            ladder={"warning": 60, "serious": 30, "critical": 14},
        ),
        ReminderType(
            key="renewal_window_open",
            label="Finestra di rinnovo aperta",
            description=(
                "Il termine per rinnovare o dare disdetta si avvicina: i giorni mancano alla "
                "chiusura della finestra. «Avviso» è quando la finestra si apre."
            ),
            subject="contract",
            direction="until",
            unit="days",
            ladder={"warning": 30, "serious": 14, "critical": 7},
        ),
        ReminderType(
            key="worked_without_approval",
            label="Giornate lavorate senza approvazione",
            description=(
                "Una giornata è stata lavorata su un contratto che richiede l'approvazione "
                "preventiva e non ne ha una: i giorni contano dalla giornata lavorata."
            ),
            subject="work_unit",
            direction="since",
            unit="days",
            ladder={"serious": 0, "critical": 3},
        ),
        ReminderType(
            key="approval_unanswered",
            label="Approvazione senza risposta",
            description=(
                "Una giornata proposta su un contratto che richiede l'approvazione aspetta "
                "ancora la risposta del cliente."
            ),
            subject="work_unit",
            direction="since",
            unit="days",
            ladder={"warning": 5, "serious": 10, "critical": 20},
        ),
        ReminderType(
            key="approval_requirement_unmet",
            label="Approvazione non conforme",
            description=(
                "Una giornata poggia su un'approvazione che non rispetta il contratto: "
                "arrivata dopo la giornata, o data per un altro contratto."
            ),
            subject="work_unit",
            direction="since",
            unit="days",
            ladder={"serious": 0},
        ),
        ReminderType(
            key="work_unit_outside_term",
            label="Giornata fuori dal periodo del contratto",
            description="Una giornata è registrata prima dell'inizio o dopo la fine del contratto.",
            subject="work_unit",
            direction="since",
            unit="days",
            ladder={"serious": 0},
        ),
        ReminderType(
            key="ceiling_approaching",
            label="Soglia del forfettario vicina",
            description=(
                "I ricavi incassati dell'anno si avvicinano alla soglia di ricavi del regime "
                "forfettario: la soglia è in percentuale."
            ),
            subject="fiscal",
            direction="since",
            unit="percent",
            ladder={"warning": 80, "serious": 90, "critical": 100},
            audience="admins",
        ),
        ReminderType(
            key="year_end_overrun_projected",
            label="Soglia del forfettario superata a fine anno",
            description=(
                "La proiezione dell'anno (incassato, da incassare e bozze) supera la soglia di "
                "ricavi del regime forfettario: la soglia è in percentuale."
            ),
            subject="fiscal",
            direction="since",
            unit="percent",
            ladder={"serious": 105, "critical": 120},
            audience="admins",
        ),
        ReminderType(
            key="month_not_closed",
            label="Mese non chiuso",
            description=(
                "Un mese è finito da giorni e non è stato chiuso, in uno spazio che chiude i mesi."
            ),
            subject="period",
            direction="since",
            unit="days",
            ladder={"warning": 10, "serious": 20},
            audience="admins",
        ),
        ReminderType(
            key="proposal_pending",
            label="Proposta in attesa",
            description="Una proposta di un agente aspetta da giorni che qualcuno la decida.",
            subject="proposal",
            direction="since",
            unit="days",
            ladder={"warning": 3, "serious": 7, "critical": 14},
            audience="admins",
        ),
        ReminderType(
            key="connection_failing",
            label="Collegamento Google in difficoltà",
            description=(
                "Gmail o Drive non funzionano: il consenso è stato revocato o è scaduto, "
                "oppure la sincronizzazione di Gmail è ferma da ore."
            ),
            subject="google_connection",
            direction="since",
            unit="hours",
            ladder={"serious": 3, "critical": 72},
            audience="admins",
        ),
        ReminderType(
            key="job_late",
            label="Operazione pianificata in ritardo",
            description=(
                "Una operazione che gira da sola (la sincronizzazione di Gmail, il passaggio dei "
                "promemoria) non parte da troppo tempo: i minuti sono dall'ultima esecuzione."
            ),
            subject="job",
            direction="since",
            unit="minutes",
            ladder={"serious": 30, "critical": 180},
            audience="admins",
        ),
    )
}


def reminder_key(type_key: str, subject_id: object) -> str:
    """`type:subject-id`, the identity of one occurrence and what acknowledge, snooze and
    the ledger are keyed by."""
    return f"{type_key}:{subject_id}"


def type_of_key(key: str) -> str:
    return key.partition(":")[0]


def get_type(type_key: str) -> ReminderType:
    try:
        return REMINDER_TYPES[type_key]
    except KeyError:
        raise ValidationFailed(
            "reminder",
            "type",
            f"tipo di promemoria sconosciuto: {type_key}",
            expected=", ".join(REMINDER_TYPES),
        ) from None


def severity_for(
    kind: ReminderType, measure: float, ladder: Mapping[Severity, int]
) -> Severity | None:
    """The highest rung `measure` has reached on `ladder`, or `None` below the first.

    `since`: a rung is reached when the measure is at or past it; `until`: when the
    measure is at or under it. A valid ladder is monotonic along the severity order, so
    the highest rung reached is the last one that holds.
    """
    reached: Severity | None = None
    for severity in SEVERITIES:
        threshold = ladder.get(severity)
        if threshold is None:
            continue
        holds = measure >= threshold if kind.direction == "since" else measure <= threshold
        if holds:
            reached = severity
    return reached


def validate_ladder(kind: ReminderType, ladder: Mapping[str, int]) -> Ladder:
    """A ladder the settings may store: some rungs of the three severities, each a whole
    number from 0, strictly ordered the way the type climbs. Refused in Italian, by field,
    so the settings page puts the sentence under the right control (spec §5)."""
    if not ladder:
        raise ValidationFailed(
            "reminder", "ladder", "serve almeno una soglia", expected="almeno una soglia"
        )
    clean: Ladder = {}
    for severity, threshold in ladder.items():
        if severity not in SEVERITIES:
            raise ValidationFailed(
                "reminder",
                "ladder",
                f"gravità sconosciuta: {severity}",
                expected="warning, serious o critical",
            )
        if isinstance(threshold, bool) or not isinstance(threshold, int):
            raise ValidationFailed(
                "reminder", "ladder", "le soglie sono numeri interi", expected="un intero"
            )
        if not 0 <= threshold <= LADDER_MAX:
            raise ValidationFailed(
                "reminder",
                "ladder",
                f"soglia fuori intervallo: {threshold}",
                expected=f"0-{LADDER_MAX}",
            )
        clean[severity] = threshold
    previous: tuple[Severity, int] | None = None
    for severity in SEVERITIES:
        if severity not in clean:
            continue
        threshold = clean[severity]
        if previous is not None:
            ordered = (
                threshold > previous[1] if kind.direction == "since" else threshold < previous[1]
            )
            if not ordered:
                sense = "più alta" if kind.direction == "since" else "più bassa"
                raise ValidationFailed(
                    "reminder",
                    "ladder",
                    f"la soglia «{severity}» deve essere {sense} di «{previous[0]}»",
                    expected="soglie in ordine di gravità",
                )
        previous = (severity, threshold)
    return clean
