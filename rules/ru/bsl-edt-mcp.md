# BSL через EDT-MCP

При работе с `.bsl` прочитай [INDEX.md](INDEX.md), затем по ситуации:

- [bsl-coding-style.md](bsl-coding-style.md) — стиль и соглашения кода
- [bsl-do-not-use.md](bsl-do-not-use.md) — обход коллекций: цикл; не `[]` с числом; `Получить(0)` при затруднённом цикле
- [bsl-tab-alignment.md](bsl-tab-alignment.md) — выравнивание Tab в группах присваиваний
- [bsl-static-typing.md](bsl-static-typing.md) — `@strict-types` и документирующие комментарии
- [bsl-ssl.md](bsl-ssl.md) — паттерны БСП (проект на БСП)
- [edt-mcp-write-safety.md](edt-mcp-write-safety.md) — перед любой записью через `write_module_source`
- [preserve-eol.md](preserve-eol.md) — не менять CRLF/LF; после MCP-записи восстановить EOL на диске

**NEVER:** читать/править `.bsl` через Read/Write/StrReplace — только MCP EDT (`read_method_source`, `read_module_source`, `write_module_source`).

**После `write_module_source`:** если исходный `.bsl` на диске был CRLF — сразу верни CRLF (MCP пишет LF). См. [preserve-eol.md](preserve-eol.md).
