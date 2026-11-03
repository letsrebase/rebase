from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.db import escape_like
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.schemas import TemplateListQuery


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, template_id: UUID) -> Template | None:
        return self.session.get(Template, template_id)

    def get_by_name(self, nome: str) -> Template | None:
        """Case-insensitive, matching the `uq_templates_nome` functional unique index
        on `lower(nome)` (models.py) exactly. `func.lower(Template.nome)` references
        the mapped column -- never a string literal, which `func.lower` would bind as
        a constant instead of a column reference, the exact bug Task 6 found and fixed
        in its own brief's sample code for the index itself."""
        stmt = select(Template).where(func.lower(Template.nome) == nome.lower())
        return self.session.execute(stmt).scalars().first()

    def add(self, template: Template) -> Template:
        self.session.add(template)
        self.session.flush()
        return template

    # `list` stays the last method defined in this class: naming a method `list`
    # rebinds that name in the class namespace, so any later method with a bare
    # `-> list[...]` return annotation would resolve `list` to this method instead of
    # the builtin and fail at import time (see `CustomerRepository.list`'s identical
    # rule, and `FieldDefinitionService.specs_for`'s docstring).
    def list(self, query: TemplateListQuery) -> list[Template]:
        stmt = select(Template)
        if not query.include_inactive:
            stmt = stmt.where(Template.active.is_(True))
        if query.type:
            stmt = stmt.where(Template.type == query.type)
        if query.search:
            # escape_like neutralizes "%"/"_"/"\" in the user's own term before it is
            # wrapped in the wildcard this method builds -- see
            # `CustomerRepository.list`'s identical comment for why.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(Template.nome.ilike(like, escape="\\"))
        if query.cursor:
            stmt = stmt.where(Template.id > query.cursor)

        # Keyset pagination on a UUIDv7 id: ordered by creation, stable under inserts.
        return list(
            self.session.execute(stmt.order_by(Template.id).limit(query.limit + 1)).scalars()
        )
