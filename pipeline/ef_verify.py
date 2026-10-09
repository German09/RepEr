# -*- coding: utf-8 -*-
"""Ждет, пока опубликованные исправления встанут в информационную базу, и проверяет, что они подключаются.

Ставит исправления сама база - регламентным заданием загрузки или вручную (обработка «Исправления из RepEr»).
Скрипт только спрашивает базу: HTTP-сервис расширения RepEr, GET <база>/hs/reperext/patches.
Ответ - исправления в базе (имя, идентификатор описания, версия, подключение) и ошибки загрузки из журнала регистрации.

Что ожидается по каталогам build/ef/EF_*:
  - собранное исправление (есть архив) - в базе сборка с этим UUID (или этой версией) и она подключается
    в новых сеансах; более новая сборка того же исправления тоже подходит;
  - отзыв (ef.json с revoke) - исправления в базе нет.

Опрос - раз в --interval секунд, не дольше --timeout. Не удалось подключить или снят флажок «Подключать» - ошибка
сразу. Не дождались - ошибка с тем, что не установлено, и ошибками загрузки: после ручной установки задание CI
достаточно перезапустить.

Учетные данные - переменные окружения EF_IB_USER и EF_IB_PASSWORD (пользователь с ролью ПроверкаИсправлений_RepEr).

Коды возврата: 0 - все установлено или проверять нечего, 1 - не установлено или ошибка.

Пример:
    python ef_verify.py --out build/ef --url https://server/base/hs/reperext/patches --timeout 900
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

OK = "ok"
WAIT = "wait"
FAIL = "fail"


@dataclass
class Expected:
    name: str
    uuid: str = ""
    version: str = ""
    revoke: bool = False
    fire: bool = False


@dataclass
class Verdict:
    name: str
    status: str
    message: str


def expected_patches(out: str) -> list[Expected]:
    """Исправления, опубликованные этим пайплайном: тот же отбор, что у задания Публикация."""
    result = []
    if not os.path.isdir(out):
        return result
    for name in sorted(os.listdir(out)):
        path = os.path.join(out, name, "ef.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        if info.get("revoke"):
            result.append(Expected(info["name"], revoke=True))
        elif os.path.isfile(os.path.join(out, name, info["name"] + ".zip")):
            result.append(Expected(info["name"], info["uuid"].lower(), info["version"], fire=info.get("fire", False)))
    return result


def version_key(version: str) -> tuple:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return ()


def evaluate(expected: Expected, patches: dict[str, dict]) -> Verdict:
    """Состояние одного исправления по ответу базы. patches - исправления базы по имени."""
    patch = patches.get(expected.name)
    if expected.revoke:
        if patch is None:
            return Verdict(expected.name, OK, "отозвано, в базе нет")
        return Verdict(expected.name, WAIT, f"отозвано, но в базе еще сборка {patch.get('version') or '?'}")
    if patch is None:
        return Verdict(expected.name, WAIT, f"сборка {expected.version} не установлена")

    version = patch.get("version") or ""
    uuid = (patch.get("uuid") or "").lower()
    # Идентификатор описания база видит только у подключенного в сеансе расширения, иначе сборку выдает версия.
    same = uuid == expected.uuid if uuid else version == expected.version
    if not same:
        if version_key(version) > version_key(expected.version):
            return Verdict(expected.name, OK, f"в базе более новая сборка {version}")
        return Verdict(expected.name, WAIT, f"в базе прежняя сборка {version or '?'}, ожидается {expected.version}")

    state = patch.get("state")
    if state == "attached":
        message = f"сборка {version} установлена и подключается"
        before = patch.get("sessions_before") or 0
        if expected.fire and before:
            message += f"; сеансов, начатых до подключения (в них прежняя версия): {before}"
        return Verdict(expected.name, OK, message)
    if state == "failed":
        return Verdict(expected.name, FAIL, f"сборка {version} установлена, но платформа не смогла ее подключить")
    if state == "disabled":
        return Verdict(expected.name, FAIL, f"сборка {version} установлена, но снят флажок «Подключать»")
    return Verdict(expected.name, WAIT, f"сборка {version} установлена, но еще не подключается ({state})")


class Base:
    def __init__(self, url: str, user: str, password: str):
        self.url = url
        self.headers = {"Accept": "application/json"}
        if user:
            token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
            self.headers["Authorization"] = f"Basic {token}"

    def state(self, since: str | None) -> dict:
        url = self.url
        if since:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"since": since})
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))


def parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.rstrip("Z"))


def report(verdicts: list[Verdict], state: dict | None, finished: str) -> str:
    lines = ["# Установка исправлений в базу", "", finished, "", "| Исправление | Итог | Состояние |", "|---|---|---|"]
    marks = {OK: "установлено", WAIT: "не установлено", FAIL: "ошибка"}
    for verdict in verdicts:
        lines.append(f"| {verdict.name} | {marks[verdict.status]} | {verdict.message} |")
    if state is not None:
        if not state.get("auto_install"):
            lines += ["", "Загрузка исправлений по расписанию в базе выключена: исправления ставятся вручную "
                      "(обработка «Исправления из RepEr»)."]
        errors = state.get("errors") or []
        if errors:
            lines += ["", "## Ошибки загрузки из журнала регистрации", ""]
            for error in errors:
                lines.append(f"- {error.get('date', '')} (раз: {error.get('count', 1)}): {error.get('text', '')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="build/ef", help="каталог собранных исправлений")
    parser.add_argument("--url", default=os.environ.get("EF_IB_URL", ""), help="адрес HTTP-сервиса .../hs/reperext/patches")
    parser.add_argument("--timeout", type=int, default=900, help="сколько ждать установки, секунд")
    parser.add_argument("--interval", type=int, default=60, help="период опроса базы, секунд")
    args = parser.parse_args()

    expected = expected_patches(args.out)
    if not expected:
        print("Опубликованных исправлений нет, проверять нечего")
        return 0
    if not args.url:
        print("Адрес HTTP-сервиса базы (EF_IB_URL) не задан, установка не проверяется")
        return 0

    base = Base(args.url, os.environ.get("EF_IB_USER", ""), os.environ.get("EF_IB_PASSWORD", ""))
    deadline = time.monotonic() + args.timeout
    since = None
    state = None
    verdicts: list[Verdict] = []
    problem = ""
    while True:
        try:
            state = base.state(since)
            problem = ""
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404):
                # Неверные учетные данные, нет роли или в базе расширение RepEr без HTTP-сервиса: ожидание не поможет.
                print(f"Ошибка запроса к базе: HTTP {error.code} {error.reason}")
                return 1
            problem = f"HTTP {error.code} {error.reason}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            problem = str(error)

        if state is not None and not problem:
            if since is None:
                # Ошибки загрузки - только после публикации: дата базы при первом запросе с запасом на публикацию.
                since = (parse_date(state["now"]) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
                if not state.get("auto_install"):
                    print("Загрузка по расписанию в базе выключена: ждем ручной установки")
            patches = {patch["name"]: patch for patch in state.get("patches", [])}
            verdicts = [evaluate(item, patches) for item in expected]
            for verdict in verdicts:
                print(f"{datetime.now():%H:%M:%S} {verdict.name}: {verdict.message}")
            if all(verdict.status == OK for verdict in verdicts):
                write_report(args.out, report(verdicts, state, "Все исправления установлены."))
                return 0
            if any(verdict.status == FAIL for verdict in verdicts):
                write_report(args.out, report(verdicts, state, "Исправление установлено, но не подключается."))
                print_errors(state)
                return 1
        else:
            print(f"{datetime.now():%H:%M:%S} база недоступна: {problem}")

        if time.monotonic() + args.interval > deadline:
            break
        time.sleep(args.interval)

    finished = f"За {args.timeout // 60} мин установка не завершилась."
    if problem:
        finished += f" Последний запрос к базе: {problem}."
    write_report(args.out, report(verdicts, state, finished))
    print(finished)
    print_errors(state)
    print("После установки (вручную или по расписанию) перезапустите это задание.")
    return 1


def print_errors(state: dict | None) -> None:
    for error in (state or {}).get("errors") or []:
        print(f"Ошибка загрузки {error.get('date', '')} (раз: {error.get('count', 1)}): {error.get('text', '')}")


def write_report(out: str, text: str) -> None:
    with open(os.path.join(out, "verify.md"), "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
