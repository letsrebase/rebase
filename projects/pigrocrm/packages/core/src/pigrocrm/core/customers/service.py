import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import (
    CUSTOMER_SORTS,
    CustomerCreate,
    CustomerFromSuggestion,
    CustomerListQuery,
    CustomerPage,
    CustomerRead,
    CustomersFromSuggestions,
    CustomerUpdate,
    PersonFromSuggestion,
)
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.gmail.query import customer_domain
from pigrocrm.core.gmail.roster import AddressRoster
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.notes.writer import append_first_note
from pigrocrm.core.people.schemas import (
    LAST_NAME_MAX_LENGTH,
    NAME_MAX_LENGTH,
    PersonCreate,
    PersonRead,
)
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`) so that passing it
# straight into `specs_for` type-checks under mypy strict -- a plain `ENTITY = "customer"`
# widens to `str` at the module level, which `specs_for(entity_type: EntityType)` then
# rejects. Every other call site here (NotFound, ValidationFailed, Conflict,
# ActivityService.record, validate_custom_fields) only asks for `str`, so the narrower
# type costs nothing there.
ENTITY: EntityType = "customer"
# `.fullmatch()`, not `.match()`, is load-bearing: `.match()` with a `$`-anchored
# pattern accepts a trailing "\n" -- `$` matches just before a final newline, not only
# at the true end of the string -- so "12345678901\n" (12 characters, one more than
# the `String(11)` column) used to pass this check and reach `flush()` as a raw,
# session-poisoning `DataError`. `.fullmatch()` requires the *entire* string to be
# consumed, which has no such exception. A pasted VAT number or an MCP agent's tool
# call carrying a trailing newline is not a lab-only case.
VAT_NUMBER_RE = re.compile(r"^\d{11}$")
SDI_CODE_LENGTH = 7


def _check_fiscal(data: dict[str, Any], country: str) -> None:
    """Mutates `data` in place: an empty string is normalized to `None` for both
    fiscal fields before either is checked. Without this, `if piva`/`if sdi` below are
    falsy on "", so an empty string skipped the check entirely and was stored as "" --
    a different thing from "not provided" that would, for instance, wrongly satisfy a
    future "has a VAT number" filter. Shared by `create` and `update`, so the
    normalization applies equally to a brand-new row and to a patch that clears the
    field with "".
    """
    if data.get("vat_number") == "":
        data["vat_number"] = None
    if data.get("sdi_code") == "":
        data["sdi_code"] = None

    vat_number = data.get("vat_number")
    # The eleven-digit rule is Italian, so it is applied to Italian customers and to
    # nobody else. It used to apply to everyone, which made a foreign customer
    # unrepresentable: a UK company's VAT number ("123456789", nine digits, and GB VATs
    # are not always numeric at all) was refused, so the only way to record it was the
    # `tax_code` field, which is not validated -- a workaround that stores the
    # right value under the wrong name and then writes it into the wrong XML element.
    #
    # `country` is passed in rather than read from `data`, because on an update the
    # caller may be patching the VAT number without mentioning the country: the check
    # has to run against the country the row will actually have, not against the subset
    # of fields this request happened to name.
    if vat_number and country.upper() == "IT" and not VAT_NUMBER_RE.fullmatch(vat_number):
        raise ValidationFailed(
            ENTITY, "vat_number", "deve essere di 11 cifre", expected="11 cifre numeriche"
        )
    sdi = data.get("sdi_code")
    if sdi and len(sdi) != SDI_CODE_LENGTH:
        raise ValidationFailed(
            ENTITY, "sdi_code", "deve essere di 7 caratteri", expected="7 caratteri"
        )


def _person_name(display: str, address: str) -> tuple[str, str | None]:
    """First name and surname from a mail header's display name, or from the address
    when the headers gave none: «Marco Bianchi» and «Bianchi, Marco» are both Marco
    Bianchi, and `marco.bianchi@` is too. A single word is a first name alone."""
    name = display.strip().strip('"').strip()
    if "," in name:
        surname, _, first = name.partition(",")
        name = f"{first.strip()} {surname.strip()}".strip()
    if not name or "@" in name:
        local = address.partition("@")[0]
        name = " ".join(word.capitalize() for word in re.split(r"[._\-]+", local) if word)
    first, _, rest = (name or address).partition(" ")
    # Clipped to what a Person holds: a header can carry a name longer than the column.
    return first[:NAME_MAX_LENGTH], rest.strip()[:LAST_NAME_MAX_LENGTH] or None


class CustomerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CustomerRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.legal_entities = LegalEntityService(session)
        self.users = UserRepository(session)

    def _owner_for(self, owner_id: UUID | None, actor: Actor, *, creating: bool) -> UUID | None:
        """The owner a write leaves on the customer (REB-773).

        Creating: whoever named in the request, and otherwise whoever creates it. The
        actor of a job or of rebase has no user id, so such a customer has no owner,
        which the reminders give to the space's owner. Naming a person other than
        yourself, and every later change, is an admin's: the owner decides who is told
        about the customer's invoices, and a collaborator reassigning that to somebody
        else is moving someone's work onto them. The person must be an active user of
        the space: a dangling id is worse than none, and a deactivated one would be told
        nothing.

        `None` on an update clears the owner, and is the only way to: on a create it
        means «not said», which is the default above.
        """
        if not creating or (owner_id is not None and owner_id != actor.id):
            actor.require_admin("change_customer_owner")
        if owner_id is None:
            return actor.id if creating else None
        owner = self.users.get(owner_id)
        if owner is None:
            raise NotFound("user", owner_id)
        if not owner.active:
            raise ValidationFailed(
                ENTITY,
                "owner_id",
                "la persona e' disattivata: un cliente si assegna a un utente attivo",
                expected="l'id di un utente attivo",
            )
        return owner.id

    def _insert(self, data: CustomerCreate, actor: Actor) -> Customer:
        """The write half of `create`, without the commit (design record
        `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §7 item 5):
        flushes the row and records its own "created" activity, on `self.session`,
        exactly as `create` does, but leaves the commit to the caller.

        This is the shape mastro's own `confirmClientContractProposal` takes an
        optional `tx: DbExecutor` for instead of opening its own transaction --
        `InvoiceService.confirm_import` is the caller that needs it: a `Customer`
        this method inserts must not outlive a register write that fails a moment
        later, so both have to land in one commit or neither does. `create` is
        `self.session` already; a caller sharing that same session (`InvoiceService`
        composes its own `CustomerService(self.session)`) inserts the customer into
        the exact transaction its own write is about to join.
        """
        actor.require_write("create_customer")
        payload = data.model_dump()
        # Not a column: the first note of the customer, written below by the creating actor.
        first_note = payload.pop("note", None)
        _check_fiscal(payload, payload.get("country") or "IT")
        payload["custom_fields"] = validate_custom_fields(
            ENTITY, self.fields.specs_for(ENTITY), payload.get("custom_fields") or {}
        )
        payload["legal_entity_id"] = self._legal_entity_for(
            payload.get("legal_entity_id"), payload.get("country")
        )
        payload["owner_id"] = self._owner_for(payload.get("owner_id"), actor, creating=True)

        customer = self.repo.add(Customer(**payload))
        self.activities.record(
            ENTITY, customer.id, "created", actor, {"legal_name": customer.legal_name}
        )
        append_first_note(self.session, ENTITY, customer.id, first_note, actor)
        return customer

    def _legal_entity_for(self, legal_entity_id: UUID | None, country: str | None) -> UUID:
        """The azienda a customer is billed by: the one named, which must exist and be
        active, or the one its nation proposes (REB-623, spec §1.6)."""
        if legal_entity_id is None:
            return self.legal_entities.propose(country).id
        legal_entity = self.legal_entities.resolve(legal_entity_id)
        if not legal_entity.active:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "l'azienda non e' attiva: un cliente si assegna a un'azienda attiva",
                expected="l'id di un'azienda attiva",
            )
        return legal_entity.id

    def create(self, data: CustomerCreate, actor: Actor) -> CustomerRead:
        customer = self._insert(data, actor)
        self.session.commit()
        return CustomerRead.model_validate(customer)

    def create_from_suggestions(
        self, data: CustomersFromSuggestions, actor: Actor
    ) -> list[CustomerRead]:
        """The customers a person ticked among the proposals of the connected mailbox
        (`GmailSyncService.suggest_customers`, spec 2026-09-16 §5, REB-223), with their
        people, in one transaction: all of them or none.

        Each customer is created as «Nuovo cliente» creates one (`_insert`, its own
        "created" activity), with the proposal's domain as its website, which is what
        discovery and the backfill read a customer's domain from. Each person is created
        as the Persone page creates one, attached to it. A domain the CRM already files
        under a customer is refused rather than duplicated, so a second click on the same
        proposal cannot make a second Acme; a person whose address is already in the
        address book is left where it is. Only addresses at the proposal's own domain are
        accepted. The batch is not checked against a fresh proposal read, which would
        spend the owner's quota again; it grants nothing «Nuovo cliente» and the Persone
        page do not already grant the same person.
        """
        actor.require_write("create_customer")
        # Before anything is read: a concurrent import of the same proposal waits here
        # and then sees what this one committed (`lock_imports`).
        self.repo.lock_imports()
        roster = AddressRoster(self.session)
        # Only the proposal's own domains and addresses are looked up, not the whole book.
        taken = set(
            roster.domains_taken(
                domain
                for choice in data.customers
                if (domain := customer_domain(website=choice.domain, email=None))
            )
        )
        known = set(
            roster.known_among(
                person.address.strip().lower()
                for choice in data.customers
                for person in choice.people
            )
        )
        # Everything is checked before anything is written, so a refusal names the
        # first thing wrong and leaves the session as it found it.
        plan: list[tuple[str, CustomerFromSuggestion, list[tuple[str, str]]]] = []
        for choice in data.customers:
            domain = customer_domain(website=choice.domain, email=None)
            if domain is None or domain != choice.domain.strip().lower():
                raise ValidationFailed(
                    ENTITY,
                    "domain",
                    f"{choice.domain} non è il dominio di un'azienda",
                    expected="dominio.tld, non una webmail",
                )
            if domain in taken:
                raise Conflict(ENTITY, f"{domain} è già un cliente", domain=domain)
            taken.add(domain)
            people: list[tuple[str, str]] = []
            for person in choice.people:
                address = person.address.strip().lower()
                if not address.endswith(f"@{domain}"):
                    raise ValidationFailed(
                        ENTITY,
                        "people",
                        f"{address} non è un indirizzo di {domain}",
                        expected=f"nome@{domain}",
                    )
                if address not in known:
                    known.add(address)
                    people.append((address, person.name))
            plan.append((domain, choice, people))

        persons = PersonService(self.session)
        created: list[Customer] = []
        try:
            for domain, choice, people in plan:
                customer = self._insert(
                    CustomerCreate(legal_name=choice.legal_name, website=domain), actor
                )
                for address, display in people:
                    first_name, last_name = _person_name(display, address)
                    persons._insert(
                        PersonCreate(
                            first_name=first_name, last_name=last_name, email=address, customer_id=customer.id
                        ),
                        actor,
                    )
                created.append(customer)
        except DomainError:
            # A custom field the space made required, say: nothing of this import stays.
            self.session.rollback()
            raise
        self.session.commit()
        return [CustomerRead.model_validate(customer) for customer in created]

    def add_person_from_mail(
        self, customer_id: UUID, data: PersonFromSuggestion, actor: Actor
    ) -> PersonRead:
        """One address the connected mailbox found at this customer's domain
        (`GmailSyncService.discover`, REB-766), put on the customer as a person.

        The name is read the way the proposals read it (`_person_name`), so the same
        header gives the same person whichever list it was ticked in. Only an address at
        the customer's own domain is accepted, the one the discovery was scoped to, and
        one the address book already holds is refused rather than filed twice. Nothing
        is asked of Gmail: the discovery's answer is not re-read, as with
        `create_from_suggestions`.
        """
        actor.require_write("create_person")
        # Before the address book is read: two presses of the same address, a double click
        # or two tabs, then run one after the other, and the second finds the person the
        # first committed instead of filing it twice (the lock `create_from_suggestions`
        # takes, so an import of the same address waits here too).
        self.repo.lock_imports()
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        domain = customer_domain(website=customer.website, email=customer.email)
        if domain is None:
            raise Conflict(
                ENTITY,
                f"il cliente {customer_id} non ha un dominio: indica il sito web o "
                "un'email aziendale (non webmail) sulla scheda cliente",
            )
        address = data.address.strip().lower()
        if not address.endswith(f"@{domain}"):
            raise ValidationFailed(
                ENTITY,
                "address",
                f"{address} non è un indirizzo di {domain}",
                expected=f"nome@{domain}",
            )
        if AddressRoster(self.session).known_among([address]):
            raise Conflict(ENTITY, f"{address} è già in anagrafica", address=address)
        persons = PersonService(self.session)
        first_name, last_name = _person_name(data.name, address)
        try:
            person = persons._insert(
                PersonCreate(
                    first_name=first_name, last_name=last_name, email=address, customer_id=customer.id
                ),
                actor,
            )
        except DomainError:
            self.session.rollback()
            raise
        self.session.commit()
        return persons._read(person)

    def update(self, customer_id: UUID, data: CustomerUpdate, actor: Actor) -> CustomerRead:
        actor.require_write("update_customer")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"settore": None},
        # meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `merge_custom`.
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Customer, changes)
        # The country after this patch, not merely the one it mentions.
        _check_fiscal(changes, changes.get("country") or customer.country)
        if changes.get("legal_entity_id") is not None:
            # Named, so no proposal: a move is a decision, and it moves nothing already
            # created under the customer (spec §1.7).
            changes["legal_entity_id"] = self._legal_entity_for(changes["legal_entity_id"], None)
        if "owner_id" in changes and changes["owner_id"] != customer.owner_id:
            changes["owner_id"] = self._owner_for(changes["owner_id"], actor, creating=False)
        else:
            # Said again, unchanged: nothing to write and nobody's permission to ask.
            changes.pop("owner_id", None)
        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), customer.custom_fields, data.custom_fields
            )
        for key, value in changes.items():
            setattr(customer, key, value)

        self.activities.record(ENTITY, customer.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return CustomerRead.model_validate(customer)

    def get(self, customer_id: UUID, actor: Actor) -> CustomerRead:
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        return CustomerRead.model_validate(customer)

    def soft_delete(self, customer_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible."""
        actor.require_write("delete_customer")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        # Decided on every active deal, the ones out of this member's sight included
        # (REB-634); said with the ones they see, since the number of the others is a
        # fact about another azienda (Greptile on PR #513).
        if self.repo.count_active_deals(customer_id):
            visible = self.repo.count_visible_active_deals(customer_id)
            raise Conflict(
                ENTITY,
                "il cliente ha deal attivi: archivia prima i deal"
                if visible
                else "il cliente ha deal attivi in un'azienda che non vedi",
                active_deals=visible,
            )

        customer.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, customer.id, "deleted", actor)
        self.session.commit()

    def restore(self, customer_id: UUID, actor: Actor) -> CustomerRead:
        actor.require_write("restore_customer")
        customer = self.repo.get(customer_id, include_deleted=True)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        # Recorded only when the customer really was deleted: unconditionally logging
        # "restored" here -- even for a customer that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened.
        was_deleted = customer.deleted_at is not None
        customer.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, customer.id, "restored", actor)
        self.session.commit()
        return CustomerRead.model_validate(customer)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `PipelineService`'s own ordering for the two other places it already applies):
    # defining a method named `list` rebinds that name in the *class* namespace, so
    # any later method whose own return annotation is a bare `list[...]` would resolve
    # `list` to this method instead of the builtin and fail at import time. No method
    # in this class has that shape today, but the rule does not have a "only when it
    # would currently break" exception -- this is the file People and Deals copy.
    def list(self, query: CustomerListQuery, actor: Actor) -> CustomerPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # The cursor encodes `(sort value, id)` of the last returned row, not the bare
        # id: a scan ordered by a non-unique column cannot resume from an id alone.
        # `getattr(…, spec.key)` is safe precisely because `spec` came out of the
        # whitelist -- the key is one of three literals declared in this package, never
        # a caller-supplied string.
        spec = CUSTOMER_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return CustomerPage(
            items=[CustomerRead.model_validate(c) for c in items],
            next_cursor=next_cursor,
        )
