"""A purchase registered with an inbox item as its justification (REB-1023, the phone's «Spesa»
with a photo): a person's act files the item, an agent's only links it, and an item that is not
waiting is only linked. Postgres is real, as in `test_inbox_upload.py`."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.schemas import InboxDiscard
from pigrocrm.core.inbox.service import InboxService
from pigrocrm.core.inbox.uploads import InboxUploads
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.purchases.schemas import PurchaseCreate, PurchaseUpdate
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.storage.local import LocalFileStorage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


@pytest.fixture
def person(seeded_user_id: UUID) -> Actor:
    return Actor(id=seeded_user_id, type="user", role="admin")


@pytest.fixture
def agent(seeded_user_id: UUID) -> Actor:
    return Actor(id=seeded_user_id, type="mcp", role="admin")


@pytest.fixture
def uploads(db_session: Session, tmp_path: Path) -> InboxUploads:
    return InboxUploads(db_session, LocalFileStorage(tmp_path / "store"))


def _photo(uploads: InboxUploads, actor: Actor, name: str = "scontrino.jpg") -> UUID:
    return uploads.upload([(name, JPEG)], actor).items[0].id


def _register(db_session: Session, category_id: UUID, item_id: UUID, actor: Actor) -> UUID:
    purchase = PurchaseService(db_session).create(
        PurchaseCreate(
            category_id=category_id,
            date=date(2026, 11, 10),
            amount=Decimal("12.50"),
            description="Bar Roma",
            inbox_item_id=item_id,
        ),
        actor,
    )
    assert purchase.inbox_item_id == item_id
    return purchase.id


def _register_any(db_session: Session, category_id: UUID, item_id: UUID, actor: Actor) -> UUID:
    """Registers and answers the item the purchase ended up linked to."""
    purchase = PurchaseService(db_session).create(
        PurchaseCreate(
            category_id=category_id,
            date=date(2026, 11, 10),
            amount=Decimal("12.50"),
            description="Bar Roma",
            inbox_item_id=item_id,
        ),
        actor,
    )
    assert purchase.inbox_item_id is not None
    return purchase.inbox_item_id


def _item(db_session: Session, item_id: UUID) -> InboxItem:
    db_session.expire_all()
    return db_session.get(InboxItem, item_id)  # type: ignore[return-value]


def _timeline(db_session: Session, item_id: UUID) -> list[Activity]:
    return list(
        db_session.execute(
            select(Activity)
            .where(Activity.entity_type == "inbox_item", Activity.entity_id == item_id)
            .order_by(Activity.occurred_at)
        ).scalars()
    )


def _pending_proposal(db_session: Session, item_id: UUID) -> UUID:
    proposal = Proposal(
        target_type="receipt",
        inbox_item_id=item_id,
        excerpt="Totale 12,50",
        confidence=1,
        proposed_fields={},
    )
    db_session.add(proposal)
    db_session.flush()
    item = db_session.get(InboxItem, item_id)
    assert item is not None
    item.state = "proposed"
    item.kind = "receipt"
    db_session.flush()
    return proposal.id


def test_a_person_registering_a_purchase_files_the_item_it_links(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    item_id = _photo(uploads, person)
    assert _item(db_session, item_id).state == "new"

    purchase_id = _register(db_session, seeded_category_id, item_id, person)

    item = _item(db_session, item_id)
    assert item.state == "filed"
    assert item.storage_key is not None  # the photo is still the purchase's file
    filed = [a for a in _timeline(db_session, item_id) if a.kind == "filed"]
    assert len(filed) == 1
    assert filed[0].payload["purchase_id"] == str(purchase_id)


def test_a_proposal_waiting_on_the_item_is_withdrawn_in_the_same_act(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    item_id = _photo(uploads, person)
    proposal_id = _pending_proposal(db_session, item_id)

    _register(db_session, seeded_category_id, item_id, person)

    assert _item(db_session, item_id).state == "filed"
    proposal = db_session.get(Proposal, proposal_id)
    assert proposal is not None and proposal.status == "rejected"


def test_an_agent_linking_an_item_only_links_it(
    db_session: Session,
    uploads: InboxUploads,
    person: Actor,
    agent: Actor,
    seeded_category_id: UUID,
) -> None:
    item_id = _photo(uploads, person)
    proposal_id = _pending_proposal(db_session, item_id)

    _register(db_session, seeded_category_id, item_id, agent)

    item = _item(db_session, item_id)
    assert item.state == "proposed"
    assert [a for a in _timeline(db_session, item_id) if a.kind == "filed"] == []
    proposal = db_session.get(Proposal, proposal_id)
    assert proposal is not None and proposal.status == "pending"


@pytest.mark.parametrize("state", ["filed", "discarded"])
def test_an_item_that_is_not_waiting_is_only_linked(
    db_session: Session,
    uploads: InboxUploads,
    person: Actor,
    seeded_category_id: UUID,
    state: str,
) -> None:
    item_id = _photo(uploads, person)
    item = _item(db_session, item_id)
    item.state = state
    item.reason = "gia' deciso"
    db_session.flush()
    reason = _item(db_session, item_id).reason

    _register(db_session, seeded_category_id, item_id, person)

    after = _item(db_session, item_id)
    assert (after.state, after.reason) == (state, reason)
    assert [a for a in _timeline(db_session, item_id) if a.kind == "filed"] == []


def test_an_item_the_person_does_not_see_is_only_linked(
    db_session: Session,
    uploads: InboxUploads,
    person: Actor,
    seeded_category_id: UUID,
) -> None:
    """Unclassified, an item is its uploader's and an unscoped administrator's: another
    collaborator links it by id and does not file what they could not open."""
    item_id = _photo(uploads, person)
    stranger = Actor(id=UUID(int=7), type="user", role="collaborator")

    _register(db_session, seeded_category_id, item_id, stranger)

    assert _item(db_session, item_id).state == "new"


def test_linking_an_item_on_update_files_it_once(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    first = _photo(uploads, person, "uno.jpg")
    second = uploads.upload([("due.png", PNG)], person).items[0].id
    service = PurchaseService(db_session)
    purchase_id = _register(db_session, seeded_category_id, first, person)

    service.update(purchase_id, PurchaseUpdate(inbox_item_id=second), person)
    assert _item(db_session, second).state == "filed"
    # Sending the same link again is no new link: nothing is recorded twice.
    service.update(purchase_id, PurchaseUpdate(inbox_item_id=second), person)

    filed = [a for a in _timeline(db_session, second) if a.kind == "filed"]
    assert len(filed) == 1


def test_a_person_discarding_a_filed_item_is_refused_as_before(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    """The inbox acts keep their rule: only an item that waits is discarded or re-typed."""
    item_id = _photo(uploads, person)
    _register(db_session, seeded_category_id, item_id, person)

    with pytest.raises(Conflict):
        InboxService(db_session).discard(item_id, InboxDiscard(reason="no"), person)


def test_a_duplicate_is_linked_to_its_original_and_a_person_files_that(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    original = _photo(uploads, person, "uno.jpg")
    copy = _photo(uploads, person, "copia.jpg")  # the same bytes: kept as a duplicate
    assert _item(db_session, copy).state == "duplicate"

    linked = _register_any(db_session, seeded_category_id, copy, person)

    assert linked == original
    assert _item(db_session, original).state == "filed"
    assert _item(db_session, original).storage_key is not None
    kept = _item(db_session, copy)
    assert (kept.state, kept.duplicate_of_id) == ("duplicate", original)


def test_an_agent_linking_a_duplicate_only_links_the_original(
    db_session: Session,
    uploads: InboxUploads,
    person: Actor,
    agent: Actor,
    seeded_category_id: UUID,
) -> None:
    original = _photo(uploads, person, "uno.jpg")
    copy = _photo(uploads, person, "copia.jpg")

    assert _register_any(db_session, seeded_category_id, copy, agent) == original
    assert _item(db_session, original).state == "new"


def test_a_chain_of_two_duplicates_resolves_to_the_root(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    original = _photo(uploads, person, "uno.jpg")
    middle = _photo(uploads, person, "due.jpg")
    last = _photo(uploads, person, "tre.jpg")
    _item(db_session, last).duplicate_of_id = middle
    db_session.flush()

    assert _register_any(db_session, seeded_category_id, last, person) == original


def test_an_original_the_actor_does_not_see_keeps_the_duplicate_id(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    original = _photo(uploads, person, "uno.jpg")
    stranger = Actor(id=UUID(int=7), type="user", role="collaborator")
    copy = _photo(uploads, person, "copia.jpg")
    assert _item(db_session, copy).duplicate_of_id == original

    assert _register_any(db_session, seeded_category_id, copy, stranger) == copy
    assert _item(db_session, original).state == "new"


def test_a_duplicate_linked_on_update_resolves_to_the_original(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    original = _photo(uploads, person, "uno.jpg")
    copy = _photo(uploads, person, "copia.jpg")
    other = uploads.upload([("altra.png", PNG)], person).items[0].id
    purchase_id = _register(db_session, seeded_category_id, other, person)

    updated = PurchaseService(db_session).update(
        purchase_id, PurchaseUpdate(inbox_item_id=copy), person
    )

    assert updated.inbox_item_id == original
    assert _item(db_session, original).state == "filed"


def test_an_item_that_is_not_a_duplicate_is_linked_as_it_is(
    db_session: Session, uploads: InboxUploads, person: Actor, seeded_category_id: UUID
) -> None:
    item_id = _photo(uploads, person)
    assert _register_any(db_session, seeded_category_id, item_id, person) == item_id
