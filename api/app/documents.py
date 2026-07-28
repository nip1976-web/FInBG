"""Parsing of счёт/спецификация references out of free-text payment descriptions.

The API (which shows the parsed reference in the Payments table) and the Bitrix
sync job (which looks the number up in Bitrix) read the same bank-statement
text, so the matching lives here rather than in each caller. When the two had
their own patterns they drifted: the table displayed "Счёт №3777" for
"счёт-договору N 3777" while the sync never recognised a number there at all,
so it silently looked nothing up and the payment showed as a mismatch.
"""

from __future__ import annotations

import re

# The number rarely sits flush against the keyword ("СЧЕТУ НА ОПЛАТУ № 3189",
# "счёт-договору N 3777"), so allow a short filler. It must not cross a digit
# (that would jump into a date) nor a comma/semicolon (that would jump into the
# next clause and pick up a contract number).
_FILLER = r"[^\d\n,;]{0,25}?"
# Refuse a number that is only the first part of something longer: the day of a
# date ("по счёту от 29.06.2026") or a composite document number that Bitrix
# never issues ("по счету 29/09/7" — grabbing 29 there hits an unrelated invoice).
_NOT_A_DATE = r"(?![./]\d)"

# "сч-ф", "сч/ф" and "счф" are счёт-фактура — a tax document, never a payable
# invoice, and never present in Bitrix.
_NOT_AN_INVOICE_FACTURA = r"(?!\s*[-/]?\s*ф)"

INVOICE_RE = re.compile(
    rf"\b(?:сч[её]т(?:у|а|ом)?|сч)\.?{_NOT_AN_INVOICE_FACTURA}{_FILLER}(\d{{1,10}}){_NOT_A_DATE}\b",
    re.IGNORECASE,
)
SPEC_RE = re.compile(
    rf"\b(?:спец[а-яё]*|сп)\.?{_FILLER}(\d{{1,10}}){_NOT_A_DATE}\b",
    re.IGNORECASE,
)
DOC_DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
# How far past the number to look for its date ("№3799 от 07.07.2026").
DOC_DATE_WINDOW = 20


def invoice_numbers(description: str | None) -> list[int]:
    """Every счёт number mentioned, in order of appearance, deduplicated."""
    if not description:
        return []
    return list(dict.fromkeys(int(value) for value in INVOICE_RE.findall(description)))


def invoice_number(description: str | None) -> int | None:
    """The first счёт number mentioned, ignoring спецификация."""
    numbers = invoice_numbers(description)
    return numbers[0] if numbers else None


INVOICE_KIND = "invoice"
SPEC_KIND = "spec"


def _first_document(pattern: re.Pattern, description: str, kind: str) -> tuple[str, str, str | None] | None:
    match = pattern.search(description)
    if not match:
        return None
    date_match = DOC_DATE_RE.search(description, match.end(), match.end() + DOC_DATE_WINDOW)
    return kind, match.group(1), date_match.group() if date_match else None


def extract_document_reference(description: str | None) -> tuple[str | None, str | None, str | None]:
    """(kind, number, date) of the document the payer referenced.

    Favours счёт over спецификация because that is what Bitrix invoice
    matching keys off of.
    """
    if not description:
        return None, None, None
    return (
        _first_document(INVOICE_RE, description, INVOICE_KIND)
        or _first_document(SPEC_RE, description, SPEC_KIND)
        or (None, None, None)
    )


def as_invoice_number(kind: str | None, number: str | None) -> int | None:
    """The reference as a Bitrix invoice id, or None when it isn't one."""
    if kind != INVOICE_KIND or not number:
        return None
    try:
        return int(number)
    except ValueError:
        return None


def is_bitrix_mismatch(
    kind: str | None, number: str | None, bitrix_invoice_id: int | None
) -> bool:
    """True when the payment names a счёт that Bitrix either never confirmed
    or confirmed under a different invoice id."""
    referenced = as_invoice_number(kind, number)
    return referenced is not None and referenced != bitrix_invoice_id
