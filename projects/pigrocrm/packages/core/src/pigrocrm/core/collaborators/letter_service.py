"""Framework agreements and engagement letters, written from the space's templates
(collaborators design § 1, «The documents»).

A letter is rendered from the active template of its kind and regime (`framework_agreement` or
`engagement_letter`, `ordinary` or `occasional`), with a scope the engagement and its parties fill:
the legal entity of the space as `emittente`, the freelancer as `professionista`, the client as
`cliente`, the engagement as `incarico`, and the signature blocks as `firma`. The free-text fields
the text declares (`attivita`, `risultati`, `luogo`, ...) are the administrator's, by name, and can
never shadow what a record says. **The client's price and the company's daily budget are not in the
scope at all**: the engagement holds the freelancer's fee and nothing of what rebase agreed with
the client (matches design § 1h), so no template can print them.

A **preview** is rendered and not stored, and may be of a text that is still a draft (the page says
so); a **stored** letter is a `documents` row owned by the freelancer's organization, written from
a final text only and from the freelancer's complete tax data. Sending for signature, Documenso and
the lifecycle of a signed letter are the next card.
"""

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import letters, rules
from pigrocrm.core.collaborators.hub_notices import HubLink
from pigrocrm.core.collaborators.letter_pdf import (
    RenderedLetter,
    SignatureBlank,
    compile_text,
    signature_blanks,
    typeset,
)
from pigrocrm.core.collaborators.letter_templates import SCOPE_ROOTS
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.outbox import hub_company_of
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.schemas import (
    LETTER_VARIABLE_MAX_LENGTH,
    LETTER_VARIABLES_MAX,
    LetterPreview,
    LetterRead,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.context import DocumentContext
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.schemas import DocumentRead
from pigrocrm.core.documents.uploads import DocumentUploads
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationRead
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.repository import TemplateRepository
from pigrocrm.core.templates.scope import seat

ENTITY = "letter"
NOT_INDICATED = "non indicata"
FLAGGED_NONE = "nessuno"
# The two fields a letter prints about who referred the parties: the freelancer's (the framework
# agreement) and the company's (the letter). They come from the hub and from nowhere else, so an
# administrator cannot type them (`_variables`), and the value printed is stored with the document.
REFERRER_FREELANCER = "segnalato_da"
REFERRER_COMPANY = "azienda_segnalata_da"
REFERRER_FIELDS = (REFERRER_FREELANCER, REFERRER_COMPANY)
SIGNED_ELECTRONICALLY = "firmato elettronicamente"
_VARIABLE_NAME = re.compile(r"[a-z][a-z0-9_]{0,59}")


def _issued_by(issuer: str, day: str) -> str:
    """What the issuer's signature blank prints, since the issuer does not sign: the legal entity
    that issued the document, and when."""
    return f"Documento emesso da {issuer} il {day}"


@dataclass(frozen=True)
class SigningCopy:
    """What `render_for_signing` answers: the text it came from, the typeset copy, where the
    signing blanks sit, and the values it printed (the new version's `variables`)."""

    template: Template
    rendered: RenderedLetter
    blanks: list[SignatureBlank]
    variables: dict[str, Any]
    legal_entity_id: UUID


class LetterService:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.ctx = DocumentContext(session, storage, settings)
        self.uploads = DocumentUploads(self.ctx)
        self.engagements = EngagementRepository(session)
        self.templates = TemplateRepository(session)

    # ---- previews ---------------------------------------------------------------------------

    def preview_letter(
        self, engagement_id: UUID, variables: Mapping[str, str | None], actor: Actor
    ) -> LetterPreview:
        """The engagement's letter as it would be written now, from the space's active template
        of its regime. Not stored; blank where the engagement or the freelancer lack a fact."""
        actor.require_admin("preview_engagement")
        engagement, freelancer = self._engagement(engagement_id)
        return self._preview(
            letters.ENGAGEMENT_LETTER,
            engagement.letter_type,
            freelancer,
            engagement,
            variables,
            actor,
        )

    def preview_framework_agreement(
        self, freelancer_id: UUID, letter_type: str | None, actor: Actor
    ) -> LetterPreview:
        """The framework agreement of a freelancer, `letter_type` omitted following the kind the
        freelancer was classified as (`occasional` for an occasional one, `ordinary` otherwise)."""
        actor.require_admin("preview_engagement")
        freelancer = self._freelancer(freelancer_id)
        regime = self._regime(letter_type, freelancer)
        return self._preview(letters.FRAMEWORK_AGREEMENT, regime, freelancer, None, {}, actor)

    # ---- stored letters -----------------------------------------------------------------------

    def create_letter(
        self, engagement_id: UUID, variables: Mapping[str, str | None], actor: Actor
    ) -> LetterRead:
        """Writes the engagement's letter as a document of its freelancer: from a final text, the
        freelancer's tax data complete, and once (a second letter for an engagement is refused)."""
        actor.require_admin("create_engagement_letter")
        engagement, freelancer = self._engagement(engagement_id, lock=True)
        if engagement.state != "draft":
            raise Conflict(
                ENTITY,
                "la lettera si scrive finché l'incarico è in bozza",
                state=engagement.state,
            )
        if self._existing(freelancer.id, letters.ENGAGEMENT_LETTER, engagement.id):
            raise Conflict(
                ENTITY, "questo incarico ha già la sua lettera", engagement_id=str(engagement.id)
            )
        return self._store(
            letters.ENGAGEMENT_LETTER,
            engagement.letter_type,
            freelancer,
            engagement,
            variables,
            actor,
        )

    def create_framework_agreement(
        self, freelancer_id: UUID, letter_type: str | None, actor: Actor
    ) -> LetterRead:
        """Writes a freelancer's framework agreement. It belongs to the freelancer and hangs on no
        engagement; a freelancer holds one of each regime, and a second of the same is refused."""
        actor.require_admin("create_framework_agreement")
        freelancer = self._freelancer(freelancer_id)
        regime = self._regime(letter_type, freelancer)
        if self._existing(freelancer.id, letters.FRAMEWORK_AGREEMENT, None, regime):
            raise Conflict(
                ENTITY,
                "questo freelance ha già un contratto quadro di questo regime",
                freelancer_id=str(freelancer.id),
                letter_type=regime,
            )
        return self._store(letters.FRAMEWORK_AGREEMENT, regime, freelancer, None, {}, actor)

    # ---- the copy that leaves for signature -----------------------------------------------------

    def render_for_signing(
        self, document: Document, actor: Actor, *, framework_signed_on: dt.date | None
    ) -> "SigningCopy":
        """The document as it leaves for signature (`SigningService.dispatch`): written again from
        the space's active text of its kind and regime with the parties as they are today, the day
        it leaves in the issuer's blank (`Documento emesso da ... il ...`) and the labels under
        the blanks the signing site fills laid out and not drawn (`signing=True`). The fields the
        administrator wrote when the letter was stored are read back from its first version. A text
        still in draft never leaves. Nothing is stored and nothing commits: the caller holds the
        row locks and keeps them until the envelope exists.

        `framework_signed_on` is the day the agreement the letter stands on was signed, printed in
        the letter's subtitle; a framework agreement has none."""
        if document.customer_id is None:
            raise NotFound("document", document.id)
        freelancer = self._freelancer(document.customer_id)
        engagement = (
            self.engagements.get(document.engagement_id) if document.engagement_id else None
        )
        if document.engagement_id is not None and engagement is None:
            raise NotFound("engagement", document.engagement_id)
        regime = document.letter_type or "ordinary"
        template = self._template(document.type, regime)
        letters.refuse_draft(template.body_markdown, template.name)
        written = self.session.scalars(
            select(DocumentVersion.variables)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.number)
            .limit(1)
        ).first()
        variables = {
            key: value
            for key, value in (written or {}).items()
            if key not in SCOPE_ROOTS and key not in REFERRER_FIELDS and isinstance(value, str)
        }
        # A letter keeps the referrer it was written with; only a «nessuno» is asked again, since
        # the referral may have been verified since.
        stored = {
            key: value
            for key in REFERRER_FIELDS
            if isinstance(value := (written or {}).get(key), str)
        }
        scope, legal_entity_id = self._scope(
            freelancer, engagement, variables, actor, framework_signed_on, stored
        )
        compiled = compile_text(template.body_markdown, scope)
        issuer = scope["emittente"].get("ragione_sociale") or ""
        return SigningCopy(
            template=template,
            rendered=typeset(compiled, issuer, self.ctx.settings, signing=True),
            blanks=signature_blanks(compiled, issuer, self.ctx.settings, signing=True),
            variables={key: value for key, value in scope.items() if key not in SCOPE_ROOTS},
            legal_entity_id=UUID(legal_entity_id),
        )

    # ---- the work ---------------------------------------------------------------------------

    def _engagement(
        self, engagement_id: UUID, *, lock: bool = False
    ) -> tuple[Engagement, Organization]:
        engagement = self.engagements.get(engagement_id, lock=lock)
        if engagement is None:
            raise NotFound("engagement", engagement_id)
        if engagement.client_organization_id is not None and (
            self.engagements.visible_organization(engagement.client_organization_id) is None
        ):
            # The fee and the role are the client's business: a reader who cannot see the client
            # cannot see the engagement either (`EngagementService._read` redacts them).
            raise NotFound("engagement", engagement_id)
        return engagement, self._freelancer(engagement.freelancer_id)

    # ---- for the signing service: what it reads of the letters' own rules --------------------

    def freelancer(self, freelancer_id: UUID) -> Organization:
        """The live organization with the role `freelancer`."""
        return self._freelancer(freelancer_id)

    def template(self, kind: str, regime: str) -> Template:
        """The space's active text of `kind` and `regime`."""
        return self._template(kind, regime)

    def require_party_data(self, freelancer: Organization, regime: str) -> None:
        """The tax data a stored letter prints, or a refusal that names what is missing."""
        self._require_party_data(freelancer, regime)

    def _freelancer(self, freelancer_id: UUID) -> Organization:
        freelancer = self.engagements.organization(freelancer_id)
        if freelancer is None:
            raise NotFound("organization", freelancer_id)
        if "freelancer" not in freelancer.roles:
            raise ValidationFailed(
                ENTITY,
                "freelancer_id",
                "l'anagrafica non ha il ruolo di freelance: aggiungilo prima",
                expected="roles con freelancer",
            )
        return freelancer

    @staticmethod
    def _regime(letter_type: str | None, freelancer: Organization) -> str:
        regime = letter_type or rules.default_letter_type(freelancer.supplier_kind)
        return rules.check_letter_type(regime, freelancer.supplier_kind)

    def _template(self, kind: str, regime: str) -> Template:
        template = self.templates.active_letter(kind, regime)
        if template is None:
            raise NotFound("template", f"{kind}/{regime}")
        return template

    def _existing(
        self, freelancer_id: UUID, kind: str, engagement_id: UUID | None, regime: str | None = None
    ) -> Document | None:
        stmt = select(Document).where(
            Document.customer_id == freelancer_id,
            Document.type == kind,
            Document.deleted_at.is_(None),
        )
        if engagement_id is not None:
            stmt = stmt.where(Document.engagement_id == engagement_id)
        else:
            stmt = stmt.where(Document.engagement_id.is_(None))
        if regime is not None:
            stmt = stmt.where(Document.letter_type == regime)
        if kind == letters.FRAMEWORK_AGREEMENT:
            # A cancelled agreement, or one a notice ended, does not stand in a new one's way.
            stmt = stmt.where(
                or_(
                    Document.signature_state.is_(None),
                    Document.signature_state.not_in(("cancelled", "notice_served")),
                )
            )
        return self.session.execute(stmt).scalars().first()

    def _variables(self, variables: Mapping[str, str | None]) -> dict[str, Any]:
        """The administrator's own fields: lowercase words with underscores, none of the roots the
        records fill, and text of a sane length. What they say is printed as given."""
        if len(variables) > LETTER_VARIABLES_MAX:
            raise ValidationFailed(
                ENTITY, "variables", "troppi campi", expected=f"al massimo {LETTER_VARIABLES_MAX}"
            )
        checked: dict[str, Any] = {}
        for name, value in variables.items():
            if not _VARIABLE_NAME.fullmatch(name):
                raise ValidationFailed(
                    ENTITY,
                    name,
                    "il nome del campo e' parole minuscole con underscore",
                    expected="per esempio periodo_verifica",
                )
            if name in REFERRER_FIELDS:
                raise ValidationFailed(
                    ENTITY,
                    name,
                    "il nome di chi ha segnalato viene dall'hub: non si scrive qui",
                    expected="un campo che il testo dichiara",
                )
            if name in SCOPE_ROOTS:
                raise ValidationFailed(
                    ENTITY,
                    name,
                    "questo dato viene dall'incarico e dalle anagrafiche: non si scrive qui",
                    expected="un campo che il testo dichiara",
                )
            if value is not None and "\x00" in value:
                raise ValidationFailed(ENTITY, name, "il campo ha caratteri non ammessi")
            if value is not None and len(value) > LETTER_VARIABLE_MAX_LENGTH:
                raise ValidationFailed(
                    ENTITY,
                    name,
                    f"il campo supera i {LETTER_VARIABLE_MAX_LENGTH} caratteri",
                    expected=f"al massimo {LETTER_VARIABLE_MAX_LENGTH} caratteri",
                )
            checked[name] = value
        return checked

    def _scope(
        self,
        freelancer: Organization,
        engagement: Engagement | None,
        variables: Mapping[str, str | None],
        actor: Actor,
        framework_signed_on: dt.date | None = None,
        stored_referrers: Mapping[str, str] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """What the text reads, and the legal entity that issues it. The administrator's fields
        go in first and the records' own second, so a field cannot shadow `emittente`,
        `professionista`, `cliente`, `incarico` or `firma`."""
        legal_entity_id = self.ctx.legal_entity_of(freelancer)
        issuer_values = self.ctx.legal_entity.as_template_values(actor, legal_entity_id)[
            "emittente"
        ]
        party = OrganizationRead.model_validate(freelancer)
        today = today_local()
        issuer = issuer_values.get("ragione_sociale") or issuer_values.get("nome") or ""
        scope: dict[str, Any] = dict(self._variables(variables))
        scope["emittente"] = issuer_values
        scope["professionista"] = {
            "nome": party.legal_name,
            "partita_iva": party.vat_number,
            "codice_fiscale": party.tax_code,
            "email": party.email,
            "pec": party.certified_email or NOT_INDICATED,
            "domicilio": seat(party.address, party.postal_code, party.city, party.province),
        }
        scope["cliente"] = self._client(engagement)
        scope["incarico"] = (
            self._engagement_values(engagement, framework_signed_on) if engagement else {}
        )
        scope["firma"] = {
            "luogo": SIGNED_ELECTRONICALLY,
            "data": None,
            "rebase": _issued_by(issuer, letters.italian_date(today)),
            "professionista": None,
            "cliente": None,
        }
        scope["oggi"] = today.isoformat()
        scope.update(self._referrers(freelancer, engagement, stored_referrers or {}))
        if engagement is not None and not scope.get("impegno") and engagement.days_per_week:
            scope["impegno"] = letters.days_per_week_words(engagement.days_per_week)
        return scope, str(legal_entity_id)

    def _referrers(
        self, freelancer: Organization, engagement: Engagement | None, stored: Mapping[str, str]
    ) -> dict[str, str]:
        """Who referred the freelancer and the company, as the hub names them for a verified
        referral (collaborators design § 5, «What the CRM reads from the hub»), asked at render
        and at send. A value already stored with the letter is kept; «nessuno» is printed when the
        referral is unverified, when the party has no hub id and when the call fails, since a
        letter must never say what nobody proved. The company is the one behind the engagement's
        deal (`rebase:request=<hub company id>`): a framework agreement, which has no engagement,
        asks only for the freelancer's."""
        out = {
            key: stored[key] if stored.get(key, FLAGGED_NONE) != FLAGGED_NONE else FLAGGED_NONE
            for key in REFERRER_FIELDS
        }
        asking = [key for key in REFERRER_FIELDS if out[key] == FLAGGED_NONE]
        company: UUID | None = None
        if engagement is not None and engagement.deal_id is not None:
            company = hub_company_of(
                self.session.scalar(select(Deal.rebase_refs).where(Deal.id == engagement.deal_id))
                or []
            )
        freelancer_id = freelancer.hub_freelancer_id if REFERRER_FREELANCER in asking else None
        company_id = company if REFERRER_COMPANY in asking else None
        if freelancer_id is None and company_id is None:
            return out
        answer = HubLink(self.ctx.settings).referrer_names(freelancer_id, company_id)
        out[REFERRER_FREELANCER] = (
            answer.freelancer
            if freelancer_id is not None and answer.freelancer
            else out[REFERRER_FREELANCER]
        )
        out[REFERRER_COMPANY] = (
            answer.company if company_id is not None and answer.company else out[REFERRER_COMPANY]
        )
        return out

    def _client(self, engagement: Engagement | None) -> dict[str, Any]:
        if engagement is None or engagement.client_organization_id is None:
            return {}
        client = self.engagements.visible_organization(engagement.client_organization_id)
        if client is None:
            return {}
        return {
            "ragione_sociale": client.legal_name,
            "partita_iva": client.vat_number,
            "sede": seat(client.address, client.postal_code, client.city, client.province),
            "pec": client.certified_email,
            "codice_sdi": client.sdi_code,
        }

    @staticmethod
    def _engagement_values(
        engagement: Engagement, framework_signed_on: dt.date | None = None
    ) -> dict[str, Any]:
        """What the engagement says, checked again by the rules that refused it when it was
        written: a fee above zero, ordered dates and a payment term within the law. A value the
        engagement does not hold is `None`, which prints as a blank line."""
        rules.check_fee(engagement.fee, engagement.fee_unit)
        rules.check_dates(engagement.starts_on, engagement.ends_on)
        receipt = engagement.letter_type == "occasional"
        term = letters.payment_term(
            engagement.payment_rule,
            engagement.payment_days,
            engagement.payment_end_of_month,
            engagement.collection_days,
            receipt=receipt,
        )
        return {
            "numero": engagement.letter_number,
            "data_contratto_quadro": (
                letters.italian_date(framework_signed_on) if framework_signed_on else None
            ),
            "ruolo": engagement.role,
            "data_inizio": letters.italian_date(engagement.starts_on),
            "data_fine": letters.italian_date(engagement.ends_on) if engagement.ends_on else None,
            "modalita": letters.FEE_MODE_PHRASE[engagement.fee_unit],
            "compenso": letters.italian_money(engagement.fee),
            "unita": letters.FEE_UNIT_PHRASE[engagement.fee_unit],
            "termine_pagamento": term,
            "giorni_incasso": engagement.collection_days,
        }

    def _render(
        self,
        kind: str,
        regime: str,
        freelancer: Organization,
        engagement: Engagement | None,
        variables: Mapping[str, str | None],
        actor: Actor,
    ) -> tuple[Template, RenderedLetter, dict[str, Any], str]:
        template = self._template(kind, regime)
        scope, legal_entity_id = self._scope(freelancer, engagement, variables, actor)
        compiled = compile_text(template.body_markdown, scope)
        issuer = scope["emittente"].get("ragione_sociale") or ""
        # Everything the render reads is read; end the transaction before the compile, which takes
        # seconds and must not hold a pooled connection while it does.
        self.session.commit()
        return template, typeset(compiled, issuer, self.ctx.settings), scope, legal_entity_id

    def _preview(
        self,
        kind: str,
        regime: str,
        freelancer: Organization,
        engagement: Engagement | None,
        variables: Mapping[str, str | None],
        actor: Actor,
    ) -> LetterPreview:
        template, rendered, scope, _ = self._render(
            kind, regime, freelancer, engagement, variables, actor
        )
        return LetterPreview(
            kind=kind,
            letter_type=regime,
            template_id=template.id,
            text_version=rendered.version,
            draft=rendered.draft,
            number=scope["incarico"].get("numero") if engagement else None,
            blank=rendered.blank,
            markdown=rendered.markdown,
            pdf=rendered.pdf,
        )

    def _require_party_data(self, freelancer: Organization, regime: str) -> None:
        """A stored letter names its freelancer: the tax code and the address always, and the VAT
        number on an ordinary letter (an occasional one is paid on a receipt and prints none)."""
        missing = [
            name
            for name, value in (
                ("tax_code", freelancer.tax_code),
                ("address", freelancer.address),
                ("vat_number", freelancer.vat_number if regime == "ordinary" else "-"),
            )
            if not value
        ]
        if missing:
            raise ValidationFailed(
                ENTITY,
                missing[0],
                "mancano i dati fiscali del freelance che la lettera stampa: " + ", ".join(missing),
                expected="codice fiscale e indirizzo, e la partita IVA su una lettera ordinaria",
            )

    def _store(
        self,
        kind: str,
        regime: str,
        freelancer: Organization,
        engagement: Engagement | None,
        variables: Mapping[str, str | None],
        actor: Actor,
    ) -> LetterRead:
        self._require_party_data(freelancer, regime)
        # A text still in draft is never written as a letter that could go out: refused before the
        # compile, not after it.
        text = self._template(kind, regime)
        letters.refuse_draft(text.body_markdown, text.name)
        engagement_id = engagement.id if engagement else None
        freelancer_id, number = freelancer.id, (engagement.letter_number if engagement else None)
        template, rendered, scope, legal_entity_id = self._render(
            kind, regime, freelancer, engagement, variables, actor
        )
        if engagement is not None:
            # The render released the row lock: the engagement may have left draft meanwhile.
            self.session.refresh(engagement)
            if engagement.state != "draft":
                raise Conflict(ENTITY, "l'incarico non è più in bozza", state=engagement.state)
        try:
            document = self.ctx.repo.add(
                Document(
                    customer_id=freelancer_id,
                    engagement_id=engagement_id,
                    legal_entity_id=UUID(legal_entity_id),
                    type=kind,
                    title=self._title(kind, scope, number),
                    letter_type=regime,
                    text_version=rendered.version,
                    signature_state="generated",
                )
            )
        except IntegrityError as exc:
            # Two requests wrote the same letter at once: the partial unique indexes of
            # `documents` let one through.
            self.session.rollback()
            raise Conflict(
                ENTITY, "questa lettera è già stata scritta", kind=kind, letter_type=regime
            ) from exc
        self.uploads.add_version_row(
            document,
            rendered.pdf,
            "application/pdf",
            actor,
            markdown_source=rendered.markdown,
            template_id=template.id,
            variables={key: value for key, value in scope.items() if key not in SCOPE_ROOTS},
        )
        self.ctx.activities.record(
            "document",
            document.id,
            "letter_created",
            actor,
            {"kind": kind, "letter_type": regime, "text_version": rendered.version},
        )
        self.session.commit()
        return LetterRead(
            document=DocumentRead.model_validate(document),
            engagement_id=engagement_id,
            letter_type=regime,
            text_version=rendered.version,
            number=number,
            blank=rendered.blank,
        )

    @staticmethod
    def _title(kind: str, scope: Mapping[str, Any], number: str | None) -> str:
        name = scope["professionista"]["nome"]
        if kind == letters.ENGAGEMENT_LETTER:
            return f"Lettera di incarico n. {number or '?'} · {name}"[:200]
        return f"Contratto quadro · {name}"[:200]
