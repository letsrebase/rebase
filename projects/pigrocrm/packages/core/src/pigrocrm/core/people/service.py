import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
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
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY exactly: passing a plain `str` into `specs_for` fails mypy
# strict, which requires the narrower `Literal["customer", "person", "deal"]`.
ENTITY: EntityType = "person"
# `.fullmatch()`, not `.match()`, is load-bearing -- identical reasoning to
# `PARTITA_IVA_RE` in customers/service.py: `.match()` with a `$`-anchored pattern
# accepts a trailing "\n", because `$` matches just before a final newline, not only
# at the true end of the string. "a@b.it\n" would pass `^...$` under `.match()` and
# could reach `flush()` as a raw, session-poisoning `DataError` once combined with a
# value long enough to exceed the `String(320)` column. This was the Critical finding
# on Customers; `.fullmatch()` requires the entire string to be consumed, which has no
# such exception.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(data: dict[str, Any]) -> None:
    """Mutates `data` in place: an empty string is normalized to `None` before the
    format check runs, exactly mirroring `_check_fiscal` in customers/service.py.
    Without this, `if email` below is falsy on "", so an empty string skips the check
    entirely and would be stored as "" -- a different thing from "not provided" that
    would, for instance, wrongly satisfy a future "has an email" filter. Shared by
    `create` and `update`, so the normalization applies equally to a brand-new row and
    to a patch that clears the field with "".
    """
    if data.get("email") == "":
        data["email"] = None

    email = data.get("email")
    if email and not EMAIL_RE.fullmatch(email):
        raise ValidationFailed(ENTITY, "email", "indirizzo non valido", expected="nome@dominio.it")


class PersonService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PersonRepository(session)
        self.customers = CustomerRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _check_customer(self, customer_id: UUID | None) -> None:
        """`None` is always accepted -- see `Person`'s own docstring: a contact may
        exist with no customer at all. A supplied id that does not resolve to a live
        row -- absent, or itself soft-deleted, since `CustomerRepository.get` treats
        both the same unless `include_deleted=True` -- is rejected: a dangling FK is
        worse than no FK."""
        if customer_id is not None and self.customers.get(customer_id) is None:
            raise NotFound("customer", customer_id)

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition -- a required-but-absent field is genuinely missing here, not
        merely untouched, unlike on a partial `update` (see `_update_custom_fields`).
        Mirrors `CustomerService._validated_custom` exactly."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, person: Person, provided: dict[str, Any]) -> dict[str, Any]:
        """Copies `CustomerService._update_custom_fields`'s contract exactly (see that
        method's docstring in customers/service.py for the full reasoning): validates
        only the keys the caller is touching, against active definitions -- never the
        union with what is already stored on `person`. Validating the union would
        contradict Task 7's contract for archiving a field ("hide it, keep the data
        readable"): an archived key still present in storage would fail the "campo non
        definito" check on every future update, even one that never mentions that key.

        A key supplied with `None` removes that entry from the stored dict, unless the
        key currently belongs to an active, `required=True` definition -- in which case
        it raises the same "campo obbligatorio" `ValidationFailed` that
        `validate_custom_fields` raises for `""`. `None` and `""` are two spellings of
        "this field has no value"; on a *required*, currently active field the two must
        be rejected identically, or a caller strips the value just by choosing the
        other spelling. Archived, undefined, or non-required keys keep allowing removal
        via `None` even if the definition was required back when it was active --
        clearing an archived field's stored value must stay possible.

        A key supplied with any other (non-blank) value must belong to a currently
        active definition. Keys already stored that `provided` does not mention --
        archived or not, required or not -- are carried over untouched.
        """
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}

        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)

        to_set = {key: value for key, value in provided.items() if value is not None}
        touched_specs = [spec for spec in active_by_key.values() if spec.key in to_set]
        validated = validate_custom_fields(ENTITY, touched_specs, to_set)

        merged = {k: v for k, v in person.custom_fields.items() if k not in to_remove}
        merged.update(validated)
        return merged

    def _read(self, person: Person) -> PersonRead:
        """One person's read shape, company name included.

        Every read path in this class goes through here rather than calling
        `PersonRead.model_validate` directly, so a person's "azienda di riferimento"
        cannot depend on which method the caller happened to use -- `create` and
        `update` return the same shape the list and the detail page do.
        """
        return self._reads([person])[0]

    def _reads(self, people: list[Person]) -> list[PersonRead]:
        """The batched form, and the reason `customer_ragione_sociale` is resolved here
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
                update={"customer_ragione_sociale": names.get(person.customer_id)}
                if person.customer_id is not None
                else {}
            )
            for person in people
        ]

    def _insert(self, data: PersonCreate, actor: Actor) -> Person:
        """The write half of `create`, without the commit: the row flushed and its
        "created" activity recorded on `self.session`, the commit left to a caller that
        writes more in the same transaction (`CustomerService.create_from_suggestions`,
        REB-223), the shape `CustomerService._insert` already has."""
        actor.require_write("create_person")
        payload = data.model_dump()
        _check_email(payload)
        self._check_customer(payload.get("customer_id"))
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})

        person = self.repo.add(Person(**payload))
        self.activities.record(ENTITY, person.id, "created", actor, {"nome": person.nome})
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
        # confused with the field itself being absent -- see `_update_custom_fields`.
        # `customer_id` is excluded here too and handled below, alongside `detach`:
        # the two interact in a way a blind `model_dump` cannot express.
        changes = supplied_changes(data, exclude={"custom_fields", "detach", "customer_id"})
        reject_cleared_columns(ENTITY, Person, changes)
        _check_email(changes)

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
            changes["custom_fields"] = self._update_custom_fields(person, data.custom_fields)
        for key, value in changes.items():
            setattr(person, key, value)

        self.activities.record(ENTITY, person.id, "updated", actor, {"changed": sorted(changes)})
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
            customer = self.customers.get(person.customer_id, include_deleted=True)
            if customer is not None and customer.deleted_at is not None:
                raise Conflict(
                    ENTITY,
                    "il cliente è archiviato: ripristina prima il cliente",
                    customer_id=str(customer.id),
                )

        # Recorded only when the person really was deleted: unconditionally logging
        # "restored" here -- even for a person that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened. Mirrors the
        # identical guard on CustomerService.restore.
        was_deleted = person.deleted_at is not None
        person.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, person.id, "restored", actor)
        self.session.commit()
        return self._read(person)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `CustomerService`'s own ordering): defining a method named `list` rebinds that
    # name in the *class* namespace, so any later method whose own return annotation is
    # a bare `list[...]` would resolve `list` to this method instead of the builtin and
    # fail at import time. No method in this class has that shape today, but the rule
    # has no "only when it would currently break" exception -- this is the file Deals
    # copies.
    def list(self, query: PersonListQuery, actor: Actor) -> PersonPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `CustomerService.list` for why the cursor carries the sort value as well
        # as the id. Here the value can legitimately be `None` -- `cognome` is
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
