"""The child process that reads a CV's PDF (REB-889, HCORE-12): `python -m
rebase_core.cv_text_worker`, the file's bytes on stdin, one JSON object on stdout.

`pypdf` parses what a stranger uploaded, and a crafted file can spin a CPU or grow memory
without bound. In the API process that pins a worker thread (or the whole process, when the
parser holds the interpreter) and no deadline can stop it; in a child the parent kills it at
a deadline (`cv_text.extract_text`) and an address-space limit makes a runaway allocation a
`MemoryError` here instead of a swap storm there. The page and character ceilings are this
module's, applied while the file is read, so the parent never holds more than they allow.

Imports `pypdf` only when it runs, and nothing else of ours: the child starts fast and the
parent never imports the parser.
"""

import json
import sys
from io import BytesIO

# A CV is two pages and a designer's portfolio-as-CV ten; twenty is past both, and it
# keeps a mis-uploaded book from being read to the end for nothing.
MAX_PAGES = 20
# Characters, not tokens: two hundred thousand is fifty pages of dense prose, and an
# MCP client that has to carry the answer in a context window has to stop somewhere.
MAX_CHARS = 200_000

# The child's whole address space. A 5 MB CV of two pages needs a small fraction of it;
# `pypdf` and the interpreter themselves some hundreds of megabytes of address space.
MEMORY_LIMIT_BYTES = 2 * 1024**3

TRUNCATION_MARKER = "\n\n[…] testo troncato"


def read_pdf(content: bytes) -> dict[str, object]:
    """The file's pages as `{"pagine": count, "testo": text, "troncato": cut}`. Every
    failure of the PDF itself is the same answer, empty text with the pages it managed to
    count, never an exception: an encrypted file, a truncated one, a `%PDF` header on
    something that is not a PDF. `pypdf` raises its own `PdfReadError` for some
    malformations and plain `KeyError`/`ValueError`/`struct.error` for others, and a list
    of them is a list the next release breaks, which is why the `except` is that broad."""
    from pypdf import PdfReader

    pages: list[str] = []
    held = 0
    total = 0
    truncated = False
    try:
        reader = PdfReader(BytesIO(content))
        total = len(reader.pages)
        truncated = total > MAX_PAGES
        for page in reader.pages[:MAX_PAGES]:
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(text)
            held += len(text)
            if held > MAX_CHARS:
                truncated = True
                break
    except Exception:  # noqa: BLE001 - see the docstring
        return {"pagine": total, "text": "", "troncato": False}
    joined = "\n".join(pages)
    if len(joined) > MAX_CHARS:
        joined = joined[:MAX_CHARS] + TRUNCATION_MARKER
    elif truncated:
        joined = joined + TRUNCATION_MARKER
    return {"pagine": total, "text": joined, "troncato": truncated}


def _limit_memory() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):  # not POSIX, or the limit is not allowed
        pass


def main() -> None:
    _limit_memory()
    sys.stdout.write(json.dumps(read_pdf(sys.stdin.buffer.read())))  # ASCII-safe JSON


if __name__ == "__main__":
    main()
