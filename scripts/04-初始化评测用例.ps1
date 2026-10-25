# 多智能体评测平台 - 初始化评测用例
# 由 04-初始化评测用例.bat 调用

$ErrorActionPreference = "Stop"

# 项目根目录（scripts/ 的上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   多智能体编排与模型评测平台 - 初始化评测用例" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "此操作会把 30 道测试题导入数据库。" -ForegroundColor Yellow
Write-Host "如果已经导入过，会自动跳过（不会重复导入）。" -ForegroundColor Yellow
Write-Host ""

# 检查虚拟环境
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[错误] 未检测到虚拟环境，请先运行 01-首次运行-安装并启动.bat" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "正在导入用例..." -ForegroundColor Yellow
Write-Host ""
& $venvPython -m app.cli.main init-cases

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   完成！" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "按回车键退出"
