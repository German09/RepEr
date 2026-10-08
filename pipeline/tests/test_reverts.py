# -*- coding: utf-8 -*-
"""Откаты EF-коммитов: откаченный коммит не входит в исправления, отзыв всех сборок исправления в ленте.

Запуск: python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ef_publish import revoke, upsert  # noqa: E402
from efgen.gitutil import Repo  # noqa: E402
from efgen.planner import drop_reverts, select_commits  # noqa: E402

TASK_RE = r"SYSTEM-(\d+)"
EF_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])EF(?![0-9A-Za-zА-Яа-яЁё_])"
MODULE = "УТ/src/CommonModules/ТестСервер_SYSTEM/Module.bsl"
VERSION = "УТ/src/CommonModules/ОбновлениеИнформационнойБазы_SYSTEM/Module.bsl"

RELEASE = '''
Функция Сумма(А, Б) Экспорт
\tВозврат А + Б;
КонецФункции

Функция Разность(А, Б) Экспорт
\tВозврат А - Б;
КонецФункции
'''


class RevertTest(unittest.TestCase):
    """История: релиз 1.0 (с EF-коммитом задачи 5 внутри), после него:
    задача 1 - EF и его откат; задача 2 - два EF и откат второго;
    задача 3 - EF, откат, откат отката; задача 5 - откат коммита из релиза;
    задача 6 - коммит с темой отката, но без ссылки на откатываемый коммит.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = cls.tmp.name

        def git(*args):
            return subprocess.run(["git", "-C", cls.path, *args], check=True, capture_output=True)

        def commit(text, message):
            full = os.path.join(cls.path, *MODULE.split("/"))
            with open(full, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(text)
            git("add", "-A")
            git("commit", "-qm", message)

        def revert():
            git("revert", "--no-edit", "HEAD")

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        for rel in (MODULE, VERSION):
            os.makedirs(os.path.join(cls.path, *rel.split("/")[:-1]), exist_ok=True)
        with open(os.path.join(cls.path, *VERSION.split("/")), "w", encoding="utf-8") as f:
            f.write('Описание.Версия = "1.0.0.1";\n')
        text = RELEASE
        commit(text, "Релиз")
        text = text.replace("А - Б;", "А - Б - 0;")
        commit(text, "SYSTEM-5 EF Разность в релизе")
        git("tag", "1.0")

        revert()  # задача 5: откат коммита из релиза
        text = text.replace("А - Б - 0;", "А - Б;")
        commit(text.replace("А + Б;", "А + Б + 1;"), "SYSTEM-1 EF Сумма")
        revert()
        commit(text.replace("А + Б;", "А + Б + 2;"), "SYSTEM-2 EF Сумма")
        commit(text.replace("А + Б;", "А + Б + 2;").replace("А - Б;", "А - Б - 2;"), "SYSTEM-2 EF Разность")
        revert()
        commit(text.replace("А + Б;", "А + Б + 3;"), "SYSTEM-3 EF Сумма")
        revert()
        revert()
        commit(text.replace("А + Б;", "А + Б + 6;"), 'Revert "SYSTEM-6 EF Сумма"')  # ручной текст без хэша

        cls.repo = Repo(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def task(self, task):
        commits = select_commits(self.repo, "1.0", "HEAD", TASK_RE, EF_RE, task)[1]
        return commits, drop_reverts(self.repo, commits)

    def test_reverted_commit_leaves_nothing(self):
        commits, (kept, notes) = self.task("1")
        self.assertEqual(len(commits), 2)
        self.assertEqual(kept, [])
        self.assertEqual(len(notes), 1)

    def test_partial_revert_keeps_other_commits(self):
        commits, (kept, _) = self.task("2")
        self.assertEqual([c.subject for c in kept], ["SYSTEM-2 EF Сумма"])

    def test_revert_of_revert_restores_commit(self):
        _, (kept, _) = self.task("3")
        self.assertEqual([c.subject for c in kept], ["SYSTEM-3 EF Сумма"])

    def test_revert_of_release_change_is_not_transferred(self):
        commits, (kept, notes) = self.task("5")
        self.assertEqual(len(commits), 1)
        self.assertEqual(kept, [])
        self.assertIn("нет среди EF-коммитов", notes[0])

    def test_revert_without_reference_is_kept_with_note(self):
        commits, (kept, notes) = self.task("6")
        self.assertEqual(kept, commits)
        self.assertIn("похож на откат", notes[0])


class RevokeFeedTest(unittest.TestCase):
    def test_all_builds_are_revoked(self):
        index = {}
        upsert(index, {"uuid": "a", "name": "EF_1", "revoked": False})
        upsert(index, {"uuid": "b", "name": "EF_1", "revoked": False})
        upsert(index, {"uuid": "c", "name": "EF_2", "revoked": False})
        self.assertEqual(revoke(index, "EF_1"), 1)
        self.assertEqual({p["uuid"]: p["revoked"] for p in index["patches"]}, {"a": True, "b": True, "c": False})
        self.assertEqual(revoke(index, "EF_1"), 0)
        self.assertEqual(revoke({}, "EF_1"), 0)


if __name__ == "__main__":
    unittest.main()
