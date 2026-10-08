# -*- coding: utf-8 -*-
"""Копирует ленту исправлений (пакет generic реестра GitLab) из одного проекта в другой.

Нужен при переезде пайплайна в другой проект: базы читают ленту по адресу проекта, а ef_publish.py
пишет в пакеты проекта, где идет пайплайн. Копируются все версии пакета (каталоги ленты). Из файлов
с одинаковым именем берется последний (index.json перезаписывается при каждой публикации). Архивы
копируются раньше index.json, чтобы база не увидела оглавление со ссылкой на еще не загруженный файл;
перед загрузкой оглавления проверяется, что все его архивы есть и sha256 совпадает. Файл, который
в проекте-приемнике уже есть с тем же содержимым, пропускается: повторный запуск безопасен.

Токен - переменная окружения EF_FEED_TOKEN (scope api, доступ к обоим проектам).

Пример:
    python feed_copy.py --api https://gitlab.domain.ru/api/v4 --source user/release --target user/system
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class Gitlab:
    def __init__(self, api: str, token: str):
        self.api = api.rstrip("/")
        self.headers = {"PRIVATE-TOKEN": token}

    def request(self, path: str, data: bytes | None = None, method: str = "GET") -> bytes:
        request = urllib.request.Request(f"{self.api}{path}", data=data, headers=self.headers, method=method)
        with urllib.request.urlopen(request) as response:
            return response.read()

    def pages(self, path: str) -> list[dict]:
        """Все страницы списка API."""
        items, page = [], 1
        separator = "&" if "?" in path else "?"
        while True:
            chunk = json.loads(self.request(f"{path}{separator}per_page=100&page={page}"))
            items.extend(chunk)
            if len(chunk) < 100:
                return items
            page += 1


def project_path(project: str) -> str:
    return "/projects/" + urllib.parse.quote(project, safe="")


def latest_files(gitlab: Gitlab, project: str, package_id: int) -> dict[str, dict]:
    """Последний файл пакета на каждое имя."""
    files: dict[str, dict] = {}
    for item in gitlab.pages(f"{project_path(project)}/packages/{package_id}/package_files"):
        known = files.get(item["file_name"])
        if known is None or item["id"] > known["id"]:
            files[item["file_name"]] = item
    return files


def target_files(gitlab: Gitlab, project: str, package: str, version: str) -> dict[str, dict]:
    for item in gitlab.pages(f"{project_path(project)}/packages?package_type=generic&package_name="
                             f"{urllib.parse.quote(package)}"):
        if item["name"] == package and item["version"] == version:
            return latest_files(gitlab, project, item["id"])
    return {}


def copy_version(gitlab: Gitlab, source: str, target: str, package: str, version: str, files: dict[str, dict],
                 dry_run: bool) -> int:
    """Копирует файлы версии пакета. Возвращает число загруженных файлов."""
    existing = target_files(gitlab, target, package, version)
    generic = f"/packages/generic/{urllib.parse.quote(package)}/{urllib.parse.quote(version)}"
    names = sorted(files, key=lambda name: (name == "index.json", name))
    downloaded: dict[str, str] = {}
    uploaded = 0
    for name in names:
        data = gitlab.request(f"{project_path(source)}{generic}/{urllib.parse.quote(name)}")
        sha256 = hashlib.sha256(data).hexdigest()
        downloaded[name] = sha256
        if name == "index.json":
            index = json.loads(data.decode("utf-8-sig"))
            for patch in index.get("patches", []):
                file_name = patch.get("file")
                if patch.get("revoked") or not file_name:
                    continue
                if file_name not in downloaded:
                    raise RuntimeError(f"{version}: в index.json архив {file_name}, которого нет в пакете")
                if patch.get("sha256") and patch["sha256"] != downloaded[file_name]:
                    raise RuntimeError(f"{version}: sha256 {file_name} не совпадает с index.json")
        if existing.get(name, {}).get("file_sha256") == sha256:
            print(f"  {name}: уже есть")
            continue
        print(f"  {name}: {len(data)} байт" + (" (не загружается: --dry-run)" if dry_run else ""))
        if not dry_run:
            gitlab.request(f"{project_path(target)}{generic}/{urllib.parse.quote(name)}", data=data, method="PUT")
            uploaded += 1
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="адрес API GitLab, например https://gitlab.domain.ru/api/v4")
    parser.add_argument("--source", required=True, help="проект-источник: путь или ID")
    parser.add_argument("--target", required=True, help="проект-приемник: путь или ID")
    parser.add_argument("--package", default="ef", help="имя пакета ленты")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет скопировано")
    args = parser.parse_args()

    token = os.environ.get("EF_FEED_TOKEN")
    if not token:
        print("Не задан токен: переменная окружения EF_FEED_TOKEN")
        return 1
    gitlab = Gitlab(args.api, token)
    try:
        packages = [p for p in gitlab.pages(f"{project_path(args.source)}/packages?package_type=generic&package_name="
                                            f"{urllib.parse.quote(args.package)}") if p["name"] == args.package]
        if not packages:
            print(f"В {args.source} нет пакета {args.package}")
            return 1
        uploaded = 0
        for package in sorted(packages, key=lambda p: p["version"]):
            print(f"{args.package} {package['version']}:")
            files = latest_files(gitlab, args.source, package["id"])
            uploaded += copy_version(gitlab, args.source, args.target, args.package, package["version"], files,
                                     args.dry_run)
    except urllib.error.HTTPError as error:
        print(f"Ошибка API GitLab: {error.code} {error.reason} ({error.url})")
        return 1
    except RuntimeError as error:
        print(error)
        return 1
    print(f"Загружено файлов: {uploaded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
