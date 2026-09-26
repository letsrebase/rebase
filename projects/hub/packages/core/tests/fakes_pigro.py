"""The CRM's door for rebase's engagements behind the `HttpCall` seam (REB-498): a list
of answers and every request that reached it, the way `fakes_documenso` plays
Documenso. Shared by the engagement, signing and API tests, so a link is recorded the
same way wherever a test asks for one."""

import json
from dataclasses import dataclass, field
from uuid import UUID

TOKEN = "un-token-per-la-porta"
PIGRO = "https://pigro.test"
DEAL = UUID("0192e0a0-0000-7000-8000-00000000d3a1")
CUSTOMER = UUID("0192e0a0-0000-7000-8000-00000000c057")
DEAL_URL = f"{PIGRO}/ada-lovelace/app/deal/{DEAL}"

Answer = tuple[int, bytes] | Exception


@dataclass
class RecordedPigro:
    """The CRM's door as a list of answers, one per call (the last one repeats), and
    every request that reached it."""

    answers: list[Answer]
    calls: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def linked_body(*, spazio_creato: bool = True, creato: bool = True) -> bytes:
    """The door's answer to a `PUT` that linked the match: Ada's space and its deal."""
    return json.dumps(
        {
            "slug": "ada-lovelace",
            "url": f"{PIGRO}/ada-lovelace/app/",
            "customer_id": str(CUSTOMER),
            "deal_id": str(DEAL),
            "deal_url": DEAL_URL,
            "spazio_creato": spazio_creato,
            "creato": creato,
        }
    ).encode()
