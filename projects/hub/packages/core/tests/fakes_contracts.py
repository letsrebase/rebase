"""Renderers that never run pandoc or Typst: the service and API tests' seam, the way
`RecordingSender` is the mail's. `FakeRenderer` still runs the checks the real one runs
before typesetting (`checked`), so a fee or a payment term the page could not print
fails here exactly as it would in production, and it reports the text's real version
and the fields it would leave blank."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from rebase_core.contracts.fields import FIELD, ContractFailed, Value, checked
from rebase_core.contracts.render import Rendered, text_path, text_version


@dataclass
class FakeRenderer:
    calls: list[tuple[str, dict[str, Value]]] = field(default_factory=list)
    draft: bool = True

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        filled = checked(dict(data))
        self.calls.append((document, dict(data)))
        asked = dict.fromkeys(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        blank = [key for key in asked if filled.get(key) in (None, "")]
        return Rendered(
            pdf=b"%PDF-1.7 fake " + document.encode(),
            blank=blank,
            version=text_version(document),
            draft=self.draft,
        )


@dataclass
class FailingRenderer:
    """Fails like a machine without pandoc, on `document` or on every one."""

    document: str | None = None

    def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
        if self.document is None or document == self.document:
            raise ContractFailed("pandoc is not on PATH")
        return FakeRenderer().render(document, data)
