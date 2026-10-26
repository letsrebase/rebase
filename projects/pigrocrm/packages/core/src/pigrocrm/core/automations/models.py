"""The whole configuration surface of the automations: five booleans and one number.

A single row, like `emitter_profile` (slice 2) and `fiscal_profile` (slice 3), and read
through `AutomationConfigRepository.get_or_create()` which creates it on first use. No
`singleton` CHECK constraint: the repository is the only writer and it never inserts a
second row, and a constraint pinning a magic primary key would be a second mechanism for
the same invariant.

No flow builder, no configurable conditions, no second effect per rule (spec §15). A flow
builder brings a condition evaluator, an execution order, a failure semantics and a way to
stop a loop: four mechanisms for a product that has five rules.
"""

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class AutomationConfig(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "automation_config"
    __table_args__ = (
        CheckConstraint(
            "a4_deal_fermo_giorni BETWEEN 1 AND 365", name="ck_automation_config_deal_fermo_giorni"
        ),
    )

    # A1 -- offerta accettata -> deal vinto. Default on: an automation nobody switched on
    # is an automation nobody knows exists.
    a1_offerta_accettata_vince_deal: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    # A2 -- offerta inviata -> il deal avanza allo stage `code='offerta'`, only if its
    # current `posizione` is lower. Never backwards: see `AutomationRunner`.
    a2_offerta_inviata_avanza_deal: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )

    # A3, A4, A5 -- the three rules of slice 7 §6 that open an attivita, run by the daily
    # pass (`daily.py`) and named after the `regola` they write. Default on, like A1 and A2.
    a3_offerta_inviata_richiama: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
    a4_deal_fermo: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # The one threshold: how many days a deal may stay in its stage before A4 fires. 30,
    # because the reminder `deal_stale` (nothing at all on a deal for 21 days) speaks
    # first and this is the stronger statement: the deal has not even changed stage.
    a4_deal_fermo_giorni: Mapped[int] = mapped_column(
        nullable=False, default=30, server_default="30"
    )
    a5_fattura_scaduta: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default="true"
    )
