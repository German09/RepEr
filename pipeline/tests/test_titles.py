# -*- coding: utf-8 -*-
"""Название исправления из темы коммита.

Запуск: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from efgen.titles import commit_link, patch_title  # noqa: E402

TASK_RE = r"SYSTEM-(\d+)"
EF_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])EF(?![0-9A-Za-zА-Яа-яЁё_])"


class PatchTitleTest(unittest.TestCase):

    def check(self, subject, task, title, fire=False):
        result = patch_title(subject, task, TASK_RE, EF_RE)
        self.assertEqual(result.title, title)
        self.assertEqual(result.fire, fire)
        self.assertEqual(result.task_code, f"SYSTEM-{task}")

    def test_plain(self):
        self.check("SYSTEM-9731 EF Дата в заказе клиента", "9731",
                   "Дата в заказе клиента")

    def test_fire(self):
        self.check("SYSTEM-9634 EF FIRE Отчет", "9634", "Отчет", fire=True)

    def test_marker_at_end_and_comma(self):
        self.check("SYSTEM-9543, Расширил список складов EF", "9543", "Расширил список складов")

    def test_repeated_code(self):
        self.check("SYSTEM-9735 SYSTEM-9735 Загрузка заказа без блока delivery EF", "9735",
                   "Загрузка заказа без блока delivery")

    def test_inner_punctuation_kept(self):
        self.check("SYSTEM-8972 EF Автор = Менеджер только если Автор пустой", "8972",
                   "Автор = Менеджер только если Автор пустой")

    def test_words_with_ef_and_fire_inside(self):
        self.check("SYSTEM-1 EF Поле REF и FIREWALL", "1", "Поле REF и FIREWALL")

    def test_revert(self):
        self.check('Revert "SYSTEM-9634 EF FIRE Отчет"', "9634", "Отчет", fire=True)

    def test_revert_of_revert(self):
        self.check('Revert "Revert "SYSTEM-9634 EF Отчет""', "9634", "Отчет")

    def test_commit_link(self):
        title, url = commit_link("abc123", "SYSTEM-1 EF Текст", "https://git.example/user/system/-/commit/{sha}")
        self.assertEqual(title, "SYSTEM-1 EF Текст")
        self.assertEqual(url, "https://git.example/user/system/-/commit/abc123")

    def test_commit_link_without_template(self):
        self.assertEqual(commit_link("abc123", "Текст"), ("Текст", ""))


if __name__ == "__main__":
    unittest.main()
