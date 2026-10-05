# scripts/stop_all.ps1
# 一键停止 V1 全部服务（按端口杀进程；MySQL 容器只 stop，不删数据）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1

$root = Split-Path -Parent $PSScriptRoot
$ports = 8010, 5011, 5012, 5013, 5014, 5015, 8111, 8112, 8210, 3010

foreach ($p in $ports) {
  $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($conn) {
    $pid_ = $conn[0].OwningProcess
    Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
    Write-Host "stopped :$p (pid=$pid_)" -ForegroundColor Yellow
  } else {
    Write-Host ":$p not running"
  }
}

Set-Location $root
Write-Host "停止 MySQL 容器（数据保留）..." -ForegroundColor Cyan
docker compose stop mysql

Write-Host "全部停止完成。" -ForegroundColor Green
