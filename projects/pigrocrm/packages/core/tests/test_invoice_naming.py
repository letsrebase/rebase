"""Names that leave the system.

The XML file goes to an intermediary that often validates its name before its
content, and the proforma's reference goes on a document somebody might try to pay.
Both are therefore deterministic functions of stored data, never derived from a
user-visible label -- the previous system recovered the fiscal progressive with
`/Fattura\\s+(\\d+)/i` against a title.
"""

from uuid import UUID

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.naming import (
    NUMERO_COMPLETO_RE,
    RIFERIMENTO_PROFORMA_RE,
    invoice_storage_prefix,
    numero_completo,
    proforma_riferimento,
    proforma_storage_prefix,
    progressivo_invio,
    sdi_filename,
)


def test_the_progressive_is_five_base36_characters_of_year_times_ten_thousand() -> None:
    """`anno * 10000 + numero` in base 36, padded to five characters: deterministic,
    so re-exporting produces the same name, and collision-free while numero stays
    under 10 000."""
    assert progressivo_invio(2026, 1) == "C28PT"
    assert progressivo_invio(2026, 2) == "C28PU"
    assert progressivo_invio(2027, 1) == "C2GFL"


def test_the_progressive_is_injective_across_a_year_boundary() -> None:
    seen = {progressivo_invio(anno, numero) for anno in (2026, 2027) for numero in (1, 9999)}
    assert len(seen) == 4


def test_a_five_digit_numero_is_refused_rather_than_producing_an_ambiguous_name() -> None:
    with pytest.raises(ValidationFailed) as caught:
        progressivo_invio(2026, 10_000)
    assert caught.value.details["field"] == "numero"


def test_the_sdi_file_name_follows_the_convention() -> None:
    assert sdi_filename("12345678901", 2026, 7) == "IT12345678901_C28PZ.xml"


def test_the_sdi_file_name_refuses_an_identifier_that_is_not_a_piva_or_a_cf() -> None:
    """A malformed IdCodice is an outright rejection; a malformed *file name* is one
    too, and cheaper to catch here."""
    for bad in ("1234567890", "IT12345678901", "12345678901\n", "abc"):
        with pytest.raises(ValidationFailed):
            sdi_filename(bad, 2026, 7)


def test_a_sixteen_character_fiscal_code_is_accepted() -> None:
    assert sdi_filename("RSSMRA80A01H501U", 2026, 1) == "ITRSSMRA80A01H501U_C28PT.xml"


def test_storage_prefixes_are_lowercase_and_disjoint() -> None:
    """`storage/base.py`'s key pattern is lowercase-only on purpose -- two keys
    differing only in case name the same file on APFS and NTFS -- so the SdI's
    uppercase name cannot be a storage key. It is the download name instead."""
    azienda_id = UUID("0192f0aa-0000-7000-8000-00000000000a")
    # The azienda first since REB-619: `2026/7` names one invoice per azienda.
    assert invoice_storage_prefix(azienda_id, 2026, 7) == f"fatture/{azienda_id}/2026/7"
    assert (
        proforma_storage_prefix(UUID("0192f0aa-0000-7000-8000-000000000001"))
        == "proforma/0192f0aa-0000-7000-8000-000000000001"
    )
    assert invoice_storage_prefix(azienda_id, 2026, 7).islower()


def test_the_full_number_is_year_slash_number() -> None:
    assert numero_completo(2026, 7) == "2026/7"
    assert NUMERO_COMPLETO_RE.fullmatch("2026/7")


def test_a_proforma_reference_can_never_match_the_fiscal_number_pattern() -> None:
    """Mechanism 1 of the four that keep a proforma from being mistaken for an
    invoice: a non-numeric prefix that no fiscal-number regex accepts."""
    riferimento = proforma_riferimento(2026, 7)
    assert riferimento == "PROV-2026-0007"
    assert RIFERIMENTO_PROFORMA_RE.fullmatch(riferimento)
    assert NUMERO_COMPLETO_RE.fullmatch(riferimento) is None
