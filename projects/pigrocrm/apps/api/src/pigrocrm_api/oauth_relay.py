"""The root's OAuth callbacks, when the root lends its Google client (REB-394).

A space borrowing the root's client sends Google the root's callback, with its slug in
the `state` (`pigrocrm.core.tenants.google`). The consent therefore comes back to
`/api/gmail/oauth/callback` or `/api/drive/oauth/callback` at the root, where the
browser carries no session of that space: its cookies live under `/<slug>/`. So the
root does not complete anything. It sends the browser, with the same three parameters,
to the same path under the space's prefix, where the space's own callback reads its own
cookie and redeems the state against its own database, exactly as it does for a consent
that never left it.

Which is why this runs before the route asks who the caller is: somebody signed in to a
space only is a stranger to the root, and would get a 401 here instead of their consent.
The slug decides only where the browser goes next. It is checked against the slug rules
before it becomes a path segment, and the path is always the root-relative
`/<slug>/api/<product>/oauth/callback`, so no state can turn this into a redirect off
the site.
"""

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse

from pigrocrm.core.config import Settings
from pigrocrm.core.tenants.google import relay_slug
from pigrocrm_api.tenancy import tenant_slug


def relay_to_space(
    request: Request,
    settings: Settings,
    callback_path: str,
    *,
    code: str | None,
    state: str | None,
    error: str | None,
) -> RedirectResponse | None:
    """A 307 to the space the `state` names, or None when this consent is not the
    root's to relay: a request already under a space, an installation that lends no
    client, or a state that names no space (the root's own consent is a bare jti)."""
    if tenant_slug(request) is not None or not settings.google_shared_client:
        return None
    slug = relay_slug(state)
    # The root's own name is the root, not a space: relaying to it would land on this
    # same callback, again and again, until the browser gave up.
    if slug is None or slug == settings.root_slug:
        return None
    query = urlencode(
        [
            (name, value)
            for name, value in (("code", code), ("state", state), ("error", error))
            if value is not None
        ]
    )
    return RedirectResponse(f"/{slug}{callback_path}?{query}", status_code=307)
