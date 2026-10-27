"""REB-800 over HTTP: the wizard attaches a second address with a known LinkedIn profile to
the person already there, an admin lists the pairs and merges one card into another, and
a refusal is a 409 with a sentence."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, Freelancer, User

ADMIN_EMAIL = "ivan@rebase.it"
ADA = "https://www.linkedin.com/in/ada-lovelace"
TABLES = (
    "admin_actions",
    "comments",
    "contract_documents",
    "freelancer_cards",
    "freelancers",
    "logins",
    "sessions",
    "magic_link_tokens",
    "users",
    "signups",
)


@pytest.fixture
def admin(api_session: Session) -> Iterator[User]:
    row = User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin")
    api_session.add(row)
    api_session.commit()
    yield row
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient, email: str, linkedin: str | None = None) -> None:
    data = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": email,
        "tariffa_giornaliera": "450",
        "posizione": "Backend developer",
        "remoto": "remoto",
    }
    if linkedin:
        data["linkedin_url"] = linkedin
    assert client.post("/api/hub/freelancers", data=data).status_code == 201


def _card_ids(session: Session) -> dict[str, str]:
    rows = session.execute(
        select(User.email, Freelancer.id).join(Freelancer, Freelancer.user_id == User.id)
    ).all()
    return {email: str(card_id) for email, card_id in rows}


def test_the_wizard_with_a_second_address_and_a_known_profile_mails_the_address_on_file(
    client: TestClient, api_session: Session, sender: RecordingSender, admin: User
) -> None:
    _apply(client, "vecchia@studio.it", "https://www.linkedin.com/in/Ada-Lovelace/")
    sender.sent.clear()

    _apply(client, "nuova@studio.it", "linkedin.com/in/ada-lovelace")

    # Other tests of this directory leave cards behind: count only this person's.
    ours = "('vecchia@studio.it', 'nuova@studio.it')"
    assert api_session.scalar(text(f"SELECT count(*) FROM users WHERE email IN {ours}")) == 1
    assert (
        api_session.scalar(
            text(
                "SELECT count(*) FROM freelancers f JOIN users u ON u.id = f.user_id "
                f"WHERE u.email IN {ours}"
            )
        )
        == 1
    )
    (mail,) = sender.sent
    # The mail goes to the address on file, never to the one typed, and says why.
    assert mail.to == "vecchia@studio.it"
    assert "profilo LinkedIn" in mail.text


def test_the_wizard_with_the_same_address_still_says_so_by_address(
    client: TestClient, sender: RecordingSender, admin: User
) -> None:
    _apply(client, "ada@studio.it", ADA)
    sender.sent.clear()
    _apply(client, "ADA@studio.it", None)
    (mail,) = sender.sent
    assert mail.to == "ada@studio.it" and "con questo indirizzo" in mail.text


def test_an_admin_merges_a_card_into_another_and_the_list_shows_one(
    client: TestClient, api_session: Session, sender: RecordingSender, admin: User
) -> None:
    _apply(client, "uno@studio.it")
    _apply(client, "due@studio.it")
    ids = _card_ids(api_session)
    _login(client, sender)

    merged = client.post(
        f"/api/hub/freelancers/{ids['uno@studio.it']}/merge", json={"da": ids["due@studio.it"]}
    )

    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["survivor_freelancer_id"] == ids["uno@studio.it"]
    assert body["absorbed_freelancer_id"] == ids["due@studio.it"]
    listed = client.get("/api/hub/freelancers").json()["items"]
    assert [item["email"] for item in listed] == ["uno@studio.it"]
    audit = client.get(f"/api/hub/freelancers/{ids['due@studio.it']}/audit").json()
    assert (
        audit[0]["kind"] == "merged"
        and audit[0]["payload"]["into_freelancer_id"] == ids["uno@studio.it"]
    )


def test_a_conflicting_merge_is_a_409_with_the_sentence(
    client: TestClient, api_session: Session, sender: RecordingSender, admin: User
) -> None:
    _apply(client, "uno@studio.it")
    _apply(client, "due@studio.it")
    ids = _card_ids(api_session)
    for card_id in ids.values():
        api_session.add(
            ContractDocument(
                kind="quadro",
                freelancer_id=card_id,
                text_version="1",
                pdf=b"%PDF-1",
                stato="firmato",
                created_by=admin.id,
            )
        )
    api_session.commit()
    _login(client, sender)

    refused = client.post(
        f"/api/hub/freelancers/{ids['uno@studio.it']}/merge", json={"da": ids["due@studio.it"]}
    )

    assert refused.status_code == 409
    assert "contratto quadro in corso o firmato" in refused.text
    assert len(client.get("/api/hub/freelancers").json()["items"]) == 2


def test_the_pairs_are_listed_for_an_admin_only(
    client: TestClient, api_session: Session, sender: RecordingSender, admin: User
) -> None:
    assert client.get("/api/hub/talent/duplicati").status_code == 401
    _apply(client, "uno@studio.it")
    _login(client, sender)
    listed = client.get("/api/hub/talent/duplicati")
    assert listed.status_code == 200
    assert listed.json() == {"unibili": [], "da_rivedere": [], "iscrizioni": 0}


def test_a_member_is_refused_the_pairs_and_the_merge(
    client: TestClient, api_session: Session, sender: RecordingSender, admin: User
) -> None:
    _apply(client, "uno@studio.it")
    _apply(client, "due@studio.it")
    ids = _card_ids(api_session)
    _login(client, sender, "uno@studio.it")
    assert client.get("/api/hub/talent/duplicati").status_code == 403
    merge = client.post(
        f"/api/hub/freelancers/{ids['uno@studio.it']}/merge", json={"da": ids["due@studio.it"]}
    )
    assert merge.status_code == 403
