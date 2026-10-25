# 多智能体评测平台 - 首次安装并启动
# 由 01-首次运行-安装并启动.bat 调用

$ErrorActionPreference = "Stop"

# 项目根目录（scripts/ 的上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 强制 Python 使用 UTF-8 编码（避免 pip 读取含中文注释的 requirements.txt 时 GBK 解码报错）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   多智能体编排与模型评测平台 - 首次安装并启动" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# === [1/6] 检查 Python ===
Write-Host "[1/6] 检查 Python 环境..." -ForegroundColor Yellow
try {
    $pyVersion = & python --version 2>&1
    Write-Host "      已检测到 $pyVersion" -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "[错误] 没有检测到 Python！" -ForegroundColor Red
    Write-Host ""
    Write-Host "请先安装 Python 3.10 或更高版本："
    Write-Host "  1. 打开 https://www.python.org/downloads/"
    Write-Host "  2. 下载并运行安装包"
    Write-Host "  3. 【非常重要】安装时勾选 ""Add Python to PATH"""
    Write-Host "  4. 安装完成后，关闭本窗口，重新双击此脚本"
    Write-Host ""
    Read-Host "按回车键退出"
    exit 1
}
Write-Host ""

# === [2/6] 创建虚拟环境 ===
Write-Host "[2/6] 创建虚拟环境（首次运行需要，约 10-30 秒）..." -ForegroundColor Yellow
$venvPython = Join-Path $projectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 创建虚拟环境失败！" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Write-Host "      虚拟环境创建成功" -ForegroundColor Green
} else {
    Write-Host "      虚拟环境已存在，跳过" -ForegroundColor Gray
}
Write-Host ""

# 虚拟环境中的工具路径
$venvPip = Join-Path $projectRoot "venv\Scripts\pip.exe"
$venvUvicorn = Join-Path $projectRoot "venv\Scripts\uvicorn.exe"

# === [3/6] 安装依赖 ===
Write-Host "[3/6] 安装项目依赖（首次运行需要，约 3-10 分钟，取决于网速）..." -ForegroundColor Yellow
if (-not (Test-Path $venvUvicorn)) {
    Write-Host "      正在下载依赖，请耐心等待，不要关闭窗口..." -ForegroundColor Gray
    & $venvPip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "[警告] 国内镜像安装失败，尝试官方源..." -ForegroundColor Yellow
        & $venvPip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[错误] 依赖安装失败！请检查网络连接后重试。" -ForegroundColor Red
            Read-Host "按回车键退出"
            exit 1
        }
    }
    Write-Host "      依赖安装完成" -ForegroundColor Green
} else {
    Write-Host "      依赖已安装，跳过" -ForegroundColor Gray
}
Write-Host ""

# === [4/6] 初始化评测用例 ===
Write-Host "[4/6] 初始化评测用例（幂等操作，已导入的会自动跳过）..." -ForegroundColor Yellow
& $venvPython -m app.cli.main init-cases
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 用例初始化失败，不影响启动，可稍后双击 04-初始化评测用例.bat 重试" -ForegroundColor Yellow
}
Write-Host ""

# === [5/6] 启动服务 ===
Write-Host "[5/6] 启动服务..." -ForegroundColor Yellow
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   服务启动中，请稍候..." -ForegroundColor Cyan
Write-Host "   启动成功后会自动打开浏览器" -ForegroundColor Cyan
Write-Host "   【重要】请勿关闭此窗口，关闭即停止服务" -ForegroundColor Yellow
Write-Host "   停止服务：直接关闭此窗口即可" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# 延迟 4 秒后打开浏览器（等服务启动）
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 4
    Start-Process "http://localhost:8000"
} | Out-Null

# 启动服务（前台运行，窗口关闭即停止）
& $venvUvicorn app.main:app --host 0.0.0.0 --port 8000

Write-Host ""
Write-Host "服务已停止。" -ForegroundColor Yellow
Read-Host "按回车键退出"
