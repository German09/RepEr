# -*- coding: utf-8 -*-
"""Формирует исходники расширений-исправлений (EF) по EF-коммитам.

Одно исправление - один EF-коммит: в расширении только то, что изменил этот коммит. Порядок установки
исправлений - порядок коммитов в истории после релиза; если несколько исправлений перехватывают один метод,
работает перехватчик последнего. Коммиты, связанные методами или модулями, которых нет в релизе, собираются
в одно исправление (см. efgen/patches.py).

Пример:
    python ef_build.py --repo D:/git/system --config .reper/config.json --commit HEAD

Коды возврата: 0 - исходники сформированы; 2 - формировать нечего;
3 - есть изменения, которые не удалось перенести в расширение (только с --strict).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ef_publish import DirFeed, GitlabFeed  # noqa: E402
from efgen.gitutil import Repo  # noqa: E402
from efgen.patches import Patch, Patches, collect, methods_of, plan_all  # noqa: E402
from efgen.planner import Planner  # noqa: E402
from efgen.render import Settings, manifest_xml, modified_metadata  # noqa: E402
from efgen.titles import commit_link, patch_title  # noqa: E402
from efgen.writers import Objects, write_edt, write_xml  # noqa: E402


def report(patch: Patch, settings: Settings, notes: list[str]) -> str:
    plan = patch.plan
    lines = [
        f"# {settings.name}",
        "",
        f"- Задача: {plan.task}",
        f"- Описание: {settings.comment}",
        f"- Релиз-основание: {plan.base_ref} ({plan.base_sha[:10]}), версия {settings.applied_versions}",
        f"- Коммит: {plan.head.short}",
        f"- Порядок установки: {patch.order}",
        f"- Версия исправления: {settings.version}",
        "",
        "## Коммиты",
        "",
    ]
    lines += [f"- {c.short} {c.date} {c.author}: {c.subject}" for c in plan.commits]
    if patch.merged:
        lines += ["", "## Собраны в одно исправление", ""]
        lines += [f"- {item}" for item in patch.merged]
    if notes:
        lines += ["", "## Откаты", ""]
        lines += [f"- {item}" for item in notes]
    lines += ["", "## Состав", ""]
    for module_plan in plan.modules:
        if module_plan.is_new:
            lines.append(f"- {module_plan.ref.title}: модуля нет в релизе, добавлен целиком")
            continue
        lines.append(f"- {module_plan.ref.title}")
        lines += [f"  - &Вместо: {m.name}" for m in module_plan.overrides]
        lines += [f"  - добавлен: {m.name}" for m in module_plan.additions]
    if plan.unsupported:
        lines += ["", "## Не вошло в исправление - нужна ручная доработка", ""]
        lines += [f"- {item}" for item in plan.unsupported]
    if plan.warnings:
        lines += ["", "## Проверить", ""]
        lines += [f"- {item}" for item in plan.warnings]
    return "\n".join(lines) + "\n"


def extension_version(release: str, commits: list[str], published: list[dict]) -> str:
    """Версия расширения <версия библиотеки>.<номер>, например 1.3.19.02.1: по ней и имени считается UUID сборки.

    Префикс - версия библиотеки, она же каталог ленты: теги релизов неоднозначны (3.17 и 3.17.2 -
    библиотеки 1.3.17.01 и 1.3.17.02), и номера исправлений разных выпусков библиотеки не пересекаются.
    Номер - число коммитов исправления. Если в ленте уже есть сборка исправления с другим
    набором коммитов и тем же или большим номером, номер - наибольший опубликованный + 1: иначе
    у сборок с разным содержимым совпали бы версия и UUID, и база не поставила бы новую сборку.
    Сборка с тем же набором коммитов получает прежнюю версию.

    published - записи оглавления ленты с именем этого исправления.
    """
    numbers = []
    for patch in published:
        version = str(patch.get("patch_version", ""))
        if not version.startswith(release + "."):
            continue
        if patch.get("commits") == commits:
            return version
        number = version[len(release) + 1:]
        if number.isdigit():
            numbers.append(int(number))
    return f"{release}.{max([len(commits)] + [n + 1 for n in numbers])}"


def published_patches(args, release: str, name: str | None = None) -> list[dict]:
    """Сборки в ленте релиза (только исправления name, если задано); без ленты (--feed-dir, --feed-url) - пустой список."""
    if getattr(args, "feed_dir", None):
        feed = DirFeed(args.feed_dir)
    elif getattr(args, "feed_url", None):
        feed = GitlabFeed(args.feed_url)
    else:
        return []
    raw = feed.read(release, "index.json")
    index = json.loads(raw.decode("utf-8")) if raw else {}
    return [p for p in index.get("patches", []) if name is None or p.get("name") == name]


def library_version(repo: Repo, config: dict, base_ref: str) -> str:
    """Версия библиотеки в релизе - каталог ленты и префикс версии расширения."""
    source = repo.show(base_ref, f"{config['src_root']}/{config['version_module']}") or ""
    found = re.search(config["version_regex"], source)
    return found.group(1) if found else ""


def build_patch(repo: Repo, config: dict, args, patch: Patch, applied_versions: str, notes: list[str],
                feed: list[dict]) -> int:
    """Формирует исходники одного исправления в каталоге <out>/<имя расширения>."""
    src_root = config["src_root"]
    plan = patch.plan
    name = patch.name(config["extension_name"])
    # Без версии библиотеки нет и каталога ленты: префикс версии - релиз-основание.
    release = applied_versions or plan.base_ref
    commits = [c.sha for c in patch.commits]
    version = extension_version(release, commits, [p for p in feed if p.get("name") == name])
    if version != f"{release}.{len(commits)}":
        print(f"{name}: версия расширения {version} - по сборкам исправления в ленте")

    head = patch.head
    title = patch_title(head.subject, patch.task, config["task_regex"], config["ef_regex"])
    commit_title, commit_url = commit_link(head.sha, head.subject, config.get("commit_url", ""))
    settings = Settings(
        name=name,
        prefix=config["method_prefix"].format(task=patch.task, n=patch.number),
        synonym=title.title or name,
        comment=title.title or head.subject,
        version=version,
        compatibility=config["compatibility_mode"],
        xml_format=config["xml_format"],
        applied_name=config["applied_name"],
        applied_versions=applied_versions,
        manifest=bool(config.get("manifest", True)) and bool(applied_versions),
    )

    shas = set(commits)
    text = report(patch, settings, [n for n in notes if any(sha[:10] in n for sha in shas)])
    print(text)
    if plan.empty:
        print(f"{name}: в коммитах нет изменений, которые можно перенести в расширение")
        return 3 if args.strict and plan.unsupported else 2

    out = os.path.join(os.path.abspath(args.out), name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    formats = {f.strip() for f in args.format.split(",")}

    def read_mdo(mdo_path: str, is_new: bool) -> str | None:
        return repo.show(plan.head.sha if is_new else plan.base_sha, f"{src_root}/{mdo_path}")

    objects = Objects(plan, read_mdo)
    if "xml" in formats:
        write_xml(os.path.join(out, "xml"), plan, settings, objects)
    if "edt" in formats:
        # EDT называет импортируемый проект по имени каталога, поэтому каталог = имя проекта.
        edt_project = f"{config['edt_base_project']}.{name}"
        write_edt(os.path.join(out, edt_project), plan, settings, objects, config["edt_base_project"])
    with open(os.path.join(out, "Manifest.xml"), "w", encoding="utf-8") as f:
        f.write(manifest_xml(plan, settings))
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(out, "ef.json"), "w", encoding="utf-8") as f:
        json.dump({
            "name": settings.name,
            "uuid": settings.uid("patch"),
            "version": settings.version,
            "order": patch.order,
            "description": settings.comment,
            "subject": head.subject,
            "task": patch.task,
            "task_code": title.task_code,
            "commit_title": commit_title,
            "commit_url": commit_url,
            "fire": patch.fire,
            "revert": patch.revert,
            "base": plan.base_ref,
            "applied_name": settings.applied_name,
            "applied_versions": settings.applied_versions,
            "modified_metadata": modified_metadata(plan),
            "methods": methods_of(plan),
            "commits": commits,
            "merged": patch.merged,
            "unsupported": plan.unsupported,
            "warnings": plan.warnings,
        }, f, ensure_ascii=False, indent=2)

    if args.strict and plan.unsupported:
        print(f"{name}: часть изменений не перенесена в расширение (--strict)")
        return 3
    return 0


def revoke_patch(config: dict, args, base_ref: str, name: str, reason: str, applied_versions: str) -> int:
    """Исправления больше нет (его коммиты откачены или вошли в другое исправление): вместо сборки - отзыв.

    В каталоге исправления только ef.json с признаком revoke и report.md: сборка и проверка его
    пропускают, публикация помечает отозванными все сборки исправления в ленте.
    """
    text = "\n".join([
        f"# {name}: отзыв",
        "",
        f"- Релиз-основание: {base_ref}, версия {applied_versions}",
        f"- {reason}: исправление отзывается из ленты, базы его удалят.",
    ]) + "\n"
    print(text)

    out = os.path.join(os.path.abspath(args.out), name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(out, "ef.json"), "w", encoding="utf-8") as f:
        json.dump({
            "name": name,
            "revoke": True,
            "description": reason,
            "base": base_ref,
            "applied_name": config["applied_name"],
            "applied_versions": applied_versions,
        }, f, ensure_ascii=False, indent=2)
    return 0


def explicit_patch(repo: Repo, planner: Planner, config: dict, args, base_ref: str, current: Patches) -> Patch:
    """Исправление из явного списка коммитов (--commits): номер в имени - короткий хэш последнего коммита,
    чтобы не совпасть с номерами EF-коммитов задачи."""
    commits = [repo.commit(c.strip()) for c in args.commits.split(",") if c.strip()]
    found = re.search(config["task_regex"], commits[-1].subject)
    task = args.task or (found.group(1) if found else "")
    positions = {c.sha: k for k, c in enumerate(repo.commits(f"{repo.rev_parse(base_ref)}..{args.commit}"), 1)}
    patch = Patch(task, commits[-1].sha[:7], positions.get(commits[-1].sha, len(positions) + 1), commits)
    overridden = set()
    for earlier in current.patches:
        if earlier.order < patch.order:
            for module_plan in earlier.plan.modules:
                overridden |= {(module_plan.ref.path, m.key) for m in module_plan.overrides}
    patch.plan = planner.build(task, base_ref, patch.head.sha, commits, pull_deps=args.pull_deps, overridden=overridden)
    return patch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="каталог репозитория конфигурации")
    parser.add_argument("--config", default=os.environ.get("EF_CONFIG"), required=not os.environ.get("EF_CONFIG"),
                        help="настройки проекта, например .reper/config.json; по умолчанию переменная EF_CONFIG")
    parser.add_argument("--commit", default="HEAD",
                        help="коммит: собрать исправления, которые он изменил (как --range <коммит>^..<коммит>)")
    parser.add_argument("--range", dest="rev_range",
                        help="диапазон A..B: собрать исправления, которые изменились от A к B, и отозвать исчезнувшие (для CI)")
    parser.add_argument("--base", help="релиз в продуктиве: тег или коммит; по умолчанию ближайший тег")
    parser.add_argument("--task", help="собрать все исправления задачи после релиза")
    parser.add_argument("--commits", help="явный список коммитов через запятую - одно исправление без отбора по признаку EF")
    parser.add_argument("--out", default="build/ef", help="каталог результата, внутри - каталог на каждое исправление")
    parser.add_argument("--format", default="xml,edt", help="что формировать: xml, edt или оба через запятую")
    parser.add_argument("--pull-deps", action="store_true",
                        help="добавлять в исправление вызываемые методы, которых нет в релизе")
    parser.add_argument("--strict", action="store_true",
                        help="завершаться с ошибкой, если часть изменений не перенесена в расширение")
    feed_group = parser.add_mutually_exclusive_group()
    feed_group.add_argument("--feed-dir", help="лента исправлений в каталоге: версии расширений и отзыв устаревших исправлений")
    feed_group.add_argument("--feed-url", help="лента исправлений в реестре пакетов GitLab, см. ef_publish.py")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    repo = Repo(args.repo)
    template = config["extension_name"]

    if args.rev_range:
        before, args.commit = args.rev_range.split("..")
    else:
        before = f"{args.commit}^"
    base_ref = args.base or repo.describe_tag(args.commit, config["base_tag_match"])
    applied_versions = library_version(repo, config, base_ref)
    feed = published_patches(args, applied_versions) if applied_versions else []

    planner = Planner(repo, config["src_root"])
    current = collect(repo, planner, base_ref, args.commit, config["task_regex"], config["ef_regex"])
    plan_all(planner, current, args.pull_deps)
    names = current.by_name(template)

    revoked: dict[str, str] = {}
    if args.commits:
        targets = [explicit_patch(repo, planner, config, args, base_ref, current)]
    elif args.task:
        targets = [p for p in current.patches if p.task == args.task]
        if not targets:
            print(f"После релиза {base_ref} нет EF-коммитов задачи {args.task}")
    else:
        previous: dict[str, list[str]] = {}
        if repo.is_ancestor(repo.rev_parse(base_ref), before):
            earlier = collect(repo, planner, base_ref, before, config["task_regex"], config["ef_regex"])
            previous = {name: [c.sha for c in p.commits] for name, p in earlier.by_name(template).items()}
        targets = [p for name, p in names.items() if previous.get(name) != [c.sha for c in p.commits]]
        for name, commits in previous.items():
            if name not in names or names[name].plan.empty and commits != [c.sha for c in names[name].commits]:
                revoked[name] = "Коммиты исправления откачены или вошли в другое исправление"
        # Лента: действующие исправления, которых по истории больше нет (например, собранные по задаче целиком
        # до перехода на исправление на коммит). Только если все их коммиты в истории до args.commit:
        # запуск по старому коммиту не должен отзывать более новые исправления.
        prefix = template.split("{")[0]
        for entry in feed:
            name = entry.get("name", "")
            if entry.get("revoked") or name in revoked or not name.startswith(prefix):
                continue
            if name in names and not names[name].plan.empty:
                continue
            if all(repo.is_ancestor(sha, args.commit) for sha in entry.get("commits", [])):
                revoked[name] = "Исправления нет среди исправлений по истории после релиза"
        if not targets and not revoked:
            print(f"В диапазоне {before}..{args.commit} исправления не изменились")

    codes = [revoke_patch(config, args, base_ref, name, reason, applied_versions) for name, reason in revoked.items()]
    for patch in targets:
        codes.append(build_patch(repo, config, args, patch, applied_versions, current.notes, feed))
    if 3 in codes:
        return 3
    return 0 if 0 in codes else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
