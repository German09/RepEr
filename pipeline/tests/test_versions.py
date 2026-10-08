# -*- coding: utf-8 -*-
"""Версия расширения по сборкам исправления в ленте: у сборок с разным содержимым она не совпадает.

Запуск: python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ef_build import extension_version, published_patches  # noqa: E402


def patch(version: str, commits: list[str], name: str = "EF_1") -> dict:
    return {"name": name, "patch_version": version, "commits": commits}


class ExtensionVersionTest(unittest.TestCase):

    def test_no_feed(self):
        self.assertEqual(extension_version("1.3.19.01", ["a", "b"], []), "1.3.19.01.2")

    def test_next_commit(self):
        self.assertEqual(extension_version("1.3.19.01", ["a", "b"], [patch("1.3.19.01.1", ["a"])]), "1.3.19.01.2")

    def test_same_commits_keep_version(self):
        published = [patch("1.3.19.01.1", ["a"]), patch("1.3.19.01.2", ["b"])]
        self.assertEqual(extension_version("1.3.19.01", ["b"], published), "1.3.19.01.2")

    def test_other_commits_same_count(self):
        # Пересборка с другим набором коммитов того же размера: номер не должен совпасть с прежним.
        self.assertEqual(extension_version("1.3.19.01", ["b"], [patch("1.3.19.01.1", ["a"])]), "1.3.19.01.2")

    def test_never_lower_than_published(self):
        self.assertEqual(extension_version("1.3.19.01", ["a", "c"], [patch("1.3.19.01.3", ["a", "b"])]), "1.3.19.01.4")

    def test_other_library_version_ignored(self):
        # Выпуск библиотеки 1.3.19.02 - свой каталог ленты и своя нумерация, версии не пересекаются.
        self.assertEqual(extension_version("1.3.19.02", ["a"], [patch("1.3.19.01.5", ["x"])]), "1.3.19.02.1")


class PublishedPatchesTest(unittest.TestCase):

    def test_feed_dir(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "1.3.19.01"))
            index = {"patches": [patch("1.3.19.01.1", ["a"]), patch("1.3.19.01.1", ["x"], name="EF_2")]}
            with open(os.path.join(root, "1.3.19.01", "index.json"), "w", encoding="utf-8") as f:
                json.dump(index, f)
            args = SimpleNamespace(feed_dir=root, feed_url=None)
            self.assertEqual(published_patches(args, "1.3.19.01", "EF_1"), [patch("1.3.19.01.1", ["a"])])
            self.assertEqual(published_patches(args, "1.3.20.01", "EF_1"), [])

    def test_without_feed(self):
        self.assertEqual(published_patches(SimpleNamespace(), "1.3.19.01", "EF_1"), [])


if __name__ == "__main__":
    unittest.main()
