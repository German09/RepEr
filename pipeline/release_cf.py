# -*- coding: utf-8 -*-
"""Собирает файл конфигурации (.cf) релиза по тегу репозитория конфигурации.

Проект конфигурации из тега готовится в рабочей области EDT - той же, что у ef_check.py, поэтому
первая проверка исправлений после тега ее уже не готовит. Проект выгружается в XML, ibcmd собирает
из XML файл .cf во временной файловой базе. Файл и описание release.md (коммит, версия, sha256,
задачи и коммиты после предыдущего тега) кладутся в <dest>/<тег>/. Повторная сборка тега
перезаписывает его файлы. В <dest> остаются последние --keep релизов по номеру тега (по умолчанию 5).
Тег, путь к файлу и предыдущий релиз пишутся в <report-dir>/release.env для заданий Загрузка (load_cf.ps1)
и Обновление (update_db.ps1).

Тег берется из --tag (переменная EF_RELEASE_TAG), иначе из тега пайплайна (CI_COMMIT_TAG), иначе из события
tag push веб-хука (файл TRIGGER_PAYLOAD).

Пример:
    python release_cf.py --repo D:/cicd/mirror/system.git --config .reper/config.json \
        --workspace D:/cicd/ws-release --edt-cli "D:/EDT/.../1cedtcli.exe" \
        --platform "C:/Program Files/1cv8/8.3.27.2074/bin" --dest D:/CF --tag 3.20

Коды возврата: 0 - собрано, 1 - ошибка, 2 - собирать нечего (событие не про новый тег релиза).
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from efgen.gitutil import Commit, GitError, Repo  # noqa: E402
from ef_check import long_path, prepare_workspace, run_cli  # noqa: E402


def tag_from_payload(payload: dict, repo: Repo) -> str | None:
    """Тег из события tag push или None, если событие не про новый тег."""
    if payload.get("object_kind") != "tag_push":
        print(f"Событие {payload.get('object_kind')} не про тег, собирать нечего")
        return None
    after = payload.get("after") or ""
    if not after.strip("0"):
        print(f"Тег {payload.get('ref')} удален, собирать нечего")
        return None
    ref = payload.get("ref") or ""
    if ref.startswith("refs/tags/"):
        return ref[len("refs/tags/"):]
    # ref события перекрывается ref из адреса триггера: тег ищется по объекту, на который он указывает.
    tags = repo.tags_at(after)
    if len(tags) != 1:
        raise SystemExit(f"Не удалось определить тег события по объекту {after}: {tags or 'тегов нет'}")
    return tags[0]


def tasks_of(commits: list[Commit], task_regex: str) -> list[str]:
    """Задачи из комментариев коммитов в порядке первого упоминания."""
    result: list[str] = []
    for commit in commits:
        for found in re.finditer(task_regex, commit.subject):
            if found.group(0) not in result:
                result.append(found.group(0))
    return result


def release_notes(tag: str, head: Commit, version: str, previous: str | None, commits: list[Commit],
                  tasks: list[str], cf_name: str, size: int, sha256: str, built: str) -> str:
    lines = [f"# Релиз {tag}", "",
             f"- Коммит: {head.short} от {head.date}",
             f"- Версия: {version or 'не найдена'}",
             f"- Предыдущий релиз: {previous or 'нет'}",
             f"- Файл: {cf_name}, {size:,} байт".replace(",", " "),
             f"- SHA-256: {sha256}",
             f"- Собран: {built}", ""]
    if previous:
        lines += [f"## Задачи после {previous} ({len(tasks)})", ""]
        lines += [f"- {task}" for task in tasks] or ["Нет"]
        lines += ["", f"## Коммиты после {previous} ({len(commits)})", ""]
        lines += [f"- {c.short} {c.date} {c.author}: {c.subject}" for c in commits] or ["Нет"]
        lines.append("")
    return "\n".join(lines)


def release_env(tag: str, cf: str, previous: str | None = None) -> str:
    """Переменные для заданий Загрузка и Обновление (dotenv-артефакт). Путь - с прямыми слэшами: обратные dotenv
    GitLab может принять за экранирование, а Windows понимает и прямые. RELEASE_PREVIOUS - предыдущий релиз:
    в его каталог Обновление сохраняет удаляемые исправления без манифеста."""
    return f"RELEASE_TAG={tag}\nRELEASE_CF={cf.replace(os.sep, '/')}\nRELEASE_PREVIOUS={previous or ''}\n"


def ibcmd(platform: str, args: list[str], timeout: int) -> None:
    command = [os.path.join(platform, "ibcmd.exe"), *args]
    print(">", " ".join(command))
    # Пустой ввод: при нехватке прав ibcmd спрашивает имя пользователя и иначе ждал бы до ограничения времени.
    code = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=timeout).returncode
    if code != 0:
        raise SystemExit(f"ibcmd {' '.join(args[:3])} завершился с кодом {code}")


def copy_with_hash(source: str, target: str) -> str:
    """Копирует файл через временное имя, чтобы в каталоге релиза не было недописанного .cf."""
    digest = hashlib.sha256()
    temp = target + ".tmp"
    with open(source, "rb") as src, open(temp, "wb") as dst:
        for chunk in iter(lambda: src.read(1 << 20), b""):
            digest.update(chunk)
            dst.write(chunk)
    os.replace(temp, target)
    return digest.hexdigest()


def version_key(tag: str) -> tuple:
    """Порядок тегов по номеру: 3.9 < 3.16.02 < 3.16.2.1 < 3.17; нечисловые части - после числовых."""
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in tag.split("."))


def stale_releases(tags: list[str], keep: int, current: str) -> list[str]:
    """Релизы сверх последних keep по номеру тега. Только что собранный не удаляется, даже если он старый."""
    if keep <= 0:
        return []
    ordered = sorted(tags, key=version_key, reverse=True)
    return [tag for tag in ordered[keep:] if tag != current]


def remove_stale(dest: str, keep: int, current: str) -> None:
    # Релизом считается только каталог с release.md: чужое в каталоге релизов не трогаем.
    tags = [name for name in os.listdir(dest) if os.path.isfile(os.path.join(dest, name, "release.md"))]
    for tag in stale_releases(tags, keep, current):
        print(f"Удаляется старый релиз {tag}: хранятся последние {keep}")
        shutil.rmtree(os.path.join(dest, tag))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="каталог репозитория конфигурации")
    parser.add_argument("--config", default=os.environ.get("EF_CONFIG"), required=not os.environ.get("EF_CONFIG"),
                        help="настройки проекта, например .reper/config.json; по умолчанию переменная EF_CONFIG")
    parser.add_argument("--tag", default=os.environ.get("EF_RELEASE_TAG") or os.environ.get("CI_COMMIT_TAG"),
                        help="тег релиза; по умолчанию переменная EF_RELEASE_TAG, иначе CI_COMMIT_TAG, "
                             "иначе событие веб-хука")
    parser.add_argument("--workspace", required=True, help="рабочая область EDT с проектом релиза")
    parser.add_argument("--edt-cli", required=True, help="путь к 1cedtcli.exe")
    parser.add_argument("--platform", required=True, help="каталог bin платформы с ibcmd.exe")
    parser.add_argument("--dest", required=True, help="каталог релизов, файлы кладутся в <dest>/<тег>/")
    parser.add_argument("--keep", type=int, default=int(os.environ.get("EF_CF_KEEP") or 5),
                        help="сколько последних релизов хранить в --dest, по номеру тега; 0 - не удалять. "
                             "По умолчанию переменная EF_CF_KEEP, иначе 5")
    parser.add_argument("--work", default="build/release", help="рабочий каталог: XML (2,3 ГБ), временная база, .cf")
    parser.add_argument("--report-dir", default="build/release", help="куда положить копию release.md для артефакта задания")
    parser.add_argument("--timeout", type=int, default=7200, help="ограничение времени одного запуска 1cedtcli и ibcmd, с")
    parser.add_argument("--vmargs", default=os.environ.get("EF_EDT_VMARGS", ""), help="параметры JVM для EDT")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    repo = Repo(args.repo)
    # Прежний release.env не должен уйти в задание Загрузка, если в этот раз собирать нечего.
    env_path = os.path.join(args.report_dir, "release.env")
    if os.path.exists(env_path):
        os.remove(env_path)

    tag = args.tag
    if not tag:
        payload_path = os.environ.get("TRIGGER_PAYLOAD")
        if not payload_path or not os.path.exists(payload_path):
            print("Тег не задан: нет --tag, EF_RELEASE_TAG, CI_COMMIT_TAG и события веб-хука")
            return 2
        with open(payload_path, encoding="utf-8-sig") as f:
            tag = tag_from_payload(json.load(f), repo)
        if not tag:
            return 2
    match = config["base_tag_match"]
    if not fnmatch.fnmatchcase(tag, match):
        print(f"Тег {tag} не похож на релиз (шаблон {match}), собирать нечего")
        return 2

    head = repo.commit(repo.rev_parse(tag))
    try:
        previous = repo.describe_tag(f"{head.sha}^", match)
    except GitError:
        previous = None
    commits = repo.commits(f"{previous}..{head.sha}") if previous else []
    source = repo.show(head.sha, f"{config['src_root']}/{config['version_module']}") or ""
    found = re.search(config["version_regex"], source)
    version = found.group(1) if found else ""
    print(f"Релиз {tag}: коммит {head.short}, версия {version or '?'}, предыдущий {previous or 'нет'}")

    project_dir = os.path.dirname(config["src_root"].rstrip("/"))
    project = os.path.basename(project_dir)
    workspace = os.path.abspath(args.workspace)
    work = os.path.abspath(os.path.join(args.work, tag))
    xml, ib = os.path.join(work, "xml"), os.path.join(work, "ib")
    cf_name = f"{config.get('applied_name', project)}_{tag}.cf"
    cf = os.path.join(work, cf_name)
    for path in (xml, ib):
        shutil.rmtree(long_path(path), ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    if os.path.exists(cf):
        os.remove(cf)

    prepare_workspace(repo, args.edt_cli, workspace, tag, project_dir, args.timeout, args.vmargs)
    run_cli(args.edt_cli, workspace, [f'export --project-name {project} --configuration-files "{xml}"'],
            os.path.join(work, "export.edtsh"), args.timeout, args.vmargs)
    if not os.path.exists(os.path.join(xml, "Configuration.xml")):
        raise SystemExit(f"EDT не выгрузил проект {project} в XML, см. журнал задания")

    # Импорт с --out пишет конфигурацию сразу в файл; база нужна ibcmd только как место работы.
    connection = [f"--db-path={os.path.join(ib, 'db')}", f"--data={os.path.join(ib, 'data')}"]
    ibcmd(args.platform, ["infobase", "create", *connection], args.timeout)
    ibcmd(args.platform, ["infobase", "config", "import", *connection, f"--out={cf}", xml], args.timeout)
    if not os.path.exists(cf):
        raise SystemExit(f"ibcmd не создал файл {cf}")

    target_dir = os.path.join(args.dest, tag)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, cf_name)
    if os.path.exists(target):
        print(f"Файл {target} уже есть, перезаписывается")
    sha256 = copy_with_hash(cf, target)
    built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    notes = release_notes(tag, head, version, previous, commits, tasks_of(commits, config["task_regex"]),
                          cf_name, os.path.getsize(target), sha256, built)
    report_dir = os.path.join(args.report_dir, tag)
    os.makedirs(report_dir, exist_ok=True)
    for path in (os.path.join(target_dir, "release.md"), os.path.join(report_dir, "release.md")):
        with open(path, "w", encoding="utf-8") as f:
            f.write(notes)
    with open(env_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(release_env(tag, target, previous))

    for path in (xml, ib, cf):
        if os.path.isdir(path):
            shutil.rmtree(long_path(path), ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    print(f"Собрано: {target}")
    remove_stale(args.dest, args.keep, tag)
    return 0


if __name__ == "__main__":
    # Построчный вывод: иначе в журнале задания строки скрипта идут после вывода 1cedtcli и ibcmd.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.exit(main())
