"""The talent's answer: `yes`, and the `si` that mails sent before the rename still carry."""

import pytest
from pydantic import ValidationError

from rebase_core.team_schemas import TeamAvailabilityAnswer


@pytest.mark.parametrize(("sent", "read"), [("yes", "yes"), ("si", "yes"), ("no", "no")])
def test_the_answer_reads_the_old_and_the_new_word(sent: str, read: str) -> None:
    assert TeamAvailabilityAnswer(t="abc", answer=sent).answer == read  # type: ignore[arg-type]


def test_any_other_word_is_refused() -> None:
    with pytest.raises(ValidationError):
        TeamAvailabilityAnswer(t="abc", answer="forse")  # type: ignore[arg-type]
