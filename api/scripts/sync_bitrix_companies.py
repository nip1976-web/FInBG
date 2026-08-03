"""Забирает из Bitrix компании и их реквизиты — ИНН, КПП, юридическое название.

Зачем. Клиент у нас опознаётся по написанию названия, и это уже дважды стоило
ошибок: «Приангарский ЛПК» и «Приангарский Лесоперерабатывающий Комплекс» —
одна фирма, но для программы разные. ИНН от написания не зависит.

Где он лежит. У счёта ИНН нет, у карточки компании отдельного поля тоже нет —
он в разделе реквизитов, отдельным запросом. Поэтому ходим дважды:
crm.company.list за названием карточки и crm.requisite.list за ИНН.

Берём только те компании, на которые ссылаются наши счета: тянуть весь Bitrix
незачем.

Использование:
    python sync_bitrix_companies.py            # показать, что изменится
    python sync_bitrix_companies.py --apply    # записать в базу
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import psycopg
from psycopg.rows import dict_row

BATCH_SIZE = 50
COMPANY_ENTITY_TYPE_ID = 4  # так Bitrix помечает реквизиты, принадлежащие компании


class BitrixCallError(Exception):
    """Сбой обращения к Bitrix. Отсутствие записи в выдаче сбоем не считается."""


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
    """Раскрывает вложенные словари и списки в запись вида `filter[@id][0]=...`.

    urlencode так не умеет: получив {"filter": {"@id": [1, 2]}}, он пройдёт по
    ключам внутреннего словаря и отправит `filter=@id`, на что Bitrix ответит
    голым HTTP 400. Проверено: без этого выдача молча схлопывается до одной
    записи, потому что повторный ключ затирает предыдущий.
    """
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
        description = answer.get("error_description") or answer.get("error")
        raise BitrixCallError(f"{method}: {description}")
    return answer


def fetch_titles(webhook: str, company_ids: list[int]) -> dict[int, str]:
    titles: dict[int, str] = {}
    for start in range(0, len(company_ids), BATCH_SIZE):
        chunk = company_ids[start : start + BATCH_SIZE]
        answer = bitrix_call(
            webhook,
            "crm.company.list",
            {"filter": {"@ID": chunk}, "select": ["ID", "TITLE"]},
        )
        for item in answer.get("result", []):
            titles[int(item["ID"])] = item.get("TITLE") or ""
    return titles


def fetch_requisites(webhook: str, company_ids: list[int]) -> dict[int, dict]:
    """ИНН, КПП и юридическое название. Выдача постраничная — идём по страницам,
    иначе на компаниях с несколькими наборами реквизитов часть потеряется."""
    requisites: dict[int, dict] = {}
    for start in range(0, len(company_ids), BATCH_SIZE):
        chunk = company_ids[start : start + BATCH_SIZE]
        offset = 0
        while True:
            answer = bitrix_call(
                webhook,
                "crm.requisite.list",
                {
                    "filter": {
                        "ENTITY_TYPE_ID": COMPANY_ENTITY_TYPE_ID,
                        "@ENTITY_ID": chunk,
                    },
                    "select": ["ENTITY_ID", "RQ_INN", "RQ_KPP", "RQ_COMPANY_NAME"],
                    "start": offset,
                },
            )
            for row in answer.get("result", []):
                company_id = int(row["ENTITY_ID"])
                inn = str(row.get("RQ_INN") or "").strip()
                # у компании бывает несколько наборов реквизитов; первый
                # с заполненным ИНН и берём, пустые не затирают найденное
                if inn and company_id in requisites:
                    continue
                requisites[company_id] = {
                    "inn": inn or None,
                    "kpp": str(row.get("RQ_KPP") or "").strip() or None,
                    "legal_name": str(row.get("RQ_COMPANY_NAME") or "").strip() or None,
                }
            offset = answer.get("next")
            if not offset:
                break
    return requisites


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
        company_ids = [
            row["company_id"]
            for row in connection.execute(
                """
                select distinct company_id
                from bitrix_invoices
                where company_id is not null
                order by 1
                """
            ).fetchall()
        ]
        print(f"компаний, на которые ссылаются наши счета: {len(company_ids)}")

        try:
            titles = fetch_titles(webhook, company_ids)
            requisites = fetch_requisites(webhook, company_ids)
        except BitrixCallError as error:
            print(f"Bitrix не ответил: {error}", file=sys.stderr)
            return 1

        with_inn = sum(1 for meta in requisites.values() if meta["inn"])
        print(f"названий получено: {len(titles)}, реквизитов: {len(requisites)}")
        print(f"из них с заполненным ИНН: {with_inn}")

        without_inn = [cid for cid in company_ids if not requisites.get(cid, {}).get("inn")]
        if without_inn:
            print(f"без ИНН: {len(without_inn)} — {without_inn[:10]}")

        if not arguments.apply:
            print("пробный прогон, в базу ничего не записано (нужен --apply)")
            return 0

        rows = [
            (
                company_id,
                titles.get(company_id),
                requisites.get(company_id, {}).get("legal_name"),
                requisites.get(company_id, {}).get("inn"),
                requisites.get(company_id, {}).get("kpp"),
            )
            for company_id in company_ids
        ]
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                insert into bitrix_companies (
                    bitrix_company_id, title, legal_name, inn, kpp
                ) values (%s, %s, %s, %s, %s)
                on conflict (bitrix_company_id) do update set
                    title = excluded.title,
                    legal_name = excluded.legal_name,
                    inn = excluded.inn,
                    kpp = excluded.kpp,
                    fetched_at = now()
                """,
                rows,
            )
        connection.commit()
        print(f"записано компаний: {len(rows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
