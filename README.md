# 多智能体办公助手（office-agent）

基于 **A2A 协议 + MCP** 的多智能体企业办公助手：员工一句话完成会议室查询/预订/改期/取消、器材查询、报修、企业知识问答；独立 Agent 部署、MCP 参数化工具、SSE 流式返回。

- V1 实施计划：`docs/多智能体办公助手V1实施计划.md`
- 验收记录：`docs/p0_验收记录.md` ~ `docs/p10_验收记录.md`
- 意图评测报告：`docs/office_intent_eval_report.md`
- 安全加固记录：`docs/p7_安全加固记录.md`

## 快速开始（V1）

> 一键启动：`powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1`
> 一键停止：`powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1`

手动方式：

```powershell
# 0. 环境：复用 conda 环境 Edu_Agent
$py = "C:\Users\97916\.conda\envs\Edu_Agent\python.exe"

# 1. 依赖（首次）
& $py -m pip install -r requirements.txt

# 2. 配置：复制 .env.example 为 .env，填写 DEEPSEEK_API_KEY
Copy-Item .env.example .env

# 3. 起 MySQL（Docker Desktop 需运行）
docker compose up -d mysql

# 4. 起网关
& $py -m uvicorn backend.main:app --host 0.0.0.0 --port 8010 --reload

# 5. 健康检查
curl http://localhost:8010/health
```

## 服务与端口（V1）

| 服务 | 端口 | 说明 |
|---|---|---|
| FastAPI 网关 | 8010 | Router / SSE / JWT |
| 会议室查询 Agent | 5011 | A2A |
| 会议室预订 Agent | 5012 | A2A（订/改/取消） |
| 器材查询 Agent | 5013 | A2A（静态+动态融合） |
| 器材报修 Agent | 5014 | A2A（报修/进度/催单） |
| 知识库 RAG Agent | 5015 | A2A（office_kb 检索 + 带来源回答） |
| OA MCP Server | 8111 | FastMCP streamable-http（会议室工具） |
| 器材 MCP Server | 8112 | FastMCP streamable-http（器材工具） |
| Mock 内部系统 | 8210 | 模拟 OA + 资产系统（台账源/动态状态） |
| MySQL | 3309 | office_oa / office_asset / office_app |
| Milvus | 19532 | office_kb 向量库（含 etcd/minio 容器） |
| 前端 dev | 3010 | Vue3 + Vite |

## 全量回归（AI 项目的"测试门禁"）

```powershell
python scripts\run_all_checks.py     # 17 个验收脚本一键回归（约 7 分钟），日志落盘 logs/checks/
```

> 覆盖：功能 / 并发 / 越权 / 注入安全 / 降级 / RAG / 提醒 / 报表 / 周期 / 渠道 / 意图评测。

## 渠道入口（Mock 钉钉）

```powershell
# 模拟钉钉机器人回调（验签可选：md5("staff_id:text:key")[:8]）
curl -X POST http://localhost:8010/api/v1/channels/mock-dingtalk/webhook `
  -H "Content-Type: application/json" `
  -d '{"staff_id":"E1001","text":"明天下午3点A栋有哪些空会议室"}'
```

## 目录结构

```
backend/     FastAPI 网关 + Router
agents/      A2A 独立业务 Agent（P3 起）
mcp/         MCP 工具服务（P2 起）
services/    Mock 内部系统（P1 起）
scripts/     初始化 / 种子 / 验收脚本
frontend/    Vue3 工作台
docs/        方案与实施文档
```

> 纪律：未实测指标不虚报；密钥不进 Git；原参考目录只读。
