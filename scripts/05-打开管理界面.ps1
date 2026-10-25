# 多智能体评测平台 - 打开管理界面
# 由 05-打开管理界面.bat 调用

# 项目根目录（scripts/ 的上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "正在打开管理界面..." -ForegroundColor Yellow
Start-Process "http://localhost:8000"

Start-Sleep -Seconds 2

# 检测服务是否在运行
$running = $false
try {
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($conn) { $running = $true }
} catch {}

if (-not $running) {
    Write-Host ""
    Write-Host "[提示] 检测到服务可能未启动。" -ForegroundColor Yellow
    Write-Host "如果浏览器显示""无法访问此网站""，请先双击运行："
    Write-Host "  02-启动服务.bat"
    Write-Host ""
    Read-Host "按回车键退出"
}
