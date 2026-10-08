# Ставит или обновляет пайплайн RepEr в репозитории конфигурации.
#
# Всегда заменяет (руками в проекте не править):
#   .reper/pipeline/           - скрипты из pipeline/ без тестов
#   .reper/reper.gitlab-ci.yml - задания из template/.reper/
#   .reper/VERSION             - версия RepEr, из которой поставлено
# Создает, только если их еще нет (настройки проекта):
#   .gitlab-ci.yml             - include заданий и значения переменных проекта
#   .reper/config.json, .reper/cluster.json, .reper/.gitattributes - примеры, заполнить под проект
#
# В репозитории проекта ничего не коммитит: изменения проверяются и коммитятся отдельно.
#
# Пример:
#   powershell -File install.ps1 -Target D:\git\system
param(
    [Parameter(Mandatory = $true)][string]$Target
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path (Join-Path $Target '.git'))) { throw "$Target - не корень репозитория git" }
$Target = (Resolve-Path $Target).Path
$reper = Join-Path $Target '.reper'
New-Item -ItemType Directory -Force $reper | Out-Null

$pipeline = Join-Path $reper 'pipeline'
if (Test-Path $pipeline) { Remove-Item -Recurse -Force $pipeline }
Copy-Item -Recurse (Join-Path $PSScriptRoot 'pipeline') $pipeline
Remove-Item -Recurse -Force (Join-Path $pipeline 'tests')
Get-ChildItem $pipeline -Recurse -Force -Include '__pycache__', '*.pyc' | Remove-Item -Recurse -Force
Copy-Item -Force (Join-Path $PSScriptRoot 'template\.reper\reper.gitlab-ci.yml') $reper
Write-Output "Обновлено: .reper/pipeline, .reper/reper.gitlab-ci.yml"

$templates = @(
    @{ From = 'template\.gitlab-ci.yml';         To = '.gitlab-ci.yml' },
    @{ From = 'template\.reper\config.json';     To = '.reper\config.json' },
    @{ From = 'template\.reper\cluster.json';    To = '.reper\cluster.json' },
    @{ From = 'template\.reper\.gitattributes';  To = '.reper\.gitattributes' }
)
foreach ($item in $templates) {
    $to = Join-Path $Target $item.To
    if (Test-Path $to) { continue }
    Copy-Item (Join-Path $PSScriptRoot $item.From) $to
    Write-Output "Создано: $($item.To) - заполнить под проект"
}

$version = & git -C $PSScriptRoot describe --tags --always --dirty 2>$null
if (-not $version) { $version = 'unknown' }
Set-Content -Path (Join-Path $reper 'VERSION') -Value $version -Encoding ASCII
Write-Output "RepEr $version поставлен в $Target"
