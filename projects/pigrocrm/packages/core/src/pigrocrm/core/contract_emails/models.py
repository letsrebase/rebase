"""`contract_email_templates`: the saved text a contract's invoice and register go out with
(REB-753).

Per contract, as mastro keeps them, and not a row of `templates`: that table is
space-level, its name is unique across the space and writing it is an unscoped admin's,
none of which fits «the wording this client gets with October's invoice». The body is
rendered by the same engine (`templates/renderer.py`), in the plain context a mail client
reads, from the variables `service.TEMPLATE_VARIABLES` names.

No delete: a template nobody wants any more is archived (`attivo` false), as every other
configuration row here is. `allegati` says which files go with it: the invoice's PDF and
the register's PDF, both archived documents, since a mail attaches document versions and
never a file made on the spot.
"""

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

ATTACHMENT_KINDS: tuple[str, ...] = ("fattura_pdf", "registro_pdf")


class ContractEmailTemplate(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "contract_email_templates"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    oggetto: Mapped[str] = mapped_column(String(300), nullable=False)
    corpo: Mapped[str] = mapped_column(Text, nullable=False)
    allegati: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    attivo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        # On `lower(nome)` for the reason `templates` gives: a plain unique on a text
        # column is case-sensitive and a Pydantic validator protects one path only.
        Index("uq_contract_email_templates_nome", "contract_id", func.lower(nome), unique=True),
    )
