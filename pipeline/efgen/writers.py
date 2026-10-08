# -*- coding: utf-8 -*-
"""Запись исходников расширения: XML-выгрузка конфигуратора и проект EDT."""
from __future__ import annotations

import os
import re
from xml.sax.saxutils import escape

from .kinds import COLLECTION_ORDER, KINDS, Kind
from .planner import ModulePlan, Plan
from .render import Settings, manifest_xml, module_text

BOM = "﻿"

# Идентификаторы классов служебных объектов конфигурации, одинаковы для всех конфигураций.
CONTAINED_CLASSES = [
    "9cd510cd-abfc-11d4-9434-004095e12fc7",
    "9fcd25a0-4822-11d4-9414-008048da11f9",
    "e3687481-0a87-462c-a166-9f34594f9bba",
    "9de14907-ec23-4a07-96f0-85521cb6b53b",
    "51f2d5d8-ea4d-4064-8892-82951750031e",
    "e68182ea-4237-4383-967f-90c1e3370bc7",
    "fb282519-d103-4dd3-bc12-cb271d631dfc",
]

XML_ROOT = (
    '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
    'xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
    'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
    'xmlns:cmi="http://v8.1c.ru/8.2/managed-application/cmi" '
    'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
    'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
    'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
    'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" '
    'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
    'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
    'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
    'xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" '
    'xmlns:xpr="http://v8.1c.ru/8.3/xcf/predef" '
    'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="{version}">'
)

MDO_NS = (
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" '
    'xmlns:mdclassExtension="http://g5.1c.ru/v8/dt/metadata/mdclass/extension"'
)


def _write(root: str, rel: str, text: str, bom: bool = False) -> None:
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write((BOM if bom else "") + text.replace("\r\n", "\n").replace("\n", "\r\n"))


def _mdo_value(mdo: str | None, element: str, default: str) -> str:
    """Значение свойства верхнего уровня из .mdo базового объекта."""
    if mdo:
        found = re.search(rf"^  <{element}>([^<]*)</{element}>", mdo, re.MULTILINE)
        if found:
            return found.group(1)
    return default


class Objects:
    """Объекты расширения, сгруппированные по видам: модули одного объекта объединяются."""

    def __init__(self, plan: Plan, mdo_reader):
        self.items: dict[tuple[str, str], dict] = {}
        for module_plan in plan.modules:
            ref = module_plan.ref
            item = self.items.setdefault((ref.kind.folder, ref.object_name), {
                "kind": ref.kind, "name": ref.object_name, "modules": [], "is_new": module_plan.is_new,
                "mdo": mdo_reader(ref.mdo_path, module_plan.is_new),
            })
            item["modules"].append(module_plan)

    def ordered(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda it: (COLLECTION_ORDER.index(it["kind"].folder), it["name"]))


# ---------------------------------------------------------------------------------------------
# XML-выгрузка конфигуратора (загружается через /LoadConfigFromFiles -Extension или ibcmd).

def write_xml(root: str, plan: Plan, settings: Settings, objects: Objects) -> None:
    head = BOM + '<?xml version="1.0" encoding="UTF-8"?>\n' + XML_ROOT.format(version=settings.xml_format) + "\n"
    tail = "</MetaDataObject>"

    children = [f"\t\t\t<Language>{settings.language}</Language>"]
    if settings.manifest:
        children.append(f"\t\t\t<CommonTemplate>{settings.name}</CommonTemplate>")
    for item in objects.ordered():
        children.append(f"\t\t\t<{item['kind'].cls}>{item['name']}</{item['kind'].cls}>")

    contained = "".join(
        "\t\t\t<xr:ContainedObject>\n"
        f"\t\t\t\t<xr:ClassId>{class_id}</xr:ClassId>\n"
        f"\t\t\t\t<xr:ObjectId>{settings.uid('contained', class_id)}</xr:ObjectId>\n"
        "\t\t\t</xr:ContainedObject>\n" for class_id in CONTAINED_CLASSES)
    _write(root, "Configuration.xml", head + (
        f'\t<Configuration uuid="{settings.uid("configuration")}">\n'
        "\t\t<InternalInfo>\n" + contained + "\t\t</InternalInfo>\n"
        "\t\t<Properties>\n"
        "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n"
        f"\t\t\t<Name>{settings.name}</Name>\n"
        "\t\t\t<Synonym>\n\t\t\t\t<v8:item>\n"
        f"\t\t\t\t\t<v8:lang>{settings.language_code}</v8:lang>\n"
        f"\t\t\t\t\t<v8:content>{escape(settings.synonym)}</v8:content>\n"
        "\t\t\t\t</v8:item>\n\t\t\t</Synonym>\n"
        f"\t\t\t<Comment>{escape(settings.comment)}</Comment>\n"
        "\t\t\t<ConfigurationExtensionPurpose>Patch</ConfigurationExtensionPurpose>\n"
        "\t\t\t<KeepMappingToExtendedConfigurationObjectsByIDs>false</KeepMappingToExtendedConfigurationObjectsByIDs>\n"
        f"\t\t\t<NamePrefix>{settings.prefix}</NamePrefix>\n"
        f"\t\t\t<ConfigurationExtensionCompatibilityMode>Version{settings.compatibility.replace('.', '_')}"
        "</ConfigurationExtensionCompatibilityMode>\n"
        "\t\t\t<ScriptVariant>Russian</ScriptVariant>\n"
        "\t\t\t<Vendor/>\n"
        f"\t\t\t<Version>{settings.version}</Version>\n"
        f"\t\t\t<DefaultLanguage>Language.{settings.language}</DefaultLanguage>\n"
        "\t\t\t<BriefInformation/>\n\t\t\t<DetailedInformation/>\n\t\t\t<Copyright/>\n"
        "\t\t\t<VendorInformationAddress/>\n\t\t\t<ConfigurationInformationAddress/>\n"
        "\t\t</Properties>\n"
        "\t\t<ChildObjects>\n" + "\n".join(children) + "\n\t\t</ChildObjects>\n"
        "\t</Configuration>\n") + tail, )

    _write(root, f"Languages/{settings.language}.xml", head + (
        f'\t<Language uuid="{settings.uid("language")}">\n'
        "\t\t<InternalInfo/>\n\t\t<Properties>\n"
        "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n"
        f"\t\t\t<Name>{settings.language}</Name>\n\t\t\t<Comment/>\n"
        f"\t\t\t<LanguageCode>{settings.language_code}</LanguageCode>\n"
        "\t\t</Properties>\n\t</Language>\n") + tail)

    if settings.manifest:
        _write(root, f"CommonTemplates/{settings.name}.xml", head + (
            f'\t<CommonTemplate uuid="{settings.uid("manifest")}">\n\t\t<Properties>\n'
            f"\t\t\t<Name>{settings.name}</Name>\n\t\t\t<Synonym/>\n\t\t\t<Comment/>\n"
            "\t\t\t<TemplateType>TextDocument</TemplateType>\n"
            "\t\t</Properties>\n\t</CommonTemplate>\n") + tail)
        _write(root, f"CommonTemplates/{settings.name}/Ext/Template.txt", manifest_xml(plan, settings), bom=True)

    for item in objects.ordered():
        kind: Kind = item["kind"]
        name = item["name"]
        generated = "".join(
            f'\t\t\t<xr:GeneratedType name="{type_name}.{name}" category="{category}">\n'
            f"\t\t\t\t<xr:TypeId>{settings.uid(kind.cls, name, element, 'type')}</xr:TypeId>\n"
            f"\t\t\t\t<xr:ValueId>{settings.uid(kind.cls, name, element, 'value')}</xr:ValueId>\n"
            "\t\t\t</xr:GeneratedType>\n" for element, type_name, category in kind.produced)
        props = "" if item["is_new"] else "\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\n"
        props += f"\t\t\t<Name>{name}</Name>\n"
        if item["is_new"]:
            synonym = re.search(r"<synonym>\s*<key>([^<]*)</key>\s*<value>([^<]*)</value>", item["mdo"] or "")
            props += ("\t\t\t<Synonym>\n\t\t\t\t<v8:item>\n"
                      f"\t\t\t\t\t<v8:lang>{synonym.group(1)}</v8:lang>\n"
                      f"\t\t\t\t\t<v8:content>{synonym.group(2)}</v8:content>\n"
                      "\t\t\t\t</v8:item>\n\t\t\t</Synonym>\n") if synonym else "\t\t\t<Synonym/>\n"
        props += "\t\t\t<Comment/>\n"
        for element, tag, default in kind.controlled:
            props += f"\t\t\t<{tag}>{_mdo_value(item['mdo'], element, default)}</{tag}>\n"
        if item["is_new"]:
            props += f"\t\t\t<Privileged>{_mdo_value(item['mdo'], 'privileged', 'false')}</Privileged>\n"
            props += ("\t\t\t<ReturnValuesReuse>"
                      f"{_mdo_value(item['mdo'], 'returnValuesReuse', 'DontUse')}</ReturnValuesReuse>\n")
        child = "\t\t<ChildObjects/>\n" if kind.produced else ""
        _write(root, f"{kind.folder}/{name}.xml", head + (
            f'\t<{kind.cls} uuid="{settings.uid(kind.cls, name)}">\n'
            "\t\t<InternalInfo>\n" + generated + "\t\t</InternalInfo>\n"
            "\t\t<Properties>\n" + props + "\t\t</Properties>\n" + child +
            f"\t</{kind.cls}>\n") + tail)
        for module_plan in item["modules"]:
            _write(root, f"{kind.folder}/{name}/Ext/{module_plan.ref.module_file}",
                   module_text(module_plan, plan, settings), bom=True)


# ---------------------------------------------------------------------------------------------
# Проект EDT (можно открыть в EDT или проверить через 1cedtcli).

def write_edt(root: str, plan: Plan, settings: Settings, objects: Objects, base_project: str) -> None:
    project_name = f"{base_project}.{settings.name}"
    _write(root, ".project", (
        '<?xml version="1.0" encoding="UTF-8"?>\n<projectDescription>\n'
        f"\t<name>{project_name}</name>\n\t<comment></comment>\n\t<projects>\n\t</projects>\n"
        "\t<buildSpec>\n\t\t<buildCommand>\n\t\t\t<name>org.eclipse.xtext.ui.shared.xtextBuilder</name>\n"
        "\t\t\t<arguments>\n\t\t\t</arguments>\n\t\t</buildCommand>\n\t</buildSpec>\n"
        "\t<natures>\n\t\t<nature>org.eclipse.xtext.ui.shared.xtextNature</nature>\n"
        "\t\t<nature>com._1c.g5.v8.dt.core.V8ExtensionNature</nature>\n\t</natures>\n"
        "</projectDescription>\n"))
    runtime = ".".join(settings.compatibility.split(".")[:3])
    _write(root, "DT-INF/PROJECT.PMF",
           f"Manifest-Version: 1.0\nRuntime-Version: {runtime}\nBase-Project: {base_project}\n")
    _write(root, ".settings/org.eclipse.core.resources.prefs",
           "eclipse.preferences.version=1\nencoding/<project>=UTF-8\n")

    contained = "".join(
        f'  <containedObjects classId="{class_id}" objectId="{settings.uid("contained", class_id)}"/>\n'
        for class_id in CONTAINED_CLASSES)
    members = ""
    if settings.manifest:
        members += f"  <commonTemplates>CommonTemplate.{settings.name}</commonTemplates>\n"
    for item in objects.ordered():
        members += f"  <{item['kind'].collection}>{item['kind'].cls}.{item['name']}</{item['kind'].collection}>\n"
    _write(root, "src/Configuration/Configuration.mdo", (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<mdclass:Configuration {MDO_NS} uuid="{settings.uid("configuration")}">\n'
        f"  <name>{settings.name}</name>\n"
        f"  <synonym>\n    <key>{settings.language_code}</key>\n    <value>{escape(settings.synonym)}</value>\n  </synonym>\n"
        f"  <comment>{escape(settings.comment)}</comment>\n"
        "  <objectBelonging>Adopted</objectBelonging>\n"
        '  <extension xsi:type="mdclassExtension:ConfigurationExtension">\n'
        "    <defaultLanguage>Checked</defaultLanguage>\n"
        "  </extension>\n" + contained +
        f"  <namePrefix>{settings.prefix}</namePrefix>\n"
        f"  <configurationExtensionCompatibilityMode>{settings.compatibility}</configurationExtensionCompatibilityMode>\n"
        "  <scriptVariant>Russian</scriptVariant>\n"
        f"  <version>{settings.version}</version>\n"
        f"  <defaultLanguage>Language.{settings.language}</defaultLanguage>\n"
        f'  <languages uuid="{settings.uid("language")}">\n'
        f"    <name>{settings.language}</name>\n"
        "    <objectBelonging>Adopted</objectBelonging>\n"
        '    <extension xsi:type="mdclassExtension:LanguageExtension">\n'
        "      <languageCode>Checked</languageCode>\n    </extension>\n"
        f"    <languageCode>{settings.language_code}</languageCode>\n"
        "  </languages>\n" + members +
        "</mdclass:Configuration>\n"))

    if settings.manifest:
        _write(root, f"src/CommonTemplates/{settings.name}/{settings.name}.mdo", (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<mdclass:CommonTemplate xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" '
            f'uuid="{settings.uid("manifest")}">\n'
            f"  <name>{settings.name}</name>\n  <templateType>TextDocument</templateType>\n"
            "</mdclass:CommonTemplate>\n"))
        _write(root, f"src/CommonTemplates/{settings.name}/Template.txt", manifest_xml(plan, settings), bom=True)

    for item in objects.ordered():
        kind: Kind = item["kind"]
        name = item["name"]
        base = f"src/{kind.folder}/{name}"
        for module_plan in item["modules"]:
            _write(root, f"{base}/{module_plan.ref.module_file}", module_text(module_plan, plan, settings), bom=True)
        if item["is_new"]:
            # Собственный объект расширения: описание берется из целевого коммита как есть.
            _write(root, f"{base}/{name}.mdo", item["mdo"])
            continue
        produced = ""
        if kind.produced:
            produced = "  <producedTypes>\n" + "".join(
                f'    <{element} typeId="{settings.uid(kind.cls, name, element, "type")}" '
                f'valueTypeId="{settings.uid(kind.cls, name, element, "value")}"/>\n'
                for element, _, _ in kind.produced) + "  </producedTypes>\n"
        extension = "".join(
            f"    <{kind.modules[mp.ref.module_file]}>Extended</{kind.modules[mp.ref.module_file]}>\n"
            for mp in item["modules"])
        extension += "".join(f"    <{element}>Checked</{element}>\n" for element, _, _ in kind.controlled)
        values = "".join(
            f"  <{element}>{_mdo_value(item['mdo'], element, default)}</{element}>\n"
            for element, _, default in kind.controlled
            if _mdo_value(item["mdo"], element, default) != default)
        standard = "  <useStandardCommands>true</useStandardCommands>\n" if kind.produced else ""
        _write(root, f"{base}/{name}.mdo", (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mdclass:{kind.cls} {MDO_NS} uuid="{settings.uid(kind.cls, name)}">\n' + produced +
            f"  <name>{name}</name>\n  <objectBelonging>Adopted</objectBelonging>\n"
            f'  <extension xsi:type="mdclassExtension:{kind.cls}Extension">\n' + extension + "  </extension>\n" +
            values + standard + f"</mdclass:{kind.cls}>\n"))
