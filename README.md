# 多智能体编排与模型评测一体化平台

基于 LangGraph + MCP 的多 Agent 编排与 LLM 评测平台。

## 功能特性

- **多 Agent 编排**：Planner → Executor → Reviewer → Aggregator 四阶段流水线
- **MCP 工具层**：web_search / code_exec / file_rw / db_query 四大工具
- **LLM 网关**：统一接口 + 重试 + 计费 + 预算控制，**已接通通义千问 qwen-plus（DashScope OpenAI 兼容模式）**，同时保留 DeepSeek / Mock 切换能力
- **评测框架**：30 条标准用例 + LLM-as-Judge 多维度评分
- **报告生成**：HTML 可视化报告 + JSON 结构化数据
- **异步任务**：Celery + Redis 异步执行，支持取消/恢复/回调（未起 Redis 时自动同步降级）
- **可观测性**：7 项核心指标 + Prometheus 端点 + 深度健康检查 + 完整检查点回放

## 技术栈

| 类别 | 技术 | 版本 |
|---|---|---|
| 语言 | Python | 3.12 |
| Web 框架 | FastAPI | 0.115 |
| ORM | SQLAlchemy 2.0 async + aiosqlite | - |
| 任务队列 | Celery + Redis | 5.4 |
| Agent 编排 | 轻量级状态图（兼容 LangGraph） | - |
| LLM | langchain-openai + DeepSeek | 0.2.5 |
| 日志 | structlog JSON | 24.4 |
| 配置 | Pydantic Settings + YAML | - |
| 数据库 | SQLite WAL | - |

## 快速开始

> **Windows 零基础用户**：直接打开 `scripts/` 文件夹，双击 `01-首次运行-安装并启动.bat` 即可自动完成以下所有步骤。详见 `QUICKSTART.txt`。

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，设置 LLM Provider 和 API Key
```

### 3. 初始化评测用例

```bash
python -m app.cli.main init-cases
```

### 4. 启动服务

```bash
# Mock 模式（无需 API Key，用于开发测试）
$env:APP_LLM_PROVIDER="mock"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 访问

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health
- 前端界面：http://localhost:8000/static/index.html

## Docker 部署

```bash
# 一键启动（API + Redis + Worker）
docker-compose up -d

# 包含 Ollama 本地模型
docker-compose --profile ollama up -d

# 查看日志
docker-compose logs -f api
```

## CLI 工具

```bash
# 初始化评测用例
python -m app.cli.main init-cases

# 创建评测运行
python -m app.cli.main eval run --name "测试运行" --category research

# 查看评测状态
python -m app.cli.main eval status <run_id>

# 备份数据库
python -m app.cli.main backup

# 清理临时文件
python -m app.cli.main cleanup
```

## API 接口

### 任务管理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/tasks | 创建任务 |
| GET | /api/v1/tasks | 任务列表 |
| GET | /api/v1/tasks/{id} | 任务详情 |
| POST | /api/v1/tasks/{id}/cancel | 取消任务 |
| GET | /api/v1/tasks/{id}/trace | 任务轨迹 |
| GET | /api/v1/tasks/{id}/messages | 消息列表 |

### 评测管理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/eval/runs | 创建评测运行 |
| GET | /api/v1/eval/runs | 运行列表 |
| GET | /api/v1/eval/runs/{id} | 运行详情 |
| GET | /api/v1/eval/runs/{id}/scores | 评分记录 |
| GET | /api/v1/eval/runs/{id}/report | 评测报告 |
| GET | /api/v1/eval/runs/{id}/report/download | 下载报告 |
| GET | /api/v1/eval/cases | 用例列表 |
| POST | /api/v1/eval/cases/import | 导入用例 |

## 项目结构

```
├── app/                 # 应用代码（FastAPI + Agent 编排 + 评测框架）
├── data/                # 运行时数据（数据库、检查点、报告、日志，已 gitignore）
├── docs/                # 项目文档（架构、开发、测试、操作手册）
├── scripts/             # 一键启动脚本（Windows .bat + .ps1，零基础用户用这个）
├── static/              # 前端静态文件（管理界面 index.html）
├── tests/               # 单元测试 + 集成测试
├── venv/                # Python 虚拟环境（已 gitignore）
├── .dockerignore        # Docker 构建忽略规则
├── .env.example         # 环境变量模板
├── .gitignore           # Git 忽略规则
├── config.yaml          # 应用配置文件
├── docker-compose.yml   # Docker Compose 编排（API + Redis + Worker）
├── Dockerfile           # Docker 镜像构建（多阶段构建）
├── pyproject.toml       # 工具配置（ruff / mypy / pytest）
├── requirements.txt     # Python 依赖清单
├── QUICKSTART.txt       # 零基础用户快速上手指引
└── README.md            # 项目说明文档（本文件）
```

### app/ 内部结构

```
app/
├── agent/              # M4 Agent 编排引擎
│   ├── nodes/          # Planner/Executor/Reviewer/Aggregator
│   ├── state.py        # 状态模型
│   ├── graph.py        # 状态图引擎
│   └── orchestrator.py # 编排器入口
├── api/                # M8 API 接入层
│   └── v1/             # tasks.py / eval.py
├── cli/                # M9 CLI 工具
├── core/               # M0 基础设施
│   ├── exceptions.py   # 22 错误码
│   ├── logging.py      # structlog JSON
│   └── middleware.py   # TraceID/鉴权/限流
├── eval/               # M6 评测框架
│   ├── loader.py       # 用例加载器
│   ├── judge.py        # LLM 评分器
│   ├── scorer.py       # 评分计算
│   └── eval_service.py # 运行管理
├── llm/                # M2 LLM 网关
│   ├── gateway.py      # 统一入口+重试+计费
│   ├── mock.py         # Mock Provider
│   └── providers/      # DeepSeek Provider
├── mcp/                # M3 MCP 工具层
│   ├── registry.py     # 工具注册中心
│   ├── server.py       # MCP Server
│   ├── client.py       # MCP Client（任务级 Session）
│   └── tools/          # web_search/code_exec/file_rw/db_query
├── models/             # M1 数据层（9 张表 ORM）
├── observability/      # M11 可观测性
├── report/             # M7 报告生成
├── repositories/       # M1 数据访问层
├── schemas/            # Pydantic 模型
├── services/           # M5 业务服务
├── workers/            # M5 Celery 任务
└── main.py             # FastAPI 入口
```

## 测试

```bash
# 运行全部单元测试
$env:APP_LLM_PROVIDER="mock"
python -m pytest tests/unit/ -v

# 运行特定测试
python -m pytest tests/unit/test_llm_gateway.py -v
```

## 配置说明

主要配置项（config.yaml / 环境变量）：

| 配置 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| llm.provider | APP_LLM_PROVIDER | mock | LLM 提供商：deepseek/mock |
| llm.api_key | APP_DEEPSEEK_API_KEY | - | DeepSeek API Key |
| database_url | APP_DATABASE_URL | sqlite+aiosqlite:///data/db/app.db | 数据库连接 |
| celery.enabled | APP_CELERY_ENABLED | false | 是否启用 Celery |
| budget.warning | APP_BUDGET_WARNING | 180 | 预算预警阈值（元） |
| budget.limit | APP_BUDGET_LIMIT | 200 | 预算上限（元） |

## 许可证

MIT License
