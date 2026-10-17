# Обновляет рабочую базу после загрузки .cf и перезапуска кластера, под блокировкой сеансов (код разрешения - /UC).
#
#   0. Полное применение требований назначения функциональности (rac rule apply --full).
#   1. Конфигуратор: /UpdateDBCfg - конфигурация базы данных по основной конфигурации.
#   1а. Удаление исправлений без манифеста (собранных вручную): БСП их не удаляет никогда. Перед удалением каждое
#       выгружается в <SaveDir>\<имя>.cfe - каталог релиза, который был в базе до обновления: при откате на него
#       исправления ставятся обратно. -SaveDir не задан - в артефакт задания <LogDir>\patches.
#   2. Предприятие: /C"ЗапуститьОбновлениеИнформационнойБазы" - обновление версии БСП (обработчики обновления;
#      исправления, не применимые к новой версии, удаляются), как в скрипте обновления БСП.
#   3. Ожидание толстых клиентов пользователя с этого компьютера и его фоновых заданий (rac session list): после
#      удаления устаревших исправлений БСП перезапускает клиент новым процессом, и обновление выполняет уже он,
#      а обработчики обновления идут фоновыми заданиями. Сам клиент в клиент-серверной базе не завершается: после
#      обновления он открывает рабочий стол. Когда фоновых заданий нет, а клиент -IdleSec не выполняет вызовов
#      сервера, обновление закончено: сеансы клиентов завершаются, процессы 1cv8 этого задания снимаются.
#      Не дождались за -TimeoutSec - то же, и задание падает.
# Блокировка не снимается: это отдельный шаг после проверки. Регламентные задания заблокированы, поэтому
# отложенные обработчики обновления пойдут после снятия блокировки.
#
#   powershell -File update_db.ps1 -Platform "C:\Program Files\1cv8\8.3.27.2074\bin" -Server "server\base"
param(
    [Parameter(Mandatory = $true)][string]$Platform,
    # Рабочая база в кластере: <сервер>\<база>.
    [string]$Server = $env:EF_PROD_IB,
    # Пользователь рабочей базы с правом администрирования.
    [string]$User = $env:EF_IB_USER,
    [string]$Password = $env:EF_IB_PASSWORD,
    # Код разрешения блокировки сеансов.
    [string]$PermissionCode = $env:EF_LOCK_CODE,
    # RAS и администратор кластера - для ожидания фоновых заданий.
    [string]$Ras = $(if ($env:EF_RAS) { $env:EF_RAS } else { 'localhost:1545' }),
    [string]$ClusterUser = $env:EF_CLUSTER_USER,
    [string]$ClusterPassword = $env:EF_CLUSTER_PASSWORD,
    [string]$LogDir = 'build/release',
    # Каталог предыдущего релиза (<EF_CF_DIR>\<тег>) для удаляемых исправлений без манифеста.
    [string]$SaveDir = '',
    [int]$TimeoutSec = $(if ($env:EF_UPDATE_TIMEOUT) { [int]$env:EF_UPDATE_TIMEOUT } else { 3600 }),
    # Сколько секунд клиент без вызовов сервера и без фоновых заданий, чтобы считать обновление законченным.
    [int]$IdleSec = $(if ($env:EF_UPDATE_IDLE) { [int]$env:EF_UPDATE_IDLE } else { 120 })
)

$ErrorActionPreference = 'Stop'
# Find-Infobase, Get-InfobaseSessions, Write-Step и другие общие функции; база для них - $Infobase.
$Infobase = $Server
. (Join-Path $PSScriptRoot 'rac.ps1')

if (-not $Server) { throw 'Не задана рабочая база: переменная EF_PROD_IB (<сервер>\<база>)' }
if (-not $PermissionCode) { throw 'Не задан код разрешения: переменная EF_LOCK_CODE' }
$client = Join-Path $Platform '1cv8.exe'
if (-not (Test-Path $client)) { throw "Нет клиента 1С ${client}" }
$LogDir = Join-Path (Get-Location) $LogDir
New-Item -ItemType Directory -Force $LogDir | Out-Null

# Запуск 1С с ограничением времени; журнал /Out выводится в журнал задания. Командная строка не выводится: в ней пароль.
function Invoke-1C([string]$Mode, [string[]]$Arguments, [string]$Log) {
    Remove-Item $Log -Force -ErrorAction SilentlyContinue
    $all = @($Mode, '/S', $Server)
    if ($User) { $all += @('/N', $User) }
    if ($Password) { $all += @('/P', $Password) }
    $all += @("/UC$PermissionCode", '/DisableStartupDialogs', '/DisableStartupMessages', '/AppAutoCheckVersion-', '/Out', $Log) + $Arguments
    $line = ($all | ForEach-Object { if ($_ -match '\s' -or $_ -eq '') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $client -ArgumentList $line -NoNewWindow -PassThru
    $null = $process.Handle
    if (-not $process.WaitForExit($TimeoutSec * 1000)) {
        $process.Kill()
        throw "$Mode не завершился за $TimeoutSec с"
    }
    if (Test-Path $Log) { Get-Content $Log }
    if ($process.ExitCode -ne 0) { throw "$Mode завершился с кодом $($process.ExitCode)" }
    Write-Step ("{0} завершен за {1:N0} с" -f $Mode, $watch.Elapsed.TotalSeconds)
}

# Требования назначения функциональности - полностью, перед Конфигуратором: после перезапуска кластера без этого
# сервер не выдавал лицензии ("На сервере 1С:Предприятия не найдена лицензия").
$target = Find-Infobase
Invoke-RuleApply $target

Write-Step "Обновление конфигурации базы данных $Server"
# -Dynamic-: без динамического обновления - база заблокирована, обновление полное.
Invoke-1C 'DESIGNER' @('/UpdateDBCfg', '-Dynamic-') (Join-Path $LogDir 'update_db.log')

# Исправления без манифеста (общего макета с именем расширения) БСП не удаляет никогда: считает их еще не примененными.
# Это исправления, собранные вручную; к новому релизу они устарели. Признаки исправления - как в
# ОбновлениеКонфигурации.ЭтоИсправление: назначение "Исправление" и имя на EF. Исправления с манифестом (из ленты,
# официальные исправления 1С) удаляет или оставляет сама БСП по применимости к версии. Удаляются до запуска
# Предприятия, чтобы устаревшие перехватчики не работали в обработчиках обновления.
$dump = Join-Path $LogDir 'extensions'
if (Test-Path $dump) { Remove-Item $dump -Recurse -Force }
Write-Step "Выгрузка расширений $Server"
Invoke-1C 'DESIGNER' @('/DumpConfigToFiles', $dump, '-AllExtensions') (Join-Path $LogDir 'extensions.log')
$manual = @()
foreach ($file in Get-ChildItem $dump -Recurse -Depth 1 -Filter 'Configuration.xml') {
    [xml]$xml = Get-Content -Raw -Encoding UTF8 $file.FullName
    $properties = $xml.SelectSingleNode("//*[local-name()='Configuration']/*[local-name()='Properties']")
    $name = $properties.SelectSingleNode("*[local-name()='Name']").InnerText
    $purpose = $properties.SelectSingleNode("*[local-name()='ConfigurationExtensionPurpose']").InnerText
    $templates = @($xml.SelectNodes("//*[local-name()='ChildObjects']/*[local-name()='CommonTemplate']") | ForEach-Object { $_.InnerText })
    $manifest = $templates -contains $name
    Write-Output ("    {0}: {1}, манифест {2}" -f $name, $purpose, $(if ($manifest) { 'есть' } else { 'нет' }))
    if ($purpose -eq 'Patch' -and $name.StartsWith('EF') -and -not $manifest) { $manual += $name }
}
Remove-Item $dump -Recurse -Force
# Исправления без манифеста нужны релизу, который был в базе до обновления: при откате на него их ставят обратно.
# Не выгрузилось - обновление останавливается до удаления.
if ($manual) {
    if (-not $SaveDir) {
        $SaveDir = Join-Path $LogDir 'patches'
        Write-Step "ВНИМАНИЕ: предыдущий релиз неизвестен (EF_PREVIOUS_RELEASE, RELEASE_PREVIOUS): исправления сохраняются только в артефакт задания $SaveDir"
    }
    New-Item -ItemType Directory -Force $SaveDir | Out-Null
}
foreach ($name in $manual) {
    $cfe = Join-Path $SaveDir "$name.cfe"
    Remove-Item $cfe -Force -ErrorAction SilentlyContinue
    Invoke-1C 'DESIGNER' @('/DumpCfg', $cfe, '-Extension', $name) (Join-Path $LogDir "save_$name.log")
    if (-not (Test-Path $cfe)) { throw "Исправление $name не выгружено в ${cfe}: оно и следующие не удалены, обновление версии не выполнено" }
    Write-Step "Исправление $name сохранено: $cfe"
    Invoke-1C 'DESIGNER' @('/DeleteCfg', '-Extension', $name) (Join-Path $LogDir "delete_$name.log")
    Write-Step "Удалено исправление без манифеста: $name"
}
if (-not $manual) { Write-Step 'Исправлений без манифеста нет' }

# Сеансы обновления: толстые клиенты $User с этого компьютера (запущенный здесь и перезапущенные БСП) и фоновые
# задания $User. Под блокировкой других таких сеансов нет.
function Get-UpdateSessions {
    @(Get-InfobaseSessions $target | Where-Object {
        $_.'user-name' -eq $User -and
        ($_.'app-id' -eq 'BackgroundJob' -or ($_.'app-id' -eq '1CV8' -and ($_.host -split '\.')[0] -ieq $env:COMPUTERNAME))
    })
}

# Завершает сеансы клиентов через RAS и снимает процессы 1cv8, запущенные в этом сеансе Windows после $Since:
# клиент без пользователя не закроет сообщение о завершении сеанса и остался бы висеть.
function Stop-UpdateClients([datetime]$Since) {
    foreach ($session in @(Get-UpdateSessions | Where-Object { $_.'app-id' -eq '1CV8' })) {
        try {
            Invoke-Rac (@('session', 'terminate', "--cluster=$($target.Cluster)", "--session=$($session.session)") + (Get-ClusterAuth)) | Out-Null
            Write-Step "Сеанс клиента $($session.'session-id') на $($session.host) завершен"
        }
        catch { Write-Step "Сеанс клиента $($session.'session-id') не завершен: $($_.Exception.Message)" }
    }
    $windowsSession = (Get-Process -Id $PID).SessionId
    foreach ($process in @(Get-Process -Name '1cv8' -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $windowsSession -and $_.StartTime -ge $Since })) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Write-Step "Процесс 1cv8 $($process.Id) снят"
    }
}

Write-Step "Обновление версии в режиме Предприятия $Server"
$enterpriseStarted = Get-Date
try {
    Invoke-1C 'ENTERPRISE' @('/C', 'ЗапуститьОбновлениеИнформационнойБазы') (Join-Path $LogDir 'enterprise.log')
}
catch {
    Stop-UpdateClients $enterpriseStarted
    throw
}

# Процесс, которого ждал Invoke-1C, мог не довести обновление до конца: после удаления устаревших исправлений БСП
# перезапускает клиент новым процессом, и обновляет базу уже он; обработчики обновления идут фоновыми заданиями.
# Сам клиент не завершается: после обновления он открывает рабочий стол. Пока идет обновление, клиент выполняет
# вызов сервера (duration-current) или ждет фоновое задание; простой -IdleSec без того и другого - обновление
# закончено, клиенты снимаются. Если за минуту ни один сеанс обновления не появился - обновление уже закончилось.
$started = Get-Date
$seen = @{}
$idleSince = $null
$stopped = $null
while ($true) {
    $sessions = Get-UpdateSessions
    foreach ($session in $sessions) {
        if (-not $seen.ContainsKey($session.'session-id')) {
            $seen[$session.'session-id'] = $true
            $kind = if ($session.'app-id' -eq 'BackgroundJob') { 'Фоновое задание' } else { 'Клиент' }
            Write-Step "$kind ${User}: сеанс $($session.'session-id') на $($session.host), начат $($session.'started-at')"
        }
    }
    $elapsed = ((Get-Date) - $started).TotalSeconds
    if (-not $sessions -and ($seen.Count -or $elapsed -ge 60)) { break }
    $clients = @($sessions | Where-Object { $_.'app-id' -eq '1CV8' })
    $busy = @($sessions | Where-Object { $_.'app-id' -eq 'BackgroundJob' -or [long]$_.'duration-current' -gt 0 })
    if ($busy -or -not $clients) { $idleSince = $null } elseif (-not $idleSince) { $idleSince = Get-Date }
    if ($stopped) {
        if (((Get-Date) - $stopped).TotalSeconds -ge 60) {
            throw "Клиенты $User не завершились: сеансы $(($clients | ForEach-Object { $_.'session-id' }) -join ', ')"
        }
    }
    elseif ($idleSince -and ((Get-Date) - $idleSince).TotalSeconds -ge $IdleSec) {
        Write-Step "Фоновых заданий нет, клиенты без вызовов сервера $IdleSec с: обновление закончено, клиенты снимаются"
        Stop-UpdateClients $enterpriseStarted
        $stopped = Get-Date
    }
    elseif ($elapsed -gt $TimeoutSec) {
        $list = ($sessions | ForEach-Object { "$($_.'session-id') ($($_.'app-id'))" }) -join ', '
        Stop-UpdateClients $enterpriseStarted
        throw "Сеансы обновления $User не завершились за $TimeoutSec с: $list"
    }
    Start-Sleep -Seconds 15
}
Write-Step ("Сеансов обновления {0} (клиенты и фоновые задания): {1}, все завершены; ожидание {2:N0} с" -f $User, $seen.Count, ((Get-Date) - $started).TotalSeconds)

Write-Step 'База обновлена. Блокировка сеансов и регламентных заданий остается: проверить и снять отдельным шагом'
