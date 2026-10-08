# Общие функции для cluster_restart.ps1 и update_db.ps1: внешние программы и администрирование кластера через RAS (rac).
# Подключается точкой (. rac.ps1) и использует переменные вызывающего скрипта: $Platform, $Ras, $Infobase (<сервер>\<база>),
# $User, $Password (пользователь базы), $ClusterUser, $ClusterPassword (администратор кластера, если задан).

# PowerShell 5.1 с ErrorActionPreference = Stop превращает первую строку stderr внешней программы в исключение,
# поэтому внешние вызовы - с Continue, а ошибка определяется по коду возврата. Ввод пустой: иначе программа
# получила бы стандартный ввод задания CI.
function Invoke-Native([string]$Executable, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = '' | & $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = @($output) }
    }
    finally { $ErrorActionPreference = $previous }
}

# rac: вывод - блоки "ключ : значение", разделенные пустой строкой.
function Invoke-Rac([string[]]$Arguments) {
    $result = Invoke-Native (Join-Path $Platform 'rac.exe') (@($Ras) + $Arguments)
    if ($result.ExitCode -ne 0) { throw "rac $($Arguments[0..1] -join ' '): код $($result.ExitCode)`n$($result.Output -join "`n")" }
    $items = @(); $item = [ordered]@{}
    foreach ($line in $result.Output + '') {
        if ($line -match '^\s*([\w-]+)\s*:\s*(.*)$') { $item[$Matches[1]] = $Matches[2].Trim('"', ' ') }
        elseif ($item.Count) { $items += [pscustomobject]$item; $item = [ordered]@{} }
    }
    return $items
}

function Get-ClusterAuth { if ($ClusterUser) { @("--cluster-user=$ClusterUser", "--cluster-pwd=$ClusterPassword") } else { @() } }

# Кластер и база по имени из $Infobase.
function Find-Infobase {
    $name = ($Infobase -split '\\')[-1]
    foreach ($cluster in Invoke-Rac @('cluster', 'list')) {
        $base = Invoke-Rac (@('infobase', 'summary', 'list', "--cluster=$($cluster.cluster)") + (Get-ClusterAuth)) |
            Where-Object { $_.name -eq $name } | Select-Object -First 1
        if ($base) { return [pscustomobject]@{ Cluster = $cluster.cluster; Infobase = $base.infobase; Name = $name; Host = $cluster.host } }
    }
    throw "В RAS $Ras нет базы $name"
}

function Get-InfobaseInfo($target) {
    Invoke-Rac (@('infobase', 'info', "--cluster=$($target.Cluster)", "--infobase=$($target.Infobase)",
        "--infobase-user=$User", "--infobase-pwd=$Password") + (Get-ClusterAuth)) | Select-Object -First 1
}

# Сеансы базы: session-id, user-name, app-id (BackgroundJob - фоновое задание), host, started-at.
function Get-InfobaseSessions($target) {
    @(Invoke-Rac (@('session', 'list', "--cluster=$($target.Cluster)", "--infobase=$($target.Infobase)") + (Get-ClusterAuth)))
}

# Полное применение требований назначения функциональности, как "Применить требования назначения функциональности
# (полное)" в консоли: без него после перезапуска служб сервисы кластера (например, лицензирования) могут остаться
# не на своих серверах, и сервер не выдает лицензии. Кластер после запуска служб отвечает не сразу - повтор до 3 минут.
function Invoke-RuleApply($target) {
    $deadline = (Get-Date).AddSeconds(180)
    while ($true) {
        try {
            Invoke-Rac (@('rule', 'apply', "--cluster=$($target.Cluster)", '--full') + (Get-ClusterAuth)) | Out-Null
            break
        }
        catch {
            if ((Get-Date) -gt $deadline) { throw "Требования назначения функциональности не применены: $($_.Exception.Message)" }
            Start-Sleep -Seconds 10
        }
    }
    Write-Step 'Требования назначения функциональности применены (полное)'
}

function Write-Step([string]$Text) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $Text) }
