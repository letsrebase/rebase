"""The data a match's documents print, and the documents written from it (REB-387, REB-892).

`MatchDocuments` is what `MatchService.create` and `SigningService` both build a document
with: the fields of a framework agreement and of a letter as they stand today
(`quadro_data`, `lettera_data`), the fields a document prints when it leaves for signature
(`data_for_sending`), and the framework agreement written for a freelancer who has none
(`write_framework`, one write for its two callers, REB-406).

The company's `budget_giornaliero` is read nowhere in this module: what rebase agrees with
the client never reaches a freelancer's document (spec § 1h).
"""

from collections.abc import Callable, Mapping
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from rebase_core.contract_schemas import MatchCreate
from rebase_core.contracts.fields import ContractFailed, Value, italian_date, merge_data
from rebase_core.contracts.render import Renderer, company_defaults
from rebase_core.framework import rome_today, signed_on
from rebase_core.match_rows import (
    QUADRO,
    fiscal_row,
    freelancer_row,
    full_name,
)
from rebase_core.models import ContractDocument, FreelancerFiscal, User
from rebase_core.referrals import ReferralService
from rebase_core.regimes import PARTITA_IVA, text_name

SIGNED_ELECTRONICALLY = "firmato elettronicamente"
PEC_MISSING = "non indicata"
# The common case: a party nobody referred prints this instead of a blank, hand-fillable
# line -- `PEC_MISSING`'s own convention for a fact that is usually absent, not a
# signature waiting to happen.
SEGNALATO_NONE = "nessuno"


def issued_by_rebase(day: date) -> str:
    """What rebase's signature blank prints, since rebase does not sign (spec § 1c)."""
    return f"Documento emesso da rebase il {italian_date(day)}"


class MatchDocuments:
    def __init__(
        self,
        session: Session,
        renderer: Renderer | None = None,
        signer: Mapping[str, Value] | None = None,
        today: Callable[[], date] = rome_today,
    ) -> None:
        """`renderer` is needed only to write a document; `signer` is
        `REBASE_SIGNER_JSON` as `signer_data` read it (`SigningService` sets it once it
        has parsed the setting)."""
        self.session = session
        self.renderer = renderer
        self.signer: Mapping[str, Value] = signer or {}
        self.today = today

    def require_renderer(self) -> Renderer:
        if self.renderer is None:
            raise ContractFailed("no renderer was handed to MatchService")
        return self.renderer

    def rebase_fields(self) -> dict[str, Value]:
        return merge_data(company_defaults(), self.signer)

    def signing_fields(self, today: date) -> dict[str, Value]:
        """The date and the freelancer's signature stay blank for the signing site; rebase,
        which does not sign, prints who issued the document and when (spec § 5)."""
        return {
            "luogo-firma": SIGNED_ELECTRONICALLY,
            "data-firma": None,
            "firma-rebase": issued_by_rebase(today),
            "firma-professionista": None,
        }

    def quadro_data(
        self, user: User, fiscal: FreelancerFiscal, today: date, regime: str
    ) -> dict[str, Value]:
        """The framework agreement's fields. The occasional text prints no partita IVA
        (REB-687), so only the ordinary one gets the field."""
        party: dict[str, Value] = {
            "professionista-nome": full_name(user),
            "professionista-cf": fiscal.codice_fiscale,
            "professionista-domicilio": fiscal.domicilio,
            "professionista-email": user.email,
            "professionista-pec": fiscal.pec or PEC_MISSING,
        }
        if regime == PARTITA_IVA:
            party["professionista-piva"] = fiscal.partita_iva
        return {
            **self.rebase_fields(),
            **party,
            "segnalato-da": ReferralService(self.session).referrer_name(
                "freelancer", fiscal.freelancer_id
            )
            or SEGNALATO_NONE,
            **self.signing_fields(today),
        }

    def letter_party_fields(
        self, user: User, fiscal: FreelancerFiscal, signed: date | None, regime: str
    ) -> dict[str, Value]:
        """A letter's own fields that must always be today's, not the draft's: the
        framework agreement's signature date, the freelancer's name, and the identifier
        the letter prints, the partita IVA on an ordinary letter and the codice fiscale on
        an occasional one (REB-687). Shared by `_lettera_data` (`create`) and
        `data_for_sending` (a send), so a field added to one cannot print stale data on
        the other (REB-406)."""
        identifier: dict[str, Value] = (
            {"professionista-piva": fiscal.partita_iva}
            if regime == PARTITA_IVA
            else {"professionista-cf": fiscal.codice_fiscale}
        )
        return {
            "data-contratto-quadro": italian_date(signed) if signed is not None else None,
            "professionista-nome": full_name(user),
            **identifier,
        }

    def lettera_data(
        self,
        user: User,
        fiscal: FreelancerFiscal,
        data: MatchCreate,
        numero: str | None,
        active: ContractDocument | None,
        today: date,
    ) -> dict[str, Value]:
        signed = signed_on(active) if active is not None else None
        return {
            **self.rebase_fields(),
            **data.lettera.to_fields(),
            "numero": numero,
            **self.letter_party_fields(user, fiscal, signed, data.regime),
            "cliente-ragione-sociale": data.cliente.cliente_ragione_sociale,
            "cliente-piva": data.cliente.cliente_piva,
            "cliente-sede": data.cliente.cliente_sede,
            "azienda-segnalata-da": ReferralService(self.session).referrer_name(
                "company", data.company_id
            )
            or SEGNALATO_NONE,
            **self.signing_fields(today),
        }

    def document(
        self,
        renderer: Renderer,
        kind: str,
        regime: str,
        data: dict[str, Value],
        freelancer_id: UUID,
        match_id: UUID | None,
        numero: str | None,
        stato: str,
        admin_id: UUID,
    ) -> ContractDocument:
        rendered = renderer.render(text_name(kind, regime), data)
        return ContractDocument(
            kind=kind,
            regime=regime,
            freelancer_id=freelancer_id,
            match_id=match_id,
            numero=numero,
            text_version=rendered.version,
            testo_bozza=rendered.draft,
            data=dict(data),
            pdf=rendered.pdf,
            stato=stato,
            created_by=admin_id,
        )

    def data_for_sending(
        self, document: ContractDocument, today: date, framework: ContractDocument | None
    ) -> dict[str, Value]:
        """The fields `document` prints when it goes out for signature (REB-387 phase 3):
        the engagement as the admin wrote it, the parties as they are today (the tax data
        and the name saved since the draft, rebase's signer as the setting says now), the
        day it leaves in rebase's blank, and for a letter the date its framework
        agreement was signed, in Rome."""
        freelancer, user = freelancer_row(self.session, document.freelancer_id)
        fiscal = fiscal_row(self.session, freelancer.id)
        if document.kind == QUADRO:
            return self.quadro_data(user, fiscal, today, document.regime)
        signed = signed_on(framework) if framework is not None else None
        return {
            **document.data,
            **self.rebase_fields(),
            **self.letter_party_fields(user, fiscal, signed, document.regime),
            **self.signing_fields(today),
        }

    def write_framework(self, freelancer_id: UUID, admin_id: UUID, regime: str) -> ContractDocument:
        """A new framework agreement of `regime`, added and flushed and not committed:
        written by `create` for a fresh draft, and by `SigningService._framework_to_send`
        for a match whose letter waits on one that was cancelled or refused, sent in the
        same transaction (REB-406: one write, two callers). The caller must already hold
        the freelancer's row lock (REB-406)."""
        renderer = self.require_renderer()
        freelancer, user = freelancer_row(self.session, freelancer_id)
        fiscal = fiscal_row(self.session, freelancer.id)
        document = self.document(
            renderer,
            QUADRO,
            regime,
            self.quadro_data(user, fiscal, self.today(), regime),
            freelancer.id,
            None,
            None,
            "generato",
            admin_id,
        )
        self.session.add(document)
        self.session.flush()
        return document
