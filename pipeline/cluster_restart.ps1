# Перезапуск кластера 1С рабочей базы с очисткой сеансовых данных (каталоги snccntx*).
#
# По шагам:
#   1. блокировка начала сеансов и регламентных заданий базы через RAS (rac infobase update), с текущего момента;
#   2. остановка служб агента на узлах из cluster.json, в их порядке; перед агентом - других служб 1С узла
#      (extraServices, например release-ibsrv - автономный сервер сборки исправлений, он тоже держит файлы платформы);
#   3. ожидание завершения ragent, rmngr и rphost; не завершились за -StopTimeoutSec - принудительно (kill -9 / Stop-Process);
#   4. удаление каталогов <srvinfo>/reg_*/snccntx*;
#   5. запуск служб в том же порядке, другие службы 1С - после агента;
#   6. полное применение требований назначения функциональности (rac rule apply --full).
# Блокировка остается: конфигурация базы данных обновляется с кодом разрешения (/UC), затем блокировка снимается.
# -ThenUnlock - перезапуск без обновления базы: блокировка снимается сразу после запуска служб и применения ТНФ.
#
# Узел раннера обслуживается локально, остальные - по SSH (учетка и ключ - sshUser и sshKey в настройках узлов), на Linux через sudo -n.
# -Check ничего не меняет: проверяет доступ по SSH, sudo, службы, процессы, каталоги snccntx и базу в RAS.
# -Unlock только снимает блокировку сеансов и регламентных заданий базы, службы не трогает.
#
#   powershell -File cluster_restart.ps1 -Config .reper/cluster.json -Platform "C:\Program Files\1cv8\8.3.27.2074\bin" -Check
param(
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Platform,
    # Рабочая база <сервер>\<база>: из нее берется имя базы в кластере.
    [string]$Infobase = $env:EF_PROD_IB,
    [string]$Ras = $(if ($env:EF_RAS) { $env:EF_RAS } else { 'localhost:1545' }),
    [string]$User = $env:EF_IB_USER,
    [string]$Password = $env:EF_IB_PASSWORD,
    # Администратор кластера, если он задан.
    [string]$ClusterUser = $env:EF_CLUSTER_USER,
    [string]$ClusterPassword = $env:EF_CLUSTER_PASSWORD,
    [string]$Message = $(if ($env:EF_LOCK_MESSAGE) { $env:EF_LOCK_MESSAGE } else { 'Ведутся технические работы.' }),
    [string]$PermissionCode = $env:EF_LOCK_CODE,
    [int]$StopTimeoutSec = $(if ($env:EF_STOP_TIMEOUT) { [int]$env:EF_STOP_TIMEOUT } else { 600 }),
    [string]$KnownHosts = '',
    [switch]$Check,
    [switch]$Unlock,
    [switch]$ThenUnlock
)

$ErrorActionPreference = 'Stop'
$processNames = @('ragent', 'rmngr', 'rphost')
# Invoke-Native, Invoke-Rac, Find-Infobase, Write-Step и другие общие функции.
. (Join-Path $PSScriptRoot 'rac.ps1')

$inventory = Get-Content -Raw -Encoding UTF8 -Path $Config | ConvertFrom-Json
if (-not $KnownHosts) { $KnownHosts = Join-Path (Split-Path (Resolve-Path $Config)) 'known_hosts' }
# -n: ssh не читает стандартный ввод - в задании CI это скрипт задания, который раннер передает PowerShell.
$sshOptions = @('-n', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR')
if (Test-Path $KnownHosts) { $sshOptions += @('-o', "UserKnownHostsFile=$((Resolve-Path $KnownHosts).Path)") }
if ($inventory.sshKey -and (Test-Path $inventory.sshKey)) { $sshOptions += @('-i', $inventory.sshKey, '-o', 'IdentitiesOnly=yes') }

function Test-Local($node) { ($node.name -eq $env:COMPUTERNAME) -or ($node.address -eq $env:COMPUTERNAME) }

# Команда на Linux-узле; -AllowFail - вернуть результат и при ненулевом коде.
function Invoke-Remote($node, [string]$Command, [switch]$AllowFail) {
    $result = Invoke-Native 'ssh' ($sshOptions + @("$($inventory.sshUser)@$($node.address)", $Command))
    if (-not $AllowFail -and $result.ExitCode -ne 0) {
        throw "$($node.name): '$Command' - код $($result.ExitCode)`n$($result.Output -join "`n")"
    }
    return $result
}

function Get-Unit([string]$Service) { "'" + $Service + "'" }

# Другие службы 1С узла: останавливаются перед агентом, запускаются после него.
function Get-ExtraServices($node) { @($node.extraServices | Where-Object { $_ }) }

# Каталоги snccntx в reg_* каталога данных кластера.
function Get-SessionDirs($node) {
    if (Test-Local $node) {
        $root = $node.srvinfo.Replace('/', '\')
        return @(Get-ChildItem -Path $root -Directory -Filter 'reg_*' -ErrorAction SilentlyContinue |
            Get-ChildItem -Directory -Filter 'snccntx*' | ForEach-Object { $_.FullName })
    }
    $result = Invoke-Remote $node "sudo -n find '$($node.srvinfo)' -mindepth 2 -maxdepth 2 -type d -path '$($node.srvinfo)/reg_*/snccntx*'"
    return @($result.Output | Where-Object { $_ })
}

function Get-ClusterProcesses($node) {
    if (Test-Local $node) {
        return @(Get-Process -Name $processNames -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Id) $($_.ProcessName)" })
    }
    $result = Invoke-Remote $node "pgrep -l -x '$($processNames -join '|')'" -AllowFail
    return @($result.Output | Where-Object { $_ })
}

function Get-ServiceState($node, [string]$Service = $node.service) {
    if (Test-Local $node) { return [string](Get-Service -Name $Service).Status }
    return ((Invoke-Remote $node "systemctl is-active $(Get-Unit $Service)" -AllowFail).Output -join ' ').Trim()
}

function Stop-NodeService($node, [string]$Service) {
    if (Test-Local $node) { Stop-Service -Name $Service -Force } else { Invoke-Remote $node "sudo -n systemctl stop $(Get-Unit $Service)" | Out-Null }
}

function Start-NodeService($node, [string]$Service) {
    if (Test-Local $node) { Start-Service -Name $Service } else { Invoke-Remote $node "sudo -n systemctl start $(Get-Unit $Service)" | Out-Null }
}

function Stop-ClusterProcesses($node) {
    if (Test-Local $node) { Get-Process -Name $processNames -ErrorAction SilentlyContinue | Stop-Process -Force }
    else { Invoke-Remote $node "sudo -n pkill -9 -x '$($processNames -join '|')'" -AllowFail | Out-Null }
}

function Remove-SessionDirs($node) {
    if (Test-Local $node) {
        Get-SessionDirs $node | ForEach-Object { Remove-Item -LiteralPath $_ -Recurse -Force }
    }
    else {
        Invoke-Remote $node "sudo -n find '$($node.srvinfo)' -mindepth 2 -maxdepth 2 -type d -path '$($node.srvinfo)/reg_*/snccntx*' -exec rm -rf {} +" | Out-Null
    }
}

if (-not $Infobase) { throw 'Не задана рабочая база: переменная EF_PROD_IB (<сервер>\<база>)' }
$nodes = @($inventory.hosts)
$problems = New-Object System.Collections.Generic.List[string]

function Remove-Lock($target) {
    Invoke-Rac (@('infobase', 'update', "--cluster=$($target.Cluster)", "--infobase=$($target.Infobase)",
        "--infobase-user=$User", "--infobase-pwd=$Password", '--sessions-deny=off', '--scheduled-jobs-deny=off') + (Get-ClusterAuth)) | Out-Null
    $info = Get-InfobaseInfo $target
    if ($info.'sessions-deny' -ne 'off' -or $info.'scheduled-jobs-deny' -ne 'off') {
        throw "Блокировка не снялась: сеансы $($info.'sessions-deny'), регламентные задания $($info.'scheduled-jobs-deny')"
    }
    Write-Step "Блокировка сеансов и регламентных заданий базы $($target.Name) снята"
}

if ($Unlock) {
    Remove-Lock (Find-Infobase)
    exit 0
}

# Проверка: в режиме -Check - весь результат, перед перезапуском - чтобы не остановить кластер наполовину.
$target = $null
try {
    $target = Find-Infobase
    $info = Get-InfobaseInfo $target
    Write-Step "База $($target.Name) в кластере $($target.Cluster) ($($target.Host)): блокировка сеансов $($info.'sessions-deny'), с $($info.'denied-from'), регламентные задания $($info.'scheduled-jobs-deny')"
}
catch { $problems.Add("RAS: $($_.Exception.Message)") }

# Рабочие серверы кластера и их требования назначения функциональности - только в режиме проверки.
if ($Check -and $target) {
    try {
        foreach ($server in Invoke-Rac (@('server', 'list', "--cluster=$($target.Cluster)") + (Get-ClusterAuth))) {
            Write-Step ("Рабочий сервер {0} ({1}:{2}), {3}, менеджер под каждый сервис: {4}" -f $server.name, $server.'agent-host',
                $server.'agent-port', $server.using, $server.'dedicate-managers')
            $rules = @(Invoke-Rac (@('rule', 'list', "--cluster=$($target.Cluster)", "--server=$($server.server)") + (Get-ClusterAuth)))
            if (-not $rules) { Write-Output '    требований нет' }
            foreach ($rule in $rules) {
                Write-Output ("    {0}: {1}, база '{2}', приложение '{3}', приоритет {4}" -f $rule.'object-type', $rule.'rule-type',
                    $rule.'infobase-name', $rule.'application-ext', $rule.priority)
            }
        }
    }
    catch { $problems.Add("ТНФ: $($_.Exception.Message)") }
}
if (-not $Check -and -not $PermissionCode) { $problems.Add('Не задан код разрешения: переменная EF_LOCK_CODE') }

foreach ($node in $nodes) {
    try {
        if (Test-Local $node) {
            $mode = 'локально'
            $null = Get-Service -Name (@($node.service) + @(Get-ExtraServices $node))
            if (-not (Test-Path $node.srvinfo.Replace('/', '\'))) { throw "нет каталога $($node.srvinfo)" }
        }
        else {
            if ($node.os -ne 'linux') { throw "удаленный узел $($node.os) не поддерживается, только linux" }
            $mode = "ssh $($inventory.sshUser)@$($node.address)"
            # Список разрешенных команд, а не sudo true: в sudoers могут быть разрешены только systemctl, pkill и find.
            $sudo = Invoke-Remote $node 'sudo -n -l' -AllowFail
            if ($sudo.ExitCode -ne 0) { throw "нет sudo без пароля: $($sudo.Output -join ' ')" }
            if ($Check) { $sudo.Output | Where-Object { $_ -match '^\s+\(' } | ForEach-Object { Write-Output "    sudo: $($_.Trim())" } }
            $units = (Invoke-Remote $node "systemctl list-unit-files --no-legend 'srv1cv8*' 'ras*'" -AllowFail).Output -join '; '
            foreach ($service in @($node.service) + @(Get-ExtraServices $node)) {
                if ((Invoke-Remote $node "systemctl cat $(Get-Unit $service)" -AllowFail).ExitCode -ne 0) {
                    throw "нет службы $service; службы 1С на узле: $units"
                }
            }
        }
        $state = Get-ServiceState $node
        # Остановленная другая служба - не проблема: перезапуск ее запустит.
        $extra = (Get-ExtraServices $node | ForEach-Object { ", служба $_ - $(Get-ServiceState $node $_)" }) -join ''
        $processes = Get-ClusterProcesses $node
        $dirs = Get-SessionDirs $node
        Write-Step "$($node.name) ($mode): служба $($node.service) - $state$extra, процессов 1С $($processes.Count), каталогов snccntx $($dirs.Count)"
        if ($Check) { $dirs | ForEach-Object { Write-Output "    $_" } }
    }
    catch { $problems.Add("$($node.name): $($_.Exception.Message)") }
}

if ($problems.Count) { throw ("Проверка не пройдена:`n" + (($problems | ForEach-Object { " - $_" }) -join "`n")) }
if ($Check) { Write-Step 'Проверка пройдена, ничего не изменено'; exit 0 }

# 1. Блокировка.
$from = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
Invoke-Rac (@('infobase', 'update', "--cluster=$($target.Cluster)", "--infobase=$($target.Infobase)",
    "--infobase-user=$User", "--infobase-pwd=$Password", '--sessions-deny=on', "--denied-from=$from",
    "--denied-message=$Message", "--permission-code=$PermissionCode", '--scheduled-jobs-deny=on') + (Get-ClusterAuth)) | Out-Null
$info = Get-InfobaseInfo $target
if ($info.'sessions-deny' -ne 'on' -or $info.'scheduled-jobs-deny' -ne 'on') { throw "Блокировка не установилась: сеансы $($info.'sessions-deny'), регламентные задания $($info.'scheduled-jobs-deny')" }
Write-Step "Блокировка сеансов и регламентных заданий базы $($target.Name) с $from"

# 2. Остановка служб: на узле сначала другие службы 1С, потом агент.
foreach ($node in $nodes) {
    foreach ($service in @(Get-ExtraServices $node) + @($node.service)) {
        Stop-NodeService $node $service
        Write-Step "$($node.name): служба $service остановлена ($(Get-ServiceState $node $service))"
    }
}

# 3. Ожидание процессов, затем принудительное завершение.
$deadline = (Get-Date).AddSeconds($StopTimeoutSec)
do {
    $left = @($nodes | Where-Object { (Get-ClusterProcesses $_).Count })
    if (-not $left) { break }
    Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)
foreach ($node in $left) {
    Write-Step "$($node.name): процессы не завершились за $StopTimeoutSec с, завершаются принудительно: $((Get-ClusterProcesses $node) -join ', ')"
    Stop-ClusterProcesses $node
}
Start-Sleep -Seconds 5
foreach ($node in $nodes) {
    $rest = Get-ClusterProcesses $node
    if ($rest.Count) { throw "$($node.name): процессы 1С не завершились: $($rest -join ', '). Службы остановлены, блокировка стоит" }
}
Write-Step 'Процессы 1С на всех узлах завершены'

# 4. Сеансовые данные.
foreach ($node in $nodes) {
    $count = (Get-SessionDirs $node).Count
    Remove-SessionDirs $node
    $rest = (Get-SessionDirs $node).Count
    if ($rest) { throw "$($node.name): не удалены каталоги snccntx ($rest). Службы остановлены, блокировка стоит" }
    Write-Step "$($node.name): удалено каталогов snccntx: $count"
}

# 5. Запуск служб: ошибка на одном узле не мешает запустить остальные. Другие службы 1С - после агента; если они
# не запустились, задание не падает: от него зависит обновление рабочей базы, а не сборка исправлений.
$extraProblems = New-Object System.Collections.Generic.List[string]
foreach ($node in $nodes) {
    try {
        Start-NodeService $node $node.service
        Write-Step "$($node.name): служба $($node.service) запущена ($(Get-ServiceState $node))"
    }
    catch { $problems.Add("$($node.name): $($_.Exception.Message)") }
    foreach ($service in Get-ExtraServices $node) {
        try {
            Start-NodeService $node $service
            Write-Step "$($node.name): служба $service запущена ($(Get-ServiceState $node $service))"
        }
        catch { $extraProblems.Add("$($node.name): служба ${service}: $($_.Exception.Message)") }
    }
}
if ($problems.Count) { throw ("Не запустились службы:`n" + (($problems | ForEach-Object { " - $_" }) -join "`n")) }

# 6. Требования назначения функциональности - полностью. Применяются еще раз в начале Обновления: сразу после
# запуска служб применения оказалось мало, лицензии сервер не выдавал до повторного применения.
Invoke-RuleApply $target

if ($ThenUnlock) {
    Remove-Lock $target
    Write-Step "Кластер перезапущен, база $($target.Name) открыта"
}
else {
    Write-Step "Кластер перезапущен. Блокировка базы $($target.Name) остается: обновить конфигурацию базы данных с кодом разрешения, затем снять блокировку"
}
foreach ($problem in $extraProblems) { Write-Step "ВНИМАНИЕ: не запустилась, запустить вручную - $problem" }
