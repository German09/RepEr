# -*- coding: utf-8 -*-
"""Рабочая область EDT релиза: обновление исходников разницей между релизами вместо подготовки с нуля.

Запуск: python -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ef_check  # noqa: E402
from efgen.gitutil import Repo  # noqa: E402

# Релиз 1.0 -> 2.0: модуль изменен, объект Старый удален целиком, объект Новый добавлен.
RELEASE_1 = {
    "УТ/.project": "<projectDescription><name>УТ</name></projectDescription>",
    "УТ/src/CommonModules/Общий/Module.bsl": "Процедура А()\nКонецПроцедуры\n",
    "УТ/src/CommonModules/Старый/Старый.mdo": "<mdclass/>",
    "УТ/src/CommonModules/Старый/Module.bsl": "Процедура Б()\nКонецПроцедуры\n",
}
RELEASE_2 = {
    "УТ/.project": RELEASE_1["УТ/.project"],
    "УТ/src/CommonModules/Общий/Module.bsl": "Процедура А()\n\tВ();\nКонецПроцедуры\n",
    "УТ/src/CommonModules/Новый/Новый.mdo": "<mdclass/>",
    "УТ/src/CommonModules/Новый/Module.bsl": "Процедура В()\nКонецПроцедуры\n",
}


def tree(root: str) -> dict:
    result = {}
    for folder, _, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                result[os.path.relpath(path, root).replace(os.sep, "/")] = f.read()
    return result


class WorkspaceUpdateTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "repo")
        os.makedirs(cls.path)

        def git(*args):
            return subprocess.run(["git", "-C", cls.path, *args], check=True, capture_output=True)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        for tag, files in (("1.0", RELEASE_1), ("2.0", RELEASE_2)):
            git("rm", "-rq", "--ignore-unmatch", ".")
            for rel, text in files.items():
                target = os.path.join(cls.path, *rel.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            git("add", "-A")
            git("commit", "-qm", tag)
            git("tag", tag)
        cls.repo = Repo(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.workspace = os.path.join(self.work.name, "ws")
        self.sources = self.workspace + "-src"
        self.scripts = []

    def tearDown(self):
        self.work.cleanup()

    def prepare(self, ref: str, code: int = 0):
        def run_cli(cli, workspace, lines, script, timeout, vmargs=""):
            # Каталог рабочей области создает EDT.
            os.makedirs(workspace, exist_ok=True)
            self.scripts.append(lines)
            return code
        with mock.patch.object(ef_check, "run_cli", run_cli):
            ef_check.prepare_workspace(self.repo, "1cedtcli", self.workspace, ref, "УТ", 60)

    def project(self) -> dict:
        return tree(os.path.join(self.sources, "УТ"))

    def test_blobs(self):
        blobs = self.repo.blobs("2.0", ["УТ/src/CommonModules/Новый/Module.bsl", "УТ/.project"])
        self.assertEqual(blobs["УТ/src/CommonModules/Новый/Module.bsl"].decode("utf-8"), "Процедура В()\nКонецПроцедуры\n")
        self.assertEqual(len(blobs), 2)

    def test_first_prepare_imports(self):
        self.prepare("1.0")
        self.assertIn("import", self.scripts[0][0])
        self.assertEqual(self.project(), {k[3:]: v for k, v in RELEASE_1.items()})

    def test_next_release_updates_sources(self):
        self.prepare("1.0")
        self.prepare("2.0")
        self.assertEqual(self.scripts[1], ["build -y true УТ"])
        self.assertEqual(self.project(), {k[3:]: v for k, v in RELEASE_2.items()})
        # Каталог удаленного объекта не остается пустым: EDT счел бы его объектом без описания.
        self.assertFalse(os.path.exists(os.path.join(self.sources, "УТ", "src", "CommonModules", "Старый")))
        with open(os.path.join(self.workspace, ef_check.MARKER), encoding="utf-8") as f:
            marker = json.load(f)
        self.assertEqual((marker["ref"], marker["updated_from"]), ("2.0", "1.0"))

    def test_back_to_previous_release(self):
        self.prepare("1.0")
        self.prepare("2.0")
        self.prepare("1.0")
        self.assertEqual(self.scripts[2], ["build -y true УТ"])
        self.assertEqual(self.project(), {k[3:]: v for k, v in RELEASE_1.items()})

    def test_same_release_does_nothing(self):
        self.prepare("1.0")
        self.prepare("1.0")
        self.assertEqual(len(self.scripts), 1)

    def test_failed_build_falls_back_to_import(self):
        self.prepare("1.0")
        self.prepare("2.0", code=1)
        self.assertEqual(self.scripts[1], ["build -y true УТ"])
        self.assertIn("import", self.scripts[2][0])
        self.assertEqual(self.project(), {k[3:]: v for k, v in RELEASE_2.items()})

    def test_full_rebuild_on_request(self):
        self.prepare("1.0")
        with mock.patch.dict(os.environ, {"EF_EDT_FULL": "1"}):
            self.prepare("2.0")
        self.assertIn("import", self.scripts[1][0])

    def test_too_many_changes_falls_back_to_import(self):
        self.prepare("1.0")
        with mock.patch.object(ef_check, "INCREMENTAL_LIMIT", 1):
            self.prepare("2.0")
        self.assertIn("import", self.scripts[1][0])


if __name__ == "__main__":
    unittest.main()
