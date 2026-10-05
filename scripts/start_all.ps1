# scripts/start_all.ps1
# 一键启动 V2 全部服务（开发环境）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
#
# 启动清单：
#   Docker  MySQL :3309 / Milvus :19532（含 etcd/minio）
#   Mock 内部系统 :8210 / OA MCP :8111 / 器材 MCP :8112
#   会议室查询 :5011 / 会议室预订 :5012 / 器材查询 :5013 / 器材报修 :5014 / 知识库 RAG :5015
#   网关 :8010 / 前端 :3010
# 依赖：Docker Desktop 已启动；conda 环境 Edu_Agent 已就绪
# 首次运行知识库需先执行：
#   python scripts/init_office_milvus.py ; python scripts/build_office_kb.py

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\97916\.conda\envs\Edu_Agent\python.exe"

if (-not (Test-Path $py)) {
  Write-Host "[ERROR] 未找到 conda 环境：$py" -ForegroundColor Red
  exit 1
}

Set-Location $root

Write-Host "[1/11] 启动基础设施（MySQL + Milvus 三件套）..." -ForegroundColor Cyan
docker compose up -d mysql etcd minio milvus

Write-Host "[2/11] Mock 内部系统 :8210 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "-m", "uvicorn", "services.mock_internal.main:app", "--host", "127.0.0.1", "--port", "8210", "--log-level", "warning"

Write-Host "[3/11] 同步器材台账（每日同步任务）..." -ForegroundColor Cyan
Start-Sleep -Seconds 3
& $py scripts\sync_assets.py

Write-Host "[4/11] OA MCP :8111 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "mcp_servers\oa_server.py"

Write-Host "[5/11] 器材 MCP :8112 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "mcp_servers\asset_server.py"

Write-Host "[6/11] 会议室查询 Agent :5011 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "agents\meeting_query\server.py"

Write-Host "[7/11] 会议室预订 Agent :5012 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "agents\meeting_book\server.py"

Write-Host "[8/11] 器材查询 Agent :5013 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "agents\equipment_query\server.py"

Write-Host "[9/11] 器材报修 Agent :5014 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "agents\equipment_repair\server.py"

Write-Host "[10/11] 知识库 RAG Agent :5015（首次加载模型约 15 秒）..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "agents\kb_rag\server.py"

Write-Host "[11/11] 网关 :8010 + 前端 :3010 ..." -ForegroundColor Cyan
Start-Process -FilePath $py -WorkingDirectory $root -WindowStyle Minimized `
  -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8010", "--log-level", "warning"
Start-Process -FilePath "npm.cmd" -WorkingDirectory "$root\frontend" -WindowStyle Minimized `
  -ArgumentList "run", "dev"

Start-Sleep -Seconds 5
Write-Host ""
Write-Host "启动完成（前端首次打开需等 Vite 就绪，RAG Agent 需等模型加载）：" -ForegroundColor Green
Write-Host "  前端  http://localhost:3010"
Write-Host "  网关  http://localhost:8010/health"
Write-Host "  MCP   :8111 / :8112     Mock  http://localhost:8210/docs"
Write-Host ""
Write-Host "停止服务：powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1"
