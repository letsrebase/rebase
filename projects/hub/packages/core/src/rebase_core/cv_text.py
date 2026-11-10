"""The text of a stored CV, as `pypdf` reads it, for an agent that has to match a card
against a request without leaving the MCP (ORB-206).

Extraction happens on read, not at upload: a CV weighs five megabytes at most and there
are a few dozen, so the work is cheap, and a text computed on the way out can never fall
behind the bytes it came from. No OCR: a scanned CV answers empty text and says so
through `pages`, so the caller can tell a scan from a card without a file.

The parse runs in a child process (`cv_text_worker`), never in the caller's (REB-889): the
file is whatever a stranger uploaded, and `pypdf` has no time or memory bound of its own.
The parent waits `PARSE_TIMEOUT_SECONDS` and then kills the child; the child has an
address-space limit, and applies the page and character ceilings while it reads. The
choice over a thread with a deadline: a thread cannot be stopped, and one stuck in the
parser holds the interpreter's lock for everybody else's requests. The cost is a fresh
interpreter per CV (a fraction of a second), paid on a path that is a few dozen CVs, after
the response or on an agent's request.

Every failure of the PDF itself is the same answer, empty text with the pages it managed
to count, never an exception: an encrypted file, a truncated one, a `%PDF` header on
something that is not a PDF. A parse that overruns its deadline or ends with an error
status is the same answer: the card writer then fails the CV as one with no readable text,
and does not try the same bytes again. A parser that cannot start, or is killed by a signal,
says nothing about the file: that is `CvTextUnavailable`, an outage the card writer retries
(a loaded host can refuse a process, and a CV marked unreadable for that would stay so until
its owner uploaded it again). The shape follows the one PigroCRM measured for its
documents; it is written again here because the hub imports nothing from the CRM.
"""

import json
import logging
import subprocess
import sys

from pydantic import BaseModel

from rebase_core.cv_text_worker import MAX_CHARS, MAX_PAGES, TRUNCATION_MARKER
from rebase_core.errors import CvTextUnavailable

__all__ = [
    "MAX_CHARS",
    "MAX_PAGES",
    "PARSE_TIMEOUT_SECONDS",
    "TRUNCATION_MARKER",
    "UNAVAILABLE",
    "CvText",
    "extract_text",
]

logger = logging.getLogger(__name__)

# Twenty pages of a normal CV read in a second or two; a file that takes twenty seconds is
# not one. The card is written after the response and the MCP tool is an agent's call, so
# the deadline can be generous without a visitor waiting on it.
PARSE_TIMEOUT_SECONDS = 20.0
UNAVAILABLE = "Non riesco a leggere il CV adesso: riprova tra poco."


class CvText(BaseModel):
    """What `read_freelancer_cv` answers: the text, page by page joined with a newline,
    with the count of pages the file has and whether the text was cut short, by the page
    ceiling or the character one."""

    filename: str
    pages: int
    text: str
    truncated: bool


def extract_text(
    content: bytes, filename: str, *, timeout: float = PARSE_TIMEOUT_SECONDS
) -> CvText:
    unreadable = CvText(filename=filename, pages=0, text="", truncated=False)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "rebase_core.cv_text_worker"],
            input=content,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:  # `run` has killed the child
        logger.warning("CV text not read: the parse took longer than %s seconds", timeout)
        return unreadable
    except OSError as error:
        logger.warning("CV text not read: the parser did not start (%s)", type(error).__name__)
        raise CvTextUnavailable(UNAVAILABLE) from None
    if done.returncode < 0:  # killed by a signal: the host's doing, not the file's
        logger.warning("CV text not read: the parser was killed (signal %s)", -done.returncode)
        raise CvTextUnavailable(UNAVAILABLE)
    if done.returncode != 0:
        logger.warning("CV text not read: the parser ended with status %s", done.returncode)
        return unreadable
    try:
        read = json.loads(done.stdout)
        return CvText(
            filename=filename,
            pages=read["pages"],
            text=read["text"],
            truncated=read["truncated"],
        )
    except (ValueError, KeyError, TypeError):
        logger.warning("CV text not read: the parser's answer was not the shape")
        return unreadable
