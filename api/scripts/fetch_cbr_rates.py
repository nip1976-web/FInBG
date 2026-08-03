"""Складывает официальные курсы ЦБ РФ в таблицу fx_rates.

Запускается раз в сутки утром. ЦБ устанавливает курс по рабочим дням вечером —
на следующий рабочий день, а в пятницу сразу на выходные. Поэтому в понедельник
действует курс, установленный в пятницу и датированный субботой: выгрузка отдаёт
Date="01.08.2026", хотя на дворе третье.

Из этого следует главное правило хранения: строка пишется на **ту дату, которую
назвал ЦБ**, а не на дату запуска. Курс на любой день — это последняя строка с
датой не позже нужной. Выходные и праздники так покрываются сами собой, и
выдуманных строк в базе не появляется.

Храним три валюты: доллар, евро и юань. Понадобится ещё одна — дописать в
CURRENCIES и добрать историю режимом --history.

Уже сохранённое не переписывается никогда: установленный ЦБ курс задним числом
не меняется, а переписывать его значит рисковать историей, по которой уже
посчитаны сделки. Обычный запуск добирает только недостающие дни — от последней
сохранённой даты до сегодня, так что простой сервера на неделю дыры не оставит.

Использование:
    python fetch_cbr_rates.py                      # добрать недостающее (ежедневный запуск)
    python fetch_cbr_rates.py 15.06.2026           # курс на указанную дату
    python fetch_cbr_rates.py --history 01.01.2025 # добрать историю с этой даты
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "dbname=finbg")
DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
DYNAMIC_URL = "https://www.cbr.ru/scripts/XML_dynamic.asp"
SOURCE = "ЦБ РФ"
TIMEOUT_SECONDS = 30

# коды валют в справочнике ЦБ — нужны для выгрузки за период,
# она отдаётся по одной валюте за запрос
CURRENCIES = {
    "USD": "R01235",
    "EUR": "R01239",
    "CNY": "R01375",
}


def load(url: str, params: dict[str, str] | None = None) -> ElementTree.Element:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "FinBG/1.0"})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        # выгрузка в windows-1251, кодировка объявлена внутри самого XML,
        # поэтому отдаём разборщику байты и не декодируем сами
        return ElementTree.fromstring(response.read())


def unit_rate(nominal_text: str | None, value_text: str | None) -> Decimal | None:
    nominal = Decimal((nominal_text or "1").replace(",", "."))
    value = Decimal((value_text or "0").replace(",", "."))
    if nominal <= 0 or value <= 0:
        return None
    # ЦБ даёт цену за номинал (за 10 юаней, за 100 йен) — приводим к единице
    return value / nominal


def read_one_day(requested_date: str | None) -> list[tuple[str, str, Decimal]]:
    """Курсы всех наших валют на одну дату. Возвращает (дата, валюта, курс)."""
    params = {"date_req": requested_date} if requested_date else None
    root = load(DAILY_URL, params)
    rate_date = root.attrib["Date"]
    rows: list[tuple[str, str, Decimal]] = []
    for item in root.findall("Valute"):
        code = (item.findtext("CharCode") or "").strip().upper()
        if code not in CURRENCIES:
            continue
        rate = unit_rate(item.findtext("Nominal"), item.findtext("Value"))
        if rate is not None:
            rows.append((rate_date, code, rate))
    return rows


def read_history(code: str, since: str, until: str) -> list[tuple[str, str, Decimal]]:
    """Вся история одной валюты за период — один запрос вместо сотен."""
    root = load(
        DYNAMIC_URL,
        {"date_req1": since, "date_req2": until, "VAL_NM_RQ": CURRENCIES[code]},
    )
    rows: list[tuple[str, str, Decimal]] = []
    for record in root.findall("Record"):
        rate = unit_rate(record.findtext("Nominal"), record.findtext("Value"))
        if rate is not None:
            rows.append((record.attrib["Date"], code, rate))
    return rows


def last_stored_date() -> date | None:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select max(rate_date) from fx_rates where source = %s", (SOURCE,))
            return cursor.fetchone()[0]


def store(rows: list[tuple[str, str, Decimal]]) -> int:
    """Пишет только недостающее. Уже сохранённый курс не трогаем никогда:
    однажды установленный ЦБ курс не меняется задним числом, и переписывать его
    значит рисковать испортить историю, по которой уже посчитаны сделки."""
    if not rows:
        return 0
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from fx_rates")
            before = cursor.fetchone()[0]
            cursor.executemany(
                """
                insert into fx_rates (rate_date, currency, rate_to_rub, source)
                values (to_date(%s, 'DD.MM.YYYY'), %s, %s, %s)
                on conflict (rate_date, currency, source) do nothing
                """,
                [(rate_date, code, rate, SOURCE) for rate_date, code, rate in rows],
            )
            cursor.execute("select count(*) from fx_rates")
            after = cursor.fetchone()[0]
        connection.commit()
    return after - before


def main() -> int:
    arguments = sys.argv[1:]
    try:
        if arguments and arguments[0] == "--history":
            if len(arguments) < 2:
                print("Укажите дату начала: --history 01.01.2025", file=sys.stderr)
                return 2
            since = arguments[1]
            until = arguments[2] if len(arguments) > 2 else date.today().strftime("%d.%m.%Y")
            rows: list[tuple[str, str, Decimal]] = []
            for code in CURRENCIES:
                found = read_history(code, since, until)
                print(f"{code}: {len(found)} дней с {since} по {until}")
                rows.extend(found)
        elif arguments:
            rows = read_one_day(arguments[0])
        else:
            # обычный ежедневный запуск: добираем от последней сохранённой даты
            # до сегодня. Если сервер стоял неделю, пропуск закроется сам, а не
            # оставит дыру, которую потом никто не заметит
            last = last_stored_date()
            if last is None:
                rows = read_one_day(None)
            else:
                today = date.today()
                if last >= today:
                    print(f"Курсы уже есть по {last.strftime('%d.%m.%Y')}, добирать нечего")
                    return 0
                rows = []
                for code in CURRENCIES:
                    rows.extend(
                        read_history(
                            code,
                            last.strftime("%d.%m.%Y"),
                            today.strftime("%d.%m.%Y"),
                        )
                    )
    except Exception as error:
        # молча падать нельзя: запуск по расписанию, и тишина будет означать
        # «всё хорошо», хотя курс не обновился
        print(f"Не удалось получить курсы с сайта ЦБ РФ: {error}", file=sys.stderr)
        return 1

    present = {code for _, code, _ in rows}
    missing = [code for code in CURRENCIES if code not in present]
    if missing:
        # если ЦБ перестал отдавать одну из наших валют, это надо заметить сразу,
        # а не обнаружить через месяц по дыре в истории
        print(f"Нет данных по валютам: {', '.join(missing)}", file=sys.stderr)
        if not rows:
            return 1

    added = store(rows)
    dates = {rate_date for rate_date, _, _ in rows}
    print(f"Получено дат: {len(dates)}, из них новых строк записано: {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
