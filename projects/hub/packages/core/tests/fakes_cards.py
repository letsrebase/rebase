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

from rebase_core.card_draft import FONTI_FIELDS, CardDraft
from rebase_core.card_judge import CardJudgement
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


# What the model says supported each field when the CV is all it read.
FONTI: dict[str, list[str]] = {name: ["cv"] for name in FONTI_FIELDS}


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


# The persona a CV that states nothing about who the person is answers (REB-855): every
# field empty, the shape every provider's strict output takes.
NO_PERSONA: dict[str, Any] = {
    "nome": None,
    "cognome": None,
    "email": None,
    "telefono": None,
    "citta": None,
    "linkedin_url": None,
    "altri_link": [],
}

# A CV that states its contacts, for the tests that read the named card.
PERSONA: dict[str, Any] = {
    "nome": "Ada",
    "cognome": "Lovelace",
    "email": "Ada.Lovelace@Example.org",
    "telefono": "+39 333 123 4567",
    "citta": "Torino",
    "linkedin_url": "linkedin.com/in/ada-lovelace",
    "altri_link": ["https://ada.example.org", "github.com/ada"],
}


def card_response(
    card: dict[str, Any] | None = None,
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 1200,
    output_tokens: int = 180,
    fonti: dict[str, list[str]] | None = None,
    employers: list[str] | None = None,
    persona: dict[str, Any] | None = None,
) -> LlmResponse:
    """The model's answer as `AiCall` hands it over under prompt version 3: `card` inside
    the private draft (`fonti`, `employers` and `persona` beside it), or `text` verbatim
    for a body that is not one. `parsed` is the body validated as `CardDraft`, as `AiCall`
    does it, and `None` when it is not one. The persona is empty unless one is handed."""
    body = (
        text
        if text is not None
        else json.dumps(
            {
                "card": card if card is not None else CARD,
                "fonti": fonti if fonti is not None else FONTI,
                "employers": employers or [],
                "persona": persona if persona is not None else NO_PERSONA,
            }
        )
    )
    try:
        parsed = CardDraft.model_validate_json(body) if stop_reason == "end_turn" else None
    except ValueError:
        parsed = None
    return LlmResponse(
        text=None if stop_reason == "refusal" else body,
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category="cyber" if stop_reason == "refusal" else None,
        model=MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
    )


def draft_body(card: dict[str, Any] | None = None) -> str:
    """The body of a writing call's answer under prompt version 3, as a provider returns it."""
    return json.dumps(
        {"card": card or CARD, "fonti": FONTI, "employers": [], "persona": NO_PERSONA}
    )


def judgement_body(
    verdicts: dict[str, tuple[str, str]] | None = None,
    *,
    invented: list[str] | None = None,
    identifying: list[str] | None = None,
) -> str:
    """The body of a judging call's answer: every field `supported` unless `verdicts` says
    otherwise (`{"anni": ("contradicted", "the CV dates three years")}`)."""
    fields = {name: {"verdict": "supported", "reason": "the CV says so"} for name in FONTI_FIELDS}
    for name, (verdict, reason) in (verdicts or {}).items():
        fields[name] = {"verdict": verdict, "reason": reason}
    return json.dumps({**fields, "invented": invented or [], "identifying": identifying or []})


def judge_response(
    verdicts: dict[str, tuple[str, str]] | None = None,
    *,
    invented: list[str] | None = None,
    identifying: list[str] | None = None,
    text: str | None = None,
    stop_reason: str = "end_turn",
) -> LlmResponse:
    """The judging call's answer as `AiCall` hands it over: `parsed` is the body validated as
    `CardJudgement`, `None` when it is not one."""
    body = (
        text
        if text is not None
        else judgement_body(verdicts, invented=invented, identifying=identifying)
    )
    try:
        parsed = CardJudgement.model_validate_json(body) if stop_reason == "end_turn" else None
    except ValueError:
        parsed = None
    return LlmResponse(
        text=None if stop_reason == "refusal" else body,
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category=None,
        model=MODEL,
        input_tokens=2500,
        output_tokens=300,
        cache_read_tokens=0,
    )
