# -*- coding: utf-8 -*-
"""Реквизиты и табличные части, которые нужно заимствовать вместе с модулем объекта.

В модуле объекта реквизит доступен по имени, но в расширении - только если он заимствован:
иначе EDT и конфигуратор сообщают "Переменная не определена". Генератор заимствует то,
на что ссылается перенесенный код:

- реквизиты и табличные части объекта, имя которых встречается в коде как самостоятельный
  идентификатор (не после точки) или после ЭтотОбъект.;
- реквизиты заимствованной табличной части, имя которых встречается в коде после точки
  (Стр.Номенклатура, Товары[0].Количество).

Имя, совпавшее с параметром метода или переменной из Перем, реквизит перекрывает и не учитывается.
Лишнее заимствование безвредно (тип копируется из релиза), пропущенное - ошибка проверки,
поэтому сомнительные совпадения заимствуются.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .bsl import IDENT, Method

RE_IDENT = re.compile(IDENT)
RE_VAR = re.compile(r"(?<![0-9A-Za-zА-Яа-яЁё_.])(?:Перем|Var)\s+([^;]*);", re.IGNORECASE)
RE_VAL = re.compile(r"^(?:Знач|Val)\s+", re.IGNORECASE)
THIS_OBJECT = {"этотобъект", "thisobject"}

# Виды объектов, в модуле объекта которых реквизиты доступны по имени.
OBJECT_KINDS = {"Catalogs", "Documents", "DataProcessors", "Reports"}


@dataclass
class TypeSpec:
    types: list[str]                                   # CatalogRef.Пользователи, String ...
    qualifiers: list[tuple[str, list[tuple[str, str]]]]  # (stringQualifiers, [(length, 50)])


@dataclass
class Member:
    name: str
    type: TypeSpec | None = None            # тип реквизита из релиза
    attributes: list[Member] | None = None  # реквизиты табличной части; None - это реквизит

    @property
    def is_tabular(self) -> bool:
        return self.attributes is not None


def _type(element: ET.Element | None) -> TypeSpec | None:
    if element is None:
        return None
    spec = TypeSpec([], [])
    for child in element:
        if child.tag == "types":
            spec.types.append((child.text or "").strip())
        elif child.tag.endswith("Qualifiers"):
            spec.qualifiers.append((child.tag, [(q.tag, (q.text or "").strip()) for q in child]))
    return spec if spec.types else None


def _attributes(parent: ET.Element) -> list[Member]:
    return [Member(a.findtext("name"), _type(a.find("type"))) for a in parent.findall("attributes")]


def object_members(mdo: str | None) -> list[Member]:
    """Реквизиты и табличные части (со всеми их реквизитами) из .mdo объекта, в порядке описания."""
    if not mdo:
        return []
    root = ET.fromstring(mdo.encode("utf-8"))
    members = _attributes(root)
    members += [Member(ts.findtext("name"), attributes=_attributes(ts)) for ts in root.findall("tabularSections")]
    return members


def parameters(method: Method) -> set[str]:
    """Имена параметров метода в нижнем регистре."""
    header = method.header
    inner = header[header.find("(") + 1:header.rfind(")")]
    names = set()
    for part in inner.split(","):
        part = RE_VAL.sub("", part.strip())
        found = RE_IDENT.match(part)
        if found:
            names.add(found.group(0).lower())
    return names


def used_names(method: Method) -> tuple[set[str], set[str]]:
    """Имена в коде метода (нижний регистр): самостоятельные и после точки.

    Самостоятельные - без параметров метода и переменных Перем; имя после ЭтотОбъект. считается
    самостоятельным. Вызовы (имя перед "(") не учитываются: реквизит не вызывают.
    """
    code = method.code
    local = parameters(method)
    for declared in RE_VAR.findall(code):
        local |= {n.strip().lower() for n in declared.split(",") if n.strip()}
    plain, dotted = set(), set()
    prev_name, prev_end = "", 0
    for found in RE_IDENT.finditer(code):
        start, end = found.span()
        name = found.group(0).lower()
        if start > 0 and (code[start - 1].isalnum() or code[start - 1] == "_"):
            continue  # хвост числа: 1Е5
        is_call = code[end:end + 200].lstrip().startswith("(")
        between = code[prev_end:start].strip()
        if between.endswith("."):
            if not is_call:
                dotted.add(name)
                if between == "." and prev_name in THIS_OBJECT:
                    plain.add(name)
        elif not is_call and name not in local:
            plain.add(name)
        prev_name, prev_end = name, end
    return plain, dotted


def adopted_members(methods: list[Method], base_mdo: str | None,
                    head_mdo: str | None = None) -> tuple[list[Member], list[str]]:
    """Что заимствовать для перенесенных методов модуля объекта.

    Возвращает (заимствуемые реквизиты и табличные части в порядке релиза, имена реквизитов,
    которые есть в целевом коммите, но нет в релизе - их расширением не заимствовать).
    """
    plain, dotted = set(), set()
    for method in methods:
        p, d = used_names(method)
        plain |= p
        dotted |= d
    result = []
    base = object_members(base_mdo)
    for member in base:
        if member.name.lower() not in plain:
            continue
        if member.is_tabular:
            member = Member(member.name, attributes=[a for a in member.attributes if a.name.lower() in dotted])
        result.append(member)
    known = {m.name.lower() for m in base}
    missing = [m.name for m in object_members(head_mdo) if m.name.lower() in plain and m.name.lower() not in known]
    return result, missing
