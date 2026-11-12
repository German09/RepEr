# -*- coding: utf-8 -*-
"""Проверка архива исправления перед публикацией в ленту.

Запуск: python -m unittest discover -s tests
"""
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ef_publish import archive_error, container_has_data  # noqa: E402

# Заголовок контейнера 1С: адрес первой свободной страницы, размер страницы, ревизия, резерв.
HEADER = bytes.fromhex("ffffff7f" "00020000" "00000000" "00000000")
# Заголовок первого блока (оглавления) за заголовком контейнера.
BLOCK = b"\r\n00000054 00000200 7fffffff \r\n"


def archive(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for name, data in files.items():
            zip_file.writestr(name, data)
    return buffer.getvalue()


class ContainerTest(unittest.TestCase):
    def test_container_with_data(self):
        self.assertTrue(container_has_data(HEADER + BLOCK + b"\x00" * 100))

    def test_empty_container(self):
        # ibcmd оставляет такой файл, когда импорт XML падает на неизвестном типе реквизита.
        self.assertFalse(container_has_data(HEADER))

    def test_garbage(self):
        self.assertFalse(container_has_data(b""))
        self.assertFalse(container_has_data(HEADER + b"xx" + b"\x00" * 100))


class ArchiveTest(unittest.TestCase):
    def test_valid(self):
        data = archive({"EF_1_1.cfe": HEADER + BLOCK, "Manifest.xml": "<Patch/>"})
        self.assertIsNone(archive_error(data, "EF_1_1"))

    def test_empty_cfe(self):
        data = archive({"EF_1_1.cfe": HEADER, "Manifest.xml": "<Patch/>"})
        self.assertIn("пустой (16 байт)", archive_error(data, "EF_1_1"))

    def test_no_cfe(self):
        data = archive({"Manifest.xml": "<Patch/>"})
        self.assertIn("нет файла EF_1_1.cfe", archive_error(data, "EF_1_1"))

    def test_other_cfe_name(self):
        data = archive({"EF_2_1.cfe": HEADER + BLOCK, "Manifest.xml": "<Patch/>"})
        self.assertIn("нет файла EF_1_1.cfe", archive_error(data, "EF_1_1"))

    def test_no_manifest(self):
        data = archive({"EF_1_1.cfe": HEADER + BLOCK})
        self.assertIn("Manifest.xml", archive_error(data, "EF_1_1"))

    def test_not_zip(self):
        self.assertIn("архив не читается", archive_error(b"not a zip", "EF_1_1"))


if __name__ == "__main__":
    unittest.main()
