# -*- coding: utf-8 -*-
"""Разбор модуля BSL на методы.

Полноценный синтаксический анализ здесь не нужен: достаточно надежно найти границы
процедур и функций, их аннотации и условия препроцессора, в которые они вложены.
Строковые литералы и комментарии учитываются, чтобы ключевые слова внутри них
не принимались за код.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENT = r"[A-Za-zА-Яа-яЁё_][0-9A-Za-zА-Яа-яЁё_]*"

RE_HEADER = re.compile(
    r"^[ \t]*(?:(Асинх|Async)[ \t]+)?(Процедура|Функция|Procedure|Function)[ \t]+(" + IDENT + r")[ \t]*\(",
    re.IGNORECASE,
)
RE_END = re.compile(
    r"(?<![0-9A-Za-zА-Яа-яЁё_])(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)(?![0-9A-Za-zА-Яа-яЁё_])",
    re.IGNORECASE,
)
RE_EXPORT = re.compile(r"^[ \t]*(Экспорт|Export)(?![0-9A-Za-zА-Яа-яЁё_])", re.IGNORECASE)
RE_PP_IF = re.compile(r"^#\s*(Если|If)\s+(.*?)\s+(Тогда|Then)\s*$", re.IGNORECASE)
RE_PP_ELSIF = re.compile(r"^#\s*(ИначеЕсли|ElsIf)\s+(.*?)\s+(Тогда|Then)\s*$", re.IGNORECASE)
RE_PP_ELSE = re.compile(r"^#\s*(Иначе|Else)\s*$", re.IGNORECASE)
RE_PP_ENDIF = re.compile(r"^#\s*(КонецЕсли|EndIf)\s*$", re.IGNORECASE)
RE_PP_REGION = re.compile(r"^#\s*(Область|КонецОбласти|Region|EndRegion)\b", re.IGNORECASE)
RE_FUNCTION = re.compile(r"^(Функция|Function)$", re.IGNORECASE)


def scan_line(line: str, in_str: bool) -> tuple[str, str, bool]:
    """Разбирает одну строку исходника.

    Возвращает (blank, nocomment, in_str):
      blank     - строка той же длины, где содержимое строковых литералов и комментарий
                  заменены пробелами (позиции символов совпадают с исходной строкой);
      nocomment - строка без комментария, строковые литералы сохранены;
      in_str    - признак незакрытого строкового литерала (продолжится на следующей строке).
    """
    n = len(line)
    blank = []
    i = 0
    if in_str:
        stripped = line.lstrip()
        if stripped.startswith("|"):
            i = line.index("|") + 1
            blank.append(" " * i)
        elif stripped == "" or stripped.startswith("//"):
            # Пустая строка или комментарий между частями многострочного литерала.
            return " " * n, "", True
        else:
            in_str = False
    end_code = n
    while i < n:
        ch = line[i]
        if in_str:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    blank.append("  ")
                    i += 2
                    continue
                in_str = False
                blank.append('"')
            else:
                blank.append(" ")
            i += 1
            continue
        if ch == '"':
            in_str = True
            blank.append('"')
            i += 1
            continue
        if ch == "/" and i + 1 < n and line[i + 1] == "/":
            end_code = i
            blank.append(" " * (n - i))
            break
        blank.append(ch)
        i += 1
    return "".join(blank), line[:end_code], in_str


@dataclass
class Method:
    name: str
    is_function: bool
    is_async: bool
    export: bool
    annotations: list[str]        # строки вида &НаСервере перед заголовком
    doc: list[str]                # документирующий комментарий перед методом
    header: str                   # текст заголовка до закрывающей скобки включительно
    header_tail: str              # остаток строки заголовка после ")" и "Экспорт"
    body: list[str]               # строки тела, без заголовка и КонецПроцедуры
    end_line: str                 # строка с КонецПроцедуры / КонецФункции
    pp_context: tuple[str, ...]   # условия #Если, в которые вложен метод
    start: int                    # номер первой строки заголовка (с 1)
    end: int                      # номер строки окончания (с 1)
    norm: str = ""                # нормализованный текст для сравнения версий
    code: str = ""                # код без строк и комментариев - для поиска вызовов

    @property
    def key(self) -> str:
        return self.name.lower()


@dataclass
class Module:
    methods: list[Method] = field(default_factory=list)
    residue: str = ""             # нормализованный код вне методов (Перем, тело модуля, #Если)

    def by_key(self) -> dict[str, Method]:
        return {m.key: m for m in self.methods}


def _pp_condition(frames: list[dict]) -> tuple[str, ...]:
    result = []
    for frame in frames:
        parts = [f"НЕ ({c})" for c in frame["prev"]]
        if frame["cur"] is not None:
            parts.append(f"({frame['cur']})" if frame["prev"] else frame["cur"])
        result.append(" И ".join(parts))
    return tuple(result)


def parse_module(text: str) -> Module:
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.split("\n")
    lines = [l[:-1] if l.endswith("\r") else l for l in lines]

    blank: list[str] = []
    nocomment: list[str] = []
    in_str = False
    for line in lines:
        b, nc, in_str = scan_line(line, in_str)
        blank.append(b)
        nocomment.append(nc)

    module = Module()
    frames: list[dict] = []
    in_method = [False] * len(lines)
    i = 0
    total = len(lines)
    while i < total:
        code = blank[i].strip()
        if code.startswith("#"):
            m = RE_PP_IF.match(code)
            if m:
                frames.append({"prev": [], "cur": m.group(2)})
            elif RE_PP_ELSIF.match(code) and frames:
                frame = frames[-1]
                frame["prev"].append(frame["cur"])
                frame["cur"] = RE_PP_ELSIF.match(code).group(2)
            elif RE_PP_ELSE.match(code) and frames:
                frame = frames[-1]
                frame["prev"].append(frame["cur"])
                frame["cur"] = None
            elif RE_PP_ENDIF.match(code) and frames:
                frames.pop()
            i += 1
            continue

        head = RE_HEADER.match(blank[i])
        if not head:
            i += 1
            continue

        # Конец заголовка: первая ")" после "(" вне строк и комментариев.
        j = i
        pos = blank[j].find(")", head.end())
        while pos < 0 and j + 1 < total:
            j += 1
            pos = blank[j].find(")")
        if pos < 0:
            i += 1
            continue
        rest = blank[j][pos + 1:]
        export = RE_EXPORT.match(rest)
        tail_from = pos + 1 + (export.end() if export else 0)

        # Конец метода.
        e = j
        end_match = RE_END.search(blank[e], tail_from)
        while not end_match and e + 1 < total:
            e += 1
            end_match = RE_END.search(blank[e])
        if not end_match:
            break

        # Аннотации и документирующий комментарий перед заголовком.
        a = i
        while a > 0 and blank[a - 1].strip().startswith("&"):
            a -= 1
        d = a
        while d > 0 and lines[d - 1].strip().startswith("//"):
            d -= 1

        header_lines = lines[i:j] + [lines[j][:pos + 1]]
        if e == j:
            body = [lines[j][tail_from:end_match.start()]]
            header_tail = ""
            end_line = lines[j][end_match.start():]
        else:
            body = lines[j + 1:e]
            if blank[e][:end_match.start()].strip():
                body.append(lines[e][:end_match.start()])
                end_line = lines[e][end_match.start():]
            else:
                end_line = lines[e]
            header_tail = lines[j][tail_from:]

        norm_parts = [nocomment[k].strip() for k in range(a, e + 1)]
        method = Method(
            name=head.group(3),
            is_function=bool(RE_FUNCTION.match(head.group(2))),
            is_async=bool(head.group(1)),
            export=bool(export),
            annotations=[lines[k].strip() for k in range(a, i)],
            doc=lines[d:a],
            header="\n".join(header_lines),
            header_tail=header_tail,
            body=body,
            end_line=end_line,
            pp_context=_pp_condition(frames),
            start=i + 1,
            end=e + 1,
            norm="\n".join(p for p in norm_parts if p),
            code="\n".join(blank[k] for k in range(i, e + 1)),
        )
        module.methods.append(method)
        for k in range(a, e + 1):
            in_method[k] = True
        i = e + 1

    residue = []
    for k in range(total):
        if in_method[k]:
            continue
        part = nocomment[k].strip()
        if not part or RE_PP_REGION.match(part):
            continue
        residue.append(part)
    module.residue = "\n".join(residue)
    return module


def rename_in_header(header: str, new_name: str) -> str:
    """Заменяет имя метода в тексте заголовка."""
    m = RE_HEADER.match(header)
    return header[:m.start(3)] + new_name + header[m.end(3):]
