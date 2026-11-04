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

function Invoke-Rac([string[]]$Arguments) {
    $result = Invoke-Native (Join-Path $Platform 'rac.exe') (@($Ras) + $Arguments)
    if ($result.ExitCode -ne 0) { throw "rac $($Arguments[0..1] -join ' '): код $($result.ExitCode)`n$($result.Output -join "`n")" }
    return ConvertFrom-RacOutput $result.Output
}

# Число кавычек в строке нечетное: значение, начатое кавычкой, еще не закрыто (кавычки внутри удвоены).
function Test-OddQuotes([string]$Text) { (($Text.ToCharArray() | Where-Object { $_ -eq '"' }) | Measure-Object).Count % 2 -eq 1 }

# Вывод rac: блоки "ключ : значение", разделенные пустой строкой; ключи блока выровнены, двоеточие - в одной колонке.
# Значение в кавычках (сообщение блокировки, описание) может занимать несколько строк, в том числе пустых: пока
# кавычка не закрыта, строки - его продолжение, а не конец записи. Иначе хвост записи терялся: сообщение блокировки
# с переводом строки отрезало denied-to и permission-code. Новый ключ - строка "ключ :" с двоеточием в колонке блока.
function ConvertFrom-RacOutput([string[]]$Lines) {
    $items = New-Object System.Collections.Generic.List[object]
    $item = [ordered]@{}; $key = $null; $column = -1; $open = $false
    foreach ($line in @($Lines) + '') {
        if ($line -match '^([A-Za-z][\w-]*)\s*:(?:\s(.*))?$' -and ($column -lt 0 -or $line.IndexOf(':') -eq $column)) {
            $key = $Matches[1]
            $item[$key] = [string]$Matches[2]
            $column = $line.IndexOf(':')
            $open = $item[$key].TrimStart().StartsWith('"') -and (Test-OddQuotes $item[$key])
        }
        elseif ($open) {
            $item[$key] += "`n" + $line
            $open = Test-OddQuotes $item[$key]
        }
        elseif (-not $line.Trim()) {
            if ($item.Count) { $items.Add((Complete-RacItem $item)) }
            $item = [ordered]@{}; $key = $null; $column = -1
        }
        # Строка без ключа вне кавычек - продолжение значения без кавычек, а не конец записи.
        elseif ($key) { $item[$key] += "`n" + $line }
    }
    return $items.ToArray()
}

# Значения записи: без пробелов по краям, в кавычках - без внешних кавычек и с одинарными внутри.
function Complete-RacItem($item) {
    $result = [ordered]@{}
    foreach ($name in $item.Keys) {
        $value = $item[$name].Trim()
        if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2).Replace('""', '"')
        }
        $result[$name] = $value
    }
    return [pscustomobject]$result
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
