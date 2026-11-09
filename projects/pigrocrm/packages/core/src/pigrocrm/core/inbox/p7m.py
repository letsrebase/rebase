"""The signed content of a `.p7m` (REB-971, design § 2.1).

A signed FatturaPA is a CMS `SignedData` envelope around the XML (CAdES, in practice). The SdI
and the intermediaries deliver it as DER or BER, sometimes Base64-wrapped (with or without PEM
armour), and a counter-signed file is an envelope inside an envelope; all of those are opened
here, down to the bytes that are not an envelope any more.

What this does **not** do is verify the signature. The XML is read either way (design § 2.1:
«whether the signature is also verified is the builder's call»), and a check against the
Italian trust list is not a thing a reader of this inbox can answer today. A file whose
envelope cannot be opened is a `P7mError` with a sentence a person can read, and the caller
keeps the file as an item of kind «other» with that sentence as its reason.
"""

import base64
import binascii
import re

from asn1crypto import cms

MAX_ENVELOPES = 4
"""How many envelopes deep a counter-signed file may be opened. Two is the real maximum; the
rest is a ceiling so a hostile file cannot make this loop."""

_DER_SEQUENCE = 0x30
_PEM_ARMOUR = re.compile(rb"-----(?:BEGIN|END)[^-]*-----")
_WHITESPACE = re.compile(rb"\s+")


class P7mError(ValueError):
    """The envelope cannot be opened. The message is Italian and is what the item shows."""


def _decode_base64(data: bytes) -> bytes | None:
    """The bytes a Base64 text decodes to, or `None` when it is not Base64 text. PEM armour
    and line breaks are ignored; the alphabet check is strict, so a binary file never decodes
    by accident."""
    text = _WHITESPACE.sub(b"", _PEM_ARMOUR.sub(b"", data))
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def envelope_bytes(data: bytes) -> bytes | None:
    """`data` as the DER or BER of an envelope: itself when it starts like one, its Base64
    decoding when it is Base64 text of one, `None` when it is neither."""
    if data[:1] == bytes([_DER_SEQUENCE]):
        return data
    decoded = _decode_base64(data)
    if decoded is not None and decoded[:1] == bytes([_DER_SEQUENCE]):
        return decoded
    return None


def _signed_content(envelope: bytes) -> bytes:
    try:
        info = cms.ContentInfo.load(envelope)
        if info["content_type"].native != "signed_data":
            raise P7mError("il file p7m non contiene una firma CMS")
        content = info["content"]["encap_content_info"]["content"]
    except P7mError:
        raise
    except Exception as exc:  # asn1crypto raises ValueError, TypeError, KeyError, OSError
        raise P7mError("la busta p7m non si legge") from exc
    native = content.native
    if native is None:
        raise P7mError("la firma p7m e' staccata: il documento firmato non e' nel file")
    if not isinstance(native, bytes):
        raise P7mError("la busta p7m non contiene un documento")
    return native


def open_p7m(data: bytes) -> bytes:
    """The document inside `data`, all the envelopes taken off.

    Raises `P7mError` for a file that is not an envelope, a detached signature, an envelope
    that does not parse, and one nested deeper than `MAX_ENVELOPES`."""
    current = data
    for depth in range(MAX_ENVELOPES + 1):
        envelope = envelope_bytes(current)
        if envelope is None:
            if depth == 0:
                raise P7mError("il file non e' una busta p7m (ne' DER ne' Base64)")
            return current
        if depth == MAX_ENVELOPES:
            raise P7mError(f"piu' di {MAX_ENVELOPES} buste p7m una dentro l'altra")
        current = _signed_content(envelope)
    raise AssertionError("unreachable")  # pragma: no cover
