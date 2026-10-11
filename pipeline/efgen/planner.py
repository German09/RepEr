# -*- coding: utf-8 -*-
"""Построение плана исправления: какие методы переопределить, какие добавить.

База сравнения - релиз, который работает в продуктиве (по умолчанию ближайший тег).
В расширение попадают только методы, затронутые EF-коммитами задачи; текст метода
берется из целевого коммита.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .adopt import OBJECT_KINDS, Member, adopted_members
from .bsl import IDENT, Method, Module, parse_module
from .gitutil import Commit, Repo
from .kinds import KINDS, ModuleRef, classify

RE_CALL = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_.])(" + IDENT + r")\s*\(")
RE_MODULE_CALL = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_.])(" + IDENT + r")\s*\.\s*(" + IDENT + r")\s*\(")


@dataclass
class ModulePlan:
    ref: ModuleRef
    is_new: bool = False                                    # модуля нет в базовом релизе
    overrides: list[Method] = field(default_factory=list)   # есть в релизе -> &Вместо
    additions: list[Method] = field(default_factory=list)   # нет в релизе -> добавить как есть
    source: str = ""                                        # полный текст (только для is_new)
    touched_by: dict[str, list[str]] = field(default_factory=dict)  # метод -> коммиты задачи
    adopted: list[Member] = field(default_factory=list)     # реквизиты и табличные части для модуля объекта


@dataclass
class Plan:
    task: str
    base_ref: str
    base_sha: str
    head: Commit
    commits: list[Commit]
    modules: list[ModulePlan] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)    # то, что в расширение не попало
    warnings: list[str] = field(default_factory=list)       # риски, которые стоит проверить глазами

    @property
    def empty(self) -> bool:
        return not any(m.overrides or m.additions or m.is_new for m in self.modules)


def method_calls(method: Method, rel: str, common_modules: dict[str, str]) -> list[tuple[str, str]]:
    """Вызовы из метода: [(путь модуля, имя метода)].

    common_modules - имена общих модулей конфигурации в нижнем регистре -> имя как есть.
    """
    calls = [(rel, name) for name in RE_CALL.findall(method.code)]
    for module_name, name in RE_MODULE_CALL.findall(method.code):
        if module_name.lower() in common_modules:
            calls.append((f"CommonModules/{common_modules[module_name.lower()]}/Module.bsl", name))
    return calls


@dataclass
class CommitChanges:
    methods: dict[str, set[str]] = field(default_factory=dict)      # путь модуля -> ключи измененных методов
    refs: dict[str, ModuleRef] = field(default_factory=dict)        # путь модуля -> вид модуля
    unsupported: list[str] = field(default_factory=list)


def _touched(old: Module | None, new: Module | None) -> tuple[set[str], bool]:
    """Методы, которые различаются между двумя версиями модуля, и признак изменения кода вне методов."""
    old_methods = old.by_key() if old else {}
    new_methods = new.by_key() if new else {}
    keys = set()
    for key in old_methods.keys() | new_methods.keys():
        a, b = old_methods.get(key), new_methods.get(key)
        if a is None or b is None or a.norm != b.norm:
            keys.add(key)
    residue_changed = (old.residue if old else "") != (new.residue if new else "")
    return keys, residue_changed


class Planner:
    def __init__(self, repo: Repo, src_root: str):
        self.repo = repo
        self.src_root = src_root.strip("/")
        self._parsed: dict[tuple[str, str], Module | None] = {}
        self._changes: dict[str, CommitChanges] = {}

    def module(self, ref: str, rel_path: str) -> Module | None:
        key = (ref, rel_path)
        if key not in self._parsed:
            text = self.repo.show(ref, f"{self.src_root}/{rel_path}")
            self._parsed[key] = parse_module(text) if text is not None else None
        return self._parsed[key]

    def _rel(self, path: str) -> str:
        return path[len(self.src_root) + 1:]

    def changes(self, commit: Commit) -> CommitChanges:
        """Что изменил коммит относительно первого родителя: методы по модулям и то, что не переносится."""
        if commit.sha in self._changes:
            return self._changes[commit.sha]
        result = CommitChanges()
        for status, path in self.repo.changed_files(commit.sha, self.src_root):
            rel = self._rel(path)
            ref, reason = classify(rel)
            if ref is None:
                result.unsupported.append(f"{commit.short} {rel}: {reason}")
                continue
            if status == "D":
                result.unsupported.append(f"{commit.short} {rel}: модуль удален, расширением это не выразить")
                continue
            keys, residue_changed = _touched(self.module(commit.sha + "^", rel), self.module(commit.sha, rel))
            if residue_changed:
                result.unsupported.append(
                    f"{commit.short} {rel}: изменен код вне процедур и функций "
                    f"(переменные модуля, тело модуля, инструкции препроцессора)")
            result.refs[rel] = ref
            if keys:
                result.methods[rel] = keys
        self._changes[commit.sha] = result
        return result

    def build(self, task: str, base_ref: str, head_ref: str, commits: list[Commit],
              pull_deps: bool = False, overridden: set[tuple[str, str]] | None = None) -> Plan:
        """План исправления по коммитам; текст методов - из head_ref.

        overridden - методы (путь модуля, ключ), которые перехватывают исправления, установленные раньше этого.
        Такой метод перехватывается, даже если его текст совпал с релизом: иначе в базе остался бы прежний перехватчик.
        """
        repo = self.repo
        base_sha = repo.rev_parse(base_ref)
        head = repo.commit(head_ref)
        plan = Plan(task, base_ref, base_sha, head, commits)
        task_shas = {c.sha for c in commits}
        overridden = overridden or set()

        # 1. Что затронули коммиты исправления.
        touched: dict[str, dict[str, list[str]]] = {}      # путь -> метод -> [коммит]
        refs: dict[str, ModuleRef] = {}
        for commit in commits:
            changes = self.changes(commit)
            plan.unsupported += changes.unsupported
            refs.update(changes.refs)
            for rel, keys in changes.methods.items():
                for key in keys:
                    touched.setdefault(rel, {}).setdefault(key, []).append(commit.short)

        # 2. Итоговое состояние затронутых методов относительно релиза.
        for rel in sorted(touched):
            ref = refs[rel]
            base, cur = self.module(base_sha, rel), self.module(head.sha, rel)
            if cur is None:
                plan.unsupported.append(f"{rel}: модуль удален в {head.short}")
                continue
            module_plan = ModulePlan(ref, touched_by=touched[rel])
            if base is None:
                if ref.kind.cls != "CommonModule":
                    plan.unsupported.append(f"{rel}: объекта нет в релизе {base_ref}, добавление объектов не автоматизировано")
                    continue
                module_plan.is_new = True
                module_plan.source = repo.show(head.sha, f"{self.src_root}/{rel}") or ""
                plan.modules.append(module_plan)
                continue
            base_methods, cur_methods = base.by_key(), cur.by_key()
            for key in sorted(touched[rel], key=lambda k: cur_methods[k].start if k in cur_methods else 0):
                method = cur_methods.get(key)
                if method is None:
                    if key in base_methods:
                        plan.warnings.append(
                            f"{ref.title}: метод {base_methods[key].name} удален, "
                            f"в продуктиве он останется до обновления релиза")
                    continue
                if key not in base_methods:
                    module_plan.additions.append(method)
                elif base_methods[key].norm != method.norm or (rel, key) in overridden:
                    module_plan.overrides.append(method)
            if module_plan.overrides or module_plan.additions:
                plan.modules.append(module_plan)

        # 3. Зависимости от кода, которого в релизе нет.
        self._resolve_dependencies(plan, pull_deps)

        # 4. Реквизиты, к которым обращаются перенесенные методы модуля объекта.
        for module_plan in plan.modules:
            self._adopt(plan, module_plan)

        # 5. Чужие правки тех же методов после релиза.
        for module_plan in plan.modules:
            if module_plan.is_new:
                continue
            rel = module_plan.ref.path
            mine = {m.key: m.name for m in module_plan.overrides + module_plan.additions}
            for commit in repo.commits(f"{base_sha}..{head.sha}", f"{self.src_root}/{rel}"):
                if commit.sha in task_shas:
                    continue
                keys, _ = _touched(self.module(commit.sha + "^", rel), self.module(commit.sha, rel))
                for key in sorted(keys & mine.keys()):
                    plan.warnings.append(
                        f"{module_plan.ref.title}.{mine[key]}: после релиза метод меняли и вне этой задачи - "
                        f"{commit.short} \"{commit.subject}\". Эти правки тоже попадут в исправление")
        return plan

    def _adopt(self, plan: Plan, module_plan: ModulePlan) -> None:
        ref = module_plan.ref
        if module_plan.is_new or ref.module_file != "ObjectModule.bsl" or ref.kind.folder not in OBJECT_KINDS:
            return
        mdo = f"{self.src_root}/{ref.mdo_path}"
        module_plan.adopted, missing = adopted_members(
            module_plan.overrides + module_plan.additions,
            self.repo.show(plan.base_sha, mdo), self.repo.show(plan.head.sha, mdo))
        for name in missing:
            plan.warnings.append(f"{ref.title}: реквизита {name} нет в релизе {plan.base_ref}, "
                                 f"добавление реквизитов не автоматизировано")

    def _resolve_dependencies(self, plan: Plan, pull_deps: bool) -> None:
        """Ищет вызовы методов, которых нет в релизе и нет в самом исправлении."""
        base_sha, head_sha = plan.base_sha, plan.head.sha
        by_path = {m.ref.path: m for m in plan.modules}
        queue: list[tuple[ModulePlan, Method]] = [
            (mp, m) for mp in plan.modules for m in mp.overrides + mp.additions]
        seen: set[tuple[str, str]] = set()
        common_modules = {n.lower(): n for n in self.repo.ls_dir(head_sha, f"{self.src_root}/CommonModules")}

        def missing(rel: str, key: str) -> Method | None:
            """Метод есть в целевом коммите, но отсутствует в релизе и в исправлении."""
            cur = self.module(head_sha, rel)
            if cur is None or key not in cur.by_key():
                return None
            base = self.module(base_sha, rel)
            if base is not None and key in base.by_key():
                return None
            module_plan = by_path.get(rel)
            if module_plan and (module_plan.is_new or any(m.key == key for m in module_plan.additions)):
                return None
            return cur.by_key()[key]

        while queue:
            module_plan, method = queue.pop()
            for rel, name in method_calls(method, module_plan.ref.path, common_modules):
                key = name.lower()
                if (rel, key) in seen:
                    continue
                seen.add((rel, key))
                dep = missing(rel, key)
                if dep is None:
                    continue
                ref, _ = classify(rel)
                where = f"{module_plan.ref.title}.{method.name}"
                if not pull_deps or ref is None:
                    plan.warnings.append(
                        f"{where} вызывает {ref.title if ref else rel}.{dep.name}, которого нет в релизе "
                        f"{plan.base_ref} и нет в исправлении")
                    continue
                target = by_path.get(rel)
                if target is None:
                    target = ModulePlan(ref)
                    if self.module(base_sha, rel) is None:
                        target.is_new = True
                        target.source = self.repo.show(head_sha, f"{self.src_root}/{rel}") or ""
                    by_path[rel] = target
                    plan.modules.append(target)
                if not target.is_new:
                    target.additions.append(dep)
                    target.touched_by.setdefault(dep.key, []).append("зависимость")
                    queue.append((target, dep))
                plan.warnings.append(f"{where}: в исправление добавлена зависимость {ref.title}.{dep.name}")


def select_commits(repo: Repo, base_ref: str, head_ref: str, task_regex: str, ef_regex: str,
                   task: str | None = None) -> tuple[str, list[Commit]]:
    """Определяет задачу по целевому коммиту и отбирает EF-коммиты этой задачи после релиза."""
    head = repo.commit(head_ref)
    re_task, re_ef = re.compile(task_regex), re.compile(ef_regex)
    if task is None:
        found = re_task.search(head.subject)
        if not found:
            raise ValueError(f"в комментарии коммита {head.short} нет номера задачи: {head.subject}")
        task = found.group(1)
    selected = []
    for commit in repo.commits(f"{repo.rev_parse(base_ref)}..{head.sha}"):
        if re_ef.search(commit.subject) and any(m.group(1) == task for m in re_task.finditer(commit.subject)):
            selected.append(commit)
    return task, selected


def drop_reverts(repo: Repo, commits: list[Commit]) -> tuple[list[Commit], list[str]]:
    """Убирает из коммитов откаты (git revert) вместе с тем, что они откатили.

    Откат коммита убирает этот коммит, откат отката возвращает его. Откат изменения, которое
    уже в релизе, расширением не переносится: такое изменение уйдет со следующим релизом.
    Возвращает оставшиеся коммиты и пояснения для отчета.
    """
    kept, _, notes = resolve_reverts(repo, commits)
    return kept, notes


def resolve_reverts(repo: Repo, commits: list[Commit]) -> tuple[list[Commit], list[Commit], list[str]]:
    """Как drop_reverts, но возвращает еще действующие откаты: откаты, которые убрали коммит из списка
    и сами не отменены. Порядок коммитов сохраняется.
    """
    kept: list[Commit] = []
    undone: dict[str, Commit] = {}  # откат -> коммит, который он убрал
    notes = []
    for commit in commits:
        target = repo.reverted(commit.sha)
        if target is None:
            if commit.subject.startswith("Revert "):
                message = repo.message(commit.sha).strip().replace("\n", " | ")
                notes.append(f'{commit.short} похож на откат, но в комментарии нет "This reverts commit <хэш>": '
                             f"считается обычным коммитом. Комментарий: {message[:300]}")
            kept.append(commit)
            continue
        # Хэш из комментария может быть сокращенным, если git его не нашел.
        removed = next((c for c in kept if c.sha.startswith(target)), None)
        undone_by = next((sha for sha in undone if sha.startswith(target)), None)
        if removed is not None:
            kept.remove(removed)
            undone[commit.sha] = removed
            notes.append(f"{commit.short} откатывает {removed.short}: {removed.short} не входит в исправления")
        elif undone_by is not None:
            kept.append(undone.pop(undone_by))
            notes.append(f"{commit.short} отменяет откат {target[:10]}: {kept[-1].short} снова в исправлениях")
        else:
            notes.append(f"{commit.short} откатывает {target[:10]}, которого нет среди EF-коммитов "
                         f"после релиза: в исправление не переносится")
    order = {c.sha: k for k, c in enumerate(commits)}
    kept.sort(key=lambda c: order[c.sha])
    reverts = sorted((c for c in commits if c.sha in undone), key=lambda c: order[c.sha])
    return kept, reverts, notes
