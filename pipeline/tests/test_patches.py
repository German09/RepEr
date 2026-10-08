# -*- coding: utf-8 -*-
"""Исправление на EF-коммит: состав, порядок установки, объединение коммитов, откаты, отзыв.

Запуск: python -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from efgen.gitutil import Repo  # noqa: E402
from efgen.patches import Patch, collect, methods_of, plan_all  # noqa: E402
from efgen.planner import Planner  # noqa: E402

TASK_RE = r"SYSTEM-(\d+)"
EF_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])EF(?![0-9A-Za-zА-Яа-яЁё_])"
MODULE = "УТ/src/CommonModules/ТестСервер_SYSTEM/Module.bsl"
VERSION = "УТ/src/CommonModules/ОбновлениеИнформационнойБазы_SYSTEM/Module.bsl"
CONFIG = {
    "src_root": "УТ/src",
    "task_regex": TASK_RE,
    "ef_regex": EF_RE,
    "base_tag_match": "[0-9]*",
    "extension_name": "EF_{task}_{n}",
    "method_prefix": "P_{task}_{n}_",
    "compatibility_mode": "8.3.27",
    "xml_format": "2.20",
    "edt_base_project": "УТ",
    "manifest": True,
    "applied_name": "SYSTEM",
    "version_module": "CommonModules/ОбновлениеИнформационнойБазы_SYSTEM/Module.bsl",
    "version_regex": "Описание\\.Версия\\s*=\\s*\"([^\"]+)\"",
}

RELEASE = '''
Функция Сумма(А, Б) Экспорт
\tВозврат А + Б;
КонецФункции

Функция Разность(А, Б) Экспорт
\tВозврат А - Б;
КонецФункции

Функция Произведение(А, Б) Экспорт
\tВозврат А * Б;
КонецФункции

Функция Частное(А, Б) Экспорт
\tВозврат А / Б;
КонецФункции
'''


class PatchesTest(unittest.TestCase):
    """История после релиза 1.0:
    1 SYSTEM-1 EF Сумма            - EF_1_1
    2 SYSTEM-1 EF Разность         - EF_1_2: второй EF-коммит задачи - отдельное исправление
    3 SYSTEM-9 без пометки         - не исправление, но в истории
    4 SYSTEM-2 EF Сумма            - EF_2_1: тот же метод, что в EF_1_1, ставится позже
    5 SYSTEM-3 EF Новая            - EF_3_1: добавляет функцию, которой нет в релизе
    6 SYSTEM-3 EF Новая изменена   - в EF_3_1: меняет функцию не из релиза
    7 SYSTEM-4 EF вызов Новая      - в EF_3_1: вызывает функцию не из релиза
    8 SYSTEM-6 EF Частное          - EF_6_1
    9 откат 8                       - EF_6_1 отзывается, откат вернул метод к релизу - исправления нет
    10 SYSTEM-7 EF Сумма как в релизе - EF_7_1: перехватывает Сумма, хотя текст как в релизе
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "repo")
        os.makedirs(cls.path)
        cls.sha = {}

        def git(*args):
            return subprocess.run(["git", "-C", cls.path, *args], check=True, capture_output=True)

        def commit(key, text, message):
            with open(os.path.join(cls.path, *MODULE.split("/")), "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(text)
            git("add", "-A")
            git("commit", "-qm", message)
            cls.sha[key] = git("rev-parse", "HEAD").stdout.decode().strip()

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        for rel in (MODULE, VERSION):
            os.makedirs(os.path.join(cls.path, *rel.split("/")[:-1]), exist_ok=True)
        with open(os.path.join(cls.path, *VERSION.split("/")), "w", encoding="utf-8") as f:
            f.write('Описание.Версия = "1.0.0.1";\n')
        text = RELEASE
        commit(0, text, "Релиз")
        git("tag", "1.0")

        text = text.replace("А + Б;", "А + Б + 1;")
        commit(1, text, "SYSTEM-1 EF Сумма")
        text = text.replace("А - Б;", "А - Б - 1;")
        commit(2, text, "SYSTEM-1 EF Разность")
        commit(3, text + "\n// комментарий\n", "SYSTEM-9 без пометки")
        text = text.replace("А + Б + 1;", "А + Б + 2;")
        commit(4, text, "SYSTEM-2 EF Сумма")
        text += "\nФункция Новая() Экспорт\n\tВозврат 0;\nКонецФункции\n"
        commit(5, text, "SYSTEM-3 EF Новая")
        text = text.replace("\tВозврат 0;", "\tВозврат 1;")
        commit(6, text, "SYSTEM-3 EF Новая изменена")
        text = text.replace("А * Б;", "А * Б * Новая();")
        commit(7, text, "SYSTEM-4 EF вызов Новая")
        commit(8, text.replace("А / Б;", "А / Б / 1;"), "SYSTEM-6 EF Частное")
        git("revert", "--no-edit", "HEAD")
        cls.sha[9] = git("rev-parse", "HEAD").stdout.decode().strip()
        text = text.replace("А + Б + 2;", "А + Б;")
        commit(10, text, "SYSTEM-7 EF Сумма как в релизе")

        cls.repo = Repo(cls.path)
        cls.planner = Planner(cls.repo, "УТ/src")
        cls.patches = collect(cls.repo, cls.planner, "1.0", "HEAD", TASK_RE, EF_RE)
        plan_all(cls.planner, cls.patches)
        cls.by_name = cls.patches.by_name(CONFIG["extension_name"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def methods(self, name):
        return methods_of(self.by_name[name].plan)

    def test_one_patch_per_commit(self):
        self.assertEqual(self.methods("EF_1_1"), ["CommonModule.ТестСервер_SYSTEM.Сумма"])
        self.assertEqual(self.methods("EF_1_2"), ["CommonModule.ТестСервер_SYSTEM.Разность"])
        self.assertEqual([c.sha for c in self.by_name["EF_1_2"].commits], [self.sha[2]])

    def test_method_text_is_taken_from_patch_commit(self):
        override = self.by_name["EF_1_1"].plan.modules[0].overrides[0]
        self.assertIn("А + Б + 1;", "\n".join(override.body))

    def test_order_follows_history(self):
        orders = {name: p.order for name, p in self.by_name.items()}
        self.assertEqual(orders["EF_1_1"], 1)
        self.assertEqual(orders["EF_1_2"], 2)
        self.assertEqual(orders["EF_2_1"], 4)  # коммит без пометки тоже занимает место в истории
        self.assertEqual([p.order for p in self.patches.patches], sorted(p.order for p in self.patches.patches))

    def test_commits_linked_by_method_not_in_release_are_merged(self):
        patch = self.by_name["EF_3_1"]
        self.assertEqual([c.sha for c in patch.commits], [self.sha[5], self.sha[6], self.sha[7]])
        self.assertEqual(patch.order, 7)
        self.assertEqual(sorted(self.methods("EF_3_1")),
                         ["CommonModule.ТестСервер_SYSTEM.Новая", "CommonModule.ТестСервер_SYSTEM.Произведение"])
        self.assertNotIn("EF_4_1", self.by_name)
        self.assertEqual(len(patch.merged), 2)

    def test_reverted_commit_leaves_no_patch(self):
        self.assertNotIn("EF_6_1", self.by_name)
        # Откат вернул метод к релизу, и раньше его никто не перехватывал: исправление пустое.
        self.assertTrue(self.by_name["EF_6_2"].plan.empty)

    def test_method_back_to_release_is_overridden_if_patched_before(self):
        self.assertEqual(self.methods("EF_7_1"), ["CommonModule.ТестСервер_SYSTEM.Сумма"])

    def run_build(self, *args):
        out = tempfile.mkdtemp(dir=self.tmp.name)
        config = os.path.join(out, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f)
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "ef_build.py"), "--repo", self.path,
                               "--config", config, "--out", out, *args], capture_output=True)
        result = {}
        for name in os.listdir(out):
            info = os.path.join(out, name, "ef.json")
            if os.path.exists(info):
                with open(info, encoding="utf-8") as f:
                    result[name] = json.load(f)
        return proc.returncode, result

    def test_build_commit(self):
        code, built = self.run_build("--commit", self.sha[4])
        self.assertEqual(code, 0)
        self.assertEqual(list(built), ["EF_2_1"])
        info = built["EF_2_1"]
        self.assertEqual(info["order"], 4)
        self.assertEqual(info["version"], "1.0.0.1.1")
        self.assertEqual(info["methods"], ["CommonModule.ТестСервер_SYSTEM.Сумма"])

    def test_build_range_rebuilds_merged_patch(self):
        code, built = self.run_build("--range", f"{self.sha[5]}..{self.sha[7]}")
        self.assertEqual(code, 0)
        self.assertEqual(list(built), ["EF_3_1"])
        self.assertEqual(built["EF_3_1"]["commits"], [self.sha[5], self.sha[6], self.sha[7]])

    def test_build_range_revokes_reverted_patch(self):
        code, built = self.run_build("--range", f"{self.sha[8]}..{self.sha[9]}")
        self.assertEqual(code, 0)
        self.assertEqual(list(built), ["EF_6_1"])
        self.assertTrue(built["EF_6_1"]["revoke"])

    def test_feed_revokes_patches_missing_in_history(self):
        feed = tempfile.mkdtemp(dir=self.tmp.name)
        os.makedirs(os.path.join(feed, "1.0.0.1"))
        index = {"patches": [
            {"name": "EF_1", "uuid": "a", "commits": [self.sha[1], self.sha[2]], "revoked": False},  # прежняя схема
            {"name": "EF_8_1", "uuid": "b", "commits": ["0" * 40], "revoked": False},  # коммита нет в истории
            {"name": "EF_1_1", "uuid": "c", "commits": [self.sha[1]], "revoked": False},
        ]}
        with open(os.path.join(feed, "1.0.0.1", "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f)
        code, built = self.run_build("--commit", self.sha[10], "--feed-dir", feed)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(built), ["EF_1", "EF_7_1"])
        self.assertTrue(built["EF_1"]["revoke"])


class RevertUnderLaterPatchTest(unittest.TestCase):
    """Откат коммита, поверх которого метод уже менял другой EF-коммит:
    1 SYSTEM-100 EF Сумма + 1        - отзывается откатом
    2 SYSTEM-200 EF Сумма + 1 + 2    - EF_200_1: текст метода содержит и правку задачи 100
    3 откат 1: Сумма + 2              - EF_100_2: возвращает метод без правки задачи 100 поверх EF_200_1
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = cls.tmp.name

        def git(*args):
            return subprocess.run(["git", "-C", path, *args], check=True, capture_output=True)

        def commit(text, message):
            with open(os.path.join(path, *MODULE.split("/")), "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(text)
            git("add", "-A")
            git("commit", "-qm", message)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        os.makedirs(os.path.join(path, *MODULE.split("/")[:-1]))
        # Между правками строки без изменений: иначе git revert не откатит первую правку без конфликта.
        text = "Функция Сумма(А, Б) Экспорт\n\tА = А;\n" + "\tБ = Б;\n" * 4 + "\tВозврат А + Б;\nКонецФункции\n"
        commit(text, "Релиз")
        git("tag", "1.0")
        text = text.replace("А = А;", "А = А + 1;")
        commit(text, "SYSTEM-100 EF Сумма")
        commit(text.replace("А + Б;", "А + Б + 2;"), "SYSTEM-200 EF Сумма")
        git("revert", "--no-edit", "HEAD~1")

        repo = Repo(path)
        planner = Planner(repo, "УТ/src")
        cls.patches = collect(repo, planner, "1.0", "HEAD", TASK_RE, EF_RE)
        plan_all(planner, cls.patches)
        cls.by_name = cls.patches.by_name(CONFIG["extension_name"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_revert_overrides_method_over_later_patch(self):
        self.assertEqual(sorted(self.by_name), ["EF_100_2", "EF_200_1"])
        later, revert = self.by_name["EF_200_1"], self.by_name["EF_100_2"]
        self.assertIn("А = А + 1;", "\n".join(later.plan.modules[0].overrides[0].body))
        body = "\n".join(revert.plan.modules[0].overrides[0].body)
        self.assertIn("А = А;", body)
        self.assertIn("А + Б + 2;", body)
        self.assertGreater(revert.order, later.order)

    def test_revert_marked_in_name(self):
        self.assertTrue(self.by_name["EF_100_2"].revert)
        self.assertFalse(self.by_name["EF_200_1"].revert)
        self.assertEqual(self.by_name["EF_100_2"].name("EF_{fire}{rev}{task}_{n}"), "EF_REV_100_2")


class FireNameTest(unittest.TestCase):
    """FIRE в имени расширения: по нему база находит срочное исправление и перезапускает сеансы."""

    TEMPLATE = "EF_{fire}{task}_{n}"

    def patch(self, *subjects):
        return Patch("9634", "1", 1, [SimpleNamespace(subject=s) for s in subjects])

    def test_fire_in_name(self):
        self.assertEqual(self.patch("SYSTEM-9634 EF FIRE Отчет").name(self.TEMPLATE), "EF_FIRE_9634_1")

    def test_ordinary_name(self):
        self.assertEqual(self.patch("SYSTEM-9634 EF Отчет").name(self.TEMPLATE), "EF_9634_1")

    def test_fire_in_any_commit(self):
        patch = self.patch("SYSTEM-9634 EF FIRE Отчет", "SYSTEM-9634 EF Отчет доработка")
        self.assertTrue(patch.fire)
        self.assertEqual(patch.name(self.TEMPLATE), "EF_FIRE_9634_1")

    def test_fire_inside_word_is_not_mark(self):
        self.assertFalse(self.patch("SYSTEM-9634 EF FIREWALL").fire)

    def test_template_without_fire(self):
        self.assertEqual(self.patch("SYSTEM-9634 EF FIRE Отчет").name("EF_{task}_{n}"), "EF_9634_1")

    def test_fire_and_revert(self):
        patch = self.patch('Revert "SYSTEM-9634 EF FIRE Отчет"')
        patch.reverts = ["sha"]
        self.assertEqual(patch.name("EF_{fire}{rev}{task}_{n}"), "EF_FIRE_REV_9634_1")


if __name__ == "__main__":
    unittest.main()
