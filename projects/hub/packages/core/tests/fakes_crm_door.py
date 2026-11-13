"""The CRM's reads of a freelancer behind the `HttpCall` seam (REB-1107): what
`/api/rebase/freelancers/{hub id}/...` answers, from what a test put in it, and every request that
reached it. Shared by the member contracts, anonymous CV, merge and door tests, the way
`fakes_pigro` plays the engagements door, so the real `FreelancerDoor` (its paths, its query, its
parsing, its retry) is what the readers under test go through.

An outage is a queue of answers consumed before the data is read: an `Exception` is raised the
way `urllib` raises it, a `(status, body)` is returned.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from fakes_pigro import PIGRO, TOKEN

from rebase_core.pigro_door import FreelancerDoor

Failure = tuple[int, bytes] | Exception
PREFIX = "/api/rebase/freelancers/"
NO_MERGE_CONFLICT: dict[str, Any] = {
    "framework_regimes": [],
    "shared_clients": 0,
    "tax_code_conflict": False,
}


def crm_document(**overrides: Any) -> dict[str, Any]:
    """One document as the CRM's door answers it: a framework agreement out for signature, the
    letter's fields blank, unless `overrides` say otherwise."""
    values: dict[str, Any] = {
        "id": "0192e0a0-0000-7000-8000-0000000000d1",
        "kind": "framework_agreement",
        "letter_type": "ordinary",
        "number": None,
        "state": "sent",
        "customer": None,
        "start": None,
        "end": None,
        "sent_at": "2026-10-01T09:00:00Z",
        "signed_at": None,
        "signing_url": "https://firma.example.test/sign/abc",
        "has_signed_copy": False,
        "filename": None,
        "active": False,
        "renewal": None,
        "last_notice_day": None,
        "pigro_url": None,
    }
    values.update(overrides)
    return values


@dataclass
class FakeCrmDoor:
    """The door's answers by hub freelancer id, and what reached it: `calls` are `(method,
    path-and-query)`; `bearers` the `Authorization` header of each."""

    documents: dict[UUID, list[dict[str, Any]]] = field(default_factory=dict)
    pdfs: dict[UUID, bytes] = field(default_factory=dict)
    fiscal: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    merge: dict[tuple[UUID, UUID], dict[str, Any]] = field(default_factory=dict)
    committed: dict[UUID, list[dict[str, Any]]] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    bearers: list[str] = field(default_factory=list)

    def door(self, *, token: str = TOKEN, api_url: str = PIGRO) -> FreelancerDoor:
        return FreelancerDoor(api_url, token, self, self)

    def down(self, *failures: Failure) -> None:
        """The next calls fail this way, one failure each, then the data is read again."""
        self.failures.extend(failures)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        parts = urlsplit(url)
        self.calls.append((method, f"{parts.path}?{parts.query}" if parts.query else parts.path))
        self.bearers.append(headers.get("Authorization", ""))
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        assert method == "GET", "the door's freelancer reads are all GETs"
        assert parts.path.startswith(PREFIX), parts.path
        freelancer_text, _, tail = parts.path.removeprefix(PREFIX).partition("/")
        freelancer = UUID(freelancer_text)
        query = parse_qs(parts.query)
        if tail == "contracts":
            return self._json({"documents": self.documents.get(freelancer, [])})
        if tail.startswith("contracts/") and tail.endswith("/pdf"):
            document = UUID(tail.split("/")[1])
            mine = {UUID(d["id"]) for d in self.documents.get(freelancer, [])}
            if document in mine and document in self.pdfs:
                return 200, self.pdfs[document]
            return 404, b'{"detail": "Not Found"}'
        if tail == "fiscal-identifiers":
            return self._json(
                self.fiscal.get(
                    freelancer, {"tax_code": None, "vat_number": None, "certified_email": None}
                )
            )
        if tail == "merge-check":
            other = UUID(query["with"][0])
            return self._json(self.merge.get((freelancer, other), NO_MERGE_CONFLICT))
        if tail == "availability":
            return self._json(self._availability(freelancer, query))
        raise AssertionError(f"the door has no read {tail!r}")

    @staticmethod
    def _json(body: dict[str, Any]) -> tuple[int, bytes]:
        return 200, json.dumps(body).encode()

    def _availability(self, freelancer: UUID, query: dict[str, list[str]]) -> dict[str, Any]:
        start = date.fromisoformat(query["from"][0]) if "from" in query else date(2026, 11, 2)
        weeks = self.committed.get(freelancer)
        if weeks is None:
            weeks = [
                {
                    "week": f"{start.isocalendar().year}-W{start.isocalendar().week:02d}",
                    "starts_on": start.isoformat(),
                    "ends_on": (start + timedelta(days=6)).isoformat(),
                    "status": "free",
                    "label": "libera",
                    "committed_days": "0",
                    "free_days": "5",
                    "engagement_ids": [],
                }
            ]
        return {
            "freelancer_id": None if freelancer not in self.committed else str(freelancer),
            "hub_freelancer_id": str(freelancer),
            "date_from": start.isoformat(),
            "date_to": (start + timedelta(days=6)).isoformat(),
            "weeks": weeks,
        }
