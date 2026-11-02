"""The numbers an azienda's register of one year does not carry, and why (spec 9 §3.2)."""

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict
from pigrocrm.core.invoices.checks import check_register_year
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.models import InvoiceRegisterGap
from pigrocrm.core.invoices.schemas import RegisterGapRead, RegisterGapsDeclare

GAPS_ACTION = "declare_invoice_register_gaps"
# How many undeclared numbers `issue`'s refusal spells out. The whole list always stays
# in `details["numeri"]`, machine-readable; the *sentence* is read by a person, and a
# register whose lowest imported number is high can leave hundreds of them.
GAPS_SHOWN_IN_MESSAGE = 20


class RegisterGaps:
    def __init__(self, ctx: InvoiceContext) -> None:
        self.ctx = ctx

    def declare_gaps(
        self,
        anno: int,
        data: RegisterGapsDeclare,
        actor: Actor,
        azienda_id: UUID | None = None,
    ) -> list[RegisterGapRead]:
        """Name the numbers the azienda's register will never carry, and why (spec 9
        §3.2 rule 4); `None` is the default azienda (REB-619).

        A declared gap is the honest alternative to two dishonest ones: inventing a row
        to fill it, or leaving it silent so that it looks like a lost invoice. It is
        refused for a number that *is* an invoice, and the import refuses a number that
        is a declared gap: the two sets never overlap.

        `lock_counter(azienda_id, anno)` first, for the same reason `import_issued` takes
        it before reading `numbers_present`/`declared_gaps`: without it, a gap declared
        here and an import of the same number could each read the register before the
        other's write, and both would go through.

        **The whole batch is validated before the first `add_gap`**: `data.buchi` carries
        no uniqueness rule of its own, so `seen` refuses a same-batch duplicate and
        `present`/`already` refuse a number that is an invoice or is already declared, all
        in one pass with nothing written yet. The writes run in a SAVEPOINT because a
        *concurrent* declaration of the same number can still reach the unique index
        underneath `uq_invoice_register_gaps_anno_numero`; that becomes a `Conflict` and
        only the savepoint is undone.
        """
        actor.require_admin(GAPS_ACTION)
        check_register_year(anno)
        ctx = self.ctx
        azienda = ctx.writing_azienda(azienda_id)
        ctx.repo.lock_counter(azienda.id, anno)
        present = ctx.repo.numbers_present(azienda.id, anno)
        already = ctx.repo.declared_gaps(azienda.id, anno)
        seen: set[int] = set()
        for buco in data.buchi:
            if buco.numero in present:
                raise Conflict(
                    ENTITY,
                    "questo numero e' una fattura del registro, non un buco",
                    anno=anno,
                    numero=buco.numero,
                )
            if buco.numero in already or buco.numero in seen:
                raise Conflict(ENTITY, "buco gia' dichiarato", anno=anno, numero=buco.numero)
            seen.add(buco.numero)

        def concurrent_declaration(exc: IntegrityError) -> Conflict:
            return Conflict(
                ENTITY, "un altro processo ha dichiarato lo stesso buco: riprova", anno=anno
            )

        with savepoint(ctx.session, concurrent_declaration):
            for buco in data.buchi:
                ctx.repo.add_gap(
                    InvoiceRegisterGap(
                        azienda_id=azienda.id,
                        anno=anno,
                        numero=buco.numero,
                        motivo=buco.motivo,
                        dichiarato_da=actor.id,
                    )
                )
                # The register has no row of its own to hang a timeline entry on, so the
                # entity id is derived deterministically from the azienda and the year:
                # `ActivityService.record` accepts any `entity_type` string, and `uuid5`
                # gives the same id every time this register is touched again.
                ctx.activities.record(
                    "invoice_register",
                    uuid5(NAMESPACE_URL, f"pigrocrm:invoice_register:{azienda.id}:{anno}"),
                    "gap_declared",
                    actor,
                    {
                        "azienda_id": str(azienda.id),
                        "anno": anno,
                        "numero": buco.numero,
                        "motivo": buco.motivo,
                    },
                )
        ctx.session.commit()
        return self.register_gaps(anno, actor, azienda.id)

    def register_gaps(
        self, anno: int, actor: Actor, azienda_id: UUID | None = None
    ) -> list[RegisterGapRead]:
        check_register_year(anno)
        azienda = self.ctx.emitter.resolve(azienda_id)
        return [RegisterGapRead.model_validate(g) for g in self.ctx.repo.gaps(azienda.id, anno)]

    def undeclared_gaps(self, anno: int, azienda_id: UUID | None = None) -> list[int]:
        """Numbers from 1 to the highest in the azienda's register that are neither an
        invoice nor a declared gap; `None` is the default azienda (REB-619). Empty is
        the only state in which native issuing may resume (spec 9 §3.2 rule 4): an
        import can arrive in any order, so a hole is expected until the operator has
        looked at every one of them and either imported or declared it.

        **From 1, not from the lowest number imported.** A year's register always starts
        at 1, so a missing 1 is exactly as much a hole as a missing 8. The previous
        system's register of 2026 is the case in point: its lowest existing invoice is
        the 2, and 1 has to be *declared* (it was never issued), not silently assumed
        away. The upper bound stays exclusive at `max(present)`: numbers above the
        highest one present are simply the future, and the counter hands them out next.
        """
        check_register_year(anno)
        azienda = self.ctx.emitter.resolve(azienda_id)
        return holes(
            self.ctx.repo.numbers_present(azienda.id, anno),
            self.ctx.repo.declared_gaps(azienda.id, anno),
        )

    def refuse_undeclared_gaps(self, anno: int, azienda_id: UUID) -> None:
        """Native issuing must not resume on top of a silent gap: the next number would
        look like it continues a register that has an unexplained hole underneath it.

        Rendered short, reported whole: `details["numeri"]` carries every number, and the
        message names the first `GAPS_SHOWN_IN_MESSAGE` plus a count. A refusal a person
        cannot read is a refusal that does not explain itself."""
        buchi = self.undeclared_gaps(anno, azienda_id)
        if not buchi:
            return
        shown = ", ".join(str(n) for n in buchi[:GAPS_SHOWN_IN_MESSAGE])
        if len(buchi) > GAPS_SHOWN_IN_MESSAGE:
            shown += f", ... ({len(buchi)} in tutto)"
        raise Conflict(
            ENTITY,
            f"il registro importato ha buchi non dichiarati ai numeri {shown}: "
            "dichiarali (o importali) prima di riprendere a emettere",
            anno=anno,
            numeri=buchi,
        )


def holes(present: set[int], declared: set[int]) -> list[int]:
    """The numbers below the register's highest that are neither present nor declared."""
    if not present:
        return []
    return [n for n in range(1, max(present)) if n not in present and n not in declared]
