"""The text of a CV, as `read_freelancer_cv` answers it (ORB-206)."""

import subprocess
import time

import pytest

from rebase_core.cv_text import MAX_CHARS, MAX_PAGES, TRUNCATION_MARKER, CvText, extract_text
from rebase_core.errors import CvTextUnavailable
from rebase_core.perks import guide_bytes


def test_a_real_pdf_answers_its_pages_and_its_text() -> None:
    read = extract_text(guide_bytes(), "guida.pdf")
    assert (read.filename, read.pages, read.truncated) == ("guida.pdf", 6, False)
    assert "prima fattura" in read.text
    assert TRUNCATION_MARKER not in read.text


def test_a_pdf_with_no_readable_text_answers_empty_text_and_no_truncation() -> None:
    # A header on nothing: pypdf cannot count a page in it, and that is not a document
    # cut short, it is a document with nothing to read.
    read = extract_text(b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n", "vuoto.pdf")
    assert read == CvText(filename="vuoto.pdf", pages=0, text="", truncated=False)


def test_bytes_that_are_not_a_pdf_answer_the_same_and_never_raise() -> None:
    assert extract_text(b"not a pdf at all", "x.pdf").text == ""
    empty = CvText(filename="x.pdf", pages=0, text="", truncated=False)
    assert extract_text(b"", "x.pdf") == empty


def test_the_ceilings_are_the_ones_the_tool_documents() -> None:
    assert MAX_PAGES == 20
    assert MAX_CHARS == 200_000


def test_a_parse_that_overruns_its_deadline_is_refused_cleanly_and_stops() -> None:
    """The parse runs in a child the parent kills at the deadline (REB-889): a deadline no
    interpreter can meet, so the child is still starting when it falls, is the same
    answer as a file with nothing to read, returned at once."""
    started = time.monotonic()

    read = extract_text(guide_bytes(), "guida.pdf", timeout=0.001)

    assert read == CvText(filename="guida.pdf", pages=0, text="", truncated=False)
    assert time.monotonic() - started < 5


def test_a_parser_that_ends_with_an_error_is_the_same_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that ends with an error status (the parser's own exit on a file it cannot
    read) has no text to give, and the caller is never raised at."""

    def fails(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fails)

    assert extract_text(guide_bytes(), "guida.pdf").text == ""


def test_a_parser_killed_by_a_signal_is_an_outage_not_an_unreadable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signal says nothing about the file (the host ran out of memory, say): it must not
    read as «no text», or the card writer would never try these bytes again."""

    def killed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=-9, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", killed)

    with pytest.raises(CvTextUnavailable):
        extract_text(guide_bytes(), "guida.pdf")


def test_a_parser_that_cannot_start_is_an_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise OSError("too many open files")

    monkeypatch.setattr(subprocess, "run", refused)

    with pytest.raises(CvTextUnavailable):
        extract_text(guide_bytes(), "guida.pdf")
