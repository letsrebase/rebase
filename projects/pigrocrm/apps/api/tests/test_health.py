"""The liveness probe, and what it deliberately does not know about.

`/health` answers for the deploy: is this process up and serving. Nothing else belongs
in it, and the pressure to add things is constant -- a third-party credential, a queue
depth, a disk. Each addition makes the probe red for something the operator cannot fix
from the deploy layer, and an operator who learns that red does not mean "roll back"
learns to ignore red.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.crypto import seal
from pigrocrm.core.gmail.models import GoogleAccount


def test_health_is_the_deploy_and_nothing_else(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_unchanged_by_a_broken_gmail_credential(
    client: TestClient, api_session: Session
) -> None:
    """Spec 13, criterion 5 (g). A broken third-party credential is not a broken deploy.

    The account is really revoked in the database, not merely imagined: a test that
    asserted this against an installation with no Gmail account at all would pass
    against a `/health` that did check the credential.
    """
    user = User(
        email="gmail-broken@example.it",
        nome="Utente",
        password_hash="x",
        role="admin",
        active=True,
    )
    api_session.add(user)
    api_session.flush()
    ciphertext, nonce = seal("1//0gBroken", b"k" * 32)
    api_session.add(
        GoogleAccount(
            user_id=user.id,
            google_sub=f"sub-{user.id}",
            email_address="rotta@example.it",
            refresh_token_ciphertext=ciphertext,
            refresh_token_nonce=nonce,
            scopes_granted=[],
            status="revoked",
            last_error="Il consenso Google è stato revocato",
        )
    )
    api_session.commit()

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "gmail" not in response.text.lower()
