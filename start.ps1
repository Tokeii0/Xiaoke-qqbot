$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "未找到虚拟环境：$Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "node_modules"))) {
    throw "尚未安装 Node 依赖，请先在 $Root 执行 npm install"
}

Set-Location -LiteralPath $Root
& $Python (Join-Path $Root "run.py")
exit $LASTEXITCODE
