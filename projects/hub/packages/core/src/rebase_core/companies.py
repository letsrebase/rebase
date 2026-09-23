"""Companies: a project that needs people, and what an admin does with the request."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from rebase_core.audit import (
    TIMELINE_LIMIT_DEFAULT,
    AdminActionRead,
    AdminActionService,
    coerce_stored_value,
    field_changes,
    reject_cleared_columns,
    supplied_changes,
    utcnow,
)
from rebase_core.comments import CommentService
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import COMPANY_STATES, Company, User
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.schemas import (
    CompanyCreate,
    CompanyList,
    CompanyOverride,
    CompanyRead,
    StatusChange,
)
from rebase_core.search import matches_any, similarity_score
from rebase_core.users import UserService

ENTITY = "company"
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
_SEARCH_COLUMNS = (
    Company.nome_azienda,
    User.nome,
    User.cognome,
    User.email,
    Company.progetto,
    Company.figura_richiesta,
)
# The three fields a request's referente shares with its `users` row (REB-281): an
# override of one of these lands on the identity, never on the row, the same split
# `rebase_core.freelancers`' own `_ADMIN_IDENTITY_FIELDS` keeps for a freelancer card.
_ADMIN_IDENTITY_FIELDS = ("nome", "cognome", "linkedin_url")


def _to_read(row: Company, user: User) -> CompanyRead:
    """`referente`/`email` read off the linked `users` row since migration B (REB-281)
    dropped the request's own copies: a company's several requests over time all name
    the one person on file, not a free-text string each request could drift from."""
    return CompanyRead(
        id=row.id,
        nome_azienda=row.nome_azienda,
        referente=f"{user.nome} {user.cognome}".strip(),
        email=user.email,
        telefono=user.telefono,
        figura_richiesta=row.figura_richiesta,
        progetto=row.progetto,
        periodo_da=row.periodo_da,
        durata=row.durata,
        budget_giornaliero=row.budget_giornaliero,
        remoto=row.remoto,
        giorni_presenza=row.giorni_presenza,
        numero_risorse=row.numero_risorse,
        stato=row.stato,
        note=row.note,
        origine=row.origine,
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        utm_content=row.utm_content,
        utm_term=row.utm_term,
        utm_id=row.utm_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _list_stmt(
    *,
    q: str,
    budget_min: Decimal | None,
    budget_max: Decimal | None,
    periodo_da: date | None,
    origine: str | None,
    creato_da: datetime | None,
    creato_a: datetime | None,
) -> Select[Any]:
    """Every company request, `stato` left for the caller to filter separately -- this
    statement backs both the listing and the per-state `GROUP BY` counts, which must
    see every state at once."""
    stmt = select(Company, User).join(User, User.id == Company.user_id)
    stmt = stmt.where(Company.deleted_at.is_(None))
    if q:
        stmt = stmt.where(matches_any(_SEARCH_COLUMNS, q))
    if budget_min is not None:
        stmt = stmt.where(Company.budget_giornaliero >= budget_min)
    if budget_max is not None:
        stmt = stmt.where(Company.budget_giornaliero <= budget_max)
    if periodo_da is not None:
        stmt = stmt.where(Company.periodo_da >= periodo_da)
    if origine is not None:
        stmt = stmt.where(Company.origine == origine)
    if creato_da is not None:
        stmt = stmt.where(Company.created_at >= creato_da)
    if creato_a is not None:
        stmt = stmt.where(Company.created_at <= creato_a)
    return stmt


class CompanyService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def request(self, data: CompanyCreate) -> tuple[CompanyRead, bool]:
        """Every request is a row: a company has several projects, and two requests a
        week apart are two things to answer, not one to merge. The referente's `users`
        row is get-or-created by lowercased email (REB-278), from the two fields the
        wizard collects (REB-279, decision (f)) plus, since REB-380, a phone number,
        and left as it was found on a repeat request: the answer's `referente`/`email`
        (and now `telefono`) read off that one row (REB-281), whether or not it
        matches what this particular request said.

        The second element is whether this `user_id` already had at least one other
        request before this one, checked before the new row is added (REB-380): a
        returning referente the thank-you page can point at the member area they may
        not know exists, mirroring `FreelancerService.apply`'s own `created` boolean."""
        utm = data.utm.model_dump() if data.utm is not None and not data.utm.is_empty() else {}
        email = data.email.strip().lower()
        user = UserService(self.session).get_or_create(
            email, data.referente_nome, data.referente_cognome, telefono=data.telefono
        )
        richiedente_esistente = (
            self.session.scalar(select(Company.id).where(Company.user_id == user.id).limit(1))
            is not None
        )
        row = Company(
            user_id=user.id,
            nome_azienda=data.nome_azienda,
            figura_richiesta=data.figura_richiesta,
            progetto=data.progetto,
            periodo_da=data.periodo_da,
            durata=data.durata,
            budget_giornaliero=data.budget_giornaliero,
            remoto=data.remoto,
            giorni_presenza=data.giorni_presenza,
            numero_risorse=data.numero_risorse,
            **utm,
        )
        self.session.add(row)
        self.session.commit()
        return _to_read(row, user), richiedente_esistente

    def list_recent(
        self,
        limit: int = LIST_LIMIT_DEFAULT,
        stato: str | None = None,
        *,
        q: str | None = None,
        cursor: str | None = None,
        budget_min: Decimal | None = None,
        budget_max: Decimal | None = None,
        periodo_da: date | None = None,
        origine: str | None = None,
        creato_da: datetime | None = None,
        creato_a: datetime | None = None,
    ) -> CompanyList:
        """Newest-or-best-match first (REB-285 adds `q`, `cursor` and the filters below
        `stato`; the field itself is unvalidated pass-through, like every other filter
        in this package -- an unknown value is simply an empty list, never a 422)."""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        term = (q or "").strip()

        base = _list_stmt(
            q=term,
            budget_min=budget_min,
            budget_max=budget_max,
            periodo_da=periodo_da,
            origine=origine,
            creato_da=creato_da,
            creato_a=creato_a,
        )

        grouped = self.session.execute(
            base.with_only_columns(Company.stato, func.count()).group_by(Company.stato)
        ).all()
        by_state: dict[str, int] = {state: count for state, count in grouped}
        per_stato = {state: by_state.get(state, 0) for state in COMPANY_STATES}
        totale = sum(per_stato.values()) if stato is None else per_stato.get(stato, 0)

        stmt = base if stato is None else base.where(Company.stato == stato)
        sort_spec = SortSpec("score", "float") if term else SortSpec("created_at", "datetime")
        sort_column: Any
        if term:
            score = similarity_score(_SEARCH_COLUMNS, term).label("score")
            stmt = stmt.add_columns(score)
            sort_column = score
        else:
            sort_column = Company.created_at
        if cursor:
            value, row_id = decode_cursor(sort_spec, cursor)
            stmt = stmt.where(keyset_predicate(sort_column, Company.id, value, row_id))
        rows = self.session.execute(
            stmt.order_by(sort_column.desc(), Company.id.desc()).limit(limit + 1)
        ).all()

        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            last_sort = last.score if term else last[0].created_at
            next_cursor = encode_cursor(sort_spec, last_sort, last[0].id)

        return CompanyList(
            totale=totale,
            items=[_to_read(row[0], row[1]) for row in page_rows],
            per_stato=per_stato,
            next_cursor=next_cursor,
        )

    def get(self, company_id: UUID) -> CompanyRead:
        """The row with its thread of comments, newest first. Only here: the list
        leaves `commenti` empty."""
        row, user = self._require(company_id)
        read = _to_read(row, user)
        read.commenti = CommentService(self.session).list(ENTITY, company_id)
        return read

    def set_status(self, company_id: UUID, change: StatusChange) -> CompanyRead:
        if change.stato not in COMPANY_STATES:
            raise ValidationFailed(ENTITY, "stato", f"uno fra {', '.join(COMPANY_STATES)}")
        row, user = self._require(company_id)
        row.stato = change.stato
        if change.note is not None:
            row.note = change.note.strip() or None
        self.session.commit()
        return _to_read(row, user)

    def override(self, company_id: UUID, data: CompanyOverride, admin_id: UUID) -> CompanyRead:
        """Sets or clears any field `CompanyOverride` names, on the row or on the
        referente's linked `users` row for the three identity fields
        (`_ADMIN_IDENTITY_FIELDS`), and records the real delta as one `AdminAction`
        (REB-347), the same discipline `FreelancerService.override` keeps."""
        row, user = self._require(company_id)
        changes = supplied_changes(data)
        if not changes:
            return _to_read(row, user)
        identity_changes = {k: v for k, v in changes.items() if k in _ADMIN_IDENTITY_FIELDS}
        row_changes = {k: v for k, v in changes.items() if k not in _ADMIN_IDENTITY_FIELDS}
        reject_cleared_columns(ENTITY, User, identity_changes)
        reject_cleared_columns(ENTITY, Company, row_changes)
        # A partial override may touch only one side of the together-rule
        # (`ck_companies_giorni_presenza_together`): computed and refused here, on the
        # prospective values and before anything is mutated, rather than left for the
        # commit below to hit the database's own `CHECK` and surface as a raw
        # `IntegrityError`.
        final_remoto = row_changes.get("remoto", row.remoto)
        final_giorni_presenza = row_changes.get("giorni_presenza", row.giorni_presenza)
        if (final_remoto == "ibrido") != (final_giorni_presenza is not None):
            raise ValidationFailed(
                ENTITY, "giorni_presenza", "va indicato solo, e sempre, per il lavoro ibrido"
            )
        before = {
            **{field: getattr(user, field) for field in identity_changes},
            **{field: getattr(row, field) for field in row_changes},
        }
        for field, value in identity_changes.items():
            setattr(user, field, value)
        for field, value in row_changes.items():
            setattr(row, field, value)
        self.session.commit()
        after = {
            **{field: getattr(user, field) for field in identity_changes},
            **{field: getattr(row, field) for field in row_changes},
        }
        delta = field_changes(before, after)
        if delta:
            AdminActionService(self.session).record(ENTITY, row.id, "overridden", admin_id, delta)
        return _to_read(row, user)

    def soft_delete(self, company_id: UUID, admin_id: UUID) -> CompanyRead:
        """Sets `deleted_at`. No hard delete anywhere in this path, same reasoning as
        `FreelancerService.soft_delete`: an admin's own mistake must be reversible."""
        row, user = self._require(company_id)
        row.deleted_at = utcnow()
        self.session.commit()
        AdminActionService(self.session).record(ENTITY, row.id, "deleted", admin_id, {})
        return _to_read(row, user)

    def restore(self, company_id: UUID, admin_id: UUID) -> CompanyRead:
        """A no-op, not an error, on a request that is not deleted -- same idempotent
        contract as `FreelancerService.restore`."""
        row, user = self._require(company_id, include_deleted=True)
        if row.deleted_at is not None:
            row.deleted_at = None
            self.session.commit()
            AdminActionService(self.session).record(ENTITY, row.id, "restored", admin_id, {})
        return _to_read(row, user)

    def audit_timeline(
        self, company_id: UUID, limit: int = TIMELINE_LIMIT_DEFAULT
    ) -> list[AdminActionRead]:
        """Who overrode, deleted or restored this request, and when. Works on a deleted
        request too, for the same reason `FreelancerService.audit_timeline` does."""
        self._require(company_id, include_deleted=True)
        return AdminActionService(self.session).timeline(ENTITY, company_id, limit)

    def revert(self, company_id: UUID, action_id: UUID, admin_id: UUID) -> CompanyRead:
        """Restores a field an earlier `overridden` action changed to its
        `payload["before"]` value, the same mechanics as
        `FreelancerService.revert`."""
        row, _user = self._require(company_id)
        action = AdminActionService(self.session).require(action_id)
        if action.entity_type != ENTITY or action.entity_id != row.id:
            raise NotFound(ENTITY, action_id)
        if action.kind != "overridden":
            raise ValidationFailed(
                ENTITY, "action_id", "si può ripristinare solo una modifica di campo"
            )
        before = action.payload.get("before", {})
        restored = {
            field: coerce_stored_value(
                User if field in _ADMIN_IDENTITY_FIELDS else Company, field, value
            )
            for field, value in before.items()
        }
        return self.override(company_id, CompanyOverride(**restored), admin_id)

    def _require(self, company_id: UUID, *, include_deleted: bool = False) -> tuple[Company, User]:
        result = self.session.execute(
            select(Company, User)
            .join(User, User.id == Company.user_id)
            .where(Company.id == company_id)
        ).first()
        if result is None or (result[0].deleted_at is not None and not include_deleted):
            raise NotFound(ENTITY, company_id)
        return result[0], result[1]
