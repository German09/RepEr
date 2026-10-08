# -*- coding: utf-8 -*-
"""Запуск: python -m unittest discover -s ci/ef/tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from efgen.bsl import parse_module, rename_in_header  # noqa: E402

MODULE = '''
Перем Кэш;

#Область ПрограммныйИнтерфейс

// Описание метода.
//
// Параметры:
//  А - Число
Процедура Первая(А, Знач Б = ")",
		В = Неопределено) Экспорт // АПК:142
	Текст = "КонецПроцедуры
	|Процедура Ложная()
	|КонецПроцедуры"; // "кавычка в комментарии
	Сообщить(Текст);
КонецПроцедуры

#КонецОбласти

#Если Сервер Или ВнешнееСоединение Тогда

&НаСервере
Асинх Функция Вторая()
	Возврат 1; // КонецФункции
КонецФункции

#Иначе

Функция Третья() Экспорт Возврат 2; КонецФункции

#КонецЕсли

Кэш = Новый Соответствие;
'''


class ParseModuleTest(unittest.TestCase):
    def setUp(self):
        self.module = parse_module(MODULE)
        self.methods = self.module.by_key()

    def test_finds_all_methods(self):
        self.assertEqual([m.name for m in self.module.methods], ["Первая", "Вторая", "Третья"])

    def test_keywords_inside_strings_and_comments_are_ignored(self):
        first = self.methods["первая"]
        self.assertEqual(len(first.body), 4)
        self.assertIn("Сообщить(Текст);", first.body[-1])

    def test_multiline_header_with_bracket_in_string(self):
        first = self.methods["первая"]
        self.assertTrue(first.export)
        self.assertTrue(first.header.endswith("В = Неопределено)"))
        self.assertEqual(first.header_tail.strip(), "// АПК:142")
        self.assertEqual(len(first.doc), 4)

    def test_annotations_async_and_preprocessor_context(self):
        second = self.methods["вторая"]
        self.assertTrue(second.is_async and second.is_function)
        self.assertEqual(second.annotations, ["&НаСервере"])
        self.assertEqual(second.pp_context, ("Сервер Или ВнешнееСоединение",))
        self.assertEqual(self.methods["третья"].pp_context, ("НЕ (Сервер Или ВнешнееСоединение)",))

    def test_single_line_method(self):
        third = self.methods["третья"]
        self.assertEqual(third.body, [" Возврат 2; "])
        self.assertEqual(third.end_line, "КонецФункции")

    def test_residue_keeps_module_level_code_only(self):
        self.assertIn("Перем Кэш;", self.module.residue)
        self.assertIn("Кэш = Новый Соответствие;", self.module.residue)
        self.assertNotIn("Область", self.module.residue)
        self.assertNotIn("Сообщить", self.module.residue)

    def test_norm_ignores_comments_and_indentation(self):
        changed = parse_module(MODULE.replace("\tСообщить(Текст);", "    Сообщить(Текст);   // правка комментария"))
        self.assertEqual(changed.by_key()["первая"].norm, self.methods["первая"].norm)
        changed = parse_module(MODULE.replace("Сообщить(Текст);", "Сообщить(Текст + 1);"))
        self.assertNotEqual(changed.by_key()["первая"].norm, self.methods["первая"].norm)

    def test_rename_in_header(self):
        self.assertTrue(rename_in_header(self.methods["вторая"].header, "P_1_Вторая").startswith("Асинх Функция P_1_Вторая("))


if __name__ == "__main__":
    unittest.main()
