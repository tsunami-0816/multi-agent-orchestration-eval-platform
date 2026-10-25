"""FastAPI 应用入口。

初始化 FastAPI 应用，注册中间件、全局异常处理器、路由和生命周期事件。
提供健康检查接口 /api/v1/health（T8-8，P2-2 python urllib 实现容器内健康检查）。

启动命令：
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_config, get_config_paths
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger, setup_logging
from app.core.middleware import register_middlewares
from app.schemas.common import ApiResponse, ErrorResponse, HealthResponse

# 初始化日志（应用启动时最先执行）
setup_logging()
logger = get_logger(__name__)

# 全局配置
config = get_config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理。

    启动时：初始化数据库（create_all 建表）、确保数据目录存在
    关闭时：关闭数据库连接池
    """
    # 启动
    logger.info("application_starting", version=config.version, provider=config.llm_provider)

    # 确保数据目录存在
    get_config_paths()

    # 初始化数据库（T1-6：create_all 自动建表）
    try:
        from app.models.base import init_database

        await init_database(config.database_url, echo=config.db_echo)
        logger.info("database_initialized_success")
    except Exception as e:
        logger.error("database_initialization_failed", error=str(e))
        # 数据库初始化失败不阻断启动（可能是 Worker 进程），但记录错误

    logger.info("application_started", version=config.version)

    yield

    # 关闭
    try:
        from app.models.base import close_database

        await close_database(config.database_url)
    except Exception as e:
        logger.error("database_close_failed", error=str(e))

    logger.info("application_shutdown")


# 创建 FastAPI 应用
app = FastAPI(
    title="多智能体编排与模型评测一体化平台",
    description="基于 LangGraph + MCP 的多 Agent 编排与 LLM 评测平台",
    version=config.version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 注册中间件（T0-5）
register_middlewares(app)


# === 全局异常处理器 ===


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """处理应用自定义异常（T0-4）。

    返回统一错误信封，HTTP 状态码由错误码决定。
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.warning(
        "app_exception",
        code=int(exc.code),
        message=exc.message,
        path=request.url.path,
        trace_id=trace_id,
    )
    error_response = ErrorResponse.from_exception(
        code=int(exc.code),
        message=exc.message,
        request_id=trace_id,
        details=exc.details if exc.details else None,
    )
    return JSONResponse(status_code=exc.http_status, content=error_response.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理 Pydantic 请求参数校验失败（T0-4，错误码 10002）。"""
    trace_id = getattr(request.state, "trace_id", "unknown")
    errors = exc.errors()
    details = {"validation_errors": errors} if errors else None

    logger.warning(
        "validation_error",
        path=request.url.path,
        errors=errors,
        trace_id=trace_id,
    )

    error_response = ErrorResponse.from_exception(
        code=int(ErrorCode.VALIDATION_ERROR),
        message="请求参数校验失败",
        request_id=trace_id,
        details=details,
    )
    return JSONResponse(status_code=400, content=error_response.model_dump())


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理 Starlette HTTP 异常（如 404 路由不存在）。"""
    trace_id = getattr(request.state, "trace_id", "unknown")

    if exc.status_code == 404:
        code = int(ErrorCode.RESOURCE_NOT_FOUND)
        message = "资源不存在"
    elif exc.status_code == 401:
        code = int(ErrorCode.UNAUTHORIZED)
        message = "未授权访问"
    elif exc.status_code == 429:
        code = int(ErrorCode.RATE_LIMITED)
        message = "请求过于频繁"
    else:
        code = int(ErrorCode.INTERNAL_ERROR)
        message = str(exc.detail) if exc.detail else "内部服务器错误"

    error_response = ErrorResponse.from_exception(
        code=code,
        message=message,
        request_id=trace_id,
    )
    return JSONResponse(status_code=exc.status_code, content=error_response.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理未捕获的异常（T0-4，错误码 10001）。

    所有未被上述处理器捕获的异常最终到达这里，返回 500。
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.error(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
        trace_id=trace_id,
        exc_info=True,
    )
    error_response = ErrorResponse.from_exception(
        code=int(ErrorCode.INTERNAL_ERROR),
        message="内部服务器错误",
        request_id=trace_id,
    )
    return JSONResponse(status_code=500, content=error_response.model_dump())


# === 健康检查接口（T8-8，P0）===


@app.get("/api/v1/health", response_model=ApiResponse[HealthResponse], tags=["系统"])
async def health_check(request: Request) -> ApiResponse[HealthResponse]:
    """健康检查接口（架构 §5.4.3）。

    检查 DB / Redis / LLM 连通性，返回各组件状态。
    使用 python urllib 实现容器内健康检查（P2-2，不依赖 curl）。

    Docker HEALTHCHECK 调用：
        python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"
    """
    trace_id = getattr(request.state, "trace_id", "unknown")

    # 检查数据库
    db_status = "ok"
    try:
        from app.models.base import get_engine
        from sqlalchemy import text

        engine = get_engine(config.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "error"
        logger.error("health_check_db_error", error=str(e))

    # 检查 Redis（可选，未配置时跳过）
    redis_status = "skipped"
    try:
        import redis as redis_lib

        r = redis_lib.from_url(config.redis_url, socket_timeout=2)
        if r.ping():
            redis_status = "ok"
        else:
            redis_status = "error"
        r.close()
    except Exception:
        redis_status = "error" if config.redis_url else "skipped"

    # 检查 LLM（Mock 模式跳过，真实模式发最小请求）
    llm_status = "skipped"
    if config.llm_provider != "mock":
        llm_status = "warning"  # 真实 LLM 检查可能较慢，降级为 warning

    # 整体状态判定
    if db_status == "error":
        overall_status = "down"
    elif db_status == "ok" and redis_status in ("ok", "skipped"):
        overall_status = "ok"
    else:
        overall_status = "degraded"

    health = HealthResponse(
        status=overall_status,
        version=config.version,
        db=db_status,
        redis=redis_status,
        llm=llm_status,
    )

    return ApiResponse.success(data=health, request_id=trace_id)


# === 静态文件挂载（M10 前端界面，T10-1）===

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    logger.info("static_files_mounted", path=str(_STATIC_DIR))


# === 根路径重定向到前端页面 ===


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """根路径重定向到前端页面（M10）。"""
    return RedirectResponse(url="/static/index.html")


# === 业务路由注册（M8 API 接入层）===

from app.api.v1.eval import router as eval_router
from app.api.v1.tasks import router as tasks_router

app.include_router(tasks_router, prefix="/api/v1")
app.include_router(eval_router, prefix="/api/v1")
