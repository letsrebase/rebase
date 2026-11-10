"""FPR12 constants: namespaces, widths, schema patterns and the Latin-1 spellings."""

import re

FPR12_NAMESPACE = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"
DS_NAMESPACE = "http://www.w3.org/2000/09/xmldsig#"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
FORMAT_TRANSMISSION = "FPR12"
# The format of an invoice to a public body (REB-994): the specifications' `FPA12`, with the
# 6-character code of the body's billing office in the Indice PA (IPA) as `CodiceDestinatario`
# (Specifiche tecniche v1.4, § 1.1; the Sistema di Interscambio refuses a 7-character code
# beside `FPA12` and a 6-character one beside `FPR12`, control 00427, and a `versione` that
# disagrees with `FormatoTrasmissione`, control 00428).
FORMAT_TRANSMISSION_PA = "FPA12"
# The withholding block's `TipoRitenuta` is the pack's (`FiscalPack.sales_withholding`).
WITHHOLDING_SUBJECT = "SI"

# All three prefixes are declared on the root, `ds:` and `xsi:` included even though
# nothing in this file uses either. `ds:` becomes load-bearing only with the digital
# signature, which is out of scope; keeping both is how the prologue stays identical
# to one already accepted downstream. This is the reason for lxml -- see the module
# docstring.
NSMAP: dict[str | None, str] = {"p": FPR12_NAMESPACE, "ds": DS_NAMESPACE, "xsi": XSI_NAMESPACE}

# `CodiceDestinatario` for a private recipient with no SDI code, paired with
# `PECDestinatario`. Not deducible from the schema, where the field is simply
# mandatory.
CODE_RECIPIENT_FALLBACK = "0000000"
# And for a recipient outside Italy, whom the SdI does not route to at all. Seven
# X's is what the specification reserves for that case; a foreign customer has no SDI
# code and no PEC and is not supposed to have either.
CODE_RECIPIENT_FOREIGN = "XXXXXXX"
# `CAP` is a five-digit numeric string in FPR12 and the technical specifications fill
# it with `00000` for an address outside Italy, where `Provincia` is left out. The
# writer applies the convention whatever the customer row holds: a London company keeps
# `EC1V 9HL` in its record, the PDF prints it, and `address_xml` carries it at the
# end of `Indirizzo` so the document does not lose it (ORB-38).
POSTAL_CODE_FOREIGN = "00000"
# Immediate VAT liability. The deferred and cash-basis variants are out of scope.
VAT_COLLECTABILITY_VAT = "I"
# Split payment (art. 17-ter DPR 633/72): the VAT is shown and the customer pays it to the tax
# office, `EsigibilitaIVA` `S` (Specifiche tecniche v1.4, § 3.9.2).
VAT_COLLECTABILITY_SPLIT = "S"
STAMP_DUTY_VIRTUAL = "SI"

# FPR12 widths. Every one is narrower than the Postgres column it comes from, so each
# is a real check and not a restatement of the schema's own bound.
DENOMINATION_MAX = 80
ADDRESS_MAX = 60
CITY_MAX = 60
DESCRIPTION_MAX = 1000
MEMO_MAX = 200

# `DatiOrdineAcquisto` (REB-693): IdDocumento is String20Type, CodiceCUP and CodiceCIG are
# String15Type, all three Basic Latin.
ID_DOCUMENT_MAX = 20
CODE_PROJECT_CODE_MAX = 15
CODE_TENDER_CODE_MAX = 15
LEGAL_REFERENCE_MAX = 100
UNIT_OF_MEASURE_MAX = 10
EMAIL_MAX = 256
# `EmailContattiType`'s own `minLength` (REB-227): `ContattiTrasmittente/Email` carries
# it, `PECDestinatario`'s `EmailType` does not.
EMAIL_MIN = 7
PHONE_MAX = 12

# `xs:pattern` restrictions the schema applies. `.fullmatch` at every call site, never
# `.match` with `$`.
POSTAL_CODE_RE = re.compile(r"\d{5}")
PROVINCE_RE = re.compile(r"[A-Z]{2}")
COUNTRY_RE = re.compile(r"[A-Z]{2}")
IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Za-z0-9]{11,30}")
CODE_RECIPIENT_RE = re.compile(r"[A-Z0-9]{7}")
CODE_RECIPIENT_IPA_RE = re.compile(r"[A-Z0-9]{6}")
TYPE_PAYMENT_RE = re.compile(r"(TP|MP)\d{2}")
# `EmailType`'s own pattern (REB-227), translated from the vendored XSD's
# `xs:pattern` verbatim: a proper `local@domain` shape, not merely "contains an @".
EMAIL_RE = re.compile(
    r"([!#-'*+/-9=?A-Z^-~-]+(\.[!#-'*+/-9=?A-Z^-~-]+)*"
    r"|\"(\[\]!#-[^-~ \t]|(\\[\t -~]))+\")"
    r"@([!#-'*+/-9=?A-Z^-~-]+(\.[!#-'*+/-9=?A-Z^-~-]+)*|\[[\t -Z^-~]*\])"
)
# `EmailContattiType` is *not* implied by `EmailType`: its own `.+@.+[.]+.+` demands a
# literal dot somewhere after the `@`, which `EmailType` does not require, so
# `info@localhost` and `pigro@[::1]` are valid `EmailType` and invalid here (verified
# against the vendored XSD). `ContattiTrasmittente/Email` and
# `CedentePrestatore/Contatti/Email` are both `EmailContattiType` (schema lines 92 and
# 719), so both are checked against the intersection of the two patterns: a value the
# schema's own `EmailContattiType` accepts and that also has the shape of a real
# address, never merely "contains an @ and a dot somewhere".
EMAIL_CONTACTS_RE = re.compile(r"(?=.+@.+[.]+.+)(?:" + EMAIL_RE.pattern + r")")
# The pattern refusal's `expected` reaches the web client verbatim
# (`apps/web/src/lib/api.ts`'s `atteso: ...`); `EMAIL_RE`/`EMAIL_CONTACTS_RE` are each
# too long to show a user, unlike the short patterns above, so both PEC and Email
# refusals use this wording instead of the raw regex.
EMAIL_PATTERN_EXPECTED = "un indirizzo email nella forma nome@dominio"
# The schema's `String*LatinType` family restricts every one of them to
# `[\p{IsBasicLatin}\p{IsLatin-1Supplement}]`, i.e. Unicode code points U+0000-U+00FF
# only (confirmed against the vendored XSD's own `String80LatinType`,
# `String1000LatinType`, etc.). A code point outside that range is an SdI rejection,
# so it is refused here with the field named instead, rather than emitted and left to
# be rejected downstream with no context.
LATIN_RE = re.compile(r"[\x00-\xff]*")
# `UnitaMisura` is not one of them: the schema types it `String10Type`, which is
# `\p{IsBasicLatin}{1,10}`, so a superscript two in `m²` (U+00B2, inside Latin-1) is a
# schema error there and nowhere else. Checked as a pattern, on top of the range.
BASIC_LATIN_RE = re.compile(r"[\x00-\x7f]*")
# Typographic punctuation is how a person types: an em dash between two halves of a
# causale, curly quotes around a product name, an ellipsis. None of it is in the range
# above, and refusing it is a refusal over spelling rather than over content -- it cost
# a real invoice its file after the number was spent (ORB-140). Each of these has a
# plain Latin-1 spelling that says the same thing, so the writer spells it that way
# and the pre-check measures the spelled value. The set is punctuation and spaces
# only, on purpose: a currency sign or a letter from another script is content, and
# content the schema cannot carry is refused, before the number, with the field named.
LATIN_SPELLINGS = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u2022": "-",  # bullet
        "\u2043": "-",  # hyphen bullet
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark, the apostrophe most keyboards type
        "\u201a": "'",  # single low-9 quotation mark
        "\u201b": "'",  # single high-reversed-9 quotation mark
        "\u2032": "'",  # prime
        "\u201c": '"',  # left double quotation mark
        "\u201d": '"',  # right double quotation mark
        "\u201e": '"',  # double low-9 quotation mark
        "\u201f": '"',  # double high-reversed-9 quotation mark
        "\u2033": '"',  # double prime
        "\u2026": "...",  # horizontal ellipsis
        "\u2000": " ",  # en quad
        "\u2001": " ",  # em quad
        "\u2002": " ",  # en space
        "\u2003": " ",  # em space
        "\u2004": " ",  # three-per-em space
        "\u2005": " ",  # four-per-em space
        "\u2006": " ",  # six-per-em space
        "\u2007": " ",  # figure space
        "\u2008": " ",  # punctuation space
        "\u2009": " ",  # thin space
        "\u200a": " ",  # hair space
        "\u202f": " ",  # narrow no-break space
        "\u205f": " ",  # medium mathematical space
        "\u3000": " ",  # ideographic space
        "\u200b": "",  # zero width space, pasted in from a browser and never seen
        "\ufeff": "",  # byte order mark, same origin
        "\u200c": "",  # zero width non-joiner, same origin
        "\u200d": "",  # zero width joiner, same origin
        "\u200e": "",  # left-to-right mark, same origin
        "\u200f": "",  # right-to-left mark, same origin
        "\u2060": "",  # word joiner, same origin
    }
)

# The two `entity` labels the checks in this module are called with, which are also the
# two roles a party can hold on the document. The role decides one thing here: the
# recipient's PEC is written into `PECDestinatario` and the issuer's email into `Email`,
# and neither party's other address reaches the file at all. A pre-check that refused a
# value the writer never emits would be its own kind of drift, so each role is checked
# for the field it actually contributes.
RECIPIENT_ENTITY = "customer"
ISSUER_ENTITY = "legal_entity"
# FPR12 allows an `IdCodice` of up to 28 characters, which is what makes room for the
# registers of other countries. Italy's own two shapes are narrower and are checked by
# `normalise_fiscal_id`; outside Italy there is no shape this exporter can meaningfully
# assert, since every country issues its own.
ID_CODE_MAX = 28
