# -*- coding: utf-8 -*-
"""Виды объектов метаданных, модули которых генератор умеет заимствовать в расширение."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Kind:
    folder: str                       # каталог в исходниках (одинаков в EDT и в XML-выгрузке)
    cls: str                          # имя класса: тег XML и mdclass:<cls>
    collection: str                   # имя коллекции в Configuration.mdo
    # (элемент EDT, префикс имени типа в XML, категория в XML)
    produced: tuple[tuple[str, str, str], ...] = ()
    # имя файла модуля -> элемент расширения в .mdo
    modules: dict[str, str] = field(default_factory=dict)
    # контролируемые свойства: (элемент EDT, тег XML, значение по умолчанию)
    controlled: tuple[tuple[str, str, str], ...] = ()


def _ref_types(prefix: str) -> tuple[tuple[str, str, str], ...]:
    return (
        ("objectType", f"{prefix}Object", "Object"),
        ("refType", f"{prefix}Ref", "Ref"),
        ("selectionType", f"{prefix}Selection", "Selection"),
        ("listType", f"{prefix}List", "List"),
        ("managerType", f"{prefix}Manager", "Manager"),
    )


_OBJECT_MODULES = {"ObjectModule.bsl": "objectModule", "ManagerModule.bsl": "managerModule"}
_REGISTER_MODULES = {"RecordSetModule.bsl": "recordSetModule", "ManagerModule.bsl": "managerModule"}

KINDS: dict[str, Kind] = {k.folder: k for k in (
    Kind("CommonModules", "CommonModule", "commonModules",
         modules={"Module.bsl": "module"},
         controlled=(
             ("global", "Global", "false"),
             ("clientManagedApplication", "ClientManagedApplication", "false"),
             ("server", "Server", "false"),
             ("externalConnection", "ExternalConnection", "false"),
             ("clientOrdinaryApplication", "ClientOrdinaryApplication", "false"),
             ("serverCall", "ServerCall", "false"),
         )),
    Kind("Catalogs", "Catalog", "catalogs", _ref_types("Catalog"), _OBJECT_MODULES),
    Kind("Documents", "Document", "documents", _ref_types("Document"), _OBJECT_MODULES),
    Kind("DataProcessors", "DataProcessor", "dataProcessors",
         (("objectType", "DataProcessorObject", "Object"), ("managerType", "DataProcessorManager", "Manager")),
         _OBJECT_MODULES),
    Kind("Reports", "Report", "reports",
         (("objectType", "ReportObject", "Object"), ("managerType", "ReportManager", "Manager")),
         _OBJECT_MODULES),
    Kind("InformationRegisters", "InformationRegister", "informationRegisters",
         (("selectionType", "InformationRegisterSelection", "Selection"),
          ("listType", "InformationRegisterList", "List"),
          ("managerType", "InformationRegisterManager", "Manager"),
          ("recordSetType", "InformationRegisterRecordSet", "RecordSet"),
          ("recordKeyType", "InformationRegisterRecordKey", "RecordKey"),
          ("recordType", "InformationRegisterRecord", "Record"),
          ("recordManagerType", "InformationRegisterRecordManager", "RecordManager")),
         _REGISTER_MODULES,
         (("informationRegisterPeriodicity", "InformationRegisterPeriodicity", "Nonperiodical"),
          ("writeMode", "WriteMode", "Independent"))),
    Kind("AccumulationRegisters", "AccumulationRegister", "accumulationRegisters",
         (("selectionType", "AccumulationRegisterSelection", "Selection"),
          ("listType", "AccumulationRegisterList", "List"),
          ("managerType", "AccumulationRegisterManager", "Manager"),
          ("recordSetType", "AccumulationRegisterRecordSet", "RecordSet"),
          ("recordKeyType", "AccumulationRegisterRecordKey", "RecordKey"),
          ("recordType", "AccumulationRegisterRecord", "Record")),
         _REGISTER_MODULES,
         (("registerType", "RegisterType", "Balance"),)),
)}

# Порядок коллекций в Configuration.mdo и ChildObjects.
COLLECTION_ORDER = ["CommonModules", "Catalogs", "Documents", "Reports", "DataProcessors",
                    "InformationRegisters", "AccumulationRegisters"]


@dataclass(frozen=True)
class ModuleRef:
    kind: Kind
    object_name: str
    module_file: str      # Module.bsl, ObjectModule.bsl ...
    path: str             # путь относительно корня исходников

    @property
    def mdo_path(self) -> str:
        return f"{self.kind.folder}/{self.object_name}/{self.object_name}.mdo"

    @property
    def title(self) -> str:
        if self.kind.cls == "CommonModule":
            return f"CommonModule.{self.object_name}"
        return f"{self.kind.cls}.{self.object_name}.{self.module_file[:-4]}"


RE_FORM_MODULE = re.compile(r"(^|/)(Forms/[^/]+|CommonForms/[^/]+)/Module\.bsl$")


def classify(rel_path: str) -> tuple[ModuleRef | None, str]:
    """Определяет, можно ли перенести изменение файла в расширение.

    Возвращает (ModuleRef, "") для поддерживаемого модуля либо (None, причина).
    """
    if not rel_path.endswith(".bsl"):
        return None, "не модуль: метаданные, формы, права и макеты генератор не переносит"
    if RE_FORM_MODULE.search(rel_path):
        return None, "модуль формы: заимствование формы пока не автоматизировано"
    parts = rel_path.split("/")
    if len(parts) == 3 and parts[0] in KINDS and parts[2] in KINDS[parts[0]].modules:
        return ModuleRef(KINDS[parts[0]], parts[1], parts[2], rel_path), ""
    return None, "модуль этого вида объекта пока не поддержан генератором"
