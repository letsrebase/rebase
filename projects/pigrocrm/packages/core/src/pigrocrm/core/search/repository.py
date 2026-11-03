"""One branch per searched entity, and nothing that crosses two of them.

The shape of every branch is the same and the repetition is deliberate: a generic
"search any model" helper would need the label rule, the subtitle rule, the field set and
the weight set as parameters, which is five dictionaries keyed by entity plus a dispatch --
strictly more code than five explicit methods, and unreadable at the point where a plan
goes wrong.

`label` and `sottotitolo` are built **here**, from the entity's own columns. Not in
the browser: composing "nome cognome" client-side is business logic in the frontend, and
the whole point of the two-adapter architecture is that an MCP agent sees the same label a
human does.

Every field of a row is scored **once**, in a subquery (`_matches`): `matches_any` narrows
the rows there through the trigram index, the subquery's `OFFSET 0` keeps Postgres from
folding it back into the outer query, where the score would be evaluated again for the
floor, for the order and for the column that names the best field, and the floor, the
order and the page are applied outside it over the computed columns.

The count and the hits are two queries over **one** set of matches, built once in
`_matches` and used by both. A card and its drill-through are the same calculation
(Global Constraints); two hand-copied `where` clauses are how they stop being one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, Subquery, func, literal, select
from sqlalchemy.orm import Session, aliased

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.search.schemas import (
    COUNT_CEILING,
    SearchEntity,
    SearchGroup,
    SearchHit,
    parse_fiscal_number,
)
from pigrocrm.core.search.scoring import (
    SCORE_EXACT,
    SCORE_FLOOR,
    SCORE_SCALE,
    WEIGHT_CODE,
    WEIGHT_EMAIL,
    WEIGHT_IDENTIFYING,
    WEIGHT_MEMO,
    ScoredField,
    combine_scores,
    field_scores,
    matches_any,
    pick_field,
)
from pigrocrm.core.tasks.models import Task

# Spec §8.1's searched surface, one tuple per entity. Declaration order is load-bearing
# twice over: `best_field` resolves a tie to the earlier field, so the identifying column
# comes first, and `matches_any` builds its `OR` in this order.
CUSTOMER_FIELDS: tuple[ScoredField, ...] = (
    ScoredField("legal_name", Customer.legal_name, WEIGHT_IDENTIFYING),
    ScoredField("vat_number", Customer.vat_number, WEIGHT_CODE),
    ScoredField("tax_code", Customer.tax_code, WEIGHT_CODE),
    ScoredField("email", Customer.email, WEIGHT_EMAIL),
)
PERSON_FIELDS: tuple[ScoredField, ...] = (
    ScoredField("last_name", Person.last_name, WEIGHT_IDENTIFYING),
    ScoredField("nome", Person.nome, WEIGHT_IDENTIFYING),
    ScoredField("email", Person.email, WEIGHT_EMAIL),
)
DEAL_FIELDS: tuple[ScoredField, ...] = (ScoredField("nome", Deal.nome, WEIGHT_IDENTIFYING),)
DOCUMENT_FIELDS: tuple[ScoredField, ...] = (
    ScoredField("title", Document.title, WEIGHT_IDENTIFYING),
)
# One field, and `number` is deliberately not a second one: a fiscal number is matched by
# equality, not by trigram, and a `ScoredField` is by definition the trigram surface. The
# equality path is `_invoices_by_number` below.
INVOICE_FIELDS: tuple[ScoredField, ...] = (ScoredField("memo", Invoice.memo, WEIGHT_MEMO),)

# §10.9 of slice 7 and REB-723: the sixth branch. The title is what a person typed to name
# the task and weighs like any identifying column; the notes are prose around it and weigh
# like an invoice's `memo`, so a task whose *title* has the word is above one that only
# mentions it.
TASK_FIELDS: tuple[ScoredField, ...] = (
    ScoredField("title", Task.title, WEIGHT_IDENTIFYING),
    ScoredField("note", Task.note, WEIGHT_MEMO),
)

# The six models this repository searches all carry `PrimaryKeyMixin`, `TimestampMixin`
# and `SoftDeleteMixin`, which is what lets the shared plumbing name `deleted_at`,
# `updated_at` and `id` without a per-entity accessor. `Any` rather than a Protocol: the
# alternative is a structural type that restates three mixins this package does not own.
_Model = Any


# The clauses that narrow a search to one azienda (REB-623): empty for the search over
# every azienda, the model's own `legal_entity_id` otherwise.
Scope = tuple[ColumnElement[bool], ...]


def _own(model: Any, legal_entity_id: UUID | None) -> Scope:
    return () if legal_entity_id is None else (model.legal_entity_id == legal_entity_id,)


@dataclass(frozen=True)
class _Matches:
    """The rows one search finds. `rows` is the entity's columns with one score column per
    field, `score` the row's score over them and `keep` the floor."""

    rows: Subquery
    scores: Sequence[ColumnElement[Decimal]]
    score: ColumnElement[Decimal]

    @property
    def keep(self) -> ColumnElement[bool]:
        return self.score >= SCORE_FLOOR


class SearchRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- shared plumbing -------------------------------------------------------

    def _matches(
        self,
        model: _Model,
        fields: Sequence[ScoredField],
        term: str,
        scope: Scope = (),
    ) -> _Matches:
        """What a search result *is*, written once, with every field scored once.

        Three clauses, and each is load-bearing. `deleted_at IS NULL` because a soft
        delete is a delete as far as a reader is concerned. `matches_any` because it is
        the indexable half -- the one the nine partial trigram indexes serve. The floor
        (`_Matches.keep`, applied by the caller) because the tail of a trigram match is
        noise, and a palette that shows noise teaches the user to ignore the palette.
        """
        # `scope` is the azienda's own clause when a search is narrowed to one (REB-623):
        # a clause of the same set of matches, so the page and the count agree on it.
        rows = (
            select(model, *field_scores(fields, term))
            .where(model.deleted_at.is_(None), matches_any(fields, term), *scope)
            # The fence: a subquery with an `OFFSET` is not pulled up into its parent.
            .offset(0)
            .subquery("matched")
        )
        scores = [rows.c[f"score_{field.name}"] for field in fields]
        return _Matches(rows, scores, combine_scores(scores))

    def _count(
        self, model: _Model, fields: Sequence[ScoredField], term: str, scope: Scope = ()
    ) -> tuple[int, bool]:
        """Exact up to COUNT_CEILING, then declared as a minimum.

        `count(*)` over a subquery with `LIMIT ceiling + 1`: the database stops reading
        once it has 201 rows, so the cost is bounded no matter how many rows match, and
        the answer is exact whenever exactness is what is being shown.
        """
        matches = self._matches(model, fields, term, scope)
        inner = (
            select(literal(1))
            .select_from(matches.rows)
            .where(matches.keep)
            .limit(COUNT_CEILING + 1)
            .subquery()
        )
        found = self.session.scalar(select(func.count()).select_from(inner)) or 0
        if found > COUNT_CEILING:
            return COUNT_CEILING, True
        return found, False

    def _scored(
        self,
        model: _Model,
        fields: Sequence[ScoredField],
        term: str,
        limit: int,
        scope: Scope = (),
    ) -> Select[Any, Decimal, str]:
        """`punteggio DESC, updated_at DESC, id DESC`, limited.

        The third key exists because the order must be **total**: without it two runs over
        the same data can return the same set in a different order, and §16 criterion 4
        checks twenty runs for a byte-identical response. The second key is §8.5's own --
        at equal score, what was touched most recently is more likely what is wanted.
        """
        matches = self._matches(model, fields, term, scope)
        found = aliased(model, matches.rows)
        return (
            select(found, matches.score, pick_field(fields, matches.scores))
            .where(matches.keep)
            .order_by(
                matches.score.desc(),
                found.updated_at.desc(),
                found.id.desc(),
            )
            .limit(limit)
        )

    def _group(
        self,
        entity: SearchEntity,
        model: _Model,
        fields: Sequence[ScoredField],
        term: str,
        limit: int,
        label: Callable[[Any], str],
        subtitle: Callable[[Any], str | None],
        scope: Scope = (),
    ) -> SearchGroup:
        rows = self.session.execute(self._scored(model, fields, term, limit, scope)).all()
        total, is_minimum = self._count(model, fields, term, scope)
        hits = [
            SearchHit(
                entity=entity,
                id=row[0].id,
                label=label(row[0]),
                subtitle=subtitle(row[0]),
                score=row[1],
                field=row[2],
            )
            for row in rows
        ]
        return SearchGroup(entity=entity, hits=hits, total=total, total_and_a_minimum=is_minimum)

    # -- one branch per entity -------------------------------------------------

    def customers(self, term: str, limit: int, legal_entity_id: UUID | None = None) -> SearchGroup:
        return self._group(
            "customer",
            Customer,
            CUSTOMER_FIELDS,
            term,
            limit,
            label=lambda row: row.legal_name,
            subtitle=lambda row: row.vat_number,
            scope=_own(Customer, legal_entity_id),
        )

    def people(self, term: str, limit: int, legal_entity_id: UUID | None = None) -> SearchGroup:
        # `last_name` is nullable, so the label is joined from the parts that exist rather
        # than formatted with a placeholder: "Ludovica" and not "Ludovica None".
        # A person has no azienda of their own: narrowed through their customer, so a
        # contact with no customer answers only the search over every azienda.
        scope: Scope = ()
        if legal_entity_id is not None:
            scope = (
                Person.customer_id.in_(
                    select(Customer.id).where(Customer.legal_entity_id == legal_entity_id)
                ),
            )
        return self._group(
            "person",
            Person,
            PERSON_FIELDS,
            term,
            limit,
            label=lambda row: " ".join(part for part in (row.nome, row.last_name) if part),
            subtitle=lambda row: row.email,
            scope=scope,
        )

    def deals(self, term: str, limit: int, legal_entity_id: UUID | None = None) -> SearchGroup:
        group = self._group(
            "deal",
            Deal,
            DEAL_FIELDS,
            term,
            limit,
            label=lambda row: row.nome,
            subtitle=lambda row: None,
            scope=_own(Deal, legal_entity_id),
        )
        return SearchGroup(
            entity=group.entity,
            hits=self._with_customer_names(Deal, group.hits),
            total=group.total,
            total_and_a_minimum=group.total_and_a_minimum,
        )

    def _with_customer_names(self, model: _Model, hits: list[SearchHit]) -> list[SearchHit]:
        """One extra lookup, after the limit, for at most `limit` rows.

        Resolving the customer name inside the scored query would mean a join evaluated
        over every trigram match rather than over the five rows that survive. A `COUNT`
        may cross a join and a `SUM` may not (spec §3); this is neither -- it is a label
        lookup, and it is placed after `LIMIT` so its cost is bounded by the page.

        `model` is a parameter rather than this method existing twice: `deals` and
        `invoices` both hang a customer name off a `customer_id` and the query is the same
        query, so a second copy would be two places for the "after the limit" property to
        be lost from. It stays one method because there is exactly one rule -- join the
        owning customer for the rows already chosen -- and not because both models happen
        to have the column.
        """
        if not hits:
            return hits
        row_ids = [hit.id for hit in hits]
        pairs = self.session.execute(
            select(model.id, Customer.legal_name)
            .join(Customer, Customer.id == model.customer_id)
            .where(model.id.in_(row_ids))
        ).all()
        names: dict[UUID, str] = {row[0]: row[1] for row in pairs}
        return [hit.model_copy(update={"sottotitolo": names.get(hit.id)}) for hit in hits]

    def documents(self, term: str, limit: int, legal_entity_id: UUID | None = None) -> SearchGroup:
        return self._group(
            "document",
            Document,
            DOCUMENT_FIELDS,
            term,
            limit,
            label=lambda row: row.title,
            subtitle=lambda row: row.type,
            scope=_own(Document, legal_entity_id),
        )

    def invoices(self, term: str, limit: int, legal_entity_id: UUID | None = None) -> SearchGroup:
        """§8.1's fifth branch: `memo` by trigram, `(anno, numero)` by equality.

        The branch `SearchEntity` has promised since Task A7 and nothing delivered until
        now: searching an invoice number answered «Nessun risultato» when the truth was
        "invoices were not looked at", which is the silent partial result §8.6 exists to
        forbid.

        The two paths are **exclusive**. When the term parses as a fiscal number the
        equality path runs alone -- served by `ix_invoices_year_number`, the partial index
        slice 3 §3 already creates, so the number half needs no new index at all.
        Trigramming `123` over `memo` would return every invoice whose description
        contains 123 beside the one that *is* 123, and the row the user named would be one
        of a list rather than the answer.
        """
        fiscal = parse_fiscal_number(term)
        scope = _own(Invoice, legal_entity_id)
        if fiscal is not None:
            return self._invoices_by_number(*fiscal, limit=limit, scope=scope)
        group = self._group(
            "invoice",
            Invoice,
            INVOICE_FIELDS,
            term,
            limit,
            label=self._invoice_label,
            subtitle=lambda row: None,
            scope=scope,
        )
        return SearchGroup(
            entity=group.entity,
            hits=self._with_customer_names(Invoice, group.hits),
            total=group.total,
            total_and_a_minimum=group.total_and_a_minimum,
        )

    def attivita(self, term: str, limit: int) -> SearchGroup:
        """§10.9: an attività by its title and its notes, whatever state it is in.

        Closed ones are found too, and say so in the subtitle: a search that hid what was
        done would answer «did I ever do that» with «no». The subtitle is the state, or the
        deadline for an open one (`gg/mm/aaaa`), which is what tells two tasks of the same
        name apart on a palette row. There is no azienda clause: an attività has no azienda
        of its own (the calendar keeps them whatever azienda is selected).
        """
        return self._group(
            "attivita",
            Task,
            TASK_FIELDS,
            term,
            limit,
            label=lambda row: row.title,
            subtitle=self._task_subtitle,
        )

    @staticmethod
    def _task_subtitle(row: Any) -> str | None:
        if row.status != "open":
            return str(row.status)
        if row.due_date is None:
            return "senza scadenza"
        return str(row.due_date.strftime("%d/%m/%Y"))

    @staticmethod
    def _invoice_label(row: Any) -> str:
        """`2026/7 — causale` when numbered, `bozza — causale` when not.

        `year`/`number` are NULL until emission, which is what makes "a failed creation
        cannot burn a number" true by construction (slice 3). A draft still has to be
        findable and readable, so the state stands in for the number rather than the label
        rendering "None/None" -- and `status` is a closed set the user already sees on the
        invoice list, so it reads as a fact rather than as a placeholder.
        """
        prefix = (
            f"{row.year}/{row.number}"
            if row.year is not None and row.number is not None
            else row.status
        )
        return f"{prefix} — {row.memo}" if row.memo else prefix

    def _number_predicate(
        self, year: int | None, number: int, scope: Scope = ()
    ) -> tuple[ColumnElement[bool], ...]:
        """What a fiscal-number match *is*, written once and used by both the page and the
        count -- the same discipline `_predicate` follows for the trigram branches, and for
        the same reason: a card and its drill-through are one calculation.

        `deleted_at IS NULL` is repeated here rather than inherited: the equality path does
        not go through `_predicate`, and a soft-deleted invoice found by its number would be
        the one hole in "a soft delete is a delete as far as a reader is concerned".
        """
        clauses: tuple[ColumnElement[bool], ...] = (
            Invoice.deleted_at.is_(None),
            Invoice.number == number,
            *scope,
        )
        if year is not None:
            clauses = (*clauses, Invoice.year == year)
        return clauses

    def _invoices_by_number(
        self, year: int | None, number: int, *, limit: int, scope: Scope = ()
    ) -> SearchGroup:
        """The equality half. At most one row per year and azienda (REB-619: two aziende
        of one space each have their own `2026/1`, listed one after the other and told
        apart by the azienda's name once milestone 3 shows it), so the ceiling is never
        reached in practice -- and the bounded count is used anyway, because "in
        practice" is not a property and this way both paths report `total` by the same
        rule.

        `ORDER BY anno DESC` is §8.5's "what was touched most recently is more likely what
        is wanted", expressed in the only ordering a number has. `id DESC` closes it into a
        total order: since REB-619 two aziende of one space may share `(anno, numero)`,
        so it is what orders their rows, and it costs nothing to not depend on a
        constraint in another package for criterion 4's byte-identical guarantee.
        """
        predicate = self._number_predicate(year, number, scope)
        rows = (
            self.session.execute(
                select(Invoice)
                .where(*predicate)
                .order_by(Invoice.year.desc(), Invoice.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        inner = (
            select(literal(1))
            .select_from(Invoice)
            .where(*predicate)
            .limit(COUNT_CEILING + 1)
            .subquery()
        )
        found = self.session.scalar(select(func.count()).select_from(inner)) or 0
        hits = [
            SearchHit(
                entity="invoice",
                id=row.id,
                label=self._invoice_label(row),
                subtitle=None,
                # An exact fiscal-number match is a code match -- §8.5's own "un match su un
                # codice è voluto" -- so weight 1.00 and score 1.00, exactly what an exact
                # `vat_number` match produces. Quantized to the declared scale because
                # `SearchHit.score` is `Numeric(6, 4)` and a `Decimal("1.00")` would be
                # a different value on the wire from every other exact match in the payload.
                score=SCORE_EXACT.quantize(Decimal(1).scaleb(-SCORE_SCALE)),
                field="number",
            )
            for row in rows
        ]
        return SearchGroup(
            entity="invoice",
            hits=self._with_customer_names(Invoice, hits),
            total=min(found, COUNT_CEILING),
            total_and_a_minimum=found > COUNT_CEILING,
        )
