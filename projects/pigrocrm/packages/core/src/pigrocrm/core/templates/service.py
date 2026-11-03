"""`TemplateService`: CRUD over `Template` rows, plus `describe` and `preview`.

Written before `task-8-brief.md` existed in the plan directory (a coordinator
omission, since corrected); reconciled against the real brief afterwards -- see
`task-8-report.md`'s "Riconciliazione col brief" section for exactly what changed
and what was kept as a deliberate deviation. Mutation permissions, the
uniqueness-conflict shape and the list/pagination shape are drawn from the closest
sibling in this codebase: `PipelineService` and `FieldDefinitionService` both gate
their own config-shaped entities (pipeline stages, custom field definitions) behind
`actor.require_admin`, not `require_write` -- a template is the same kind of thing,
an org-wide configuration object that shapes every future document, not a
day-to-day record like a customer or a deal, so it follows the same rule here.

`describe` and `preview` are the two methods this task exists for:

- `describe` reports what a template needs from a caller *before* asking the user
  to supply anything -- the compilation-form variables (`declared_variables`,
  declared, not deduced) plus every other path the template body actually
  references (`templates.parser.declared_paths`), split exactly as that function
  already splits them (root vs. loop-relative). This is what makes an MCP
  `describe_template` tool worth calling at all: without the paths half, an agent
  would learn only about the handful of variables a human is meant to fill in and
  have no way to know the template also needs, say, `customer.legal_name` or
  `emittente.vat_number` supplied by whatever calls `preview`/renders the document.
- `preview` compiles a template's `body_markdown` against caller-supplied values
  and returns the compiled Markdown itself, as a plain `str` (not a PDF -- PDF
  rendering is `pigrocrm.core.render.pdf`, not built yet -- and not wrapped in a
  schema, since there is nothing else to carry alongside it: the "fail precisely
  rather than render a document with a hole in it" behaviour already lives in
  `templates.renderer.render_template`'s own `ValidationFailed`, naming the
  missing/unresolved variable and the template line, which this method lets
  propagate rather than translating into anything preview-specific).
"""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.render.pdf import ASSETS_DIR
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.parser import declared_paths, parse_template
from pigrocrm.core.templates.renderer import DeclaredVariable, render_template
from pigrocrm.core.templates.repository import TemplateRepository
from pigrocrm.core.templates.schemas import (
    TemplateCreate,
    TemplateDescription,
    TemplateListQuery,
    TemplatePage,
    TemplateRead,
    TemplateUpdate,
    TemplateVariable,
)

ENTITY = "template"


def _conflicting_name(name: str) -> Conflict:
    return Conflict(ENTITY, "esiste già un template con questo nome", name=name)


# The default offer a space is born with (spec 2026-09-12 §6.5). Flat variables on
# purpose: a declared variable is one field of the compilation form, so the body cannot
# iterate a list the way `render/assets/template-offer.md` does with `offer.lines`.
# `customer.*`, `emittente.*` and `today` come from `DocumentTemplating.template_scope`.
OFFER_TEMPLATE_NAME = "Offerta"
OFFER_TEMPLATE_VARIABLES: tuple[dict[str, Any], ...] = (
    {"name": "subject", "label": "Oggetto", "type": "text", "required": True},
    {"name": "ambito", "label": "Ambito e obiettivi", "type": "textarea", "required": True},
    {"name": "attivita", "label": "Attività", "type": "textarea", "required": True},
    {
        "name": "fee",
        "label": "Condizioni economiche",
        "type": "textarea",
        "required": True,
    },
    {
        "name": "pagamento",
        "label": "Fatturazione e pagamento",
        "type": "textarea",
        "required": True,
    },
)


class TemplateService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TemplateRepository(session)
        self.activities = ActivityService(session)

    def create(self, data: TemplateCreate, actor: Actor) -> TemplateRead:
        actor.require_unscoped_admin("create_template")
        # Fails fast, at save time, naming the offending template line -- rather
        # than at the first `preview`/render, which could be long after whoever
        # wrote the template has moved on. `parse_template` already raises
        # `ValidationFailed(entity="template", field="corpo_markdown", ...)` itself
        # (parser.py's own ENTITY/FIELD constants), so there is nothing to catch or
        # re-wrap here.
        parse_template(data.body_markdown)
        if self.repo.get_by_name(data.name) is not None:
            raise _conflicting_name(data.name)

        template = Template(**data.model_dump())
        try:
            self.repo.add(template)
            self.activities.record(ENTITY, template.id, "created", actor, {"name": template.name})
            self.session.commit()
        except IntegrityError as exc:
            # The pre-check above cannot cover two concurrent creates that both pass
            # it before either commits -- `uq_templates_nome` (case-insensitive, on
            # `lower(nome)`) is the real authority, so a case-only duplicate ("Offerta
            # Standard" vs. "OFFERTA STANDARD") still surfaces as this same clean
            # Conflict rather than a raw IntegrityError. The rollback is mandatory:
            # without it the caller's session is unusable on its next statement.
            self.session.rollback()
            raise _conflicting_name(data.name) from exc
        return TemplateRead.model_validate(template)

    def update(self, template_id: UUID, data: TemplateUpdate, actor: Actor) -> TemplateRead:
        actor.require_unscoped_admin("update_template")
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)

        # See `UserService.update`: converted for the same reason, though every column
        # `TemplateUpdate` can reach is `NOT NULL`, so here the change is only that a
        # `null` is refused by name instead of being silently discarded.
        changes = supplied_changes(data)
        reject_cleared_columns(ENTITY, Template, changes)
        if "body_markdown" in changes:
            parse_template(changes["body_markdown"])
        if "name" in changes:
            existing = self.repo.get_by_name(changes["name"])
            # `existing is not template`, not merely `is not None`: renaming a
            # template to a case-only variant of its own current name (e.g.
            # "Offerta" -> "OFFERTA") must not conflict with itself. Both `existing`
            # and `template` come from the same `Session`'s identity map, so a
            # lookup that resolves to this same row returns the identical Python
            # object, not merely an equal one.
            if existing is not None and existing is not template:
                raise _conflicting_name(changes["name"])
        for key, value in changes.items():
            setattr(template, key, value)

        try:
            self.activities.record(
                ENTITY, template.id, "updated", actor, {"changed": sorted(changes)}
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise _conflicting_name(changes.get("name", template.name)) from exc
        return TemplateRead.model_validate(template)

    def get(self, template_id: UUID, actor: Actor) -> TemplateRead:
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)
        return TemplateRead.model_validate(template)

    def deactivate(self, template_id: UUID, actor: Actor) -> TemplateRead:
        """Sets `attivo = False`. `Template` has no `deleted_at` (Task 6's own
        decision: see the model's docstring) -- this is that model's equivalent of
        every other domain's soft delete, hiding a template from `list`'s default
        view and from being picked for a new document while keeping every document
        already generated from it, and its own history, fully intact."""
        actor.require_unscoped_admin("deactivate_template")
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)
        was_active = template.active
        template.active = False
        if was_active:
            self.activities.record(ENTITY, template.id, "deactivated", actor)
        self.session.commit()
        return TemplateRead.model_validate(template)

    def activate(self, template_id: UUID, actor: Actor) -> TemplateRead:
        actor.require_unscoped_admin("activate_template")
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)
        was_active = template.active
        template.active = True
        if not was_active:
            self.activities.record(ENTITY, template.id, "activated", actor)
        self.session.commit()
        return TemplateRead.model_validate(template)

    def describe(self, template_id: UUID, actor: Actor) -> TemplateDescription:
        """Root paths only in `used_journeys`: `describe` answers "what must the
        caller supply", and a caller supplies root-level values, never a
        loop-relative one -- see `TemplateDescription`'s own docstring. A declared
        variable is "used" when its own name is the first segment of some root path
        the body reads (`{{oggetto}}` and `{{oggetto.riga}}` both count); anything
        declared but never referenced that way is a compilation-form field nobody's
        document will ever show, worth flagging as `variables_not_used` -- an
        authoring mistake, not merely a naming curiosity.
        """
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)
        declared = [TemplateVariable(**v) for v in template.declared_variables]
        root_paths = declared_paths(parse_template(template.body_markdown)).root
        used_names = {path[0] for path in root_paths}
        return TemplateDescription(
            id=template.id,
            name=template.name,
            type=template.type,
            variables=declared,
            used_journeys=[list(path) for path in root_paths],
            variables_not_used=[v.name for v in declared if v.name not in used_names],
        )

    def declared_variables(self, template: Template) -> tuple[DeclaredVariable, ...]:
        """`template.declared_variables` is JSONB -- plain `dict`s, one per declared
        variable, validated on the way in by `TemplateVariable` (schemas.py) but stored
        with no Python type of their own. `render_template` (renderer.py) wants the
        dataclass it already defines for exactly this shape; only the four fields it
        actually reads are passed through -- `options` (schemas.py's own addition, for
        the compilation form, not the renderer) is dropped here rather than in storage,
        so a round trip through `describe` still returns it. This is the intentional
        `TemplateVariable`/`DeclaredVariable` asymmetry: two types, not one, because
        the renderer has no use for `options` and the compilation form cannot do
        without it.

        Public, not a module-level helper: Task 11's `create_document_from_template`
        calls `self.templates.declared_variables(template)` directly, to build the
        same `DeclaredVariable` tuple this method's own `preview` uses, against a
        `Template` it already has in hand rather than one it would otherwise have to
        re-fetch through this service.
        """
        return tuple(
            DeclaredVariable(
                name=v["name"],
                label=v["etichetta"],
                type=v["tipo"],
                required=v["obbligatoria"],
            )
            for v in template.declared_variables
        )

    def preview(self, template_id: UUID, values: dict[str, Any], actor: Actor) -> str:
        """The compiled Markdown -- not a PDF, not a wrapper: nothing beyond the
        markdown itself is carried, so a plain `str` is the whole of what there is to
        return, and `render_template` already raises `ValidationFailed` (naming the
        missing/unresolved variable and the template line) rather than returning
        anything partial for this method to surface differently."""
        template = self.repo.get(template_id)
        if template is None:
            raise NotFound(ENTITY, template_id)
        return render_template(template.body_markdown, values, self.declared_variables(template))

    def seed_defaults(self, actor: Actor) -> list[TemplateRead]:
        """Admin-only, like every other write method on this class (`create`,
        `update`, `deactivate`/`activate` all call `actor.require_admin` themselves).

        On `PipelineService.seed_defaults`'s model -- including this admin check: that
        method's own docstring explains why it has to live here rather than solely in
        whichever router happens to call it first, and `test_pipeline.py`'s
        `test_seed_defaults_requires_admin` is the regression test for exactly this
        lesson. Deduplicates on `lower(nome)` to match `uq_templates_nome`'s own
        functional index -- a case-sensitive check would pass for "rapporto ore"
        against a stored "Rapporto ore" and then be refused by the database as a raw
        `IntegrityError`.

        Seeds three templates: the timesheet, slice 5's payment reminder and the
        default offer (spec 2026-09-12 §6.5).
        `render/assets/template-offer.md` is deliberately left alone -- adopting it
        would change slice 2's shipped behaviour in a slice that is not about offers.

        Returns only what it actually created, so a caller can tell "seeded" from
        "already there".
        """
        actor.require_unscoped_admin("seed_templates")
        from pigrocrm.core.gmail.payment_reminders_template import (  # local: avoids a package cycle
            PAYMENT_REMINDER_TEMPLATE_NAME,
            PAYMENT_REMINDER_TEMPLATE_SOURCE,
            PAYMENT_REMINDER_TEMPLATE_VARIABLES,
        )
        from pigrocrm.core.timetracking.report import (  # local: avoids a package cycle
            TIME_REPORT_TEMPLATE_NAME,
            TIME_REPORT_TEMPLATE_VARIABLES,
        )

        seeds = (
            (
                TIME_REPORT_TEMPLATE_NAME,
                "hours_report",
                (ASSETS_DIR / "template-time-report.md").read_text(encoding="utf-8"),
                TIME_REPORT_TEMPLATE_VARIABLES,
            ),
            # The reminder body of spec 7.3, seeded so a user can edit the wording that
            # goes out in their name without touching Python. Its source lives in
            # `gmail/solleciti_template.py` rather than in `ASSETS_DIR` because the
            # reminder path renders that constant directly -- a reminder must go out
            # even on an install where nobody ran `seed_defaults`, and the row here is
            # the editable copy, not the only copy.
            (
                PAYMENT_REMINDER_TEMPLATE_NAME,
                "payment_reminder",
                PAYMENT_REMINDER_TEMPLATE_SOURCE,
                PAYMENT_REMINDER_TEMPLATE_VARIABLES,
            ),
            # The offer every space starts from (spec 2026-09-12 §6.5): the one
            # document the landing promises, editable in Impostazioni → Template like
            # the other two. `template-offer.md` stays the renderer's own asset.
            (
                OFFER_TEMPLATE_NAME,
                "offer",
                (ASSETS_DIR / "template-offer-default.md").read_text(encoding="utf-8"),
                OFFER_TEMPLATE_VARIABLES,
            ),
        )
        created: list[TemplateRead] = []
        for name, type, body, variables in seeds:
            if self.repo.get_by_name(name) is not None:
                continue
            template = self.repo.add(
                Template(
                    name=name,
                    type=type,
                    body_markdown=body,
                    declared_variables=list(variables),
                    active=True,
                )
            )
            self.activities.record(
                "template", template.id, "created", actor, {"name": name, "seed": True}
            )
            created.append(TemplateRead.model_validate(template))
        self.session.commit()
        return created

    # `list` must stay the last method defined in this class -- see
    # `TemplateRepository.list`'s identical comment for the import-time crash this
    # avoids.
    def list(self, query: TemplateListQuery, actor: Actor) -> TemplatePage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        return TemplatePage(
            items=[TemplateRead.model_validate(t) for t in items],
            next_cursor=items[-1].id if has_more and items else None,
        )
