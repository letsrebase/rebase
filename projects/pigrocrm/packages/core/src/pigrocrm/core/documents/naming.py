import re
import unicodedata

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify_folder(raw: str) -> str:
    """A customer name (or a document title) as a storage-key/filename segment.

    Mirrors `fields/schemas.slugify_key` in spirit -- NFKD, drop the combining marks,
    collapse the rest -- but joins with "-" rather than "_", because these segments
    are read by a human browsing Google Drive, which is the whole point of storing a
    slug at all.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    transliterated = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SLUG_STRIP.sub("-", transliterated.strip().lower()).strip("-") or "senza-nome"
