"""What the card evaluation's tests share (HTEST-10): the CVs, the model names, a person in the
sample and the run over a fake writer and judge. `test_card_eval.py`, `test_card_eval_baseline.py`
and `test_card_eval_spend.py` read from here.
"""

import base64
import json
import os
from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_cards import (
    CARD,
    text_pdf,
)
from sqlalchemy.orm import Session

from rebase_core.card_eval import (
    Target,
    run_eval,
    sample,
)
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmResponse
from rebase_core.models import (
    CardEvalProfile,
    CardEvalRun,
)
from rebase_core.team_schemas import Card

# A CV that dates its one role: nine years to this autumn at 2017 - 2025 (the card's 9).
CV = text_pdf("Backend developer, Acme, 2017 - 2025. Python, FastAPI, AWS, a Torino.")


# One that dates nothing, as the card of nine years would then be a guess.
UNDATED_CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI.")


MODEL = "claude-opus-5"


OTHER_MODEL = "claude-opus-5-5"


API_KEY = "sk-ant-test-not-a-real-key"


SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()


def _person(
    session: Session,
    email: str = "ada@studio.it",
    cognome: str = "Lovelace",
    cv: bytes = CV,
    label: str | None = None,
) -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome=cognome,
            email=email,
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        cv,
        "cv.pdf",
        "",
    )
    if label is not None:
        session.add(CardEvalProfile(freelancer_id=read.id, label=label))
        session.commit()
    return read.id


def _card(**changes: Any) -> dict[str, Any]:
    return {**CARD, **changes}


def _bare_card_response(card: dict[str, Any] | None = None) -> LlmResponse:
    """An answer under prompt version 1: the card alone, no `fonti`, no employers."""
    body = json.dumps(card or CARD)
    return LlmResponse(
        text=body,
        parsed=Card.model_validate_json(body),
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=900,
        output_tokens=150,
        cache_read_tokens=0,
    )


def _run(
    session: Session,
    writer: Any,
    judging: Any,
    targets: list[Target] | None = None,
    *,
    provider: str = "anthropic",
    model: str = MODEL,
    prompt_version: int = PROMPT_VERSION,
    baseline_run_id: UUID | None = None,
    judge_by: tuple[str, str] = ("anthropic", MODEL),
    effort: str | None = "medium",  # the card task's, as migration 0036 seeds it
) -> CardEvalRun:
    return run_eval(
        session,
        lambda _person: writer,
        lambda _person: judging,
        task="card",
        provider=provider,
        model=model,
        effort=effort,
        targets=targets if targets is not None else sample(session),
        prompt_version=prompt_version,
        judge_by=judge_by,
        baseline_run_id=baseline_run_id,
    )
