"""IBAN normalisation and the ISO 13616 check (mod 97), shared by the account, the
counterparty list and every statement reader."""

import re

IBAN_PATTERN = "^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$"
_SHAPE = re.compile(IBAN_PATTERN)
_MOD = 97


def normalise_iban(value: str) -> str:
    """The form two IBANs are compared by: no spaces, upper case."""
    return "".join(value.split()).upper()


def is_valid_iban(value: str) -> bool:
    """True when `value`, once normalised, has the shape of an IBAN and passes mod 97."""
    iban = normalise_iban(value)
    if not _SHAPE.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(char, 36)) for char in rearranged)
    return int(digits) % _MOD == 1
