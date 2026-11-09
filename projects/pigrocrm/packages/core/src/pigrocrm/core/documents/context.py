"""What the document collaborators (`uploads`, `text`, `templating`) and the
`DocumentService` facade share: the session, the storage, the repositories, and the
reads every one of them needs (the owner of a document, its customer, its storage key).
"""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.naming import slugify_folder
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import ALLOWED_CONTENT_TYPES, DocumentCreate
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.legal_entities.assets import LegalEntityAssets
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.templates.service import TemplateService

ENTITY: EntityType = "document"

_CUSTOMER_ID_FRAGMENT = 8


class DocumentContext:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.repo = DocumentRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.templates = TemplateService(session)
        self.legal_entity = LegalEntityService(session)
        self.assets = LegalEntityAssets(session, storage)

    def require(self, document_id: UUID) -> Document:
        document = self.repo.get(document_id)
        if document is None:
            raise NotFound(ENTITY, document_id)
        return document

    # ---- owner resolution ---------------------------------------------------

    def check_owner(
        self,
        customer_id: UUID | None,
        deal_id: UUID | None,
        contract_id: UUID | None = None,
    ) -> Organization | Deal | Contract:
        """Exactly one owner, and it must exist; returns it.

        The database check constraint (`ck_documents_customer_xor_deal`) is the
        second line under concurrency; this is the first, and it is what turns a
        syntactically valid but unknown UUID into this project's own `NotFound`
        instead of a raw `ForeignKeyViolation` reaching the caller from `flush()`.

        `contract_id` defaults to `None` so every existing caller that only knows
        about `customer_id`/`deal_id` (`create_from_template`) keeps working
        unchanged -- REB-358 widened the *rule* from two owners to three without
        widening every call site's own schema.
        """
        owners = (customer_id, deal_id, contract_id)
        if sum(owner is not None for owner in owners) != 1:
            raise ValidationFailed(
                ENTITY,
                "customer_id",
                "un documento appartiene a un cliente, a un deal o a un contratto: "
                "mai a più di uno, mai a nessuno",
                expected="esattamente uno fra customer_id, deal_id e contract_id",
            )
        if customer_id is not None:
            customer = self.session.get(Organization, customer_id)
            if customer is None:
                raise NotFound("organization", customer_id)
            return customer
        if deal_id is not None:
            deal = self.session.get(Deal, deal_id)
            if deal is None:
                raise NotFound("deal", deal_id)
            return deal
        if contract_id is None:
            raise ValueError("check_owner takes exactly one owner id")
        contract = self.session.get(Contract, contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)
        return contract

    def legal_entity_of(self, owner: Organization | Deal | Contract) -> UUID:
        """The legal entity a new document hangs on (REB-623, spec §1.7): its owner's.
        Refused when that legal entity is deactivated (spec §3)."""
        return self.legal_entity.inherited(owner.legal_entity_id, ENTITY)

    def customer_for(
        self, customer_id: UUID | None, deal_id: UUID | None, contract_id: UUID | None
    ) -> Organization | None:
        if customer_id is not None:
            return self.session.get(Organization, customer_id)
        if deal_id is not None:
            deal = self.session.get(Deal, deal_id)
            return self.session.get(Organization, deal.customer_id) if deal else None
        if contract_id is not None:
            contract = self.session.get(Contract, contract_id)
            return self.session.get(Organization, contract.customer_id) if contract else None
        return None

    def customer_of(self, document: Document) -> Organization | None:
        return self.customer_for(document.customer_id, document.deal_id, document.contract_id)

    def storage_key_for(
        self, document: Document, number: int, content_type: str, *, prefix: str | None = None
    ) -> str:
        """`{cliente-slug}-{id[:8]}/{document_id}/v{number}{ext}`, or
        `{prefix}/v{number}{ext}` when a caller supplies its own prefix.

        The customer-id fragment is what keeps the folder stable when a customer is
        renamed -- the slug alone would send the next version into a different folder
        and orphan the earlier ones. The slug is what makes the folder legible to a
        human browsing Drive. Built entirely from validated internal pieces (a
        slugified name, a UUID's own hex digits, an integer, an extension drawn from
        `ALLOWED_CONTENT_TYPES`, never the caller's raw string) and still passed
        through `storage.put`'s own `validate_storage_key` gate before anything
        touches a filesystem -- this method's job is to produce a *sensible* key, not
        to be the security boundary itself.

        The `prefix` override exists for the fiscal artefacts of slice 3, which need
        `invoices/{year}/{number}/` and `proforma/{id}/` rather than a customer folder:
        the bytes live under a prefix that keeps a file identifiable when it is pulled
        out of its context, which is one of the four independent mechanisms that stop a
        proforma from being read as an invoice.
        """
        extension = ALLOWED_CONTENT_TYPES[content_type]
        if prefix is not None:
            return f"{prefix}/v{number}{extension}"
        customer = self.customer_of(document)
        folder = (
            f"{slugify_folder(customer.legal_name)}-{str(customer.id)[:_CUSTOMER_ID_FRAGMENT]}"
            if customer
            else "senza-cliente"
        )
        return f"{folder}/{document.id}/v{number}{extension}"

    def resolve_version(
        self, document_id: UUID, number: int | None
    ) -> tuple[Document, DocumentVersion]:
        """The document and the version a caller asked for -- `None` meaning "the
        current one".

        Shared by `download` and `extract_text` rather than written twice: the two differ
        only in what they do with the bytes, and a second copy of "no `number` means
        `current_version`" is a second place for that default to drift. A document
        with no version at all (`versione_corrente is None`) and a `number` nobody ever
        uploaded answer the same `NotFound`, because to the caller they are the same
        fact: that version is not there.
        """
        document = self.require(document_id)
        wanted = number if number is not None else document.current_version
        version = self.repo.version(document_id, wanted) if wanted else None
        if version is None:
            raise NotFound("document_version", f"{document_id}#{wanted}")
        return document, version

    # ---- custom fields ------------------------------------------------------

    def validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    # ---- rows -----------------------------------------------------------------

    def create_row(
        self, data: DocumentCreate, actor: Actor, *, legal_entity_id: UUID | None = None
    ) -> Document:
        """Everything `DocumentService.create` does except the commit, so `import_bytes`
        can compose it with `add_version_row` inside one transaction.

        Not named `*_in_transaction`: that suffix is reserved by spec §9.3 for methods
        that skip authorisation and record nothing, callable only from
        `core/automations/` (see `tests/test_in_transaction_callers.py`). This one
        checks the actor and records exactly as before -- the only thing it leaves to
        its caller is *when* the transaction ends.
        """
        actor.require_write("create_document")
        payload = data.model_dump()
        if legal_entity_id is None:
            # The owner is the authority on the legal entity, so it must be there to ask. A
            # caller that names the legal entity is filing a record's own child (an invoice's
            # artefact): the record already holds its owner, which may have moved to an
            # legal entity this member cannot see, and the artefact must still render. The
            # legal entity is then the record's own, not the one its customer is in now: an
            # invoice keeps its legal entity when its customer moves (spec §1.7).
            owner = self.check_owner(
                payload["customer_id"], payload["deal_id"], payload["contract_id"]
            )
            legal_entity_id = self.legal_entity_of(owner)
        # Only an offer has a state; everything else keeps NULL. A new offer starts as
        # a draft rather than stateless, so a Kanban-style state picker always has a
        # value to show.
        is_offer = payload["type"] == "offer"
        payload["status"] = (payload.get("status") or "draft") if is_offer else None
        payload["custom_fields"] = self.validated_custom(payload.get("custom_fields") or {})
        payload["legal_entity_id"] = legal_entity_id

        document = self.repo.add(Document(**payload))
        self.activities.record(
            ENTITY,
            document.id,
            "created",
            actor,
            {"title": document.title, "type": document.type},
        )
        return document
