# -*- coding: utf-8 -*-
"""Проверяет собранные исправления в EDT относительно релиза.

Рабочая область EDT держит проект конфигурации в состоянии релиза (тега). Первый раз она готовится
с нуля: исходники тега выгружаются из git, проект импортируется и собирается. На другом релизе
в исходники накладывается разница между тегами и проект пересобирается (update_workspace).
Затем каждый проект исправления из <out>/EF_*/ проверяется командой validate и удаляется
из рабочей области. Все команды идут одним запуском 1cedtcli.

Ошибкой считаются замечания с важностью из FAIL_SEVERITIES (например, "Функция не определена"
или "Повторяющееся имя метода"). Замечания стандартов кодирования попадают в отчет, но сборку
не останавливают: сгенерированный код получает их всегда.

Пример:
    python ef_check.py --repo D:/git/system --config .reper/config.json --out build/ef \n        --workspace C:/cicd/ws-release --edt-cli "C:/.../1cedtcli.exe"

Коды возврата: 0 - ошибок нет, 1 - есть ошибки, 2 - проверять нечего.
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from efgen.gitutil import GitError, Repo  # noqa: E402

# EDT пишет отчет на языке интерфейса: на сервере он английский, на рабочих местах русский.
FAIL_SEVERITIES = {"Ошибка конфигурации", "Блокирующая", "Ошибка",
                   "Configuration error", "Blocker", "Error"}
STYLE_CATEGORIES = {"Стандарты кодирования", "Code style"}
MARKER = "ef-release.json"
# Больше изменений (например, обновление типовой конфигурации) - рабочая область готовится заново:
# по одному файлу это уже не быстрее полного импорта.
INCREMENTAL_LIMIT = 5000


def is_style(finding: dict) -> bool:
    return finding["category"] in STYLE_CATEGORIES


def run_cli(cli: str, workspace: str, script_lines: list[str], script_path: str, timeout: int,
            vmargs: str = "") -> int:
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(script_lines) + "\n")
    command = [cli, "-data", workspace, "-timeout", str(timeout), "-file", script_path]
    # Язык сообщений EDT: без него отчет пишется на языке ОС, на сервере - по-английски.
    command += ["-nl", os.environ.get("EF_EDT_NL", "ru_RU")]
    if vmargs:
        # -vmargs забирает все аргументы после себя, поэтому идет последним.
        command += ["-vmargs", *vmargs.split()]
    print(">", " ".join(command))
    return subprocess.run(command, stdin=subprocess.DEVNULL, timeout=timeout + 120).returncode


def long_path(path: str) -> str:
    """Путь для файловых операций без ограничения MAX_PATH в Windows.

    В исходниках УТ есть файлы вложенных подсистем с полным путем длиннее 260 символов.
    """
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def extract_tar(data: bytes, dest: str) -> None:
    """Распаковывает архив git archive: только каталоги и обычные файлы, с длинными путями.

    tarfile.extractall не годится: в Python 3.13 он собирает пути каталогов с прямым слэшем,
    а путь с префиксом \\\\?\\ такие не принимает.
    """
    root = long_path(dest)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            parts = [p for p in member.name.split("/") if p]
            if member.name.startswith("/") or ".." in parts:
                continue
            target = os.path.join(root, *parts)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
            elif member.isfile():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tar.extractfile(member) as source, open(target, "wb") as file:
                    shutil.copyfileobj(source, file)


def sync_sources(repo: Repo, changes: list[tuple[str, str]], new: str, project_dir: str, sources: str) -> tuple[int, int]:
    """Приводит исходники проекта к состоянию new по разнице changes (Repo.diff от прежнего состояния):
    пишет новые и измененные файлы, удаляет исчезнувшие вместе с опустевшими каталогами.
    Возвращает (записано, удалено)."""
    written = [path for status, path in changes if status != "D"]
    removed = [path for status, path in changes if status == "D"]

    def local(path: str) -> str:
        return long_path(os.path.join(sources, *path.split("/")))

    for path in removed:
        target = local(path)
        if os.path.exists(target):
            os.remove(target)
        # Каталог удаленного объекта метаданных пустым не оставляем.
        parent, top = os.path.dirname(target), local(project_dir)
        while parent != top and os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
            parent = os.path.dirname(parent)
    for path, data in repo.blobs(new, written).items():
        target = local(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
    return len(written), len(removed)


def update_workspace(repo: Repo, cli: str, workspace: str, old: str, sha: str, project_dir: str,
                     timeout: int, vmargs: str = "") -> bool:
    """Переводит готовую рабочую область на другой релиз: в исходники проекта накладывается
    разница между релизами, EDT при запуске замечает измененные файлы и пересобирает их.
    Так в разы быстрее, чем выгружать и импортировать проект заново. False - не вышло,
    рабочую область надо готовить с нуля."""
    sources = workspace.rstrip("\\/") + "-src"
    if not os.path.exists(os.path.join(sources, *project_dir.split("/"), ".project")):
        return False
    try:
        changes = repo.diff(old, sha, project_dir)
    except GitError as error:
        print(f"Разница с прежним релизом недоступна, рабочая область готовится заново: {error}")
        return False
    if len(changes) > INCREMENTAL_LIMIT:
        print(f"Изменено {len(changes)} файлов (больше {INCREMENTAL_LIMIT}), рабочая область готовится заново")
        return False

    # Без отметки прерванное обновление в следующий раз не сочтется готовой областью.
    os.remove(os.path.join(workspace, MARKER))
    try:
        written, removed = sync_sources(repo, changes, sha, project_dir, sources)
    except (GitError, OSError) as error:
        print(f"Исходники обновить не удалось, рабочая область готовится заново: {error}")
        return False
    print(f"Исходники обновлены: записано {written}, удалено {removed}")
    name = os.path.basename(project_dir)
    code = run_cli(cli, workspace, [f"build -y true {name}"], os.path.join(sources, "update.edtsh"), timeout, vmargs)
    if code != 0:
        print(f"Сборка после обновления завершилась с кодом {code}, рабочая область готовится заново")
        return False
    return True


def prepare_workspace(repo: Repo, cli: str, workspace: str, base_ref: str, project_dir: str,
                      timeout: int, vmargs: str = "") -> None:
    """Готовит рабочую область с проектом конфигурации из релиза, если ее еще нет.

    Область, готовая для другого релиза, обновляется разницей между релизами (update_workspace),
    с нуля - только если ее нет, обновить не вышло или задано EF_EDT_FULL=1.
    """
    sha = repo.rev_parse(base_ref)
    marker = os.path.join(workspace, MARKER)
    if os.path.exists(marker):
        with open(marker, encoding="utf-8") as f:
            ready = json.load(f)
        if ready.get("sha") == sha:
            print(f"Рабочая область релиза {base_ref} уже готова: {workspace}")
            return
        if os.environ.get("EF_EDT_FULL") != "1":
            print(f"Обновление рабочей области с релиза {ready.get('ref')} на {base_ref} ({sha[:10]})")
            if update_workspace(repo, cli, workspace, ready["sha"], sha, project_dir, timeout, vmargs):
                with open(marker, "w", encoding="utf-8") as f:
                    json.dump({"ref": base_ref, "sha": sha, "updated_from": ready.get("ref")}, f)
                return

    print(f"Подготовка рабочей области для релиза {base_ref} ({sha[:10]})")
    sources = workspace.rstrip("\\/") + "-src"
    for path in (workspace, sources):
        shutil.rmtree(long_path(path), ignore_errors=True)
    os.makedirs(sources)
    archive = subprocess.run(["git", "-C", repo.path, "archive", "--format=tar", sha, project_dir],
                             capture_output=True, check=True).stdout
    extract_tar(archive, sources)
    project_path = os.path.join(sources, *project_dir.split("/"))
    name = os.path.basename(project_dir)
    run_cli(cli, workspace, [f'import --project "{project_path}"', f"build -y true {name}"],
            os.path.join(sources, "prepare.edtsh"), timeout, vmargs)
    with open(marker, "w", encoding="utf-8") as f:
        json.dump({"ref": base_ref, "sha": sha}, f)


def read_findings(tsv: str) -> list[dict]:
    findings = []
    if not os.path.exists(tsv):
        return findings
    with open(tsv, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 8:
                continue
            findings.append({"severity": row[1], "category": row[2], "check": row[4],
                             "location": row[5], "line": row[6], "message": row[7]})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="каталог репозитория конфигурации")
    parser.add_argument("--config", default=os.environ.get("EF_CONFIG"), required=not os.environ.get("EF_CONFIG"),
                        help="настройки проекта, например .reper/config.json; по умолчанию переменная EF_CONFIG")
    parser.add_argument("--out", default="build/ef", help="каталог с исправлениями EF_*")
    parser.add_argument("--workspace", required=True, help="рабочая область EDT для проверки относительно релиза")
    parser.add_argument("--edt-cli", required=True, help="путь к 1cedtcli.exe")
    parser.add_argument("--base", help="релиз; по умолчанию берется из ef.json исправлений")
    parser.add_argument("--timeout", type=int, default=7200, help="ограничение времени одного запуска 1cedtcli, с")
    parser.add_argument("--vmargs", default=os.environ.get("EF_EDT_VMARGS", ""),
                        help='параметры JVM для EDT, например "-Xmx8g -XX:ActiveProcessorCount=4": '
                             "на рабочем узле кластера ограничивают память и число потоков")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    out = os.path.abspath(args.out)
    patches = []
    for info_path in sorted(glob.glob(os.path.join(out, "*", "ef.json"))):
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
        project = os.path.join(os.path.dirname(info_path), f"{config['edt_base_project']}.{info['name']}")
        if os.path.isdir(project):
            patches.append((info, info_path, project))
    if not patches:
        print("Проверять нечего: нет исправлений с проектом EDT")
        return 2

    base_ref = args.base or patches[0][0]["base"]
    project_dir = os.path.dirname(config["src_root"].rstrip("/"))
    workspace = os.path.abspath(args.workspace)
    prepare_workspace(Repo(args.repo), args.edt_cli, workspace, base_ref, project_dir, args.timeout, args.vmargs)

    script = []
    for info, _, project in patches:
        tsv = os.path.join(os.path.dirname(project), "check.tsv")
        if os.path.exists(tsv):
            os.remove(tsv)
        script.append(f'validate --file "{tsv}" --project-list "{project}"')
        script.append(f"delete -y true {os.path.basename(project)}")
    run_cli(args.edt_cli, workspace, script, os.path.join(out, "check.edtsh"), args.timeout, args.vmargs)

    failed = False
    for info, info_path, project in patches:
        tsv = os.path.join(os.path.dirname(project), "check.tsv")
        findings = read_findings(tsv)
        errors = [f for f in findings if f["severity"] in FAIL_SEVERITIES]
        other = [f for f in findings if f["severity"] not in FAIL_SEVERITIES and not is_style(f)]
        style = len(findings) - len(errors) - len(other)
        status = "failed" if errors or not os.path.exists(tsv) else "passed"
        failed = failed or status == "failed"

        lines = [f"# Проверка {info['name']} относительно релиза {base_ref}", ""]
        if not os.path.exists(tsv):
            lines.append("EDT не сформировал результат проверки, см. журнал задания.")
        lines += [f"- Ошибок: {len(errors)}", f"- Прочих замечаний: {len(other)}",
                  f"- Замечаний стандартов кодирования: {style}", ""]
        for title, items in (("Ошибки", errors), ("Прочие замечания", other)):
            if items:
                lines += [f"## {title}", ""]
                lines += [f"- {f['location']}, {f['line']}: {f['message']} ({f['severity']})" for f in items]
                lines.append("")
        with open(os.path.join(os.path.dirname(project), "check.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        info["check"] = {"status": status, "base": base_ref, "errors": len(errors), "warnings": len(other)}
        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        print(f"{info['name']}: {'ОШИБКИ' if status == 'failed' else 'ок'} "
              f"(ошибок {len(errors)}, прочих {len(other)}, стиль {style})")
        for f in errors:
            print(f"  {f['location']}, {f['line']}: {f['message']}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
