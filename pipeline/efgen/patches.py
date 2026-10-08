# -*- coding: utf-8 -*-
"""Исправления из EF-коммитов: одно исправление на EF-коммит, порядок установки - по истории.

Исправление содержит только то, что изменил его коммит. Несколько исправлений могут перехватывать
один метод: база ставит их по возрастанию порядка, и последний перехватчик работает поверх прежних.
Перехватить метод, которого нет в релизе, нельзя (&Вместо - только для методов конфигурации), а добавить его
в два расширения - получить два одноименных метода в одном модуле. Поэтому коммиты, связанные такими
методами и модулями (изменяют их или вызывают), собираются в одно исправление.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gitutil import Commit, Repo
from .planner import Plan, Planner, method_calls, resolve_reverts
from .titles import FIRE_RE


@dataclass
class Patch:
    task: str
    number: str                 # номер в имени: порядковый номер первого EF-коммита задачи после релиза
    order: int                  # порядок установки: номер последнего коммита в истории после релиза
    commits: list[Commit]
    merged: list[str] = field(default_factory=list)     # почему коммиты собраны в одно исправление
    plan: Plan | None = None
    reverts: list[str] = field(default_factory=list)    # коммиты исправления, которые откатывают другие коммиты

    @property
    def head(self) -> Commit:
        return self.commits[-1]

    @property
    def fire(self) -> bool:
        """Срочное исправление: пометка FIRE в теме хотя бы одного его коммита."""
        return any(re.search(FIRE_RE, c.subject) for c in self.commits)

    @property
    def revert(self) -> bool:
        """Откат: в исправлении есть действующий откат (git revert) EF-коммита, он возвращает методы к прежнему тексту."""
        return bool(self.reverts)

    def name(self, template: str) -> str:
        """Имя расширения по шаблону: {task} - номер задачи, {n} - номер исправления, {fire} - FIRE_ у срочного,
        {rev} - REV_ у отката.

        FIRE в имени нужен базе: срочное расширение она находит по имени и перезапускает сеансы со старой версией.
        REV - чтобы откат было видно по имени в списках расширений базы.
        """
        return template.format(task=self.task, n=self.number, fire="FIRE_" if self.fire else "",
                               rev="REV_" if self.revert else "")


@dataclass
class Patches:
    base_ref: str
    patches: list[Patch]        # по возрастанию порядка
    notes: list[str]            # откаты

    def by_name(self, template: str) -> dict[str, Patch]:
        return {p.name(template): p for p in self.patches}


def ef_commits(repo: Repo, base_sha: str, head_ref: str, task_regex: str, ef_regex: str) -> list[Commit]:
    """EF-коммиты после релиза с номером задачи, от старых к новым."""
    re_task, re_ef = re.compile(task_regex), re.compile(ef_regex)
    return [c for c in repo.commits(f"{base_sha}..{repo.rev_parse(head_ref)}")
            if re_ef.search(c.subject) and re_task.search(c.subject)]


def collect(repo: Repo, planner: Planner, base_ref: str, head_ref: str, task_regex: str, ef_regex: str) -> Patches:
    """Исправления по EF-коммитам между релизом и head_ref, без планов (см. plan_all)."""
    re_task = re.compile(task_regex)
    base_sha = repo.rev_parse(base_ref)
    positions = {c.sha: k for k, c in enumerate(repo.commits(f"{base_sha}..{repo.rev_parse(head_ref)}"), 1)}
    commits = ef_commits(repo, base_sha, head_ref, task_regex, ef_regex)

    task_of: dict[str, str] = {}
    number_of: dict[str, int] = {}
    counters: dict[str, int] = {}
    for commit in commits:
        task = re_task.search(commit.subject).group(1)
        counters[task] = counters.get(task, 0) + 1
        task_of[commit.sha], number_of[commit.sha] = task, counters[task]

    kept, reverts, notes = resolve_reverts(repo, commits)
    revert_shas = {c.sha for c in reverts}
    # Действующий откат - обычное изменение: он возвращает методы, которые могут перехватывать другие исправления.
    active = sorted(kept + reverts, key=lambda c: positions[c.sha])

    parent = {c.sha: c.sha for c in active}

    def find(sha: str) -> str:
        while parent[sha] != sha:
            parent[sha] = parent[parent[sha]]
            sha = parent[sha]
        return sha

    owners: dict[tuple[str, str], Commit] = {}      # метод или модуль не из релиза -> первый коммит, который его изменил
    reasons: dict[str, list[str]] = {}

    def link(commit: Commit, item: tuple[str, str], why: str) -> None:
        owner = owners.get(item)
        if owner is None:
            return
        a, b = find(owner.sha), find(commit.sha)
        if a != b:
            parent[b] = a
            reasons.setdefault(commit.sha, []).append(f"{owner.short} и {commit.short}: {why}")

    def not_in_release(rel: str, key: str) -> tuple[str, str] | None:
        """Элемент, которого нет в релизе: ("", путь) - модуль целиком, (путь, ключ) - метод."""
        base = planner.module(base_sha, rel)
        if base is None:
            return "", rel
        return None if key in base.by_key() else (rel, key)

    for commit in active:
        changes = planner.changes(commit)
        items = []
        for rel, keys in changes.methods.items():
            for key in keys:
                item = not_in_release(rel, key)
                if item is not None and item not in items:
                    items.append(item)
        for item in items:
            title = item[1] if not item[0] else f"{item[0]}: {item[1]}"
            link(commit, item, f"изменяют {title}, которого нет в релизе")
            owners.setdefault(item, commit)
        # Вызов метода, который добавил другой EF-коммит, тоже связывает коммиты: порознь расширение
        # с вызовом не пройдет проверку относительно релиза.
        common_modules = {n.lower(): n for n in repo.ls_dir(commit.sha, f"{planner.src_root}/CommonModules")}
        for rel, keys in changes.methods.items():
            module = planner.module(commit.sha, rel)
            if module is None:
                continue
            methods = module.by_key()
            for key in keys:
                if key not in methods:
                    continue
                for call_rel, name in method_calls(methods[key], rel, common_modules):
                    item = not_in_release(call_rel, name.lower())
                    if item is not None and item not in items:
                        link(commit, item, f"{methods[key].name} вызывает {name}, которого нет в релизе")

    groups: dict[str, list[Commit]] = {}
    for commit in active:
        groups.setdefault(find(commit.sha), []).append(commit)
    patches = []
    for members in groups.values():
        first = members[0]
        merged = [r for c in members for r in reasons.get(c.sha, [])]
        undoing = [c.sha for c in members if c.sha in revert_shas]
        patches.append(Patch(task_of[first.sha], str(number_of[first.sha]), positions[members[-1].sha], members, merged,
                             reverts=undoing))
    patches.sort(key=lambda p: p.order)
    return Patches(base_ref, patches, notes)


def plan_all(planner: Planner, patches: Patches, pull_deps: bool = False) -> None:
    """Планы исправлений по возрастанию порядка: метод, который перехватывает более раннее исправление,
    перехватывается и более поздним, даже если вернулся к тексту релиза."""
    overridden: set[tuple[str, str]] = set()
    for patch in patches.patches:
        patch.plan = planner.build(patch.task, patches.base_ref, patch.head.sha, patch.commits,
                                   pull_deps=pull_deps, overridden=overridden)
        for module_plan in patch.plan.modules:
            overridden |= {(module_plan.ref.path, m.key) for m in module_plan.overrides}


def methods_of(plan: Plan) -> list[str]:
    """Что исправление перехватывает и добавляет: "Вид.Объект.Метод" или "Вид.Объект" для нового модуля.

    По этому списку база находит исправления, которые перехватывают одни и те же методы.
    """
    result = []
    for module_plan in plan.modules:
        if module_plan.is_new:
            result.append(module_plan.ref.title)
            continue
        result += [f"{module_plan.ref.title}.{m.name}" for m in module_plan.overrides + module_plan.additions]
    return result
