"""Проверка разбора назначения платежа - на тех случаях, что уже стоили времени.

Разбор в app/documents.py читают двое: интерфейс, который показывает номер
документа в таблице платежей, и обмен с Bitrix, который по этому номеру ищет
счёт. Ломается он молча - платёж просто перестаёт находить свой счёт, - поэтому
ловушки записаны здесь по одной, с объяснением, откуда каждая взялась.

Запуск (зависимостей не требует, база не нужна):

    /opt/finbg/venv/bin/python -m unittest discover -s api/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.documents import (  # noqa: E402
    extract_document_reference,
    invoice_numbers,
    is_bitrix_mismatch,
)


class ИзвлечениеНомераСчёта(unittest.TestCase):
    def test_номер_отделён_от_слова_словами(self):
        # «СЧЕТУ НА ОПЛАТУ № 3189» - прокладка между словом и номером обычна
        self.assertEqual(invoice_numbers("СЧЕТУ НА ОПЛАТУ № 3189 ОТ 01.02.2026"), [3189])

    def test_счёт_договор_с_латинской_n(self):
        # из-за этого написания таблица показывала счёт, а обмен его не видел
        self.assertEqual(invoice_numbers("оплата по счёт-договору N 3777"), [3777])

    def test_сокращение_сч_и_лишние_пробелы(self):
        self.assertEqual(invoice_numbers("Оплата по сч  3853 от 28.07.2026г."), [3853])

    def test_день_даты_не_является_номером(self):
        # «по счёту от 29.06.2026» - схватив 29, попали бы в чужой счёт
        self.assertEqual(invoice_numbers("по счёту от 29.06.2026"), [])

    def test_составной_номер_не_разбирается(self):
        # «по счету 29/09/7» - такого номера Bitrix не выдаёт
        self.assertEqual(invoice_numbers("оплата по счету 29/09/7"), [])

    def test_счёт_фактура_это_не_счёт(self):
        # налоговый документ, в Bitrix его нет никогда - все три написания
        for text in ("оплата по сч-ф №117 от 07.04.2026", "по сч/ф 118", "по счф 119"):
            with self.subTest(text=text):
                self.assertEqual(invoice_numbers(text), [])

    def test_счёт_берётся_даже_рядом_со_спецификацией(self):
        self.assertEqual(
            invoice_numbers("ОПЛАТА ПО СЧЕТУ 3077 ОТ 13.01.2026 АВАНС ПО СПЕЦ. № 4"),
            [3077],
        )

    def test_несколько_счетов_в_одном_платеже(self):
        self.assertEqual(invoice_numbers("ПО СЧЕТУ № 2799 И СЧЕТУ № 2881"), [2799, 2881])

    def test_счёт_фактура_через_пробел_тоже_не_счёт(self):
        # #1414: «по счет фактуре N133 ... Сч 781» - короткое «сч» проскакивало
        # мимо запрета, и номер налогового документа 133 уводил платёж к чужому
        # счёту: 133 в Bitrix есть
        self.assertEqual(
            invoice_numbers("Оплата 30% по счет фактуре N133 от 03.06.2024 г. Сч 781 - дж камень"),
            [781],
        )

    def test_перечисление_счетов_через_запятую(self):
        # #1622: «по счетам 1395, 1401» - читался только 1395, и весь платёж
        # ложился на него: счёт числился переплаченным, 1401 - неоплаченным
        self.assertEqual(
            invoice_numbers("Оплата за круги алмазные и смазку по счетам 1395, 1401"),
            [1395, 1401],
        )

    def test_перечисление_из_трёх_с_решёткой(self):
        self.assertEqual(
            invoice_numbers("ОПЛАТА ПО СЧЕТАМ № 1377, 1381, 1409 ОТ 2024 Г. ЗА ТОВАР."),
            [1377, 1381, 1409],
        )

    def test_между_номерами_стоит_дата(self):
        # #1757: «№1453 от 15.11.2024, №1613 от 24.12.2024» - дата принадлежит
        # первому счёту и список не обрывает
        self.assertEqual(
            invoice_numbers("ОПЛ. ПО СЧЕТУ №1453 ОТ 15.11.2024, №1613 ОТ 24.12.2024 ЗА МАТЕРИАЛЫ"),
            [1453, 1613],
        )

    def test_за_запятой_количество_а_не_счёт(self):
        # «3777, 2 шт» - одна цифра после запятой это штуки
        self.assertEqual(invoice_numbers("оплата по счету 3777, 2 шт"), [3777])

    def test_за_запятой_ндс_а_не_счёт(self):
        self.assertEqual(
            invoice_numbers("Оплата по счету 1234, в т.ч. НДС (20%) 1000-00"), [1234]
        )

    def test_за_запятой_дата_а_не_счёт(self):
        self.assertEqual(invoice_numbers("Оплата по счету 225, 16.10.2023 г."), [225])

    def test_договор_через_пробел_в_список_не_попадает(self):
        # продолжение только через запятую: иначе номер договора уедет в счета
        self.assertEqual(
            invoice_numbers("Оплата по счету 1469 по договору 12/05 от 12.05.2024"), [1469]
        )

    def test_номера_нет_вовсе(self):
        self.assertEqual(invoice_numbers("Оплата по договору 23.05 от 22.05.2024"), [])

    def test_пустое_назначение(self):
        self.assertEqual(invoice_numbers(None), [])


class ВидНомерИДатаДокумента(unittest.TestCase):
    def test_счёт_с_датой(self):
        self.assertEqual(
            extract_document_reference("АВАНС ПО СЧЕТУ № 3265 ОТ 02.03.2026Г."),
            ("invoice", "3265", "02.03.2026"),
        )

    def test_спецификация_с_датой(self):
        self.assertEqual(
            extract_document_reference("50% предоплата по СП N43 от 27.01.2025"),
            ("spec", "43", "27.01.2025"),
        )

    def test_счёт_важнее_спецификации(self):
        # привязка к Bitrix идёт по счёту, поэтому он и выигрывает
        kind, number, _ = extract_document_reference(
            "по счету N1647 от 14.07.2022 по спецификации N24"
        )
        self.assertEqual((kind, number), ("invoice", "1647"))


class РасхождениеСBitrix(unittest.TestCase):
    def test_счёт_совпал(self):
        self.assertFalse(is_bitrix_mismatch("invoice", "3265", 3265))

    def test_bitrix_записал_другой_счёт(self):
        self.assertTrue(is_bitrix_mismatch("invoice", "3265", 3266))

    def test_bitrix_счёт_не_подтвердил(self):
        self.assertTrue(is_bitrix_mismatch("invoice", "3265", None))

    def test_спецификация_расхождением_не_считается(self):
        self.assertFalse(is_bitrix_mismatch("spec", "43", None))


if __name__ == "__main__":
    unittest.main()
