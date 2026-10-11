# -*- coding: utf-8 -*-
"""Заимствование реквизитов и табличных частей, к которым обращается перенесенный модуль объекта.

Запуск: python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from efgen.adopt import adopted_members, used_names  # noqa: E402
from efgen.bsl import parse_module  # noqa: E402
from efgen.gitutil import Repo  # noqa: E402
from efgen.planner import Planner, select_commits  # noqa: E402
from efgen.render import Settings  # noqa: E402
from efgen.writers import Objects, write_edt, write_xml  # noqa: E402

TASK_RE = r"SYSTEM-(\d+)"
EF_RE = r"(?<![0-9A-Za-zА-Яа-яЁё_])EF(?![0-9A-Za-zА-Яа-яЁё_])"
SRC = "УТ/src"
MDO = "Documents/Заказ/Заказ.mdo"
MODULE = "Documents/Заказ/ObjectModule.bsl"


def attribute(name, type_xml, pad="  "):
    return (f'{pad}<attributes uuid="00000000-0000-0000-0000-000000000000">\n{pad}  <name>{name}</name>\n'
            f"{pad}  <type>\n{type_xml}{pad}  </type>\n{pad}</attributes>\n")


def document(extra=""):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mdclass:Document xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:core="http://g5.1c.ru/v8/dt/mcore" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" '
        'uuid="00000000-0000-0000-0000-000000000001">\n'
        "  <name>Заказ</name>\n"
        + attribute("Автор", "      <types>CatalogRef.ВнешниеПользователи</types>\n"
                             "      <types>CatalogRef.Пользователи</types>\n")
        + attribute("Менеджер", "      <types>CatalogRef.Пользователи</types>\n")
        + attribute("Согласован", "      <types>Boolean</types>\n")
        + attribute("Комментарий", "      <types>String</types>\n      <stringQualifiers/>\n")
        + attribute("Сумма", "      <types>Number</types>\n      <numberQualifiers>\n"
                             "        <precision>15</precision>\n        <scale>2</scale>\n"
                             "      </numberQualifiers>\n")
        + attribute("Статус", "      <types>EnumRef.Статусы</types>\n")
        + attribute("Отказ", "      <types>Boolean</types>\n")
        + attribute("Склад", "      <types>CatalogRef.Склады</types>\n")
        + extra
        + '  <tabularSections uuid="00000000-0000-0000-0000-000000000002">\n    <name>Товары</name>\n'
        + attribute("Количество", "        <types>Number</types>\n", "    ")
        + attribute("Цена", "        <types>Number</types>\n", "    ")
        + "  </tabularSections>\n"
        + '  <tabularSections uuid="00000000-0000-0000-0000-000000000003">\n    <name>Услуги</name>\n'
        + "  </tabularSections>\n"
        "</mdclass:Document>\n")


RELEASE = '''#Если Сервер Или ТолстыйКлиентОбычноеПриложение Или ВнешнееСоединение Тогда

Процедура ПередЗаписью(Отказ, РежимЗаписи, РежимПроведения)
\tЕсли Не Согласован Тогда
\t\tВозврат;
\tКонецЕсли;
КонецПроцедуры

#КонецЕсли
'''

METHOD = '''
Процедура ПередЗаписью(Отказ, Знач РежимЗаписи, РежимПроведения = Неопределено)
\tПерем Склад;
\tЕсли Не Согласован И Статус <> Перечисления.Статусы.Новый Тогда // Комментарий: Сумма
\t\tОтказ = Истина;
\tКонецЕсли;
\tСообщить("Менеджер " + ЭтотОбъект.Автор);
\tДля Каждого Стр Из Товары Цикл
\t\tСтр.Количество = Стр.Количество + 1;
\tКонецЦикла;
\tМенеджер = Пользователи.ТекущийПользователь();
\tСклад = Комментарий(1);
КонецПроцедуры
'''


class UsedNamesTest(unittest.TestCase):
    def setUp(self):
        self.method = parse_module(METHOD).methods[0]

    def test_plain_names_without_parameters_variables_calls_strings_comments(self):
        plain, _ = used_names(self.method)
        self.assertTrue({"согласован", "статус", "товары", "менеджер", "автор"} <= plain)
        # Параметры, Перем, вызовы, строки и комментарии реквизитами не считаются.
        self.assertFalse({"отказ", "склад", "комментарий", "сумма", "количество", "новый"} & plain)

    def test_names_after_dot(self):
        _, dotted = used_names(self.method)
        self.assertTrue({"количество", "автор", "статусы", "новый"} <= dotted)
        self.assertNotIn("текущийпользователь", dotted)

    def test_adopted_members(self):
        members, missing = adopted_members([self.method], document())
        self.assertEqual([m.name for m in members], ["Автор", "Менеджер", "Согласован", "Статус", "Товары"])
        tabular = members[-1]
        self.assertEqual([a.name for a in tabular.attributes], ["Количество"])
        self.assertEqual(members[0].type.types, ["CatalogRef.ВнешниеПользователи", "CatalogRef.Пользователи"])
        self.assertEqual(missing, [])

    def test_attribute_missing_in_release_is_reported(self):
        head = document(attribute("Новый", "      <types>Boolean</types>\n"))
        method = parse_module("Процедура П()\n\tНовый = Истина;\nКонецПроцедуры\n").methods[0]
        members, missing = adopted_members([method], document(), head)
        self.assertEqual(members, [])
        self.assertEqual(missing, ["Новый"])


class AdoptBuildTest(unittest.TestCase):
    """Сквозная сборка: EF-коммит меняет обработчик модуля документа."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "repo")
        os.makedirs(cls.path)

        def git(*args):
            subprocess.run(["git", "-C", cls.path, *args], check=True, capture_output=True)

        def write(rel, text):
            full = os.path.join(cls.path, *f"{SRC}/{rel}".split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        write(MDO, document())
        write(MODULE, RELEASE)
        git("add", "-A")
        git("commit", "-qm", "Релиз")
        git("tag", "1.0")
        body = METHOD.strip().split("\n")
        write(MODULE, RELEASE.replace(
            "Процедура ПередЗаписью(Отказ, РежимЗаписи, РежимПроведения)\n\tЕсли Не Согласован Тогда\n"
            "\t\tВозврат;\n\tКонецЕсли;\nКонецПроцедуры", "\n".join(body)))
        git("add", "-A")
        git("commit", "-qm", "SYSTEM-1 EF ПередЗаписью")

        repo = Repo(cls.path)
        task, commits = select_commits(repo, "1.0", "HEAD", TASK_RE, EF_RE)
        cls.plan = Planner(repo, SRC).build(task, "1.0", "HEAD", commits)
        settings = Settings(name="EF_1_1", prefix="P_1_1_", synonym="", comment="", version="1.0.1",
                            compatibility="8.3.27", xml_format="2.20", applied_name="", applied_versions="",
                            manifest=False)
        objects = Objects(cls.plan, lambda mdo, is_new: repo.show(cls.plan.base_sha, f"{SRC}/{mdo}"))
        out = os.path.join(cls.tmp.name, "out")
        write_xml(os.path.join(out, "xml"), cls.plan, settings, objects)
        write_edt(os.path.join(out, "edt"), cls.plan, settings, objects, "УТ")
        with open(os.path.join(out, "xml", "Documents", "Заказ.xml"), encoding="utf-8-sig") as f:
            cls.xml = f.read()
        with open(os.path.join(out, "edt", "src", "Documents", "Заказ", "Заказ.mdo"), encoding="utf-8") as f:
            cls.mdo = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_members_are_planned(self):
        module = self.plan.modules[0]
        self.assertEqual([m.name for m in module.overrides], ["ПередЗаписью"])
        self.assertEqual([m.name for m in module.adopted], ["Автор", "Менеджер", "Согласован", "Статус", "Товары"])

    def test_xml_keeps_full_type(self):
        self.assertIn("<Name>Автор</Name>", self.xml)
        self.assertIn("<v8:Type>cfg:CatalogRef.ВнешниеПользователи</v8:Type>", self.xml)
        self.assertIn("<v8:Type>cfg:CatalogRef.Пользователи</v8:Type>", self.xml)
        self.assertIn("<v8:Type>cfg:EnumRef.Статусы</v8:Type>", self.xml)
        self.assertIn("<v8:Type>xs:boolean</v8:Type>", self.xml)
        self.assertIn('<xr:GeneratedType name="DocumentTabularSectionRow.Заказ.Товары" category="TabularSectionRow">',
                      self.xml)
        self.assertIn("<Name>Количество</Name>", self.xml)
        for name in ("Сумма", "Комментарий", "Склад", "Отказ", "Цена", "Услуги"):
            self.assertNotIn(f"<Name>{name}</Name>", self.xml)

    def test_edt_keeps_only_primitive_types(self):
        self.assertIn("<name>Автор</name>", self.mdo)
        self.assertIn("<type>Boolean</type>", self.mdo)
        self.assertNotIn("CatalogRef", self.mdo)
        self.assertNotIn("EnumRef", self.mdo)
        self.assertIn("<typeExtension/>", self.mdo)
        self.assertIn("<name>Товары</name>", self.mdo)
        self.assertIn("<name>Количество</name>", self.mdo)
        self.assertLess(self.mdo.index("<useStandardCommands>"), self.mdo.index("<attributes"))
        self.assertLess(self.mdo.index("<attributes"), self.mdo.index("<tabularSections"))


if __name__ == "__main__":
    unittest.main()
