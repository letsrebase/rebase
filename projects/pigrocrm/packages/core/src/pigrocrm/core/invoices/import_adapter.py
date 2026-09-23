"""The neutral import-adapter interface (REB-363), ported from mastro's
`src/lib/server/import/adapter.ts`.

`InvoiceFormatAdapter` and `import_schemas.ParsedInvoice` are the only things a
caller of this module depends on: neither names FatturaPA or any other concrete
format, so adding a second format one day is a new module implementing this
protocol, never a change to it -- the same "engine never names a format" split
mastro's `adapter.ts`/`invoice.ts` keep from `formats/fattura-pa/`.

`detect`/`parse` take raw bytes, not a wrapped file handle: unlike mastro, nothing
in this issue's scope needs a filename for diagnostics (no registry scans several
candidate adapters against one file yet), and mastro's own docstring is explicit
that an adapter must never use a filename to decide whether it claims a file --
trusting an extension is how a renamed PDF ends up "detected" as an invoice. A
future registry is free to carry a filename alongside the bytes without this
protocol needing one.
"""

from typing import Protocol, runtime_checkable

from pigrocrm.core.invoices.import_schemas import ParsedInvoice


@runtime_checkable
class InvoiceFormatAdapter(Protocol):
    """One structured invoice format. FatturaPA's FPR12
    (`fatturapa_import.fattura_pa_fpr12_adapter`) is the first; a second is a new
    module implementing this protocol, never a change to it.
    """

    id: str

    def detect(self, content: bytes) -> bool:
        """Whether `content` is this adapter's format.

        Must be cheap and total: unrelated bytes, a different XML document, or
        outright garbage must make this return `False`, never raise. A caller
        that tries several candidate adapters in turn against many files relies
        on that.
        """
        ...

    def parse(self, content: bytes) -> list[ParsedInvoice]:
        """Parses `content` into the neutral shape, one list entry per invoice
        the document actually carries.

        Only ever called after `detect` has returned `True` for the same bytes.
        A document that passes `detect` but turns out malformed raises rather
        than returning a partial or guessed `ParsedInvoice` -- there is no human
        in this path to notice a wrong field.

        Most documents produce exactly one entry. A format whose documents can
        be transmitted as a batch of several invoices bundled into one file
        (FatturaPA's own `lotto`) returns one entry per invoice the file
        actually carries, in document order, rather than parsing only the first
        and dropping the rest.
        """
        ...
