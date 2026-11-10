from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contact_points.service import prepare, write_points
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.notes.writer import append_first_note
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.repository import PersonRepository
from pigrocrm.core.people.schemas import (
    PERSON_SORTS,
    PersonCreate,
    PersonListQuery,
    PersonPage,
    PersonRead,
    PersonUpdate,
)
from pigrocrm.core.schemas import changed_keys, reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# OrganizationService.ENTITY exactly: passing a plain `str` into `specs_for` fails mypy
# strict, which requires the narrower `Literal["organization", "person", "deal"]`.
ENTITY: EntityType = "person"


class PersonService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PersonRepository(session)
        self.organizations = OrganizationRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _check_customer(self, customer_id: UUID | None) -> None:
        """`None` is always accepted -- see `Person`'s own docstring: a contact may
        exist with no customer at all. A supplied id that does not resolve to a live
        row -- absent, or itself soft-deleted, since `OrganizationRepository.get` treats
        both the same unless `include_deleted=True` -- is rejected: a dangling FK is
        worse than no FK."""
        if customer_id is not None and self.organizations.get(customer_id) is None:
            raise NotFound("organization", customer_id)

    def _read(self, person: Person) -> PersonRead:
        """One person's read shape, company name included.

        Every read path in this class goes through here rather than calling
        `PersonRead.model_validate` directly, so a person's "azienda di riferimento"
        cannot depend on which method the caller happened to use -- `create` and
        `update` return the same shape the list and the detail page do.
        """
        return self._reads([person])[0]

    def _reads(self, people: list[Person]) -> list[PersonRead]:
        """The batched form, and the reason `customer_legal_name` is resolved here
        and not inside `PersonRead` itself: one lookup for the whole page (see
        `PersonRepository.customer_names`), so a 50-row list costs two queries rather
        than fifty-one. A schema-level validator or a lazy ORM relationship would both
        put the lookup on the row, which is exactly the N+1 this avoids.

        `model_copy` and not a second `model_validate`: the name is not an attribute of
        `Person` at all, so there is nothing on the ORM object for `from_attributes` to
        read -- and the value comes from a `String` column, already the right type.
        """
        names = self.repo.customer_names(
            {person.customer_id for person in people if person.customer_id is not None}
        )
        return [
            PersonRead.model_validate(person).model_copy(
                update={"customer_legal_name": names.get(person.customer_id)}
                if person.customer_id is not None
                else {}
            )
            for person in people
        ]

    def _insert(self, data: PersonCreate, actor: Actor) -> Person:
        """The write half of `create`, without the commit: the row flushed and its
        "created" activity recorded on `self.session`, the commit left to a caller that
        writes more in the same transaction (`OrganizationService.create_from_suggestions`,
        REB-223), the shape `OrganizationService._insert` already has."""
        actor.require_write("create_person")
        payload = data.model_dump()
        # Not a column: the first note of the person, written below by the creating actor.
        first_note = payload.pop("note", None)
        # Not a column either: the points are written below, once the row has an id. Checked
        # now so that a refusal leaves nothing in the session.
        payload.pop("contact_points")
        prepare(ENTITY, data.contact_points)
        self._check_customer(payload.get("customer_id"))
        payload["custom_fields"] = validate_custom_fields(
            ENTITY, self.fields.specs_for(ENTITY), payload.get("custom_fields") or {}
        )

        person = self.repo.add(Person(**payload))
        write_points(self.session, person, data.contact_points)
        self.activities.record(
            ENTITY, person.id, "created", actor, {"first_name": person.first_name}
        )
        append_first_note(self.session, ENTITY, person.id, first_note, actor)
        return person

    def create(self, data: PersonCreate, actor: Actor) -> PersonRead:
        person = self._insert(data, actor)
        self.session.commit()
        return self._read(person)

    def update(self, person_id: UUID, data: PersonUpdate, actor: Actor) -> PersonRead:
        actor.require_write("update_person")
        person = self.repo.get(person_id)
        if person is None:
            raise NotFound(ENTITY, person_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"seniority":
        # None}, meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `merge_custom`.
        # `customer_id` is excluded here too and handled below, alongside `detach`:
        # the two interact in a way a blind `model_dump` cannot express.
        changes = supplied_changes(
            data, exclude={"custom_fields", "detach", "customer_id", "contact_points"}
        )
        reject_cleared_columns(ENTITY, Person, changes)

        # `detach` is an explicit, self-contained intention and wins outright over any
        # `customer_id` the caller also happens to send in the same payload -- checked
        # first, and once true, `data.customer_id` is never read at all. Two real
        # defects existed here before this ordering: `PersonUpdate(customer_id=<valid>,
        # detach=True)` used to run `_check_customer` and briefly assign the supplied
        # id before detach unconditionally overwrote it back to `None` a few lines
        # later -- wasted work with no error, silently discarding a value the caller
        # may not have intended to throw away. `PersonUpdate(customer_id=<missing>,
        # detach=True)` was worse: `_check_customer` ran *before* `data.detach` was
        # ever consulted, so it raised `NotFound` and aborted the whole update --
        # blocking the detach entirely, even though its own success never depended on
        # that field being valid, or even present. A client re-submitting stale form
        # state alongside an explicit "unlink" action is not exotic. `_check_customer`
        # must never run at all when detaching.
        #
        # `changes["customer_id"]` is only set -- and therefore only appears in the
        # timeline's "changed" list below -- when the person actually had a customer to
        # remove: detaching an already-detached person must not write an audit entry
        # claiming a change that never happened, the same reasoning `restore()` applies
        # via its own `was_deleted` guard.
        if data.detach:
            if person.customer_id is not None:
                changes["customer_id"] = None
        elif data.customer_id is not None:
            self._check_customer(data.customer_id)
            changes["customer_id"] = data.customer_id

        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), person.custom_fields, data.custom_fields
            )
        changed = changed_keys(person, changes)
        for key, value in changes.items():
            setattr(person, key, value)
        # `null` leaves the list alone, as every omitted field is left: an empty list clears it.
        if data.contact_points is not None and write_points(
            self.session, person, data.contact_points
        ):
            changed = sorted([*changed, "contact_points"])

        self.activities.record(ENTITY, person.id, "updated", actor, {"changed": changed})
        self.session.commit()
        return self._read(person)

    def get(self, person_id: UUID, actor: Actor) -> PersonRead:
        person = self.repo.get(person_id)
        if person is None:
            raise NotFound(ENTITY, person_id)
        return self._read(person)

    def soft_delete(self, person_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible."""
        actor.require_write("delete_person")
        person = self.repo.get(person_id)
        if person is None:
            raise NotFound(ENTITY, person_id)
        person.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, person.id, "deleted", actor)
        self.session.commit()

    def restore(self, person_id: UUID, actor: Actor) -> PersonRead:
        """Refuses when the person's customer is archived, mirroring
        `DealService.restore` exactly. `customer_id` is nullable here (unlike on
        `Deal`), so the check is skipped entirely for the common case -- most
        people have no customer at all -- and only applies when one is actually
        set. Checked unconditionally, not only when the person itself was
        actually archived: the invariant is about the person's *current* state
        after this call, not about what changed."""
        actor.require_write("restore_person")
        person = self.repo.get(person_id, include_deleted=True)
        if person is None:
            raise NotFound(ENTITY, person_id)

        if person.customer_id is not None:
            customer = self.organizations.get(person.customer_id, include_deleted=True)
            if customer is not None and customer.deleted_at is not None:
                raise Conflict(
                    ENTITY,
                    "il cliente è archiviato: ripristina prima il cliente",
                    customer_id=str(customer.id),
                )

        # Recorded only when the person really was deleted: unconditionally logging
        # "restored" here -- even for a person that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened. Mirrors the
        # identical guard on OrganizationService.restore.
        was_deleted = person.deleted_at is not None
        person.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, person.id, "restored", actor)
        self.session.commit()
        return self._read(person)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `OrganizationService`'s own ordering): defining a method named `list` rebinds that
    # name in the *class* namespace, so any later method whose own return annotation is
    # a bare `list[...]` would resolve `list` to this method instead of the builtin and
    # fail at import time. No method in this class has that shape today, but the rule
    # has no "only when it would currently break" exception -- this is the file Deals
    # copies.
    def list(self, query: PersonListQuery, actor: Actor) -> PersonPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `OrganizationService.list` for why the cursor carries the sort value as well
        # as the id. Here the value can legitimately be `None` -- `last_name` is
        # nullable -- which is the whole reason the cursor is opaque rather than a
        # query parameter a client could compose.
        spec = PERSON_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return PersonPage(
            items=self._reads(items),
            next_cursor=next_cursor,
        )
