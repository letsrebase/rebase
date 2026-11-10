"""What the model's settings page reads and writes. The key is written, never read: a read says
only whether one is set."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

AiProvider = Literal["anthropic", "openai"]
PROVIDER_LABELS: dict[str, str] = {"anthropic": "Anthropic", "openai": "OpenAI"}


class AiModelUpdate(BaseModel):
    """Only what is sent changes. `enabled=True` needs the settings complete and, when the model
    is being switched on, `privacy_acknowledged=True` in the same request: the person who turns
    it on has read what it receives (`AiModelRead.privacy_notice`). Changing the provider or the
    model switches it off and clears the acknowledgement, since the notice names them; the same
    request may switch it on again with the new acknowledgement."""

    model_config = ConfigDict(extra="forbid")

    provider: AiProvider | None = None
    model: SafeStr | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(default=None, min_length=8, max_length=500, repr=False)
    clear_api_key: bool = False
    daily_cap_eur: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    input_eur_per_mtok: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)
    output_eur_per_mtok: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=4)
    enabled: bool | None = None
    privacy_acknowledged: bool = False
    read_payslips: bool | None = Field(
        default=None,
        description=(
            "Whether the model also reads the figures of a payslip (default off). Switching it "
            "on needs `privacy_acknowledged` in the same request: the notice names what a "
            "payslip sends"
        ),
    )


class AiModelRead(BaseModel):
    enabled: bool
    provider: AiProvider | None
    model: str | None
    has_key: bool
    daily_cap_eur: Decimal | None
    input_eur_per_mtok: Decimal | None
    output_eur_per_mtok: Decimal | None
    missing: list[str] = Field(
        description="What the model still needs before it can be switched on"
    )
    spent_today_eur: Decimal
    privacy_acknowledged_at: datetime | None
    privacy_acknowledged_by_id: UUID | None
    privacy_notice: str = Field(description="What the model receives, in the words a person reads")
    read_payslips: bool = Field(description="Whether the model also reads payslips")
    payslip_notice: str = Field(
        description="What reading a payslip sends, in the words a person reads"
    )


class AiModelTestResult(BaseModel):
    ok: bool
    input_tokens: int
    output_tokens: int
    cost_eur: Decimal
