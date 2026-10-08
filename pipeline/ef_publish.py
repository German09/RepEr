# -*- coding: utf-8 -*-
"""Публикует собранное исправление в ленту исправлений и обновляет ее оглавление index.json.

Лента - каталог на версию релиза: файлы <имя>_<версия>.zip и index.json. Расположение:
  --feed-dir  каталог на диске или сетевом ресурсе;
  --feed-url  реестр пакетов GitLab (generic), например
              %CI_API_V4_URL%/projects/%CI_PROJECT_ID%/packages/generic/ef
              Токен берется из переменной окружения CI_JOB_TOKEN или EF_FEED_TOKEN.

Если в ef.json признак revoke (коммиты исправления откачены или вошли в другое исправление), архива нет:
все сборки исправления в оглавлении помечаются отозванными, и базы их удаляют.

В записи оглавления order - порядок установки (база ставит исправления по возрастанию), methods - что
исправление перехватывает и добавляет: по нему база находит исправления, которые нужно переустановить после
исправления с меньшим порядком.

Пример:
    python ef_publish.py --ef-dir build/ef/EF_9731 --feed-dir \\\\server\\share\\ef
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime


def upsert(index: dict, entry: dict) -> dict:
    """Добавляет исправление в оглавление.

    Прежние сборки исправления с тем же именем помечаются отозванными: по этому признаку
    информационная база удалит старую сборку перед установкой новой.
    """
    patches = [p for p in index.get("patches", []) if p["uuid"] != entry["uuid"]]
    for patch in patches:
        if patch["name"] == entry["name"]:
            patch["revoked"] = True
    patches.append(entry)
    index["patches"] = patches
    return index


def revoke(index: dict, name: str) -> int:
    """Помечает отозванными все сборки исправления. Возвращает число сборок, отозванных сейчас."""
    count = 0
    for patch in index.get("patches", []):
        if patch["name"] == name and not patch.get("revoked"):
            patch["revoked"] = True
            count += 1
    return count


class DirFeed:
    def __init__(self, root: str):
        self.root = root

    def read(self, version: str, name: str) -> bytes | None:
        path = os.path.join(self.root, version, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def write(self, version: str, name: str, data: bytes) -> None:
        os.makedirs(os.path.join(self.root, version), exist_ok=True)
        with open(os.path.join(self.root, version, name), "wb") as f:
            f.write(data)


class GitlabFeed:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        if os.environ.get("EF_FEED_TOKEN"):
            self.headers = {"PRIVATE-TOKEN": os.environ["EF_FEED_TOKEN"]}
        else:
            self.headers = {"JOB-TOKEN": os.environ.get("CI_JOB_TOKEN", "")}

    def read(self, version: str, name: str) -> bytes | None:
        request = urllib.request.Request(f"{self.url}/{version}/{name}", headers=self.headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def write(self, version: str, name: str, data: bytes) -> None:
        request = urllib.request.Request(f"{self.url}/{version}/{name}", data=data, headers=self.headers, method="PUT")
        with urllib.request.urlopen(request) as response:
            response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ef-dir", required=True, help="каталог исправления с ef.json и архивом")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--feed-dir")
    target.add_argument("--feed-url")
    args = parser.parse_args()

    with open(os.path.join(args.ef_dir, "ef.json"), encoding="utf-8") as f:
        info = json.load(f)
    feed = DirFeed(args.feed_dir) if args.feed_dir else GitlabFeed(args.feed_url)
    release = info["applied_versions"]

    if info.get("revoke"):
        raw = feed.read(release, "index.json")
        index = json.loads(raw.decode("utf-8")) if raw else {}
        count = revoke(index, info["name"])
        if not count:
            print(f"{info['name']}: в ленте релиза {release} нет действующих сборок, отзывать нечего")
            return 0
        feed.write(release, "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"))
        print(f"Отозвано: {info['name']}, релиз {release}, сборок: {count}")
        return 0

    with open(os.path.join(args.ef_dir, info["name"] + ".zip"), "rb") as f:
        archive = f.read()
    file_name = f"{info['name']}_{info['version']}.zip"

    raw = feed.read(release, "index.json")
    index = json.loads(raw.decode("utf-8")) if raw else {"configuration": info["applied_name"], "version": release}
    upsert(index, {
        "uuid": info["uuid"],
        "name": info["name"],
        "patch_version": info["version"],
        "order": info["order"],
        "description": info["description"],
        "task": info.get("task_code", ""),
        "commit_title": info.get("commit_title", ""),
        "commit_url": info.get("commit_url", ""),
        "fire": info.get("fire", False),
        "revert": info.get("revert", False),
        "modified_metadata": info["modified_metadata"],
        "methods": info["methods"],
        "file": file_name,
        "size": len(archive),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "commits": info["commits"],
        "published": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "revoked": False,
    })
    feed.write(release, file_name, archive)
    feed.write(release, "index.json", json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"Опубликовано: {file_name}, релиз {release}, исправлений в оглавлении: {len(index['patches'])}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
