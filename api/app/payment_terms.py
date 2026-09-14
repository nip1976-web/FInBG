"""Курс и сумма в валюте, названные плательщиком прямо в назначении платежа.

Валютный счёт оплачивается рублями, и клиент сплошь и рядом пишет в платёжке,
как он их посчитал: «(21 368.40 евро по курсу 107,20)», «(КУРС 96,1657)»,
«ЭКВ 2462,40 ЕВРО». Таких платежей в базе 160.

Это сильнее нашего пересчёта по курсу ЦБ на дату платежа: клиент считает то по
курсу на дату уведомления, то по договорному курсу «ЦБ плюс процент», и счёт
после обратного пересчёта выглядит переплаченным, хотя закрыт полностью.

Здесь только чтение текста. Решать, верить ли прочитанному, должен вызывающий:
в назначении попадаются и чужие числа - сумма другой спецификации, дата,
номер договора, - поэтому наружу отдаются **все** кандидаты по порядку, а
отбор идёт по правдоподобию против курса ЦБ.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Число с разделителями тысяч и дробной частью: «21 368.40», «12 384,96»,
# «27750,00», «4122».
_NUMBER = r"\d[\d\s ]{0,12}(?:[.,]\d{1,6})?"
# Дату за числом отсекаем: «курсу на 28.08.2024 102,2911» - здесь 28.08 не курс.
_NOT_A_DATE_TAIL = r"(?![.,]?\d)"

# «2900 евро», «13431,75 ЕВРО», «ЭКВ 2462,40 ЕВРО»
_AMOUNT_BEFORE_RE = re.compile(rf"({_NUMBER})\s*(?:евро|eur)\b", re.IGNORECASE)
# «(Евро 4122; курс ...)» - тот же смысл, обратный порядок. Между словом и
# числом пускаем только знаки, но не буквы: иначе «ЕВРО ПО КУРСУ 93,2274»
# отдаст курс вместо суммы.
_AMOUNT_AFTER_RE = re.compile(rf"(?:евро|eur)\b\s*[:№-]?\s*({_NUMBER})", re.IGNORECASE)
# «(КУРС 96,1657)», «КУРС ЕВРО 98,4099», «по курсу ЦБ РФ 102,7782», «курс=99.9341»,
# «по курсу на 28.08.2024 102,2911», «курс 28.10.24г.- 104,8094».
# Между словом и курсом попадается дата, и первое же число за словом бывает её
# обрывком, поэтому берём **все** числа с дробной частью в коротком окне после
# слова «курс»: дробная часть у курса есть всегда, а нужное из кандидатов
# выберет вызывающий по правдоподобию.
_RATE_KEYWORD_RE = re.compile(r"курс", re.IGNORECASE)
_RATE_VALUE_RE = re.compile(rf"(\d{{2,3}}[.,]\d{{2,6}}){_NOT_A_DATE_TAIL}")
_RATE_WINDOW = 60


def _number(raw: str) -> Decimal | None:
    text = raw.replace(" ", " ").replace(" ", "").rstrip(".,").replace(",", ".")
    if text.count(".") > 1 or not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _candidates(pattern: re.Pattern, description: str) -> list[Decimal]:
    found: list[Decimal] = []
    for match in pattern.finditer(description):
        value = _number(match.group(1))
        if value is not None and value not in found:
            found.append(value)
    return found


def stated_amounts(description: str | None) -> list[Decimal]:
    """Суммы в евро, названные в назначении, по порядку появления."""
    if not description:
        return []
    found = _candidates(_AMOUNT_BEFORE_RE, description)
    for value in _candidates(_AMOUNT_AFTER_RE, description):
        if value not in found:
            found.append(value)
    return found


def stated_rates(description: str | None) -> list[Decimal]:
    """Курсы, названные в назначении, по порядку появления."""
    if not description:
        return []
    found: list[Decimal] = []
    for keyword in _RATE_KEYWORD_RE.finditer(description):
        window = description[keyword.end() : keyword.end() + _RATE_WINDOW]
        for match in _RATE_VALUE_RE.finditer(window):
            value = _number(match.group(1))
            if value is not None and value not in found:
                found.append(value)
    return found
