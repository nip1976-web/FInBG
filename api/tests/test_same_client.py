"""Проверка сверки клиента при разноске платежей по сделкам.

Имя у фирмы бывает разное, ИНН один. Сверка по имени пропускала «ГФК» против
«Галичский Фанерный Комбинат» - это одна фирма, а по написанию они не сойдутся
никогда. При этом смягчать сверку имён нельзя: вхождение одного названия в
другое уже приводило платежи чужих клиентов в кандидаты.

Запуск - вместе с остальными:

    /opt/finbg/venv/bin/python -m unittest discover -s api/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from link_payments_by_document import (  # noqa: E402
    normalize_name,
    normalize_tax_id,
    same_client,
)


def клиент(имя: str, инн: str = "") -> tuple[str, str]:
    return normalize_name(имя), normalize_tax_id(инн)


class СверкаКлиента(unittest.TestCase):
    def test_имя_совпало_точно(self):
        deal, payment = клиент("Профлес ООО"), клиент('ООО "Профлес"')
        self.assertTrue(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_разные_имена_без_инн_не_сходятся(self):
        deal, payment = клиент("ГФК"), клиент("Галичский Фанерный Комбинат")
        self.assertFalse(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_инн_сводит_разные_написания(self):
        deal = клиент("ГФК", "4403003914")
        payment = клиент("Галичский Фанерный Комбинат ООО", "4403003914")
        self.assertTrue(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_инн_с_пробелами_и_мусором_тот_же(self):
        deal = клиент("ГФК", " 4403003914 ")
        payment = клиент("Галичский Фанерный Комбинат", "4403-003-914")
        self.assertTrue(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_разный_инн_перевешивает_совпавшее_имя(self):
        # однофамильцы: имя одно, фирмы разные - платёж к чужой сделке не уедет
        deal = клиент("Лесторг ООО", "7701111111")
        payment = клиент("Лесторг ООО", "5902222222")
        self.assertFalse(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_инн_только_у_одной_стороны_решает_имя(self):
        # так живут все платежи до августа 2026: ИНН в выписке ещё нет
        deal = клиент("Профлес ООО", "7701111111")
        payment = клиент("Профлес ООО")
        self.assertTrue(same_client(deal[0], payment[0], deal[1], payment[1]))
        other = клиент("Аспэк Ефимовский")
        self.assertFalse(same_client(deal[0], other[0], deal[1], other[1]))

    def test_короткое_имя_внутри_чужого_не_считается(self):
        deal, payment = клиент("АС ООО"), клиент("Аспэк Ефимовский ООО")
        self.assertFalse(same_client(deal[0], payment[0], deal[1], payment[1]))

    def test_пустой_клиент_ни_с_чем_не_сходится(self):
        deal, payment = клиент(""), клиент("")
        self.assertFalse(same_client(deal[0], payment[0], deal[1], payment[1]))


class НормализацияИНН(unittest.TestCase):
    def test_остаются_только_цифры(self):
        self.assertEqual(normalize_tax_id(" 4403003914 "), "4403003914")
        self.assertEqual(normalize_tax_id(4403003914), "4403003914")

    def test_пустое_остаётся_пустым(self):
        for value in (None, "", "   ", "-"):
            with self.subTest(value=value):
                self.assertEqual(normalize_tax_id(value), "")


if __name__ == "__main__":
    unittest.main()
