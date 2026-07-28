"""Parsing the date filter people actually type into a column search box.

One narrow column has to cover a single day, a whole month, a year and a range,
so instead of several inputs the filter accepts what a Russian accountant would
write by hand:

    10.03.2026              один день
    03.2026                 весь март
    2026                    весь год
    10.03.2026-15.03.2026   диапазон
"""

from __future__ import annotations

import calendar
import re
from datetime import date

RANGE_SEPARATORS = ("..", "—", "–", " - ", "-")
# The books start in 2024, so anything outside this window is a half-typed date
# ("10.03" would otherwise read as October 2003) rather than a real query.
MIN_YEAR, MAX_YEAR = 2015, 2100


def _full_year(value: int) -> int:
    # "26" means 2026: the data starts in 2024 and nobody types 1926 here.
    return value if value >= 100 else 2000 + value


def _plausible(year: int) -> bool:
    return MIN_YEAR <= year <= MAX_YEAR


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _parse_part(text: str) -> tuple[date, date] | None:
    """A day, a month or a year, as the range of dates it covers."""
    parts = [part for part in re.split(r"[./-]", text.strip()) if part != ""]
    if not parts or not all(part.isdigit() for part in parts):
        return None

    try:
        if len(parts) == 1:
            digits = parts[0]
            # Typed without separators, the fastest way to enter a date:
            # "280726" and "28072026" both mean 28.07.2026.
            if len(digits) in (6, 8):
                day, month = int(digits[:2]), int(digits[2:4])
                year = _full_year(int(digits[4:]))
                if not _plausible(year):
                    return None
                exact = date(year, month, day)
                return exact, exact

            # "0726" is July 2026. No ambiguity with a bare year: every year we
            # accept starts with 20 or 21, which is never a month.
            if len(digits) == 4 and 1 <= int(digits[:2]) <= 12:
                month, year = int(digits[:2]), _full_year(int(digits[2:]))
                if _plausible(year):
                    return _month_range(year, month)

            year = int(digits)
            if not _plausible(year):
                return None
            return date(year, 1, 1), date(year, 12, 31)

        if len(parts) == 2:
            # "03.2026" — month and year, in that order.
            month, year = int(parts[0]), _full_year(int(parts[1]))
            if not 1 <= month <= 12 or not _plausible(year):
                return None
            return _month_range(year, month)

        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), _full_year(int(parts[2]))
            if not _plausible(year):
                return None
            exact = date(year, month, day)
            return exact, exact
    except ValueError:
        return None
    return None


def parse_date_query(value: str | None) -> tuple[date, date] | None:
    """The date range the text covers, or None when it isn't a date at all.

    Returning None lets the caller drop the filter instead of showing nothing,
    which matters while the user is still typing.
    """
    if not value or not value.strip():
        return None

    text = value.strip()
    whole = _parse_part(text)
    if whole:
        return whole

    for separator in RANGE_SEPARATORS:
        if separator not in text:
            continue
        left, _, right = text.partition(separator)
        start, end = _parse_part(left), _parse_part(right)
        if start and end:
            return min(start[0], end[0]), max(start[1], end[1])
    return None
