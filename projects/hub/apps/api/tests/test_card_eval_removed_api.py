"""«Valutazione» is gone (REB-842): the evaluation is a command now (`rebase cards-eval`),
so the admin API that corrected expected cards, accepted them and rated a run has no
routes left, for an admin as for anybody."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_admin
from rebase_core.admin_tokens import AdminRead
from rebase_core.models import User

BASE = "/api/hub/admin/card-eval"
SOME_ID = UUID("00000000-0000-4000-8000-000000000842")

ROUTES = [
    ("get", BASE, None),
    ("put", f"{BASE}/{SOME_ID}", {}),
    ("post", f"{BASE}/{SOME_ID}/accept", {}),
    ("post", f"{BASE}/runs/{SOME_ID}/ratings", {"ratings": {}}),
]


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[User]:
    row = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    api_session.add(row)
    api_session.commit()
    client.app.dependency_overrides[get_admin] = lambda: AdminRead(  # type: ignore[attr-defined]
        id=row.id, email=row.email, nome=row.nome, attivo=True, created_at=datetime.now(UTC)
    )
    yield row
    api_session.rollback()
    api_session.execute(text("DELETE FROM users"))
    api_session.commit()


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_the_old_routes_answer_404_to_an_admin_and_to_nobody(
    client: TestClient, admin: User, method: str, path: str, body: dict[str, object] | None
) -> None:
    call = getattr(client, method)
    kwargs = {} if body is None else {"json": body}

    assert call(path, **kwargs).status_code == 404
    client.app.dependency_overrides.pop(get_admin, None)  # type: ignore[attr-defined]
    assert call(path, **kwargs).status_code == 404


def test_the_openapi_has_no_card_eval_path(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert not [path for path in paths if "card-eval" in path]
