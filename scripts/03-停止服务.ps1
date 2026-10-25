# 多智能体评测平台 - 停止服务
# 由 03-停止服务.bat 调用

# 项目根目录（scripts/ 的上级目录）
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   多智能体编排与模型评测平台 - 停止服务" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "正在查找并停止服务进程..." -ForegroundColor Yellow
Write-Host ""

# 查找占用 8000 端口的进程
$connections = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  找到进程: $($proc.ProcessName) (PID: $($proc.Id))，正在终止..." -ForegroundColor Gray
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Write-Host "  未检测到占用 8000 端口的进程" -ForegroundColor Gray
}

# 同时查找 uvicorn 进程
Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  终止 uvicorn 进程 (PID: $($_.Id))" -ForegroundColor Gray
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "   操作完成！" -ForegroundColor Green
Write-Host ""
Write-Host "   如果服务仍在运行，直接关闭那个黑色/蓝色窗口即可。" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Read-Host "按回车键退出"
