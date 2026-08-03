"""Переносит подтверждённые предложения ИНН в карточки клиентов.

Берёт только строки со статусом confirmed — то есть те, которые человек
посмотрел и одобрил. Ничего не решает сам.

Порядок работы:
    1. suggest_counterparty_tax_ids.py --apply   — собрать предложения
    2. посмотреть их и отметить верные:
       update counterparty_tax_id_suggestions
          set status='confirmed', decided_at=now() where id in (...);
       неверные так же, но status='rejected' — тогда они не всплывут снова
    3. apply_confirmed_tax_ids.py --apply        — перенести в карточки

Использование:
    python apply_confirmed_tax_ids.py            # показать, что перенесётся
    python apply_confirmed_tax_ids.py --apply    # перенести
"""

from __future__ import annotations

import argparse
import os

import psycopg
from psycopg.rows import dict_row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "dbname=finbg"))
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    with psycopg.connect(arguments.database_url, row_factory=dict_row) as connection:
        ready = connection.execute(
            """
            select s.id, s.counterparty_id, s.suggested_tax_id, s.legal_name,
                   c.name as counterparty_name, c.tax_id as current_tax_id
            from counterparty_tax_id_suggestions s
            join counterparties c on c.id = s.counterparty_id
            where s.status = 'confirmed'
            order by c.name
            """
        ).fetchall()

        if not ready:
            print("подтверждённых предложений нет")
            return 0

        applied = []
        for row in ready:
            if row["current_tax_id"]:
                print(
                    f"  {row['counterparty_name']}: ИНН уже стоит "
                    f"({row['current_tax_id']}) — пропуск"
                )
                continue
            print(f"  {row['counterparty_name']} → ИНН {row['suggested_tax_id']}")
            applied.append(row)

        print(f"\nперенесётся: {len(applied)}")
        if not arguments.apply:
            print("пробный прогон, карточки не изменены (нужен --apply)")
            return 0

        with connection.cursor() as cursor:
            for row in applied:
                # ограничение «один ИНН — одна карточка» может сработать здесь:
                # значит этот ИНН уже стоит у другого клиента, и это не ошибка
                # переноса, а знак, что у нас две карточки одной фирмы
                try:
                    cursor.execute(
                        """
                        update counterparties
                        set tax_id = %(tax_id)s,
                            legal_name = coalesce(legal_name, %(legal_name)s)
                        where id = %(id)s
                        """,
                        {
                            "tax_id": row["suggested_tax_id"],
                            "legal_name": row["legal_name"],
                            "id": row["counterparty_id"],
                        },
                    )
                except psycopg.errors.UniqueViolation:
                    connection.rollback()
                    print(
                        f"  {row['counterparty_name']}: ИНН {row['suggested_tax_id']} "
                        "уже принадлежит другому клиенту — это две карточки одной "
                        "фирмы, их надо свести"
                    )
                    continue
        connection.commit()
        print(f"перенесено: {len(applied)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
