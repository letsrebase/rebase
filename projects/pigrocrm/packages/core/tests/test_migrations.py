


def _forecast_key_state(url: str) -> tuple[bool, bool, str]:
    """Whether `due_items` has `forecast_key` and its partial unique index, and the text of the
    check on the rule's columns, with the whitespace and parentheses taken out."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            column = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                    "table_name = 'due_items' AND column_name = 'forecast_key'"
                )
            ).scalar_one()
            index = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM pg_indexes WHERE "
                    "indexname = 'uq_due_items_forecast_key'"
                )
            ).scalar_one()
            definition = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_due_items_rule_columns'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    flat = "".join(definition.split()).replace("(", "").replace(")", "")
    return bool(column), bool(index), flat


def test_0090_adds_the_forecast_key_and_lets_a_waiting_item_carry_its_offset(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1003: one nullable column with its partial unique index, and a relaxed check: only
    the waiting `after_settlement` clause stops requiring a zero offset."""
    waiting_strict = "rule_not_afterISNOTNULLANDrule_offset_days=0"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0089")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (False, False)
        assert waiting_strict in check
        upgrade(config, "0090")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (True, True)
        assert waiting_strict not in check
        downgrade(config, "0089")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (False, False)
        assert waiting_strict in check
        upgrade(config, "0090")
        assert _forecast_key_state(url)[:2] == (True, True)
