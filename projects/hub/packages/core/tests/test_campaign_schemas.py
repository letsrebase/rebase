"""The shapes campaigns read, where a shape itself is the rule (no database)."""

from rebase_core.campaigns.schemas import ScheduleRequest


def test_esclusi_are_plain_addresses_stripped_and_lowercased() -> None:
    """An address the list holds but `EmailStr` would reject (a legacy sign-up with two
    dots in a row) must not 422 the whole send: the unticked are compared, never mailed."""
    request = ScheduleRequest(esclusi=[" Other@Studio.IT ", "odd..legacy@studio.it"])
    assert request.esclusi == ["other@studio.it", "odd..legacy@studio.it"]
