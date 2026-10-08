# Шаг CI: формирует и собирает исправления по EF-коммитам, пришедшим в push.
# Если EF-коммитов нет, завершается успешно и ничего не создает.
#
# Диапазон коммитов: EF_BEFORE_SHA..EF_HEAD_SHA (ручной запуск), иначе before..after
# из события push, если пайплайн запущен веб-хуком (TRIGGER_PAYLOAD), иначе CI_COMMIT_BEFORE_SHA..CI_COMMIT_SHA.
param(
    [Parameter(Mandatory = $true)][string]$Platform,
    [Parameter(Mandatory = $true)][string]$Config,
    [string]$Repo = '.',
    [string]$Out = 'build/ef',
    [string]$BaseRef = $env:EF_BASE_REF,
    [string]$Head = $(if ($env:EF_HEAD_SHA) { $env:EF_HEAD_SHA } else { $env:CI_COMMIT_SHA }),
    [string]$Before = $(if ($env:EF_HEAD_SHA) { $env:EF_BEFORE_SHA } else { $env:CI_COMMIT_BEFORE_SHA }),
    [string]$Python = $(if ($env:EF_PYTHON) { $env:EF_PYTHON } else { 'python' })
)

$ErrorActionPreference = 'Stop'

# Запуск веб-хуком push из репозитория конфигурации: GitLab кладет тело события в файл TRIGGER_PAYLOAD.
if (-not $env:EF_HEAD_SHA -and $env:TRIGGER_PAYLOAD -and (Test-Path $env:TRIGGER_PAYLOAD)) {
    $payload = Get-Content $env:TRIGGER_PAYLOAD -Raw -Encoding UTF8 | ConvertFrom-Json
    # Тег собирает задание Релиз. Сюда событие тега попадет, только если tag push включить в веб-хуке исправлений.
    if ($payload.object_kind -eq 'tag_push') {
        Write-Output 'Событие тега, сборка исправлений не требуется'
        exit 0
    }
    $branch = if ($env:EF_BRANCH) { $env:EF_BRANCH } else { 'master' }
    # Поле ref события перекрывается ref из адреса триггера (.../ref/main/trigger/pipeline), поэтому
    # ветку проверяем, только если в ref путь ветки; основной отбор - фильтр веток веб-хука.
    if (($payload.ref -like 'refs/*' -and $payload.ref -ne "refs/heads/$branch") -or -not $payload.after -or $payload.after -match '^0+$') {
        Write-Output "Событие не про push в ветку ${branch} (ref $($payload.ref), after $($payload.after)), сборка исправлений не требуется"
        exit 0
    }
    Write-Output "Push в $branch по веб-хуку: $($payload.before)..$($payload.after)"
    $Head = $payload.after
    $Before = $payload.before
}

if (-not $Head) { $Head = 'HEAD' }
# У первого запуска ветки BEFORE состоит из нулей: берем один коммит.
if (-not $Before -or $Before -match '^0+$') { $Before = "$Head^" }

$arguments = @("$PSScriptRoot/ef_build.py", '--repo', $Repo, '--config', $Config, '--range', "$Before..$Head", '--out', $Out)
if ($BaseRef) { $arguments += @('--base', $BaseRef) }
# По сборкам в ленте выбирается версия расширения: у сборок с разным содержимым она не совпадает.
if ($env:EF_FEED_URL) { $arguments += @('--feed-url', $env:EF_FEED_URL) }
if ($env:EF_STRICT -eq '1') { $arguments += '--strict' }

& $Python @arguments
$code = $LASTEXITCODE
if ($code -eq 2) {
    Write-Output 'EF-коммитов нет, сборка исправлений не требуется'
    exit 0
}
if ($code -ne 0) { exit $code }

foreach ($dir in Get-ChildItem $Out -Directory) {
    $infoPath = Join-Path $dir.FullName 'ef.json'
    if (-not (Test-Path $infoPath)) { continue }
    # Отзыв исправления (коммиты откачены или вошли в другое исправление): собирать нечего, отзывает публикация.
    if ((Get-Content $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json).revoke) {
        Write-Output "$($dir.Name): исправления больше нет, оно будет отозвано при публикации"
        continue
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot/build_cfe.ps1" -EfDir $dir.FullName -Platform $Platform
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
exit 0
