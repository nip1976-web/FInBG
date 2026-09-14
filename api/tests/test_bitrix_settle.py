"""Проверка остатка по счёту перед выгрузкой оплат в Bitrix.

Счёт в евро оплачивают рублями; при переводе обратно по курсу ЦБ сумма частей
расходится со счётом на копейки. Такой шум не должен останавливать выгрузку как
«переплата», а настоящая переплата - должна: её разносит человек.

Запуск - вместе с остальными:

    /opt/finbg/venv/bin/python -m unittest discover -s api/tests
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from sync_bitrix_invoice_payments import settle, vat_topup  # noqa: E402

D = Decimal


class ОстатокПоСчёту(unittest.TestCase):
    def test_оплачен_ровно(self):
        self.assertEqual(settle(D("6527.00"), D("6527.00")), D("0.00"))

    def test_частичная_оплата_даёт_остаток(self):
        self.assertEqual(settle(D("6527.00"), D("4226.50")), D("2300.50"))

    def test_недобор_в_копейку_считается_оплатой(self):
        self.assertEqual(settle(D("13883.76"), D("13883.75")), D("0.00"))

    def test_копеечная_переплата_не_останавливает(self):
        # реальные случаи: 12270,00 против 12270,01 и 3724,76 против 3724,93
        for total, paid in (("12270.00", "12270.01"), ("3724.76", "3724.93")):
            with self.subTest(total=total, paid=paid):
                self.assertEqual(settle(D(total), D(paid)), D("0.00"))

    def test_переплата_ровно_на_допуске_ещё_шум(self):
        self.assertEqual(settle(D("100.00"), D("102.00")), D("0.00"))

    def test_недобор_на_допуске_тоже_шум(self):
        # Сорб 1421: два платежа по курсу ЦБ+1%, каждый округлён по-своему,
        # вместе не хватает 1,80 - это арифметика, а не долг клиента
        self.assertEqual(settle(D("21399.84"), D("21398.04")), D("0.00"))

    def test_недобор_больше_допуска_остаётся_долгом(self):
        self.assertEqual(settle(D("100.00"), D("97.99")), D("2.01"))

    def test_настоящая_переплата_уходит_человеку(self):
        # Профлес 3853: к счёту привязан и возвращённый клиенту платёж
        self.assertIsNone(settle(D("90951.00"), D("181902.00")))
        # самая мелкая из настоящих - 35,94 сверх счёта 673 (счёт 3467)
        self.assertIsNone(settle(D("673.00"), D("708.94")))

    def test_переплата_чуть_больше_допуска_уходит_человеку(self):
        self.assertIsNone(settle(D("100.00"), D("102.01")))


class ДоплатаНДС(unittest.TestCase):
    """С 2026 года ставка 20% -> 22%; по старому счёту клиент доплачивает разницу."""

    def test_считает_доплату_по_счетам_со_ставкой_20(self):
        # четыре реальных случая: счёт, налог в счёте, ожидаемая доплата
        for total, tax, expected in (
            ("99759.00", "16626.50", "1662.65"),
            ("6489.50", "1081.58", "108.16"),
            ("10496.00", "1749.33", "174.93"),
            ("2160.00", "360.00", "36.00"),
        ):
            with self.subTest(total=total):
                self.assertEqual(vat_topup(D(total), D(tax)), D(expected))

    def test_доплата_закрывает_счёт_в_пределах_допуска(self):
        # Лейтц 2875: заплатили 10 670,94 при счёте 10 496 и доплате 174,93 -
        # копейка сверху, и это округление
        total, topup = D("10496.00"), vat_topup(D("10496.00"), D("1749.33"))
        self.assertEqual(settle(total + topup, D("10670.94")), D("0.00"))

    def test_счёт_по_новой_ставке_доплаты_не_имеет(self):
        # 22% внутри суммы - это 18,03%, а не шестая часть
        self.assertIsNone(vat_topup(D("122.00"), D("22.00")))

    def test_без_налога_в_карточке_не_гадаем(self):
        self.assertIsNone(vat_topup(D("1000.00"), None))
        self.assertIsNone(vat_topup(D("0.00"), D("0.00")))


if __name__ == "__main__":
    unittest.main()
