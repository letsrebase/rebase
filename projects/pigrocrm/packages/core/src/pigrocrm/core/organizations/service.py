import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.contact_points.schemas import ContactPointIn
from pigrocrm.core.contact_points.service import company_email_addresses, prepare, write_points
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
from pigrocrm.core.organizations.duplicates import duplicate_vat_groups
from pigrocrm.core.organizations.merge import MergeOutcome, OrganizationMerge
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.organizations.roles import (
    CUSTOMER_KINDS,
    FREELANCER_KINDS,
    HUB_FREELANCER_UNIQUE_INDEX,
    ORGANIZATION_ROLES,
    SUPPLIER_AREAS,
    SUPPLIER_KINDS,
    SUPPLIER_SUPPLIES,
    VAT_NUMBER_MAX_LENGTH,
    VAT_UNIQUE_INDEX,
    normalise_vat,
)
from pigrocrm.core.organizations.schemas import (
    ORGANIZATION_SORTS,
    DuplicateGroupRead,
    DuplicateOrganizationRead,
    OrganizationCreate,
    OrganizationFromSuggestion,
    OrganizationListQuery,
    OrganizationMergeRead,
    OrganizationPage,
    OrganizationRead,
    OrganizationsFromSuggestions,
    OrganizationUpdate,
    PersonFromSuggestion,
)
from pigrocrm.core.people.schemas import (
    LAST_NAME_MAX_LENGTH,
    NAME_MAX_LENGTH,
    PersonCreate,
    PersonRead,
)
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.schemas import changed_keys, reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`) so that passing it
# straight into `specs_for` type-checks under mypy strict -- a plain `ENTITY = "organization"`
# widens to `str` at the module level, which `specs_for(entity_type: EntityType)` then
# rejects. Every other call site here (NotFound, ValidationFailed, Conflict,
# ActivityService.record, validate_custom_fields) only asks for `str`, so the narrower
# type costs nothing there.
ENTITY: EntityType = "organization"
# `.fullmatch()`, not `.match()`, is load-bearing: `.match()` with a `$`-anchored
# pattern accepts a trailing "\n" -- `$` matches just before a final newline, not only
# at the true end of the string -- so "12345678901\n" (12 characters, one more than
# the `String(11)` column) used to pass this check and reach `flush()` as a raw,
# session-poisoning `DataError`. `.fullmatch()` requires the *entire* string to be
# consumed, which has no such exception. A pasted VAT number or an MCP agent's tool
# call carrying a trailing newline is not a lab-only case.
VAT_NUMBER_RE = re.compile(r"^\d{11}$")
VAT_ASCII_RE = re.compile(r"[\x20-\x7e]+")
SDI_CODE_LENGTH = 7
IPA_CODE_LENGTH = 6


def _check_fiscal(data: dict[str, Any], country: str, *, customer_kind: str | None = None) -> None:
    """Mutates `data` in place: an empty string is normalized to `None` for both
    fiscal fields before either is checked. Without this, `if piva`/`if sdi` below are
    falsy on "", so an empty string skipped the check entirely and was stored as "" --
    a different thing from "not provided" that would, for instance, wrongly satisfy a
    future "has a VAT number" filter. Shared by `create` and `update`, so the
    normalization applies equally to a brand-new row and to a patch that clears the
    field with "".

    `customer_kind` is the kind the row will have after this write: a public body's
    `sdi_code` is its 6-character IPA code, every other recipient's is 7 characters.
    """
    if data.get("vat_number") == "":
        data["vat_number"] = None
    if data.get("sdi_code") == "":
        data["sdi_code"] = None

    vat_number = data.get("vat_number")
    # ASCII only: the comparison key is computed by Python on the way in and by Postgres in the
    # index, which fold the case of a letter like ß differently. Letters, digits and the
    # punctuation a number is written with are all a VAT number holds.
    if vat_number and not VAT_ASCII_RE.fullmatch(vat_number):
        raise ValidationFailed(
            ENTITY,
            "vat_number",
            "deve contenere solo lettere, cifre e punteggiatura ASCII",
            expected="lettere A-Z, cifre, spazi e punteggiatura",
        )
    # The eleven-digit rule is Italian, so it is applied to Italian organizations and to
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
    # Any other country: as long as the column, and with something to compare in it, since
    # two organizations are the same company when their numbers are the same once the
    # punctuation is gone.
    if vat_number and country.upper() != "IT":
        if len(vat_number) > VAT_NUMBER_MAX_LENGTH:
            raise ValidationFailed(
                ENTITY,
                "vat_number",
                f"deve essere di al massimo {VAT_NUMBER_MAX_LENGTH} caratteri",
                expected=f"al massimo {VAT_NUMBER_MAX_LENGTH} caratteri",
            )
        if not normalise_vat(vat_number):
            raise ValidationFailed(
                ENTITY,
                "vat_number",
                "deve contenere lettere o cifre",
                expected="una partita IVA estera, ad esempio NL123456789B01",
            )
    sdi = data.get("sdi_code")
    if sdi and customer_kind == "public_body":
        if len(sdi) != IPA_CODE_LENGTH:
            raise ValidationFailed(
                ENTITY,
                "sdi_code",
                "per un ente pubblico e' il codice IPA, di 6 caratteri",
                expected="6 caratteri",
            )
    elif sdi and len(sdi) != SDI_CODE_LENGTH:
        raise ValidationFailed(
            ENTITY, "sdi_code", "deve essere di 7 caratteri", expected="7 caratteri"
        )


def _one_of(field: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValidationFailed(
            ENTITY,
            field,
            f"valore non valido: {value}",
            expected=" | ".join(allowed),
        )


def _check_roles_and_kinds(data: dict[str, Any], current: Organization | None) -> None:
    """The role set and the fiscal kinds of a write, against the row they will describe:
    `current` is the stored row on an update and `None` on a create. Normalises `roles` in
    place (no repeats, order kept).

    A foreign supplier says where it is and what it supplies; a supplier of any other kind
    does not, since those two answers only mean something for reverse charge (design § 1).
    """
    if "roles" in data:
        roles = data["roles"]
        if not roles:
            raise ValidationFailed(
                ENTITY,
                "roles",
                "serve almeno un ruolo",
                expected=" | ".join(ORGANIZATION_ROLES),
            )
        for role in roles:
            _one_of("roles", role, ORGANIZATION_ROLES)
        data["roles"] = list(dict.fromkeys(roles))
    for field, allowed in (
        ("supplier_kind", SUPPLIER_KINDS),
        ("supplier_area", SUPPLIER_AREAS),
        ("supplier_supplies", SUPPLIER_SUPPLIES),
        ("customer_kind", CUSTOMER_KINDS),
    ):
        if data.get(field) is not None:
            _one_of(field, data[field], allowed)

    def after(field: str) -> Any:
        if field in data:
            return data[field]
        return getattr(current, field) if current is not None else None

    if after("supplier_kind") != "foreign" and (
        after("supplier_area") is not None or after("supplier_supplies") is not None
    ):
        raise ValidationFailed(
            ENTITY,
            "supplier_kind",
            "area e tipo di fornitura valgono solo per un fornitore estero",
            expected="foreign",
        )
    if "freelancer" in after("roles") and after("supplier_kind") not in (None, *FREELANCER_KINDS):
        raise ValidationFailed(
            ENTITY,
            "supplier_kind",
            "un freelance e' un professionista, un forfettario o una prestazione occasionale",
            expected=" | ".join(FREELANCER_KINDS),
        )
    hub_id = after("hub_freelancer_id")
    if hub_id is not None:
        if "freelancer" not in after("roles"):
            raise ValidationFailed(
                ENTITY,
                "hub_freelancer_id",
                "l'id dell'hub vale solo per chi ha il ruolo di freelance",
                expected="freelancer",
            )
        stored = current.hub_freelancer_id if current is not None else None
        if stored is not None and stored != hub_id:
            raise ValidationFailed(
                ENTITY,
                "hub_freelancer_id",
                "l'id dell'hub di un freelance non cambia: e' la sua identita'",
            )
    elif current is not None and current.hub_freelancer_id is not None:
        raise ValidationFailed(
            ENTITY,
            "hub_freelancer_id",
            "l'id dell'hub di un freelance non si toglie: e' la sua identita'",
        )


def _vat_key(country: str, vat_number: str | None) -> tuple[str, str] | None:
    """What two organizations are the same company by: the country and the VAT number
    without case and punctuation. `None` when there is nothing to compare."""
    normalised = normalise_vat(vat_number) if vat_number else ""
    return (country.upper(), normalised) if normalised else None


def _vat_taken(
    country: str, vat_number: str, legal_name: str | None, existing_id: UUID | None
) -> Conflict:
    who = f" ({legal_name})" if legal_name else ""
    return Conflict(
        ENTITY,
        f"esiste gia' un'anagrafica con la partita IVA {vat_number} ({country.upper()}){who}: "
        "e' la stessa azienda, usa quella e aggiungi il ruolo che manca",
        field="vat_number",
        **({"existing_id": str(existing_id)} if existing_id is not None else {}),
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


class OrganizationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = OrganizationRepository(session)
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

    def _insert(self, data: OrganizationCreate, actor: Actor) -> Organization:
        """The write half of `create`, without the commit (design record
        `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §7 item 5):
        flushes the row and records its own "created" activity, on `self.session`,
        exactly as `create` does, but leaves the commit to the caller.

        This is the shape mastro's own `confirmClientContractProposal` takes an
        optional `tx: DbExecutor` for instead of opening its own transaction --
        `InvoiceService.confirm_import` is the caller that needs it: a `Organization`
        this method inserts must not outlive a register write that fails a moment
        later, so both have to land in one commit or neither does. `create` is
        `self.session` already; a caller sharing that same session (`InvoiceService`
        composes its own `OrganizationService(self.session)`) inserts the customer into
        the exact transaction its own write is about to join.
        """
        actor.require_write("create_organization")
        payload = data.model_dump()
        # Not a column: the first note of the customer, written below by the creating actor.
        first_note = payload.pop("note", None)
        # Not a column either: the points are written below, once the row has an id. Checked
        # now so that a refusal leaves nothing in the session.
        payload.pop("contact_points")
        prepare(ENTITY, data.contact_points)
        _check_fiscal(
            payload, payload.get("country") or "IT", customer_kind=payload.get("customer_kind")
        )
        _check_roles_and_kinds(payload, None)
        if payload.get("hub_freelancer_id") is not None:
            actor.require_admin("set_organization_hub_freelancer")
        self._check_vat_free(payload.get("country") or "IT", payload.get("vat_number"), None)
        payload["custom_fields"] = validate_custom_fields(
            ENTITY, self.fields.specs_for(ENTITY), payload.get("custom_fields") or {}
        )
        payload["legal_entity_id"] = self._legal_entity_for(
            payload.get("legal_entity_id"), payload.get("country")
        )
        payload["owner_id"] = self._owner_for(payload.get("owner_id"), actor, creating=True)

        try:
            # A savepoint: a refusal by the index (two requests racing for one number) leaves
            # the caller's transaction, and whatever it wrote before this, standing.
            with self.session.begin_nested():
                customer = self.repo.add(Organization(**payload))
        except IntegrityError as exc:
            raise self._vat_refused(
                exc, payload.get("country") or "IT", payload.get("vat_number")
            ) from exc
        write_points(self.session, customer, data.contact_points)
        self.activities.record(
            ENTITY, customer.id, "created", actor, {"legal_name": customer.legal_name}
        )
        append_first_note(self.session, ENTITY, customer.id, first_note, actor)
        return customer

    def _check_vat_free(self, country: str, vat_number: str | None, excluding: UUID | None) -> None:
        """Refuses a VAT number a live organization of the same country already holds, in
        any role: one record per company (design § 1). The unique index says the same when
        two requests race, and `_vat_refused` turns its error into this one."""
        if not vat_number:
            return
        found = self.repo.find_by_vat(country, vat_number, excluding=excluding)
        if found is not None:
            raise _vat_taken(country, vat_number, found.legal_name, found.id)

    def _vat_refused(
        self,
        exc: IntegrityError,
        country: str,
        vat_number: str | None,
        excluding: UUID | None = None,
    ) -> Exception:
        """What a database refusal of a write becomes: the sentence for the VAT number when
        it is the unique index that refused, the original error otherwise. `country` and
        `vat_number` are the ones the write attempted, and `excluding` the row it was for."""
        if HUB_FREELANCER_UNIQUE_INDEX in str(exc.orig):
            return Conflict(
                ENTITY,
                "esiste gia' un'anagrafica con questo id dell'hub: e' lo stesso freelance",
                field="hub_freelancer_id",
            )
        if VAT_UNIQUE_INDEX not in str(exc.orig) or not vat_number:
            return exc
        found = self.repo.find_by_vat(country, vat_number, excluding=excluding)
        return _vat_taken(
            country,
            vat_number,
            found.legal_name if found else None,
            found.id if found else None,
        )

    def _legal_entity_for(self, legal_entity_id: UUID | None, country: str | None) -> UUID:
        """The legal entity a customer is billed by: the one named, which must exist and be
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

    def create(self, data: OrganizationCreate, actor: Actor) -> OrganizationRead:
        customer = self._insert(data, actor)
        self.session.commit()
        return OrganizationRead.model_validate(customer)

    def create_from_suggestions(
        self, data: OrganizationsFromSuggestions, actor: Actor
    ) -> list[OrganizationRead]:
        """The organizations a person ticked among the proposals of the connected mailbox
        (`GmailSyncService.suggest_organizations`, spec 2026-09-16 §5, REB-223), with their
        people, in one transaction: all of them or none.

        Each customer is created as «Nuovo cliente» creates one (`_insert`, its own
        "created" activity), with the proposal's domain as its website, which is what
        discovery and the backfill read a customer's domain from. Each person is created
        as the «Persone» page creates one, attached to it. A domain the CRM already files
        under a customer is refused rather than duplicated, so a second click on the same
        proposal cannot make a second Acme; a person whose address is already in the
        address book is left where it is. Only addresses at the proposal's own domain are
        accepted. The batch is not checked against a fresh proposal read, which would
        spend the owner's quota again; it grants nothing «Nuovo cliente» and the «Persone»
        page do not already grant the same person.
        """
        actor.require_write("create_organization")
        # Before anything is read: a concurrent import of the same proposal waits here
        # and then sees what this one committed (`lock_imports`).
        self.repo.lock_imports()
        roster = AddressRoster(self.session)
        # Only the proposal's own domains and addresses are looked up, not the whole book.
        taken = set(
            roster.domains_taken(
                domain
                for choice in data.organizations
                if (domain := customer_domain(website=choice.domain, email=None))
            )
        )
        known = set(
            roster.known_among(
                person.address.strip().lower()
                for choice in data.organizations
                for person in choice.people
            )
        )
        # Everything is checked before anything is written, so a refusal names the
        # first thing wrong and leaves the session as it found it.
        plan: list[tuple[str, OrganizationFromSuggestion, list[tuple[str, str]]]] = []
        for choice in data.organizations:
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
        created: list[Organization] = []
        try:
            for domain, choice, people in plan:
                customer = self._insert(
                    OrganizationCreate(legal_name=choice.legal_name, website=domain), actor
                )
                for address, display in people:
                    first_name, last_name = _person_name(display, address)
                    persons._insert(
                        PersonCreate(
                            first_name=first_name,
                            last_name=last_name,
                            contact_points=[ContactPointIn(kind="email", value=address)],
                            customer_id=customer.id,
                        ),
                        actor,
                    )
                created.append(customer)
        except DomainError:
            # A custom field the space made required, say: nothing of this import stays.
            self.session.rollback()
            raise
        self.session.commit()
        return [OrganizationRead.model_validate(customer) for customer in created]

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
        domain = customer_domain(
            website=customer.website,
            email=customer.email,
            other_emails=company_email_addresses(customer),
        )
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
                    first_name=first_name,
                    last_name=last_name,
                    contact_points=[ContactPointIn(kind="email", value=address)],
                    customer_id=customer.id,
                ),
                actor,
            )
        except DomainError:
            self.session.rollback()
            raise
        self.session.commit()
        return persons._read(person)

    def update(self, customer_id: UUID, data: OrganizationUpdate, actor: Actor) -> OrganizationRead:
        actor.require_write("update_organization")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"settore": None},
        # meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `merge_custom`.
        changes = supplied_changes(data, exclude={"custom_fields", "contact_points"})
        reject_cleared_columns(ENTITY, Organization, changes)
        # The country after this patch, not merely the one it mentions. A change of kind alone
        # re-checks the stored recipient code against the length the new kind asks for.
        fiscal = dict(changes)
        if "customer_kind" in changes and "sdi_code" not in changes:
            fiscal["sdi_code"] = customer.sdi_code
        _check_fiscal(
            fiscal,
            changes.get("country") or customer.country,
            customer_kind=changes.get("customer_kind", customer.customer_kind),
        )
        for key in ("vat_number", "sdi_code"):
            if key in changes:
                changes[key] = fiscal[key]
        _check_roles_and_kinds(changes, customer)
        if changes.get("hub_freelancer_id") not in (None, customer.hub_freelancer_id):
            actor.require_admin("set_organization_hub_freelancer")
        # Only when the key the row is found by changes. A form resends the number and the
        # country it loaded, and a row of a legacy pair (two rows, one number) must stay
        # editable until a person resolves the pair.
        attempted_country = changes.get("country") or customer.country
        attempted_vat = changes.get("vat_number", customer.vat_number)
        if _vat_key(attempted_country, attempted_vat) not in (
            None,
            _vat_key(customer.country, customer.vat_number),
        ):
            self._check_vat_free(attempted_country, attempted_vat, customer.id)
        if "legal_entity_id" in changes and changes["legal_entity_id"] is None:
            # An explicit `null` shares the organization across the legal entities (REB-970).
            actor.require_admin("share_organization")
            if customer.legal_entity_id is None:
                del changes["legal_entity_id"]
        elif changes.get("legal_entity_id") is not None:
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
        changed = changed_keys(customer, changes)
        try:
            # A savepoint taken before the first change (it flushes what is pending on entry,
            # so a change made first would be flushed outside it), so a refusal by the index
            # (two requests racing for one number) undoes this write only. The values it
            # attempted are the ones named, read before the rollback expires the row.
            with self.session.begin_nested():
                for key, value in changes.items():
                    setattr(customer, key, value)
                self.session.flush()
        except IntegrityError as exc:
            raise self._vat_refused(
                exc, attempted_country, attempted_vat, excluding=customer.id
            ) from exc
        # `null` leaves the list alone, as every omitted field is left: an empty list clears it.
        if data.contact_points is not None and write_points(
            self.session, customer, data.contact_points
        ):
            changed = sorted([*changed, "contact_points"])

        self.activities.record(ENTITY, customer.id, "updated", actor, {"changed": changed})
        self.session.commit()
        return OrganizationRead.model_validate(customer)

    def get(self, customer_id: UUID, actor: Actor) -> OrganizationRead:
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        return OrganizationRead.model_validate(customer)

    def soft_delete(self, customer_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible."""
        actor.require_write("delete_organization")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        # Decided on every active deal, the ones out of this member's sight included
        # (REB-634); said with the ones they see, since the number of the others is a
        # fact about another legal entity (Greptile on PR #513).
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

    def restore(self, customer_id: UUID, actor: Actor) -> OrganizationRead:
        actor.require_write("restore_organization")
        customer = self.repo.get(customer_id, include_deleted=True)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        # Recorded only when the customer really was deleted: unconditionally logging
        # "restored" here -- even for a customer that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened.
        was_deleted = customer.deleted_at is not None
        if was_deleted:
            # Its number may have been given to another organization while it was archived.
            self._check_vat_free(customer.country, customer.vat_number, customer.id)
        customer.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, customer.id, "restored", actor)
        self.session.commit()
        return OrganizationRead.model_validate(customer)

    def _share(self, organization: Organization, actor: Actor) -> None:
        """Make `organization` shared across the legal entities of the space, without the
        commit (REB-970, design § 1 «Scope»): one company used by two of them is one row, and
        the timeline says it was widened. Idempotent."""
        if organization.legal_entity_id is None:
            return
        organization.legal_entity_id = None
        self.activities.record(
            ENTITY, organization.id, "updated", actor, {"changed": ["legal_entity_id"]}
        )

    def share(self, organization_id: UUID, actor: Actor) -> OrganizationRead:
        """`legal_entity_id` to `NULL`: the organization stays one row and becomes visible in
        every legal entity's scope. An administrator's decision, since it shows the company to
        people who could not see it."""
        actor.require_admin("share_organization")
        organization = self.repo.get(organization_id)
        if organization is None:
            raise NotFound(ENTITY, organization_id)
        self._share(organization, actor)
        self.session.commit()
        return OrganizationRead.model_validate(organization)

    def duplicates(self, actor: Actor) -> list[DuplicateGroupRead]:
        """The live organizations that share a country and a VAT number, in groups, for a
        person to resolve by archiving one or correcting a number (design § 1). The whole
        registry or nothing: a report read through a legal entity's scope would call a
        group resolved that is not."""
        actor.require_unscoped_admin("list_duplicate_organizations")
        return [
            DuplicateGroupRead(
                country=group.country,
                vat_key=group.vat_key,
                organizations=[
                    DuplicateOrganizationRead(
                        id=row.id,
                        legal_name=row.legal_name,
                        vat_number=row.vat_number,
                        roles=list(row.roles),
                        legal_entity_id=row.legal_entity_id,
                        created_at=row.created_at,
                    )
                    for row in group.organizations
                ],
            )
            for group in duplicate_vat_groups(self.session)
        ]

    def merge(
        self, survivor_id: UUID, loser_id: UUID, actor: Actor, *, dry_run: bool = True
    ) -> OrganizationMergeRead:
        """Folds `loser_id` into `survivor_id` (REB-995, design § 1): every deal, contract,
        invoice, person, contact point, document, task, note, timeline entry, Gmail link, draft,
        due item and engagement that pointed at the loser points at the survivor, the roles are
        joined, what the survivor left empty is filled from the loser, and the loser is
        archived, each timeline saying so. The whole registry or nothing, so an administrator of
        the whole space only; a row that sits under a legal entity the caller cannot see would
        otherwise stay behind. With `dry_run` it answers what would move and writes nothing.

        Two rows under different legal entities are one company used by two of them, so the
        survivor becomes shared (`legal_entity_id` null), as `share` does by hand."""
        actor.require_unscoped_admin("merge_organizations")
        if survivor_id == loser_id:
            raise ValidationFailed(
                ENTITY,
                "loser_id",
                "un'anagrafica non si unisce a se stessa",
                expected="un'anagrafica diversa da quella di destinazione",
            )
        rows = {
            row.id: row
            for row in self.session.scalars(
                select(Organization)
                .where(Organization.id.in_((survivor_id, loser_id)))
                .order_by(Organization.id)
                .with_for_update()
            )
        }
        survivor, loser = rows.get(survivor_id), rows.get(loser_id)
        if survivor is None:
            raise NotFound(ENTITY, survivor_id)
        if loser is None:
            raise NotFound(ENTITY, loser_id)
        for row in (survivor, loser):
            if row.deleted_at is not None:
                raise Conflict(
                    ENTITY,
                    "un'anagrafica archiviata non si unisce: ripristinala prima",
                    organization_id=str(row.id),
                )
        if loser.hub_freelancer_id is not None and survivor.hub_freelancer_id is not None:
            raise Conflict(
                ENTITY,
                "le due anagrafiche sono due freelance diversi dell'hub",
                survivor_hub_freelancer_id=str(survivor.hub_freelancer_id),
                loser_hub_freelancer_id=str(loser.hub_freelancer_id),
            )
        survivor_name, loser_name = survivor.legal_name, loser.legal_name

        try:
            outcome = OrganizationMerge(self.session).run(survivor, loser, apply=not dry_run)
            if dry_run:
                # Nothing was written; this only lets go of the two row locks.
                self.session.commit()
                return self._merge_read(
                    survivor_id, loser_id, survivor_name, loser_name, outcome, dry_run
                )
            self._record_merge(survivor, loser, outcome, actor)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                ENTITY,
                "l'unione non e' riuscita: un'altra modifica ha toccato le due anagrafiche",
                organization_id=str(survivor_id),
            ) from exc
        read = self._merge_read(survivor_id, loser_id, survivor_name, loser_name, outcome, dry_run)
        read.survivor = OrganizationRead.model_validate(
            self.session.get_one(Organization, survivor_id)
        )
        return read

    def _record_merge(
        self, survivor: Organization, loser: Organization, outcome: MergeOutcome, actor: Actor
    ) -> None:
        if outcome.shares_survivor:
            self.activities.record(
                ENTITY, survivor.id, "updated", actor, {"changed": ["legal_entity_id"]}
            )
        # The loser's timeline moved with the rest, so what it keeps is this one line: where
        # everything went. The survivor's says what came in.
        self.activities.record(
            ENTITY,
            loser.id,
            "merged_into",
            actor,
            {"organization_id": str(survivor.id), "legal_name": survivor.legal_name},
        )
        self.activities.record(
            ENTITY,
            survivor.id,
            "merged",
            actor,
            {
                "organization_id": str(loser.id),
                "legal_name": loser.legal_name,
                "moved": outcome.moved,
                "dropped": outcome.dropped,
            },
        )

    @staticmethod
    def _merge_read(
        survivor_id: UUID,
        loser_id: UUID,
        survivor_name: str,
        loser_name: str,
        outcome: MergeOutcome,
        dry_run: bool,
    ) -> OrganizationMergeRead:
        return OrganizationMergeRead(
            dry_run=dry_run,
            survivor_id=survivor_id,
            survivor_name=survivor_name,
            loser_id=loser_id,
            loser_name=loser_name,
            moved=outcome.moved,
            total_moved=outcome.total_moved,
            dropped=outcome.dropped,
            roles=outcome.roles,
            filled_fields=outcome.filled_fields,
            custom_fields_added=outcome.custom_fields_added,
            shares_survivor=outcome.shares_survivor,
            moves_hub_freelancer_id=outcome.moves_hub_freelancer_id,
        )

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `PipelineService`'s own ordering for the two other places it already applies):
    # defining a method named `list` rebinds that name in the *class* namespace, so
    # any later method whose own return annotation is a bare `list[...]` would resolve
    # `list` to this method instead of the builtin and fail at import time. No method
    # in this class has that shape today, but the rule does not have a "only when it
    # would currently break" exception -- this is the file People and Deals copy.
    def list(self, query: OrganizationListQuery, actor: Actor) -> OrganizationPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # The cursor encodes `(sort value, id)` of the last returned row, not the bare
        # id: a scan ordered by a non-unique column cannot resume from an id alone.
        # `getattr(…, spec.key)` is safe precisely because `spec` came out of the
        # whitelist -- the key is one of three literals declared in this package, never
        # a caller-supplied string.
        spec = ORGANIZATION_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return OrganizationPage(
            items=[OrganizationRead.model_validate(c) for c in items],
            next_cursor=next_cursor,
            total=self.repo.count(query),
        )
