import re
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.models import PARTITA_IVA_WIDTH, Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.schemas import (
    NOME_MAX_LENGTH,
    TEMPLATE_EXCLUDED_FIELDS,
    AziendaRead,
    AziendaUpsert,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed

# The error label and the timeline entity keep the table's name (spec 2026-10-03 §10):
# every row already written under it stays readable as history.
ENTITY = "emitter_profile"
# `.fullmatch()`, not `.match()`: `$` matches before a trailing newline, so
# "01234567890\n" -- 12 characters, one more than the String(11) column -- would pass
# a `.match()` check and reach flush() as a raw, session-poisoning DataError. The same
# defect this project has already paid for once on `customers.partita_iva`.
PARTITA_IVA_RE = re.compile(r"\d{11}")
# The column's width (`emitter/models.py`). A foreign VAT number keeps its own shape
# through `normalise_foreign_fiscal_id`, and one longer than the column would reach the
# flush as a session-poisoning DataError, so it is refused here in words.
PARTITA_IVA_MAX_LENGTH = PARTITA_IVA_WIDTH
CODICE_SDI_LENGTH = 7
DEFAULT_LABEL = "predefinita"


def _normalise_ids(data: dict[str, Any]) -> None:
    """Store both fiscal ids in the shape the import classifier compares them in
    (spec §2 step 1), so the two unique indexes on `upper(...)` really mean «one
    azienda per code»: an Italian azienda through `normalise_fiscal_id`, which also
    strips an `IT` prefix and refuses anything that is not a P.IVA or a codice fiscale;
    a foreign one through `normalise_foreign_fiscal_id`, which keeps a nine-digit
    British VAT number instead of emptying it. A value either function empties is
    refused rather than saved as `NULL`: a code the person typed does not vanish.

    Imported lazily: `invoices.fatturapa` is the module that owns the two functions
    and importing it at module level from here would make the issuer's own module
    depend on the exporter's at import time.
    """
    from pigrocrm.core.invoices.fatturapa import (
        normalise_fiscal_id,
        normalise_foreign_fiscal_id,
    )

    normalise = (
        normalise_fiscal_id if data.get("nazione", "IT") == "IT" else normalise_foreign_fiscal_id
    )
    for field, expected in (
        ("partita_iva", "11 cifre numeriche"),
        ("codice_fiscale", "16 caratteri"),
    ):
        raw = data.get(field)
        if not raw:
            continue
        cleaned = normalise(raw)
        if cleaned is None:
            raise ValidationFailed(
                ENTITY, field, "non e' un identificativo fiscale valido", expected=expected
            )
        data[field] = cleaned


def _check_fiscal(data: dict[str, Any]) -> None:
    # The country code upper-cased before anything reads it: the exporter compares it
    # that way (`fatturapa.py`), and a lower-case «it» must not slip an Italian P.IVA
    # past the Italian checks by looking foreign.
    data["nazione"] = (data.get("nazione") or "IT").strip().upper()
    for field in ("partita_iva", "codice_sdi", "codice_fiscale"):
        if data.get(field) == "":
            data[field] = None
    _normalise_ids(data)
    piva = data.get("partita_iva")
    if piva and data.get("nazione", "IT") == "IT" and not PARTITA_IVA_RE.fullmatch(piva):
        raise ValidationFailed(
            ENTITY, "partita_iva", "deve essere di 11 cifre", expected="11 cifre numeriche"
        )
    if piva and len(piva) > PARTITA_IVA_MAX_LENGTH:
        raise ValidationFailed(
            ENTITY,
            "partita_iva",
            f"una partita IVA estera non supera i {PARTITA_IVA_MAX_LENGTH} caratteri",
            expected=f"al massimo {PARTITA_IVA_MAX_LENGTH} caratteri",
        )
    sdi = data.get("codice_sdi")
    if sdi and len(sdi) != CODICE_SDI_LENGTH:
        raise ValidationFailed(
            ENTITY, "codice_sdi", "deve essere di 7 caratteri", expected="7 caratteri"
        )
    if not data.get("nome"):
        data["nome"] = data["ragione_sociale"][:NOME_MAX_LENGTH]


class AziendaService:
    """The aziende of a space, and «the» azienda for every caller that has one.

    Every read takes an optional `azienda_id`; `None` means the default, which on a
    space with one azienda is the only one, so a consumer written for the single
    emitter keeps working unchanged. There is deliberately no `create` here before
    the milestone that can number, own and render for a second azienda (spec §9): the
    first row of a space is written by `upsert_default`, from provisioning and from
    `ensure_defaults`, and nothing else can add one.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AziendaRepository(session)
        self.activities = ActivityService(session)

    # -- reads ---------------------------------------------------------------

    def list(self, actor: Actor, *, only_active: bool = True) -> list[AziendaRead]:
        return [AziendaRead.model_validate(row) for row in self.repo.list(only_active=only_active)]

    def propose(self, nazione: str | None) -> Azienda:
        """The azienda a new customer of `nazione` is billed by when the caller names
        none (REB-623, spec 2026-10-03 §1.6): the one active azienda of that nation when
        exactly one has it; else, for a customer outside Italy, the one active azienda
        outside Italy when exactly one exists; else the default. The nation never
        decides between two aziende that share it: an Italian customer in a space with a
        forfettario and an SRL, both Italian, gets the default proposed and the person
        picks. `NotFound("emitter_profile", "predefinita")` on a space with no azienda.
        """
        paese = (nazione or "IT").strip().upper() or "IT"
        active = self.repo.list(only_active=True)
        same = [a for a in active if (a.nazione or "IT").upper() == paese]
        if len(same) == 1:
            return same[0]
        if paese != "IT":
            abroad = [a for a in active if (a.nazione or "IT").upper() != "IT"]
            if len(abroad) == 1:
                return abroad[0]
        return self.resolve(None)

    def inherited(self, azienda_id: UUID, entity: str) -> UUID:
        """The azienda a new deal, contract, document or invoice takes from its parent
        (REB-623, spec §1.7 and §3): the parent's, and never a deactivated one. The
        history of a closed azienda stays readable, but nothing new is born on it; the
        answer is to move the customer first, and the message says so. `entity` is the
        thing being created, so the refusal names the field of that request."""
        azienda = self.resolve(azienda_id)
        if not azienda.attiva:
            raise ValidationFailed(
                entity,
                "customer_id",
                "l'azienda del cliente non e' attiva: sposta prima il cliente su un'azienda attiva",
                expected="un cliente di un'azienda attiva",
            )
        return azienda.id

    def resolve(self, azienda_id: UUID | None = None) -> Azienda:
        """The row `azienda_id` names, or the default when it is `None`. `NotFound`
        either way when there is nothing to answer with, under the label every
        consumer already handles (`emitter_profile`, `predefinita`)."""
        row = self.repo.default() if azienda_id is None else self.repo.get(azienda_id)
        if row is None:
            raise NotFound(ENTITY, DEFAULT_LABEL if azienda_id is None else str(azienda_id))
        return row

    def get(self, actor: Actor, azienda_id: UUID | None = None) -> AziendaRead:
        return AziendaRead.model_validate(self.resolve(azienda_id))

    def as_template_values(self, actor: Actor, azienda_id: UUID | None = None) -> dict[str, Any]:
        """The azienda as a template scope, under the name `emittente`.

        This is what makes `{{emittente.ragione_sociale}}` work in a template and what
        replaced the hardcoded issuer data of the previous system's `header.typ`. The
        row's identity and state are excluded: they are storage, not a fact about the
        business (and `AziendaRead` already drops nothing else).
        """
        profile = self.get(actor, azienda_id)
        return {"emittente": profile.model_dump(mode="json", exclude=set(TEMPLATE_EXCLUDED_FIELDS))}

    # -- writes --------------------------------------------------------------

    def upsert_default(self, data: AziendaUpsert, actor: Actor) -> AziendaRead:
        """Create the space's first azienda as its default, or replace the default's
        fields: the one write a space with one azienda ever needs, and the only path
        that can insert a row before creation opens (spec §9, milestone 5).

        `repo.add` sits inside the `try`: it is the flush, and the flush is where the
        partial unique index on `predefinita` refuses a second default when two
        first-time saves race, which `Conflict` then reports instead of a raw,
        session-poisoning `IntegrityError`. The same shape, for the same reason, as
        the singleton this replaced (REB-615; the race is reproduced in
        `test_emitter.py`).
        """
        actor.require_admin("upsert_emitter_profile")
        payload = data.model_dump()
        _check_fiscal(payload)
        row = self.repo.default()
        try:
            if row is None:
                row = self.repo.add(Azienda(**payload, predefinita=True, attiva=True))
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": sorted(payload)})
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(ENTITY, self._conflict_reason(exc)) from exc
        return AziendaRead.model_validate(row)

    def update(self, azienda_id: UUID, data: AziendaUpsert, actor: Actor) -> AziendaRead:
        """Replace one azienda's fields. Whole-row, like every write on this table:
        a key left out goes back to its default, which is why the MCP tool tells the
        agent to read first and send the object back."""
        actor.require_admin("upsert_emitter_profile")
        payload = data.model_dump()
        _check_fiscal(payload)
        row = self.resolve(azienda_id)
        try:
            for key, value in payload.items():
                setattr(row, key, value)
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": sorted(payload)})
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                ENTITY, self._conflict_reason(exc, "i dati fiscali sono gia' di un'altra azienda")
            ) from exc
        return AziendaRead.model_validate(row)

    def set_default(self, azienda_id: UUID, actor: Actor) -> AziendaRead:
        """Move the default. One transaction, old row off first, so the partial unique
        index never sees two defaults; an inactive azienda cannot become the default,
        because the default is what every implicit read resolves to."""
        actor.require_admin("set_default_azienda")
        row = self.resolve(azienda_id)
        if not row.attiva:
            raise ValidationFailed(
                ENTITY,
                "predefinita",
                "un'azienda disattivata non puo' essere la predefinita",
                expected="un'azienda attiva",
            )
        current = self.repo.default()
        try:
            if current is not None and current.id != row.id:
                current.predefinita = False
                self.session.flush()
            row.predefinita = True
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": ["predefinita"]})
            self.session.commit()
        except IntegrityError as exc:
            # Two admins moving the default at once: the second flush meets the partial
            # unique index, or the check that keeps the default active meets a
            # deactivation that landed in between. A clean Conflict, not a 500.
            self.session.rollback()
            raise Conflict(ENTITY, "la predefinita e' cambiata nel frattempo: ricarica") from exc
        return AziendaRead.model_validate(row)

    def deactivate(self, azienda_id: UUID, actor: Actor) -> AziendaRead:
        """Switch an azienda off. Never a delete: an azienda that issued an invoice
        stays readable forever, and a row that is gone cannot be the `emittente` a
        snapshot was frozen from. The default is refused, since every implicit read
        resolves to it; move the default first."""
        actor.require_admin("deactivate_azienda")
        row = self.resolve(azienda_id)
        if row.predefinita:
            raise ValidationFailed(
                ENTITY,
                "attiva",
                "l'azienda predefinita non si puo' disattivare",
                expected="prima sposta la predefinita su un'altra azienda",
            )
        try:
            row.attiva = False
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": ["attiva"]})
            self.session.commit()
        except IntegrityError as exc:
            # `ck_emitter_profile_default_active`: a `set_default` that made this row the
            # default after the check above read it.
            self.session.rollback()
            raise Conflict(ENTITY, "l'azienda e' diventata la predefinita nel frattempo") from exc
        return AziendaRead.model_validate(row)

    @staticmethod
    def _conflict_reason(
        exc: IntegrityError, fallback: str = "l'azienda predefinita esiste gia'"
    ) -> str:
        text = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_emitter_profile_partita_iva" in text:
            return "la partita IVA e' gia' di un'altra azienda"
        if "uq_emitter_profile_codice_fiscale" in text:
            return "il codice fiscale e' gia' di un'altra azienda"
        return fallback
