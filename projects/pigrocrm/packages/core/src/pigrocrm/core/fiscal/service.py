from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_NON_IT
from pigrocrm.core.fiscal.regime import resolve_regime
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileRead, FiscalProfileUpsert, FiscalSnapshot
from pigrocrm.core.versioning import require_unchanged

ENTITY = "fiscal_profile"
ZERO = Decimal("0.00")
# The first half of the advisory lock a versioned save takes per azienda (REB-622), the
# second being the azienda id hashed: a profile not saved yet has no row to lock, and two
# first saves sent with `updated_at: null` would otherwise both read «no row», both pass
# the comparison, and the loser would meet the unique key as a generic `conflict` instead
# of the `stale_row` the panel knows how to recover from. Transaction-scoped.
VERSION_LOCK = 0xF15C


class FiscalProfileService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = FiscalProfileRepository(session)
        self.aziende = LegalEntityService(session)
        self.activities = ActivityService(session)

    # Every read takes an optional `azienda_id`; `None` is the default azienda, which
    # on a space with one azienda is the only one, so every caller written for the
    # single profile keeps working unchanged (REB-615, spec 2026-10-03 §1.2).

    def get(self, actor: Actor, azienda_id: UUID | None = None) -> FiscalProfileRead:
        return FiscalProfileRead.model_validate(self._require(azienda_id))

    def snapshot(self, azienda_id: UUID | None = None) -> FiscalSnapshot:
        """The parameters as a frozen value object, ready to be written into
        `invoices.snapshot`. No `actor`: it takes no decision and returns no identity,
        it is the read `InvoiceService.issue` performs on the caller's behalf."""
        profile = self._require(azienda_id)
        return FiscalSnapshot(
            codice_regime=profile.codice_regime,
            pack_id=profile.pack_id,
            aliquota_iva_default=profile.aliquota_iva_default,
            natura_default=profile.natura_default,
            riferimento_normativo=profile.riferimento_normativo,
            applica_bollo=profile.applica_bollo,
            soglia_bollo=profile.soglia_bollo,
            importo_bollo=profile.importo_bollo,
            condizioni_pagamento=profile.condizioni_pagamento,
            modalita_pagamento=profile.modalita_pagamento,
            giorni_scadenza=profile.giorni_scadenza,
            iban=profile.iban,
        )

    def describe(self, actor: Actor, azienda_id: UUID | None = None) -> dict[str, Any]:
        """What `describe_fiscal_profile` returns over MCP: the parameters an agent
        needs to compose a proforma the human will actually be able to issue, with no
        identity or timestamps in it."""
        profile = self.get(actor, azienda_id)
        return profile.model_dump(
            mode="json", exclude={"id", "azienda_id", "created_at", "updated_at"}
        )

    def upsert(
        self, data: FiscalProfileUpsert, actor: Actor, azienda_id: UUID | None = None
    ) -> FiscalProfileRead:
        """Create-or-update the one row of an azienda, admin only.

        `repo.add` sits **inside** the `try`, not before it: it is the only statement
        that can violate the unique key on `azienda_id`, and leaving it outside would
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
        payload = data.model_dump(exclude={"updated_at"})
        self.check(payload)
        azienda = self.aziende.resolve(azienda_id)

        versioned = "updated_at" in data.model_fields_set
        if versioned:
            # Before the read, so two versioned saves on one azienda run one after the
            # other and the second reads what the first committed: a row where it
            # expected none (`null` sent) is `stale_row`, never the unique key's
            # `conflict`. Then the row's own lock, as `LegalEntityService.update` takes
            # it, which also orders a versioned save after an unversioned one in flight.
            self.session.execute(
                select(func.pg_advisory_xact_lock(VERSION_LOCK, func.hashtext(str(azienda.id))))
            )
        profile = self.repo.get(azienda.id)
        if versioned:
            if profile is not None:
                self.session.refresh(profile, with_for_update=True)
            require_unchanged(
                ENTITY,
                sent=data.updated_at,
                current=None if profile is None else profile.updated_at,
            )
        try:
            if profile is None:
                profile = self.repo.add(FiscalProfile(**payload, azienda_id=azienda.id))
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

    @staticmethod
    def check(payload: dict[str, Any]) -> None:
        """Every refusal a profile body can earn, on the dumped payload, before any row
        is touched. Public since REB-630: `LegalEntityService.create` runs it on the
        profile a new azienda is born with, so a bad profile refuses the whole request
        rather than leaving an azienda `issue` could never use."""
        # `resolve_regime` does every check: the pack decides whether a code is
        # required (`it-flat-rate`) or forbidden (`non-it`, REB-619), then the
        # `RF01`-`RF19` shape, with `.fullmatch` so "RF19\n" cannot reach the String(4)
        # column, and whether a strategy exists. Raising here means an unimplemented
        # regime is refused at configuration time rather than at the first emission.
        resolve_regime(payload["codice_regime"], payload["pack_id"])
        abroad = payload["pack_id"] == PACK_NON_IT
        if payload["aliquota_iva_default"] == ZERO and not payload.get("natura_default"):
            # The same rule for both packs, since `invoice_lines` requires a natura
            # beside a zero rate whoever issues; the reason differs, so the words do.
            raise ValidationFailed(
                ENTITY,
                "natura_default",
                "un'azienda estera con aliquota zero richiede una natura: la dicitura che "
                "ogni riga non tassata porta"
                if abroad
                else "un'aliquota di default a zero richiede una natura, altrimenti ogni "
                "riepilogo prodotto verrebbe scartato dallo SdI",
                expected="una natura, oppure un'aliquota maggiore di zero"
                if abroad
                else "una natura, per esempio N2.2",
            )
        if abroad:
            # What a foreign azienda cannot carry (REB-619): the bollo is an Italian
            # duty, and the three income parameters are the forfettario's arithmetic,
            # which the fiscal estimate would otherwise compute for a company it does
            # not apply to. `FiscalProfileUpsert` defaults them off for this pack; an
            # explicit value is refused, not silently dropped.
            if payload["applica_bollo"]:
                raise ValidationFailed(
                    ENTITY,
                    "applica_bollo",
                    "un'azienda estera non applica il bollo virtuale",
                    expected="applica_bollo = false",
                )
            for field in (
                "coefficiente_redditivita",
                "aliquota_imposta_sostitutiva",
                "aliquota_inps",
            ):
                if payload.get(field) is not None:
                    raise ValidationFailed(
                        ENTITY,
                        field,
                        "un'azienda estera non ha i parametri del forfettario",
                        expected="nessun valore",
                    )
        if payload["aliquota_iva_default"] != ZERO and payload.get("natura_default"):
            raise ValidationFailed(
                ENTITY,
                "natura_default",
                "una natura con un'aliquota di default diversa da zero viene scartata dallo SdI",
                expected="nessuna natura",
            )
        if payload["applica_bollo"] and payload["importo_bollo"] <= ZERO:
            raise ValidationFailed(
                ENTITY,
                "importo_bollo",
                "il bollo e' attivo ma il suo importo non e' positivo",
                expected="un importo maggiore di zero",
            )

    def _require(self, azienda_id: UUID | None = None) -> FiscalProfile:
        azienda = self.aziende.resolve(azienda_id)
        profile = self.repo.get(azienda.id)
        if profile is None:
            raise NotFound(ENTITY, str(azienda.id))
        return profile


__all__ = ["FiscalProfileService"]
