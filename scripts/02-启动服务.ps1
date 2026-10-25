# 多智能体评测平台 - 启动服务
# 由 02-启动服务.bat 调用

$ErrorActionPreference = "Stop"

# 项目根目录（scripts/ 的上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 强制 Python 使用 UTF-8 编码（避免 pip 读取含中文注释的 requirements.txt 时 GBK 解码报错）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   多智能体编排与模型评测平台 - 启动服务" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 检查虚拟环境
Write-Host "[1/3] 检查虚拟环境..." -ForegroundColor Yellow
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
$venvUvicorn = Join-Path $projectRoot "venv\Scripts\uvicorn.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host ""
    Write-Host "[错误] 未检测到虚拟环境！" -ForegroundColor Red
    Write-Host ""
    Write-Host "如果你是第一次使用，请先双击运行："
    Write-Host "  01-首次运行-安装并启动.bat"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}
Write-Host "      虚拟环境正常" -ForegroundColor Green
Write-Host ""

# 检查依赖
Write-Host "[2/3] 检查依赖..." -ForegroundColor Yellow
if (-not (Test-Path $venvUvicorn)) {
    Write-Host "      依赖未安装完整，正在自动安装..." -ForegroundColor Yellow
    $venvPip = Join-Path $projectRoot "venv\Scripts\pip.exe"
    & $venvPip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        & $venvPip install -r requirements.txt
    }
}
Write-Host "      依赖正常" -ForegroundColor Green
Write-Host ""

# 启动服务
Write-Host "[3/3] 启动服务..." -ForegroundColor Yellow
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   服务启动中，请稍候..." -ForegroundColor Cyan
Write-Host "   浏览器访问地址：http://localhost:8000" -ForegroundColor Cyan
Write-Host "   【重要】请勿关闭此窗口，关闭即停止服务" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 延迟 3 秒后打开浏览器
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://localhost:8000"
} | Out-Null

& $venvUvicorn app.main:app --host 0.0.0.0 --port 8000

Write-Host ""
Write-Host "服务已停止。" -ForegroundColor Yellow
Read-Host "按回车键退出"
