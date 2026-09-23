"""The customers a connected mailbox proposes, and importing the ones a person ticks
(spec 2026-09-16 §5, REB-223).

Against the recording `FakeGmail`, so the assertions are about what was asked of
Google as much as what came back: the one listing is the mailbox's own sent mail over
the period, each conversation is read as headers only, and nothing is stored.
"""

import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fakes.fake_gmail import FakeGmail, FakeMessage
from fakes.gmail_fixtures import MAILBOX, actor_for, connected_account, sync_service
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CUSTOMERS_LOCK_NAMESPACE, CustomerRepository
from pigrocrm.core.customers.schemas import CustomersFromSuggestions
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.errors import AgentForbidden, Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.gmail import sync as sync_module
from pigrocrm.core.gmail.errors import GmailUnavailable, GoogleCallFailed, UpstreamFailure
from pigrocrm.core.gmail.models import GmailMessage
from pigrocrm.core.gmail.query import sent_since_query, thread_metadata_url
from pigrocrm.core.gmail.roster import AddressRoster
from pigrocrm.core.gmail.sync import company_name_from_domain
from pigrocrm.core.people.models import Person

OWN = MAILBOX  # io@example.it: the mailbox's own domain is example.it


def _mail(
    index: int,
    *,
    frm: str,
    to: str,
    thread: str,
    days_ago: float = 1.0,
    cc: str = "",
    draft: bool = False,
) -> FakeMessage:
    stamp = int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)
    headers = {
        "From": frm,
        "To": to,
        "Subject": f"Oggetto riservato {index}",
        "Message-ID": f"<msg{index}@example.it>",
    }
    if cc:
        headers["Cc"] = cc
    return FakeMessage(
        id=f"m{index}",
        thread_id=thread,
        headers=headers,
        body_text=f"corpo riservato {index}",
        internal_date_ms=stamp,
        label_ids=["DRAFT"] if draft else ["SENT"],
    )


def _mailbox() -> FakeGmail:
    """A small year: Acme twice, one thread with a colleague in copy who answers; Studio
    Rossi once; a colleague at the owner's own domain; a webmail friend; a PEC; a
    newsletter that only ever wrote in; and a conversation from two years ago."""
    fake = FakeGmail()
    mails = [
        _mail(1, frm=OWN, to="Marco Bianchi <marco@acme.it>", thread="t1", days_ago=40),
        _mail(
            2,
            frm="Sara Verdi <sara@acme.it>",
            to=OWN,
            cc="marco@acme.it",
            thread="t1",
            days_ago=39,
        ),
        _mail(3, frm=OWN, to="marco@acme.it", thread="t2", days_ago=3),
        _mail(4, frm=OWN, to="Anna <anna@studio-rossi.it>", thread="t3", days_ago=100),
        _mail(5, frm=OWN, to="collega@example.it", thread="t4", days_ago=5),
        _mail(6, frm=OWN, to="amico@gmail.com", thread="t5", days_ago=6),
        _mail(7, frm=OWN, to="ufficio@pec.acme.it", thread="t6", days_ago=7),
        _mail(8, frm="news@newsletter.com", to=OWN, thread="t7", days_ago=2),
        _mail(9, frm=OWN, to="old@vecchio.it", thread="t8", days_ago=700),
        _mail(10, frm=OWN, to="noreply@servizio.it", thread="t9", days_ago=8),
        _mail(12, frm=OWN, to="bozza@mai-inviata.it", thread="t11", days_ago=1, draft=True),
    ]
    for mail in mails:
        fake.messages[mail.id] = mail
    return fake


def test_the_proposals_group_the_sent_conversations_by_domain(db_session: Session) -> None:
    account = connected_account(db_session)
    fake = _mailbox()

    proposals = sync_service(db_session, fake).suggest_customers(actor=actor_for(account))

    assert [proposal.dominio for proposal in proposals] == ["acme.it", "studio-rossi.it"]
    acme, rossi = proposals
    assert acme.nome == "Acme"
    assert acme.conversazioni == 2
    # The last message of any conversation it took part in: t2, three days ago.
    assert acme.ultimo_messaggio == datetime.fromtimestamp(
        fake.messages["m3"].internal_date_ms / 1000, tz=UTC
    )
    people = {person.indirizzo: person.nome for person in acme.persone}
    # Sara never received a mail from the owner, but she answered in a thread the owner
    # started: she is somebody the owner corresponds with.
    assert people == {"marco@acme.it": "Marco Bianchi", "sara@acme.it": "Sara Verdi"}
    assert rossi.nome == "Studio Rossi"
    assert rossi.conversazioni == 1
    assert [person.indirizzo for person in rossi.persone] == ["anna@studio-rossi.it"]


def test_what_names_nobody_to_import_is_left_out(db_session: Session) -> None:
    """The owner's own domain, a webmail, a PEC, a system address, what only ever wrote
    in, what is older than the period, and a draft nobody was sent."""
    account = connected_account(db_session)

    proposals = sync_service(db_session, _mailbox()).suggest_customers(actor=actor_for(account))

    domains = {proposal.dominio for proposal in proposals}
    for absent in (
        "example.it",
        "gmail.com",
        "pec.acme.it",
        "servizio.it",
        "newsletter.com",
        "vecchio.it",
        "mai-inviata.it",
    ):
        assert absent not in domains, absent


def test_google_is_asked_for_the_sent_mail_of_the_period_and_headers_only(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    fake = _mailbox()

    sync_service(db_session, fake).suggest_customers(actor=actor_for(account), mesi=6)

    listings = [request for request in fake.requests if request.is_messages_list]
    assert len(listings) == 1
    assert listings[0].q is not None
    clause, drafts, horizon = listings[0].q.split(" ")
    assert clause == f"from:{MAILBOX}"
    assert drafts == "-in:draft"
    after = int(horizon.removeprefix("after:"))
    six_months_ago = (datetime.now(UTC) - timedelta(days=182)).timestamp()
    assert abs(after - six_months_ago) < 5
    reads = [request for request in fake.requests if "/threads/" in request.path]
    assert reads, "no conversation was read, so this test proves nothing"
    for request in reads:
        assert request.query["format"] == ["metadata"]
        assert request.query["metadataHeaders"] == ["From", "To", "Cc"]
        # Not even the snippet: only the id, the date and those headers come back.
        assert request.query["fields"] == ["messages(id,internalDate,payload/headers)"]
    assert not any(request.is_messages_send for request in fake.requests)


def test_a_proposal_stores_nothing_and_moves_no_watermark(db_session: Session) -> None:
    account = connected_account(db_session)

    sync_service(db_session, _mailbox()).suggest_customers(actor=actor_for(account))

    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(Customer)).scalar_one() == 0
    db_session.refresh(account)
    assert account.sync_watermark is None


def test_a_domain_already_filed_under_a_customer_is_not_proposed_again(
    db_session: Session,
) -> None:
    """By the customer's website, and by a person of a customer whose record carries no
    domain of its own. A person already in the address book is marked, not dropped."""
    account = connected_account(db_session)
    db_session.add(Customer(ragione_sociale="Acme S.r.l.", sito_web="https://www.acme.it"))
    rossi = Customer(ragione_sociale="Studio Rossi")
    db_session.add(rossi)
    db_session.flush()
    db_session.add(Person(nome="Anna", email="anna@studio-rossi.it", customer_id=rossi.id))
    db_session.flush()
    fake = _mailbox()
    fake.messages["m11"] = _mail(11, frm=OWN, to="luca@nuovo.it", thread="t10")
    db_session.add(Person(nome="Luca", email="Luca@Nuovo.it"))
    db_session.flush()

    proposals = sync_service(db_session, fake).suggest_customers(actor=actor_for(account))

    assert [proposal.dominio for proposal in proposals] == ["nuovo.it"]
    assert [(p.indirizzo, p.gia_in_anagrafica) for p in proposals[0].persone] == [
        ("luca@nuovo.it", True)
    ]


def test_a_long_year_is_read_as_its_most_recent_conversations(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most `SUGGEST_MAX_THREADS` conversations, and the listing stops paging there."""
    monkeypatch.setattr(sync_module, "SUGGEST_MAX_THREADS", 2)
    account = connected_account(db_session)
    fake = _mailbox()

    sync_service(db_session, fake).suggest_customers(actor=actor_for(account))

    reads = [request for request in fake.requests if "/threads/" in request.path]
    assert len(reads) == 2


def test_the_period_is_bounded(db_session: Session) -> None:
    account = connected_account(db_session)
    service = sync_service(db_session, _mailbox())
    for mesi in (0, 25):
        with pytest.raises(ValidationFailed):
            service.suggest_customers(actor=actor_for(account), mesi=mesi)


def test_a_readonly_person_and_a_plain_agent_are_refused(db_session: Session) -> None:
    account = connected_account(db_session)
    service = sync_service(db_session, _mailbox())
    with pytest.raises(PermissionDenied):
        service.suggest_customers(actor=Actor(id=account.user_id, type="user", role="readonly"))
    with pytest.raises(AgentForbidden):
        service.suggest_customers(actor=Actor(id=account.user_id, type="mcp", role="admin"))


def test_nobody_connected_is_a_conflict(db_session: Session) -> None:
    account = connected_account(db_session, status="disconnected")
    with pytest.raises(Conflict):
        sync_service(db_session, _mailbox()).suggest_customers(actor=actor_for(account))


def test_a_conversation_gone_before_its_read_is_skipped(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted between the listing and the read: Gmail answers 404 for it, and the
    proposals are built from the others."""
    account = connected_account(db_session)
    fake = _mailbox()
    service = sync_service(db_session, fake)
    real = service._thread_headers

    def vanished(thread_id: str, token: str) -> dict[str, object]:
        if thread_id == "t3":
            raise GoogleCallFailed(UpstreamFailure(404, "notFound", None), "lettura")
        return real(thread_id, token)

    monkeypatch.setattr(service, "_thread_headers", vanished)
    proposals = service.suggest_customers(actor=actor_for(account))
    assert [proposal.dominio for proposal in proposals] == ["acme.it"]


def test_gmail_failing_is_a_sentence_to_retry_not_a_server_error(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    fake = _mailbox()
    service = sync_service(db_session, fake)
    # A token in hand, and every attempt at the listing answered 503 until the
    # transport's retries are spent.
    service._access_token = lambda _account: "ya29.token"  # type: ignore[method-assign]
    fake.fail_with = [(503, b"{}", {})] * 10
    with pytest.raises(GmailUnavailable):
        service.suggest_customers(actor=actor_for(account))


def test_a_read_that_finished_in_time_counts_even_behind_a_slow_one(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing order is not arrival order: a slow first conversation past the budget
    does not drop the later ones that already answered."""
    monkeypatch.setattr(sync_module, "SUGGEST_READ_BUDGET_SECONDS", 0.5)
    account = connected_account(db_session)
    service = sync_service(db_session, _mailbox())
    real = service._thread_headers

    def first_is_slow(thread_id: str, token: str) -> dict[str, object]:
        if thread_id == "t1":
            time.sleep(2.0)
        return real(thread_id, token)

    monkeypatch.setattr(service, "_thread_headers", first_is_slow)
    proposals = service.suggest_customers(actor=actor_for(account))

    # t1 (Acme, with Sara) missed the budget; t2 (Acme) and t3 (Studio Rossi) did not.
    assert {proposal.dominio for proposal in proposals} == {"acme.it", "studio-rossi.it"}
    acme = next(proposal for proposal in proposals if proposal.dominio == "acme.it")
    assert [person.indirizzo for person in acme.persone] == ["marco@acme.it"]


def test_a_slow_gmail_gives_a_shorter_list_not_a_timeout(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the budget, the reads still queued are dropped and what arrived is used:
    here every read takes longer than the whole budget, so nothing arrived."""
    monkeypatch.setattr(sync_module, "SUGGEST_READ_BUDGET_SECONDS", 0.05)
    account = connected_account(db_session)
    service = sync_service(db_session, _mailbox())
    real = service._thread_headers

    def slow(thread_id: str, token: str) -> dict[str, object]:
        time.sleep(0.3)
        return real(thread_id, token)

    monkeypatch.setattr(service, "_thread_headers", slow)
    started = time.monotonic()
    proposals = service.suggest_customers(actor=actor_for(account))

    assert proposals == []
    # It answered at the budget, without waiting for the reads it dropped.
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize(
    ("domain", "name"),
    [
        ("acme.it", "Acme"),
        ("studio-rossi.it", "Studio Rossi"),
        ("mail.acme.it", "Acme"),
        ("acme.co.uk", "Acme"),
        ("design_lab.com", "Design Lab"),
    ],
)
def test_a_company_name_is_guessed_from_the_domain(domain: str, name: str) -> None:
    assert company_name_from_domain(domain) == name


def test_the_query_builders_refuse_what_they_should() -> None:
    with pytest.raises(ValidationFailed):
        sent_since_query("io@example.it", after_epoch=0)
    with pytest.raises(ValidationFailed):
        sent_since_query("io@example.it OR from:x@y.it", after_epoch=1)
    url = thread_metadata_url("abc123")
    assert parse_qs(urlparse(url).query) == {
        "format": ["metadata"],
        "metadataHeaders": ["From", "To", "Cc"],
        "fields": ["messages(id,internalDate,payload/headers)"],
    }


# --- the import ------------------------------------------------------------------------


ADMIN = Actor(id=None, type="user", role="admin")


def _import(**overrides: object) -> CustomersFromSuggestions:
    clienti: object = overrides.get(
        "clienti",
        [
            {
                "dominio": "acme.it",
                "ragione_sociale": "Acme S.r.l.",
                "persone": [
                    {"indirizzo": "marco@acme.it", "nome": "Marco Bianchi"},
                    {"indirizzo": "sara@acme.it", "nome": ""},
                ],
            },
            {"dominio": "studio-rossi.it", "ragione_sociale": "  Studio Rossi  ", "persone": []},
        ],
    )
    return CustomersFromSuggestions.model_validate({"clienti": clienti})


def test_ticked_proposals_become_customers_and_people_in_one_go(db_session: Session) -> None:
    created = CustomerService(db_session).create_from_suggestions(_import(), ADMIN)

    assert [customer.ragione_sociale for customer in created] == ["Acme S.r.l.", "Studio Rossi"]
    assert [customer.sito_web for customer in created] == ["acme.it", "studio-rossi.it"]
    people = db_session.execute(select(Person).order_by(Person.email)).scalars().all()
    assert [(p.email, p.nome, p.cognome, p.customer_id) for p in people] == [
        ("marco@acme.it", "Marco", "Bianchi", created[0].id),
        # No display name: the address stands in for it.
        ("sara@acme.it", "Sara", None, created[0].id),
    ]
    # One «created» activity per customer, as «Nuovo cliente» records, and per person.
    kinds = db_session.execute(
        select(Activity.entity_type, Activity.kind).order_by(Activity.entity_type)
    ).all()
    assert kinds.count(("customer", "created")) == 2
    assert kinds.count(("person", "created")) == 2


def test_a_domain_that_is_already_a_customer_refuses_the_whole_import(
    db_session: Session,
) -> None:
    db_session.add(Customer(ragione_sociale="Rossi", sito_web="studio-rossi.it"))
    db_session.flush()
    before = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()

    with pytest.raises(Conflict) as caught:
        CustomerService(db_session).create_from_suggestions(_import(), ADMIN)
    assert "studio-rossi.it" in caught.value.message
    db_session.rollback()
    # Nothing of Acme either: all of them or none.
    assert db_session.execute(select(func.count()).select_from(Customer)).scalar_one() <= before
    assert (
        db_session.execute(select(Customer).where(Customer.sito_web == "acme.it")).first() is None
    )


def test_the_same_domain_twice_is_one_customer_not_two(db_session: Session) -> None:
    twice = [
        {"dominio": "acme.it", "ragione_sociale": "Acme", "persone": []},
        {"dominio": "acme.it", "ragione_sociale": "Acme di nuovo", "persone": []},
    ]
    with pytest.raises(Conflict):
        CustomerService(db_session).create_from_suggestions(_import(clienti=twice), ADMIN)


def test_a_person_outside_the_domain_or_a_webmail_domain_is_refused(db_session: Session) -> None:
    stranger = [
        {
            "dominio": "acme.it",
            "ragione_sociale": "Acme",
            "persone": [{"indirizzo": "ceo@rival.it", "nome": "Ceo"}],
        }
    ]
    with pytest.raises(ValidationFailed):
        CustomerService(db_session).create_from_suggestions(_import(clienti=stranger), ADMIN)
    webmail = [{"dominio": "gmail.com", "ragione_sociale": "Gmail", "persone": []}]
    with pytest.raises(ValidationFailed):
        CustomerService(db_session).create_from_suggestions(_import(clienti=webmail), ADMIN)


def test_a_person_already_in_the_address_book_is_left_where_it_is(db_session: Session) -> None:
    db_session.add(Person(nome="Marco", email="marco@acme.it"))
    db_session.flush()

    CustomerService(db_session).create_from_suggestions(_import(), ADMIN)

    marcos = db_session.execute(select(Person).where(Person.email == "marco@acme.it")).all()
    assert len(marcos) == 1


def test_a_refusal_half_way_through_leaves_nothing_behind(db_session: Session) -> None:
    """A customer custom field the space made required refuses the first insert, after
    the batch passed its own checks: nothing of the import stays."""
    from pigrocrm.core.fields.models import FieldDefinition

    db_session.add(
        FieldDefinition(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        )
    )
    db_session.flush()

    with pytest.raises(ValidationFailed):
        CustomerService(db_session).create_from_suggestions(_import(), ADMIN)

    assert db_session.execute(select(func.count()).select_from(Customer)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(Person)).scalar_one() == 0


def test_a_display_name_longer_than_a_person_holds_is_clipped(db_session: Session) -> None:
    long = [
        {
            "dominio": "acme.it",
            "ragione_sociale": "Acme",
            "persone": [{"indirizzo": "marco@acme.it", "nome": "M" * 130 + " " + "B" * 124}],
        }
    ]
    CustomerService(db_session).create_from_suggestions(_import(clienti=long), ADMIN)
    person = db_session.execute(select(Person)).scalar_one()
    assert len(person.nome) == 120
    assert person.cognome is not None and len(person.cognome) == 120


def test_an_import_holds_the_lock_a_concurrent_one_waits_on(
    db_session: Session, db_engine: Engine
) -> None:
    """Two imports of the same proposal at once (a double click, two tabs) must not
    both see the domain free: each takes the same transaction lock first, so the
    second waits for the first to commit and then refuses the domain."""
    CustomerRepository(db_session).lock_imports()
    with db_engine.connect() as other:
        free = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, 1)"), {"ns": CUSTOMERS_LOCK_NAMESPACE}
        ).scalar_one()
    assert free is False


def test_the_import_takes_the_lock_before_it_reads(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_lock = CustomerRepository.lock_imports
    real_domains = AddressRoster.customer_domains

    def lock(self: CustomerRepository) -> None:
        calls.append("lock")
        real_lock(self)

    def domains(self: AddressRoster) -> frozenset[str]:
        calls.append("read")
        return real_domains(self)

    monkeypatch.setattr(CustomerRepository, "lock_imports", lock)
    monkeypatch.setattr(AddressRoster, "customer_domains", domains)
    CustomerService(db_session).create_from_suggestions(_import(), ADMIN)
    assert calls[:2] == ["lock", "read"]


def test_a_readonly_person_imports_nothing(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        CustomerService(db_session).create_from_suggestions(
            _import(), Actor(id=None, type="user", role="readonly")
        )
