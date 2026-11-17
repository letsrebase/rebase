"""What the CLI sign-in and intake API tests share: the freelancer who applies (and so has a
card), signed in through the magic link."""

from fastapi.testclient import TestClient

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
CARD_EMAIL = "ada@studio.it"


def apply_card(client: TestClient) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": CARD_EMAIL,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
