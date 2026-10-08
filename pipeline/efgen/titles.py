# -*- coding: utf-8 -*-
"""Название исправления, код задачи и признак срочности из темы коммита.

Тема EF-коммита: «system-9634 EF FIRE ФРК». Название исправления - тема без кодов задач,
пометок EF и FIRE и лишних разделителей: «ФРК». FIRE - срочное исправление.
Тема отката от git revert - «Revert "system-9634 EF ФРК"»: название - название откаченного коммита.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

FIRE_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])FIRE(?![0-9A-Za-zА-Яа-яЁё_])"
SEPARATORS = " \t,;:.-\u2013\u2014|/"  # с короткими и длинными тире
REVERT_RE = r'^Revert "(.*)"$'


@dataclass
class PatchTitle:
    title: str        # название исправления для пользователя
    task_code: str    # полный код задачи, например system-9634
    fire: bool        # срочное исправление


def patch_title(subject: str, task: str, task_regex: str, ef_regex: str) -> PatchTitle:
    """Разбирает тему коммита исправления задачи task.
    """
    task_code = ""
    for found in re.finditer(task_regex, subject):
        if found.group(1) == task:
            task_code = found.group(0)
            break

    fire = re.search(FIRE_RE, subject) is not None
    title = subject.strip()
    # Откат отката: Revert "Revert "...""
    while (found := re.match(REVERT_RE, title)) is not None:
        title = found.group(1).strip()
    title = re.sub(task_regex, " ", title)
    title = re.sub(ef_regex, " ", title)
    title = re.sub(FIRE_RE, " ", title)
    title = re.sub(r"\s*[,;:]\s*(?=[,;:]|$)", " ", title)
    title = re.sub(r"\s+", " ", title).strip(SEPARATORS)

    return PatchTitle(title=title, task_code=task_code, fire=fire)


def commit_link(sha: str, subject: str, commit_url: str = "") -> tuple[str, str]:
    """Представление и адрес коммита: тема коммита и адрес по шаблону.

    commit_url - шаблон адреса коммита с подстановкой {sha}; пустой - адреса нет.
    """
    return subject, commit_url.format(sha=sha) if commit_url else ""
