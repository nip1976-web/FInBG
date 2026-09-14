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

from sync_bitrix_invoice_payments import (  # noqa: E402
    manual_split,
    settle,
    split_shares,
    vat_topup,
)

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


class ДелениеПлатежаМеждуСчетами(unittest.TestCase):
    """Одной платёжкой закрывают несколько счетов; делим, только если сходится."""

    def test_профлес_три_счёта_одной_платёжкой(self):
        # #1467: 142 920 = 27 960 (счёт 1057) + 72 144 (1053) + 42 816 (1051)
        totals = [D("27960.00"), D("72144.00"), D("42816.00")]
        self.assertEqual(split_shares(D("142920.00"), totals), totals)

    def test_союзбалткомплект_два_счёта(self):
        # #1930: 121 200 = 58 800 (счёт 1981) + 62 400 (1989)
        totals = [D("58800.00"), D("62400.00")]
        self.assertEqual(split_shares(D("121200.00"), totals), totals)

    def test_копейка_расхождения_не_мешает(self):
        # пересчёт валютного счёта в рубли даёт копеечный хвост
        self.assertIsNotNone(split_shares(D("100001.50"), [D("60000.00"), D("40000.00")]))

    def test_не_сходится_значит_человеку(self):
        # #1501: 63 460 против 15 800 + 42 800 - недостаёт 4 860, делить нельзя
        self.assertIsNone(split_shares(D("63460.00"), [D("15800.00"), D("42800.00")]))

    def test_платёж_больше_суммы_счетов_тоже_человеку(self):
        self.assertIsNone(split_shares(D("200000.00"), [D("60000.00"), D("40000.00")]))

    def test_один_счёт_не_делится(self):
        self.assertIsNone(split_shares(D("27960.00"), [D("27960.00")]))

    def test_пустой_счёт_в_списке_останавливает(self):
        # счёт с нулевой суммой в Bitrix (как 257) делить не по чему
        self.assertIsNone(split_shares(D("60000.00"), [D("60000.00"), D("0.00")]))


class РучноеДеление(unittest.TestCase):
    """Счета и суммы называет человек, но сойтись они обязаны так же."""

    @staticmethod
    def платёж(amount: str) -> dict:
        return {"id": 1955, "amount_rub": D(amount), "amount": D(amount), "currency": "RUB"}

    @staticmethod
    def доли(*pairs) -> list[dict]:
        return [{"bitrix_invoice_id": number, "amount_rub": D(amount)} for number, amount in pairs]

    def test_промлес_четыре_счёта_с_частичной_долей(self):
        # #1955: 1991 и 1947 названы, 1597 - «задолженность» с карточки-двойника,
        # 1727 оплачен частью: 13 140 из 16 080
        parts = manual_split(
            self.платёж("412014.00"),
            self.доли((1991, "7224.00"), (1947, "314250.00"), (1597, "77400.00"), (1727, "13140.00")),
        )
        self.assertIsNotNone(parts)
        self.assertEqual([number for number, _ in parts], [1991, 1947, 1597, 1727])
        self.assertEqual([share["amount_rub"] for _, share in parts],
                         [D("7224.00"), D("314250.00"), D("77400.00"), D("13140.00")])

    def test_каждая_доля_знает_весь_платёж(self):
        # в комментарий Bitrix идёт и доля, и сумма всей платёжки
        parts = manual_split(self.платёж("230234.00"), self.доли((741, "163734.00"), (745, "66500.00")))
        for _, share in parts:
            self.assertEqual(share["split_total_rub"], D("230234.00"))
            self.assertEqual(share["split_invoices"], [741, 745])

    def test_описка_в_сумме_не_проходит(self):
        self.assertIsNone(
            manual_split(self.платёж("63460.00"), self.доли((1131, "15800.00"), (869, "42800.00")))
        )

    def test_копейка_расхождения_прощается(self):
        self.assertIsNotNone(
            manual_split(self.платёж("100.00"), self.доли((1, "60.00"), (2, "41.00")))
        )

    def test_один_счёт_тоже_можно(self):
        # человек вправе назвать и один счёт - это просто перевод платежа
        parts = manual_split(self.платёж("63000.00"), self.доли((2899, "63000.00")))
        self.assertEqual([number for number, _ in parts], [2899])


if __name__ == "__main__":
    unittest.main()
