"""The CAMT.053 reader (ISO 20022 bank-to-customer statement, REB-999; spec 2026-11-09, due
items and the cash schedule, § 2).

One `Stmt` of `camt.053.001.02` or `camt.053.001.08` becomes one `ParsedStatement`: the stated
opening (`OPBD`) and closing (`CLBD`) balances, the period (`FrToDt`, else the dates of those
balances) and one movement per booked `Ntry`. Element names are matched by local name, so the
reader does not care which version's namespace the file declares; the differences between the
two versions that touch what is read are the status (`Sts` text in .02, `Sts/Cd` in .08) and
the booking date (`Dt` or `DtTm`).

The counterparty of a movement is the other party of the transaction: the creditor of money
that goes out, the debtor of money that comes in, with its account. An entry with several
`TxDtls` (a batch) has no single counterparty, so it carries none and a description joining
what each says. The bank's reference is `AcctSvcrRef` alone: with none the movement has no
reference and the dedup key is the reference-less one. The `EndToEndId` goes to `end_to_end_id`
(`NOTPROVIDED` is the standard's «none»), never to the reference, since payers reuse it. Only
booked entries are read (`Sts` `BOOK`, or none stated): a pending one comes back booked in a
later statement, and importing both would count it twice, the same rule as the Banco BPM
`-da contab` rows. A file with more than one `Stmt` (several
accounts) is refused: a statement is imported into one account.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation

from lxml import etree

from pigrocrm.core.bank.readers import ParsedMovement, ParsedStatement
from pigrocrm.core.errors import ValidationFailed

FORMAT = "camt053"
ENTITY = "bank_statement"
NOT_PROVIDED = "NOTPROVIDED"
DESCRIPTION_MAX_LENGTH = 1000
NAME_MAX_LENGTH = 255
REFERENCE_MAX_LENGTH = 255


def _refuse(field: str, reason: str) -> ValidationFailed:
    return ValidationFailed(ENTITY, field, reason)


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _children(element: etree._Element, name: str) -> list[etree._Element]:
    return [child for child in element if _local(child) == name]


def _find(element: etree._Element | None, *path: str) -> etree._Element | None:
    for name in path:
        if element is None:
            return None
        found = _children(element, name)
        element = found[0] if found else None
    return element


def _text(element: etree._Element | None, *path: str) -> str | None:
    found = _find(element, *path)
    if found is None or found.text is None:
        return None
    value = " ".join(found.text.split())
    return value or None


def _date(element: etree._Element | None) -> dt.date | None:
    """A `Dt` or a `DtTm` child of `element`, as a date."""
    value = _text(element, "Dt") or _text(element, "DtTm")
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        raise _refuse("date", f"data non valida: {value[:10]}") from None


def _amount(element: etree._Element) -> tuple[Decimal, str]:
    """The signed amount of an `Amt` sibling pair (`Amt` and `CdtDbtInd`) and its currency."""
    amt = _find(element, "Amt")
    indicator = _text(element, "CdtDbtInd")
    if amt is None or amt.text is None or indicator not in ("CRDT", "DBIT"):
        raise _refuse("amount", "importo o segno mancante")
    try:
        value = Decimal(amt.text.strip())
    except InvalidOperation:
        raise _refuse("amount", "importo non valido") from None
    currency = amt.get("Ccy")
    if currency is None:
        raise _refuse("currency", "valuta mancante")
    return (value if indicator == "CRDT" else -value), currency


def _is_booked(entry: etree._Element) -> bool:
    status = _text(entry, "Sts") or _text(entry, "Sts", "Cd")
    return status is None or status == "BOOK"


def _joined(parts: list[str], limit: int) -> str | None:
    return " ".join(parts)[:limit] or None


def _transaction(entry: etree._Element, outgoing: bool) -> dict[str, str | None]:
    """The counterparty, the end-to-end id and the description of an entry's transaction
    details."""
    details = [
        detail for group in _children(entry, "NtryDtls") for detail in _children(group, "TxDtls")
    ]
    description: list[str] = []
    for detail in details:
        remittance = _find(detail, "RmtInf")
        if remittance is not None:
            description += [
                " ".join(line.text.split())
                for line in _children(remittance, "Ustrd")
                if line.text and line.text.strip()
            ]
    if not description:
        description = [text for text in [_text(entry, "AddtlNtryInf")] if text]
    out: dict[str, str | None] = {
        "name": None,
        "iban": None,
        "end_to_end_id": None,
        "description": _joined(description, DESCRIPTION_MAX_LENGTH),
    }
    if len(details) != 1:
        return out
    detail = details[0]
    side, account = ("Cdtr", "CdtrAcct") if outgoing else ("Dbtr", "DbtrAcct")
    out["name"] = (_text(detail, "RltdPties", side, "Nm") or "")[:NAME_MAX_LENGTH] or None
    out["iban"] = _text(detail, "RltdPties", account, "Id", "IBAN")
    end_to_end = _text(detail, "Refs", "EndToEndId")
    out["end_to_end_id"] = None if end_to_end in (None, NOT_PROVIDED) else end_to_end
    return out


def _movement(entry: etree._Element) -> ParsedMovement:
    amount, currency = _amount(entry)
    booking = _date(_find(entry, "BookgDt"))
    if booking is None:
        raise _refuse("booking_date", "movimento senza data contabile")
    info = _transaction(entry, outgoing=amount < 0)
    reference = (_text(entry, "AcctSvcrRef") or "")[:REFERENCE_MAX_LENGTH]
    return ParsedMovement(
        booking_date=booking,
        value_date=_date(_find(entry, "ValDt")),
        amount=amount,
        currency=currency,
        counterparty_name=info["name"],
        counterparty_iban=info["iban"],
        description=info["description"],
        bank_reference=reference or None,
        end_to_end_id=info["end_to_end_id"],
    )


def _balance(statement: etree._Element, code: str) -> tuple[Decimal, dt.date | None] | None:
    for balance in _children(statement, "Bal"):
        if _text(balance, "Tp", "CdOrPrtry", "Cd") == code:
            return _amount(balance)[0], _date(_find(balance, "Dt"))
    return None


def read_camt053(content: bytes) -> ParsedStatement:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError:
        raise _refuse("file", "il file non è un XML valido") from None
    statements = [
        statement
        for message in _children(root, "BkToCstmrStmt")
        for statement in _children(message, "Stmt")
    ]
    if not statements:
        raise _refuse("file", "il file non contiene un estratto conto CAMT.053")
    if len(statements) > 1:
        raise _refuse("file", "il file contiene più estratti conto: uno per conto")
    statement = statements[0]

    opening = _balance(statement, "OPBD")
    closing = _balance(statement, "CLBD")
    window = _find(statement, "FrToDt")
    period_from = _day(_text(window, "FrDtTm")) or (opening[1] if opening else None)
    period_to = _day(_text(window, "ToDtTm")) or (closing[1] if closing else None)
    iban = _text(statement, "Acct", "Id", "IBAN")
    return ParsedStatement(
        format=FORMAT,
        account_iban=iban,
        currency=_text(statement, "Acct", "Ccy"),
        period_from=period_from,
        period_to=period_to,
        opening_balance=opening[0] if opening else None,
        closing_balance=closing[0] if closing else None,
        movements=[_movement(e) for e in _children(statement, "Ntry") if _is_booked(e)],
    )


def _day(value: str | None) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        raise _refuse("period", f"periodo non valido: {value[:10]}") from None
