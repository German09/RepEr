# Свои индексы PostgreSQL рабочей базы (созданные не платформой: sfp_, spf_, POS5000_, ix_ и т.п.) при обновлении.
# Реструктуризация таблицы в /UpdateDBCfg их теряет, поэтому:
#
#   Snapshot - до Обновления: снимок своих индексов (sql/custom_indexes.sql) в build/indexes/indexes_before.csv.
#   Restore  - после Обновления: по снимку недействительные остатки удаляются, отсутствующие индексы создаются
#              CREATE INDEX CONCURRENTLY по одному; отчет - build/indexes/indexes_after.csv (статус, команда, ошибка).
#
# Подключение - ODBC-драйвером PostgreSQL на раннере (тем же, через который работают внешние источники данных 1С),
# в режиме автокоммита: CREATE/DROP INDEX CONCURRENTLY в транзакции не работает. Параметры - переменные проекта
# PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD; PGUSER - владелец таблиц базы или суперпользователь.
# Не созданный индекс не блокирует обновление: Restore перечисляет такие индексы и завершается кодом 1, задание
# с allow_failure помечается предупреждением.
#
#   powershell -File indexes.ps1 -Mode Snapshot
param(
    [Parameter(Mandatory = $true)][ValidateSet('Snapshot', 'Restore')][string]$Mode,
    [string]$Driver = $(if ($env:EF_PG_ODBC_DRIVER) { $env:EF_PG_ODBC_DRIVER } else { 'PostgreSQL Unicode(x64)' }),
    [string]$WorkDir = 'build/indexes'
)

$ErrorActionPreference = 'Stop'

foreach ($name in 'PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "Не задано подключение к базе: переменная $name" }
}
New-Item -ItemType Directory -Force $WorkDir | Out-Null
$before = Join-Path $WorkDir 'indexes_before.csv'
$after = Join-Path $WorkDir 'indexes_after.csv'

function Open-Database {
    $builder = New-Object System.Data.Odbc.OdbcConnectionStringBuilder
    $builder.Driver = $Driver
    $builder['Server'] = $env:PGHOST
    $builder['Port'] = $(if ($env:PGPORT) { $env:PGPORT } else { '5432' })
    $builder['Database'] = $env:PGDATABASE
    $builder['Uid'] = $env:PGUSER
    $builder['Pwd'] = $env:PGPASSWORD
    $connection = New-Object System.Data.Odbc.OdbcConnection $builder.ConnectionString
    $connection.Open()
    return $connection
}

function Get-Rows($Connection, [string]$Sql) {
    $command = $Connection.CreateCommand()
    $command.CommandText = $Sql
    $command.CommandTimeout = 0
    $table = New-Object System.Data.DataTable
    [void](New-Object System.Data.Odbc.OdbcDataAdapter $command).Fill($table)
    return , $table
}

function Invoke-Sql($Connection, [string]$Sql) {
    $command = $Connection.CreateCommand()
    $command.CommandText = $Sql
    $command.CommandTimeout = 0
    [void]$command.ExecuteNonQuery()
}

# Индексы схемы public: имя -> действителен ли.
function Get-IndexState($Connection) {
    $state = @{}
    $rows = Get-Rows $Connection @"
SELECT c.relname AS index_name, x.indisvalid::int AS valid
FROM pg_index x
JOIN pg_class c ON c.oid = x.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
"@
    # boolean psqlODBC отдает строкой '0'/'1', а [bool]'0' - истина: флаг - числом.
    foreach ($row in $rows) { $state[[string]$row.index_name] = ([int]$row.valid -eq 1) }
    return $state
}

$connection = Open-Database
try {
    if ($Mode -eq 'Snapshot') {
        $rows = Get-Rows $connection (Get-Content (Join-Path $PSScriptRoot 'sql\custom_indexes.sql') -Raw -Encoding UTF8)
        $taken = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $rows | Select-Object @{ n = 'taken_at'; e = { $taken } }, table_name, index_name, definition |
            Export-Csv $before -NoTypeInformation -Encoding UTF8
        Write-Output "Своих индексов в снимке: $($rows.Rows.Count)"
        $rows | ForEach-Object { Write-Output "  $($_.index_name) ($($_.table_name))" }
        exit 0
    }

    if (-not (Test-Path $before)) { throw 'Нет снимка indexes_before.csv: задание «Индексы до» не выполнилось' }
    $snapshot = @(Import-Csv $before -Encoding UTF8)
    $state = Get-IndexState $connection
    $errors = @{}
    foreach ($index in $snapshot) {
        $name = $index.index_name
        $quoted = '"' + $name.Replace('"', '""') + '"'
        try {
            # Недействительный остаток (неудачный CONCURRENTLY) - удалить, иначе IF NOT EXISTS его пропустит.
            if ($state.ContainsKey($name) -and -not $state[$name]) {
                Write-Output "Удаляется недействительный $name"
                Invoke-Sql $connection "DROP INDEX CONCURRENTLY IF EXISTS public.$quoted"
                $state.Remove($name)
            }
            if (-not $state.ContainsKey($name)) {
                Write-Output "Создается $name ($($index.table_name))"
                $watch = [Diagnostics.Stopwatch]::StartNew()
                Invoke-Sql $connection ($index.definition -replace '^CREATE (UNIQUE )?INDEX ', 'CREATE $1INDEX CONCURRENTLY IF NOT EXISTS ')
                Write-Output ("  создан за {0:N0} с" -f $watch.Elapsed.TotalSeconds)
            }
        }
        catch {
            $message = $_.Exception.InnerException.Message
            if (-not $message) { $message = $_.Exception.Message }
            $errors[$name] = ($message -split '\r?\n' | Where-Object { $_ }) -join ' '
            Write-Output "  ошибка: $($errors[$name])"
        }
    }

    $state = Get-IndexState $connection
    $report = foreach ($index in $snapshot) {
        $name = $index.index_name
        [pscustomobject]@{
            index_name = $name
            table_name = $index.table_name
            status     = $(if (-not $state.ContainsKey($name)) { 'не создан' } elseif (-not $state[$name]) { 'недействителен' } else { 'ок' })
            create_sql = $index.definition -replace '^CREATE (UNIQUE )?INDEX ', 'CREATE $1INDEX CONCURRENTLY IF NOT EXISTS '
            error      = $errors[$name]
        }
    }
    @($report) | Export-Csv $after -NoTypeInformation -Encoding UTF8

    $failed = @($report | Where-Object { $_.status -ne 'ок' })
    if ($failed.Count -gt 0) {
        Write-Output "Не восстановлены индексы: $($failed.Count) из $($snapshot.Count) (команды создания и ошибки - в indexes_after.csv)"
        $failed | ForEach-Object { Write-Output "  $($_.index_name) ($($_.table_name)): $($_.status) $($_.error)" }
        exit 1
    }
    Write-Output "Все индексы снимка на месте: $($snapshot.Count)"
    exit 0
}
finally {
    $connection.Close()
}
