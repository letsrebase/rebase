"""Keeping what the card writer made (REB-892, split out of `cards.py`): the card, or the
sentence of its failure, in one upsert under the person's row lock, and what is retired with it.

`CardStore` is the part of `CardWriter` that writes `freelancer_cards`. It is the one save
path: the synchronous write and a Message Batch's apply both end in `store`.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from rebase_core.card_persona import PERSONA_CV, persona_json, stored_gendered
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.card_regen import RegenerationTrail
from rebase_core.card_types import FAILURES, Composed, Failure, Outcome
from rebase_core.errors import LlmCapReached, LlmNotConfigured, LlmUnavailable
from rebase_core.models import Freelancer, FreelancerCard
from rebase_core.team_schemas import CardPersonaRead

logger = logging.getLogger(__name__)

# The last successful generation, written together and retired together. `card` is
# retired with SQL `NULL`, never `None`: the JSONB type binds `None` as the JSON value
# `null`, which `card IS NOT NULL` (the catalogue's filter) still counts as a card.
_RETIRED: dict[str, Any] = {
    "cv_sha256": None,
    "card": null(),
    "model": None,
    "input_tokens": None,
    "output_tokens": None,
    "generated_at": None,
    "prompt_version": None,
    "provider": None,
    "fonti": null(),
    "source_hashes": null(),
    "conflicts": null(),
    "thinking_tokens": None,
    "persona": null(),
}


class CardStore(RegenerationTrail):
    """The save path of `CardWriter`."""

    def store(self, freelancer_id: UUID, digest: str, composed: Composed) -> Outcome:
        """Keeps what `compose` (or a Message Batch's result through `finish`) made of the
        CV `digest`: the card, or the sentence of its failure. The one save path: the
        synchronous write and the batch's apply both end here. When a person started the
        write (`run`), what became of the card is signed in the talent's trail."""
        outcome = self._store(freelancer_id, digest, composed)
        self.card_done(freelancer_id, outcome, reason=composed.failure, model=composed.model)
        return outcome

    def _store(self, freelancer_id: UUID, digest: str, composed: Composed) -> Outcome:
        if composed.card is None:
            if composed.failure is None:
                raise ValueError("a Composed with no card says why")
            return self._fail(freelancer_id, digest, composed.failure, composed.persona)
        saved = self._save(
            freelancer_id,
            digest,
            {
                "cv_sha256": digest,
                "card": composed.card.model_dump(mode="json"),
                "model": composed.model,
                "input_tokens": composed.input_tokens,
                # What was billed as output, thinking included, as the row has always
                # counted it.
                "output_tokens": composed.output_tokens + composed.thinking_tokens,
                "generated_at": self.now(),
                "error": None,
                "error_cv_sha256": None,
                # What it was written from and by what (REB-839): the inputs as they
                # were when the prompt was built, so a change after it marks the card.
                "prompt_version": PROMPT_VERSION,
                "provider": composed.provider or None,
                "fonti": composed.fonti,
                "source_hashes": composed.input_hashes,
                "conflicts": [conflict._asdict() for conflict in composed.conflicts],
                "thinking_tokens": composed.thinking_tokens,
                "persona": (
                    persona_json(composed.persona, digest)
                    if composed.persona is not None
                    else null()
                ),
            },
        )
        if not saved:
            return "superseded"
        return "written"

    def unanswered(self, freelancer_id: UUID, digest: str, error: LlmUnavailable) -> Outcome:
        """The call did not give a card. A real outage is `_fail`'s «unavailable». A call
        the seam refused before any network, for the daily cap or a task with no model, is
        not a provider outage and does not read like one: the stored sentence is the
        reason «Modelli AI» shows («Tetto giornaliero raggiunto», …), and nothing is
        retired, since the previous card is as good as it was. Both stop the batch, as an
        outage does: the next call would be refused the same."""
        outcome, reason = self._unanswered(freelancer_id, digest, error)
        self.card_done(freelancer_id, outcome, reason=reason)
        return outcome

    def _unanswered(
        self, freelancer_id: UUID, digest: str, error: LlmUnavailable
    ) -> tuple[Outcome, str]:
        if isinstance(error, LlmCapReached):
            reason = "cap_reached"
        elif isinstance(error, LlmNotConfigured):
            reason = "not_configured"
        else:
            return self._fail(freelancer_id, digest, "unavailable"), "unavailable"
        if not self._save(freelancer_id, digest, {"error": error.message, "error_cv_sha256": None}):
            return "superseded", reason
        logger.warning("card for freelancer %s not written: %s", freelancer_id, "refused")
        return "unavailable", reason

    def _fail(
        self,
        freelancer_id: UUID,
        digest: str,
        kind: Failure,
        persona: CardPersonaRead | None = None,
    ) -> Outcome:
        """The sentence beside the card, and the failed CV's hash unless the failure was
        the provider's: an outage keeps nothing that would stop the next attempt. Every
        failure, an outage too, retires a card written from another CV than the one
        that just failed (`_save`): the person replaced that CV, and its card must not
        stay in the catalogue until Claude answers again. A card of the same CV stays,
        except, after a `gendered` failure, one that itself gives the gender away (a card
        written before REB-802 turned the guard into a gate): the catalogue would go on
        showing it to companies beside an error nobody reads there. The persona the
        model read (REB-855) is stored whatever the failure, when there is one: the
        contacts are not the card's, and the admin wants them even when the guard refused
        the card."""
        outage = kind == "unavailable"
        values: dict[str, Any] = {
            "error": FAILURES[kind],
            "error_cv_sha256": None if outage else digest,
        }
        if persona is not None:
            values["persona"] = persona_json(persona, digest)
        if not self._save(
            freelancer_id,
            digest,
            values,
            retire_other=True,
            retire_gendered=kind == "gendered",
        ):
            return "superseded"
        logger.warning("card for freelancer %s not written: %s", freelancer_id, kind)
        return "unavailable" if outage else "failed"

    def _save(
        self,
        freelancer_id: UUID,
        digest: str,
        values: dict[str, Any],
        *,
        retire_other: bool = False,
        retire_gendered: bool = False,
    ) -> bool:
        """One upsert of the given columns, and only if the freelancer's CV is still the
        one `digest` was taken from: re-read under the row's lock, which `clear_cv`'s and
        `replace_cv`'s `UPDATE` take too, so a CV cleared or replaced while Claude was
        writing is never given a card, or a failure, that is not its own. `False` when
        it was, with nothing written. `retire_other` also empties the card columns when
        the stored card came from another CV: read under the same lock, since every
        writer of this row holds it; `retire_gendered` does the same for a stored card of
        this very CV that `_gendered` flags. Two writes racing on one CV (a double «Rigenera
        scheda») both land, the later one last, rather than the second failing on the
        primary key."""
        current = self.session.scalar(
            select(Freelancer.cv_sha256).where(Freelancer.id == freelancer_id).with_for_update()
        )
        if current != digest:
            self.session.commit()
            logger.info("card for freelancer %s not written: the CV changed", freelancer_id)
            return False
        stored = self.session.execute(
            select(FreelancerCard.cv_sha256, FreelancerCard.card, FreelancerCard.persona).where(
                FreelancerCard.freelancer_id == freelancer_id
            )
        ).one_or_none()
        if stored is not None:
            written_from, written, named = stored
            other = retire_other and written_from is not None and written_from != digest
            if other or (retire_gendered and stored_gendered(written)):
                values = {**_RETIRED, **values}
            # The named card belongs to the CV it was read from (`PERSONA_CV` in the stored
            # value): a write for another CV that read none of its own never leaves the old
            # one in place, whatever the card's own state.
            if "persona" not in values and named is not None and named.get(PERSONA_CV) != digest:
                values = {**values, "persona": null()}
        insert = pg_insert(FreelancerCard).values(freelancer_id=freelancer_id, **values)
        self.session.execute(
            insert.on_conflict_do_update(
                index_elements=[FreelancerCard.freelancer_id],
                set_={
                    **{column: insert.excluded[column] for column in values},
                    "updated_at": func.now(),
                },
            )
        )
        self.session.commit()
        return True
