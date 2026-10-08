# -*- coding: utf-8 -*-
"""Сквозная проверка генератора на временном репозитории: релиз, EF-коммиты, чужой коммит.

Запуск: python -m unittest discover -s ci/ef/tests
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from efgen.gitutil import Repo  # noqa: E402
from efgen.planner import Planner, select_commits  # noqa: E402

TASK_RE = r"SYSTEM-(\d+)"
EF_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])EF(?![0-9A-Za-zА-Яа-яЁё_])"
MODULE = "УТ/src/CommonModules/ТестСервер_SYSTEM/Module.bsl"

RELEASE = '''
// Сумма двух чисел.
Функция Сумма(А, Б) Экспорт
\tВозврат А + Б;
КонецФункции

Процедура Лог(Текст) Экспорт
\tСообщить(Текст);
КонецПроцедуры
'''


class PlannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = cls.tmp.name

        def git(*args):
            subprocess.run(["git", "-C", cls.path, *args], check=True, capture_output=True)

        def commit(text, message):
            full = os.path.join(cls.path, *MODULE.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(text)
            git("add", "-A")
            git("commit", "-qm", message)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        text = RELEASE
        commit(text, "Релиз")
        git("tag", "1.0")
        text = text.replace("\tВозврат А + Б;", "\tВозврат А + Б + Новая();") + \
            "\nФункция Новая()\n\tВозврат 0;\nКонецФункции\n"
        commit(text, "SYSTEM-1 EF Сумма")
        text += "\nФункция НеВРелизе() Экспорт\n\tВозврат 1;\nКонецФункции\n"
        commit(text, "SYSTEM-3 функция без пометки")
        text = text.replace("\tСообщить(Текст);", "\tСообщить(Текст + НеВРелизе());")
        commit(text, "SYSTEM-2 EF Лог")
        text = text.replace("\tВозврат А + Б + Новая();", "\tВозврат А + Б + Новая(); // комментарий")
        commit(text, "SYSTEM-1 EF только комментарий")
        text = text.replace("Новая(); // комментарий", "Новая() + 0; // комментарий")
        commit(text, "SYSTEM-4 правка без пометки")

        cls.repo = Repo(cls.path)
        cls.planner = Planner(cls.repo, "УТ/src")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def plan(self, task, pull_deps=False):
        task, commits = select_commits(self.repo, "1.0", "HEAD", TASK_RE, EF_RE, task)
        return self.planner.build(task, "1.0", "HEAD", commits, pull_deps=pull_deps), commits

    def test_commits_of_task_are_selected_by_marker(self):
        _, commits = self.plan("1")
        self.assertEqual([c.subject for c in commits], ["SYSTEM-1 EF Сумма", "SYSTEM-1 EF только комментарий"])

    def test_changed_method_is_overridden_and_new_one_added(self):
        plan, _ = self.plan("1")
        module = plan.modules[0]
        self.assertEqual([m.name for m in module.overrides], ["Сумма"])
        self.assertEqual([m.name for m in module.additions], ["Новая"])
        self.assertFalse(plan.unsupported)

    def test_missing_dependency_is_reported(self):
        plan, _ = self.plan("2")
        self.assertEqual([m.name for m in plan.modules[0].overrides], ["Лог"])
        self.assertTrue(any("НеВРелизе" in w and "нет в релизе" in w for w in plan.warnings))

    def test_missing_dependency_is_pulled_on_request(self):
        plan, _ = self.plan("2", pull_deps=True)
        self.assertIn("НеВРелизе", [m.name for m in plan.modules[0].additions])

    def test_foreign_changes_of_same_method_are_reported(self):
        plan, _ = self.plan("1")
        self.assertTrue(any("вне этой задачи" in w and "SYSTEM-4" in w for w in plan.warnings))
        plan, _ = self.plan("2")
        self.assertFalse(any("вне этой задачи" in w for w in plan.warnings))


if __name__ == "__main__":
    unittest.main()
