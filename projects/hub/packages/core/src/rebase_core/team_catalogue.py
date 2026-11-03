"""Who the team builder and the talent cloud can show, and the catalogue Claude reads
(REB-511, spec § 3.3, § 4.2; REB-892 split it out of `team_builder.py`).

`cloud_visible` is the one filter of who is in the catalogue and the cloud: the engine
(`team_builder`), the cloud's reads (`cloud`) and the requests (`team_requests`) all go
through it, so they cannot disagree about who exists. `catalogue_lines` is the prompt's
cached prefix: one JSON line per such freelancer, sorted by the freelancer's id, so the
text is byte for byte the same for every visitor until a card changes. A line names its
freelancer by its position, `t1`, `t2`...: never by the UUID, which the public must not
collect, and never by a slice of it, which two signups in the same millisecond share.
"""

import json
import logging
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from rebase_core.bands import band_for
from rebase_core.card_level import card_read
from rebase_core.models import Freelancer, FreelancerCard

logger = logging.getLogger(__name__)


def cloud_visible[*Ts](stmt: Select[*Ts]) -> Select[*Ts]:
    """The one filter of who is in the catalogue and the talent cloud (shared with D3):
    a live freelancer (`deleted_at IS NULL`), not turned down (`stato != 'scartato'`),
    with a card. `stmt` selects from `Freelancer` and has not joined `FreelancerCard`:
    this joins it. «With a card» is a JSON object: the card writer retires a card to SQL
    `NULL`, and a JSON `null` left by any other writer is not an object either."""
    return stmt.join(FreelancerCard, FreelancerCard.freelancer_id == Freelancer.id).where(
        Freelancer.deleted_at.is_(None),
        Freelancer.status != "discarded",
        func.jsonb_typeof(FreelancerCard.card) == "object",
    )


def catalogue_lines(session: Session) -> tuple[str, list[UUID]]:
    """One JSON line per `cloud_visible` freelancer, sorted by `Freelancer.id` so the
    text is the same across requests (the cache prefix): the card's structured fields,
    its `location` for the engine alone, the work mode and the band, but no `profile_summary`, no
    name, no link, no rate and no id of ours. `"id"` is the line's position, `t1` first;
    the second value is the freelancer behind each position, in order."""
    rows = session.execute(
        cloud_visible(
            select(
                Freelancer.id,
                Freelancer.remote,
                Freelancer.daily_rate,
                FreelancerCard.card,
                Freelancer.seniority_admin,
                Freelancer.experience_years_admin,
            )
        ).order_by(Freelancer.id)
    ).all()
    lines: list[str] = []
    positions: list[UUID] = []
    for freelancer_id, remote, rate, stored, seniority_admin, experience_years_admin in rows:
        try:
            card = card_read(stored, seniority_admin, experience_years_admin)
        except ValidationError:
            logger.warning("catalogue: the card of freelancer %s is not a card", freelancer_id)
            continue
        positions.append(freelancer_id)
        band = band_for(rate)
        line = {
            "id": f"t{len(positions)}",
            "role": card.role,
            "seniority": card.seniority,
            "experience_years": card.experience_years,
            "seniority_uncertain": card.seniority_uncertain,
            "skills": card.skills,
            "industries": card.industries,
            "languages": card.languages,
            "location": card.location,
            "mode": remote,
            "band": band.bounds() if band is not None else None,
        }
        lines.append(json.dumps(line, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines), positions
