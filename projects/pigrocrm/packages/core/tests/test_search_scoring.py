"""Spec §8.5's formula, pinned against real Postgres.

    field_score = 1.00  if lower(field) = lower(term)
                = 0.80  if lower(field) starts with lower(term)
                = 0.60 × similarity(field, term)
    field_weight = 1.00  identifier / code field
                 = 0.90  email
                 = 0.80  memo
    row_score    = max(field_weight × field_score)

Exact and prefix scores are asserted to the cent, because they are literals. The
substring score is asserted only by *ordering* and by its bounds: the precise value of
`similarity()` is a pg_trgm implementation detail, and pinning it would make a Postgres
upgrade look like a defect in this file.

Every assertion below goes through a real query. That is not incidental: the score being
one SQL expression is the task, because a score computed in Python over fetched rows
cannot be ordered or paged by the database and turns the search into a scan of the whole
trigram tail. `test_the_ranking_happens_in_the_database` is the assertion that says so
directly -- it never fetches a score at all, it asks Postgres to sort by it and take two.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.search.scoring import (
    SCORE_EXACT,
    SCORE_FLOOR,
    SCORE_PREFIX,
    SCORE_SCALE,
    SCORE_SUBSTRING_FACTOR,
    WEIGHT_EMAIL,
    WEIGHT_IDENTIFYING,
    ScoredField,
    best_field,
    field_score,
    matches_any,
    row_score,
)

_NAME = ScoredField(name="legal_name", column=Customer.legal_name, weight=WEIGHT_IDENTIFYING)
_EMAIL = ScoredField(name="email", column=Customer.email, weight=WEIGHT_EMAIL)
_FIELDS = (_NAME, _EMAIL)


def _add(session: Session, legal_name: str, email: str | None = None) -> Customer:
    row = Customer(legal_name=legal_name, email=email, country="IT", custom_fields={})
    session.add(row)
    session.flush()
    return row


def _score(
    session: Session,
    row: Customer,
    term: str,
    fields: Sequence[ScoredField] = _FIELDS,
) -> Decimal:
    value = session.scalar(select(row_score(fields, term)).where(Customer.id == row.id))
    # Never `None`: a NULL row score would sort *ahead* of every real hit under
    # `ORDER BY … DESC` in Postgres, so "did not match" has to be a number.
    assert value is not None
    return value


def test_an_exact_case_insensitive_match_scores_one(db_session: Session) -> None:
    row = _add(db_session, "Rossi Ingegneria Srl")
    assert _score(db_session, row, "rossi ingegneria srl") == SCORE_EXACT


def test_a_prefix_match_scores_zero_point_eight(db_session: Session) -> None:
    row = _add(db_session, "Rossi Ingegneria Srl")
    assert _score(db_session, row, "Rossi") == SCORE_PREFIX


def test_a_mid_word_match_scores_below_a_prefix_match(db_session: Session) -> None:
    """§16 criterion 4's second sentence: a prefix of a company name ranks above a
    match in the middle of a word."""
    prefix_row = _add(db_session, "Ingegneria Rossi Srl")
    middle_row = _add(db_session, "Grande Ingegneria Lombarda Srl")

    prefix = _score(db_session, prefix_row, "Ingegn")
    middle = _score(db_session, middle_row, "Ingegn")

    assert prefix == SCORE_PREFIX
    assert Decimal("0") < middle < prefix


def test_an_email_match_is_weighted_below_an_identifying_match(db_session: Session) -> None:
    by_name = _add(db_session, "Vulcano Srl")
    by_email = _add(db_session, "Altra Societa Srl", email="vulcano@example.it")

    assert _score(db_session, by_name, "Vulcano") == SCORE_PREFIX
    # 0.90 × 0.80 = 0.72: same field score, lower weight.
    assert _score(db_session, by_email, "Vulcano") == Decimal("0.7200")


def test_the_row_score_is_the_maximum_and_not_the_sum(db_session: Session) -> None:
    """Summing would let two mediocre matches outrank one exact one, and would make a
    row with more populated columns rank higher for no reason a user could explain."""
    both = _add(db_session, "Vulcano Srl", email="vulcano@example.it")
    assert _score(db_session, both, "Vulcano") == SCORE_PREFIX
    # Named explicitly, because "max, not sum" is only a claim if the sum is a different
    # number: 0.80 + 0.72 would have been 1.52, above even an exact match.
    assert _score(db_session, both, "Vulcano") < SCORE_PREFIX + Decimal("0.7200")


def test_best_field_names_the_column_that_produced_the_score(db_session: Session) -> None:
    by_email = _add(db_session, "Altra Societa Srl", email="vulcano@example.it")
    name = db_session.scalar(
        select(best_field(_FIELDS, "Vulcano")).where(Customer.id == by_email.id)
    )
    assert name == "email"


def test_best_field_resolves_a_tie_to_the_earlier_field(db_session: Session) -> None:
    """Criterion 4 asks for byte-identical responses across runs, and `field` is in the
    response. Two fields of equal weight matching equally well is not a rare case -- a
    person whose surname is also in their email address produces it -- so the tie has to
    resolve by declaration order rather than by whichever `CASE` arm the planner reaches
    first."""
    equal_weight = (
        _NAME,
        ScoredField(name="email", column=Customer.email, weight=WEIGHT_IDENTIFYING),
    )
    row = _add(db_session, "Vulcano Srl", email="vulcano@example.it")

    scores = db_session.execute(
        select(*(field_score(field, "Vulcano") for field in equal_weight)).where(
            Customer.id == row.id
        )
    ).one()
    assert scores[0] == scores[1], "the fixture no longer produces a tie"

    name = db_session.scalar(
        select(best_field(equal_weight, "Vulcano")).where(Customer.id == row.id)
    )
    assert name == "legal_name"


def test_matches_any_is_true_only_for_a_row_that_contains_the_term(
    db_session: Session,
) -> None:
    hit = _add(db_session, "Rossi Ingegneria Srl")
    # No email at all on the miss: `NULL ILIKE '%x%'` is NULL, and `false OR NULL` is
    # NULL, which is not `true` -- so a row with nothing but null columns must be
    # filtered out rather than silently included.
    miss = _add(db_session, "Quadrifoglio Logistica Spa")

    found = set(db_session.scalars(select(Customer.id).where(matches_any(_FIELDS, "ingegn"))).all())
    assert hit.id in found
    assert miss.id not in found


def test_matches_any_leaves_the_column_unwrapped_and_keeps_its_escape(
    db_session: Session,
) -> None:
    """The filter is what the nine partial `*_trgm` indexes of task A2 serve, and both
    halves of that are structural.

    A `lower(column)` in the predicate would make every one of those indexes unusable
    (spec §8.2) -- `similarity()` already normalises to lower case internally, which is
    why none of the index expressions wraps its column either. And the `ESCAPE` clause
    stays: A2 measured the same plan node for node with and without it, so the Global
    Constraint is unchanged and this is the assertion that keeps someone from "tidying"
    it away on the theory that it costs the index something.
    """
    rendered = str(
        matches_any(_FIELDS, "Rossi").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "ILIKE" in rendered
    assert "lower(" not in rendered
    assert "ESCAPE" in rendered


def test_a_null_column_never_wins_and_never_raises(db_session: Session) -> None:
    """`customers.email` is nullable. `GREATEST` in Postgres ignores NULLs, but
    `similarity(NULL, 'x')` is NULL and a CASE that returned NULL for every field would
    make the row score NULL -- which would sort unpredictably rather than not matching."""
    row = _add(db_session, "Rossi Ingegneria Srl", email=None)
    assert _score(db_session, row, "Rossi") == SCORE_PREFIX

    # And the row that matches on nothing at all scores zero, not NULL.
    nothing = _add(db_session, "Quadrifoglio Logistica Spa", email=None)
    assert _score(db_session, nothing, "zzqqxxww") == Decimal("0")

    # Scored against the nullable column *alone*, which is the only shape that reaches
    # the `coalesce`: with `customers.legal_name` in the list, `GREATEST` hides a
    # NULL behind the non-null field's own zero and the miss branch is never observed.
    # Some entity's searched columns are all nullable -- an invoice's `memo` is --
    # and that row must score zero rather than heading the palette.
    assert _score(db_session, row, "zzqqxxww", (_EMAIL,)) == Decimal("0")


def test_the_score_is_a_decimal_at_the_declared_scale(db_session: Session) -> None:
    """`similarity()` returns `real`. Left uncast it would arrive as a Python float,
    render as `0.6000000238418579` in JSON and break criterion 4's byte-identical
    requirement -- besides violating the project's no-float rule. The `::numeric` cast
    and the `round(…, SCORE_SCALE)` are both observable here: the value comes back as a
    `Decimal` whose exponent is exactly the declared scale.
    """
    row = _add(db_session, "Grande Ingegneria Lombarda Srl")
    value = _score(db_session, row, "Ingegn")

    assert isinstance(value, Decimal)
    assert not isinstance(value, float)
    assert value.as_tuple().exponent == -SCORE_SCALE

    # Deterministic: the same input scores the same twice, which is what criterion 4
    # needs from a `real` that has been cast rather than rounded away.
    assert _score(db_session, row, "Ingegn") == value


def test_the_ranking_happens_in_the_database(db_session: Session) -> None:
    """The point of the whole module.

    A score computed in Python over fetched rows cannot be ordered or paged by the
    database: the search would have to fetch the entire trigram tail to rank it and then
    throw almost all of it away, which is the sequential scan task A9 exists to forbid.
    Here Postgres does the sorting and the discarding -- `ORDER BY <score> DESC LIMIT 2`
    -- and no score ever crosses the wire.
    """
    exact = _add(db_session, "Ingegn")
    prefix = _add(db_session, "Ingegneria Rossi Srl")
    _middle = _add(db_session, "Grande Ingegneria Lombarda Srl")

    top_two = list(
        db_session.scalars(
            select(Customer.id)
            .where(matches_any(_FIELDS, "Ingegn"))
            .order_by(row_score(_FIELDS, "Ingegn").desc(), Customer.id)
            .limit(2)
        ).all()
    )

    assert top_two == [exact.id, prefix.id]


def test_a_like_metacharacter_in_the_term_is_escaped_in_the_prefix_test(
    db_session: Session,
) -> None:
    """The prefix arm builds a LIKE pattern, so it needs `escape_like` just as the
    substring filter does -- otherwise searching `Rossi_` prefix-matches `RossiX`."""
    exact = _add(db_session, "Rossi_Ingegneria")
    other = _add(db_session, "RossiXIngegneria")

    assert _score(db_session, exact, "Rossi_") == SCORE_PREFIX
    assert _score(db_session, other, "Rossi_") < SCORE_PREFIX


def test_the_floor_admits_a_whole_word_match_and_excludes_pure_noise(
    db_session: Session,
) -> None:
    """Task A8 applies `SCORE_FLOOR`; this is what makes the value defensible rather than
    decorative.

    A constant asserted equal to its own literal would pass against any number at all. So
    the floor is pinned by what it has to separate: a whole word found inside a company
    name must survive it, and a row that shares no trigram at all must not. Both figures
    come from `word_similarity()` on real Postgres -- 0.6000 and 0.0000 as measured here
    -- so if a pg_trgm upgrade moves them enough to cross the floor, this test says so
    instead of the palette quietly losing results.
    """
    real_match = _add(db_session, "Grande Ingegneria Lombarda Srl")
    noise = _add(db_session, "Quadrifoglio Logistica Spa")

    assert _score(db_session, real_match, "Ingegneria") >= SCORE_FLOOR
    assert _score(db_session, noise, "Ingegneria") == Decimal("0")

    # The floor is a fraction of the substring arm's own ceiling, not of the exact one:
    # it only ever discards substring matches, since a prefix scores 0.80 x 0.80 = 0.64
    # even on the least-weighted field.
    assert Decimal("0") < SCORE_FLOOR < SCORE_SUBSTRING_FACTOR


def test_a_short_fragment_of_a_middle_word_survives_the_floor(db_session: Session) -> None:
    """The measurement that chose `word_similarity()` over §8.5's literal
    `similarity(campo, termine)`, kept as an executable record of the decision.

    `similarity()` compares whole strings, so it charges a field for the text it carries
    besides the match: it scores this pair at 0.1875, putting §8.5's third rung at 0.1125
    -- under the 0.20 floor -- and it scores the fragment the plan advertises by name,
    "34567" inside the P.IVA 01234567890, at 0.2000 for a rung of 0.1200, also under the
    floor. The plan promises that exact search in three places (§17, the
    `search_everything` MCP docstring and an end-to-end test), so the formula as written,
    the floor and the promise cannot all three hold. `word_similarity()` is the pg_trgm
    function for a term matched against an *extent* of a string, and with it every literal
    containment clears the floor while every non-containment still scores exactly zero.

    If this ever goes red, the palette has silently stopped finding customers by a
    fragment of their VAT number, which is 6A's stated reason to exist.
    """
    long_name = _add(db_session, "Grande Ingegneria Lombarda Srl")
    vat_like = _add(db_session, "01234567890")

    assert _score(db_session, long_name, "Ingegn") > SCORE_FLOOR
    assert _score(db_session, vat_like, "34567") > SCORE_FLOOR

    # Still below a prefix match on the same term, which is what §16 criterion 4 asks
    # for: the ladder is unchanged, only the measure on its third rung.
    assert _score(db_session, long_name, "Ingegn") < SCORE_PREFIX
