"""Снимки EDT по изменённым методам и месячная сводка для GitLab."""
import csv
import json
import os
import re
import subprocess
from html import escape
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

from ef_check import prepare_workspace, run_cli
from efgen.bsl import parse_module
from efgen.gitutil import Repo

ENV = os.environ
OUT = Path("build/quality").resolve()
PACKAGE = "edt-method-stat"
CONFIG = json.loads(Path(".reper/config.json").read_text(encoding="utf-8-sig"))
SRC = CONFIG["src_root"]
PROJECT_DIR = str(Path(SRC).parent.as_posix())
KINDS = dict(zip(
    "AccumulationRegisters BusinessProcesses Catalogs ChartsOfCharacteristicTypes CommonCommands CommonForms CommonModules Constants DataProcessors DocumentJournals Documents Enums ExchangePlans ExternalDataSources FilterCriteria HTTPServices InformationRegisters IntegrationServices Reports SettingsStorages Tasks WebServices Forms Commands Tables".split(),
    "РегистрНакопления БизнесПроцесс Справочник ПланВидовХарактеристик ОбщаяКоманда ОбщаяФорма ОбщийМодуль Константа Обработка ЖурналДокументов Документ Перечисление ПланОбмена ВнешнийИсточникДанных КритерийОтбора HTTPСервис РегистрСведений СервисИнтеграции Отчет ХранилищеНастроек Задача WebСервис Форма Команда Таблица".split(), strict=True))
MODULES = dict(zip(
    "Module CommandModule ManagerModule ObjectModule RecordSetModule ValueManagerModule SessionModule ManagedApplicationModule OrdinaryApplicationModule ExternalConnectionModule".split(),
    "Модуль МодульКоманды МодульМенеджера МодульОбъекта МодульНабораЗаписей МодульМенеджераЗначения МодульСеанса МодульУправляемогоПриложения МодульОбычногоПриложения МодульВнешнегоСоединения".split(), strict=True))


def location(path):
    parts = Path(path).relative_to(SRC).with_suffix("").parts
    if parts[0] == "Configuration":
        return "Конфигурация." + MODULES[parts[-1]]
    return ".".join([KINDS[p] if i % 2 == 0 else p for i, p in enumerate(parts[:-1])] + [MODULES[parts[-1]]])


def touched(repo, sha):
    result = {}
    for status, path in repo.changed_files(sha, SRC):
        if status == "D" or not path.endswith(".bsl"):
            continue
        old = repo.show(sha + "^", path) or ""
        new = repo.show(sha, path)
        before = {}
        for source, save in ((old, True), (new, False)):
            lines = source.splitlines()
            for m in parse_module(source).methods:
                start = m.start - len(m.doc) - len(m.annotations)
                body = lines[start - 1:m.end]
                key = (m.key, m.pp_context)
                if save:
                    before[key] = body
                elif before.get(key) != body:
                    result.setdefault(location(path), []).append((start, m.end, {
                        "module": path, "method": m.name, "count": 0, "category": Counter(), "severity": Counter()}))
    return result


def check_profile(repo, sha, project):
    relative = ".settings/Default.cset"
    expected = repo.show(sha, f"{PROJECT_DIR}/{relative}")
    profile = project / relative
    if expected is None and not profile.exists():
        return
    if expected is not None and profile.is_file():
        # git archive на Windows может заменить LF на CRLF.
        actual = json.loads(profile.read_text(encoding="utf-8-sig"))
        if actual == json.loads(expected):
            return
    raise ValueError(f"Профиль EDT {profile} отличается от коммита {sha}")


def snapshot(repo, sha, methods):
    workspace = ENV["EF_EDT_WORKSPACE"] + "-quality"
    cli = ENV["EF_EDT_CLI"]
    args = ENV["EF_EDT_VMARGS"]
    prepare_workspace(repo, cli, workspace, sha, PROJECT_DIR, 7200, args)
    # При полном импорте EDT заново создаёт служебный каталог workspace.
    prefs = Path(workspace, ".metadata/.plugins/org.eclipse.core.runtime/.settings/com.github.malikovpro.dt.bsl.ls_connector.prefs")
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text("eclipse.preferences.version=1\nLAUNCH_MODE=native\n", encoding="ascii")
    project = Path(workspace + "-src") / PROJECT_DIR
    check_profile(repo, sha, project)
    tsv = OUT / (sha + ".tsv")
    tsv.unlink(missing_ok=True)
    command = f'validate --file "{tsv}" --project-list "{project}"'
    if run_cli(cli, workspace, [command], str(OUT / "validate.edtsh"), 7200, args):
        raise RuntimeError("Ошибка validate")
    seen_plugin = False
    with tsv.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 8:
                continue
            seen_plugin |= "malikov-pro" in row[4]
            for start, end, method in methods.get(row[5], []):
                n = re.search(r"\d+", row[6])
                if n and start <= int(n[0]) <= end:
                    method["count"] += 1
                    method["severity"][row[1]] += 1
                    method["category"][row[2]] += 1
    if not seen_plugin:
        raise RuntimeError("Полный отчёт EDT не содержит диагностик коннектора")
    c = repo.commit(sha)
    return dict(schema=1, commit=sha, author=c.author, commit_date=c.iso_date,
                job=int(ENV["CI_JOB_ID"]), methods=[m for group in methods.values() for _, _, m in group])


def api(path, data=None):
    url = ENV["CI_API_V4_URL"] + "/projects/" + ENV["CI_PROJECT_ID"] + "/" + path
    headers = {"JOB-TOKEN": ENV["CI_JOB_TOKEN"]}
    req = Request(url, headers=headers, data=data, method="GET" if data is None else "PUT")
    with urlopen(req, timeout=60) as response:
        return response.read()


def snapshots():
    result = {}
    page = 1
    while True:
        packages = json.loads(api(f"packages?package_type=generic&package_name={PACKAGE}&per_page=100&page={page}"))
        for item in packages:
            if item["name"] == PACKAGE:
                sha = item["version"]
                result[sha] = json.loads(api(f"packages/generic/{PACKAGE}/{sha}/snapshot.json"))
        if len(packages) < 100:
            return result
        page += 1


def render(history):
    months = defaultdict(dict)
    for s in sorted(history.values(), key=lambda s: (s["commit_date"], s["commit"])):
        month = s["commit_date"][:7]
        for m in s["methods"]:
            module = m["module"]
            if "/" not in module:  # первый снимок EF содержит имя общего модуля
                module = f"{SRC}/CommonModules/{module}/Module.bsl"
            months[month][s["author"], module, m["method"]] = m, s
    lines = ["Статистика EDT по изменённым методам", "",
             "За месяц у каждого автора учитывается последнее состояние каждого затронутого метода.", ""]
    html = ['<!doctype html><html lang="ru"><meta charset="utf-8"><title>Статистика EDT</title>',
            '<style>body{font:16px system-ui;margin:2rem}table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:.4em;text-align:left}</style>',
            '<h1>Статистика EDT</h1>', '<p>' + lines[2] + '</p>']
    for month in sorted(months, reverse=True):
        lines.append(month)
        html.append(f'<h2>{escape(month)}</h2>')
        rows = months[month]
        for author in sorted({k[0] for k in rows}, key=lambda a: -sum(m["count"] for k,(m,s) in rows.items() if k[0]==a)):
            items = [(mod, name, m, s) for (a,mod,name),(m,s) in rows.items() if a==author and m["count"]]
            lines.append(f'{author}: методов с замечаниями {len(items)}, замечаний {sum(m["count"] for _,_,m,_ in items)}')
            html.append('<h3>' + escape(lines[-1]) + '</h3>')
            html.append('<table><tr><th>Модуль<th>Метод<th>Замечаний<th>Категории<th>Коммит</tr>')
            for mod, name, m, s in sorted(items, key=lambda r: -r[2]["count"]):
                cats = ", ".join(f"{k}: {v}" for k,v in m["category"].items())
                lines.append(f'  {mod} • {name}: {m["count"]} ({cats}) • {s["commit"][:10]}')
                cells = (mod, name, str(m["count"]), cats, s["commit"][:10])
                html.append('<tr>' + ''.join('<td>' + escape(value) + '</td>' for value in cells) + '</tr>')
            html.append('</table>')
        lines.append("")
    (OUT / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "report.html").write_text("\n".join(html + ['</html>']), encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    history = snapshots()
    repo = Repo(ENV["EF_MIRROR"])
    head, base = ENV["CI_COMMIT_SHA"], ENV["CI_COMMIT_BEFORE_SHA"]
    if ENV.get("CI_PIPELINE_SOURCE") == "schedule":
        # В расписании BEFORE_SHA нулевой. Перебираем диапазон с начала учёта,
        # опубликованные снимки пропускаются, включая снимки прерванного запуска.
        base = repo.rev_parse(ENV["EDT_STATS_BASE"])
        if not repo.is_ancestor(base, head):
            raise ValueError("EDT_STATS_BASE вне истории проверяемой ветки")
    elif not base.strip("0"):
        base = head + "^"
    commits = subprocess.check_output(["git", "-C", repo.path, "rev-list", "--first-parent", "--reverse",
                                       base + ".." + head], text=True).splitlines()
    for sha in commits:
        if sha in history:
            continue
        methods = touched(repo, sha)
        if methods:
            s = snapshot(repo, sha, methods)
            api(f"packages/generic/{PACKAGE}/{sha}/snapshot.json", json.dumps(s, ensure_ascii=False).encode())
            history[sha] = s
    render(history)


if __name__ == "__main__":
    main()
