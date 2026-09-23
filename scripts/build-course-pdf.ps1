$ErrorActionPreference = 'Stop'

$builder = Join-Path $PSScriptRoot 'build_course_pdf.py'
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

if (-not (Get-Command pandoc -ErrorAction SilentlyContinue)) {
    throw 'Pandoc не найден. Установи Pandoc и повтори сборку.'
}

if (Test-Path -LiteralPath $bundledPython) {
    $pythonExe = $bundledPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = (Get-Command python).Source
} else {
    throw 'Python не найден. Установи Python 3 и пакет reportlab.'
}

& $pythonExe -c 'import reportlab' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "В Python нет reportlab. Установи пакет: `"$pythonExe`" -m pip install reportlab"
}

& $pythonExe $builder @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
