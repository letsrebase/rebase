"""What the anonymous card's tests share (REB-510): a CV with text in it and Claude's
canned answers, for `test_cards.py` here and the API's tests over the same writer.

The PDF every other test module uploads is a header on nothing, which `extract_text`
reads as a scan: fine for a download, useless for a card, whose writer makes no call on
an empty text. `text_pdf` writes one page with a line of Helvetica on it, the smallest
file `pypdf` reads text out of, with the cross-reference offsets computed rather than
typed so a different line stays a valid file.
"""

import json
from typing import Any

from rebase_core.llm import LlmResponse

MODEL = "claude-opus-5"

CARD: dict[str, Any] = {
    "ruolo": "Backend developer",
    "seniority": "senior",
    "anni": 9,
    "competenze": ["Python", "FastAPI", "PostgreSQL", "AWS"],
    "settori": ["fintech", "e-commerce"],
    "lingue": ["italiano", "inglese"],
    "luogo": "Torino",
    "sintesi": (
        "Backend developer senior, nove anni fra fintech ed e-commerce, "
        "API in Python e infrastruttura AWS."
    ),
}


def text_pdf(line: str) -> bytes:
    escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def card_response(
    card: dict[str, Any] | None = None,
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 1200,
    output_tokens: int = 180,
) -> LlmResponse:
    """Claude's answer as `AnthropicCall` hands it over: `card` as the JSON body, or
    `text` verbatim for a body that is not one."""
    body = text if text is not None else json.dumps(card if card is not None else CARD)
    return LlmResponse(
        text=None if stop_reason == "refusal" else body,
        stop_reason=stop_reason,
        refusal_category="cyber" if stop_reason == "refusal" else None,
        model=MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
    )
