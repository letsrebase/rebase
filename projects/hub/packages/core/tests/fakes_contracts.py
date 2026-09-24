"""Renderers that never run pandoc or Typst: the service and API tests' seam, the way
`RecordingSender` is the mail's. `FakeRenderer` still runs the checks the real one runs
before typesetting (`checked`), so a fee or a payment term the page could not print
fails here exactly as it would in production, and it reports the text's real version
and the fields it would leave blank."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from rebase_core.contracts.fields import FIELD, ContractFailed, Value, checked
from rebase_core.contracts.render import Rendered, SignatureBlank, text_path, text_version

# Where the phase 1 probe measured the letter's two blanks (probe § 9), and the same shape
# for the framework agreement's three: what `signature_blanks` answers without Typst.
FAKE_BLANKS: dict[str, list[SignatureBlank]] = {
    "lettera-di-incarico": [
        SignatureBlank("data firma", 2, 165.811, 647.241, 85.039, 6.660),
        SignatureBlank("firma professionista", 2, 303.638, 707.281, 155.906, 22.660),
    ],
    "contratto-quadro": [
        SignatureBlank("data firma", 5, 165.811, 520.0, 85.039, 6.660),
        SignatureBlank("firma professionista", 5, 303.638, 580.0, 155.906, 22.660),
        SignatureBlank("firma professionista", 6, 303.638, 300.0, 155.906, 22.660),
    ],
}


@dataclass
class FakeRenderer:
    calls: list[tuple[str, dict[str, Value]]] = field(default_factory=list)
    draft: bool = True
    # Whether each `render` asked for the copy that goes out for signature.
    signing: list[bool] = field(default_factory=list)
    # Whether each `signature_blanks` asked for the signing copy's layout (REB-406 fix
    # round 1, M4): `_dispatch` reads the blanks from the same copy it typeset.
    blanks_signing: list[bool] = field(default_factory=list)

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        filled = checked(dict(data))
        self.calls.append((document, dict(data)))
        self.signing.append(signing)
        asked = dict.fromkeys(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        blank = [key for key in asked if filled.get(key) in (None, "")]
        return Rendered(
            pdf=b"%PDF-1.7 fake " + document.encode(),
            blank=blank,
            version=text_version(document),
            draft=self.draft,
        )

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        checked(dict(data))
        self.blanks_signing.append(signing)
        return list(FAKE_BLANKS[document])

    def is_draft(self, document: str) -> bool:
        text_path(document)
        return self.draft


@dataclass
class FailingRenderer:
    """Fails like a machine without pandoc, on `document` or on every one."""

    document: str | None = None
    draft: bool = True

    def render(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> Rendered:
        if self.document is None or document == self.document:
            raise ContractFailed("pandoc is not on PATH")
        return FakeRenderer(draft=self.draft).render(document, data, signing=signing)

    def signature_blanks(
        self, document: str, data: Mapping[str, Value], *, signing: bool = False
    ) -> list[SignatureBlank]:
        if self.document is None or document == self.document:
            raise ContractFailed("typst is not on PATH")
        return list(FAKE_BLANKS[document])

    def is_draft(self, document: str) -> bool:
        return self.draft
