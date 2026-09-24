"""The contracts a freelancer signs, typeset by the hub itself (REB-387).

`fields` is everything about a contract that is not a binary: the `{{key}}` fields, the
`[[...]]` proposals, the checks on the fee and the payment term, an Italian number and
an Italian date. `brand` reads the palette and instances the typeface. `render` runs
pandoc and Typst over the Markdown in `texts/` with `contract.typ.template`, and asks
Typst where the blanks the signing site fills have landed. `tools/build_contract_pdf.py`
is the laptop's thin wrapper over the same three.
"""
