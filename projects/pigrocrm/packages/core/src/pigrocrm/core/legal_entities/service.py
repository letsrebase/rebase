import re
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.fatturapa.normalise import (
    normalise_fiscal_id,
    normalise_foreign_fiscal_id,
)
from pigrocrm.core.legal_entities.models import VAT_NUMBER_WIDTH, LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.schemas import (
    NAME_MAX_LENGTH,
    TEMPLATE_EXCLUDED_FIELDS,
    LegalEntityCreate,
    LegalEntityDeactivated,
    LegalEntityRead,
    LegalEntityUpsert,
)
from pigrocrm.core.versioning import require_unchanged

# The error label and the timeline entity keep the table's name (spec 2026-10-03 §10):
# every row already written under it stays readable as history.
ENTITY = "legal_entity"
# `.fullmatch()`, not `.match()`: `$` matches before a trailing newline, so
# "01234567890\n" -- 12 characters, one more than the String(11) column -- would pass
# a `.match()` check and reach flush() as a raw, session-poisoning DataError. The same
# defect this project has already paid for once on `customers.vat_number`.
VAT_NUMBER_RE = re.compile(r"\d{11}")
# The column's width (`emitter/models.py`). A foreign VAT number keeps its own shape
# through `normalise_foreign_fiscal_id`, and one longer than the column would reach the
# flush as a session-poisoning DataError, so it is refused here in words.
VAT_NUMBER_MAX_LENGTH = VAT_NUMBER_WIDTH
SDI_CODE_LENGTH = 7
DEFAULT_LABEL = "default"


def _normalise_ids(data: dict[str, Any]) -> None:
    """Store both fiscal ids in the shape the import classifier compares them in
    (spec §2 step 1), so the two unique indexes on `upper(...)` really mean «one
    azienda per code»: an Italian legal entity through `normalise_fiscal_id`, which also
    strips an `IT` prefix and refuses anything that is not a P.IVA or a codice fiscale;
    a foreign one through `normalise_foreign_fiscal_id`, which keeps a nine-digit
    British VAT number instead of emptying it. A value either function empties is
    refused rather than saved as `NULL`: a code the person typed does not vanish.
    """
    normalise = (
        normalise_fiscal_id if data.get("country", "IT") == "IT" else normalise_foreign_fiscal_id
    )
    for field, expected in (
        ("vat_number", "11 cifre numeriche"),
        ("tax_code", "16 caratteri"),
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
    # that way (`fatturapa/normalise.py`), and a lower-case «it» must not slip an Italian P.IVA
    # past the Italian checks by looking foreign.
    data["country"] = (data.get("country") or "IT").strip().upper()
    for field in ("vat_number", "sdi_code", "tax_code"):
        if data.get(field) == "":
            data[field] = None
    _normalise_ids(data)
    vat_number = data.get("vat_number")
    if vat_number and data.get("country", "IT") == "IT" and not VAT_NUMBER_RE.fullmatch(vat_number):
        raise ValidationFailed(
            ENTITY, "vat_number", "deve essere di 11 cifre", expected="11 cifre numeriche"
        )
    if vat_number and len(vat_number) > VAT_NUMBER_MAX_LENGTH:
        raise ValidationFailed(
            ENTITY,
            "vat_number",
            f"una partita IVA estera non supera i {VAT_NUMBER_MAX_LENGTH} caratteri",
            expected=f"al massimo {VAT_NUMBER_MAX_LENGTH} caratteri",
        )
    sdi = data.get("sdi_code")
    if sdi and len(sdi) != SDI_CODE_LENGTH:
        raise ValidationFailed(
            ENTITY, "sdi_code", "deve essere di 7 caratteri", expected="7 caratteri"
        )
    if not data.get("name"):
        data["name"] = data["legal_name"][:NAME_MAX_LENGTH]


class LegalEntityService:
    """The legal entities of a space, and «the» legal entity for every caller that has one.

    Every read takes an optional `legal_entity_id`; `None` means the default, which on a
    space with one legal entity is the only one, so a consumer written for the single
    emitter keeps working unchanged. There is deliberately no `create` here before
    the milestone that can number, own and render for a second legal entity (spec §9): the
    first row of a space is written by `upsert_default`, from provisioning and from
    `ensure_defaults`, and nothing else can add one.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = LegalEntityRepository(session)
        self.activities = ActivityService(session)

    # -- reads ---------------------------------------------------------------

    def list(self, actor: Actor, *, only_active: bool = True) -> list[LegalEntityRead]:
        return [
            LegalEntityRead.model_validate(row) for row in self.repo.list(only_active=only_active)
        ]

    def propose(self, country: str | None) -> LegalEntity:
        """The legal entity a new customer of `country` is billed by when the caller names
        none (REB-623, spec 2026-10-03 §1.6): the one active legal entity of that nation when
        exactly one has it; else, for a customer outside Italy, the one active legal entity
        outside Italy when exactly one exists; else the default. The nation never
        decides between two legal entities that share it: an Italian customer in a space with a
        flat-rate regime and an SRL, both Italian, gets the default proposed and the person
        picks. `NotFound("emitter_profile", "predefinita")` on a space with no legal entity.
        """
        country_code = (country or "IT").strip().upper() or "IT"
        active = self.repo.list(only_active=True)
        same = [a for a in active if (a.country or "IT").upper() == country_code]
        if len(same) == 1:
            return same[0]
        if country_code != "IT":
            abroad = [a for a in active if (a.country or "IT").upper() != "IT"]
            if len(abroad) == 1:
                return abroad[0]
        return self.resolve(None)

    def inherited(self, legal_entity_id: UUID, entity: str) -> UUID:
        """The legal entity a new deal, contract, document or invoice takes from its parent
        (REB-623, spec §1.7 and §3): the parent's, and never a deactivated one. The
        history of a closed legal entity stays readable, but nothing new is born on it; the
        answer is to move the customer first, and the message says so. `entity` is the
        thing being created, so the refusal names the field of that request."""
        legal_entity = self.resolve(legal_entity_id)
        if not legal_entity.active:
            raise ValidationFailed(
                entity,
                "customer_id",
                "l'azienda del cliente non e' attiva: sposta prima il cliente su un'azienda attiva",
                expected="un cliente di un'azienda attiva",
            )
        return legal_entity.id

    def resolve(self, legal_entity_id: UUID | None = None) -> LegalEntity:
        """The row `legal_entity_id` names, or the default when it is `None`. `NotFound`
        either way when there is nothing to answer with, under the label every
        consumer already handles (`legal_entities`, `predefinita`)."""
        row = self.repo.default() if legal_entity_id is None else self.repo.get(legal_entity_id)
        if row is None:
            raise NotFound(
                ENTITY, DEFAULT_LABEL if legal_entity_id is None else str(legal_entity_id)
            )
        return row

    def single(self, legal_entity_id: UUID | None = None) -> LegalEntity | None:
        """The one legal entity a per-legal entity figure speaks for (REB-630, spec 2026-10-03
        §1.9): the row `legal_entity_id` names, or the only active legal entity when the caller
        named none, which is what keeps every read written for a one-legal entity space
        working unchanged. `None` when the space has several and none was named: the
        coefficients of a flat-rate regime and the arithmetic of an SRL do not add, so there
        is no figure to answer with. `NotFound` on a space with no legal entity at all, under
        the label every consumer already handles."""
        if legal_entity_id is not None:
            return self.resolve(legal_entity_id)
        active = self.repo.list(only_active=True)
        if len(active) == 1:
            return active[0]
        if not active:
            return self.resolve(None)
        return None

    def require_single(self, legal_entity_id: UUID | None, *, entity: str) -> LegalEntity:
        """`single`, or a refusal that names the parameter the caller has to add. The
        label is the caller's own (`analytics` for the estimate and the ceilings), since
        it is that request's field that is missing, not this table's."""
        row = self.single(legal_entity_id)
        if row is None:
            raise ValidationFailed(
                entity,
                "legal_entity_id",
                "lo spazio ha piu' di un'azienda: indica per quale calcolare",
                expected="l'id di un'azienda attiva",
            )
        return row

    def get(self, actor: Actor, legal_entity_id: UUID | None = None) -> LegalEntityRead:
        return LegalEntityRead.model_validate(self.resolve(legal_entity_id))

    def as_template_values(
        self, actor: Actor, legal_entity_id: UUID | None = None
    ) -> dict[str, Any]:
        """The legal entity as a template scope, under the name `emittente`.

        This is what makes `{{emittente.ragione_sociale}}` work in a template and what
        replaced the hardcoded issuer data of the previous system's `header.typ`. The
        row's identity and state are excluded: they are storage, not a fact about the
        business (and `LegalEntityRead` already drops nothing else).
        """
        profile = self.get(actor, legal_entity_id)
        return {"emittente": profile.model_dump(mode="json", exclude=set(TEMPLATE_EXCLUDED_FIELDS))}

    # -- writes --------------------------------------------------------------

    def upsert_default(self, data: LegalEntityUpsert, actor: Actor) -> LegalEntityRead:
        """Create the space's first legal entity as its default, or replace the default's
        fields: the one write a space with one legal entity ever needs, and the only path
        that can insert a row before creation opens (spec §9, milestone 5).

        `repo.add` sits inside the `try`: it is the flush, and the flush is where the
        partial unique index on `predefinita` refuses a second default when two
        first-time saves race, which `Conflict` then reports instead of a raw,
        session-poisoning `IntegrityError`. The same shape, for the same reason, as
        the singleton this replaced (REB-615; the race is reproduced in
        `test_emitter.py`).
        """
        actor.require_admin("upsert_legal_entities")
        # Never a column: the version travels beside the fields and is checked, when it
        # is checked, by `update`; the default's first save has no row to compare with.
        payload = data.model_dump(exclude={"updated_at"})
        _check_fiscal(payload)
        row = self.repo.default()
        try:
            if row is None:
                row = self.repo.add(LegalEntity(**payload, is_default=True, active=True))
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": sorted(payload)})
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(ENTITY, self._conflict_reason(exc)) from exc
        return LegalEntityRead.model_validate(row)

    def create(self, data: LegalEntityCreate, actor: Actor) -> LegalEntityRead:
        """A second legal entity, with its fiscal profile, in one transaction (REB-630, spec
        2026-10-03 §1.2, §3, §9 milestone 5). Active, and the default only when the
        space had none: the first legal entity of a space is what every implicit read
        resolves to, whichever route wrote it. Both payloads are checked before the
        first `INSERT`, so a profile the fiscal service would refuse never leaves an
        legal entity behind that `issue` could not use; the two rows then flush under one
        `try`, where the partial unique indexes on the P.IVA and the codice fiscale are
        the only things that can refuse them, and a refusal is a clean `Conflict`.

        `create_legal_entity` is the action's name for the agent gate, like
        `update_fiscal_profile` since ORB-188: setting a space up is not a fiscal act.
        The fiscal service is imported here, not at module level, because it imports
        this one for `resolve`.
        """
        from pigrocrm.core.fiscal.models import FiscalProfile
        from pigrocrm.core.fiscal.service import ENTITY as FISCAL_ENTITY
        from pigrocrm.core.fiscal.service import FiscalProfileService

        actor.require_unscoped_admin("create_legal_entity")
        payload = data.model_dump(exclude={"fiscal_profile"})
        _check_fiscal(payload)
        # The profile body is `FiscalProfileUpsert`, whose `updated_at` is a version and
        # not a column (REB-622); a row being created has none to check against.
        profile_data = data.fiscal_profile.model_dump(exclude={"updated_at"})
        FiscalProfileService.check(profile_data)
        try:
            row = self.repo.add(
                LegalEntity(**payload, is_default=self.repo.default() is None, active=True)
            )
            profile = FiscalProfile(**profile_data, legal_entity_id=row.id)
            self.session.add(profile)
            self.session.flush()
            self.activities.record(ENTITY, row.id, "created", actor, {"changed": sorted(payload)})
            self.activities.record(
                FISCAL_ENTITY, profile.id, "created", actor, {"changed": sorted(profile_data)}
            )
            self.session.commit()
        except IntegrityError as exc:
            # The two fiscal indexes are named by `_conflict_reason`; what is left is
            # the partial unique index on `predefinita`, two first creates on an empty
            # space racing for the default, which the fallback names.
            self.session.rollback()
            raise Conflict(ENTITY, self._conflict_reason(exc)) from exc
        return LegalEntityRead.model_validate(row)

    def update(
        self, legal_entity_id: UUID, data: LegalEntityUpsert, actor: Actor
    ) -> LegalEntityRead:
        """Replace one legal entity's fields. Whole-row, like every write on this table:
        a key left out goes back to its default, which is why the MCP tool tells the
        agent to read first and send the object back. A body that names the version it
        was built on (`updated_at`, REB-622) is refused when the row has moved since,
        before anything is assigned."""
        actor.require_admin("upsert_legal_entities")
        payload = data.model_dump(exclude={"updated_at"})
        _check_fiscal(payload)
        row = self.resolve(legal_entity_id)
        if "updated_at" in data.model_fields_set:
            # The row's lock before the comparison: two saves carrying the same version
            # would otherwise both read the old `updated_at` under READ COMMITTED and
            # the second would still land. The second waits here, then reads the first's.
            self.session.refresh(row, with_for_update=True)
            require_unchanged(ENTITY, sent=data.updated_at, current=row.updated_at)
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
        return LegalEntityRead.model_validate(row)

    def set_default(self, legal_entity_id: UUID, actor: Actor) -> LegalEntityRead:
        """Move the default. One transaction, old row off first, so the partial unique
        index never sees two defaults; an inactive legal entity cannot become the default,
        because the default is what every implicit read resolves to."""
        actor.require_unscoped_admin("set_default_legal_entity")
        row = self.resolve(legal_entity_id)
        if not row.active:
            raise ValidationFailed(
                ENTITY,
                "is_default",
                "un'azienda disattivata non puo' essere la predefinita",
                expected="un'azienda attiva",
            )
        current = self.repo.default()
        try:
            if current is not None and current.id != row.id:
                current.is_default = False
                self.session.flush()
            row.is_default = True
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": ["is_default"]})
            self.session.commit()
        except IntegrityError as exc:
            # Two admins moving the default at once: the second flush meets the partial
            # unique index, or the check that keeps the default active meets a
            # deactivation that landed in between. A clean Conflict, not a 500.
            self.session.rollback()
            raise Conflict(ENTITY, "la predefinita e' cambiata nel frattempo: ricarica") from exc
        return LegalEntityRead.model_validate(row)

    def deactivate(self, legal_entity_id: UUID, actor: Actor) -> LegalEntityDeactivated:
        """Switch a legal entity off. Never a delete: a legal entity that issued an invoice
        stays readable forever, and a row that is gone cannot be the `emittente` a
        snapshot was frozen from. The default is refused, since every implicit read
        resolves to it; move the default first. The answer carries how many live
        customers still point at the row (spec §3): each of them refuses a new deal,
        document or proforma until it is moved, so the count is the work left."""
        actor.require_unscoped_admin("deactivate_legal_entity")
        row = self.resolve(legal_entity_id)
        if row.is_default:
            raise ValidationFailed(
                ENTITY,
                "active",
                "l'azienda predefinita non si puo' disattivare",
                expected="prima sposta la predefinita su un'altra azienda",
            )
        try:
            row.active = False
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": ["active"]})
            self.session.commit()
        except IntegrityError as exc:
            # `ck_legal_entities_default_active`: a `set_default` that made this row the
            # default after the check above read it.
            self.session.rollback()
            raise Conflict(ENTITY, "l'azienda e' diventata la predefinita nel frattempo") from exc
        return LegalEntityDeactivated(
            **LegalEntityRead.model_validate(row).model_dump(),
            linked_customers=self.repo.count_customers(row.id),
        )

    @staticmethod
    def _conflict_reason(
        exc: IntegrityError, fallback: str = "l'azienda predefinita esiste gia'"
    ) -> str:
        text = str(exc.orig) if exc.orig is not None else str(exc)
        if "uq_legal_entities_vat_number" in text:
            return "la partita IVA e' gia' di un'altra azienda"
        if "uq_legal_entities_tax_code" in text:
            return "il codice fiscale e' gia' di un'altra azienda"
        return fallback
