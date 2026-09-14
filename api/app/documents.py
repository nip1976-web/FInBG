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
# Одной платёжкой закрывают несколько счетов и перечисляют их через запятую:
# «по счетам 1395, 1401», «№ 1377, 1381, 1409», «№1453 от 15.11.2024, №1613 от
# 24.12.2024». Слово «счёт» при этом стоит один раз, поэтому список продолжаем
# вручную - вплотную за уже найденным номером и только через запятую или точку
# с запятой. Пробела мало: «по счету 1469 по договору 12/05» и «счёт 1234 в
# т.ч. НДС 20%» тогда притащили бы чужие числа.
_INVOICE_DATE_TAIL = r"(?:\s*от\s*\d{1,2}\.\d{1,2}\.\d{2,4}\s*г?\.?)?"
# Два знака и больше: одна цифра после запятой - это почти всегда количество
# («3777, 2 шт»), а не номер счёта.
_MORE_INVOICES_RE = re.compile(
    rf"{_INVOICE_DATE_TAIL}\s*[,;]\s*(?:и\s+)?(?:№|N|#)?\s*(\d{{2,10}}){_NOT_A_DATE}\b",
    re.IGNORECASE,
)
DOC_DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
# How far past the number to look for its date ("№3799 от 07.07.2026").
DOC_DATE_WINDOW = 20


# «Оплата 30% по счет фактуре N133 ... Сч 781» - счёт-фактуру пишут и через
# пробел, и тогда короткое «сч» проскакивает мимо запрета выше: остаётся «ет
# фактуре N» внутри прокладки. Номер налогового документа в Bitrix существует
# и принадлежит чужому клиенту, поэтому такие совпадения отбрасываем целиком.
_FACTURA_RE = re.compile(r"фактур", re.IGNORECASE)


def _invoice_matches(description: str):
    for match in INVOICE_RE.finditer(description):
        if _FACTURA_RE.search(match.group(0)):
            continue
        yield match


def invoice_numbers(description: str | None) -> list[int]:
    """Every счёт number mentioned, in order of appearance, deduplicated.

    Списки продолжаются за найденным номером: «по счетам 1395, 1401» - это два
    счёта, а не один. Пока читался только первый, платёж целиком ложился на
    него, счёт числился переплаченным, а второй - неоплаченным.
    """
    if not description:
        return []
    found: list[str] = []
    for match in _invoice_matches(description):
        found.append(match.group(1))
        position = match.end()
        while (more := _MORE_INVOICES_RE.match(description, position)) is not None:
            found.append(more.group(1))
            position = more.end()
    return list(dict.fromkeys(int(value) for value in found))


def invoice_number(description: str | None) -> int | None:
    """The first счёт number mentioned, ignoring спецификация."""
    numbers = invoice_numbers(description)
    return numbers[0] if numbers else None


INVOICE_KIND = "invoice"
SPEC_KIND = "spec"


def _first_document(pattern: re.Pattern, description: str, kind: str) -> tuple[str, str, str | None] | None:
    if kind == INVOICE_KIND:
        match = next(_invoice_matches(description), None)
    else:
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
