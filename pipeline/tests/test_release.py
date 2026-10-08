# -*- coding: utf-8 -*-
"""Сборка .cf релиза: тег из события веб-хука, задачи и описание релиза.

Запуск: python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from efgen.gitutil import Commit  # noqa: E402
from release_cf import release_env, release_notes, stale_releases, tag_from_payload, tasks_of, version_key  # noqa: E402

SHA = "a" * 40
TASK = r"SYSTEM-(\d+)"


class FakeRepo:
    def __init__(self, tags: dict[str, list[str]]):
        self.tags = tags

    def tags_at(self, sha: str) -> list[str]:
        return self.tags.get(sha, [])


class TagFromPayloadTest(unittest.TestCase):

    def test_tag_from_ref(self):
        payload = {"object_kind": "tag_push", "ref": "refs/tags/3.20", "after": SHA}
        self.assertEqual(tag_from_payload(payload, FakeRepo({})), "3.20")

    def test_ref_overridden_by_trigger(self):
        # ref из адреса триггера перекрывает ref события: тег ищется по объекту.
        payload = {"object_kind": "tag_push", "ref": "main", "after": SHA}
        self.assertEqual(tag_from_payload(payload, FakeRepo({SHA: ["3.20"]})), "3.20")

    def test_push_is_not_tag(self):
        payload = {"object_kind": "push", "ref": "refs/heads/master", "after": SHA}
        self.assertIsNone(tag_from_payload(payload, FakeRepo({})))

    def test_deleted_tag(self):
        payload = {"object_kind": "tag_push", "ref": "refs/tags/3.20", "after": "0" * 40}
        self.assertIsNone(tag_from_payload(payload, FakeRepo({})))

    def test_ambiguous_tag(self):
        payload = {"object_kind": "tag_push", "ref": "main", "after": SHA}
        with self.assertRaises(SystemExit):
            tag_from_payload(payload, FakeRepo({SHA: ["3.20", "3.20.1"]}))


class NotesTest(unittest.TestCase):

    def setUp(self):
        self.commits = [
            Commit("1" * 40, "SYSTEM-9794 Индексы EF", "user", "2026-10-01"),
            Commit("2" * 40, "SYSTEM-9731, SYSTEM-9794 Исправление", "user", "2026-10-02"),
            Commit("3" * 40, "Слияние без задачи", "user", "2026-10-03"),
        ]

    def test_tasks_in_first_mention_order(self):
        self.assertEqual(tasks_of(self.commits, TASK), ["SYSTEM-9794", "SYSTEM-9731"])

    def test_notes(self):
        head = self.commits[-1]
        notes = release_notes("3.20", head, "1.3.20.01", "3.19", self.commits, tasks_of(self.commits, TASK),
                              "SYSTEM_3.20.cf", 1234567, "f" * 64, "2026-10-05 20:00")
        self.assertIn("# Релиз 3.20", notes)
        self.assertIn("- Версия: 1.3.20.01", notes)
        self.assertIn("1 234 567 байт", notes)
        self.assertIn("## Задачи после 3.19 (2)", notes)
        self.assertIn("## Коммиты после 3.19 (3)", notes)

    def test_first_release_without_previous(self):
        notes = release_notes("3.01", self.commits[0], "", None, [], [], "SYSTEM_3.01.cf", 1, "f" * 64, "")
        self.assertIn("- Предыдущий релиз: нет", notes)
        self.assertNotIn("## Задачи", notes)


class RetentionTest(unittest.TestCase):

    TAGS = ["3.9", "3.16", "3.16.02", "3.17.2", "3.18", "3.19", "3.20"]

    def test_version_order(self):
        # По номеру, а не по строке: 3.9 старше 3.16, 3.16.02 новее 3.16.
        self.assertEqual(sorted(["3.16.02", "3.9", "3.17.2", "3.16"], key=version_key),
                         ["3.9", "3.16", "3.16.02", "3.17.2"])

    def test_keep_last_five(self):
        self.assertEqual(stale_releases(self.TAGS, 5, "3.20"), ["3.16", "3.9"])

    def test_rebuilt_old_release_is_kept(self):
        # Ручная пересборка старого тега: его только что собрали, удалять сразу нельзя.
        self.assertEqual(stale_releases(self.TAGS, 5, "3.9"), ["3.16"])

    def test_zero_keeps_all(self):
        self.assertEqual(stale_releases(self.TAGS, 0, "3.20"), [])

    def test_fewer_than_keep(self):
        self.assertEqual(stale_releases(["3.19", "3.20"], 5, "3.20"), [])


class ReleaseEnvTest(unittest.TestCase):

    def test_env_for_load_job(self):
        cf = os.path.join("D:" + os.sep, "CF", "3.20", "SYSTEM_3.20.cf")
        self.assertEqual(release_env("3.20", cf), "RELEASE_TAG=3.20\nRELEASE_CF=D:/CF/3.20/SYSTEM_3.20.cf\n")


if __name__ == "__main__":
    unittest.main()
