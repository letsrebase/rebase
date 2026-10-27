"""fiscal_profile: the ordinary regime moves to its own pack, it-standard

Revision ID: 0058
Revises: 0057

REB-760. Until now `RF01` was a regime code beside the only Italian pack, `it-flat-rate`,
so an ordinary azienda pointed at the forfettario's pack and the ceilings code had to look
at the regime to know the thresholds did not apply. The ordinary regime has a pack of its
own (`it-standard`, no ceiling, no statutory charge) and `resolve_regime` now refuses to
pair `RF01` with the forfettario's pack, so a profile left as it was would fail its first
emission. This repoints every such profile and clears what only the forfettario carries:
the natura and the normative declaration (`N2.2` and L. 190/2014's text, which an ordinary
invoice's footer would otherwise print), the profitability coefficient, the substitute tax
rate and the INPS rate (the fiscal
estimate would otherwise compute the forfettario's taxes for an azienda it does not apply
to) and the stamp duty (an ordinary invoice has no untaxed base for it, and the profile's
own check now refuses it). No column changes, and no row on the forfettario or a foreign
pack is touched. Issued invoices keep the snapshot they were frozen with.

The downgrade points them back at `it-flat-rate`; the cleared parameters are not given
back, because nothing says what they were.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | Sequence[str] | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE fiscal_profile
        SET pack_id = 'it-standard',
            pack_version = '1',
            applica_bollo = false,
            natura_default = NULL,
            riferimento_normativo = NULL,
            coefficiente_redditivita = NULL,
            aliquota_imposta_sostitutiva = NULL,
            aliquota_inps = NULL
        WHERE pack_id = 'it-flat-rate' AND codice_regime = 'RF01'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE fiscal_profile
        SET pack_id = 'it-flat-rate', pack_version = '1'
        WHERE pack_id = 'it-standard'
        """
    )
