# -*- coding: utf-8 -*-
"""Текст расширяющих модулей и манифест исправления."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from xml.sax.saxutils import escape

from .bsl import Method, rename_in_header
from .planner import ModulePlan, Plan

NAMESPACE = uuid.UUID("6f1c3b52-5d0a-4b5e-9a57-e5f0c0de5000")


@dataclass
class Settings:
    name: str                 # имя расширения, должно начинаться с EF_
    prefix: str               # префикс имен методов-перехватчиков и свойство NamePrefix
    synonym: str
    comment: str
    version: str              # версия расширения
    compatibility: str        # режим совместимости расширения, например 8.3.27
    xml_format: str           # версия формата XML-выгрузки, например 2.20
    applied_name: str         # имя библиотеки БСП, для которой применимо исправление
    applied_versions: str     # версии библиотеки через запятую
    language: str = "Русский"
    language_code: str = "ru"
    manifest: bool = True

    def uid(self, *parts: str) -> str:
        """Устойчивый идентификатор: повторная сборка того же исправления дает те же UUID."""
        return str(uuid.uuid5(NAMESPACE, "/".join((self.name, self.version) + parts)))


def _interceptor(method: Method, settings: Settings, commits: list[str]) -> list[str]:
    lines = [f"// {method.name}: изменен в {', '.join(commits)}"]
    lines += method.annotations
    lines.append(f'&Вместо("{method.name}")')
    lines.append(rename_in_header(method.header, settings.prefix + method.name) + method.header_tail)
    lines += method.body
    lines.append(method.end_line)
    return lines


def _addition(method: Method, commits: list[str]) -> list[str]:
    lines = [f"// {method.name}: добавлен в {', '.join(commits)}"]
    lines += method.doc
    lines += method.annotations
    lines.append(method.header + (" Экспорт" if method.export else "") + method.header_tail)
    lines += method.body
    lines.append(method.end_line)
    return lines


def module_text(module_plan: ModulePlan, plan: Plan, settings: Settings) -> str:
    if module_plan.is_new:
        return module_plan.source
    out = [
        f"// {settings.name}: {plan.head.subject}",
        f"// Сформировано автоматически из коммитов: {', '.join(c.short for c in plan.commits)}",
        f"// Релиз, к которому применяется исправление: {plan.base_ref}",
        "",
    ]
    blocks = [(m, _interceptor(m, settings, module_plan.touched_by.get(m.key, []))) for m in module_plan.overrides]
    blocks += [(m, _addition(m, module_plan.touched_by.get(m.key, []))) for m in module_plan.additions]
    blocks.sort(key=lambda item: item[0].start)

    context: tuple[str, ...] = ()
    for method, lines in blocks:
        # Закрываем и открываем условия препроцессора так же, как они стоят в исходном модуле.
        common = 0
        while common < min(len(context), len(method.pp_context)) and context[common] == method.pp_context[common]:
            common += 1
        for _ in context[common:]:
            out += ["#КонецЕсли", ""]
        for condition in method.pp_context[common:]:
            out += [f"#Если {condition} Тогда", ""]
        context = method.pp_context
        out += lines
        out.append("")
    for _ in context:
        out += ["#КонецЕсли", ""]
    return "\n".join(out)


def modified_metadata(plan: Plan) -> str:
    return ", ".join(m.ref.title for m in plan.modules)


def manifest_xml(plan: Plan, settings: Settings) -> str:
    """Описание исправления в формате XDTO-пакета http://www.v8.1c.ru/ssl/patch.

    БСП читает его из общего макета, имя которого совпадает с именем исправления,
    и из файла Manifest.xml в архиве исправления.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Patch xmlns="http://www.v8.1c.ru/ssl/patch" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"\t<Name>{escape(settings.name)}</Name>\n"
        f"\t<Description>{escape(settings.comment)}</Description>\n"
        f"\t<UUID>{settings.uid('patch')}</UUID>\n"
        f"\t<ModifiedMetadata>{escape(modified_metadata(plan))}</ModifiedMetadata>\n"
        "\t<AppliedFor>\n"
        f"\t\t<ConfigurationName>{escape(settings.applied_name)}</ConfigurationName>\n"
        f"\t\t<Versions>{escape(settings.applied_versions)}</Versions>\n"
        "\t</AppliedFor>\n"
        "</Patch>\n"
    )
