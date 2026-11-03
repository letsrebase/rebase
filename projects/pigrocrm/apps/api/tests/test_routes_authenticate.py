"""Default deny: a route answers 401 to a caller with no credential unless it is on the
short list below of routes that are open on purpose.

The routers authenticate with a router-level `dependencies=[Depends(get_actor)]`, so a
handler that never names the actor is still behind it. This walks the OpenAPI document
and asks every route once with no cookie and no token, so a route added to a router that
forgot the dependency (or to a new router) fails here whatever its method.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

# Routes with their own door, each decided where it is declared: the session mechanics, the
# space signup and the identity cookie, the service-token engagements, the OAuth callbacks
# (a browser navigation that answers with a page, never a 401), the probes and the docs.
OPEN_PREFIXES = (
    "/api/auth/",
    "/api/identity/",
    "/api/tenants",
    "/api/rebase/",
    "/health",
    "/openapi.json",
    "/docs",
    "/redoc",
)
OPEN_SUFFIXES = ("/oauth/callback",)


def _routes(client: TestClient) -> list[tuple[str, str]]:
    paths = client.get("/openapi.json").json()["paths"]
    return [
        (method.upper(), path)
        for path, item in sorted(paths.items())
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
        and not path.startswith(OPEN_PREFIXES)
        and not path.endswith(OPEN_SUFFIXES)
    ]


def test_every_route_refuses_a_caller_with_no_credential(client: TestClient) -> None:
    routes = _routes(client)
    assert len(routes) > 150  # the walk found the API, not an empty document
    anonymous = TestClient(client.app, base_url="https://testserver")
    offenders = []
    for method, path in routes:
        url = path
        for name in {segment for segment in path.split("/") if segment.startswith("{")}:
            url = url.replace(name, str(uuid4()))
        response = anonymous.request(method, url)
        if response.status_code != 401:
            offenders.append((method, url, response.status_code))
    assert offenders == []
