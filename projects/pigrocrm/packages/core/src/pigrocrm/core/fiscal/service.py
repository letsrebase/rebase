from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, PACK_IT_STANDARD, PACK_NON_IT
from pigrocrm.core.fiscal.regime import resolve_regime
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileRead, FiscalProfileUpsert, FiscalSnapshot
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.versioning import require_unchanged

ENTITY = "fiscal_profile"
ZERO = Decimal("0.00")
# The first half of the advisory lock every save takes per legal entity (REB-622), the second
# being the legal entity id hashed: a profile not saved yet has no row to lock, and two first
# saves would otherwise both read «no row»; two versioned ones would both pass the
# comparison and the loser would meet the unique key as a generic `conflict` instead of
# the `stale_row` the panel knows how to recover from, and a versionless one (the MCP
# tool) would race a versioned one to the insert. Transaction-scoped.
VERSION_LOCK = 0xF15C


class FiscalProfileService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = FiscalProfileRepository(session)
        self.legal_entities = LegalEntityService(session)
        self.activities = ActivityService(session)

    # Every read takes an optional `legal_entity_id`; `None` is the default legal entity, which
    # on a space with one legal entity is the only one, so every caller written for the
    # single profile keeps working unchanged (REB-615, spec 2026-10-03 §1.2).

    def get(self, actor: Actor, legal_entity_id: UUID | None = None) -> FiscalProfileRead:
        return FiscalProfileRead.model_validate(self._require(legal_entity_id))

    def snapshot(self, legal_entity_id: UUID | None = None) -> FiscalSnapshot:
        """The parameters as a frozen value object, ready to be written into
        `invoices.snapshot`. No `actor`: it takes no decision and returns no identity,
        it is the read `InvoiceService.issue` performs on the caller's behalf."""
        profile = self._require(legal_entity_id)
        return FiscalSnapshot(
            regime_code=profile.regime_code,
            pack_id=profile.pack_id,
            default_vat_rate=profile.default_vat_rate,
            default_vat_nature=profile.default_vat_nature,
            legal_reference=profile.legal_reference,
            apply_stamp_duty=profile.apply_stamp_duty,
            stamp_duty_threshold=profile.stamp_duty_threshold,
            stamp_duty_amount=profile.stamp_duty_amount,
            payment_terms=profile.payment_terms,
            payment_method=profile.payment_method,
            due_days=profile.due_days,
            iban=profile.iban,
        )

    def describe(self, actor: Actor, legal_entity_id: UUID | None = None) -> dict[str, Any]:
        """What `describe_fiscal_profile` returns over MCP: the parameters an agent
        needs to compose a proforma the human will actually be able to issue, with no
        identity or timestamps in it."""
        profile = self.get(actor, legal_entity_id)
        return profile.model_dump(
            mode="json", exclude={"id", "legal_entity_id", "created_at", "updated_at"}
        )

    def upsert(
        self, data: FiscalProfileUpsert, actor: Actor, legal_entity_id: UUID | None = None
    ) -> FiscalProfileRead:
        """Create-or-update the one row of a legal entity, admin only.

        `repo.add` sits **inside** the `try`, not before it: it is the only statement
        that can violate the unique key on `legal_entity_id`, and leaving it outside would
        let two concurrent first-time saves poison the session with a raw
        `IntegrityError` instead of surfacing a clean `Conflict`. Same shape, same
        reason, as `LegalEntityService.upsert_default`.

        A body that names the version it was built on (`updated_at`, REB-622) is
        refused when the row has moved since, before anything is assigned. `null` is
        the version of a profile not saved yet: sent on a first save it is accepted,
        sent once a row exists it is a draft built on nothing and is refused, as is a
        timestamp sent for a profile that does not exist. A body without the key
        checks nothing.
        """
        actor.require_admin("update_fiscal_profile")
        payload = data.stored_fields()
        self.check(payload)
        legal_entity = self.legal_entities.resolve(legal_entity_id)

        # Before the read and on every save, versioned or not, so two saves on one
        # legal entity run one after the other and the second reads what the first
        # committed: a versioned one finding a row where it expected none (`null` sent)
        # is `stale_row`, never the unique key's `conflict`, and a versionless one
        # finding the row updates it instead of racing it to the insert. Then the row's
        # own lock for a versioned save, as `LegalEntityService.update` takes it.
        self.session.execute(
            select(func.pg_advisory_xact_lock(VERSION_LOCK, func.hashtext(str(legal_entity.id))))
        )
        profile = self.repo.get(legal_entity.id)
        if "updated_at" in data.model_fields_set:
            if profile is not None:
                self.session.refresh(profile, with_for_update=True)
            require_unchanged(
                ENTITY,
                sent=data.updated_at,
                current=None if profile is None else profile.updated_at,
            )
        if profile is not None and profile.pack_id != payload["pack_id"]:
            self._freeze_estimates(actor, legal_entity.id)
        try:
            if profile is None:
                profile = self.repo.add(FiscalProfile(**payload, legal_entity_id=legal_entity.id))
            else:
                for key, value in payload.items():
                    setattr(profile, key, value)
            # R5 is closed for this table: changing regime without a trace is a
            # different order of severity from renaming a pipeline stage, and the
            # timeline is also what reconstructs the history of regimes without a
            # validity column. Recorded last, so it can never survive a rollback.
            self.activities.record(
                ENTITY, profile.id, "updated", actor, {"changed": sorted(payload)}
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(ENTITY, "il profilo fiscale esiste gia'") from exc
        return FiscalProfileRead.model_validate(profile)

    def _freeze_estimates(self, actor: Actor, legal_entity_id: UUID) -> None:
        """Before a legal entity leaves its pack, keep the estimate the pack computes for
        every year the legal entity issued in (REB-974), so those years keep their figures
        after the move: the profile is one row and holds the new pack's parameters from then
        on. The analytics service is imported here, not at module level, because it imports
        this one."""
        from pigrocrm.core.analytics.service import AnalyticsService

        AnalyticsService(self.session).freeze_fiscal_estimates(actor, legal_entity_id)

    @staticmethod
    def check(payload: dict[str, Any]) -> None:
        """Every refusal a profile body can earn, on the dumped payload, before any row
        is touched. Public since REB-630: `LegalEntityService.create` runs it on the
        profile a new legal entity is born with, so a bad profile refuses the whole request
        rather than leaving a legal entity `issue` could never use."""
        # `resolve_regime` does the regime checks: the pack decides whether a code is
        # required (the Italian packs) or forbidden (`non-it`, REB-619), then the
        # `RF01`-`RF19` shape, with `.fullmatch` so "RF19\n" cannot reach the String(4)
        # column, whether a strategy exists, and that it is the pack's own (REB-760).
        # Raising here means an unimplemented regime is refused at configuration time
        # rather than at the first emission.
        resolve_regime(payload["regime_code"], payload["pack_id"])
        pack_id = payload["pack_id"]
        abroad = pack_id == PACK_NON_IT
        if pack_id == PACK_IT_STANDARD and payload["default_vat_rate"] <= ZERO:
            # The ordinary regime taxes every operation: `ORDINARY.resolve_line_vat`
            # refuses a zero rate, so a zero default would leave a profile whose every
            # invoice is refused at the first line.
            raise ValidationFailed(
                ENTITY,
                "default_vat_rate",
                "il regime ordinario applica l'IVA: l'aliquota di default non puo' essere zero",
                expected="un'aliquota maggiore di zero, per esempio 22.00",
            )
        if payload["default_vat_rate"] == ZERO and not payload.get("default_vat_nature"):
            # The same rule for both packs, since `invoice_lines` requires a natura
            # beside a zero rate whoever issues; the reason differs, so the words do.
            raise ValidationFailed(
                ENTITY,
                "default_vat_nature",
                "un'azienda estera con aliquota zero richiede una natura: la dicitura che "
                "ogni riga non tassata porta"
                if abroad
                else "un'aliquota di default a zero richiede una natura, altrimenti ogni "
                "riepilogo prodotto verrebbe scartato dallo SdI",
                expected="una natura, oppure un'aliquota maggiore di zero"
                if abroad
                else "una natura, per esempio N2.2",
            )
        if pack_id != PACK_IT_FLAT_RATE:
            # What only the flat-rate regime carries (REB-619, REB-760): the stamp duty is owed on
            # an untaxed base and the three income parameters are the flat-rate regime's
            # arithmetic, which the fiscal estimate would otherwise compute for a legal entity
            # it does not apply to. `FiscalProfileUpsert` defaults them off for these
            # packs; an explicit value is refused, not silently dropped.
            whose = "un'azienda estera" if abroad else "il regime ordinario"
            if payload["apply_stamp_duty"]:
                raise ValidationFailed(
                    ENTITY,
                    "apply_stamp_duty",
                    f"{whose} non applica il bollo virtuale",
                    expected="applica_bollo = false",
                )
            for field in (
                "profitability_coefficient",
                "substitute_tax_rate",
                "social_security_rate",
            ):
                if payload.get(field) is not None:
                    raise ValidationFailed(
                        ENTITY,
                        field,
                        f"{whose} non ha i parametri del forfettario",
                        expected="nessun valore",
                    )
            stated = [key for key in ("inps_category", "isa") if payload.get(key) is not None]
            if abroad and stated:
                raise ValidationFailed(
                    ENTITY,
                    stated[0],
                    "un'azienda estera non ha una categoria INPS né l'ISA",
                    expected="nessun valore",
                )
        if payload["default_vat_rate"] != ZERO and payload.get("default_vat_nature"):
            raise ValidationFailed(
                ENTITY,
                "default_vat_nature",
                "una natura con un'aliquota di default diversa da zero viene scartata dallo SdI",
                expected="nessuna natura",
            )
        if payload["apply_stamp_duty"] and payload["stamp_duty_amount"] <= ZERO:
            raise ValidationFailed(
                ENTITY,
                "stamp_duty_amount",
                "il bollo e' attivo ma il suo importo non e' positivo",
                expected="un importo maggiore di zero",
            )

    def _require(self, legal_entity_id: UUID | None = None) -> FiscalProfile:
        legal_entity = self.legal_entities.resolve(legal_entity_id)
        profile = self.repo.get(legal_entity.id)
        if profile is None:
            raise NotFound(
                ENTITY,
                str(legal_entity.id),
                message=(
                    f"l'azienda {legal_entity.name} non ha un profilo fiscale: "
                    "compilalo in Impostazioni, Aziende"
                ),
            )
        return profile


__all__ = ["FiscalProfileService"]
