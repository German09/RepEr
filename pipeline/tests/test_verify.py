# -*- coding: utf-8 -*-
"""Проверка установки исправлений в базу по ответу HTTP-сервиса расширения RepEr.

Запуск: python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ef_verify import FAIL, OK, WAIT, Expected, evaluate, expected_patches  # noqa: E402

UUID = "23129ec2-c038-11f1-8853-e89e4925478b"


def base(**patch) -> dict:
    record = {"name": "EF_1_1", "uuid": UUID, "version": "1.3.19.01.2", "state": "attached", "sessions_before": 0}
    record.update(patch)
    return {record["name"]: record}


class EvaluateTest(unittest.TestCase):

    expected = Expected("EF_1_1", UUID, "1.3.19.01.2")

    def test_attached(self):
        self.assertEqual(evaluate(self.expected, base()).status, OK)

    def test_not_installed(self):
        self.assertEqual(evaluate(self.expected, {}).status, WAIT)

    def test_previous_build(self):
        verdict = evaluate(self.expected, base(uuid="other", version="1.3.19.01.1"))
        self.assertEqual(verdict.status, WAIT)
        self.assertIn("1.3.19.01.1", verdict.message)

    def test_newer_build_is_ok(self):
        # Пока ждали, следующий пайплайн опубликовал новую сборку, и база поставила ее.
        self.assertEqual(evaluate(self.expected, base(uuid="other", version="1.3.19.01.10")).status, OK)

    def test_uuid_case_ignored(self):
        self.assertEqual(evaluate(self.expected, base(uuid=UUID.upper())).status, OK)

    def test_without_uuid_by_version(self):
        # У неподключенного в сеансе расширения база не видит описание: сборку выдает версия.
        self.assertEqual(evaluate(self.expected, base(uuid="", state="pending")).status, WAIT)
        self.assertEqual(evaluate(self.expected, base(uuid="", version="1.3.19.01.1")).status, WAIT)

    def test_failed_and_disabled(self):
        self.assertEqual(evaluate(self.expected, base(state="failed")).status, FAIL)
        self.assertEqual(evaluate(self.expected, base(state="disabled")).status, FAIL)

    def test_fire_sessions_before(self):
        expected = Expected("EF_1_1", UUID, "1.3.19.01.2", fire=True)
        verdict = evaluate(expected, base(sessions_before=12))
        self.assertEqual(verdict.status, OK)
        self.assertIn("12", verdict.message)

    def test_revoke(self):
        expected = Expected("EF_1_1", revoke=True)
        self.assertEqual(evaluate(expected, {}).status, OK)
        self.assertEqual(evaluate(expected, base()).status, WAIT)


class ExpectedPatchesTest(unittest.TestCase):

    def write(self, root: str, name: str, info: dict, archive: bool) -> None:
        os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, name, "ef.json"), "w", encoding="utf-8") as f:
            json.dump(info, f)
        if archive:
            with open(os.path.join(root, name, name + ".zip"), "wb") as f:
                f.write(b"zip")

    def test_built_revoked_and_unbuilt(self):
        with tempfile.TemporaryDirectory() as root:
            self.write(root, "EF_1_1", {"name": "EF_1_1", "uuid": UUID.upper(), "version": "1.3.19.01.1"}, True)
            self.write(root, "EF_2_1", {"name": "EF_2_1", "revoke": True}, False)
            # Собрать не удалось или сборка не дошла до архива: не публиковалось, не ждем.
            self.write(root, "EF_3_1", {"name": "EF_3_1", "uuid": UUID, "version": "1.3.19.01.1"}, False)
            self.assertEqual(expected_patches(root), [
                Expected("EF_1_1", UUID, "1.3.19.01.1"),
                Expected("EF_2_1", revoke=True),
            ])

    def test_no_dir(self):
        self.assertEqual(expected_patches(os.path.join(tempfile.gettempdir(), "no-such-ef-dir")), [])


if __name__ == "__main__":
    unittest.main()
