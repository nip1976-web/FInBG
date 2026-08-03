"""Ищет в Bitrix ИНН для клиентов, у которых его ещё нет. Только предложения.

В карточку клиента ничего не пишет. Совсем. Поиск идёт по названию, а название
врёт: на живых данных «Приангарский Лесоперерабатывающий Комплекс» нашёл «ООО
ПОШЕХОНСКИЙ ЛЕСОПЕРЕРАБАТЫВАЮЩИЙ КОМПЛЕКС» — совпало длинное слово, а фирма
другая. Поэтому решение за человеком, а скрипт только раскладывает варианты.

Клиенты без ИНН — не всегда пробел. Частные лица и Юмани его в нашем обороте
не имеют, и предложений по ним не будет: в Bitrix их просто нет.

Подтверждение — отдельным шагом:
    update counterparty_tax_id_suggestions set status='confirmed', decided_at=now() where id=...
    затем apply_confirmed_tax_ids.py

Использование:
    python suggest_counterparty_tax_ids.py            # показать найденное
    python suggest_counterparty_tax_ids.py --apply    # сохранить как предложения
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from psycopg.rows import dict_row

COMPANY_ENTITY_TYPE_ID = 4
MAX_CANDIDATES = 5


class BitrixCallError(Exception):
    pass


def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as source:
        for line in source:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def flatten_params(params: dict, prefix: str = "") -> list[tuple[str, object]]:
    encoded: list[tuple[str, object]] = []
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            encoded.extend(flatten_params(value, full_key))
        elif isinstance(value, (list, tuple)):
            for index, entry in enumerate(value):
                encoded.append((f"{full_key}[{index}]", entry))
        else:
            encoded.append((full_key, value))
    return encoded


def bitrix_call(base: str, method: str, params: dict | None = None) -> dict:
    payload = urllib.parse.urlencode(flatten_params(params or {})).encode()
    request = urllib.request.Request(base + method + ".json", data=payload)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        raise BitrixCallError(f"{method}: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BitrixCallError(f"{method}: {error}") from error
    if "error" in answer:
        raise BitrixCallError(answer.get("error_description") or answer["error"])
    return answer


def search_word(name: str) -> str:
    """Самое длинное значимое слово названия: «Амурская ЛК ООО» → «Амурская».

    Форма собственности и знаки препинания только мешают: в Bitrix они пишутся
    иначе, чем у нас, и точное совпадение не случается почти никогда.
    """
    cleaned = re.sub(r"[«»\"'.,()№-]", " ", name)
    cleaned = re.sub(
        r"\b(ООО|АО|ЗАО|ПАО|ОАО|ИП|НКО|Групп|филиал)\b", " ", cleaned, flags=re.I
    )
    words = [word for word in cleaned.split() if len(word) >= 4]
    return max(words, key=len) if words else name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--env-file", default="/opt/finbg/.secrets/bitrix.env")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    load_env(arguments.env_file)
    webhook_url = os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook_url:
        print("BITRIX_WEBHOOK_URL не задан", file=sys.stderr)
        return 2
    webhook = webhook_url.rstrip("/") + "/"

    with psycopg.connect(arguments.database_url, row_factory=dict_row) as connection:
        pending = connection.execute(
            """
            select c.id, c.name
            from counterparties c
            where c.tax_id is null
              and not exists (
                  select 1 from counterparty_tax_id_suggestions s
                  where s.counterparty_id = c.id
                    and s.status in ('confirmed', 'rejected')
              )
            order by c.name
            """
        ).fetchall()
        print(f"клиентов без ИНН и без решения: {len(pending)}")

        taken = {
            row["tax_id"]
            for row in connection.execute(
                "select tax_id from counterparties where tax_id is not null"
            ).fetchall()
        }

        found: list[tuple] = []
        for client in pending:
            word = search_word(client["name"])
            try:
                answer = bitrix_call(
                    webhook,
                    "crm.company.list",
                    {"filter": {"%TITLE": word}, "select": ["ID", "TITLE"]},
                )
            except BitrixCallError as error:
                print(f"  {client['name']}: Bitrix не ответил — {error}", file=sys.stderr)
                continue
            companies = answer.get("result", [])[:MAX_CANDIDATES]
            if not companies:
                print(f"  {client['name']}: в Bitrix не найдено (искал «{word}»)")
                continue

            ids = [int(item["ID"]) for item in companies]
            requisites = bitrix_call(
                webhook,
                "crm.requisite.list",
                {
                    "filter": {"ENTITY_TYPE_ID": COMPANY_ENTITY_TYPE_ID, "@ENTITY_ID": ids},
                    "select": ["ENTITY_ID", "RQ_INN", "RQ_COMPANY_NAME"],
                },
            ).get("result", [])
            details = {
                int(row["ENTITY_ID"]): (
                    str(row.get("RQ_INN") or "").strip(),
                    str(row.get("RQ_COMPANY_NAME") or "").strip() or None,
                )
                for row in requisites
            }

            for item in companies:
                company_id = int(item["ID"])
                inn, legal_name = details.get(company_id, ("", None))
                if not inn:
                    continue
                if inn in taken:
                    # этот ИНН уже стоит у другого клиента: либо у нас две
                    # карточки одной фирмы, либо совпадение ложное — в обоих
                    # случаях это разбирают руками, а не предложением
                    print(
                        f"  {client['name']}: ИНН {inn} уже занят другим клиентом — пропуск"
                    )
                    continue
                print(
                    f"  {client['name']} → {item.get('TITLE')} [ИНН {inn}]"
                )
                found.append(
                    (client["id"], inn, company_id, item.get("TITLE"), legal_name, "name_search")
                )

        print(f"\nвсего предложений: {len(found)}")
        if not arguments.apply:
            print("пробный прогон, ничего не сохранено (нужен --apply)")
            return 0

        with connection.cursor() as cursor:
            cursor.executemany(
                """
                insert into counterparty_tax_id_suggestions (
                    counterparty_id, suggested_tax_id, bitrix_company_id,
                    bitrix_title, legal_name, match_reason
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (counterparty_id, suggested_tax_id) do nothing
                """,
                found,
            )
        connection.commit()
        print("предложения сохранены, ни одна карточка клиента не изменена")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
