"""The texts the hub rendered, as templates of the `collaborators` module (collaborators design § 1,
«The documents», and § 7).

The seven texts in `texts/` are the hub's own, byte for byte, in their hub form (flat hyphenated
keys, `{{se key}}` conditionals): the framework agreements and engagement letters of REB-387 and
REB-687 with the pay-after-the-client clause of REB-979 (framework 1.1, letter 1.2) unchanged, and
the freelancer NDA and the company's general terms and proposal of REB-689. `convert_hub_text`
(`letters.HUB_KEY_MAP`) turns each into the CRM engine's dotted keys when it is seeded; the import
at the cutover runs the same function over whatever the hub's texts say by then.

Seeding follows the rest of the space's defaults: a text is written only while the space has none
of its kind, so an admin who edited or deleted one never finds the original back.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.letters import (
    ENGAGEMENT_LETTER,
    FRAMEWORK_AGREEMENT,
    convert_hub_text,
)
from pigrocrm.core.templates.ast import EachNode, IfNode, Node, VariableNode
from pigrocrm.core.templates.parser import parse_template
from pigrocrm.core.templates.schemas import (
    LetterType,
    TemplateCreate,
    TemplateRead,
    TemplateType,
    TemplateVariable,
)
from pigrocrm.core.templates.service import TemplateService

TEXTS = Path(__file__).resolve().parent / "texts"

# The roots the engine's scope fills by itself for these texts: the space's legal entity, the
# freelancer, the client, the engagement and the signature blocks. Everything else a text reads at
# the top level is a field the admin writes for each letter.
SCOPE_ROOTS = frozenset({"emittente", "professionista", "cliente", "incarico", "firma", "oggi"})


@dataclass(frozen=True)
class TextSeed:
    file: str
    name: str
    type: TemplateType
    letter_type: LetterType | None


# Product copy: the names an admin picks a text by.
SEEDS: tuple[TextSeed, ...] = (
    TextSeed("framework-agreement", "Contratto quadro", FRAMEWORK_AGREEMENT, "ordinary"),
    TextSeed(
        "framework-agreement-occasional",
        "Contratto quadro per prestazioni occasionali",
        FRAMEWORK_AGREEMENT,
        "occasional",
    ),
    TextSeed("engagement-letter", "Lettera di incarico", ENGAGEMENT_LETTER, "ordinary"),
    TextSeed(
        "engagement-letter-occasional",
        "Lettera di incarico occasionale",
        ENGAGEMENT_LETTER,
        "occasional",
    ),
    # Not wired into a flow yet (REB-689): the freelancer's NDA and the company's contract are
    # templates of the module, written in draft, that an admin can generate and review.
    TextSeed("freelancer-nda", "Accordo di riservatezza", "contract", None),
    TextSeed("company-general-terms", "Condizioni generali di servizio", "contract", None),
    TextSeed("company-proposal", "Proposta di servizio", "contract", None),
)


def seed_body(seed: TextSeed) -> str:
    """The text as the hub carries it, through the key map."""
    return convert_hub_text((TEXTS / f"{seed.file}.md").read_text(encoding="utf-8"))


def _root_names(nodes: tuple[Node, ...]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        if isinstance(node, VariableNode):
            if len(node.path) == 1 and node.path[0] not in SCOPE_ROOTS:
                names.append(node.path[0])
        elif isinstance(node, IfNode):
            names += _root_names(node.then) + _root_names(node.otherwise)
        elif isinstance(node, EachNode):
            names += _root_names(node.body)
    return names


def declared_variables(body: str) -> list[TemplateVariable]:
    """The fields the admin writes for each letter: every top-level name the text reads that the
    scope does not fill. All optional: a field left empty prints as a blank line on the page."""
    seen: dict[str, None] = {}
    for name in _root_names(parse_template(body)):
        seen.setdefault(name)
    return [
        TemplateVariable(
            name=name, label=name.replace("_", " ").capitalize(), type="textarea", required=False
        )
        for name in seen
    ]


def seed_collaborator_templates(session: Session, actor: Actor) -> list[TemplateRead]:
    """The texts of the module this space does not have yet, written as active templates; what it
    wrote, so a caller can tell «seeded» from «already there». A kind and regime that has an
    active template of its own is left alone, and so is a name that is taken."""
    service = TemplateService(session)
    created: list[TemplateRead] = []
    for seed in SEEDS:
        if service.repo.get_by_name(seed.name) is not None:
            continue
        if seed.letter_type is not None and service.repo.active_letter(seed.type, seed.letter_type):
            continue
        body = seed_body(seed)
        created.append(
            service.create(
                TemplateCreate(
                    name=seed.name,
                    type=seed.type,
                    body_markdown=body,
                    declared_variables=declared_variables(body),
                    letter_type=seed.letter_type,
                ),
                actor,
            )
        )
    return created
