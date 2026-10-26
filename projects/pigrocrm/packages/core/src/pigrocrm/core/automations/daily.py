"""The daily automation pass: the rules of slice 7 §6 that fire with time (REB-727, REB-728).

`AutomationRunner` reacts to an offer changing state, inside that change's transaction. A
rule like «an offer sent a week ago has had no answer» has no change to react to: it is a
fact that becomes true on a day nobody touched anything. So these rules are **evaluated, not
triggered**: once a day, per space, `pigrocrm automations` (the host's crontab, like the
digest and the Gmail sync) asks each switched-on rule which subjects match today and opens
one attivita for each that has none open. The runner and this pass share the configuration
row and nothing else; there is one place a rule is switched off.

**Idempotent by the database.** `uq_attivita_regola_aperta` allows one open, live attivita
per `(regola, subject)`. The pass reads which subjects already have one and skips them (so
the log can say «già aperta» instead of failing), and still inserts under a savepoint and
treats that index's violation as the same answer: two passes at the same instant both pass
the read, and only the index decides. Closing the attivita, or cancelling it, frees the
subject and the rule may open the next one on a later pass: «chase Rossi» closed without
Rossi paying means «I spoke to him», and a month on the reminder is needed again (§6.1).

**An automation-opened attivita and a reminder do not double up.** A reminder (spec
2026-10-26) is a notice about a cause, recomputed on every pass; an attivita is a to-do, with
a state. They are different objects and both exist, so the places where they would say the
same thing twice are decided here rather than left to happen:

* `fattura_scaduta` and the reminder `invoice_overdue` have the same cause (an issued, unpaid
  invoice past its due date, strictly) and the reminder already climbs its own ladder. The
  attivita is the to-do the reminder points at, so the reminder engine does not raise
  `attivita_due` or `attivita_overdue` for what this rule opened to a person who is already
  told of the invoice by `invoice_overdue`; to anybody else it is the only notice.
* `deal_fermo` and the reminder `deal_stale` measure different things: the reminder is the
  absence of any mail, attivita or hours for 21 days, the rule is a deal that has not changed
  stage. The attivita this rule opens is itself an attivita created on the deal, so it
  restarts `deal_stale`'s clock: the two never speak about the same stretch twice.
* `offerta_inviata_richiama` has no reminder of its own: the attivita is the only notice, so
  its `attivita_due` reminder is wanted.

**What it never does.** It sends nothing to anybody: the sollecito stays an act (slice 7 §9),
and an attivita is a row in somebody's list. It writes through no actor's authority: the
system opens them, `actor_type = 'system'` on the timeline entry, `origine = 'automazione'`
and `regola` on the row.

**Windows, and why they are there.** An offer is called back a week after it was sent, so
the one still waiting after thirty-seven days is not the follow-up of that sending any more;
without a limit, switching the rule on in a space with a year of old offers would open every
one of them on the first pass. The invoices have no such limit: an unpaid invoice past its
due date is what the receivables page lists, whatever its age.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.schemas import TITOLO_MAX_LENGTH
from pigrocrm.core.auth.models import User
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.repository import AutomationConfigRepository
from pigrocrm.core.automations.schemas import (
    ATTIVITA_RULES,
    KIND_ATTIVITA_OPENED,
    KIND_STAGE_MOVED,
    AttivitaRule,
)
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import today_local
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.pipeline.models import PipelineStage

# The offer is called back this many days after it was sent, and the attivita falls due then.
OFFERTA_ATTESA_GIORNI: Final = 7
# And stops being the follow-up of that sending this many days after the wait is over.
OFFERTA_FINESTRA_GIORNI: Final = 30

_ATTIVITA_ENTITY = "attivita"
_UNIQUE_INDEX = "uq_attivita_regola_aperta"
# The two timeline kinds that say a deal changed stage: a person's move and the automation
# A1/A2's. Both, because a deal moved by an accepted offer has changed stage all the same.
_STAGE_KINDS = ("stage_changed", KIND_STAGE_MOVED)
_SUBJECT_COLUMNS = {
    "deal_id": Attivita.deal_id,
    "customer_id": Attivita.customer_id,
    "invoice_id": Attivita.invoice_id,
}


@dataclass(frozen=True)
class RuleReport:
    """What one rule did in one space, as three counters that cannot be mistaken for each
    other: `disattivata` says the switch is off and nothing was looked at, `gia_aperte` the
    subjects that matched and already had their attivita, `aperte` the ones opened now."""

    regola: AttivitaRule
    disattivata: bool
    aperte: int
    gia_aperte: int


@dataclass(frozen=True)
class PassReport:
    rules: tuple[RuleReport, ...]

    @property
    def aperte(self) -> int:
        return sum(report.aperte for report in self.rules)


@dataclass(frozen=True)
class _Candidate:
    """One subject a rule matched today, with everything the attivita is made of."""

    subject_field: str
    subject_id: UUID
    titolo: str
    scadenza: date
    owner_id: UUID | None


def _titolo(text: str) -> str:
    return text if len(text) <= TITOLO_MAX_LENGTH else text[: TITOLO_MAX_LENGTH - 1] + "…"


class AutomationPass:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings if settings is not None else get_settings()
        self.config = AutomationConfigRepository(session)
        self.activities = ActivityService(session)

    def run(self, *, today: date | None = None, dry_run: bool = False) -> PassReport:
        """Every switched-on rule, once. Commits after each rule, so one that fails does not
        take the others' attivita with it. `dry_run` does the same reads and writes inside a
        savepoint and rolls that back, which is what makes the rehearsal's counters the real
        ones and leaves nothing behind, the configuration row's first creation included."""
        day = today if today is not None else today_local(self.settings)
        # The pass sees the whole space, like the reminder pass and the digest: the
        # application role reads through row-level policies that treat a session with no
        # scope as one that may see nothing, and a pass that saw nothing would print zeros
        # and exit 0 (REB-634). Who is told what is the reminders' business, not this one's.
        bind_scope(self.session, Actor.system())
        if not dry_run:
            return self._run_all(day, dry_run=False)
        rehearsal = self.session.begin_nested()
        try:
            return self._run_all(day, dry_run=True)
        finally:
            rehearsal.rollback()

    def _run_all(self, day: date, *, dry_run: bool) -> PassReport:
        config = self.config.get_or_create()
        if not dry_run:
            # `get_or_create` may have inserted the row: committed before the first rule so
            # that a rule's own failure cannot take it away.
            self.session.commit()
        return PassReport(
            rules=tuple(
                self._run_rule(rule, config, day, dry_run=dry_run) for rule in ATTIVITA_RULES
            )
        )

    # -- one rule -----------------------------------------------------------------

    def _run_rule(
        self, rule: AttivitaRule, config: AutomationConfig, today: date, *, dry_run: bool
    ) -> RuleReport:
        if not self._enabled(rule, config):
            return RuleReport(rule, True, 0, 0)
        candidates = self._candidates(rule, config, today)
        already = self._already_open(rule)
        active = self._active_users({c.owner_id for c in candidates if c.owner_id is not None})
        opened = skipped = 0
        for candidate in candidates:
            if candidate.subject_id in already:
                skipped += 1
                continue
            if self._open(rule, candidate, active):
                opened += 1
            else:
                skipped += 1
        if not dry_run:
            self.session.commit()
        return RuleReport(rule, False, opened, skipped)

    @staticmethod
    def _enabled(rule: AttivitaRule, config: AutomationConfig) -> bool:
        match rule:
            case "offerta_inviata_richiama":
                return config.a3_offerta_inviata_richiama
            case "deal_fermo":
                return config.a4_deal_fermo
            case "fattura_scaduta":
                return config.a5_fattura_scaduta

    def _candidates(
        self, rule: AttivitaRule, config: AutomationConfig, today: date
    ) -> list[_Candidate]:
        match rule:
            case "offerta_inviata_richiama":
                return self._offerte(today)
            case "deal_fermo":
                return self._deal_fermi(config.a4_deal_fermo_giorni, today)
            case "fattura_scaduta":
                return self._fatture_scadute(today)

    def _already_open(self, rule: AttivitaRule) -> set[UUID]:
        """The subjects with an open, live attivita of this rule: the reading half of the
        idempotency, which the index backs."""
        subject = func.coalesce(
            Attivita.customer_id, Attivita.person_id, Attivita.deal_id, Attivita.invoice_id
        )
        return set(
            self.session.scalars(
                select(subject).where(
                    Attivita.regola == rule,
                    Attivita.stato == "aperta",
                    Attivita.deleted_at.is_(None),
                )
            )
        )

    def _active_users(self, ids: set[UUID]) -> set[UUID]:
        """An attivita cannot be assigned to a disabled account (`AttivitaService`), and the
        pass would be the one writer to break that: a rule's attivita for a deal whose owner
        left goes to «whoever opens the list» instead."""
        if not ids:
            return set()
        return set(self.session.scalars(select(User.id).where(User.id.in_(ids), User.attivo)))

    def _open(self, rule: AttivitaRule, candidate: _Candidate, active: set[UUID]) -> bool:
        owner = candidate.owner_id if candidate.owner_id in active else None
        row = Attivita(
            titolo=candidate.titolo,
            scadenza=candidate.scadenza,
            stato="aperta",
            origine="automazione",
            regola=rule,
            assegnata_a=owner,
            custom_fields={},
            **{candidate.subject_field: candidate.subject_id},
        )
        try:
            # A savepoint, so the loser of a race loses only its own row and the pass goes
            # on with the next subject in the same transaction.
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != _UNIQUE_INDEX:
                raise
            return False
        self.activities.record(
            _ATTIVITA_ENTITY,
            row.id,
            KIND_ATTIVITA_OPENED,
            Actor.system(),
            {
                "regola": rule,
                "titolo": row.titolo,
                "scadenza": row.scadenza,
                candidate.subject_field: str(candidate.subject_id),
            },
        )
        return True

    # -- the three rules' queries ---------------------------------------------------

    def _offerte(self, today: date) -> list[_Candidate]:
        """Offers sent at least seven days ago, still waiting, and not older than thirty
        days past that. The attivita hangs on the offer's deal, or on its customer when the
        offer has none, and falls due seven days after the sending: if the pass is late, it
        is born late, which is the truth."""
        sent_before = today - timedelta(days=OFFERTA_ATTESA_GIORNI)
        sent_after = sent_before - timedelta(days=OFFERTA_FINESTRA_GIORNI)
        rows = self.session.execute(
            select(
                Document.titolo,
                Document.stato_dal,
                Document.deal_id,
                Customer.id,
                Customer.ragione_sociale,
                func.coalesce(Deal.owner_id, Customer.owner_id),
            )
            .outerjoin(Deal, Deal.id == Document.deal_id)
            .join(Customer, Customer.id == func.coalesce(Document.customer_id, Deal.customer_id))
            .where(
                Document.deleted_at.is_(None),
                Document.tipo == "offerta",
                Document.stato == "inviata",
                Document.stato_dal.is_not(None),
                Document.stato_dal <= sent_before,
                Document.stato_dal >= sent_after,
                or_(Document.deal_id.is_(None), Deal.deleted_at.is_(None)),
                Customer.deleted_at.is_(None),
            )
            .order_by(Document.stato_dal, Document.id)
        ).all()
        found: list[_Candidate] = []
        for titolo, stato_dal, deal_id, customer_id, cliente, owner_id in rows:
            assert stato_dal is not None  # the query asked for dated ones only
            field, subject = (
                ("deal_id", deal_id) if deal_id is not None else ("customer_id", customer_id)
            )
            found.append(
                _Candidate(
                    field,
                    subject,
                    _titolo(f"Richiamare {cliente} sull'offerta {titolo}"),
                    stato_dal + timedelta(days=OFFERTA_ATTESA_GIORNI),
                    owner_id,
                )
            )
        return found

    def _deal_fermi(self, soglia: int, today: date) -> list[_Candidate]:
        """Open deals that have been in their stage for at least `soglia` days.

        «Since when» is the latest stage change on the deal's timeline, a person's or an
        automation's, and the deal's creation when it never moved. Not `updated_at`: a deal
        somebody keeps editing and never advances is the case the rule is for."""
        zone = ZoneInfo(self.settings.timezone)
        moved = {
            entity_id: last
            for entity_id, last in self.session.execute(
                select(Activity.entity_id, func.max(Activity.occurred_at))
                .where(Activity.entity_type == "deal", Activity.kind.in_(_STAGE_KINDS))
                .group_by(Activity.entity_id)
            )
        }
        rows = self.session.execute(
            select(
                Deal.id,
                Deal.nome,
                func.coalesce(Deal.owner_id, Customer.owner_id),
                Deal.created_at,
            )
            .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
            .join(Customer, Customer.id == Deal.customer_id)
            .where(
                Deal.deleted_at.is_(None),
                PipelineStage.tipo == "open",
                Customer.deleted_at.is_(None),
            )
            .order_by(Deal.created_at, Deal.id)
        ).all()
        found: list[_Candidate] = []
        for deal_id, nome, owner_id, created_at in rows:
            since: datetime = moved.get(deal_id) or created_at
            days = (today - since.astimezone(zone).date()).days
            if days < soglia:
                continue
            found.append(
                _Candidate(
                    "deal_id",
                    deal_id,
                    _titolo(f"Il deal {nome} è fermo da {days} giorni"),
                    today,
                    owner_id,
                )
            )
        return found

    def _fatture_scadute(self, today: date) -> list[_Candidate]:
        """Issued invoices nobody has paid, strictly past their due date: the receivables
        page's «scaduto», and the reminder `invoice_overdue`'s cause. Assigned to the owner
        of the invoice's deal, or of its customer when it has no deal."""
        rows = self.session.execute(
            select(
                Invoice.id,
                Invoice.anno,
                Invoice.numero,
                Customer.ragione_sociale,
                func.coalesce(Deal.owner_id, Customer.owner_id),
            )
            .join(Customer, Customer.id == Invoice.customer_id)
            .outerjoin(Deal, Deal.id == Invoice.deal_id)
            .where(
                Invoice.deleted_at.is_(None),
                Invoice.tipo == "fattura",
                Invoice.stato == "emessa",
                Invoice.stato_pagamento == "da_incassare",
                Invoice.data_scadenza.is_not(None),
                Invoice.data_scadenza < today,
                Customer.deleted_at.is_(None),
            )
            .order_by(Invoice.data_scadenza, Invoice.id)
        ).all()
        found: list[_Candidate] = []
        for invoice_id, anno, numero, cliente, owner_id in rows:
            label = (
                numero_completo(anno, numero)
                if anno is not None and numero is not None
                else "la fattura"
            )
            found.append(
                _Candidate(
                    "invoice_id",
                    invoice_id,
                    _titolo(f"Sollecitare {cliente} per {label}"),
                    today,
                    owner_id,
                )
            )
        return found
