"""The one way a test checks a setup call: the status is asserted where the call is made,
so a setup that failed is a failure there and not a puzzling one three lines later."""

from httpx import Response


def ok(response: Response, status: int = 200) -> Response:
    """`response` itself, after asserting it answered `status` (200 unless said), with the
    body in the failure message."""
    assert response.status_code == status, response.text
    return response
