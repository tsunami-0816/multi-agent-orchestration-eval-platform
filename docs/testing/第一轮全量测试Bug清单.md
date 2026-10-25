# 《第一轮全量测试Bug清单》

**项目名称**：多智能体编排与模型评测一体化平台
**测试版本**：基线 v1.0（2026-10-05）+ 第二轮联调修复后代码
**测试日期**：2026-10-06
**测试环境**：Windows 11 + Python 3.11 + Mock LLM 模式 + SQLite
**测试方式**：代码静态走查 + 单元测试（81项）+ 动态 API 接口测试（17个端点）
**测试人**：资深测试工程师

---

## 一、测试范围

本次全量测试覆盖以下功能、链路和场景：

| 模块 | 覆盖范围 | 测试方式 |
|---|---|---|
| **系统启动与健康检查** | 服务启动、健康检查（DB/Redis/LLM）、Swagger文档、根路径重定向 | 动态测试 + 单元测试 |
| **任务管理链路** | 创建任务、任务列表、任务详情、取消任务、恢复任务、任务轨迹(trace)、消息列表、执行记录 | 动态测试 + 代码审查 |
| **多Agent编排引擎** | Planner→Executor→Reviewer→Aggregator 四阶段流水线、状态图引擎、检查点持久化、消息持久化、执行记录持久化 | 代码审查 + 单元测试 |
| **MCP工具层** | 工具注册中心、参数校验、web_search/code_exec/file_rw/db_query 四大工具、Session生命周期管理 | 代码审查 + 单元测试 |
| **LLM网关** | Provider工厂、Mock/DeepSeek、指数退避重试、Token计费、预算控制（¥180预警/¥200拒绝）、用量持久化 | 代码审查 + 单元测试 |
| **评测框架** | 用例加载/导入/列表、评测运行创建/列表/详情、评分记录、完成检测、报告生成、一致性校验 | 动态测试 + 代码审查 |
| **报告生成** | HTML报告、JSON报告、报告记录持久化、报告下载 | 代码审查 |
| **可观测性** | TraceID中间件、API Key鉴权、限流、structlog JSON日志、Prometheus指标 | 代码审查 + 动态测试 |
| **数据层** | 9张表ORM模型、Repository层、异步Session、原子递增、分页查询 | 单元测试 + 代码审查 |
| **CLI工具** | init-cases、eval run、backup、cleanup | 代码审查 |
| **前端界面** | 静态页面可访问性 | 动态测试 |

**单元测试结果**：81项中 79 passed / 2 failed（通过率 97.5%）
**动态API测试**：17个端点中 10正常 / 5返回500 / 2异常处理正常

---

## 二、严重等级划分

| 等级 | 定义 | 本轮数量 |
|---|---|---|
| **P0 致命** | 功能完全不可用、数据错误、系统崩溃、核心链路中断 | **2** |
| **P1 严重** | 核心功能异常、性能不达标、无异常兜底 | **4** |
| **P2 一般** | 非核心功能异常、交互体验问题 | 5（列入待优化） |
| **P3 轻微** | 样式、文案、提示类小问题 | 3（列入待优化） |

---

## 三、Bug清单（P0 + P1，本轮必修）

### BUG-001【P0 致命】Celery 默认被错误启用，任务执行链路完全中断

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-001 |
| **严重等级** | P0 致命 |
| **Bug描述** | `app/workers/celery_app.py:43` 中 `CELERY_ENABLED = getattr(_config, "celery_enabled", True)` 默认值为 `True`，且 `app/config.py` 的 `AppConfig` 类**未定义** `celery_enabled` 字段。导致 `getattr` 始终返回默认值 `True`，Celery 被错误启用。但开发环境未启动 Celery Worker，所有任务被提交到 Redis 队列后无人消费，**任务永久停留在 pending 状态**，核心任务执行链路完全中断。 |
| **复现步骤** | 1. 启动服务：`uvicorn app.main:app --port 8000`<br>2. POST `/api/v1/tasks` 创建任务（body: `{"input":"测试","scenario":"orchestration"}`）<br>3. 观察服务日志输出 `task_submitted_celery`<br>4. GET `/api/v1/tasks/{task_id}` 轮询状态 → **永远为 pending**，started_at 始终为 null<br>5. 等待任意时长后状态不变 |
| **预期结果** | 开发环境（未配置 `APP_CELERY_ENABLED=true`）应走同步降级模式，任务应在数秒内变为 running 并最终 completed；README 和 config.yaml 均声明默认值为 `false` |
| **影响范围** | 全部任务执行链路：任务创建后无法执行、Agent编排无法触发、MCP工具无法调用、回调不会触发、取消/恢复功能失去意义。系统核心价值（多Agent编排）完全不可用。 |
| **根因定位** | ① `celery_app.py:43` 默认值应为 `False` 而非 `True`<br>② `config.py` 的 `AppConfig` 应添加 `celery_enabled: bool = False` 字段<br>③ 两处不一致叠加导致默认行为与文档声明相反 |

---

### BUG-002【P0 致命】数据库 eval_runs 表缺少 failed_cases 列，评测模块完全不可用

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-002 |
| **严重等级** | P0 致命 |
| **Bug描述** | 第二轮联调修复 BUG-011 时，在 `app/models/eval.py:85-87` 的 `EvalRun` 模型中添加了 `failed_cases` 字段，但**未执行数据库迁移**。SQLAlchemy 的 `create_all` 使用 `CREATE TABLE IF NOT EXISTS`，不会修改已存在的表结构。导致运行时 SQLAlchemy 生成的 INSERT/SELECT 语句包含 `failed_cases` 列，但实际数据库表中不存在该列，抛出 `sqlite3.OperationalError: table eval_runs has no column named failed_cases`。**所有涉及 eval_runs 表的接口全部返回 500**。 |
| **复现步骤** | 1. 启动服务（使用已存在的 data/db/app.db）<br>2. POST `/api/v1/eval/runs` 创建评测运行 → **500 Internal Server Error**<br>3. GET `/api/v1/eval/runs` 评测运行列表 → **500**<br>4. GET `/api/v1/eval/runs/{run_id}` 运行详情 → **500**<br>5. GET `/api/v1/eval/runs/{run_id}/scores` 评分记录 → **500**<br>6. GET `/api/v1/eval/runs/{run_id}/report` 报告 → **500**<br>7. GET `/api/v1/eval/runs/{run_id}/consistency` 一致性校验 → **500** |
| **预期结果** | 评测运行创建成功并返回 run_id，所有评测查询接口正常返回数据；模型变更应配套数据库迁移脚本或启动时自动 ALTER TABLE |
| **影响范围** | 整个评测管理模块（7个接口全部500）：评测运行创建、列表、详情、评分、报告、一致性校验、完成检测。评测框架是项目三大核心价值之一，完全不可用。同时 eval_scores.task_id 外键关联也受影响。 |
| **根因定位** | ① 缺少数据库迁移机制（无 Alembic 或等效方案）<br>② 模型字段新增后未提供 `ALTER TABLE eval_runs ADD COLUMN failed_cases INTEGER NOT NULL DEFAULT 0` 的兼容逻辑<br>③ `main.py` 启动时 `init_database` 仅调用 `create_all`，不做 schema diff 或迁移 |

---

### BUG-003【P1 严重】所有 API 成功响应的 request_id 和 timestamp 为空字符串

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-003 |
| **严重等级** | P1 严重 |
| **Bug描述** | `app/api/v1/tasks.py` 和 `app/api/v1/eval.py` 中所有成功响应使用 `ApiResponse(data=...)` **直接构造**，而非 `ApiResponse.success(data=..., request_id=trace_id)`。`ApiResponse` 的 `__init__` 不会自动填充 `request_id` 和 `timestamp`（默认值为空字符串 `""`），只有 `success()` 类方法才会生成 timestamp。导致所有成功响应中 `request_id=""`、`timestamp=""`，而 TraceID 中间件已生成 trace_id 并注入 `request.state.trace_id`，但未传递到响应体。 |
| **复现步骤** | 1. 调用任意成功接口，如 GET `/api/v1/health`（此接口用了 success()，正常）<br>2. 调用 GET `/api/v1/tasks` → 响应体中 `"request_id": "", "timestamp": ""`<br>3. 调用 POST `/api/v1/tasks` → 同样为空<br>4. 调用 GET `/api/v1/eval/cases` → 同样为空<br>5. 对比错误响应（如404）→ 错误响应中 request_id 和 timestamp 有值（因为用了 from_exception()） |
| **预期结果** | 所有响应（成功和失败）的 `request_id` 应等于本次请求的 trace_id，`timestamp` 应为响应生成时间的 ISO 8601 格式；健康检查接口已正确实现，应统一 |
| **影响范围** | tasks.py 全部 8 个接口 + eval.py 全部 10 个接口的成功响应。影响链路追踪能力（无法通过响应体中的 request_id 关联日志）、前端时间展示、API 契约一致性。 |
| **根因定位** | ① 两处 API 文件共 18 处 `ApiResponse(data=...)` 应改为 `ApiResponse.success(data=..., request_id=request.state.trace_id)`<br>② 或在 `ApiResponse` 模型中添加自动填充 timestamp 的验证器，并通过依赖注入统一传递 request_id |

---

### BUG-004【P1 严重】任务结果缓存功能完全不可用（config.get() 调用类型错误）

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-004 |
| **严重等级** | P1 严重 |
| **Bug描述** | `app/services/task_service.py:81` 中调用 `self.config.get("task_result_cache_enabled", True)`，但 `self.config` 是 `AppConfig` 实例（Pydantic `BaseSettings`），**不是字典，没有 `.get()` 方法**。当结果缓存命中时会抛出 `AttributeError: 'AppConfig' object has no attribute 'get'`。此外 `AppConfig` 中也未定义 `task_result_cache_enabled` 字段。结果缓存（T5-7）功能完全不可用。 |
| **复现步骤** | 1. 手动向 `_result_cache` 字典中插入一条缓存（或通过 `TaskService.cache_result()` 静态方法）<br>2. POST `/api/v1/tasks` 使用相同输入创建任务<br>3. 缓存命中路径执行到 `self.config.get(...)` → **AttributeError**，任务创建失败<br>4. 由于缓存从未被填充（`cache_result` 无调用方），当前运行时不会触发，但代码缺陷客观存在 |
| **预期结果** | 缓存命中时应直接返回已完成的任务记录，不触发异常；配置项应通过 `getattr(self.config, "task_result_cache_enabled", True)` 或在 AppConfig 中定义字段访问 |
| **影响范围** | 任务结果缓存功能（T5-7）。当前因 `cache_result()` 无调用方导致缓存永不命中，属于"潜伏型Bug"——一旦上游开始调用缓存写入，创建任务接口就会崩溃。 |
| **根因定位** | ① `task_service.py:81` 应改为 `getattr(self.config, "task_result_cache_enabled", True)`<br>② `AppConfig` 应添加 `task_result_cache_enabled: bool = True` 字段<br>③ 需确认 `cache_result()` 的调用方是否缺失（编排完成后应写入缓存） |

---

### BUG-005【P1 严重】EvalRun.status 模型默认值和注释仍为大写，与代码写入小写不一致

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-005 |
| **严重等级** | P1 严重 |
| **Bug描述** | `app/models/eval.py:74-76` 中 `EvalRun.status` 字段定义为 `default="PENDING"`，注释为 `"PENDING/RUNNING/COMPLETED/FAILED"`（全大写），但 `app/eval/eval_service.py:89` 创建运行时写入 `status="running"`（小写），`eval_service.py:193` 完成时写入 `"completed"`（小写）。模型默认值与实际写入不一致。Task 模型已在 BUG-002 修复中统一为小写，但 EvalRun 模型遗漏。 |
| **复现步骤** | 1. 直接通过 ORM 创建 EvalRun 且不显式设置 status → 数据库中存储为大写 "PENDING"<br>2. 通过 API 创建的运行存储为小写 "running"<br>3. 按状态筛选时 `GET /api/v1/eval/runs?status=pending` 无法匹配大写记录<br>4. `check_completion` 中原子更新 `WHERE status='running'` 无法匹配大写 PENDING 记录 |
| **预期结果** | EvalRun.status 的 default 和注释应统一为小写 `pending/running/completed/failed`，与 Task 模型和所有写入代码保持一致 |
| **影响范围** | 评测运行状态管理。若有代码路径未显式设置 status（如直接 ORM 插入），会产生大写状态记录，导致状态筛选、完成检测的原子更新（`WHERE status='running'`）无法匹配，运行可能永久停留。 |
| **根因定位** | `models/eval.py:74-76` 的 `default` 和 `comment` 未随 BUG-002 统一修改，Task 模型修了但 EvalRun 模型遗漏 |

---

### BUG-006【P1 严重】eval_repo.update_run_status() 状态判断用大写，与系统小写不一致

| 项目 | 内容 |
|---|---|
| **Bug编号** | BUG-006 |
| **严重等级** | P1 严重 |
| **Bug描述** | `app/repositories/eval_repo.py:146` 中 `update_run_status` 方法判断 `if status == "COMPLETED"` 用大写，但系统中所有评测运行状态写入均为小写（`eval_service.py` 写 "running"/"completed"）。该方法当前为死代码（全项目无调用方），但若被调用，`completed_at` 永远不会被设置。同类问题 `task_repo.update_status()` 已在 NEW-003 修复中改为小写，eval_repo 遗漏。 |
| **复现步骤** | 1. 代码审查确认 `update_run_status` 无任何调用方<br>2. 假设调用 `repo.update_run_status(run_id, "completed")` → 第146行 `status == "COMPLETED"` 为 False → `completed_at` 不被设置<br>3. 调用 `repo.update_run_status(run_id, "COMPLETED")` → 虽然能设置 completed_at，但写入数据库的 status 是大写，与系统其他记录不一致 |
| **预期结果** | 状态判断应统一为小写 `if status == "completed"`，与 task_repo 的修复保持一致；或删除该死代码方法避免未来踩坑 |
| **影响范围** | 当前为死代码无运行时影响，但作为公共 Repository 方法存在隐患，任何未来调用方都会踩坑。与 BUG-005 叠加会加剧状态管理混乱。 |
| **根因定位** | `eval_repo.py:146` 未随 NEW-003 修复同步修改，联调报告 OUT-001 已记录但未修复 |

---

## 四、待优化建议（P2 + P3，本轮不修复，留到下版本）

### P2 级问题

| 编号 | 问题描述 | 位置 | 建议 |
|---|---|---|---|
| OPT-001 | `TaskMessageResponse` 缺少 `agent_role`/`message_type`/`round_number` 三个字段，trace 接口无法返回已持久化的轮次和消息类型信息（REG-002，联调报告已记录未修复） | `app/schemas/task.py:58-69` | 添加三个 Optional 字段 |
| OPT-002 | `GET /tasks/{id}/messages` 接口手动构造字典，未复用 `TaskMessageResponse`，同样缺少三个字段（OUT-002） | `app/api/v1/tasks.py:173-193` | 改用 `TaskMessageResponse.model_validate(m)` |
| OPT-003 | 评测用例完全失败时（DB连接失败/case不存在/评分保存失败）不递增 `completed_cases`，运行可能永久停留 running（REG2-001） | `app/workers/tasks/eval_case.py:30-48` | 在外层 except 中增加兜底递增 `increment_completed_cases(run_id, passed=False, failed=True)` |
| OPT-004 | 单元测试 `test_update_task_status` 使用大写状态值 "RUNNING"/"COMPLETED"，与代码小写判断不匹配导致测试失败 | `tests/unit/test_models.py:60,65` | 测试用例改为小写 "running"/"completed" |
| OPT-005 | 单元测试 `test_root_endpoint` 期望根路径返回 200，实际返回 307 重定向到 /static/index.html | `tests/unit/test_health_api.py:67` | 测试断言改为 307 或跟随重定向后验证 200 |

### P3 级问题

| 编号 | 问题描述 | 位置 | 建议 |
|---|---|---|---|
| OPT-006 | Pydantic 警告：`EvalRunCreateRequest.model_settings` 和 `EvalRunResponse.model_config_json` 字段名与 protected namespace "model_" 冲突 | `app/schemas/eval.py` | 设置 `model_config = {"protected_namespaces": ()}` 或重命名字段 |
| OPT-007 | 创建任务 API 响应耗时 7 秒+，疑似 Redis 连接超时导致（`is_celery_enabled()` 或 revoke 调用时等待 Redis） | 服务日志观测 | 优化 Redis 连接池或添加连接超时；Celery 未启用时应跳过 Redis 操作 |
| OPT-008 | 取消任务接口响应中 `message` 字段中文显示为乱码（PowerShell 编码问题，需确认前端是否正常） | `tasks.py:100` | 确认前端编码处理，必要时统一 UTF-8 |

---

## 五、未覆盖场景与边界

以下场景因环境限制或受 P0 Bug 阻塞，本轮测试未覆盖，需在下一轮补充：

| 编号 | 未覆盖场景 | 原因 | 建议 |
|---|---|---|---|
| UNC-001 | **Celery 异步模式全链路** | 需启动 Redis + Celery Worker，当前 P0-001 导致默认走 Celery 但无 Worker | 修复 P0-001 后启动 Worker 验证异步执行、并发、任务路由 |
| UNC-002 | **MCP 工具实际调用验证** | 受 P0-001 阻塞，任务无法执行到 Executor 节点；MCP 子进程启动需独立验证 | 修复 P0-001 后验证 web_search/code_exec/file_rw/db_query 实际调用和 Session 关闭 |
| UNC-003 | **评测执行完整链路** | 受 P0-002 阻塞，评测运行无法创建 | 修复 P0-002 后验证：用例执行→Agent编排→Judge评分→进度递增→完成检测→报告生成全链路 |
| UNC-004 | **报告生成与下载** | 受 P0-002 阻塞 | 修复后验证 HTML/JSON 报告文件生成、EvalReport 记录写入、下载接口 |
| UNC-005 | **任务取消的优雅退出与强制终止** | 受 P0-001 阻塞，任务无法进入 running 状态 | 修复后验证：RUNNING 取消→cancellation_requested→Agent每步检查→30秒超时强制终止 |
| UNC-006 | **任务回调通知实际发送** | 受 P0-001 阻塞，任务无法完成 | 修复后验证回调URL接收、指数退避重试、X-Task-Id头、载荷字段完整性 |
| UNC-007 | **检查点回放与恢复** | 受 P0-001 阻塞，无检查点文件生成 | 修复后验证检查点文件生成、list_checkpoints返回、resume接口从检查点恢复 |
| UNC-008 | **真实 LLM API 调用** | 测试环境使用 Mock 模式，未配置 DeepSeek API Key | 需在有 API Key 的环境验证真实模型调用、计费、预算控制 |
| UNC-009 | **并发压力测试** | 本轮为单用户串行测试 | 需验证多任务并发、评测运行并发、数据库锁竞争、Redis缓存一致性 |
| UNC-010 | **前端页面功能交互** | 仅验证静态页面可访问（200），未测试 JS 交互、表单提交、数据展示 | 需手工或 E2E 测试前端四个 Tab 的完整操作流程 |
| UNC-011 | **Docker 部署验证** | 本轮在本地 Python 环境测试 | 需验证 docker-compose up 一键启动、容器内健康检查、卷挂载持久化 |
| UNC-012 | **LLM 预算控制边界** | Mock 模式计费为0，无法触发 ¥180 预警和 ¥200 拒绝 | 需模拟用量数据验证强制 Mock 切换和预算超限异常 |
| UNC-013 | **API Key 鉴权功能** | 未配置 APP_API_KEY，鉴权中间件跳过 | 需配置后验证写操作强制鉴权、读操作开放、无效Key返回401 |
| UNC-014 | **CLI 工具实际运行** | 仅代码审查，未执行 `python -m app.cli.main` 各命令 | 需验证 init-cases/eval run/status/backup/cleanup 命令 |

---

## 六、测试结论

| 维度 | 结论 |
|---|---|
| **P0 级 Bug** | **2 项**，均为核心链路完全中断，必须立即修复 |
| **P1 级 Bug** | **4 项**，影响数据一致性和可观测性，本轮必须修复 |
| **单元测试** | 79/81 通过，2 项失败为测试用例过时（P2） |
| **动态接口** | 任务管理模块 8 接口中 7 正常（执行链路受 P0-001 阻塞）；评测模块 10 接口全部受 P0-002 阻塞返回 500 |
| **核心链路状态** | 多Agent编排链路：**中断**（P0-001）；评测执行链路：**中断**（P0-002）；MCP工具层：代码就绪但未动态验证；LLM网关：Mock模式正常 |
| **是否可进入下一阶段** | **否**。2 项 P0 必须修复后进行回归验证，确认核心链路打通后方可进入下一轮测试 |

**修复优先级建议**：
1. 第一优先：BUG-002（数据库迁移，修复后评测模块恢复可用）
2. 第二优先：BUG-001（Celery 默认值，修复后任务执行链路恢复）
3. 第三优先：BUG-003（响应字段，影响全接口可观测性）
4. 第四优先：BUG-004、BUG-005、BUG-006（数据一致性隐患）

修复完成后需执行**回归验证**：重点验证任务创建→执行→完成全链路、评测运行创建→执行→报告全链路，以及所有接口响应的 request_id/timestamp 字段。
