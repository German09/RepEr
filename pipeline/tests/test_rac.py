# -*- coding: utf-8 -*-
"""Разбор вывода rac (rac.ps1, ConvertFrom-RacOutput): записи, значения в кавычках, многострочные значения.

Скрипт PowerShell вызывается через powershell.exe; без него (не Windows) тесты пропускаются.

Запуск: python -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

RAC_PS1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rac.ps1")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

# Вывод rac infobase info: сообщение блокировки из 1С с переводами строк и пустой строкой внутри.
INFO_MULTILINE = """infobase                                   : 3ec9dae7-3589-4477-8205-31b8268efcf7
name                                       : pos
sessions-deny                              : on
denied-from                                : 2026-10-21T23:00:00
denied-message                             : "Сохраните данные, работа системы будет возобновлена в 23:20

Для администратора: name : value
ENTERPRISE /S""pos-p-app01\\pos"" /UC<код>"
denied-parameter                           : "2026.10.21 22:22:16 5083906"
denied-to                                  :
permission-code                            : "12"
descr                                      : "Рабочая база"
"""

# Два блока infobase summary list и значение без кавычек, продолженное на следующей строке.
SUMMARY_LIST = """infobase : 3ec9dae7-3589-4477-8205-31b8268efcf7
name     : pos
descr    : "Рабочая база"

infobase : 0b1c2d3e-0000-4000-8000-000000000001
name     : pos-test
descr    : первая строка
вторая строка

"""


def parse(text: str) -> list:
    """Разбирает вывод rac скриптом rac.ps1 и возвращает записи списком словарей."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as source:
        source.write(text)
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f". '{RAC_PS1}'; "
        f"$lines = [IO.File]::ReadAllLines('{source.name}', [Text.Encoding]::UTF8); "
        "ConvertTo-Json -InputObject @(ConvertFrom-RacOutput $lines) -Depth 3 -Compress"
    )
    try:
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, check=False)
    finally:
        os.unlink(source.name)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return json.loads(result.stdout.decode("utf-8-sig"))


@unittest.skipUnless(POWERSHELL, "нет PowerShell")
class RacOutputTest(unittest.TestCase):

    def test_multiline_message_keeps_tail(self):
        records = parse(INFO_MULTILINE)
        self.assertEqual(len(records), 1)
        info = records[0]
        self.assertEqual(info["permission-code"], "12")
        self.assertEqual(info["denied-to"], "")
        self.assertEqual(info["denied-parameter"], "2026.10.21 22:22:16 5083906")
        self.assertEqual(info["descr"], "Рабочая база")

    def test_multiline_message_text(self):
        message = parse(INFO_MULTILINE)[0]["denied-message"]
        self.assertEqual(message.splitlines(), [
            "Сохраните данные, работа системы будет возобновлена в 23:20",
            "",
            "Для администратора: name : value",
            'ENTERPRISE /S"pos-p-app01\\pos" /UC<код>',
        ])

    def test_blocks_and_unquoted_continuation(self):
        records = parse(SUMMARY_LIST)
        self.assertEqual([r["name"] for r in records], ["pos", "pos-test"])
        self.assertEqual(records[0]["descr"], "Рабочая база")
        self.assertEqual(records[1]["descr"].splitlines(), ["первая строка", "вторая строка"])

    def test_empty_output(self):
        self.assertEqual(parse(""), [])


if __name__ == "__main__":
    unittest.main()
