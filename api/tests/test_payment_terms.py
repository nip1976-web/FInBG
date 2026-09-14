"""Проверка чтения курса и суммы в евро из назначения платежа.

Разбор нарочно щедрый: он отдаёт всех кандидатов, а отбор идёт по
правдоподобию против курса ЦБ уже в выгрузке. Поэтому здесь проверяется и то,
что нужное найдено, и то, что мусор (обрывок даты, сумма чужой спецификации)
приходит отдельным кандидатом, а не подменяет нужное.

Запуск (зависимостей не требует, база не нужна):

    /opt/finbg/venv/bin/python -m unittest discover -s api/tests
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.payment_terms import stated_amounts, stated_rates  # noqa: E402

D = Decimal


class СуммаВЕвро(unittest.TestCase):
    def test_сумма_с_пробелом_и_точкой(self):
        # Сорб, счёт 1393: платёж закрывает счёт ровно, хотя по курсу ЦБ
        # выглядит переплатой на 212,87 EUR
        self.assertEqual(
            stated_amounts("за комплектующие (21 368.40 евро по курсу 107,20)"),
            [D("21368.40")],
        )

    def test_целая_сумма(self):
        self.assertEqual(
            stated_amounts("частичная оплата (14000 евро по курсу 106,63)"), [D("14000")]
        )

    def test_сокращение_экв(self):
        self.assertEqual(
            stated_amounts("СЧЁТ №3469 ОТ 10.04.26 ЭКВ 2462,40 ЕВРО ОПЛАТА В РУБЛЯХ"),
            [D("2462.40")],
        )

    def test_сумма_после_слова(self):
        self.assertEqual(
            stated_amounts("(Евро 4122; курс 28.10.24г.- 104,8094)"), [D("4122")]
        )

    def test_курс_евро_числом_попадает_в_кандидаты(self):
        # «КУРС ЕВРО 98,4099» читается и как сумма - отбраковывать это должен
        # вызывающий: 98,41 евро при платеже в сотни тысяч рублей невозможны
        self.assertEqual(
            stated_amounts("ДЕРЖАТЕЛЬ НОЖА КУРС ЕВРО 98,4099"), [D("98.4099")]
        )

    def test_без_суммы(self):
        self.assertEqual(stated_amounts("Оплата по счету № 3163 от 03.02.26"), [])
        self.assertEqual(stated_amounts(None), [])

    def test_курс_на_день_оплаты_без_числа(self):
        # «6940 евро по курсу ЦБ РФ на день оплаты» - сумма есть, курса нет
        text = "за ТМЦ (6940 евро по курсу ЦБ РФ на день оплаты)"
        self.assertEqual(stated_amounts(text), [D("6940")])
        self.assertEqual(stated_rates(text), [])


class Курс(unittest.TestCase):
    def test_курс_в_скобках(self):
        self.assertEqual(
            stated_rates("ПО СЧЕТУ № 397 ОТ 25.01.2024 Г.(КУРС 96,1657)"), [D("96.1657")]
        )

    def test_курс_через_равно(self):
        self.assertEqual(
            stated_rates("по счету №651 от 16.04.24 курс евро=99.9341"), [D("99.9341")]
        )

    def test_курс_цб_рф_с_датой_впереди(self):
        # «по курсу на 28.08.2024 102,2911»: 28.08 не курс, дата отсекается
        self.assertIn(
            D("102.2911"), stated_rates("за ножи, по курсу на 28.08.2024 102,2911 Сумма 60070-45")
        )

    def test_дата_не_становится_курсом(self):
        self.assertNotIn(
            D("28.08"), stated_rates("за ножи, по курсу на 28.08.2024 102,2911 Сумма 60070-45")
        )

    def test_курс_с_надбавкой_по_договору(self):
        # Сорб: 107,20 - это курс ЦБ 106,1426 плюс 1% по договору
        self.assertEqual(stated_rates("(21 368.40 евро по курсу 107,20)"), [D("107.20")])

    def test_курса_нет(self):
        self.assertEqual(stated_rates("Оплата по счету № 3163 от 03.02.26"), [])
        self.assertEqual(stated_rates(None), [])


if __name__ == "__main__":
    unittest.main()
