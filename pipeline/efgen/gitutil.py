# -*- coding: utf-8 -*-
"""Чтение истории и содержимого файлов из git без обращения к рабочему каталогу."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache

RE_REVERTS = re.compile(r"This reverts commit ([0-9a-f]{7,40})")


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    author: str
    date: str
    # Дата и время автора коммита в ISO 8601 с часовым поясом: 2026-10-07T13:11:05+03:00.
    iso_date: str = ""

    @property
    def short(self) -> str:
        return self.sha[:10]


class Repo:
    def __init__(self, path: str):
        self.path = path
        self._show = lru_cache(maxsize=4096)(self._show_uncached)

    def _run(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", self.path, *args],
            capture_output=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc.stdout.decode("utf-8", "replace")

    def rev_parse(self, ref: str) -> str:
        return self._run("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def is_ancestor(self, ancestor: str, ref: str) -> bool:
        """Достижим ли ancestor из ref."""
        proc = subprocess.run(["git", "-C", self.path, "merge-base", "--is-ancestor", ancestor, ref], capture_output=True)
        return proc.returncode == 0

    def describe_tag(self, ref: str, match: str = "*") -> str:
        """Ближайший тег, достижимый из ref."""
        return self._run("describe", "--tags", "--abbrev=0", "--match", match, ref).strip()

    def tags_at(self, sha: str) -> list[str]:
        """Теги, указывающие на объект sha: коммит легкого тега или сам объект аннотированного."""
        out = self._run("for-each-ref", "refs/tags", "--format=%(refname:short)%1f%(objectname)%1f%(*objectname)")
        result = []
        for line in out.splitlines():
            name, obj, peeled = line.split("\x1f")
            if sha in (obj, peeled):
                result.append(name)
        return result

    def commit(self, ref: str) -> Commit:
        out = self._run("log", "-1", "--format=%H%x1f%s%x1f%an%x1f%ad%x1f%aI", "--date=short", ref).strip()
        sha, subject, author, date, iso_date = out.split("\x1f")
        return Commit(sha, subject, author, date, iso_date)

    def message(self, sha: str) -> str:
        """Полный текст комментария коммита."""
        return self._run("log", "-1", "--format=%B", sha)

    def reverted(self, sha: str) -> str | None:
        """Коммит, который откатывает sha (git revert пишет "This reverts commit <sha>."), или None.

        Если git не находит коммит по сокращенному хэшу, возвращается хэш из текста как есть.
        """
        found = RE_REVERTS.search(self.message(sha))
        if not found:
            return None
        full = self._run("rev-parse", "--verify", "-q", f"{found.group(1)}^{{commit}}", check=False).strip()
        return full or found.group(1)

    def commits(self, rev_range: str, *paths: str) -> list[Commit]:
        """Коммиты диапазона без merge-коммитов, от старых к новым."""
        args = ["log", "--no-merges", "--reverse", "--format=%H%x1f%s%x1f%an%x1f%ad%x1f%aI", "--date=short", rev_range]
        if paths:
            args += ["--", *paths]
        result = []
        for line in self._run(*args).splitlines():
            if line:
                sha, subject, author, date, iso_date = line.split("\x1f")
                result.append(Commit(sha, subject, author, date, iso_date))
        return result

    def changed_files(self, sha: str, root: str) -> list[tuple[str, str]]:
        """Файлы, измененные коммитом относительно первого родителя: [(статус, путь)]."""
        return self.diff(f"{sha}^", sha, root)

    def diff(self, old: str, new: str, root: str) -> list[tuple[str, str]]:
        """Файлы, которые отличаются между ревизиями: [(статус, путь)], статусы A, M, D, T."""
        out = self._run("diff", "--name-status", "--no-renames", "-z", old, new, "--", root)
        parts = out.split("\0")
        return [(parts[k], parts[k + 1]) for k in range(0, len(parts) - 1, 2)]

    def blobs(self, ref: str, paths: list[str]) -> dict[str, bytes]:
        """Содержимое файлов ревизии одним запуском git cat-file: {путь: байты}."""
        request = "".join(f"{ref}:{path}\n" for path in paths).encode("utf-8")
        proc = subprocess.run(["git", "-C", self.path, "cat-file", "--batch"], input=request, capture_output=True)
        if proc.returncode != 0:
            raise GitError(f"git cat-file: {proc.stderr.decode('utf-8', 'replace').strip()}")
        out, pos, result = proc.stdout, 0, {}
        for path in paths:
            end = out.index(b"\n", pos)
            header = out[pos:end].split()
            if len(header) != 3:
                raise GitError(f"Нет файла {ref}:{path}")
            size = int(header[2])
            result[path] = out[end + 1:end + 1 + size]
            pos = end + 1 + size + 1
        return result

    def ls_dir(self, ref: str, path: str) -> set[str]:
        """Имена элементов каталога в указанной ревизии."""
        out = self._run("ls-tree", "--name-only", "-z", ref, path.rstrip("/") + "/", check=False)
        return {p.rsplit("/", 1)[-1] for p in out.split("\0") if p}

    def _show_uncached(self, ref: str, path: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", self.path, "show", f"{ref}:{path}"],
            capture_output=True,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8-sig", "replace")

    def show(self, ref: str, path: str) -> str | None:
        """Содержимое файла в указанной ревизии или None, если файла там нет."""
        return self._show(ref, path)
